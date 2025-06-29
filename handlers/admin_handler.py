import telebot
from telebot import types
import json
import datetime
import os
import logging
from decimal import Decimal

# from bot import bot, get_user_state, update_user_state, clear_user_state # Removed
from modules.auth_utils import is_admin # is_admin will be used in bot.py decorators
from modules.db_utils import (
    get_all_open_tickets_admin, get_ticket_details_by_id, # Assuming these are still needed for ticket system
    add_message_to_ticket, update_ticket_status,
    update_admin_ticket_view_message_id,
    get_or_create_user, get_user_transaction_history
)
from modules import file_system_utils
from modules.message_utils import send_or_edit_message, delete_message
from modules.text_utils import escape_md
import config
from modules import db_utils
from handlers.utils import format_transaction_history_display, TX_HISTORY_PAGE_SIZE


logger = logging.getLogger(__name__)

TICKETS_PER_PAGE = 5
ITEMS_PER_PAGE_ADMIN = 5
USERS_PER_PAGE_ADMIN = 10


# --- Admin Support Ticket Management ---
def format_ticket_summary_for_list(ticket):
    messages = json.loads(ticket['messages_json']) if ticket['messages_json'] else []
    first_message_snippet = "No messages yet."
    if messages:
        first_message_obj = messages[0]
        text_snippet = escape_md(first_message_obj['text'][:40])
        if len(first_message_obj['text']) > 40: text_snippet += "..."
        first_message_snippet = f"_{text_snippet}_"

    last_active_dt = datetime.datetime.fromisoformat(ticket['last_message_at'])
    last_active_str = escape_md(last_active_dt.strftime("%Y-%m-%d %H:%M UTC"))
    status_escaped = escape_md(ticket['status'])

    return (
        f"Ticket ID: `{ticket['ticket_id']}` (User: `{ticket['user_id']}`)\n"
        f"Status: *{status_escaped}*\n"
        f"Last Update: _{last_active_str}_\n"
        f"Snippet: {first_message_snippet}"
    )

# Note: Removed @bot decorators from all ticket and user management handlers below.
# They will need to be registered in bot.py and accept bot_instance, state utils.

def handle_admin_list_tickets_command(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message, page=1):
    admin_id = message.from_user.id
    chat_id = message.chat.id
    logger.info(f"Admin {admin_id} listing tickets, page {page}.")

    open_tickets = get_all_open_tickets_admin()

    if not open_tickets:
        bot_instance.send_message(chat_id, "No open support tickets.")
        return

    total_tickets = len(open_tickets)
    total_pages = (total_tickets + TICKETS_PER_PAGE - 1) // TICKETS_PER_PAGE
    page = max(1, min(page, total_pages)) # Ensure page is valid
    start_index = (page - 1) * TICKETS_PER_PAGE
    end_index = start_index + TICKETS_PER_PAGE
    tickets_to_display = open_tickets[start_index:end_index]

    # Message Management: Delete previous pagination message if any
    old_pagination_msg_id = get_user_state_fn(admin_id, 'admin_ticket_list_pagination_msg_id')
    if old_pagination_msg_id:
        try: delete_message(bot_instance, chat_id, old_pagination_msg_id)
        except Exception: pass
        update_user_state_fn(admin_id, 'admin_ticket_list_pagination_msg_id', None)

    # Delete the command message itself
    if hasattr(message, 'text') and message.text and message.text.startswith('/tickets'):
        try: delete_message(bot_instance, chat_id, message.message_id)
        except Exception: pass

    # Send a new header for the ticket list
    header_text = f"📋 *Open Support Tickets (Page {page}/{total_pages}):*\n"
    # This message should ideally be editable if this function is called again for new page.
    # For now, let's send it. If pagination is via editing, this needs adjustment.
    # The current structure sends new messages for each ticket item, then a pagination message.
    # This is probably fine.
    bot_instance.send_message(chat_id, header_text, parse_mode="MarkdownV2")

    if not tickets_to_display and page > 1: # Should not happen if page is clamped correctly
        bot_instance.send_message(chat_id, "No tickets on this page. This is unexpected.")

    for ticket in tickets_to_display:
        summary = format_ticket_summary_for_list(ticket)
        ticket_markup = types.InlineKeyboardMarkup(row_width=2)
        ticket_markup.add(
            types.InlineKeyboardButton(f"👁️ View/Reply #{ticket['ticket_id']}", callback_data=f"admin_view_ticket_{ticket['ticket_id']}"),
            types.InlineKeyboardButton(f"❌ Close #{ticket['ticket_id']}", callback_data=f"admin_close_ticket_{ticket['ticket_id']}")
        )
        bot_instance.send_message(chat_id, summary + "\n--------------------", reply_markup=ticket_markup, parse_mode="MarkdownV2")

    if total_pages > 1:
        pagination_markup = types.InlineKeyboardMarkup(row_width=3)
        nav_buttons = []
        if page > 1: nav_buttons.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"admin_list_tickets_page_{page-1}"))
        nav_buttons.append(types.InlineKeyboardButton(f"🔄 Pg {page}/{total_pages}", callback_data=f"admin_list_tickets_page_{page}")) # Refresh current
        if page < total_pages: nav_buttons.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"admin_list_tickets_page_{page+1}"))

        if nav_buttons:
            pagination_markup.add(*nav_buttons)
            pg_msg = bot_instance.send_message(chat_id, "Ticket List Navigation:", reply_markup=pagination_markup)
            update_user_state_fn(admin_id, 'admin_ticket_list_pagination_msg_id', pg_msg.message_id)

    update_user_state_fn(admin_id, 'admin_ticket_current_page', page)


