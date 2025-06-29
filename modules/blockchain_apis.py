import logging
import requests
import time
import config
from decimal import Decimal, InvalidOperation
import json # For JSONDecodeError

logger = logging.getLogger(__name__)

# --- Custom Exceptions ---
class BlockchainAPIError(Exception):
    """Base class for blockchain API related errors."""
    def __init__(self, message, status_code=None, underlying_exception=None):
        super().__init__(message)
        self.status_code = status_code
        self.underlying_exception = underlying_exception

class BlockchainAPITimeoutError(BlockchainAPIError):
    """Raised when an API request times out."""
    pass

class BlockchainAPIUnavailableError(BlockchainAPIError):
    """Raised when an API is unavailable or returns a server-side error (5xx)."""
    pass

class BlockchainAPIRateLimitError(BlockchainAPIError):
    """Raised when an API rate limit is hit (e.g., HTTP 429)."""
    pass

class BlockchainAPIInvalidAddressError(BlockchainAPIError):
    """Raised when an address is considered invalid by the API (e.g., HTTP 400/404)."""
    pass

class BlockchainAPIBadResponseError(BlockchainAPIError):
    """Raised for unexpected response structure or JSON decoding issues."""
    pass


# API Base URLs
BLOCKSTREAM_API_BASE_URL_BTC = "https://blockstream.info/api"
BLOCKCYPHER_API_BASE_URL_LTC = "https://api.blockcypher.com/v1/ltc/main"
TRONGRID_API_BASE_URL = "https://api.trongrid.io"

REQUESTS_HEADERS = {
    'User-Agent': 'TelegramCryptoBot/1.0'
}
DEFAULT_TIMEOUT = 15 # seconds

def _make_request(url: str, method: str = "GET", params: dict = None, headers: dict = None, data: dict = None) -> requests.Response:
    """Makes an HTTP request and handles common errors, raising custom exceptions."""
    effective_headers = REQUESTS_HEADERS.copy()
    if headers:
        effective_headers.update(headers)

    try:
        if method.upper() == "GET":
            response = requests.get(url, params=params, headers=effective_headers, timeout=DEFAULT_TIMEOUT)
        elif method.upper() == "POST":
            response = requests.post(url, params=params, headers=effective_headers, json=data, timeout=DEFAULT_TIMEOUT)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status() # Raises HTTPError for 4xx/5xx
        return response
    except requests.exceptions.Timeout as e:
        logger.warning(f"API Timeout for URL: {url}. Error: {e}")
        raise BlockchainAPITimeoutError(f"Request timed out: {url}", underlying_exception=e)
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"API ConnectionError for URL: {url}. Error: {e}")
        raise BlockchainAPIUnavailableError(f"Could not connect to API: {url}", underlying_exception=e)
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        logger.error(f"API HTTPError for URL: {url}. Status: {status_code}. Response: {e.response.text[:200]}")
        if status_code == 429:
            raise BlockchainAPIRateLimitError(f"Rate limit hit for: {url}", status_code=status_code, underlying_exception=e)
        elif status_code in [400, 404]: # Often indicates bad address format or not found
             # Some APIs use 400 for invalid address, some 404 for not found.
            raise BlockchainAPIInvalidAddressError(f"Invalid address or resource not found for: {url}", status_code=status_code, underlying_exception=e)
        elif status_code >= 500:
            raise BlockchainAPIUnavailableError(f"API server error ({status_code}) for: {url}", status_code=status_code, underlying_exception=e)
        else:
            raise BlockchainAPIError(f"API request failed ({status_code}) for: {url}", status_code=status_code, underlying_exception=e)
    except requests.exceptions.RequestException as e: # Catch-all for other request-related errors
        logger.exception(f"API Generic RequestException for URL {url}: {e}")
        raise BlockchainAPIError(f"Generic API request error for: {url}", underlying_exception=e)


def get_address_transactions_btc(address: str) -> list[dict]:
    url = f"{BLOCKSTREAM_API_BASE_URL_BTC}/address/{address}/txs"
    logger.debug(f"Fetching BTC transactions for address {address} from URL: {url}")
    try:
        response = _make_request(url)
        raw_txs = response.json()
        processed_txs = []

        # To get actual confirmations, we need the current block height.
        # Fetching it for every call to this function might be too much.
        # For now, if Blockstream says 'confirmed', we'll use a high number.
        # The payment_monitor will ultimately decide based on its configured min_confirmations.
        current_btc_height = None
        try:
            tip_height_url = f"{BLOCKSTREAM_API_BASE_URL_BTC}/blocks/tip/height"
            tip_response = _make_request(tip_height_url) # Shorter timeout for this one maybe
            current_btc_height = int(tip_response.text)
        except Exception as e_tip:
            logger.warning(f"Could not fetch BTC current block height: {e_tip}. Confirmations might be less accurate.")


        for tx in raw_txs:
            total_value_to_address = Decimal('0')
            for vout in tx.get('vout', []):
                if vout.get('scriptpubkey_address') == address:
                    total_value_to_address += Decimal(vout['value'])

            if total_value_to_address > 0:
                tx_status = tx.get('status', {})
                is_confirmed_api = tx_status.get('confirmed', False)
                tx_block_height = tx_status.get('block_height')
                confirmations = 0

                if is_confirmed_api and tx_block_height is not None and current_btc_height is not None:
                    confirmations = current_btc_height - tx_block_height + 1
                elif is_confirmed_api: # Confirmed but couldn't get tip or block_height from this tx
                    confirmations = getattr(config, "MIN_CONFIRMATIONS_BTC", 1) # Default to configured min if confirmed by API

                processed_txs.append({
                    'txid': tx['txid'],
                    'amount_satoshi': str(total_value_to_address),
                    'confirmations': confirmations,
                    'block_height': tx_block_height,
                    'block_time': tx_status.get('block_time'),
                })
        logger.info(f"Found {len(processed_txs)} incoming BTC transactions for address {address}.")
        return processed_txs
    except json.JSONDecodeError as e:
        logger.exception(f"BTC API JSONDecodeError for address {address}. URL: {url}. Error: {e}")
        raise BlockchainAPIBadResponseError(f"Failed to decode JSON response from BTC API for {address}", underlying_exception=e)
    except BlockchainAPIError: # Re-raise custom exceptions from _make_request
        raise
    except Exception as e: # Catch any other unexpected errors
        logger.exception(f"Unexpected error fetching BTC transactions for address {address}: {e}")
        raise BlockchainAPIError(f"Unexpected error during BTC API call for {address}", underlying_exception=e)


