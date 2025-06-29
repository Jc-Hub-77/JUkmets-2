import telebot
from telebot import types
import os
import logging
import datetime

from modules.db_utils import get_or_create_user, get_user_transaction_history
from modules.message_utils import send_or_edit_message, delete_message
from modules import text_utils
from modules import db_utils
import config
from handlers.utils import format_transaction_history_display, TX_HISTORY_PAGE_SIZE # Ensure this is available
from modules.text_utils import escape_md # Import escape_md

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = TX_HISTORY_PAGE_SIZE # Use the constant from utils or define a new one

def handle_account_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = call.message.message_id
    logger.info(f"User {user_id} requested account details.")

    try:
        user_data = get_or_create_user(user_id)
        # Access data from sqlite3.Row object using column name
        balance = user_data['balance']
        transaction_count = user_data['transaction_count']

        # Fetch recent transactions for a quick overview (e.g., last 3-5)
        # For simplicity, we'll just show counts here and let "View Full History" handle details.
        # To show recent ones, you'd call:
        # recent_transactions = get_user_transaction_history(user_id, page=1, page_size=3)
        # formatted_recent_txs = format_transaction_history_display(recent_transactions, page=1, page_size=3, total_count=transaction_count)


        account_info_text = (
            f"👤 Your Account\n\n"
            f"💰 Balance: {balance:.2f} EUR\n"
            f"📊 Total Transactions: {transaction_count}\n\n"
            # f"{formatted_recent_txs['text'] if recent_transactions else 'No recent transactions.'}\n\n" # If showing recent
            f"Select an option below:"
        )

        markup = types.InlineKeyboardMarkup(row_width=1)
        if transaction_count > 0:
            markup.add(types.InlineKeyboardButton("📜 View Full Transaction History", callback_data="view_tx_history_page_1"))
        markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        sent_message_id = send_or_edit_message(
            bot_instance, chat_id, escape_md(account_info_text),
            reply_markup=markup,
            existing_message_id=existing_message_id,
            parse_mode="MarkdownV2"
        )
        if sent_message_id:
            update_user_state(user_id, 'last_bot_message_id', sent_message_id)

        update_user_state(user_id, 'current_flow', 'account_details_displayed')
        bot_instance.answer_callback_query(call.id)

    except Exception as e:
        logger.exception(f"Error in handle_account_callback for user {user_id}: {e}")
        bot_instance.answer_callback_query(call.id, "Error fetching account details.")
        try:
            fallback_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            send_or_edit_message(bot_instance, chat_id, escape_md("Sorry, an error occurred while fetching your account details."),
                                 reply_markup=fallback_markup, existing_message_id=existing_message_id, parse_mode="MarkdownV2")
        except Exception as e_fallback:
            logger.error(f"Error sending fallback message in handle_account_callback to user {user_id}: {e_fallback}")


def handle_view_full_history_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = call.message.message_id

    try:
        page_data = call.data.split('view_tx_history_page_')
        if len(page_data) < 2 or not page_data[1].isdigit():
            logger.warning(f"Invalid page number in callback data: {call.data} for user {user_id}")
            bot_instance.answer_callback_query(call.id, "Error: Invalid page number.", show_alert=True)
            return
        page = int(page_data[1])
        if page < 1: page = 1 # Ensure page is at least 1

        logger.info(f"User {user_id} requested transaction history page {page}.")
        update_user_state(user_id, 'current_tx_history_page', page)

        user_data = get_or_create_user(user_id) # To get total_transactions
        total_transactions = user_data.get('transactions_count', 0)

        transactions = get_user_transaction_history(user_id, page=page, page_size=DEFAULT_PAGE_SIZE)

        if not transactions and page > 1: # User might be trying to access a page that no longer exists after items were deleted
            logger.info(f"No transactions on page {page} for user {user_id}, redirecting to page 1.")
            page = 1
            update_user_state(user_id, 'current_tx_history_page', page)
            transactions = get_user_transaction_history(user_id, page=page, page_size=DEFAULT_PAGE_SIZE)

        display_data = format_transaction_history_display(transactions, page, DEFAULT_PAGE_SIZE, total_transactions)

        markup = types.InlineKeyboardMarkup(row_width=2)
        nav_buttons = []
        if display_data['has_previous_page']:
            nav_buttons.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"view_tx_history_page_{page-1}"))
        if display_data['has_next_page']:
            nav_buttons.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"view_tx_history_page_{page+1}"))

        if nav_buttons:
            markup.add(*nav_buttons) # Unpack if list is not empty

        markup.add(types.InlineKeyboardButton("⬅️ Back to Account", callback_data="main_account"))
        markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        sent_message_id = send_or_edit_message(
            bot_instance, chat_id, escape_md(display_data['text']),
            reply_markup=markup,
            existing_message_id=existing_message_id,
            parse_mode="MarkdownV2"
        )
        if sent_message_id:
            update_user_state(user_id, 'last_bot_message_id', sent_message_id)

        update_user_state(user_id, 'current_flow', 'account_history_displayed')
        bot_instance.answer_callback_query(call.id)

    except Exception as e:
        logger.exception(f"Error in handle_view_full_history_callback for user {user_id}, page {page if 'page' in locals() else 'unknown'}: {e}")
        bot_instance.answer_callback_query(call.id, "Error fetching transaction history.")
        try:
            # Attempt to send a new message with an error if edit fails or if appropriate
            fallback_markup = types.InlineKeyboardMarkup()
            fallback_markup.add(types.InlineKeyboardButton("⬅️ Back to Account", callback_data="main_account"))
            fallback_markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            send_or_edit_message(bot_instance, chat_id, escape_md("Sorry, an error occurred while fetching transaction history."),
                                 reply_markup=fallback_markup, existing_message_id=existing_message_id, parse_mode="MarkdownV2")
        except Exception as e_fallback:
            logger.error(f"Error sending fallback message in handle_view_full_history_callback to user {user_id}: {e_fallback}")
