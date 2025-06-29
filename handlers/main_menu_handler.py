import telebot
from telebot import types
from modules.db_utils import get_or_create_user
from modules.message_utils import send_or_edit_message
import logging

logger = logging.getLogger(__name__)

def get_main_menu_text_and_markup():
    """Prepares the main menu text and inline keyboard markup."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_buy = types.InlineKeyboardButton("🛍️ Buy", callback_data="buy_initiate")
    btn_add_balance = types.InlineKeyboardButton("💰 Add Balance", callback_data="main_add_balance")
    btn_account = types.InlineKeyboardButton("👤 Account", callback_data="main_account")
    btn_support = types.InlineKeyboardButton("💬 Support", callback_data="support_initiate")
    markup.add(btn_buy, btn_add_balance, btn_account, btn_support)
    welcome_text = "Welcome to the main menu! How can I help you?"
    return welcome_text, markup

def handle_start(bot, clear_user_state, get_user_state, update_user_state, message):
    """Handles the /start command."""
    user_id = message.chat.id
    chat_id = message.chat.id
    logger.info(f"User {user_id} started bot with /start command.")

    try:
        get_or_create_user(user_id)
        clear_user_state(user_id)

        last_bot_message_id = get_user_state(user_id, 'last_bot_message_id')
        welcome_text, markup = get_main_menu_text_and_markup()

        new_message_id = send_or_edit_message(
            bot, chat_id, welcome_text,
            reply_markup=markup,
            existing_message_id=last_bot_message_id,
            parse_mode=None
        )
        if new_message_id:
            update_user_state(user_id, 'last_bot_message_id', new_message_id)
    except Exception as e:
        logger.exception(f"Error in handle_start for user {user_id}: {e}")
        bot.send_message(chat_id, "An error occurred while starting. Please try again later.")


def handle_back_to_main_menu_callback(bot, clear_user_state, get_user_state, update_user_state, call):
    """Handles the 'back_to_main' callback query."""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    logger.info(f"User {user_id} navigating back to main menu.")

    try:
        get_or_create_user(user_id)
        clear_user_state(user_id)

        welcome_text, markup = get_main_menu_text_and_markup()

        new_message_id = send_or_edit_message(
            bot, chat_id, welcome_text,
            reply_markup=markup,
            existing_message_id=call.message.message_id,
            parse_mode=None
        )
        if new_message_id:
            update_user_state(user_id, 'last_bot_message_id', new_message_id)

        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.exception(f"Error in handle_back_to_main_menu_callback for user {user_id}: {e}")
        bot.answer_callback_query(call.id, "Error returning to main menu.")
        try:
            bot.send_message(chat_id, "An error occurred. Please try /start again.")
        except Exception as e_send:
            logger.error(f"Failed to send error fallback message to user {user_id}: {e_send}")