def handle_admin_list_tickets_page_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    try: page = int(call.data.split('admin_list_tickets_page_')[1])
    except (IndexError, ValueError):
        bot_instance.answer_callback_query(call.id, "Invalid page.", show_alert=True); return

    # Delete the pagination message that was clicked
    try: delete_message(bot_instance, call.message.chat.id, call.message.message_id)
    except: pass

    # Create a mock message to pass to the command handler
    mock_message = types.Message(
        message_id=0, # This message won't be deleted by the handler as it's not a command
        from_user=call.from_user,
        date=int(datetime.datetime.now().timestamp()), # Ensure it's an int
        chat=call.message.chat, # Use chat object from callback
        content_type='text',
        options={},
        json_string=""
    )
    mock_message.text = None # Not a command text

    handle_admin_list_tickets_command(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, mock_message, page=page)
    bot_instance.answer_callback_query(call.id)


def handle_admin_view_ticket_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    admin_id = call.from_user.id
    chat_id = call.message.chat.id
    try: ticket_id = int(call.data.split('admin_view_ticket_')[1])
    except (IndexError, ValueError): bot_instance.answer_callback_query(call.id, "Bad Ticket ID.", show_alert=True); return

    ticket = db_utils.get_ticket_details_by_id(ticket_id)
    if not ticket:
        bot_instance.answer_callback_query(call.id, f"Ticket #{ticket_id} not found."); return

    update_user_state_fn(admin_id, 'admin_current_ticket_id', ticket_id)
    update_user_state_fn(admin_id, 'admin_flow', 'viewing_ticket') # For reply context

    messages_list = json.loads(ticket['messages_json']) if ticket['messages_json'] else []
    conversation_history = [f"📜 *Conversation for Ticket \\#{ticket_id}* (User ID: `{ticket['user_id']}`)"]
    conversation_history.append(f"Status: *{escape_md(ticket['status'].replace('_', ' ').title())}*")

    for msg_data in messages_list:
        sender = escape_md(msg_data.get('sender', 'System').title())
        text = escape_md(msg_data.get('text', ''))
        ts_str = "Unknown time"
        try:
            ts_dt = datetime.datetime.fromisoformat(msg_data.get('timestamp'))
            ts_str = escape_md(ts_dt.strftime('%Y-%m-%d %H:%M:%S UTC'))
        except: pass
        conversation_history.append(f"\n*{sender}* ({ts_str}):\n{text}")

    full_conversation_text = "\n".join(conversation_history)
    if len(full_conversation_text) > 4000: full_conversation_text = full_conversation_text[:4000] + "\n\\.\\.\\. (truncated)"

    markup = types.InlineKeyboardMarkup(row_width=1)
    if ticket['status'] == 'open':
        markup.add(types.InlineKeyboardButton(f"✍️ Reply to Ticket #{ticket_id}", callback_data=f"admin_reply_ticket_{ticket_id}"))
        markup.add(types.InlineKeyboardButton(f"❌ Close Ticket #{ticket_id}", callback_data=f"admin_close_ticket_{ticket_id}")
    )
    markup.add(types.InlineKeyboardButton("⬅️ Back to Tickets List", callback_data="admin_list_tickets_cmd_from_view"))

    # Delete the message that contained the button for THIS ticket (the list item)
    try: delete_message(bot_instance, chat_id, call.message.message_id)
    except: pass

    # Send new message for the ticket view
    sent_msg = bot_instance.send_message(chat_id, full_conversation_text, reply_markup=markup, parse_mode="MarkdownV2")
    update_user_state_fn(admin_id, 'admin_ticket_view_msg_id', sent_msg.message_id) # Store ID of this view message
    bot_instance.answer_callback_query(call.id)

def handle_admin_list_tickets_cmd_from_view_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    admin_id = call.from_user.id
    current_page = get_user_state_fn(admin_id, 'admin_ticket_current_page', 1)

    # Delete the ticket view message
    ticket_view_msg_id = get_user_state_fn(admin_id, 'admin_ticket_view_msg_id')
    if ticket_view_msg_id and ticket_view_msg_id == call.message.message_id:
        try: delete_message(bot_instance, call.message.chat.id, ticket_view_msg_id)
        except: pass
        update_user_state_fn(admin_id, 'admin_ticket_view_msg_id', None)
    elif call.message.message_id : # Fallback if state ID doesn't match current message
        try: delete_message(bot_instance, call.message.chat.id, call.message.message_id)
        except: pass

    mock_message = types.Message(0,call.from_user,int(datetime.datetime.now().timestamp()),call.message.chat,'text',{},"")
    mock_message.text = None # Indicates not a direct command input
    handle_admin_list_tickets_command(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, mock_message, page=current_page)
    bot_instance.answer_callback_query(call.id)


def handle_admin_initiate_reply_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    admin_id = call.from_user.id
    chat_id = call.message.chat.id
    try: ticket_id = int(call.data.split('admin_reply_ticket_')[1])
    except (IndexError, ValueError): bot_instance.answer_callback_query(call.id, "Bad Ticket ID.", show_alert=True); return

    ticket = db_utils.get_ticket_details_by_id(ticket_id)
    if not ticket or ticket['status'] != 'open':
        bot_instance.answer_callback_query(call.id, f"Ticket #{ticket_id} not found or not open."); return

    update_user_state_fn(admin_id, 'admin_replying_to_ticket_id', ticket_id)
    update_user_state_fn(admin_id, 'admin_replying_to_user_id', ticket['user_id'])
    update_user_state_fn(admin_id, 'admin_flow', 'awaiting_admin_reply_text')

    # Delete the ticket view message
    ticket_view_msg_id = get_user_state_fn(admin_id, 'admin_ticket_view_msg_id')
    if ticket_view_msg_id and ticket_view_msg_id == call.message.message_id:
        try: delete_message(bot_instance, chat_id, ticket_view_msg_id)
        except: pass
        update_user_state_fn(admin_id, 'admin_ticket_view_msg_id', None)
    elif call.message.message_id: # Fallback for safety
         try: delete_message(bot_instance, chat_id, call.message.message_id)
         except: pass

    reply_prompt_text = f"✍️ Replying to Ticket \\#{ticket_id} \\(User ID: `{ticket['user_id']}`\\)\\.\nSend your reply message now\\. Type /cancel\\_admin\\_action to abort\\."
    pm = bot_instance.send_message(chat_id, reply_prompt_text, parse_mode="MarkdownV2", reply_markup=types.ForceReply(selective=True))
    update_user_state_fn(admin_id, 'admin_reply_prompt_msg_id', pm.message_id)
    bot_instance.answer_callback_query(call.id, f"Ready for reply to Ticket #{ticket_id}")


