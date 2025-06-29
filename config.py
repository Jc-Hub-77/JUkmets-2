# This file will store all the configurations and API keys.

BOT_TOKEN = '6947523327:AAF-j1t_wKZ4uLr3serfu08Wt37BfX41IGE'
ADMIN_ID = '979517124' # Your Telegram User ID
SERVICE_FEE_EUR = 0.50 # Service fee for item purchases
ADD_BALANCE_SERVICE_FEE_EUR = 0.25 # Service fee for adding balance

# --- Payment Configuration ---
PAYMENT_WINDOW_MINUTES = 60 # Time in minutes an address is kept active for monitoring a new payment.

import os
DATABASE_NAME = os.path.join(os.path.dirname(__file__), 'data', 'database', 'bot_database.db')
ITEMS_BASE_DIR = 'data/items'
PURCHASED_ITEMS_BASE_DIR = 'data/purchased_items'

# Image Paths (optional, can be None or actual local paths to images)
# These are used in various flows to display images.
ACCOUNT_IMAGE_PATH = "assets/images/account_flow_image.png" # Example local path
BUY_FLOW_IMAGE_PATH = "assets/images/buy_flow_initiate_image.png" # Example local path

# --- HD Wallet Configuration ---
# WARNING: STORING YOUR SEED PHRASE (MNEMONIC) IN A CONFIG FILE IS EXTREMELY INSECURE.
# If this server or file is compromised, ALL YOUR FUNDS associated with this seed could be STOLEN.
# This is intended for advanced users who understand and accept this significant risk.
# Consider hardware wallets or more secure key management solutions for production use.
# Ensure this file has strict permissions and the server is highly secured if you proceed.
# For development, use a testnet seed phrase with no real value.
SEED_PHRASE = "merit step nuclear digital appear project innocent doll genre educate swing pluck"

# Standard gap limit for address discovery in HD wallets.
ACCOUNT_DISCOVERY_GAP_LIMIT = 20

# Minimum number of confirmations required for a transaction to be considered valid.
MIN_CONFIRMATIONS_BTC = 1
MIN_CONFIRMATIONS_LTC = 3  # Example: Litecoin often uses more confirmations than BTC
MIN_CONFIRMATIONS_TRX = 10 # Example: Tron confirmations are fast, so a higher number is still quick

# Derivation Paths: Standard BIP44 paths will be used by default within the wallet utility.
# (e.g., m/44'/0'/0'/0/{index} for BTC, m/44'/2'/0'/0/{index} for LTC, m/44'/195'/0'/0/{index} for TRX).
# If non-standard paths are needed, they would typically be configured within the wallet utility itself
# or explicitly passed to its functions, rather than making config.py overly complex.

# --- Blockchain API Configuration ---
# API keys for block explorers or connection details for your own nodes.
# Fill these if you are using public block explorer APIs that require them for higher rate limits
# or specific functionalities. Many basic endpoints work without API keys.

# Example for BlockCypher API (for BTC, LTC)
BLOCKCYPHER_API_TOKEN = "" # Optional, some endpoints might work without it but may be rate-limited

# Example for TronGrid API (for TRX and TRC20 tokens like USDT)
TRONGRID_API_KEY = "" # Optional, but recommended for higher rate limits if using TronGrid

# Official USDT TRC20 contract address on the Tron network. This is a fixed value.
USDT_TRC20_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# Ensure to replace placeholder values like 'YOUR_TELEGRAM_BOT_TOKEN'
# and 'YOUR_TELEGRAM_ADMIN_ID' with your actual credentials and IDs.
# Crucially, replace the placeholder SEED_PHRASE with your actual seed phrase
# ONLY if you understand and accept the security risks involved.

# --- Scheduler Configuration (Defaults used in bot.py if not set here) ---
# These settings control the timing of various background tasks.
# You can uncomment and adjust these values. If they remain commented or are not present,
# the bot will use the default values specified in bot.py via getattr().

# SCHEDULER_INIT_DELAY_TICKET_EXPIRY_SECONDS = 10  # Initial delay (seconds) before the first ticket expiration check.
# SCHEDULER_INTERVAL_TICKET_EXPIRY_SECONDS = 3600 # Interval (seconds) between ticket expiration checks (e.g., 1 hour).
# SCHEDULER_INIT_DELAY_ITEM_SYNC_SECONDS = 20    # Initial delay (seconds) before the first item availability sync.
# SCHEDULER_INTERVAL_ITEM_SYNC_SECONDS = 3600  # Interval (seconds) between item availability syncs (e.g., 1 hour).
# SCHEDULER_INIT_DELAY_PAYMENT_CHECK_SECONDS = 30 # Initial delay (seconds) before the first pending crypto payment check.
# SCHEDULER_INTERVAL_PAYMENT_CHECK_SECONDS = 120 # Interval (seconds) between pending crypto payment checks (e.g., 2 minutes).
# SCHEDULER_INIT_DELAY_PROCESS_CONFIRMED_SECONDS = 15 # Initial delay (seconds) before first processing of confirmed payments.
# SCHEDULER_INTERVAL_PROCESS_CONFIRMED_SECONDS = 60 # Interval (seconds) for processing confirmed payments (e.g., 1 minute).
# SCHEDULER_INIT_DELAY_EXPIRE_PAYMENTS_SECONDS = 60 # Initial delay (seconds) before first check for expiring stale payments.
# SCHEDULER_INTERVAL_EXPIRE_PAYMENTS_SECONDS = 300 # Interval (seconds) for expiring stale payments (e.g., 5 minutes).

# --- Blockchain API Call Delays (Defaults used in modules if not set here) ---
# This can help manage rate limiting if you are using public API endpoints without keys.
# BLOCKCHAIN_API_CALL_DELAY_SECONDS = 2.0 # General delay (seconds) between calls in payment_monitor loops to different APIs.
                                         # Individual API modules might have their own specific internal delays or logic.
