import telebot
import os
import config
import sqlite3
from threading import Thread
import logging
import time # For scheduler
from modules import db_utils
from modules import payment_monitor # Import the new payment monitor
from modules.utils import update_user_state, get_user_state, clear_user_state
from modules import text_utils # Import text_utils
import sys # Import sys for stdout

# Basic logging configuration
# Configure both file and console logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_activity.log', mode='a'),
        logging.StreamHandler(sys.stdout) # Add this handler for console output
    ]
)
logger = logging.getLogger(__name__)

# Bot token and admin ID
BOT_TOKEN = config.BOT_TOKEN
ADMIN_ID = config.ADMIN_ID

# Create bot instance
# Explicitly set threaded=False to see if it affects callback query handling
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

# --- Configuration ---
SERVICE_FEE_EUR = getattr(config, 'SERVICE_FEE_EUR', 0.50) # Default if not in config
ADD_BALANCE_SERVICE_FEE_EUR = getattr(config, 'ADD_BALANCE_SERVICE_FEE_EUR', 0.25) # Default if not in config


# Initialize database
from modules.db_utils import initialize_database, initial_sync_filesystem_to_db, print_users_table_schema
logger.info("Initializing database...")
initialize_database()
# Call the schema printing function after initialization
print_users_table_schema()
logger.info("Performing initial filesystem to DB sync...")
initial_sync_filesystem_to_db()
logger.info("Initial sync complete.")

# Validate HD Wallet Seed Phrase
from modules.hd_wallet_utils import validate_seed_phrase
if not validate_seed_phrase():
    logger.critical("CRITICAL: HD Wallet seed phrase is invalid or not configured properly. Payment functionalities will FAIL. Please check config.py and ensure SEED_PHRASE is a valid BIP39 mnemonic.")
    # Depending on desired behavior, you might want to exit or prevent the bot from fully starting here.
    # For now, it will log critically and continue, but payments will not work.
else:
    logger.info("HD Wallet seed phrase validated successfully.")

# Import handlers
logger.info("Importing handlers...")
from handlers import main_menu_handler
from handlers import buy_flow_handler
from handlers import add_balance_handler
from handlers import account_handler
from handlers import support_handler
from handlers import admin_handler # admin_handler is also imported

logger.info("Handlers imported.")

# Register handlers after bot instance is created
logger.info("Registering handlers...")

# --- Main Menu and Navigation ---
@bot.message_handler(commands=['start'])
def start_command_wrapper(message):
    logger.debug(f"Received /start command from user {message.from_user.id}")
    main_menu_handler.handle_start(bot, clear_user_state, get_user_state, update_user_state, message)

# --- Admin User Management Handlers (Registered after bot instance creation) ---
bot.message_handler(commands=['viewusers'], func=admin_handler.is_admin)(
    lambda message: admin_handler.command_view_users(bot, clear_user_state, get_user_state, update_user_state, message)
)
bot.callback_query_handler(func=lambda call: call.data.startswith('admin_users_page_') and admin_handler.is_admin(call))(
    lambda call: admin_handler.callback_view_users_page(bot, clear_user_state, get_user_state, update_user_state, call)
)
bot.callback_query_handler(func=lambda call: call.data.startswith('admin_view_user_details_') and admin_handler.is_admin(call))(
    lambda call: admin_handler.handle_admin_view_user_details_callback(bot, clear_user_state, get_user_state, update_user_state, call)
)
bot.callback_query_handler(func=lambda call: call.data == 'admin_back_to_user_list' and admin_handler.is_admin(call))(
    lambda call: admin_handler.handle_admin_back_to_user_list_callback(bot, clear_user_state, get_user_state, update_user_state, call)
)

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_main')
def back_to_main_callback_wrapper(call):
    logger.debug(f"Received back_to_main callback from user {call.from_user.id}")
    main_menu_handler.handle_back_to_main_menu_callback(bot, clear_user_state, get_user_state, update_user_state, call)