def handle_admin_ticket_reply_message_content(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message):
    admin_id = message.from_user.id
    chat_id = message.chat.id
    ticket_id = get_user_state_fn(admin_id, 'admin_replying_to_ticket_id')
    target_user_id = get_user_state_fn(admin_id, 'admin_replying_to_user_id')

    prompt_id = get_user_state_fn(admin_id, 'admin_reply_prompt_msg_id')
    if prompt_id:
        try: delete_message(bot_instance, chat_id, prompt_id)
        except: pass
        update_user_state_fn(admin_id, 'admin_reply_prompt_msg_id', None)

    # Delete admin's reply message itself
    try: delete_message(bot_instance, chat_id, message.message_id)
    except: pass

    if message.text and message.text.lower() == '/cancel_admin_action':
        clear_user_state_fn(admin_id) # Clear flow and ticket context
        bot_instance.send_message(chat_id, "Reply cancelled.")
        return

    if not ticket_id or not target_user_id:
        bot_instance.send_message(chat_id, "Error: No ticket context for reply. Please start over.") # Use send_message as reply_to might fail if original msg deleted
        return

    admin_reply_text = message.text or message.caption or ""
    photo_file_id = message.photo[-1].file_id if message.photo else None
    message_content_for_db = admin_reply_text
    if photo_file_id: message_content_for_db += f" [Admin Image Attached: {photo_file_id}]"

    if not message_content_for_db.strip():
        # Re-prompt if reply is empty, or handle as error
        bot_instance.send_message(chat_id, "Reply seems empty. Please try again or /cancel_admin_action.")
        # Optionally re-send the ForceReply prompt here if desired.
        return

    add_success = db_utils.add_message_to_ticket(ticket_id, 'admin', message_content_for_db, admin_tg_message_id=message.message_id)
    if add_success:
        bot_instance.send_message(admin_id, f"✅ Reply sent for Ticket \\#{ticket_id}\\.", parse_mode="MarkdownV2")
        user_notification_text = f"💬 Admin has replied to your Ticket \\#`{ticket_id}`:\n\n{escape_md(admin_reply_text)}"
        try:
            if photo_file_id: bot_instance.send_photo(target_user_id, photo_file_id, caption=user_notification_text, parse_mode="MarkdownV2")
            else: bot_instance.send_message(target_user_id, user_notification_text, parse_mode="MarkdownV2")
        except Exception as e_user_notify:
            logger.error(f"Failed to send admin reply for ticket {ticket_id} to user {target_user_id}: {e_user_notify}")
            bot_instance.send_message(admin_id, f"⚠️ Failed to deliver your reply to user {target_user_id} for Ticket \\#{ticket_id}\\. Error: {escape_md(str(e_user_notify))}", parse_mode="MarkdownV2")
    else:
        bot_instance.send_message(admin_id, f"⚠️ Error saving reply for Ticket \\#{ticket_id}\\.", parse_mode="MarkdownV2")

    clear_user_state_fn(admin_id) # Clear flow and ticket context after reply

def handle_general_cancel_admin_action(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message):
    admin_id = message.from_user.id
    chat_id = message.chat.id

    # Extended list of potential prompt message IDs stored in user state
    prompt_ids_keys = [
        'admin_reply_prompt_msg_id', 'admin_ticket_list_pagination_msg_id',
        'admin_ticket_view_msg_id',
        'admin_last_prompt_msg_id', # From item addition
        'admin_user_list_main_msg_id', 'admin_view_user_details_msg_id'
        # Add any other state keys that store message IDs for admin prompts
    ]
    for key in prompt_ids_keys:
        prompt_id = get_user_state_fn(admin_id, key)
        if prompt_id:
            try: delete_message(bot_instance, chat_id, prompt_id)
            except: pass

    # Delete the /cancel_admin_action message itself
    try: delete_message(bot_instance, chat_id, message.message_id)
    except: pass

    clear_user_state_fn(admin_id) # Clears all flow specific data for the admin
    bot_instance.send_message(chat_id, "Your current admin action has been cancelled.")


