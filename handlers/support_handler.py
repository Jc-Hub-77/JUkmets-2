import telebot # Keep for types
from telebot import types
import logging
# from bot import bot, clear_user_state, get_user_state, update_user_state # Removed to break circular dependency
from modules.message_utils import send_or_edit_message, delete_message
from modules.text_utils import escape_md
import config
# User state functions will be passed in, so direct import isn't needed at the top level here.
from modules.db_utils import (
    get_open_ticket_for_user, create_new_ticket, add_message_to_ticket,
    get_ticket_details_by_id, update_ticket_status, get_or_create_user
)
from datetime import datetime

logger = logging.getLogger(__name__)


def handle_support_initiate_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = call.message.message_id
    logger.info(f"User {user_id} initiated support flow.")

    try:
        get_or_create_user(user_id) # Ensure user exists in DB
        # User state functions (clear_user_state, get_user_state, update_user_state) are passed in.

        user_id_str = str(user_id) # Get user ID as string
        support_message_text = (
            f"💬 Support Channel\n\n" # Removed asterisks for bold
            f"For any questions or issues, simply send a message directly to this bot. "
            f"Our administrator will receive your query here and reply through the bot.\n\n"
            f"Please provide your User ID (`{user_id_str}`) and any relevant details to "
            f"help us assist you promptly."
        )

        markup = types.InlineKeyboardMarkup(row_width=1)

        # Check for an open ticket first from user state, then from DB
        current_ticket_id = get_user_state(user_id, 'current_ticket_id')
        open_ticket = None
        if current_ticket_id:
            ticket_data = get_ticket_details_by_id(current_ticket_id)
            if ticket_data and ticket_data['status'] == 'open' and ticket_data['user_id'] == user_id:
                open_ticket = ticket_data

        if not open_ticket: # If not in state or state one is closed/invalid, check DB
            open_ticket = get_open_ticket_for_user(user_id)

        if open_ticket:
            ticket_id = open_ticket['ticket_id']
            # Removed pre-escaped parentheses and periods
            support_message_text += f"\n\nPS: You currently have an open ticket (ID: `{ticket_id}`). Any message you send will be added to this ticket."
            markup.add(types.InlineKeyboardButton(f"❌ Close My Ticket #{ticket_id}", callback_data=f"user_close_ticket_{ticket_id}"))
            update_user_state(user_id, 'current_ticket_id', ticket_id) # Ensure state is up-to-date
            update_user_state(user_id, 'current_flow', f'in_support_ticket_{ticket_id}') # More specific flow
        else:
            update_user_state(user_id, 'current_flow', 'support_info_displayed') # General support flow, ready for new ticket
            update_user_state(user_id, 'current_ticket_id', None) # Clear any old ticket ID from state


        markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        # Escape the final text once before sending
        sent_message_id = send_or_edit_message(bot_instance, chat_id, escape_md(support_message_text),
                                               reply_markup=markup,
                                               existing_message_id=existing_message_id,
                                               parse_mode="MarkdownV2")

        if sent_message_id:
            update_user_state(user_id, 'last_bot_message_id', sent_message_id)

        bot_instance.answer_callback_query(call.id)

    except Exception as e:
        logger.exception(f"Error in handle_support_initiate_callback for user {user_id}: {e}")
        bot_instance.answer_callback_query(call.id, "An error occurred while loading support information.")
        try:
            fallback_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            # Attempt to edit existing message with error, or send new if edit fails
            send_or_edit_message(bot_instance, chat_id, escape_md("Sorry, there was an error. Please try returning to the main menu."),
                                 reply_markup=fallback_markup, existing_message_id=existing_message_id)
        except Exception as e_fallback:
            logger.error(f"Error sending/editing fallback message in handle_support_initiate_callback to user {user_id}: {e_fallback}")