# --- Buy Flow Handlers ---
@bot.callback_query_handler(func=lambda call: call.data == 'buy_initiate')
def buy_initiate_callback_wrapper(call):
    logger.debug(f"Received buy_initiate callback from user {call.from_user.id}")
    buy_flow_handler.handle_buy_initiate_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_city_'))
def city_selection_callback_wrapper(call):
    logger.debug(f"Received select_city_ callback from user {call.from_user.id}: {call.data}")
    buy_flow_handler.handle_city_selection_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_item_'))
def item_selection_callback_wrapper(call):
    logger.debug(f"Received select_item_ callback from user {call.from_user.id}: {call.data}")
    buy_flow_handler.handle_item_selection_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('pay_buy_'))
def pay_buy_crypto_callback_wrapper(call):
    logger.debug(f"Received pay_buy_ callback from user {call.from_user.id}: {call.data}")
    buy_flow_handler.handle_pay_buy_crypto_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_buy_payment_'))
def check_buy_payment_callback_wrapper(call):
    logger.debug(f"Received check_buy_payment_ callback from user {call.from_user.id}: {call.data}")
    buy_flow_handler.handle_buy_check_payment_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_buy_payment_'))
def cancel_buy_payment_callback_wrapper(call):
    logger.debug(f"Received cancel_buy_payment_ callback from user {call.from_user.id}: {call.data}")
    buy_flow_handler.handle_cancel_buy_payment_callback(bot, clear_user_state, get_user_state, update_user_state, call)


# --- Add Balance Flow Handlers ---
@bot.callback_query_handler(func=lambda call: call.data == 'main_add_balance')
def add_balance_callback_wrapper(call):
    logger.debug(f"Received main_add_balance callback from user {call.from_user.id}")
    add_balance_handler.handle_add_balance_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.message_handler(func=lambda message: get_user_state(message.from_user.id, 'current_flow') == 'add_balance_awaiting_amount', content_types=['text'])
def amount_input_for_add_balance_wrapper(message):
    logger.debug(f"Received amount input for add balance from user {message.from_user.id}: {message.text}")
    add_balance_handler.handle_amount_input_for_add_balance(bot, clear_user_state, get_user_state, update_user_state, message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('pay_balance_'))
def pay_balance_crypto_callback_wrapper(call):
    logger.debug(f"Received pay_balance_ callback from user {call.from_user.id}: {call.data}")
    add_balance_handler.handle_pay_balance_crypto_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_bal_payment_'))
def check_add_balance_payment_callback_wrapper(call):
    logger.debug(f"Received check_bal_payment_ callback from user {call.from_user.id}: {call.data}")
    add_balance_handler.handle_check_add_balance_payment_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_bal_payment_'))
def cancel_add_balance_payment_callback_wrapper(call):
    logger.debug(f"Received cancel_bal_payment_ callback from user {call.from_user.id}: {call.data}")
    add_balance_handler.handle_cancel_add_balance_payment_callback(bot, clear_user_state, get_user_state, update_user_state, call)


# --- Account Flow Handlers ---
@bot.callback_query_handler(func=lambda call: call.data == 'main_account')
def account_callback_wrapper(call):
    logger.debug(f"Received main_account callback from user {call.from_user.id}")
    # This will call the function to be implemented in account_handler.py
    account_handler.handle_account_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('view_tx_history_page_'))
def view_full_history_callback_wrapper(call):
    logger.debug(f"Received view_tx_history_page_ callback from user {call.from_user.id}: {call.data}")
    # This will call the function to be implemented in account_handler.py
    account_handler.handle_view_full_history_callback(bot, clear_user_state, get_user_state, update_user_state, call)