def handle_admin_close_ticket_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    admin_id = call.from_user.id
    chat_id = call.message.chat.id
    try: ticket_id = int(call.data.split('admin_close_ticket_')[1])
    except (IndexError, ValueError): bot_instance.answer_callback_query(call.id, "Bad Ticket ID.", show_alert=True); return

    ticket = db_utils.get_ticket_details_by_id(ticket_id)
    if not ticket: bot_instance.answer_callback_query(call.id, f"Ticket #{ticket_id} not found."); return

    current_ticket_view_msg_id = get_user_state_fn(admin_id, 'admin_ticket_view_msg_id')

    if ticket['status'] != 'open':
        bot_instance.answer_callback_query(call.id, f"Ticket #{ticket_id} is already {ticket['status']}.")
        if current_ticket_view_msg_id == call.message.message_id : # If the message is the one showing this button
            closed_text = f"Ticket \\#`{ticket_id}` is already *{escape_md(ticket['status'].replace('_', ' '))}*\\."
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Tickets List", callback_data="admin_list_tickets_cmd_from_view"))
            # Edit the existing message to reflect it's already closed and update buttons
            send_or_edit_message(bot_instance, chat_id, closed_text, existing_message_id=current_ticket_view_msg_id, reply_markup=markup, parse_mode="MarkdownV2")
        return

    updated = db_utils.update_ticket_status(ticket_id, 'closed_by_admin')
    if updated:
        bot_instance.answer_callback_query(call.id, f"Ticket #{ticket_id} closed.")
        closed_text = f"Ticket \\#`{ticket_id}` is now *closed\\_by\\_admin*\\."
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Tickets List", callback_data="admin_list_tickets_cmd_from_view"))

        # If this callback came from the ticket view message, edit it. Otherwise, delete old and send new.
        if current_ticket_view_msg_id and str(get_user_state_fn(admin_id, 'admin_current_ticket_id')) == str(ticket_id) and call.message.message_id == current_ticket_view_msg_id:
             send_or_edit_message(bot_instance, chat_id, closed_text, existing_message_id=current_ticket_view_msg_id, reply_markup=markup, parse_mode="MarkdownV2")
             update_user_state_fn(admin_id, 'admin_ticket_view_msg_id', None) # Clear as it's now "Back to list"
        else: # Came from list view or other context
            try: delete_message(bot_instance, chat_id, call.message.message_id) # Delete the list item message
            except: pass
            bot_instance.send_message(chat_id, closed_text, parse_mode="MarkdownV2", reply_markup=markup) # Send new confirmation with back to list

        try: bot_instance.send_message(ticket['user_id'], f"ℹ️ Your Support Ticket \\#`{ticket_id}` has been closed by an administrator\\.", parse_mode="MarkdownV2")
        except Exception as e_notify: logger.error(f"Failed to notify user {ticket['user_id']} of ticket {ticket_id} closure: {e_notify}")
    else:
        bot_instance.answer_callback_query(call.id, f"Error closing ticket \\#{ticket_id}\\.", show_alert=True)

    if str(get_user_state_fn(admin_id, 'admin_current_ticket_id')) == str(ticket_id):
        update_user_state_fn(admin_id, 'admin_current_ticket_id', None)
        update_user_state_fn(admin_id, 'admin_flow', None)

# --- Admin Item Addition Flow (Filesystem Based) ---

# Placeholder for product_fs_utils, will be imported properly later
# For now, assume functions like product_fs_utils.get_available_cities() exist.
from modules import product_fs_utils # Ensure this is created and has the planned functions

def handle_admin_add_item_command(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message):
    admin_id = message.from_user.id
    chat_id = message.chat.id
    logger.info(f"Admin {admin_id} initiated /add command for new FS item.")

    try:
        delete_message(bot_instance, chat_id, message.message_id)
    except Exception as e_del:
        logger.warning(f"Could not delete /add command message: {e_del}")

    clear_user_state_fn(admin_id) # Clear any previous admin flow
    update_user_state_fn(admin_id, 'admin_add_item_flow', {'step': 'select_city', 'data': {}})

    cities = product_fs_utils.get_available_cities()
    markup = types.InlineKeyboardMarkup(row_width=2)
    city_buttons = []
    for city in cities:
        city_buttons.append(types.InlineKeyboardButton(f"🏙️ {escape_md(city)}", callback_data=f"admin_add_city_{city}"))

    # Dynamic button creation
    paired_buttons = [city_buttons[i:i + 2] for i in range(0, len(city_buttons), 2)]
    for pair in paired_buttons:
        markup.row(*pair)

    markup.add(types.InlineKeyboardButton("➕ Create New City", callback_data="admin_add_city_new"))
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_add_item_cancel"))

    prompt_text = "🛍️ *Add New Item Instance*\n\nStep 1: Select City or Create New"
    msg = send_or_edit_message(bot_instance, chat_id, escape_md(prompt_text), reply_markup=markup, parse_mode="MarkdownV2")
    if msg:
        update_user_state_fn(admin_id, 'admin_last_prompt_msg_id', msg.message_id)

def handle_admin_add_item_cancel_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    admin_id = call.from_user.id
    chat_id = call.message.chat.id

    last_prompt_msg_id = get_user_state_fn(admin_id, 'admin_last_prompt_msg_id')
    if last_prompt_msg_id:
        try:
            delete_message(bot_instance, chat_id, last_prompt_msg_id)
        except Exception as e:
            logger.warning(f"Failed to delete last prompt message on cancel: {e}")

    clear_user_state_fn(admin_id)
    send_or_edit_message(bot_instance, chat_id, "Item addition cancelled.", existing_message_id=call.message.message_id if call.message else None)
    bot_instance.answer_callback_query(call.id, "Cancelled.")

