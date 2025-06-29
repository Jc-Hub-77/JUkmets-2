import sqlite3
import json
import config
import os
import datetime
import logging

logger = logging.getLogger(__name__)

try:
    from modules.utils import clear_user_state
except ImportError:
    logger.warning("Could not import clear_user_state from bot. May indicate circular dependency or bot not running in main context.")
    def clear_user_state(user_id):
        logger.info(f"Dummy clear_user_state called for {user_id} due to import issue.")
        pass

DATABASE_NAME = config.DATABASE_NAME

def get_db_connection():
    db_dir = os.path.dirname(DATABASE_NAME)
    logger.info(f"Attempting to connect to database at: {DATABASE_NAME}")
    logger.info(f"Database directory is: {db_dir}")
    logger.info(f"Database directory exists: {os.path.exists(db_dir)}")
    if not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logger.info(f"Created database directory: {db_dir}")
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    logger.info("Initializing database schema for HD wallet integration...")

    conn.execute('BEGIN')
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0.0,
                transaction_count INTEGER DEFAULT 0
            )
        ''')
        logger.debug("Users table ensured.")

        # Remove 'products' table as products are now managed by filesystem
        cursor.execute("DROP TABLE IF EXISTS products")
        logger.info("'products' table dropped as it's replaced by filesystem product management.")
        # Remove 'product_instances' table if it existed (it wasn't in the provided schema but good to ensure)
        cursor.execute("DROP TABLE IF EXISTS product_instances")
        logger.info("'product_instances' table (if existed) dropped.")


        # Adjust 'transactions' table:
        # - Remove product_id FOREIGN KEY
        # - Add item_details_json TEXT to store product info for purchases

        # Check if transactions table exists and needs migration for item_details_json
        transactions_table_exists = False
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions';")
        if cursor.fetchone():
            transactions_table_exists = True

        temp_transactions_name = "transactions_old_for_fs_item_migration"
        cursor.execute(f"DROP TABLE IF EXISTS {temp_transactions_name}")

        if transactions_table_exists:
            logger.info(f"Existing 'transactions' table found. Renaming to '{temp_transactions_name}' for schema adjustment.")
            cursor.execute(f"ALTER TABLE transactions RENAME TO {temp_transactions_name}")
        else:
            logger.info("'transactions' table not found, will be created with new schema.")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_details_json TEXT, -- Stores JSON of item details for purchase type
                type TEXT NOT NULL, -- e.g., 'purchase_crypto', 'purchase_balance', 'balance_top_up'
                eur_amount REAL NOT NULL,
                crypto_amount TEXT, -- For crypto payments
                currency TEXT, -- For crypto payments
                payment_status TEXT DEFAULT 'pending' NOT NULL,
                original_add_balance_amount REAL, -- For balance_top_up type
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
                -- Removed FOREIGN KEY (product_id)
            )
        ''')
        logger.info("'transactions' table created/ensured with item_details_json and no product_id FK.")

        # Data migration from old transactions table (if it existed) could be added here if necessary
        # For now, we are just creating the new schema. User would lose old transaction product links.
        # If temp_transactions_name table exists, log that manual migration might be needed.
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{temp_transactions_name}';")
        if cursor.fetchone():
            logger.warning(f"Table '{temp_transactions_name}' exists. Old transaction data is there. "
                           "Manual migration to the new 'transactions' table structure (especially for product details) "
                           "would be needed to preserve full history if desired. Product FK is removed.")


        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hd_address_indices (
                coin_symbol TEXT PRIMARY KEY,
                last_used_index INTEGER DEFAULT -1 NOT NULL
            )
        ''')
        logger.debug("hd_address_indices table ensured.")
        initial_coins = ['BTC', 'LTC', 'TRX']
        for coin in initial_coins:
            cursor.execute("INSERT OR IGNORE INTO hd_address_indices (coin_symbol, last_used_index) VALUES (?, -1)", (coin,))
        logger.info(f"Seeded hd_address_indices with: {', '.join(initial_coins)} (if not already present).")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_crypto_payments (
                payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id INTEGER UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                address TEXT UNIQUE NOT NULL,
                coin_symbol TEXT NOT NULL,
                network TEXT,
                expected_crypto_amount TEXT NOT NULL,
                received_crypto_amount TEXT,
                status TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                last_checked_at DATETIME,
                expires_at DATETIME NOT NULL,
                blockchain_tx_id TEXT,
                confirmations INTEGER DEFAULT 0 NOT NULL,
                paid_from_balance_eur REAL DEFAULT 0.0 NOT NULL,
                FOREIGN KEY (transaction_id) REFERENCES transactions (transaction_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        logger.debug("pending_crypto_payments table ensured.")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_payments_status ON pending_crypto_payments (status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_payments_address ON pending_crypto_payments (address);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_payments_transaction_id ON pending_crypto_payments (transaction_id);")
        logger.debug("Indexes for pending_crypto_payments ensured.")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS support_tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT DEFAULT 'open' NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_message_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                messages_json TEXT,
                admin_chat_id INTEGER,
                admin_ticket_view_message_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        logger.debug("Support tickets table ensured.")

        conn.commit()
        logger.info("Database schema initialization and HD wallet table setup complete.")
    except sqlite3.Error as e:
        logger.exception("Error during database schema initialization for HD wallet integration. Rolling back.")
        conn.rollback()
    finally:
        conn.close()


def print_users_table_schema():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        logger.info("Fetching schema for 'users' table...")
        cursor.execute("PRAGMA table_info(users);")
        schema = cursor.fetchall()
        logger.info("Schema for 'users' table:")
        for col in schema:
            logger.info(f"  CID: {col['cid']}, Name: {col['name']}, Type: {col['type']}, NotNull: {col['notnull']}, DfltValue: {col['dflt_value']}, PK: {col['pk']}")
    except sqlite3.Error as e:
        logger.exception(f"Error fetching schema for 'users' table: {e}")
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    initialize_database()
    logger.info(f"Database '{DATABASE_NAME}' initialized successfully via direct script run.")
    print_users_table_schema()


def get_or_create_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Explicitly select columns to ensure they are included in the result
    cursor.execute("SELECT user_id, balance, transaction_count FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if user is None:
        logger.info(f"User {user_id} not found, creating new user.")
        cursor.execute("INSERT INTO users (user_id, balance, transaction_count) VALUES (?, 0.0, 0)", (user_id,))
        conn.commit()
        # Fetch the newly created user with explicit column selection
        cursor.execute("SELECT user_id, balance, transaction_count FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
    conn.close()
    return user

# clear_user_process seems to be a duplicate or alternative way to call clear_user_state.
# clear_user_state itself is imported (or dummied) from modules.utils.
# This function doesn't do anything unique with the DB, so it can be removed if clear_user_state is used directly.
# For now, I'll leave it but it's redundant if modules.utils.clear_user_state is the primary.
def clear_user_process(user_id):
    logger.info(f"Clearing process state for user ID {user_id} via clear_user_state.")
    # clear_user_state(user_id) # This call should be to the imported version
    logger.debug(f"User state for {user_id} should have been cleared (Note: relies on imported clear_user_state).")
    pass

# --- HD Wallet Specific Functions ---
def get_next_address_index(coin_symbol: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Use INSERT OR IGNORE to ensure the row exists, then proceed with update
        cursor.execute("INSERT OR IGNORE INTO hd_address_indices (coin_symbol, last_used_index) VALUES (?, -1)", (coin_symbol,))
        conn.commit() # Commit the insert if it happened

        # Now get the current index and update it within a transaction
        conn.execute('BEGIN IMMEDIATE') # Start a new transaction for the update
        cursor.execute("SELECT last_used_index FROM hd_address_indices WHERE coin_symbol = ?", (coin_symbol,))
        row = cursor.fetchone()
        if row is None:
            logger.critical(f"HD Wallet Index: Coin symbol {coin_symbol} still not found after INSERT OR IGNORE.")
            conn.rollback()
            raise sqlite3.OperationalError(f"Could not find or initialize index for coin {coin_symbol}. Critical error.")
        current_index = row['last_used_index']
        new_index = current_index + 1
        cursor.execute("UPDATE hd_address_indices SET last_used_index = ? WHERE coin_symbol = ?", (new_index, coin_symbol))
        conn.commit() # Commit the update
        logger.info(f"HD Wallet Index: Next index for {coin_symbol} is {new_index} (old was {current_index}).")
        return new_index
    except sqlite3.Error as e:
        logger.exception(f"HD Wallet Index: Database error for {coin_symbol}: {e}")
        if conn:
            try: conn.rollback()
            except sqlite3.Error as rb_err:
                logger.exception(f"HD Wallet Index: Error during rollback for {coin_symbol}: {rb_err}")
        raise
    finally:
        if conn: conn.close()

# --- Pending Crypto Payments CRUD ---
def create_pending_payment(transaction_id: int, user_id: int, address: str, coin_symbol: str,
                           network: str | None, expected_crypto_amount: str, expires_at: datetime.datetime,
                           paid_from_balance_eur: float = 0.0, status: str = 'monitoring') -> int | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    now_iso = datetime.datetime.utcnow().isoformat()
    expires_at_iso = expires_at.isoformat()
    try:
        cursor.execute("""
            INSERT INTO pending_crypto_payments
            (transaction_id, user_id, address, coin_symbol, network, expected_crypto_amount, paid_from_balance_eur, status, created_at, last_checked_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (transaction_id, user_id, address, coin_symbol, network, expected_crypto_amount, paid_from_balance_eur, status, now_iso, now_iso, expires_at_iso))
        payment_id = cursor.lastrowid
        conn.commit()
        logger.info(f"Created pending payment record ID {payment_id} for main tx {transaction_id}, address {address}, paid_from_balance_eur: {paid_from_balance_eur}.")
        return payment_id
    except sqlite3.IntegrityError as e:
        logger.error(f"Integrity Error: Attempted to create duplicate pending payment for main tx {transaction_id}, address {address}. This transaction ID likely already has a pending payment entry. Error: {e}")
        conn.rollback()
        return None
    except sqlite3.Error as e:
        logger.exception(f"Failed to create pending payment for main tx {transaction_id}, address {address}: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def get_pending_payments_to_monitor(limit: int = 100) -> list[sqlite3.Row]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT * FROM pending_crypto_payments
            WHERE status = 'monitoring' AND datetime('now', 'utc') < expires_at
            ORDER BY last_checked_at ASC NULLS FIRST, created_at ASC
            LIMIT ?
        """, (limit,))
        payments = cursor.fetchall()
        logger.debug(f"Fetched {len(payments)} pending payments to monitor.")
        return payments
    except sqlite3.Error as e:
        logger.exception(f"Failed to fetch pending payments to monitor: {e}")
        return []
    finally:
        conn.close()

def update_pending_payment_check_details(payment_id: int, confirmations: int, received_amount: str | None = None, blockchain_tx_id: str | None = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    now_iso = datetime.datetime.utcnow().isoformat()
    try:
        if received_amount is not None and blockchain_tx_id is not None:
            cursor.execute("""
                UPDATE pending_crypto_payments
                SET last_checked_at = ?, confirmations = ?, received_crypto_amount = ?, blockchain_tx_id = ?
                WHERE payment_id = ?
            """, (now_iso, confirmations, received_amount, blockchain_tx_id, payment_id))
        else:
            cursor.execute("""
                UPDATE pending_crypto_payments
                SET last_checked_at = ?, confirmations = ?
                WHERE payment_id = ?
            """, (now_iso, confirmations, payment_id))
        conn.commit()
        logger.info(f"Updated check details for pending payment ID {payment_id}. Confirmations: {confirmations}.")
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.exception(f"Failed to update check details for pending payment ID {payment_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def update_pending_payment_status(payment_id: int, new_status: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    now_iso = datetime.datetime.utcnow().isoformat()
    try:
        cursor.execute("""
            UPDATE pending_crypto_payments
            SET status = ?, last_checked_at = ?
            WHERE payment_id = ?
        """, (new_status, now_iso, payment_id))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"Updated status for pending payment ID {payment_id} to {new_status}.")
        else:
            logger.warning(f"No pending payment found with ID {payment_id} to update status to {new_status}.")
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.exception(f"Failed to update status for pending payment ID {payment_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def get_confirmed_unprocessed_payments(limit: int = 100) -> list[sqlite3.Row]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT * FROM pending_crypto_payments
            WHERE status = 'confirmed_unprocessed'
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,))
        payments = cursor.fetchall()
        logger.debug(f"Fetched {len(payments)} confirmed_unprocessed payments.")
        return payments
    except sqlite3.Error as e:
        logger.exception(f"Failed to fetch confirmed_unprocessed payments: {e}")
        return []
    finally:
        conn.close()

def get_pending_payment_by_transaction_id(transaction_id: int) -> sqlite3.Row | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM pending_crypto_payments WHERE transaction_id = ?", (transaction_id,))
        payment = cursor.fetchone()
        return payment
    except sqlite3.Error as e:
        logger.exception(f"Failed to fetch pending payment by transaction_id {transaction_id}: {e}")
        return None
    finally:
        conn.close()

def get_pending_payment_by_address(address: str) -> sqlite3.Row | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM pending_crypto_payments WHERE address = ?", (address,))
        payment = cursor.fetchone()
        return payment
    except sqlite3.Error as e:
        logger.exception(f"Failed to fetch pending payment by address {address}: {e}")
        return None
    finally:
        conn.close()

def get_stale_monitoring_payments(limit: int = 100) -> list[sqlite3.Row]:
    """Fetches 'monitoring' payments that have passed their 'expires_at' time."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Using datetime('now', 'utc') for SQLite to compare with stored ISO8601 strings
        # Ensure expires_at is stored in a format comparable by SQLite's datetime functions
        cursor.execute("""
            SELECT payment_id, user_id, transaction_id, address
            FROM pending_crypto_payments
            WHERE status = 'monitoring' AND datetime('now', 'utc') >= expires_at
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,))
        payments = cursor.fetchall()
        logger.debug(f"Fetched {len(payments)} stale monitoring payments.")
        return payments
    except sqlite3.Error as e:
        logger.exception(f"Failed to fetch stale monitoring payments: {e}")
        return []
    finally:
        conn.close()

# --- General Transaction Functions (Product functions removed/to be removed) ---

# get_cities_with_available_items, get_available_items_in_city, get_product_details_by_id
# are removed as they relied on the 'products' table. Product listing is now FS based.

def update_user_balance(user_id, new_balance, increment_transactions=True):
    conn = get_db_connection()
    cursor = conn.cursor()
    if increment_transactions:
        cursor.execute("""
            UPDATE users
            SET balance = ?, transaction_count = transaction_count + 1
            WHERE user_id = ?
        """, (new_balance, user_id))
    else:
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
    conn.commit()
    conn.close()
    logger.info(f"User {user_id} balance updated to {new_balance:.2f}. Transactions incremented: {increment_transactions}")

def record_transaction(user_id: int, type: str, eur_amount: float,
                       item_details_json: str | None = None, # New field for FS-based item info
                       crypto_amount: str | None = None, currency: str | None = None,
                       payment_status: str = 'pending',
                       original_add_balance_amount: float | None = None, notes: str | None = None) -> int | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO transactions
                (user_id, item_details_json, type, eur_amount, crypto_amount, currency,
                 payment_status, original_add_balance_amount, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (user_id, item_details_json, type, eur_amount, crypto_amount, currency,
              payment_status, original_add_balance_amount, notes))
        transaction_id = cursor.lastrowid
        conn.commit()
        item_info_log = f", ItemDetails: {item_details_json[:50]}..." if item_details_json else ""
        logger.info(f"Transaction recorded: ID {transaction_id} for user {user_id}, type {type}, status {payment_status}{item_info_log}")
        return transaction_id
    except sqlite3.Error as e:
        logger.exception(f"Failed to record transaction for user {user_id}, type {type}: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def get_transaction_by_id(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,))
    transaction = cursor.fetchone()
    conn.close()
    return transaction

def update_transaction_status(transaction_id, status, notes: str | None = None) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if notes is not None:
            cursor.execute("""
                UPDATE transactions
                SET payment_status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE transaction_id = ?
            """, (status, notes, transaction_id))
        else:
            cursor.execute("""
                UPDATE transactions
                SET payment_status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE transaction_id = ?
            """, (status, transaction_id))
        conn.commit()
        if cursor.rowcount == 0:
            logger.warning(f"update_transaction_status did not update any row for TXID {transaction_id}.")
        else:
            logger.info(f"Transaction {transaction_id} status updated to {status}" + (f" with notes: {notes[:30]}..." if notes else ""))
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.exception(f"Failed to update transaction status for TXID {transaction_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

# --- Ticket System Functions ---
def get_open_ticket_for_user(user_id):
    logger.debug(f"Checking for open ticket for user_id: {user_id}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM support_tickets WHERE user_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 1",
        (user_id,)
    )
    ticket = cursor.fetchone()
    conn.close()
    if ticket: logger.debug(f"Open ticket for user {user_id}: {ticket['ticket_id']}")
    else: logger.debug(f"No open ticket for user {user_id}")
    return ticket

def create_new_ticket(user_id, initial_message_text, user_tg_message_id=None):
    logger.info(f"Creating new ticket for user {user_id}. Initial message snippet: {initial_message_text[:50]}")
    current_time_iso = datetime.datetime.utcnow().isoformat()
    message_obj = {
        "sender": "user", "text": initial_message_text, "timestamp": current_time_iso,
    }
    if user_tg_message_id is not None:
         message_obj["user_tg_message_id"] = user_tg_message_id
    messages_json_str = json.dumps([message_obj])

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO support_tickets (user_id, status, created_at, last_message_at, messages_json)
               VALUES (?, 'open', ?, ?, ?)""",
            (user_id, current_time_iso, current_time_iso, messages_json_str)
        )
        ticket_id = cursor.lastrowid
        conn.commit()
        logger.info(f"New ticket {ticket_id} created for user {user_id}.")
        return ticket_id
    except sqlite3.Error as e:
        logger.exception(f"SQLite error creating new ticket for user {user_id}: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def add_message_to_ticket(ticket_id, sender_type, message_text, user_tg_message_id=None, admin_tg_message_id=None):
    logger.info(f"Adding message to ticket {ticket_id}. Sender: {sender_type}, Text snippet: {message_text[:50]}")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT messages_json FROM support_tickets WHERE ticket_id = ?", (ticket_id,))
        row = cursor.fetchone()
        if row is None:
            logger.error(f"Ticket {ticket_id} not found when trying to add message.")
            return False

        messages_list = json.loads(row['messages_json']) if row['messages_json'] else []
        current_time_iso_msg = datetime.datetime.utcnow().isoformat()
        new_message_obj = {"sender": sender_type, "text": message_text, "timestamp": current_time_iso_msg}
        if user_tg_message_id: new_message_obj["user_tg_message_id"] = user_tg_message_id
        if admin_tg_message_id: new_message_obj["admin_tg_message_id"] = admin_tg_message_id
        messages_list.append(new_message_obj)

        current_time_iso_ticket_update = datetime.datetime.utcnow().isoformat()
        cursor.execute(
            "UPDATE support_tickets SET messages_json = ?, last_message_at = ? WHERE ticket_id = ?",
            (json.dumps(messages_list), current_time_iso_ticket_update, ticket_id)
        )
        conn.commit()
        success = cursor.rowcount > 0
        if success: logger.debug(f"Message added to ticket {ticket_id} and committed.")
        else: logger.warning(f"Failed to add message to ticket {ticket_id} (rowcount 0 after update).")
        return success
    except sqlite3.Error as e:
        logger.exception(f"SQLite error adding message to ticket {ticket_id}: {e}")
        conn.rollback()
        return False
    except json.JSONDecodeError as e_json:
        logger.exception(f"JSON error processing messages for ticket {ticket_id}: {e_json}")
        return False
    finally:
        conn.close()

def get_all_open_tickets_admin():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ticket_id, user_id, messages_json, last_message_at, status FROM support_tickets WHERE status = 'open' ORDER BY last_message_at ASC")
    tickets = cursor.fetchall()
    conn.close()
    logger.debug(f"Fetched {len(tickets)} open tickets for admin.")
    return tickets

def get_ticket_details_by_id(ticket_id: int) -> sqlite3.Row | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM support_tickets WHERE ticket_id = ?", (ticket_id,))
        ticket = cursor.fetchone()
        return ticket
    except sqlite3.Error as e:
        logger.exception(f"Error fetching ticket details for ticket_id {ticket_id}: {e}")
        return None
    finally:
        if conn: conn.close()


def update_ticket_status(ticket_id, new_status):
    conn = get_db_connection()
    cursor = conn.cursor()
    current_time_iso = datetime.datetime.utcnow().isoformat()
    try:
        cursor.execute("UPDATE support_tickets SET status = ?, last_message_at = ? WHERE ticket_id = ?", (new_status, current_time_iso, ticket_id))
        conn.commit()
        updated_rows = cursor.rowcount
        if updated_rows > 0: logger.info(f"Ticket {ticket_id} status updated to {new_status}.")
        else: logger.warning(f"No ticket found with ID {ticket_id} to update status to {new_status}.")
        return updated_rows > 0
    except sqlite3.Error as e:
        logger.exception(f"SQLite error updating status for ticket {ticket_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def update_admin_ticket_view_message_id(ticket_id, message_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE support_tickets SET admin_ticket_view_message_id = ? WHERE ticket_id = ?", (message_id, ticket_id))
        conn.commit()
        if cursor.rowcount > 0: logger.debug(f"Admin view message ID {message_id} stored for ticket {ticket_id}.")
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.exception(f"SQLite error updating admin_ticket_view_message_id for ticket {ticket_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

# get_all_products_admin is removed as 'products' table is gone. Admin will interact with FS.

def expire_old_tickets():
    conn = get_db_connection()
    cursor = conn.cursor()
    twenty_four_hours_ago_iso = (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).isoformat()
    expired_details = []
    try:
        cursor.execute("SELECT ticket_id, user_id FROM support_tickets WHERE status = 'open' AND last_message_at < ?", (twenty_four_hours_ago_iso,))
        tickets_to_expire = cursor.fetchall()
        if tickets_to_expire:
            logger.info(f"Found {len(tickets_to_expire)} tickets to auto-expire.")
            closure_time = datetime.datetime.utcnow().isoformat()
            for ticket in tickets_to_expire:
                cursor.execute("UPDATE support_tickets SET status = 'auto_expired', last_message_at = ? WHERE ticket['ticket_id'] = ?", (closure_time, ticket['ticket_id']))
                expired_details.append({'ticket_id': ticket['ticket_id'], 'user_id': ticket['user_id']})
            conn.commit()
    except sqlite3.Error as e:
        logger.exception(f"SQLite error in expire_old_tickets: {e}")
        conn.rollback()
    finally:
        conn.close()
    return expired_details

def periodic_filesystem_to_db_sync():
    # periodic_filesystem_to_db_sync is no longer needed as products table is removed.
    # If there was any other logic in it (e.g. cleaning old purchased items), that would need separate handling.
    # For now, removing the function.
    # logger.info(f"Periodic_sync: Starting periodic filesystem to DB sync at {datetime.datetime.utcnow().isoformat()} UTC...")
    # from modules import file_system_utils # Old import
    # ... (rest of old function) ...
    logger.info("Periodic_sync: periodic_filesystem_to_db_sync is now obsolete due to FS-based product management.")
    return {"status": "obsolete"}

# update_product_availability, update_product_name, etc. are removed as they target 'products' table.
# Product details are now managed via filesystem.

# delete_product_by_id is removed. Deletion is FS based + ensuring no transactions reference it if needed.

# sync_item_from_fs_to_db is removed. No DB table to sync items to.

# mark_item_as_unavailable_in_db is removed. Availability is FS based.


def increment_user_transaction_count(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        if cursor.rowcount > 0:
            logger.info(f"Incremented transaction count for user {user_id}.")
        else:
            logger.warning(f"Attempted to increment transaction count, but user {user_id} not found.")
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.exception(f"DB error incrementing transaction count for user {user_id}: {e}")
        conn.rollback()
        return False
    finally:
        if conn: conn.close()

def update_main_transaction_for_hd_payment(transaction_id: int, status: str, crypto_amount: str, currency: str) -> bool:
    """
    Updates an existing main transaction record with crypto payment details for HD wallet payments.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE transactions
            SET payment_status = ?,
                crypto_amount = ?,
                currency = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE transaction_id = ?
        """, (status, crypto_amount, currency, transaction_id))
        conn.commit()
        if cursor.rowcount == 0:
            logger.warning(f"update_main_transaction_for_hd_payment: No transaction found with ID {transaction_id} to update.")
            return False
        else:
            logger.info(f"Main transaction {transaction_id} updated for HD payment. Status: {status}, Crypto: {crypto_amount} {currency}.")
            return True
    except sqlite3.Error as e:
        logger.exception(f"Failed to update main transaction {transaction_id} for HD payment: {e}")
        conn.rollback()
        return False
    finally:
        if conn: conn.close()

def initial_sync_filesystem_to_db():
    # initial_sync_filesystem_to_db is obsolete as products table is removed.
    # The filesystem is now the source of truth for products.
    logger.info("Initial_sync_filesystem_to_db is now obsolete.")
    pass # Keep the function defined to avoid breaking existing calls in bot.py if any, but it does nothing.


def get_user_transaction_history(user_id: int, limit: int = 5, offset: int = 0) -> list[sqlite3.Row]:
    """
    Fetches a paginated transaction history for a given user.
    Product name for purchases will need to be extracted from item_details_json if displayed.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT
                transaction_id,
                type,
                eur_amount,
                crypto_amount,
                currency,
                payment_status,
                notes,
                created_at,
                original_add_balance_amount,
                item_details_json -- Include this to potentially extract item name in calling code
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(query, (user_id, limit, offset))
        transactions = cursor.fetchall()
        logger.debug(f"Fetched {len(transactions)} transactions for user {user_id} with limit {limit}, offset {offset}.")
        return transactions
    except sqlite3.Error as e:
        logger.exception(f"Failed to fetch transaction history for user {user_id}: {e}")
        return []
    finally:
        if conn:
            conn.close()

# --- Admin Item Management DB Functions --- (These are now obsolete) ---
# def add_product_type(...):
# def update_product_details(...):
# def delete_product_type_db_record(...):
# All functions that directly manipulated the 'products' table are removed or commented out
# as product management is now primarily filesystem-based.

def get_all_users_admin(limit: int = 10, offset: int = 0) -> tuple[list[sqlite3.Row], int]:
    """
    Fetches a paginated list of all users for admin view.
    Returns a list of user rows and the total count of users.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    total_users = 0
    try:
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        query = """
            SELECT user_id, balance, transaction_count
            FROM users
            ORDER BY user_id ASC
            LIMIT ? OFFSET ?
        """
        cursor.execute(query, (limit, offset))
        users = cursor.fetchall()
        logger.debug(f"Fetched {len(users)} users for admin view. Total users: {total_users}.")
        return users, total_users
    except sqlite3.Error as e:
        logger.exception(f"Failed to fetch all users for admin: {e}")
        return [], 0
    finally:
        if conn:
            conn.close()