def get_address_transactions_ltc(address: str) -> list[dict]:
    url = f"{BLOCKCYPHER_API_BASE_URL_LTC}/addrs/{address}/full?limit=50"
    params = {}
    if config.BLOCKCYPHER_API_TOKEN:
        params['token'] = config.BLOCKCYPHER_API_TOKEN

    logger.debug(f"Fetching LTC transactions for address {address} from BlockCypher.")
    try:
        response = _make_request(url, params=params)
        data = response.json()
        processed_txs = []

        for tx in data.get('txs', []):
            total_value_to_address = Decimal('0')
            for vout in tx.get('outputs', []):
                if address in vout.get('addresses', []):
                    total_value_to_address += Decimal(vout['value'])

            if total_value_to_address > 0:
                confirmations = tx.get('confirmations', 0) # Blockcypher provides this directly
                processed_txs.append({
                    'txid': tx['hash'],
                    'amount_litoshi': str(total_value_to_address),
                    'confirmations': confirmations,
                    'block_height': tx.get('block_height'),
                    'received_time': tx.get('received'),
                })
        logger.info(f"Found {len(processed_txs)} incoming LTC transactions for address {address}.")
        return processed_txs
    except json.JSONDecodeError as e:
        logger.exception(f"LTC API JSONDecodeError for address {address}. URL: {url}. Error: {e}")
        raise BlockchainAPIBadResponseError(f"Failed to decode JSON response from LTC API for {address}", underlying_exception=e)
    except BlockchainAPIError:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error fetching LTC transactions for address {address}: {e}")
        raise BlockchainAPIError(f"Unexpected error during LTC API call for {address}", underlying_exception=e)


def get_trc20_transfers_usdt_trx(address: str, since_timestamp_ms: int = 0) -> list[dict]:
    url = f"{TRONGRID_API_BASE_URL}/v1/accounts/{address}/transactions/trc20"
    params = {
        'limit': 50,
        'contract_address': config.USDT_TRC20_CONTRACT_ADDRESS,
        'only_to': 'true',
        'min_block_timestamp': since_timestamp_ms,
    }
    headers = {} # Local headers for this function
    if config.TRONGRID_API_KEY:
        headers['TRON-PRO-API-KEY'] = config.TRONGRID_API_KEY # Corrected header key

    logger.debug(f"Fetching TRC20 USDT transactions for address {address} since {since_timestamp_ms} from TronGrid.")
    try:
        response = _make_request(url, params=params, headers=headers)
        data = response.json()
        processed_txs = []

        if data.get('success') and 'data' in data:
            for transfer in data['data']:
                # Ensure it's the correct token and an incoming transfer
                if transfer.get('token_info', {}).get('symbol') == 'USDT' and \
                   transfer.get('to', '').lower() == address.lower():

                    amount_smallest_unit = transfer['value']

                    # TronGrid /trc20 endpoint data objects have a 'confirmed' boolean.
                    is_confirmed_api = transfer.get('confirmed', False) # Default to False if not present
                    confirmations = getattr(config, "MIN_CONFIRMATIONS_TRX", 10) if is_confirmed_api else 0

                    processed_txs.append({
                        'txid': transfer['transaction_id'],
                        'amount_smallest_unit': str(amount_smallest_unit),
                        'token_symbol': 'USDT',
                        'decimals': int(transfer['token_info'].get('decimals', 6)),
                        'confirmations': confirmations, # Use API confirmed status
                        'timestamp_ms': transfer['block_timestamp'],
                    })
            logger.info(f"Found {len(processed_txs)} incoming TRC20 USDT transfers for address {address}.")
            return processed_txs
        else:
            logger.error(f"TronGrid API error for address {address}: Success flag false or no data. Response: {data.get('meta', data)}")
            # Consider raising BlockchainAPIBadResponseError if success is consistently false
            raise BlockchainAPIBadResponseError(f"TronGrid API indicated failure for {address}. Meta: {data.get('meta')}")

    except json.JSONDecodeError as e:
        logger.exception(f"TRC20 API JSONDecodeError for address {address}. URL: {url}. Error: {e}")
        raise BlockchainAPIBadResponseError(f"Failed to decode JSON response from TRC20 API for {address}", underlying_exception=e)
    except BlockchainAPIError:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error fetching TRC20 transactions for address {address}: {e}")
        raise BlockchainAPIError(f"Unexpected error during TRC20 API call for {address}", underlying_exception=e)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.info("Blockchain API module - Self-Test Mode (most tests skipped if placeholder addresses are not changed).")