def _handle_admin_add_item_step(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call_or_message, selected_value=None):
    # Generic handler for stepping through the add item flow
    is_call = isinstance(call_or_message, types.CallbackQuery)
    admin_id = call_or_message.from_user.id
    chat_id = call_or_message.message.chat.id if is_call else call_or_message.chat.id

    flow_data = get_user_state_fn(admin_id, 'admin_add_item_flow')
    if not flow_data:
        send_or_edit_message(bot_instance, chat_id, "Error: Item addition flow data not found. Please start over with /add.", existing_message_id=call_or_message.message.message_id if is_call else None)
        if is_call: bot_instance.answer_callback_query(call_or_message.id, "Flow error.")
        return

    current_step = flow_data.get('step')
    item_data = flow_data.get('data', {})

    # Delete previous prompt message
    last_prompt_msg_id = get_user_state_fn(admin_id, 'admin_last_prompt_msg_id')
    if last_prompt_msg_id:
        try:
            delete_message(bot_instance, chat_id, last_prompt_msg_id)
        except Exception as e:
            logger.warning(f"Failed to delete previous prompt message: {e}")
        update_user_state_fn(admin_id, 'admin_last_prompt_msg_id', None)

    # Delete user's message if it's a text input step
    if not is_call and hasattr(call_or_message, 'message_id'):
        try:
            delete_message(bot_instance, chat_id, call_or_message.message_id)
        except Exception:
            pass

    next_step_info = {}
    markup = types.InlineKeyboardMarkup(row_width=2)

    if current_step == 'select_city':
        if selected_value == "admin_add_city_new":
            next_step_info = {'step': 'awaiting_new_city_name', 'prompt': "🏙️ Enter new city name:"}
            markup = types.ForceReply(selective=True) # Expect text input
        else:
            item_data['city'] = call_or_message.data.split('admin_add_city_')[1]
            next_step_info = {'step': 'select_area', 'prompt': f"Selected City: *{escape_md(item_data['city'])}*\n\nStep 2: Select Area or Create New"}
            areas = product_fs_utils.get_available_areas(item_data['city'])
            area_buttons = [types.InlineKeyboardButton(f"📍 {escape_md(a)}", callback_data=f"admin_add_area_{a}") for a in areas]
            paired_buttons = [area_buttons[i:i + 2] for i in range(0, len(area_buttons), 2)]
            for pair in paired_buttons: markup.row(*pair)
            markup.add(types.InlineKeyboardButton("➕ Create New Area", callback_data="admin_add_area_new"))

    elif current_step == 'awaiting_new_city_name':
        item_data['city'] = selected_value # selected_value is the new city name from text
        # Basic validation for folder name
        if not selected_value or not selected_value.replace(" ", "").isalnum():
            next_step_info = {'step': 'awaiting_new_city_name', 'prompt': "Invalid city name. Use alphanumeric characters and spaces only. Enter new city name:"}
            markup = types.ForceReply(selective=True)
        else:
            next_step_info = {'step': 'select_area', 'prompt': f"Created City: *{escape_md(item_data['city'])}*\n\nStep 2: Select Area or Create New"}
            # Since city is new, there are no existing areas yet
            markup.add(types.InlineKeyboardButton("➕ Create New Area", callback_data="admin_add_area_new"))

    elif current_step == 'select_area':
        if selected_value == "admin_add_area_new":
            next_step_info = {'step': 'awaiting_new_area_name', 'prompt': f"City: *{escape_md(item_data['city'])}*\n\nEnter new area name:"}
            markup = types.ForceReply(selective=True)
        else:
            item_data['area'] = call_or_message.data.split('admin_add_area_')[1]
            next_step_info = {'step': 'select_item_type', 'prompt': f"City: *{escape_md(item_data['city'])}* / Area: *{escape_md(item_data['area'])}*\n\nStep 3: Select Item Type or Create New"}
            item_types = product_fs_utils.get_available_item_types(item_data['city'], item_data['area'])
            type_buttons = [types.InlineKeyboardButton(f"🏷️ {escape_md(it)}", callback_data=f"admin_add_type_{it}") for it in item_types]
            paired_buttons = [type_buttons[i:i + 2] for i in range(0, len(type_buttons), 2)]
            for pair in paired_buttons: markup.row(*pair)
            markup.add(types.InlineKeyboardButton("➕ Create New Item Type", callback_data="admin_add_type_new"))

    elif current_step == 'awaiting_new_type_name':
        item_data['item_type'] = selected_value
        if not selected_value or not selected_value.replace(" ", "").isalnum(): # Basic validation
            next_step_info = {'step': 'awaiting_new_type_name', 'prompt': "Invalid type name. Use alphanumeric and spaces. Enter new item type name:"}
            markup = types.ForceReply(selective=True)
        else:
            next_step_info = {'step': 'select_size', 'prompt': f"...Created Item Type: *{escape_md(item_data['item_type'])}*\n\nStep 4: Select Size or Create New"}
            markup.add(types.InlineKeyboardButton("➕ Create New Size", callback_data="admin_add_size_new"))

    elif current_step == 'select_size':
        if selected_value == "admin_add_size_new":
            next_step_info = {'step': 'awaiting_new_size_name', 'prompt': f"...Type: *{escape_md(item_data['item_type'])}*\n\nEnter new size name (e.g., Small, Large, 330ml):"}
            markup = types.ForceReply(selective=True)
        else:
            item_data['size'] = call_or_message.data.split('admin_add_size_')[1]
            next_step_info = {'step': 'awaiting_price', 'prompt': f"...Size: *{escape_md(item_data['size'])}*\n\nStep 5: Enter Price (EUR, e.g., 10.99)"}
            markup = types.ForceReply(selective=True)

    elif current_step == 'awaiting_new_size_name':
        item_data['size'] = selected_value
        if not selected_value or not selected_value.replace(" ", "").replace(".", "").isalnum(): # Basic validation
            next_step_info = {'step': 'awaiting_new_size_name', 'prompt': "Invalid size name. Use alphanumeric, spaces, dots. Enter new size name:"}
            markup = types.ForceReply(selective=True)
        else:
            next_step_info = {'step': 'awaiting_price', 'prompt': f"...Created Size: *{escape_md(item_data['size'])}*\n\nStep 5: Enter Price (EUR, e.g., 10.99)"}
            markup = types.ForceReply(selective=True)

    elif current_step == 'awaiting_price':
        try:
            item_data['price'] = float(selected_value.replace(',', '.'))
            if item_data['price'] < 0: raise ValueError("Price must be non-negative")
            item_data['images'] = [] # Initialize for image collection
            next_step_info = {'step': 'awaiting_images', 'prompt': f"Price: *{item_data['price']:.2f} EUR*\n\nStep 6: Send up to 3 images one by one. Type /done_images when finished."}
            # No ForceReply here, user sends photos or /done_images command
        except (ValueError, TypeError):
            next_step_info = {'step': 'awaiting_price', 'prompt': "Invalid price. Please enter a number (e.g., 10.99).\n\nEnter Price:"}
            markup = types.ForceReply(selective=True)

    elif current_step == 'awaiting_images': # This step is handled by a separate message handler for photos & /done_images
        # This function shouldn't be directly called for this state via callback, but for text message.
        # If called by /done_images, selected_value would be that command.
        if selected_value == "/done_images": # Command from text
            next_step_info = {'step': 'awaiting_description', 'prompt': f"Images collected: {len(item_data.get('images',[]))}\n\nStep 7: Enter item description."}
            markup = types.ForceReply(selective=True)
        else: # Should not happen if logic is correct, means unexpected text was sent.
             bot_instance.send_message(chat_id, "Please send a photo or type /done_images.")
             return # Stay in image step, don't update prompt message from here.

    elif current_step == 'awaiting_description':
        item_data['description'] = selected_value
        next_step_info = {'step': 'confirm_add', 'prompt': "Final Step: Confirm Details"}
        # Build confirmation message and buttons
        summary = (f"City: *{escape_md(item_data['city'])}*\nArea: *{escape_md(item_data['area'])}*\n"
                   f"Type: *{escape_md(item_data['item_type'])}*\nSize: *{escape_md(item_data['size'])}*\n"
                   f"Price: *{item_data['price']:.2f} EUR*\nImages: *{len(item_data.get('images',[]))}*\n"
                   f"Description: _{escape_md(item_data['description'][:100])}{'...' if len(item_data['description'])>100 else ''}_")
        next_step_info['prompt'] = f"Please review:\n\n{summary}\n\nAdd this item instance?"
        markup.add(types.InlineKeyboardButton("✅ Yes, Add Item", callback_data="admin_add_item_execute"))
        markup.add(types.InlineKeyboardButton("✏️ Restart Item Addition", callback_data="admin_add_item_restart")) # Full restart

    else: # Unknown step or end of flow
        send_or_edit_message(bot_instance, chat_id, "Unknown step in item addition. Please start over with /add.")
        clear_user_state_fn(admin_id)
        if is_call: bot_instance.answer_callback_query(call_or_message.id, "Error.")
        return

    flow_data['step'] = next_step_info['step']
    flow_data['data'] = item_data
    update_user_state_fn(admin_id, 'admin_add_item_flow', flow_data)

    if not (next_step_info['step'] == 'awaiting_images' and selected_value != "/done_images"): # Don't send prompt if waiting for image and it wasn't /done
        # For ForceReply, existing_message_id should be None to send a new message for reply.
        # For InlineKeyboards, we can edit.
        existing_id_for_step = call_or_message.message.message_id if is_call and not isinstance(markup, types.ForceReply) else None

        final_prompt_text = escape_md(next_step_info['prompt'])
        if not isinstance(markup, types.ForceReply): # Add cancel button to inline keyboards
             markup.add(types.InlineKeyboardButton("❌ Cancel Item Addition", callback_data="admin_add_item_cancel"))

        msg = send_or_edit_message(bot_instance, chat_id, final_prompt_text, reply_markup=markup,
                                   existing_message_id=existing_id_for_step, parse_mode="MarkdownV2")
        if msg:
            update_user_state_fn(admin_id, 'admin_last_prompt_msg_id', msg.message_id)

    if is_call:
        bot_instance.answer_callback_query(call_or_message.id)