# --- Support Flow Handlers ---
@bot.callback_query_handler(func=lambda call: call.data == 'support_initiate')
def support_initiate_callback_wrapper(call):
    logger.debug(f"Received support_initiate callback from user {call.from_user.id}")
    support_handler.handle_support_initiate_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.message_handler(
    func=lambda message: (
        get_user_state(message.from_user.id, 'current_flow') == 'support_info_displayed' or
        (get_user_state(message.from_user.id, 'current_flow') and
         get_user_state(message.from_user.id, 'current_flow').startswith('in_support_ticket_')) or
        (
            get_user_state(message.from_user.id, 'current_ticket_id') is not None and
            db_utils.get_ticket_details_by_id(get_user_state(message.from_user.id, 'current_ticket_id'))['status'] == 'open' # Ensure direct DB check for active ticket
        )
    ) and message.chat.type == 'private' and not (message.text and message.text.startswith('/')), # Ensure it's not a command
    content_types=['text', 'photo']
)
def support_message_wrapper(message):
    logger.debug(f"Received support message from user {message.from_user.id}")
    support_handler.handle_support_message(bot, clear_user_state, get_user_state, update_user_state, message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('user_close_ticket_'))
def user_close_ticket_callback_wrapper(call):
    logger.debug(f"Received user_close_ticket_ callback from user {call.from_user.id}: {call.data}")
    support_handler.handle_user_close_ticket_callback(bot, clear_user_state, get_user_state, update_user_state, call)

# --- Admin Handlers ---