# The lambda for this message handler will be defined in bot.py
def handle_support_message(bot_instance, clear_user_state, get_user_state, update_user_state, message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    logger.info(f"Received support message from user {user_id}. Photo: {'Yes' if message.photo else 'No'}")

    try:
        admin_id_to_notify = int(config.ADMIN_ID)
        if not admin_id_to_notify: # Check if ADMIN_ID is a non-empty string after int conversion attempt
            logger.critical("ADMIN_ID is not configured or is empty. Cannot notify admin for support tickets.")
            bot_instance.send_message(chat_id, "We are currently unable to process support requests. Please try again later.")
            return
    except (ValueError, TypeError, AttributeError):
        logger.critical(f"ADMIN_ID ('{config.ADMIN_ID if hasattr(config, 'ADMIN_ID') else 'NOT SET'}') is invalid. Cannot notify admin.")
        bot_instance.send_message(chat_id, "Support system configuration error. Please try again later.")
        return

    message_text = message.text or message.caption or ""
    photo_file_id = message.photo[-1].file_id if message.photo else None

    message_content_for_db = message_text
    if photo_file_id:
        message_content_for_db += f" [Image Attached: {photo_file_id}]" # Store photo reference in DB

    if not message_content_for_db.strip():
        bot_instance.reply_to(message, "Your message seems empty. Please send your query or issue.")
        return

    # Check state for current_ticket_id first, then DB for any open ticket
    ticket_id = get_user_state(user_id, 'current_ticket_id')
    open_ticket = None
    if ticket_id:
        ticket_data = get_ticket_details_by_id(ticket_id)
        if ticket_data and ticket_data['status'] == 'open' and ticket_data['user_id'] == user_id:
            open_ticket = ticket_data

    if not open_ticket: # If not in state or state one is closed/invalid, check DB
        open_ticket = get_open_ticket_for_user(user_id)


        if open_ticket is None:
            logger.info(f"No open ticket for user {user_id}. Creating new ticket.")
            ticket_id = create_new_ticket(user_id, message_content_for_db, user_tg_message_id=message.message_id)
            if ticket_id:
                # Removed pre-escaped parentheses and periods
                bot_instance.send_message(chat_id, escape_md(f"✅ Your support request (Ticket #{ticket_id}) has been received. Our team will get back to you shortly."), parse_mode="MarkdownV2")
                update_user_state(user_id, 'current_flow', f'in_support_ticket_{ticket_id}')
                update_user_state(user_id, 'current_ticket_id', ticket_id)

                user_info_escaped = escape_md(f"User ID: {user_id} (@{message.from_user.username})" if message.from_user.username else f"User ID: {user_id}")
                # Escape message content preview separately to handle potential Markdown in user message
                message_preview_escaped = escape_md(message_content_for_db[:150])
                # Removed pre-escaped characters from admin notify text
                admin_notify_text = (f"📢 New Support Ticket #{ticket_id}\n"
                                    f"From: {user_info_escaped}\n"
                                    f"Message: {message_preview_escaped}{'...' if len(message_content_for_db) > 150 else ''}")
                markup_admin = types.InlineKeyboardMarkup(row_width=1)
                markup_admin.add(types.InlineKeyboardButton(f"➡️ View/Reply Ticket #{ticket_id}", callback_data=f"admin_ticket_view_{ticket_id}"))
                markup_admin.add(types.InlineKeyboardButton(f"❌ Close Ticket #{ticket_id}", callback_data=f"admin_ticket_close_{ticket_id}"))
                try:
                    # If photo was sent by user, forward it to admin as well
                    if photo_file_id:
                        bot_instance.forward_message(admin_id_to_notify, chat_id, message.message_id) # Forward the original photo message
                    # Escape the final admin notify text once before sending
                    bot_instance.send_message(admin_id_to_notify, escape_md(admin_notify_text), reply_markup=markup_admin, parse_mode="MarkdownV2")
                except Exception as e_admin_notify:
                    logger.exception(f"Failed to notify admin {admin_id_to_notify} for new ticket {ticket_id}: {e_admin_notify}")
            else:
                logger.error(f"Failed to create new ticket for user {user_id}.")
                bot_instance.send_message(chat_id, escape_md("Sorry, there was an error creating your ticket. Please try again."))
        else:
            ticket_id = open_ticket['ticket_id']
            logger.info(f"Adding message to existing ticket {ticket_id} for user {user_id}.")
            add_success = add_message_to_ticket(ticket_id, 'user', message_content_for_db, user_tg_message_id=message.message_id)
            if add_success:
                # User already has a ticket open, confirm message added - removed pre-escaped characters
                bot_instance.send_message(chat_id, escape_md(f"🗣️ Your message has been added to Ticket #{ticket_id}."), parse_mode="MarkdownV2")
                update_user_state(user_id, 'current_flow', f'in_support_ticket_{ticket_id}') # Ensure flow state is correct
                update_user_state(user_id, 'current_ticket_id', ticket_id) # Ensure state is correct

                user_info_escaped = escape_md(f"User ID: {user_id} (@{message.from_user.username})" if message.from_user.username else f"User ID: {user_id}")
                # Escape message content preview separately
                message_preview_escaped = escape_md(message_content_for_db[:150])
                # Removed pre-escaped characters from admin notify text
                admin_notify_text = (f"💬 New Reply in Ticket #{ticket_id}\n"
                                    f"From: {user_info_escaped}\n"
                                    f"Message: {message_preview_escaped}{'...' if len(message_content_for_db) > 150 else ''}")
                markup_admin = types.InlineKeyboardMarkup(row_width=1)
                markup_admin.add(types.InlineKeyboardButton(f"➡️ View/Reply Ticket #{ticket_id}", callback_data=f"admin_ticket_view_{ticket_id}"))
                markup_admin.add(types.InlineKeyboardButton(f"❌ Close Ticket #{ticket_id}", callback_data=f"admin_ticket_close_{ticket_id}"))
                try:
                    if photo_file_id:
                        bot_instance.forward_message(admin_id_to_notify, chat_id, message.message_id)
                    # Escape the final admin notify text once before sending
                    bot_instance.send_message(admin_id_to_notify, escape_md(admin_notify_text), reply_markup=markup_admin, parse_mode="MarkdownV2")
                except Exception as e_admin_notify:
                    logger.exception(f"Failed to notify admin {admin_id_to_notify} for reply in ticket {ticket_id}: {e_admin_notify}")
            else:
                logger.error(f"Failed to add message to ticket {ticket_id} for user {user_id}.")
                bot_instance.send_message(chat_id, escape_md("Sorry, there was an error adding your message to the ticket. Please try again."))


def handle_user_close_ticket_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    logger.info(f"User {user_id} attempting to close ticket via callback: {call.data}")
    try:
        ticket_id = int(call.data.split('user_close_ticket_')[1])
    except (IndexError, ValueError):
        logger.warning(f"Invalid ticket ID in callback data: {call.data} for user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error: Invalid ticket reference.", show_alert=True)
        return

    ticket_details = get_ticket_details_by_id(ticket_id)

    if not ticket_details or ticket_details['user_id'] != user_id:
        logger.warning(f"User {user_id} attempted to close ticket {ticket_id} not owned by them or ticket not found.")
        bot_instance.answer_callback_query(call.id, "Error: Ticket not found or you are not the owner.", show_alert=True)
        return

    if ticket_details['status'] != 'open':
        logger.info(f"User {user_id} tried to close ticket {ticket_id} which is already {ticket_details['status']}.")
        # Edit the message that had the "Close Ticket" button - removed pre-escaped characters
        closed_text = f"This ticket (#{ticket_id}) is already *{escape_md(ticket_details['status'].replace('_', ' '))}*."
        markup_back = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
        # Escape the final text once before sending
        send_or_edit_message(bot_instance, chat_id, text=escape_md(closed_text),
                             reply_markup=markup_back, # Allow going back to main menu
                             existing_message_id=call.message.message_id,
                             parse_mode="MarkdownV2")
        bot_instance.answer_callback_query(call.id, "Ticket already closed.")
        return

    update_success = update_ticket_status(ticket_id, 'closed_by_user')

    if update_success:
        logger.info(f"Ticket {ticket_id} closed by user {user_id}.")
        bot_instance.answer_callback_query(call.id, f"Ticket #{ticket_id} closed.")
        # Removed pre-escaped characters
        closed_text = f"Ticket #{ticket_id} has been closed by you."

        # Replace the old support message with the closed confirmation and a back button
        markup_back_to_main = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
        # Escape the final text once before sending
        send_or_edit_message(bot_instance, chat_id, text=escape_md(closed_text),
                             reply_markup=markup_back_to_main,
                             existing_message_id=call.message.message_id,
                             parse_mode="MarkdownV2")

        try:
            admin_id_to_notify = int(config.ADMIN_ID)
            if admin_id_to_notify:
                # Removed pre-escaped characters
                admin_notify_text = f"ℹ️ Ticket #{ticket_id} (User {user_id}) was closed by the user."
                # Escape the final admin notify text once before sending
                bot_instance.send_message(admin_id_to_notify, escape_md(admin_notify_text), parse_mode="MarkdownV2")
        except (ValueError, TypeError, AttributeError) as e_admin_conf:
             logger.error(f"Error notifying admin about user closing ticket {ticket_id}: {e_admin_conf}")

        update_user_state(user_id, 'current_flow', None) # Reset flow
        update_user_state(user_id, 'current_ticket_id', None) # Clear current ticket ID
    else:
        logger.error(f"Failed to update status for ticket {ticket_id} on user close request by {user_id}.")
        bot_instance.answer_callback_query(call.id, "Error closing ticket. Please try again.", show_alert=True)

# Note: Admin replies to tickets are handled in admin_handler.py
# This handler needs to ensure that when an admin replies, the user receives the message.

if __name__ == '__main__': # For testing or direct execution (if any)
    logger.info("Support Handler module initialized (direct run).")
