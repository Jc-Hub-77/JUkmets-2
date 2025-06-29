import logging
import os
import time # For QR code filenames
import qrcode # For QR code generation
from mnemonic import Mnemonic # For seed phrase validation and generation (if needed)
from bip_utils import (
    Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes,
    Base58ChecksumError, # For handling potential address errors if we were to validate them
)
import config # For SEED_PHRASE and other potential configs

logger = logging.getLogger(__name__)

# --- HD Wallet Configuration & Constants ---

# Standard BIP44 derivation: m / purpose' / coin_type' / account' / change / address_index
BIP44_PURPOSE = 44
BIP44_ACCOUNT = 0 # Default account
BIP44_CHANGE = Bip44Changes.CHAIN_EXT # External chain (receiving addresses)

COIN_MAP = {
    "BTC": {"coin_type": Bip44Coins.BITCOIN, "uri_prefix": "bitcoin"},
    "LTC": {"coin_type": Bip44Coins.LITECOIN, "uri_prefix": "litecoin"},
    "TRX": {"coin_type": Bip44Coins.TRON, "uri_prefix": "tron"}, # Tron for USDT TRC20 addresses
    # Add other supported coins here if needed
}

# Directory for storing generated QR codes
QR_CODE_DIR = os.path.join("assets", "qr_codes")
if not os.path.exists(QR_CODE_DIR):
    try:
        os.makedirs(QR_CODE_DIR)
        logger.info(f"Created QR code directory at: {QR_CODE_DIR}")
    except OSError as e:
        logger.exception(f"Could not create QR code directory at {QR_CODE_DIR}: {e}")
        # Depending on how critical QR codes are, this could raise an error or just log.

def validate_seed_phrase() -> bool:
    """
    Validates the SEED_PHRASE from config.py.
    Returns True if valid, False otherwise.
    """
    seed_phrase = getattr(config, 'SEED_PHRASE', None)

    if not seed_phrase or seed_phrase == "your actual twelve (or 24) word bip39 mnemonic seed phrase here replace this entire string":
        logger.critical("SEED_PHRASE is not configured or is still set to the placeholder value in config.py. HD wallet functionality will not work.")
        logger.critical("Please generate a secure seed phrase and update config.py. FOR DEVELOPMENT/TESTING ONLY, NEVER USE REAL FUNDS WITH A SEED PHRASE STORED THIS WAY IN PRODUCTION.")
        return False

    try:
        # Mnemonic("english") is the default and typically what bip_utils/Bip39SeedGenerator expects
        is_valid = Mnemonic("english").check(seed_phrase)
        if not is_valid:
            logger.error("SEED_PHRASE from config.py is not a valid BIP39 mnemonic.")
            return False
    except Exception as e:
        logger.exception(f"An unexpected error occurred during seed phrase validation: {e}")
        return False

    logger.info("SEED_PHRASE successfully validated (format appears correct).")
    return True

def generate_address(coin_symbol: str, index: int) -> str | None:
    """
    Generates a cryptocurrency address for the given coin symbol and index
    using the SEED_PHRASE from config.py and standard BIP44 derivation.

    Args:
        coin_symbol: The symbol of the coin (e.g., "BTC", "LTC", "TRX").
        index: The address index to derive.

    Returns:
        The generated address string, or None if an error occurs.
    """
    logger.debug(f"Attempting to generate address for {coin_symbol}, index {index}.")
    seed_phrase = getattr(config, 'SEED_PHRASE', None)

    if not seed_phrase or seed_phrase == "your actual twelve (or 24) word bip39 mnemonic seed phrase here replace this entire string":
        logger.error(f"Cannot generate address for {coin_symbol}: SEED_PHRASE is not configured or is a placeholder.")
        return None

    if coin_symbol not in COIN_MAP:
        logger.error(f"Unsupported coin symbol for address generation: {coin_symbol}")
        return None

    coin_config = COIN_MAP[coin_symbol]

    try:
        seed_bytes = Bip39SeedGenerator(seed_phrase).Generate()
        bip44_mst_ctx = Bip44.FromSeed(seed_bytes, coin_config["coin_type"])

        # Derive path: m / purpose' / coin_type' / account' / change / address_index
        # Derive path: m / purpose' / coin_type' / account' / change / address_index
        # Speculative fix based on TypeError: Bip44.Purpose() takes 1 positional argument but 2 were given
        # Assuming Purpose is a class method taking only the master context
        bip44_acc_ctx = Bip44.Purpose(bip44_mst_ctx).Coin().Account(BIP44_ACCOUNT)
        bip44_chg_ctx = bip44_acc_ctx.Change(BIP44_CHANGE) # External chain
        bip44_addr_ctx = bip44_chg_ctx.AddressIndex(index)

        address = bip44_addr_ctx.PublicKey().ToAddress()
        logger.info(f"Generated {coin_symbol} address at index {index}: {address}")
        return address

    except Exception as e_bip:
        # Catching a general Exception as Bip32DerivationError might not be directly importable
        # In a production environment, it's better to import the specific error if possible.
        # For now, we log the exception and return None.
        logger.exception(f"Error deriving address for {coin_symbol} at index {index}: {e_bip}")
        return None