# --- Admin Item Addition Flow ---
@bot.message_handler(commands=['add'], func=lambda message: admin_handler.is_admin(message.from_user.id))
def admin_add_item_command_wrapper(message):
    logger.debug(f"Received /add command from admin {message.from_user.id}")
    admin_handler.handle_admin_add_item_command(bot, clear_user_state, get_user_state, update_user_state, message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_add_') and admin_handler.is_admin(call.from_user.id))
def admin_add_item_step_callback_wrapper(call):
    logger.debug(f"Received admin_add_ callback from admin {call.from_user.id}: {call.data}")
    if call.data == 'admin_add_item_cancel':
        admin_handler.handle_admin_add_item_cancel_callback(bot, clear_user_state, get_user_state, update_user_state, call)
    elif call.data == 'admin_add_item_execute':
        admin_handler.handle_admin_add_item_execute(bot, clear_user_state, get_user_state, update_user_state, call)
    elif call.data == 'admin_add_item_restart': # Added restart option from confirm step
        # Effectively same as /add command to restart the flow
        # Simulate a message object for handle_admin_add_item_command
        mock_message = telebot.types.Message(
            message_id=call.message.message_id, # Use existing message context if possible
            from_user=call.from_user,
            date=call.message.date if call.message else int(time.time()),
            chat=call.message.chat if call.message else types.Chat(id=call.from_user.id, type='private'), # Ensure chat is valid
            content_type='text',
            options={},
            json_string=""
        )
        mock_message.text = "/add" # For logging or potential future use
        admin_handler.handle_admin_add_item_command(bot, clear_user_state, get_user_state, update_user_state, mock_message)
        bot.answer_callback_query(call.id, "Restarting item addition.")
        if call.message: # Attempt to delete the confirmation message
            try: delete_message(bot, call.message.chat.id, call.message.message_id)
            except: pass
    else:
        # General step progression via callbacks (select city, area, type, size)
        admin_handler.handle_admin_add_item_step_callback(bot, clear_user_state, get_user_state, update_user_state, call)

# Message handler for text inputs during admin item addition (new city name, price, description etc.)
@bot.message_handler(
    func=lambda message: admin_handler.is_admin(message.from_user.id) and \
                         get_user_state(message.from_user.id, 'admin_add_item_flow') and \
                         get_user_state(message.from_user.id, 'admin_add_item_flow').get('step') not in ['awaiting_images', 'confirm_add'], # these are handled by photo or callback
    content_types=['text']
)
def admin_add_item_text_input_wrapper(message):
    logger.debug(f"Received admin add item text input from admin {message.from_user.id}")
    # Avoid conflict with /done_images or other commands if they are introduced for text steps
    if message.text.startswith('/'):
        # Let other command handlers pick it up if it's not /done_images for the image step
        # (which is handled by admin_add_item_images_wrapper)
        return
    admin_handler.handle_admin_add_item_text_input(bot, clear_user_state, get_user_state, update_user_state, message)

# Message handler for photo uploads or /done_images command during admin item addition
@bot.message_handler(
    func=lambda message: admin_handler.is_admin(message.from_user.id) and \
                         get_user_state(message.from_user.id, 'admin_add_item_flow') and \
                         get_user_state(message.from_user.id, 'admin_add_item_flow').get('step') == 'awaiting_images',
    content_types=['photo', 'text'] # Text for /done_images
)
def admin_add_item_images_wrapper(message):
    logger.debug(f"Received admin add item image/text input from admin {message.from_user.id}")
    if message.text and not message.text.lower() == '/done_images':
        # If it's text but NOT /done_images during image step, send a reminder.
        # This could be handled inside handle_admin_add_item_images_input too.
        bot.send_message(message.chat.id, "Please send a photo or type /done_images.")
        try: delete_message(bot, message.chat.id, message.message_id) # delete the invalid text
        except: pass
        return
    admin_handler.handle_admin_add_item_images_input(bot, clear_user_state, get_user_state, update_user_state, message)


# --- Admin Ticket Management Handlers ---
@bot.message_handler(commands=['tickets'], func=lambda message: admin_handler.is_admin(message.from_user.id))
def admin_list_tickets_wrapper(message):
    logger.debug(f"Received /tickets command from admin {message.from_user.id}")
    # Assuming handle_admin_list_tickets_command is updated to accept bot_instance and state utils
    admin_handler.handle_admin_list_tickets_command(bot, clear_user_state, get_user_state, update_user_state, message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_list_tickets_page_') and admin_handler.is_admin(call.from_user.id))
def admin_list_tickets_page_wrapper(call):
    logger.debug(f"Received admin_list_tickets_page_ callback from admin {call.from_user.id}: {call.data}")
    admin_handler.handle_admin_list_tickets_page_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_view_ticket_') and admin_handler.is_admin(call.from_user.id))
def admin_view_ticket_wrapper(call):
    logger.debug(f"Received admin_view_ticket_ callback from admin {call.from_user.id}: {call.data}")
    admin_handler.handle_admin_view_ticket_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_list_tickets_cmd_from_view' and admin_handler.is_admin(call.from_user.id))
def admin_list_tickets_cmd_from_view_wrapper(call):
    logger.debug(f"Received admin_list_tickets_cmd_from_view callback from admin {call.from_user.id}")
    admin_handler.handle_admin_list_tickets_cmd_from_view_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_reply_ticket_') and admin_handler.is_admin(call.from_user.id))
def admin_initiate_reply_wrapper(call):
    logger.debug(f"Received admin_reply_ticket_ callback from admin {call.from_user.id}: {call.data}")
    admin_handler.handle_admin_initiate_reply_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.message_handler(
    func=lambda message: admin_handler.is_admin(message.from_user.id) and \
                         get_user_state(message.from_user.id, 'admin_flow') == 'awaiting_admin_reply_text',
    content_types=['text', 'photo']
)
def admin_ticket_reply_message_wrapper(message):
    logger.debug(f"Received admin ticket reply from admin {message.from_user.id}")
    admin_handler.handle_admin_ticket_reply_message_content(bot, clear_user_state, get_user_state, update_user_state, message)

@bot.message_handler(commands=['cancel_admin_action'], func=lambda message: admin_handler.is_admin(message.from_user.id))
def admin_cancel_action_wrapper(message):
    logger.debug(f"Received /cancel_admin_action command from admin {message.from_user.id}")
    admin_handler.handle_general_cancel_admin_action(bot, clear_user_state, get_user_state, update_user_state, message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_close_ticket_') and admin_handler.is_admin(call.from_user.id))
def admin_close_ticket_wrapper(call):
    logger.debug(f"Received admin_close_ticket_ callback from admin {call.from_user.id}: {call.data}")
    admin_handler.handle_admin_close_ticket_callback(bot, clear_user_state, get_user_state, update_user_state, call)


# --- Admin User Management Handlers ---
@bot.message_handler(commands=['viewusers'], func=lambda message: admin_handler.is_admin(message.from_user.id))
def admin_view_users_wrapper(message):
    logger.debug(f"Received /viewusers command from admin {message.from_user.id}")
    admin_handler.command_view_users(bot, clear_user_state, get_user_state, update_user_state, message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_users_page_') and admin_handler.is_admin(call.from_user.id))
def admin_users_page_wrapper(call):
    logger.debug(f"Received admin_users_page_ callback from admin {call.from_user.id}: {call.data}")
    admin_handler.callback_view_users_page(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_view_user_details_') and admin_handler.is_admin(call.from_user.id))
def admin_view_user_details_wrapper(call):
    logger.debug(f"Received admin_view_user_details_ callback from admin {call.from_user.id}: {call.data}")
    admin_handler.handle_admin_view_user_details_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_back_to_user_list' and admin_handler.is_admin(call.from_user.id))
def admin_back_to_user_list_wrapper(call):
    logger.debug(f"Received admin_back_to_user_list callback from admin {call.from_user.id}")
    admin_handler.handle_admin_back_to_user_list_callback(bot, clear_user_state, get_user_state, update_user_state, call)

# Note: Admin adjust balance flow is not explicitly in the plan but exists in admin_handler.
# If it's to be kept, it also needs its decorators removed and registration here.
# For now, focusing on what was in the plan / explicitly mentioned as problematic.


# --- Add Balance Change Payment Confirmation Handlers ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_change_payment_'))
def confirm_change_payment_callback_wrapper(call):
    logger.debug(f"Received confirm_change_payment_ callback from user {call.from_user.id}: {call.data}")
    add_balance_handler.handle_confirm_change_payment_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('execute_change_payment_'))
def execute_change_payment_callback_wrapper(call):
    logger.debug(f"Received execute_change_payment_ callback from user {call.from_user.id}: {call.data}")
    add_balance_handler.handle_execute_change_payment_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_change_payment_'))
def cancel_change_payment_callback_wrapper(call):
    logger.debug(f"Received cancel_change_payment_ callback from user {call.from_user.id}: {call.data}")
    add_balance_handler.handle_cancel_change_payment_callback(bot, clear_user_state, get_user_state, update_user_state, call)

# --- Add Balance Copy Handlers ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('copy_address_'))
def copy_address_callback_wrapper(call):
    logger.debug(f"Received copy_address_ callback from user {call.from_user.id}: {call.data}")
    add_balance_handler.handle_copy_address_callback(bot, clear_user_state, get_user_state, update_user_state, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('copy_amount_'))
def copy_amount_callback_wrapper(call):
    logger.debug(f"Received copy_amount_ callback from user {call.from_user.id}: {call.data}")
    add_balance_handler.handle_copy_amount_callback(bot, clear_user_state, get_user_state, update_user_state, call)


# Custom update listener to log all incoming updates
def handle_updates(updates):
    for update in updates:
        logger.info(f"Received update: {update}")
        # You could add logic here to manually process updates if needed
        # For now, just logging to see what updates are received

# --- Scheduled Tasks ---
def scheduled_ticket_expiration_check():
    logger.info("Scheduler: Ticket expiration check thread started.")
    # Use getattr for config values with defaults
    init_delay = getattr(config, 'SCHEDULER_INIT_DELAY_TICKET_EXPIRY_SECONDS', 10)
    interval = getattr(config, 'SCHEDULER_INTERVAL_TICKET_EXPIRY_SECONDS', 3600)
    logger.info(f"Ticket Expiry: Initial delay {init_delay}s, Interval {interval}s")
    time.sleep(init_delay)
    while True:
        current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        logger.info(f"Scheduler: Running ticket expiration check at {current_time_str} UTC...")
        try:
            expired_ticket_details_list = db_utils.expire_old_tickets()
            if expired_ticket_details_list:
                logger.info(f"Scheduler: Found {len(expired_ticket_details_list)} tickets to auto-expire.")
                for ticket_info in expired_ticket_details_list:
                    try:
                        user_id_to_notify = ticket_info['user_id']
                        ticket_id_expired = ticket_info['ticket_id']
                        bot.send_message(user_id_to_notify,
                                         f"Your support ticket #{ticket_id_expired} has been automatically closed due to 24 hours of inactivity. "
                                         f"If you still need help, please open a new ticket by sending another message in the support channel.")
                        logger.info(f"Scheduler: Notified user {user_id_to_notify} about auto-expired ticket {ticket_id_expired}.")
                        if config.ADMIN_ID and str(config.ADMIN_ID).strip():
                            try:
                                admin_id_int = int(config.ADMIN_ID)
                                bot.send_message(admin_id_int, f"Ticket #{ticket_id_expired} (User {user_id_to_notify}) was auto-expired due to inactivity.")
                                logger.info(f"Scheduler: Notified admin {admin_id_int} about auto-expired ticket {ticket_id_expired}.")
                            except ValueError: logger.error(f"Scheduler: ADMIN_ID '{config.ADMIN_ID}' is not valid int for auto-expiry notice of ticket #{ticket_id_expired}.")
                            except Exception as e_admin: logger.error(f"Scheduler: Failed to send auto-expiration notice for ticket #{ticket_id_expired} to ADMIN_ID {config.ADMIN_ID}: {e_admin}")
                    except Exception as e_notify: logger.error(f"Scheduler: Error notifying user {user_id_to_notify} about auto-expired ticket #{ticket_id_expired}: {e_notify}")
            else:
                logger.info(f"Scheduler: No tickets for auto-expiration at {current_time_str} UTC.")
        except Exception as e_task: logger.exception(f"Scheduler: Critical error in ticket expiration task: {e_task}")
        time.sleep(interval)

def scheduled_item_sync():
    logger.info("Scheduler: Item availability sync thread started.")
    init_delay = getattr(config, 'SCHEDULER_INIT_DELAY_ITEM_SYNC_SECONDS', 20)
    interval = getattr(config, 'SCHEDULER_INTERVAL_ITEM_SYNC_SECONDS', 3600) # Default 1 hour
    logger.info(f"Item Sync: Initial delay {init_delay}s, Interval {interval}s")
    time.sleep(init_delay)
    while True:
        current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        logger.info(f"Scheduler: Running item availability sync at {current_time_str} UTC...")
        try:
            if hasattr(db_utils, 'periodic_filesystem_to_db_sync'):
                sync_summary = db_utils.periodic_filesystem_to_db_sync()
                logger.info(f"Scheduler: Item sync finished. Summary: {sync_summary}")
            else:
                logger.error("Scheduler: periodic_filesystem_to_db_sync function not found in db_utils.")
                time.sleep(3600 * 24) # Sleep long if function missing
                continue
        except Exception as e: logger.exception(f"Scheduler: Critical error in item availability sync task: {e}")
        time.sleep(interval)

# --- HD Wallet Payment Monitoring Tasks ---
def scheduled_check_pending_crypto_payments():
    logger.info("Scheduler: Pending crypto payment check thread started.")
    init_delay = getattr(config, 'SCHEDULER_INIT_DELAY_PAYMENT_CHECK_SECONDS', 30)
    interval = getattr(config, 'SCHEDULER_INTERVAL_PAYMENT_CHECK_SECONDS', 120) # Default 2 minutes
    if interval < 60: logger.warning(f"Payment check interval {interval}s is very frequent. Consider increasing.")
    logger.info(f"Pending Payment Check: Initial delay {init_delay}s, Interval {interval}s")
    time.sleep(init_delay)
    while True:
        logger.info("Scheduler: Running pending crypto payment check...")
        try:
            payment_monitor.check_pending_payments()
        except Exception as e:
            logger.exception("Scheduler: Critical error in check_pending_payments task.")
        time.sleep(interval)

def scheduled_process_confirmed_crypto_payments():
    logger.info("Scheduler: Process confirmed crypto payment thread started.")
    init_delay = getattr(config, 'SCHEDULER_INIT_DELAY_PROCESS_CONFIRMED_SECONDS', 15)
    interval = getattr(config, 'SCHEDULER_INTERVAL_PROCESS_CONFIRMED_SECONDS', 60) # Default 1 minute
    logger.info(f"Process Confirmed Payments: Initial delay {init_delay}s, Interval {interval}s")
    time.sleep(init_delay)
    while True:
        logger.info("Scheduler: Running process confirmed crypto payments...")
        try:
            payment_monitor.process_confirmed_payments(bot)
        except Exception as e:
            logger.exception("Scheduler: Critical error in process_confirmed_payments task.")
        time.sleep(interval)

def scheduled_expire_stale_crypto_payments():
    logger.info("Scheduler: Expire stale crypto payment thread started.")
    init_delay = getattr(config, 'SCHEDULER_INIT_DELAY_EXPIRE_PAYMENTS_SECONDS', 60)
    interval = getattr(config, 'SCHEDULER_INTERVAL_EXPIRE_PAYMENTS_SECONDS', 300) # Default 5 minutes
    logger.info(f"Expire Stale Payments: Initial delay {init_delay}s, Interval {interval}s")
    time.sleep(init_delay)
    while True:
        logger.info("Scheduler: Running expire stale crypto payments...")
        try:
            payment_monitor.expire_stale_monitoring_payments(bot)
        except Exception as e:
            logger.exception("Scheduler: Critical error in expire_stale_monitoring_payments task.")
        time.sleep(interval)

# Main function
def start_bot():
    logger.info("Bot starting...")

    # Existing scheduled tasks
    logger.info("Starting scheduled ticket expiration check thread...")
    expiration_thread = Thread(target=scheduled_ticket_expiration_check, daemon=True)
    expiration_thread.start()

    logger.info("Scheduler: Starting item availability sync thread...")
    item_sync_thread = Thread(target=scheduled_item_sync, daemon=True)
    item_sync_thread.start()

    # New HD Wallet payment monitoring tasks
    logger.info("Starting scheduled pending crypto payment check thread...")
    pending_crypto_check_thread = Thread(target=scheduled_check_pending_crypto_payments, daemon=True)
    pending_crypto_check_thread.start()

    logger.info("Starting scheduled process confirmed crypto payment thread...")
    process_confirmed_crypto_thread = Thread(target=scheduled_process_confirmed_crypto_payments, daemon=True)
    process_confirmed_crypto_thread.start()

    logger.info("Starting scheduled expire stale crypto payment thread...")
    expire_stale_crypto_thread = Thread(target=scheduled_expire_stale_crypto_payments, daemon=True)
    expire_stale_crypto_thread.start()

    logger.info("Starting Telegram bot polling...")
    bot.delete_webhook() # Ensure no webhook is active before polling
    try:
        # Use a custom update listener to log all incoming updates
        bot.set_update_listener(handle_updates)
        bot.infinity_polling(timeout=10) # Poll for all update types
    except Exception as e_poll:
        logger.critical(f"Bot polling failed critically: {e_poll}", exc_info=True)
    finally:
        logger.info("Bot polling stopped.")