# Callback handlers for each step that uses buttons
def handle_admin_add_item_step_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    action = call.data # e.g., admin_add_city_CityA, admin_add_city_new, admin_add_item_execute
    _handle_admin_add_item_step(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call, selected_value=action)

# Message handlers for steps that expect text input
def handle_admin_add_item_text_input(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message):
    text_value = message.text.strip()
    _handle_admin_add_item_step(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message, selected_value=text_value)

# Specific handler for /done_images command or photo messages
def handle_admin_add_item_images_input(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message):
    admin_id = message.from_user.id
    chat_id = message.chat.id
    flow_data = get_user_state_fn(admin_id, 'admin_add_item_flow')

    if not flow_data or flow_data.get('step') != 'awaiting_images':
        # Not in the image step, ignore or handle as unexpected
        return

    item_data = flow_data.get('data', {})
    last_prompt_msg_id = get_user_state_fn(admin_id, 'admin_last_prompt_msg_id')
    if last_prompt_msg_id:
        try: delete_message(bot_instance, chat_id, last_prompt_msg_id)
        except Exception: pass
    try: delete_message(bot_instance, chat_id, message.message_id) # Delete user's photo message or /done_images
    except Exception: pass

    if message.text and message.text.lower() == '/done_images':
        _handle_admin_add_item_step(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message, selected_value="/done_images")
    elif message.photo:
        if len(item_data.get('images', [])) < 3:
            photo_file_info = bot_instance.get_file(message.photo[-1].file_id)
            downloaded_bytes = bot_instance.download_file(photo_file_info.file_path)

            # Try to get original filename if possible, else generate one
            original_filename = f"image_{len(item_data.get('images', [])) + 1}.jpg" # Default
            if hasattr(photo_file_info, 'file_path') and photo_file_info.file_path:
                fname_from_path = os.path.basename(photo_file_info.file_path)
                if fname_from_path: # Use original extension if present
                     original_filename = f"image_{len(item_data.get('images', [])) + 1}{os.path.splitext(fname_from_path)[1] or '.jpg'}"


            item_data.setdefault('images', []).append({'filename': original_filename, 'bytes': downloaded_bytes})
            update_user_state_fn(admin_id, 'admin_add_item_flow', flow_data) # data in flow_data is updated

            prompt_text = f"Image {len(item_data['images'])}/3 received. Send another, or type /done_images."
            if len(item_data['images']) >= 3:
                prompt_text = "Maximum 3 images received. Type /done_images to continue."
            msg = bot_instance.send_message(chat_id, prompt_text)
            update_user_state_fn(admin_id, 'admin_last_prompt_msg_id', msg.message_id)
        else:
            msg = bot_instance.send_message(chat_id, "Max 3 images already. Type /done_images.")
            update_user_state_fn(admin_id, 'admin_last_prompt_msg_id', msg.message_id)
    else: # Unexpected text other than /done_images
        msg = bot_instance.send_message(chat_id, "Please send a photo or type /done_images.")
        update_user_state_fn(admin_id, 'admin_last_prompt_msg_id', msg.message_id)


