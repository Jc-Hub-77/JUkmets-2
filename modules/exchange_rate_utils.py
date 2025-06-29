import logging
import requests
import time
import json # For json.JSONDecodeError
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

COINGECKO_API_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_COIN_IDS = {
    "BTC": "bitcoin",
    "LTC": "litecoin",
    "USDT": "tether", # Used for USDT_TRX as well
    # Add more mappings if other cryptocurrencies are supported by the bot
}

# Simple in-memory cache: {"EUR_BTC": {"rate": Decimal("..."), "expiry": timestamp}, ...}
RATES_CACHE = {}
CACHE_DURATION_SECONDS = 300  # Cache rates for 5 minutes (300 seconds)

def get_current_exchange_rate(from_currency: str, to_currency: str) -> Decimal | None:
    """
    Fetches the current exchange rate from from_currency to to_currency using CoinGecko API.
    Currently, it's designed to fetch EUR to Crypto rates (e.g., EUR to BTC).
    The rate returned is: 1 unit of to_currency = X units of from_currency.
    Example: if from_currency="EUR", to_currency="BTC", rate is EUR per BTC.
    """
    logger.info(f"Attempting to get exchange rate for {from_currency} to {to_currency}")

    normalized_from_currency = from_currency.upper()
    normalized_to_currency = to_currency.upper()

    # Handle USDT_TRX specifically by mapping it to the general USDT CoinGecko ID
    if "USDT" in normalized_to_currency: # Handles "USDT", "USDT_TRX"
        normalized_to_currency = "USDT"

    if normalized_from_currency != "EUR":
        logger.error(f"Exchange rate lookup currently only supports EUR as the 'from_currency'. Requested: {from_currency} to {to_currency}")
        return None

    coingecko_id = COINGECKO_COIN_IDS.get(normalized_to_currency)
    if not coingecko_id:
        logger.error(f"No CoinGecko ID mapping found for currency: {normalized_to_currency} (original: {to_currency})")
        return None

    cache_key = f"{normalized_from_currency}_{normalized_to_currency}"

    # Check cache first
    cached_entry = RATES_CACHE.get(cache_key)
    if cached_entry and cached_entry['expiry'] > time.time():
        logger.info(f"Returning cached rate for {cache_key}: {cached_entry['rate']}")
        return cached_entry['rate']

    # API Call if not cached or expired
    api_url = f"{COINGECKO_API_BASE_URL}/simple/price?ids={coingecko_id}&vs_currencies={normalized_from_currency.lower()}"

    logger.debug(f"Fetching live rate from CoinGecko: {api_url}")

    try:
        response = requests.get(api_url, timeout=10) # 10-second timeout
        response.raise_for_status()  # Raises HTTPError for bad responses (4XX or 5XX)
        data = response.json()

        rate_value = data.get(coingecko_id, {}).get(normalized_from_currency.lower())

        if rate_value is not None:
            try:
                rate_decimal = Decimal(str(rate_value)) # Convert to Decimal
                # Update Cache
                RATES_CACHE[cache_key] = {"rate": rate_decimal, "expiry": time.time() + CACHE_DURATION_SECONDS}
                logger.info(f"Fetched and cached new rate for {cache_key}: {rate_decimal}")
                return rate_decimal
            except InvalidOperation:
                logger.error(f"Invalid rate value received from CoinGecko for {cache_key}: {rate_value}")
                return None
        else:
            logger.error(f"Rate not found in CoinGecko response for {cache_key}. Response: {data}")
            return None

    except requests.exceptions.Timeout:
        logger.error(f"Timeout while fetching exchange rate from CoinGecko for {cache_key}: {api_url}")
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error occurred while fetching exchange rate for {cache_key}: {http_err} - URL: {api_url}")
    except requests.exceptions.RequestException as req_err: # Catch other requests errors (network, etc.)
        logger.error(f"Request exception occurred while fetching exchange rate for {cache_key}: {req_err} - URL: {api_url}")
    except json.JSONDecodeError as json_err:
        logger.error(f"Failed to decode JSON response from CoinGecko for {cache_key}: {json_err}. Response text: {response.text if 'response' in locals() else 'N/A'}")
    except (KeyError, TypeError) as e_parse: # Catch potential errors if response structure is unexpected
        logger.error(f"Error parsing CoinGecko response for {cache_key}: {e_parse}. Data: {data if 'data' in locals() else 'N/A'}")
    except Exception as e: # Catch-all for any other unexpected errors
        logger.exception(f"An unexpected error occurred in get_current_exchange_rate for {cache_key}: {e}")

    return None

if __name__ == '__main__':
    # Simple test cases
    logging.basicConfig(level=logging.INFO)

    print("--- Testing EUR to Crypto ---")
    rate_btc = get_current_exchange_rate("EUR", "BTC")
    print(f"EUR to BTC: {rate_btc}")

    rate_ltc = get_current_exchange_rate("EUR", "LTC")
    print(f"EUR to LTC: {rate_ltc}")

    rate_usdt = get_current_exchange_rate("EUR", "USDT")
    print(f"EUR to USDT: {rate_usdt}")

    rate_usdt_trx = get_current_exchange_rate("EUR", "USDT_TRX")
    print(f"EUR to USDT_TRX (should use USDT): {rate_usdt_trx}")

    print("\n--- Testing Cache ---")
    time.sleep(1) # Ensure timestamp is different for next call if it were to miss cache due to sub-second expiry
    rate_btc_cached = get_current_exchange_rate("EUR", "BTC")
    print(f"EUR to BTC (cached): {rate_btc_cached}")

    print("\n--- Testing Unsupported ---")
    rate_eth = get_current_exchange_rate("EUR", "ETH") # Assuming ETH is not in COINGECKO_COIN_IDS
    print(f"EUR to ETH (unsupported in map): {rate_eth}")

    rate_usd_btc = get_current_exchange_rate("USD", "BTC") # Unsupported 'from_currency'
    print(f"USD to BTC (unsupported from_currency): {rate_usd_btc}")

    # Test cache expiry (manual test would involve waiting > CACHE_DURATION_SECONDS)
    # print(f"\n--- Waiting for cache to expire ({CACHE_DURATION_SECONDS}s)... ---")
    # time.sleep(CACHE_DURATION_SECONDS + 5)
    # rate_btc_expired_cache = get_current_exchange_rate("EUR", "BTC")
    # print(f"EUR to BTC (after cache expiry): {rate_btc_expired_cache}")
    # print(f"Cache content: {RATES_CACHE}")