def generate_qr_code_for_address(address: str, crypto_amount: str | None = None, coin_symbol: str | None = None, message: str | None = None) -> str | None:
    """
    Generates a QR code for a given cryptocurrency address, optionally including amount and message
    as part of a payment URI (e.g., bitcoin:address?amount=0.1).
    Saves the QR code image to 'assets/qr_codes/' and returns the file path.

    Args:
        address: The cryptocurrency address.
        crypto_amount: Optional. The amount of cryptocurrency for the payment URI.
        coin_symbol: Optional. The coin symbol (e.g., "BTC", "LTC") to determine URI prefix.
        message: Optional. A message/label for the payment URI.

    Returns:
        The file path to the generated QR code image, or None if an error occurs.
    """
    if not address:
        logger.warning("generate_qr_code_for_address called with no address.")
        return None

    payment_uri = address
    if coin_symbol and coin_symbol in COIN_MAP:
        uri_prefix = COIN_MAP[coin_symbol]["uri_prefix"]
        payment_uri = f"{uri_prefix}:{address}"

        params = []
        if crypto_amount:
            # Ensure crypto_amount is URL-safe if it can contain special characters (though usually just a number)
            params.append(f"amount={crypto_amount}")
        if message:
            # qrcode.escape might not be standard. Use urllib.parse.quote_plus for URI parameters.
            from urllib.parse import quote_plus
            params.append(f"message={quote_plus(message)}")

        if params:
            payment_uri += "?" + "&".join(params)
    else: # No coin symbol or not in map, QR will just be the address
        logger.info(f"Generating QR code for address only: {address} (no coin symbol or prefix found).")


    logger.debug(f"Generating QR code for payment URI: {payment_uri}")

    try:
        # Ensure QR_CODE_DIR exists (it's created at module load, but double check)
        if not os.path.exists(QR_CODE_DIR):
            os.makedirs(QR_CODE_DIR)
            logger.info(f"Re-created QR code directory at: {QR_CODE_DIR}")

        img = qrcode.make(payment_uri)

        # Create a somewhat unique filename
        # Using a hash of the address + timestamp for more uniqueness if needed,
        # but simple address might be fine if overwriting is acceptable or if address itself is unique enough.
        # For simplicity, let's use address (sanitized) and a timestamp to avoid clashes.
        sanitized_address = "".join(c for c in address if c.isalnum())
        filename = f"qr_{sanitized_address}_{int(time.time())}.png"
        qr_file_path = os.path.join(QR_CODE_DIR, filename)

        img.save(qr_file_path)
        logger.info(f"QR code generated and saved to: {qr_file_path}")
        return qr_file_path

    except Exception as e:
        logger.exception(f"Error generating or saving QR code for address {address}: {e}")
        return None

# Example self-check on module load (optional, can be called from bot.py)
# if not validate_seed_phrase():
#    logger.critical("HD Wallet utilities will not function correctly due to invalid seed phrase.")
    # Consider raising a more specific error or exiting if this is critical for bot operation.