def handle_admin_add_item_execute(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    admin_id = call.from_user.id
    chat_id = call.message.chat.id
    flow_data = get_user_state_fn(admin_id, 'admin_add_item_flow')

    if not flow_data or flow_data.get('step') != 'confirm_add':
        send_or_edit_message(bot_instance, chat_id, "Error: Confirmation step not found. Please start over.", existing_message_id=call.message.message_id)
        bot_instance.answer_callback_query(call.id, "Error.")
        clear_user_state_fn(admin_id)
        return

    item_data = flow_data['data']
    images_to_save = [(img_info['filename'], img_info['bytes']) for img_info in item_data.get('images', [])]

    instance_path = product_fs_utils.add_item_instance(
        city=item_data['city'],
        area=item_data['area'],
        item_type=item_data['item_type'],
        size=item_data['size'],
        price=item_data['price'],
        images=images_to_save,
        description=item_data['description']
    )

    if instance_path:
        send_or_edit_message(bot_instance, chat_id, f"✅ Successfully added new item instance to filesystem at: `{escape_md(instance_path)}`", existing_message_id=call.message.message_id, parse_mode="MarkdownV2")
    else:
        send_or_edit_message(bot_instance, chat_id, "❌ Error: Failed to save item instance to filesystem. Check logs.", existing_message_id=call.message.message_id)

    bot_instance.answer_callback_query(call.id)
    clear_user_state_fn(admin_id)


# --- Admin User Management ---
# (Keeping user management for now, as it's not directly conflicting with FS item management)
def command_view_users(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, message, page=0):
    admin_id = message.from_user.id
    chat_id = message.chat.id
    logger.info(f"Admin {admin_id} requested /viewusers, page {page}.")

    existing_list_msg_id = get_user_state_fn(admin_id, 'admin_user_list_main_msg_id')

    if hasattr(message, 'text') and message.text and message.text.startswith('/viewusers'):
        try: delete_message(bot_instance, chat_id, message.message_id)
        except: pass
        if existing_list_msg_id:
            try: delete_message(bot_instance, chat_id, existing_list_msg_id)
            except: pass
        update_user_state_fn(admin_id, 'admin_user_list_main_msg_id', None)
        existing_list_msg_id = None
    elif hasattr(message, 'message') and message.message:
        existing_list_msg_id = message.message.message_id

    users_list, total_users = db_utils.get_all_users_admin(limit=USERS_PER_PAGE_ADMIN, offset=page * USERS_PER_PAGE_ADMIN)

    if not users_list and total_users == 0:
        if existing_list_msg_id:
            try: delete_message(bot_instance, chat_id, existing_list_msg_id)
            except: pass
            update_user_state_fn(admin_id, 'admin_user_list_main_msg_id', None)
        bot_instance.send_message(chat_id, "No users found in the system.")
        return

    response_text = f"👥 *User List (Page {page + 1} / { (total_users + USERS_PER_PAGE_ADMIN -1) // USERS_PER_PAGE_ADMIN })*\nSelect a user to view details:\n\n"

    markup = types.InlineKeyboardMarkup(row_width=1)

    if not users_list and page > 0 :
        response_text += "No users on this page."
    else:
        for user_row in users_list:
            user_id_to_view = user_row['user_id']
            user_info_line = (f"ID: `{user_id_to_view}` B: *{user_row['balance']:.2f}€* Txs: *{user_row['transaction_count']}*")
            markup.add(types.InlineKeyboardButton(user_info_line, callback_data=f"admin_view_user_details_{user_id_to_view}"))

    nav_buttons_row = []
    if page > 0:
        nav_buttons_row.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"admin_users_page_{page - 1}"))
    if (total_users > USERS_PER_PAGE_ADMIN) and not (page == 0 and total_users <= USERS_PER_PAGE_ADMIN) :
         nav_buttons_row.append(types.InlineKeyboardButton(f"📄 {page+1}", callback_data=f"admin_users_page_{page}"))
    if (page + 1) * USERS_PER_PAGE_ADMIN < total_users:
        nav_buttons_row.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"admin_users_page_{page + 1}"))

    if nav_buttons_row:
        markup.row(*nav_buttons_row)

    sent_msg = send_or_edit_message(bot_instance, chat_id, response_text,
                                    reply_markup=markup,
                                    existing_message_id=existing_list_msg_id,
                                    parse_mode="MarkdownV2")
    if sent_msg:
        update_user_state_fn(admin_id, 'admin_user_list_main_msg_id', sent_msg.message_id)
    update_user_state_fn(admin_id, 'admin_user_list_current_page', page)


def callback_view_users_page(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    admin_id = call.from_user.id
    try:
        page = int(call.data.split('_')[-1])
    except (IndexError, ValueError):
        bot_instance.answer_callback_query(call.id, "Invalid page number.", show_alert=True)
        return

    bot_instance.answer_callback_query(call.id)
    command_view_users(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call, page=page)

def handle_admin_view_user_details_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call, page=0):
    admin_id = call.from_user.id
    chat_id = call.message.chat.id
    target_user_id = None # Initialize

    if call.data.startswith('admin_view_user_details_page_'):
        try:
            parts = call.data.split('_')
            target_user_id = int(parts[4])
            page = int(parts[5])
            update_user_state_fn(admin_id, 'admin_viewing_user_id', target_user_id)
            update_user_state_fn(admin_id, 'admin_viewing_user_tx_page', page)
        except (IndexError, ValueError):
            bot_instance.answer_callback_query(call.id, "Invalid user/page for details.", show_alert=True); return
    elif call.data.startswith('admin_view_user_details_'):
        try:
            target_user_id = int(call.data.split('admin_view_user_details_')[1])
            update_user_state_fn(admin_id, 'admin_viewing_user_id', target_user_id)
            update_user_state_fn(admin_id, 'admin_viewing_user_tx_page', 0)
            page = 0
        except (IndexError, ValueError):
            bot_instance.answer_callback_query(call.id, "Invalid user ID for details.", show_alert=True); return
    else:
        bot_instance.answer_callback_query(call.id, "Unknown action.", show_alert=True); return

    if target_user_id is None: # Should be set by one of the branches above
        bot_instance.answer_callback_query(call.id, "Target user ID not determined.", show_alert=True); return

    logger.info(f"Admin {admin_id} viewing details for user {target_user_id}, tx page {page}.")
    update_user_state_fn(admin_id, 'admin_flow', 'admin_viewing_user_details')

    user_data = get_or_create_user(target_user_id)
    if not user_data:
        bot_instance.answer_callback_query(call.id, "User not found.", show_alert=True)
        user_list_page = get_user_state_fn(admin_id, 'admin_user_list_current_page', 0)
        command_view_users(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call, page=user_list_page)
        return

    # Delete the main user list message or previous detail view message
    # If called from user list, call.message.id is the user list message.
    # If called from its own pagination, call.message.id is the user detail message.
    if call.message and call.message.message_id:
        try: delete_message(bot_instance, chat_id, call.message.message_id)
        except: pass
    # Clear specific message IDs from state
    update_user_state_fn(admin_id, 'admin_user_list_main_msg_id', None)
    update_user_state_fn(admin_id, 'admin_view_user_details_msg_id', None)


    balance = user_data['balance']
    total_user_transactions = user_data['transaction_count']

    user_info_text = (
        f"👤 *User Details: ID `{target_user_id}`*\n\n"
        f"Current Balance: *{balance:.2f} EUR*\n"
        f"Total Transactions: *{total_user_transactions}*"
    )

    offset = page * TX_HISTORY_PAGE_SIZE
    transactions = get_user_transaction_history(target_user_id, limit=TX_HISTORY_PAGE_SIZE, offset=offset)
    history_text_formatted = format_transaction_history_display(transactions)

    full_user_detail_text = user_info_text + history_text_formatted

    markup = types.InlineKeyboardMarkup(row_width=2)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Prev TXs", callback_data=f"admin_view_user_details_page_{target_user_id}_{page - 1}"))

    total_tx_pages_for_user = (total_user_transactions + TX_HISTORY_PAGE_SIZE - 1) // TX_HISTORY_PAGE_SIZE
    if (page + 1) * TX_HISTORY_PAGE_SIZE < total_user_transactions:
        nav_buttons.append(types.InlineKeyboardButton("Next TXs ➡️", callback_data=f"admin_view_user_details_page_{target_user_id}_{page + 1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(types.InlineKeyboardButton("💰 Adjust Balance", callback_data=f"admin_adjust_bal_init_{target_user_id}"))
    markup.add(types.InlineKeyboardButton("⬅️ Back to User List", callback_data="admin_back_to_user_list"))

    sent_msg = bot_instance.send_message(chat_id, full_user_detail_text, reply_markup=markup, parse_mode="MarkdownV2")
    update_user_state_fn(admin_id, 'admin_view_user_details_msg_id', sent_msg.message_id)
    bot_instance.answer_callback_query(call.id)

def handle_admin_back_to_user_list_callback(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call):
    admin_id = call.from_user.id
    details_msg_id = get_user_state_fn(admin_id, 'admin_view_user_details_msg_id')
    if details_msg_id: # This is the user detail message with TX history
        try: delete_message(bot_instance, call.message.chat.id, details_msg_id)
        except: pass
        update_user_state_fn(admin_id, 'admin_view_user_details_msg_id', None)

    page = get_user_state_fn(admin_id, 'admin_user_list_current_page', 0)
    # Clear specific user view states, but keep admin_user_list_current_page
    update_user_state_fn(admin_id, 'admin_viewing_user_id', None)
    update_user_state_fn(admin_id, 'admin_viewing_user_tx_page', None)
    update_user_state_fn(admin_id, 'admin_flow', None) # Reset general admin flow

    command_view_users(bot_instance, clear_user_state_fn, get_user_state_fn, update_user_state_fn, call, page=page)
    bot_instance.answer_callback_query(call.id)


if __name__ == '__main__':
    logger.info("Admin Handler module loaded.")
