import telebot
from telebot import types
import json
import time
import logging
from modules.db_utils import (
    get_or_create_user, update_user_balance, # Keep user related
    record_transaction, update_transaction_status, # Keep transaction related
    get_pending_payment_by_transaction_id, # Keep payment related
    update_pending_payment_status, # Keep payment related
    increment_user_transaction_count, # Keep user related
    get_next_address_index, create_pending_payment, # HD Wallet specific
    update_main_transaction_for_hd_payment, # HD Wallet specific
    get_transaction_by_id # transaction related
    # Removed: get_cities_with_available_items, get_available_items_in_city,
    # get_product_details_by_id, sync_item_from_fs_to_db (these will be handled by product_fs_utils)
)
# from modules import file_system_utils # This will be replaced by product_fs_utils
from modules import product_fs_utils # New FS utility for products
from modules.message_utils import send_or_edit_message, delete_message # Removed escape_markdownv2
from modules.text_utils import escape_md # Keep escape_md import for now if it's used elsewhere with version 1 or for other purposes
from modules import hd_wallet_utils, exchange_rate_utils, payment_monitor
import config
import os
import datetime # Ensure datetime is imported
from decimal import Decimal, ROUND_UP # Ensure ROUND_UP is imported
import sqlite3 # For specific exception handling in finalize

from handlers.main_menu_handler import get_main_menu_text_and_markup # For fallbacks


logger = logging.getLogger(__name__)


def handle_buy_initiate_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    logger.info(f"handle_buy_initiate_callback called for user {call.from_user.id}")
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = call.message.message_id
    logger.info(f"User {user_id} initiated buy flow.")

    try:
        clear_user_state(user_id)
        update_user_state(user_id, 'current_flow', 'buy_selecting_city')

        available_cities = product_fs_utils.get_available_cities() # Use new FS util
        markup = types.InlineKeyboardMarkup(row_width=2)
        prompt_text = "🏙️ Please select a city:"

        if not available_cities:
            prompt_text = "😔 We're sorry, there are currently no items available for purchase. Please check back later."
            # As per spec: "If no cities are available, displays a general error message ... and returns to the main menu."
            # This part is tricky if we are editing an existing message. We might need to send a new one.
            # For now, just update the text. A better UX might involve deleting this message and sending main menu.
            # However, the current structure sends/edits then answers callback.
        else:
            city_buttons = []
            for city_name in available_cities:
                # Check if city actually has any item instance available down the hierarchy
                # This could be slow if done for every city here.
                # Assuming get_available_cities only returns cities that *could* have items.
                # A more robust check: loop through areas, types, sizes to see if any get_oldest_available_item_instance is not None.
                # For now, we'll list all cities returned by get_available_cities.
                # The check will happen at a later stage (e.g. when selecting item type or size).
                city_name_escaped_for_button = escape_md(city_name) # Use escape_md
                city_buttons.append(types.InlineKeyboardButton(text=f"🏙️ {city_name_escaped_for_button}", callback_data=f"select_city_{city_name}"))

            for i in range(0, len(city_buttons), 2):
                if i + 1 < len(city_buttons):
                    markup.add(city_buttons[i], city_buttons[i+1])
                else:
                    markup.add(city_buttons[i])

        if not available_cities : # If prompt_text was changed to no items
             # If we are editing, the buttons might remain if not cleared.
             # Let's ensure markup only has 'Back to Main Menu' if no cities.
             markup = types.InlineKeyboardMarkup(row_width=1)


        markup.add(types.InlineKeyboardButton(text="⬅️ Back to Main Menu", callback_data="back_to_main"))

        # Message Management: "the previous message from the bot should be updated or replaced"
        # send_or_edit_message handles this if existing_message_id is the main menu message.
        sent_message = None
        buy_flow_image_path = getattr(config, 'BUY_FLOW_IMAGE_PATH', None)
        photo_exists_and_valid = buy_flow_image_path and os.path.exists(buy_flow_image_path)

        if photo_exists_and_valid:
            if existing_message_id:
                try:
                    delete_message(bot_instance, chat_id, existing_message_id)
                except Exception as e_del:
                    logger.warning(f"Notice: Could not delete previous message {existing_message_id} before sending buy_initiate photo for user {user_id}: {e_del}")

            with open(buy_flow_image_path, 'rb') as photo_file:
                sent_message = bot_instance.send_photo(
                    chat_id,
                    photo=photo_file,
                    caption=prompt_text,
                    reply_markup=markup
                )
        else:
            sent_message = send_or_edit_message(
                bot=bot_instance,
                chat_id=chat_id,
                text=prompt_text,
                reply_markup=markup,
                existing_message_id=existing_message_id
            )

        if sent_message:
            update_user_state(user_id, 'last_bot_message_id', sent_message) # Use the returned message ID directly

        bot_instance.answer_callback_query(call.id)

    except Exception as e:
        logger.exception(f"Error in handle_buy_initiate_callback for user {user_id}: {e}")
        bot_instance.answer_callback_query(call.id, "An error occurred while loading cities.")
        try:
            fallback_markup = types.InlineKeyboardMarkup()
            btn_fallback_back = types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")
            fallback_markup.add(btn_fallback_back)
            send_or_edit_message(bot_instance, chat_id, "Sorry, there was an error. Please try returning to the main menu.",
                                 reply_markup=fallback_markup, existing_message_id=existing_message_id)
        except Exception as e_fallback:
            logger.error(f"Error sending fallback message in handle_buy_initiate_callback to user {user_id}: {e_fallback}")


def handle_city_selection_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id
    logger.info(f"User {user_id} selected city via callback: {call.data}")

    try:
        city_name = call.data.split('select_city_', 1)[1]
    except IndexError:
        logger.warning(f"Invalid callback data for city selection: {call.data} by user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error processing city selection. Please try again.", show_alert=True)
        return

    update_user_state(user_id, 'buy_selected_city', city_name)
    update_user_state(user_id, 'current_flow', 'buy_selecting_area') # Next step is area

    available_areas = product_fs_utils.get_available_areas(city_name)
    markup = types.InlineKeyboardMarkup(row_width=2) # Can use 2 for areas too
    escaped_city_name = escape_md(city_name) # Use escape_md

    if not available_areas:
        prompt_text = f"😔 No areas with items currently available in *{escaped_city_name}*\\. Please select another city or check back later."
        # As per spec, if no items (implicitly areas lead to items), alert and return to city selection
        # Here, if no areas, it's similar.
        bot_instance.answer_callback_query(call.id, f"No areas found in {city_name}.", show_alert=True)
        # This should ideally take them back to city selection.
        # The current structure will send the message then answer.
        # To go back, we'd call handle_buy_initiate_callback.
        # For now, let the message show, and the back button will work.
        markup = types.InlineKeyboardMarkup(row_width=1) # Reset markup for this case
    else:
        prompt_text = f"You selected city: *{escaped_city_name}*\\.\nNow, please select an area:"
        area_buttons = []
        for area_name in available_areas:
            area_name_escaped = escape_md(area_name) # Use escape_md
            # TODO: Potentially check if area has item types before listing
            area_buttons.append(types.InlineKeyboardButton(text=f"📍 {area_name_escaped}", callback_data=f"select_area_{area_name}"))

        for i in range(0, len(area_buttons), 2):
            if i + 1 < len(area_buttons):
                markup.add(area_buttons[i], area_buttons[i+1])
            else:
                markup.add(area_buttons[i])

    markup.add(types.InlineKeyboardButton("⬅️ Back to City Selection", callback_data="buy_initiate"))
    # markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")) # Already in city selection back button

    sent_message_id = send_or_edit_message(
        bot=bot_instance,
        chat_id=chat_id,
        text=prompt_text,
        reply_markup=markup,
        existing_message_id=existing_message_id,
        parse_mode="MarkdownV2"
    )
    if sent_message_id:
        update_user_state(user_id, 'last_bot_message_id', sent_message_id)
    bot_instance.answer_callback_query(call.id)


# New handler for area selection
def handle_area_selection_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id

    try:
        area_name = call.data.split('select_area_', 1)[1]
    except IndexError:
        logger.warning(f"Invalid callback data for area selection: {call.data} by user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error processing area selection.", show_alert=True)
        return

    selected_city = get_user_state(user_id, 'buy_selected_city')
    if not selected_city:
        logger.error(f"User {user_id} in area selection without city selected.")
        bot_instance.answer_callback_query(call.id, "Error: City not selected. Please start over.", show_alert=True)
        # Ideally, send back to city selection or main menu
        handle_buy_initiate_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call) # Restart
        return

    update_user_state(user_id, 'buy_selected_area', area_name)
    update_user_state(user_id, 'current_flow', 'buy_selecting_item_type')

    available_item_types = product_fs_utils.get_available_item_types(selected_city, area_name)
    markup = types.InlineKeyboardMarkup(row_width=1) # Usually 1 for item types / sizes
    escaped_area_name = escape_md(area_name)

    if not available_item_types:
        prompt_text = f"😔 No item types currently available in *{escaped_area_name}* area of *{escape_md(selected_city)}*\\."
        bot_instance.answer_callback_query(call.id, f"No item types in {area_name}.", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=1) # Ensure only back button if none
    else:
        prompt_text = f"City: *{escape_md(selected_city)}* / Area: *{escaped_area_name}*\\.\nPlease select an item type:"
        for item_type_name in available_item_types:
            # TODO: Check if item_type has sizes and instances before listing
            item_type_escaped = escape_md(item_type_name)
            markup.add(types.InlineKeyboardButton(text=f"🏷️ {item_type_escaped}", callback_data=f"select_type_{item_type_name}"))

    markup.add(types.InlineKeyboardButton("⬅️ Back to Area Selection", callback_data=f"select_city_{selected_city}"))

    sent_message_id = send_or_edit_message(
        bot=bot_instance,
        chat_id=chat_id,
        text=prompt_text,
        reply_markup=markup,
        existing_message_id=existing_message_id,
        parse_mode="MarkdownV2"
    )
    if sent_message_id:
        update_user_state(user_id, 'last_bot_message_id', sent_message_id)
    bot_instance.answer_callback_query(call.id)


# New handler for item type selection
def handle_type_selection_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id

    try:
        item_type_name = call.data.split('select_type_', 1)[1]
    except IndexError:
        logger.warning(f"Invalid callback data for type selection: {call.data} by user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error processing item type selection.", show_alert=True)
        return

    selected_city = get_user_state(user_id, 'buy_selected_city')
    selected_area = get_user_state(user_id, 'buy_selected_area')
    if not selected_city or not selected_area:
        logger.error(f"User {user_id} in type selection without city/area selected.")
        bot_instance.answer_callback_query(call.id, "Error: City/Area not selected. Please start over.", show_alert=True)
        handle_buy_initiate_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call) # Restart
        return

    update_user_state(user_id, 'buy_selected_item_type', item_type_name)
    update_user_state(user_id, 'current_flow', 'buy_selecting_size')

    available_sizes = product_fs_utils.get_available_sizes(selected_city, selected_area, item_type_name)
    markup = types.InlineKeyboardMarkup(row_width=1)
    escaped_item_type_name = escape_md(item_type_name)

    if not available_sizes:
        prompt_text = f"😔 No sizes currently available for *{escaped_item_type_name}* in this area\\."
        bot_instance.answer_callback_query(call.id, f"No sizes for {item_type_name}.", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=1)
    else:
        prompt_text = f"...Type: *{escaped_item_type_name}*\\.\nPlease select a size:"
        for size_name in available_sizes:
            # TODO: Check if size has actual instances before listing
            size_name_escaped = escape_md(size_name)
            markup.add(types.InlineKeyboardButton(text=f"📏 {size_name_escaped}", callback_data=f"select_size_{size_name}"))

    markup.add(types.InlineKeyboardButton("⬅️ Back to Item Type Selection", callback_data=f"select_area_{selected_area}"))

    sent_message_id = send_or_edit_message(
        bot=bot_instance,
        chat_id=chat_id,
        text=prompt_text,
        reply_markup=markup,
        existing_message_id=existing_message_id,
        parse_mode="MarkdownV2"
    )
    if sent_message_id:
        update_user_state(user_id, 'last_bot_message_id', sent_message_id)
    bot_instance.answer_callback_query(call.id)

# Renamed from handle_item_selection_callback to handle_size_selection_callback
# This is where the actual item instance is chosen and details are displayed.
def handle_size_selection_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id

    try:
        size_name = call.data.split('select_size_', 1)[1]
    except IndexError:
        logger.warning(f"Invalid callback data for size selection: {call.data} by user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error processing size selection.", show_alert=True)
        return

    selected_city = get_user_state(user_id, 'buy_selected_city')
    selected_area = get_user_state(user_id, 'buy_selected_area')
    selected_item_type = get_user_state(user_id, 'buy_selected_item_type')

    if not all([selected_city, selected_area, selected_item_type]):
        logger.error(f"User {user_id} in size selection without full path selected.")
        bot_instance.answer_callback_query(call.id, "Error: Full item path not selected. Please start over.", show_alert=True)
        handle_buy_initiate_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call) # Restart
        return

    update_user_state(user_id, 'buy_selected_size', size_name)

    # Get the OLDEST available item instance for this selection
    instance_path = product_fs_utils.get_oldest_available_item_instance(selected_city, selected_area, selected_item_type, size_name)

    if not instance_path:
        logger.warning(f"No instance available for {selected_city}/{selected_area}/{selected_item_type}/{size_name} for user {user_id}.")
        error_text = "This specific item/size is currently out of stock or an error occurred. Please try a different selection."
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("⬅️ Back to Size Selection", callback_data=f"select_type_{selected_item_type}"))
        send_or_edit_message(bot_instance, chat_id, error_text, reply_markup=markup, existing_message_id=existing_message_id, parse_mode="MarkdownV2")
        bot_instance.answer_callback_query(call.id, "Item out of stock.")
        return

    item_details_fs = product_fs_utils.get_item_instance_details(instance_path)
    if not item_details_fs or item_details_fs.get('price', 0.0) <= 0: # Price should be positive
        logger.warning(f"Details (especially price) missing or invalid for instance {instance_path}, user {user_id}.")
        error_text = "Details for this item could not be loaded or are invalid (e.g. price). It might be temporarily unavailable."
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("⬅️ Back to Size Selection", callback_data=f"select_type_{selected_item_type}"))
        send_or_edit_message(bot_instance, chat_id, error_text, reply_markup=markup, existing_message_id=existing_message_id, parse_mode="MarkdownV2")
        bot_instance.answer_callback_query(call.id, "Item details error.")
        return

    # Store details for payment step
    update_user_state(user_id, 'buy_selected_instance_path', instance_path)
    update_user_state(user_id, 'buy_selected_item_name_display', f"{selected_item_type} ({size_name})") # For display
    update_user_state(user_id, 'buy_selected_item_price', item_details_fs['price'])
    update_user_state(user_id, 'buy_selected_item_description', item_details_fs['description'])
    update_user_state(user_id, 'buy_selected_item_image_paths', item_details_fs['image_paths'])


    user_data = get_or_create_user(user_id)
    item_price = Decimal(str(item_details_fs['price']))
    try:
        service_fee = Decimal(str(config.SERVICE_FEE_EUR))
    except (AttributeError, ValueError, TypeError):
        logger.critical(f"SERVICE_FEE_EUR ('{getattr(config, 'SERVICE_FEE_EUR', 'NOT SET')}') is not a valid Decimal. Defaulting to 0.0.")
        service_fee = Decimal('0.0')

    total_cost = item_price + service_fee
    user_balance = Decimal(str(user_data['balance'])) if user_data and 'balance' in user_data else Decimal('0.0')

    # --- Purchase with balance logic ---
    if user_balance >= total_cost:
        logger.info(f"User {user_id} purchasing item from {instance_path} entirely with balance. Total: {total_cost}, Balance: {user_balance}")
        update_user_state(user_id, 'current_flow', 'buy_processing_balance_payment')
        new_balance = user_balance - total_cost
        update_user_balance(user_id, float(new_balance), increment_transactions=True)

        move_success = product_fs_utils.move_item_instance_to_purchased(instance_path, user_id)

        transaction_item_details_json = json.dumps({
            'city': selected_city, 'area': selected_area, 'type': selected_item_type,
            'size': size_name, 'price': float(item_price), 'instance_path_original': instance_path
        })
        record_transaction(
            user_id=user_id, # Removed product_id=None
            item_details_json=transaction_item_details_json, # Using new field
            type='purchase_balance', eur_amount=float(total_cost),
            payment_status='completed' if move_success else 'completed_fs_move_error',
            notes=f"Paid from balance. Instance: {os.path.basename(instance_path)}. FS Move: {'OK' if move_success else 'FAIL'}"
        )

        if not move_success:
             logger.error(f"Filesystem move FAILED for instance {instance_path} (User: {user_id}). Payment processed from balance.")
             # Critical error, user paid but item not moved.
             bot_instance.send_message(chat_id, "Purchase processed, but there was a CRITICAL error with item delivery. Please contact support with your User ID and this message.", parse_mode="MarkdownV2")
             # Fall through to clear state and go to main menu, but support is needed.

        item_name_display_escaped = escape_md(f"{selected_item_type} ({size_name})") # Use escape_md
        city_escaped = escape_md(selected_city) # Use escape_md
        full_desc_escaped = escape_md(item_details_fs['description']) # Use escape_md

        success_message = (f"🎉 Your purchase of *{item_name_display_escaped}* in *{city_escaped}* is complete, paid with balance\\!\n\n"
                           f"*Item Details:*\n{full_desc_escaped}")

        markup_main_menu = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        # Message Management: Update previous message or send new if not possible
        send_or_edit_message(bot_instance, chat_id, success_message, reply_markup=markup_main_menu, existing_message_id=existing_message_id, parse_mode="MarkdownV2")

        # Send delivery images if any
        fs_image_paths = item_details_fs.get('image_paths', [])
        # As per spec, item delivery details are generic, but images can be sent.
        # The spec says "sends item delivery message with generic details ... and item images (if any)"
        # The success_message above includes the item's actual description.
        # We will just send the images here if available.
        if fs_image_paths:
            # Send up to 3 images as specified
            media_group = []
            for img_path in fs_image_paths[:3]: # Max 3 images
                if os.path.exists(img_path):
                    try:
                        with open(img_path, 'rb') as photo_file:
                            # For single photo with caption, or media group for multiple
                            if len(fs_image_paths) == 1:
                                bot_instance.send_photo(chat_id, photo=photo_file, caption="Your purchased item:")
                                break
                            media_group.append(types.InputMediaPhoto(media=photo_file.read()))
                    except Exception as e_photo_open:
                        logger.error(f"Error opening/reading image {img_path} for delivery: {e_photo_open}")

            if media_group:
                try:
                    bot_instance.send_media_group(chat_id, media=media_group)
                except Exception as e_media_group:
                    logger.error(f"Error sending media group for delivery: {e_media_group}")
                    # Fallback to sending one by one if group fails, or just the first one.
                    if os.path.exists(fs_image_paths[0]):
                         with open(fs_image_paths[0], 'rb') as pf:
                            bot_instance.send_photo(chat_id, pf, caption="Your purchased item:")


        clear_user_state(user_id) # Important: clear state after successful purchase
        # update_user_state(user_id, 'last_bot_message_id', sent_msg.message_id) # Already handled by send_or_edit
        bot_instance.answer_callback_query(call.id, "Purchase successful!")
        # Return to main menu is handled by the "Back to Main Menu" button in the success message.
        return

    # --- External Payment Logic ---
    paid_from_balance = Decimal('0.0')
    amount_to_pay_externally = total_cost

    if user_balance > Decimal('0.0'):
        paid_from_balance = min(user_balance, total_cost)
        amount_to_pay_externally = total_cost - paid_from_balance

    if amount_to_pay_externally < Decimal('0.0'): amount_to_pay_externally = Decimal('0.0')


    if amount_to_pay_externally == Decimal('0.0') and total_cost > Decimal('0.0') : # Should not happen if logic above is correct
        logger.error(f"LOGIC ERROR: amount_to_pay_externally is 0 but balance was less than total_cost. User: {user_id}, Balance: {user_balance}, Total: {total_cost}")
        bot_instance.send_message(chat_id, "There was an issue calculating payment. Please try again or contact support.")
        clear_user_state(user_id) # Clear potentially corrupted state
        bot_instance.answer_callback_query(call.id, "Calculation error.")
        return

    update_user_state(user_id, 'buy_amount_due_eur', float(amount_to_pay_externally))
    update_user_state(user_id, 'buy_paid_from_balance', float(paid_from_balance))
    update_user_state(user_id, 'buy_total_cost_eur', float(total_cost))
    update_user_state(user_id, 'current_flow', 'buy_awaiting_payment_method')

    # Retrieve stored item details from user_state
    item_name_display = get_user_state(user_id, 'buy_selected_item_name_display', "Item")
    item_price_from_state = get_user_state(user_id, 'buy_selected_item_price', float(item_price)) # Fallback to calculated
    item_description_from_state = get_user_state(user_id, 'buy_selected_item_description', "N/A")
    item_image_paths_from_state = get_user_state(user_id, 'buy_selected_item_image_paths', [])
    selected_city = get_user_state(user_id, 'buy_selected_city') # For back button
    selected_area = get_user_state(user_id, 'buy_selected_area')
    selected_item_type = get_user_state(user_id, 'buy_selected_item_type')


    logger.info(f"User {user_id} proceeding to crypto payment for item '{item_name_display}'. Amount due: {amount_to_pay_externally}, Paid from balance: {paid_from_balance}")

    item_name_escaped = escape_md(item_name_display) # Use escape_md
    description_raw = item_description_from_state
    max_desc_len_caption = 600 # As per spec, item info for payment selection
    if len(description_raw) > max_desc_len_caption:
        description_raw = description_raw[:max_desc_len_caption] + "..."
    description_escaped = escape_md(description_raw) # Use escape_md

    # Format numbers for display (no escaping here, send_or_edit_message will handle it)
    paid_from_balance_str = f"{paid_from_balance_float:.2f}"
    amount_due_eur_str = f"{amount_due_eur_float:.2f}"
    # expected_crypto_amount_str = f"{expected_crypto_amount_decimal_hr}" # This variable is not defined here

    # Escape parts that are not numbers/addresses/already escaped strings
    item_name_display_escaped = escape_md(item_name_display) # Use escape_md
    # display_coin_symbol_escaped = escape_md(display_coin_symbol) # This variable is not defined here
    # network_for_db_escaped = escape_md(network_for_db) # This variable is not defined here
    # unique_address_escaped = escape_md(unique_address) # This variable is not defined here
    # expires_at_formatted_escaped = escape_md(expires_at_dt.strftime('%Y-%m-%d %H:%M:%S UTC')) # This variable is not defined here
    # final_sentence_escaped = escape_md("Send the exact amount using the correct network. This address is for single use only.") # This variable is not defined here


    price_info_parts = [
        f"Item: *{item_name_display_escaped}*",
        f"Original Price: *{f'{Decimal(str(item_price_from_state)):.2f}'}* EUR", # Corrected to use item_price_from_state
        f"Service Fee: *{f'{service_fee:.2f}'}* EUR", # Corrected to use service_fee
        f"Total Cost: *{f'{total_cost:.2f}'}* EUR", # Corrected to use total_cost
    ]


    if paid_from_balance_float > 0:
      price_info_parts.append(f"Paid from balance: *{paid_from_balance_str}* EUR")
    price_info_parts.append(f"Amount Due: *{amount_due_eur_str}* EUR")

    price_info_text = "\n".join(price_info_parts)

    final_caption = f"{price_info_text}\n\n*Item Info:*\n{description_escaped}\n\nPlease select a payment method:"
    if len(final_caption) > 1024: # Telegram caption limit
        final_caption = final_caption[:1021] + "..."

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🪙 USDT (TRC20)", callback_data="pay_buy_USDT"))
    markup.add(types.InlineKeyboardButton("🪙 BTC (Bitcoin)", callback_data="pay_buy_BTC"))
    markup.add(types.InlineKeyboardButton("🪙 LTC (Litecoin)", callback_data="pay_buy_LTC"))
    # Back button should return to item list for the selected city (which is size selection)
    # The callback for size selection was `select_size_{size_name}`
    # The previous step was `select_type_{item_type_name}`
    markup.add(types.InlineKeyboardButton("⬅️ Back to Size Selection", callback_data=f"select_type_{selected_item_type}"))
    # markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")) # Main menu is too far back usually

    sent_message_id_val = None
    # Use image paths from state, up to 3 as per spec
    # For this payment selection screen, spec says "Displays item image(s) (up to 3) if available"

    # For simplicity, let's try to send a new message with the first image if available,
    # or edit if the current message is already a photo.
    # More complex logic for multiple images (media group) can be added if needed here,
    # but spec implies one main display for this screen.

    first_image_path = item_image_paths_from_state[0] if item_image_paths_from_state and os.path.exists(item_image_paths_from_state[0]) else None
    current_msg_is_photo = call.message.content_type == 'photo' if call.message else False


    if first_image_path:
        # If current message is a photo and it's the one we want to show, edit its caption
        if current_msg_is_photo and existing_message_id == call.message.message_id:
            try:
                bot_instance.edit_message_caption(caption=final_caption, chat_id=chat_id, message_id=existing_message_id, reply_markup=markup, parse_mode="MarkdownV2")
                sent_message_id_val = existing_message_id
            except Exception as e_caption: # If edit fails (e.g. not a photo originally), delete and resend
                logger.warning(f"Failed to edit photo caption, will resend: {e_caption}")
                if existing_message_id: delete_message(bot_instance, chat_id, existing_message_id)
                with open(first_image_path, 'rb') as photo_file:
                    new_msg = bot_instance.send_photo(chat_id, photo=photo_file, caption=final_caption, reply_markup=markup, parse_mode="MarkdownV2")
                    sent_message_id_val = new_msg.message_id
        else: # Current message is not a photo or not the one we want, so send new photo message
            if existing_message_id: delete_message(bot_instance, chat_id, existing_message_id)
            with open(first_image_path, 'rb') as photo_file:
                new_msg = bot_instance.send_photo(chat_id, photo=photo_file, caption=final_caption, reply_markup=markup, parse_mode="MarkdownV2")
                sent_message_id_val = new_msg.message_id
    else: # No image, send/edit text message
        if current_msg_is_photo and existing_message_id: # If previous was photo, delete it
             delete_message(bot_instance, chat_id, existing_message_id)
             existing_message_id = None # Force send_or_edit to send new text message

        sent_message_id_val = send_or_edit_message(
            bot_instance, chat_id, final_caption,
            reply_markup=markup,
            existing_message_id=existing_message_id, # Will be None if photo deleted
            parse_mode="MarkdownV2"
        )

    if sent_message_id_val:
        update_user_state(user_id, 'last_bot_message_id', sent_message_id_val)

    bot_instance.answer_callback_query(call.id)


def handle_pay_buy_crypto_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    original_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id
    ack_msg = None

    try:
        crypto_currency = call.data.split('pay_buy_')[1]
        if crypto_currency.upper() not in ["USDT", "BTC", "LTC"]: raise IndexError("Invalid crypto")
        logger.info(f"User {user_id} selected crypto {crypto_currency} for buying item.")
    except IndexError:
        logger.warning(f"Invalid callback data for pay_buy: {call.data} by user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error processing your selection.", show_alert=True)
        return

    # Retrieve necessary info from user_state
    selected_instance_path = get_user_state(user_id, 'buy_selected_instance_path')
    item_name_display = get_user_state(user_id, 'buy_selected_item_name_display', "Item")
    amount_due_eur_float = get_user_state(user_id, 'buy_amount_due_eur')
    paid_from_balance_float = get_user_state(user_id, 'buy_paid_from_balance', 0.0)
    total_cost_eur_float = get_user_state(user_id, 'buy_total_cost_eur')
    # For "Back" button on invoice:
    selected_city = get_user_state(user_id, 'buy_selected_city')
    selected_area = get_user_state(user_id, 'buy_selected_area')
    selected_item_type = get_user_state(user_id, 'buy_selected_item_type')
    selected_size = get_user_state(user_id, 'buy_selected_size')
    item_price_from_state = get_user_state(user_id, 'buy_selected_item_price') # Added to retrieve item price for display


    if not all([selected_instance_path, item_name_display is not None, amount_due_eur_float is not None,
                total_cost_eur_float is not None, selected_city, selected_area, selected_item_type, selected_size, item_price_from_state is not None]):
        logger.warning(f"Missing session data for pay_buy_crypto for user {user_id}.")
        error_text = "Your session seems to have expired or critical information is missing. Please restart the purchase."
        markup_error = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
        send_or_edit_message(bot_instance, chat_id, error_text, reply_markup=markup_error, existing_message_id=original_message_id, parse_mode="MarkdownV2")
        clear_user_state(user_id)
        bot_instance.answer_callback_query(call.id, "Session error. Please restart.", show_alert=True)
        return

    bot_instance.answer_callback_query(call.id)
    # "acknowledgment" message
    # This should edit the current message (which is the item detail/crypto selection screen)
    ack_msg = send_or_edit_message(bot_instance, chat_id, "⏳ Generating your payment address...",
                                   existing_message_id=original_message_id, reply_markup=None)
    current_message_id_for_invoice = ack_msg.message_id if ack_msg else original_message_id


    transaction_item_details_json = json.dumps({
        'city': selected_city, 'area': selected_area, 'type': selected_item_type,
        'size': selected_size, 'price': item_price_from_state, # Original item price
        'instance_path_original': selected_instance_path
    })
    transaction_notes = (f"User buying '{item_name_display}'. "
                         f"Total: {total_cost_eur_float:.2f} EUR. Paid from balance: {paid_from_balance_float:.2f} EUR. "
                         f"Due via {crypto_currency}: {amount_due_eur_float:.2f} EUR.")

    main_transaction_id = record_transaction(
        user_id=user_id, # Removed product_id=None
        item_details_json=transaction_item_details_json,
        type='purchase_crypto', eur_amount=total_cost_eur_float, # This is the total value of the transaction
        payment_status='pending_address_generation', notes=transaction_notes,
        charge_id=None
    )
    if not main_transaction_id:
        logger.error(f"Failed to create transaction record for user {user_id}, item '{item_name_display}'.")
        send_or_edit_message(bot_instance, chat_id, "Database error creating transaction. Please try again.", existing_message_id=current_message_id_for_invoice)
        return
    update_user_state(user_id, 'buy_transaction_id', main_transaction_id)

    coin_symbol_for_hd_wallet = crypto_currency
    display_coin_symbol = crypto_currency
    network_for_db = crypto_currency # Default for BTC, LTC

    if crypto_currency == "USDT":
        coin_symbol_for_hd_wallet = "TRX" # USDT (TRC20) uses TRX addresses
        network_for_db = "TRC20 (Tron)"
        # display_coin_symbol remains "USDT"

    try:
        next_idx = get_next_address_index(coin_symbol_for_hd_wallet)
    except Exception as e_idx:
        logger.exception(f"HD Wallet: Error getting next address index for {coin_symbol_for_hd_wallet} (user {user_id}, tx {main_transaction_id}): {e_idx}")
        send_or_edit_message(bot_instance, chat_id, "Error generating payment address (index). Please try again later or contact support.", existing_message_id=current_message_id_for_invoice)
        update_transaction_status(main_transaction_id, 'error_address_generation')
        return

    unique_address = hd_wallet_utils.generate_address(coin_symbol_for_hd_wallet, next_idx)
    if not unique_address:
        logger.error(f"HD Wallet: Failed to generate address for {coin_symbol_for_hd_wallet}, index {next_idx} (user {user_id}, tx {main_transaction_id}).")
        send_or_edit_message(bot_instance, chat_id, "Error generating payment address (HD). Please try again later or contact support.", existing_message_id=current_message_id_for_invoice)
        update_transaction_status(main_transaction_id, 'error_address_generation')
        return

    rate = exchange_rate_utils.get_current_exchange_rate("EUR", display_coin_symbol)
    if not rate:
        logger.error(f"HD Wallet: Could not get exchange rate for EUR to {display_coin_symbol} (user {user_id}, tx {main_transaction_id}).")
        send_or_edit_message(bot_instance, chat_id, f"Could not retrieve exchange rate for {escape_md(display_coin_symbol)}. Please try again or contact support.", existing_message_id=current_message_id_for_invoice, parse_mode='MarkdownV2')
        update_transaction_status(main_transaction_id, 'error_exchange_rate')
        return

    precision_map = {"BTC": 8, "LTC": 8, "USDT": 6} # TODO: Move to config or coin_utils
    num_decimals = precision_map.get(display_coin_symbol, 8)
    amount_due_eur_decimal = Decimal(str(amount_due_eur_float))
    expected_crypto_amount_decimal_hr = (amount_due_eur_decimal / rate).quantize(Decimal('1e-' + str(num_decimals)), rounding=ROUND_UP)
    smallest_unit_multiplier = Decimal('1e-' + str(num_decimals))
    expected_crypto_amount_smallest_unit_str = str(int(expected_crypto_amount_decimal_hr * smallest_unit_multiplier))

    payment_window_minutes = getattr(config, 'PAYMENT_WINDOW_MINUTES', 60)
    expires_at_dt = datetime.datetime.utcnow() + datetime.timedelta(minutes=payment_window_minutes)

    update_success = update_main_transaction_for_hd_payment(
       main_transaction_id,
       status='awaiting_payment',
       crypto_amount=str(expected_crypto_amount_decimal_hr), # Store human-readable for now
       currency=display_coin_symbol
    )
    if not update_success:
        logger.error(f"HD Wallet: Failed to update main transaction {main_transaction_id} for user {user_id} (buy flow).")
        send_or_edit_message(bot_instance, chat_id, "Database error updating transaction. Please try again.", existing_message_id=current_message_id_for_invoice)
        return

    db_coin_symbol_for_pending = "USDT_TRX" if crypto_currency == "USDT" else display_coin_symbol
    pending_payment_id = create_pending_payment(
       transaction_id=main_transaction_id,
       user_id=user_id,
       address=unique_address,
       coin_symbol=db_coin_symbol_for_pending,
       network=network_for_db,
       expected_crypto_amount=expected_crypto_amount_smallest_unit_str,
       expires_at=expires_at_dt,
       paid_from_balance_eur=paid_from_balance_float
    )
    if not pending_payment_id:
       logger.error(f"HD Wallet: Failed to create pending_crypto_payment for main_tx {main_transaction_id} (user {user_id}, buy flow).")
       update_transaction_status(main_transaction_id, 'error_creating_pending_payment')
       send_or_edit_message(bot_instance, chat_id, "Error preparing payment record. Please try again or contact support.", existing_message_id=current_message_id_for_invoice)
       return

    qr_code_path = None
    try:
        qr_code_path = hd_wallet_utils.generate_qr_code_for_address(
           unique_address,
           str(expected_crypto_amount_decimal_hr),
           display_coin_symbol
        )
    except Exception as e_qr_gen:
        logger.error(f"HD Wallet (buy): QR code generation failed for {unique_address} (user {user_id}, tx {main_transaction_id}): {e_qr_gen}")

    # Format numbers for display (no escaping here, send_or_edit_message will handle it)
    paid_from_balance_str = f"{paid_from_balance_float:.2f}"
    amount_due_eur_str = f"{amount_due_eur_float:.2f}"
    expected_crypto_amount_str = f"{expected_crypto_amount_decimal_hr}" # Keep as Decimal string for display

    # Escape parts that are not numbers/addresses/already escaped strings
    item_name_display_escaped = escape_md(item_name_display) # Use escape_md
    display_coin_symbol_escaped = escape_md(display_coin_symbol) # Use escape_md
    network_for_db_escaped = escape_md(network_for_db) # Use escape_md
    unique_address_escaped = escape_md(unique_address) # Use escape_md
    expires_at_formatted_escaped = escape_md(expires_at_dt.strftime('%Y-%m-%d %H:%M:%S UTC')) # Use escape_md
    final_sentence_escaped = escape_md("Send the exact amount using the correct network. This address is for single use only.") # Use escape_md


    price_info_parts = [
        f"Item: *{item_name_display_escaped}*",
        f"Original Price: *{f'{Decimal(str(item_price_from_state)):.2f}'}* EUR", # Corrected to use item_price_from_state
        f"Service Fee: *{f'{service_fee:.2f}'}* EUR", # Corrected to use service_fee
        f"Total Cost: *{f'{total_cost:.2f}'}* EUR", # Corrected to use total_cost
    ]


    if paid_from_balance_float > 0:
      price_info_parts.append(f"Paid from balance: *{paid_from_balance_str}* EUR")
    price_info_parts.append(f"Amount Due: *{amount_due_eur_str}* EUR")

    price_info_text = "\n".join(price_info_parts)

    invoice_text = (
        f"Invoice for your purchase of *{item_name_display_escaped}*:\n\n"
        f"{price_info_text}\n\n"
        f"Payment Method: *{display_coin_symbol_escaped}* ({network_for_db_escaped})\n"
        f"Amount: *{expected_crypto_amount_str}* {display_coin_symbol_escaped}\n"
        f"Address: `{unique_address_escaped}`\n\n"
        f"Expires At: *{expires_at_formatted_escaped}*\n\n"
        f"{final_sentence_escaped}"
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(f"✅ I have sent {display_coin_symbol}", callback_data=f"check_buy_payment_{main_transaction_id}"))
    markup.add(types.InlineKeyboardButton("❌ Cancel Payment", callback_data=f"cancel_buy_payment_{main_transaction_id}"))
    # Back button should return to the payment method selection screen for this item
    # The callback for payment method selection was `select_size_{size_name}`
    markup.add(types.InlineKeyboardButton("⬅️ Try Different Payment Method", callback_data=f"select_size_{selected_size}"))


    sent_message_id_val = None
    if qr_code_path and os.path.exists(qr_code_path):
        try:
            with open(qr_code_path, 'rb') as photo_file:
                # Delete the "Generating address..." message before sending the invoice with QR
                if current_message_id_for_invoice:
                    try: delete_message(bot_instance, chat_id, current_message_id_for_invoice)
                    except Exception as e_del: logger.warning(f"Could not delete 'Generating address...' message {current_message_id_for_invoice}: {e_del}")

                sent_message = bot_instance.send_photo(
                    chat_id,
                    photo=photo_file,
                    caption=invoice_text,
                    reply_markup=markup,
                    parse_mode="MarkdownV2"
                )
                sent_message_id_val = sent_message.message_id
        except Exception as e_photo:
            logger.error(f"Error sending QR code photo for user {user_id}, tx {main_transaction_id}: {e_photo}. Sending text invoice instead.")
            # Fallback to sending text invoice if photo fails
            sent_message = send_or_edit_message(
                bot_instance, chat_id, invoice_text,
                reply_markup=markup,
                existing_message_id=current_message_id_for_invoice, # Try to edit the ack message
                parse_mode="MarkdownV2"
            )
            if sent_message: sent_message_id_val = sent_message.message_id
    else:
        logger.warning(f"QR code path invalid or file not found for user {user_id}, tx {main_transaction_id}. Sending text invoice.")
        # Send text invoice if QR code generation failed
        sent_message = send_or_edit_message(
            bot_instance, chat_id, invoice_text,
            reply_markup=markup,
            existing_message_id=current_message_id_for_invoice, # Try to edit the ack message
            parse_mode="MarkdownV2"
        )
        if sent_message: sent_message_id_val = sent_message.message_id

    if sent_message_id_val:
        update_user_state(user_id, 'last_bot_message_id', sent_message_id_val)
        update_user_state(user_id, 'current_flow', f'buy_awaiting_payment_{main_transaction_id}') # Update flow state to await payment for this tx

    # No answer_callback_query here, it was done earlier after initial validation.


def handle_buy_check_payment_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    original_invoice_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id
    logger.info(f"User {user_id} checking buy payment status for callback: {call.data}")
    # bot_instance is already passed as an argument, no need to reassign from a global 'bot'

    try:
        transaction_id_str = call.data.split('check_buy_payment_')[1]
        transaction_id = int(transaction_id_str)
    except (IndexError, ValueError):
        logger.warning(f"Invalid transaction ID in callback data for check_buy_payment: {call.data} for user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error: Invalid transaction reference.", show_alert=True)
        return

    pending_payment_record = get_pending_payment_by_transaction_id(transaction_id)

    if not pending_payment_record:
        main_tx = get_transaction_by_id(transaction_id) # Use get_transaction_by_id
        status_msg = "Payment record not found or already processed."
        if main_tx: status_msg = f"Payment status: {escape_md(main_tx['payment_status'])}." # Use escape_md
        bot_instance.answer_callback_query(call.id, status_msg, show_alert=True)
        if main_tx and main_tx['payment_status'] in ['completed', 'cancelled_by_user', 'expired_payment_window', 'error_finalizing_data', 'completed_fs_move_error', 'completed_item_data_error', 'completed_fulfillment_error', 'error_finalizing_db', 'error_finalizing_user_data', 'error_finalizing_balance_update', 'error_finalizing_unexpected']: # Terminal states
            new_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            if original_invoice_message_id and call.message.message_id == original_invoice_message_id:
                try:
                    if call.message.photo: bot_instance.edit_message_caption(caption=status_msg, chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=new_markup, parse_mode="MarkdownV2")
                    else: bot_instance.edit_message_text(text=status_msg, chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=new_markup, parse_mode="MarkdownV2")
                except: pass # Best effort
        return

    bot_instance.answer_callback_query(call.id)
    ack_msg = bot_instance.send_message(chat_id, "⏳ Checking payment status (on-demand)...")

    try:
        newly_confirmed, status_info = payment_monitor.check_specific_pending_payment(transaction_id)
        if ack_msg: delete_message(bot_instance, chat_id, ack_msg.message_id)

        if newly_confirmed and status_info == 'confirmed_unprocessed':
            logger.info(f"On-demand check for buy tx {transaction_id} (user {user_id}) resulted in new confirmation. Processing...")
            bot_instance.send_message(chat_id, "✅ Payment detected! Processing your purchase...")
            payment_monitor.process_confirmed_payments(bot_instance) # This will call finalize
        else:
            logger.info(f"On-demand check for buy tx {transaction_id} (user {user_id}): newly_confirmed={newly_confirmed}, status_info='{status_info}'")
            pending_payment_latest = get_pending_payment_by_transaction_id(transaction_id) # Refresh data

            current_invoice_text = call.message.caption if call.message.photo else call.message.text
            base_invoice_text = "\n".join([line for line in (current_invoice_text or "").split('\n') if not line.strip().startswith("Status:")])

            new_status_line = ""
            alert_message = ""
            show_alert_flag = True
            reply_markup_to_use = call.message.reply_markup # Keep existing buttons by default

            # For "Try Different Payment Method" button, we need the item context
            # This would go back to the size selection screen where payment options are shown.
            selected_size_for_back = get_user_state(user_id, 'buy_selected_size')


            if status_info == 'monitoring':
                confs = pending_payment_latest['confirmations'] if pending_payment_latest else 'N/A'
                new_status_line = f"Status: Still monitoring for sufficient confirmations. Current: {confs}."
                alert_message = f"Still monitoring. Confirmations: {confs}."
                show_alert_flag = False
            elif status_info == 'monitoring_updated':
                confs = pending_payment_latest['confirmations'] if pending_payment_latest else 'N/A'
                new_status_line = f"Status: Monitoring updated. Current confirmations: {confs}."
                alert_message = f"Monitoring updated. Confirmations: {confs}."
                show_alert_flag = False
            elif status_info == 'expired':
                new_status_line = f"Status: This payment request has expired."
                alert_message = "This payment request has expired."
                new_markup = types.InlineKeyboardMarkup(row_width=1)
                if selected_size_for_back: # If we have size, we can go back to that item's payment options
                    new_markup.add(types.InlineKeyboardButton("⬅️ Try Different Payment Method", callback_data=f"select_size_{selected_size_for_back}"))
                new_markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
                reply_markup_to_use = new_markup
            elif status_info == 'error_api':
                new_status_line = f"Status: Could not check status due to a temporary API error. Please try again in a moment."
                alert_message = "Could not check status due to an API error. Please try again."
            elif status_info == 'not_found':
                new_status_line = f"Status: Payment record not found."
                alert_message = "Payment record not found. This is unexpected."
                reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            elif status_info in ['processed', 'cancelled_by_user', 'error_finalizing', 'error_finalizing_data', 'error_monitoring_unsupported', 'error_processing_tx_missing', 'processed_tx_already_complete', 'completed', 'completed_fs_move_error', 'completed_item_data_error', 'completed_fulfillment_error', 'error_finalizing_db', 'error_finalizing_user_data', 'error_finalizing_balance_update', 'error_finalizing_unexpected']: # Terminal states
                new_status_line = f"Status: Payment is in a final state: {escape_md(status_info)}." # Use escape_md
                alert_message = f"Payment status: {status_info}."
                reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            else:
                new_status_line = f"Status: Current status: {escape_md(status_info)}." # Use escape_md
                alert_message = f"Current status: {status_info}."
                if status_info != 'monitoring': # Non-monitoring, non-expired usually means terminal or error
                     reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

            updated_text_for_invoice = f"{new_status_line}\n\n{base_invoice_text}".strip()
            if len(updated_text_for_invoice) > (1024 if call.message.photo else 4096): # Truncate
                updated_text_for_invoice = updated_text_for_invoice[:(1021 if call.message.photo else 4093)] + "..."

            try:
                if call.message.photo:
                    bot_instance.edit_message_caption(caption=updated_text_for_invoice, chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=reply_markup_to_use, parse_mode="MarkdownV2")
                else:
                    bot_instance.edit_message_text(text=updated_text_for_invoice, chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=reply_markup_to_use, parse_mode="MarkdownV2")
                bot_instance.answer_callback_query(call.id, alert_message, show_alert=show_alert_flag)
            except Exception as e_edit:
                logger.error(f"Error editing message {original_invoice_message_id} for on-demand buy check (tx {transaction_id}): {e_edit}")
                bot_instance.answer_callback_query(call.id, "Status updated, but message display failed to refresh.", show_alert=True)

    except Exception as e:
        logger.exception(f"Error in handle_check_buy_payment_callback (on-demand) for user {user_id}, tx {transaction_id_str}: {e}")
        if ack_msg: delete_message(bot_instance, chat_id, ack_msg.message_id)
        bot_instance.answer_callback_query(call.id, "An error occurred while checking payment status. Please try again.", show_alert=True)


def handle_cancel_buy_payment_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    original_invoice_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id
    logger.info(f"User {user_id} initiated cancel for buy payment: {call.data}")
    # bot_instance is already passed as an argument

    try:
        transaction_id_str = call.data.split('cancel_buy_payment_')[1]
        transaction_id = int(transaction_id_str)
    except (IndexError, ValueError):
        logger.warning(f"Invalid transaction ID in cancel_buy_payment callback: {call.data} for user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error: Invalid transaction reference.", show_alert=True)
        return

    user_cancel_message = "Payment process cancelled by user."

    pending_payment = get_pending_payment_by_transaction_id(transaction_id)
    if pending_payment:
        if pending_payment['status'] == 'monitoring':
            if update_pending_payment_status(pending_payment['payment_id'], 'user_cancelled'):
                logger.info(f"HD Pending Payment {pending_payment['payment_id']} for buy TX_ID {transaction_id} marked as user_cancelled.")
                user_cancel_message = "Payment (HD Wallet) successfully cancelled."
            else:
                logger.error(f"Failed to update HD Pending Payment {pending_payment['payment_id']} for buy TX_ID {transaction_id} status to user_cancelled.")
                user_cancel_message = "Payment cancellation processed, but there was an issue updating pending record."
        else:
            logger.info(f"HD Pending Payment {pending_payment['payment_id']} for buy TX_ID {transaction_id} was not 'monitoring' (was {pending_payment['status']}). Main transaction will be cancelled.")
            user_cancel_message = f"Payment already in state '{pending_payment['status']}'. Marked as cancelled by you."
    else:
        logger.warning(f"No HD pending payment record found for buy TX_ID {transaction_id} upon cancellation. Main transaction will be marked cancelled.")

    if transaction_id:
        update_transaction_status(transaction_id, 'cancelled_by_user')
    else:
        logger.error(f"Cancel buy callback with no valid transaction_id from data: {call.data}")

    if original_invoice_message_id:
        try:
            delete_message(bot_instance, chat_id, original_invoice_message_id)
        except Exception as e_del:
            logger.error(f"Error deleting invoice message {original_invoice_message_id} on cancel for user {user_id}, buy tx {transaction_id}: {e_del}")

    user_cancel_message_final = user_cancel_message + "\nReturning to the main menu."
    markup_main_menu = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

    final_msg_sent = None
    try:
        final_msg_sent = bot_instance.send_message(chat_id, user_cancel_message_final, reply_markup=markup_main_menu, parse_mode="MarkdownV2")
    except Exception as e_send:
        logger.error(f"Error sending cancel confirmation (Markdown) for buy tx {transaction_id}: {e_send}. Sending plain text.")
        final_msg_sent = bot_instance.send_message(chat_id, user_cancel_message_final.replace('*','').replace('_','').replace('`','').replace('[','').replace(']','').replace('(','').replace(')',''), reply_markup=markup_main_menu)

    bot_instance.answer_callback_query(call.id, "Payment Cancelled.")

    clear_user_state(user_id)
    # Send a new main menu message, don't try to edit the (possibly deleted) invoice.
    welcome_text, markup = get_main_menu_text_and_markup()
    new_main_menu_msg = send_or_edit_message(
        bot_instance, chat_id, welcome_text,
        reply_markup=markup,
        existing_message_id=None, # Send a new message
        parse_mode=None
    )
    if new_main_menu_msg and hasattr(new_main_menu_msg, 'message_id'):
        update_user_state(user_id, 'last_bot_message_id', new_main_menu_msg.message_id)


# --- Payment Finalization Function (called by payment_monitor) ---
# This function needs to be updated to use item details from the transaction record (item_details_json)
# and product_fs_utils for moving the item.
def finalize_successful_crypto_purchase(bot_instance, main_transaction_id: int, user_id: int,
                                        # product_id: int, # No longer using product_id directly from DB
                                        paid_from_balance_eur_str: str,
                                        received_crypto_amount_str: str,
                                        coin_symbol: str,
                                        blockchain_tx_id: str
                                        ) -> bool:
    logger.info(f"Finalizing successful crypto purchase for user {user_id}, main_tx_id {main_transaction_id}.")
    chat_id = user_id # Assuming direct message to user

    transaction_details = get_transaction_by_id(main_transaction_id)
    if not transaction_details:
        logger.error(f"finalize_successful_crypto_purchase: CRITICAL - Main transaction {main_transaction_id} not found.")
        # Cannot notify user as we don't have chat_id if user_id is not chat_id
        return False # Critical error

    item_details_json_str = transaction_details.get('item_details_json')
    if not item_details_json_str:
        logger.error(f"finalize_successful_crypto_purchase: CRITICAL - item_details_json missing for tx {main_transaction_id}.")
        bot_instance.send_message(chat_id, f"Payment confirmed for TXID {main_transaction_id}, but there was a CRITICAL error fetching item details for delivery. Please contact support immediately.")
        update_transaction_status(main_transaction_id, 'completed_item_data_error')
        return False

    try:
        item_purchase_info = json.loads(item_details_json_str)
        original_instance_path = item_purchase_info.get('instance_path_original')
        item_display_name = f"{item_purchase_info.get('type','Item')} ({item_purchase_info.get('size','N/A')})" # For messages
    except json.JSONDecodeError as e_json:
        logger.error(f"finalize_successful_crypto_purchase: CRITICAL - Failed to parse item_details_json for tx {main_transaction_id}: {e_json}")
        bot_instance.send_message(chat_id, f"Payment confirmed for TXID {main_transaction_id}, but item data for delivery is corrupted. Please contact support.")
        update_transaction_status(main_transaction_id, 'completed_item_data_error')
        return False

    if not original_instance_path:
        logger.critical(f"finalize_successful_crypto_purchase: CRITICAL - Original instance path missing in parsed item_details_json for tx {main_transaction_id}.")
        bot_instance.send_message(chat_id, f"Payment confirmed for {escape_md(item_display_name)}, TXID {main_transaction_id}. However, the item instance path is missing. Please contact support.") # Use escape_md
        update_transaction_status(main_transaction_id, 'completed_fulfillment_error')
        return False

    try:
        paid_from_balance_eur = Decimal(paid_from_balance_eur_str)
    except Exception as e_conv:
        logger.error(f"finalize_successful_crypto_purchase: Invalid paid_from_balance_eur_str '{paid_from_balance_eur_str}' for tx {main_transaction_id}. Error: {e_conv}")
        update_transaction_status(main_transaction_id, 'error_finalizing_data') # This status might need to be specific
        bot_instance.send_message(chat_id, f"Payment confirmed for {escape_md(item_display_name)}, TXID {main_transaction_id}. There was an issue with payment data. Please contact support.") # Use escape_md
        return False

    try:
        # 1. Adjust user balance if part of the payment was from balance
        if paid_from_balance_eur > Decimal('0.0'):
            user_current_data = get_or_create_user(user_id)
            if not user_current_data: # Should not happen if user exists for transaction
                logger.error(f"finalize_successful_crypto_purchase: Failed to get/create user {user_id} for tx {main_transaction_id} while adjusting balance.")
                update_transaction_status(main_transaction_id, 'error_finalizing_user_data')
                bot_instance.send_message(chat_id, f"Payment confirmed for {escape_md(item_display_name)}, TXID {main_transaction_id}. User data error during finalization. Please contact support.") # Use escape_md
                return False

            current_balance_decimal = Decimal(str(user_current_data['balance']))
            new_user_balance_decimal = current_balance_decimal - paid_from_balance_eur

            if not update_user_balance(user_id, float(new_user_balance_decimal), increment_transactions=False): # Transaction already recorded, just adjust balance
                logger.error(f"finalize_successful_crypto_purchase: Failed to update balance for user {user_id} (tx {main_transaction_id}) after partial balance payment.")
                update_transaction_status(main_transaction_id, 'error_finalizing_balance_update')
                bot_instance.send_message(chat_id, f"Payment confirmed for {escape_md(item_display_name)}, TXID {main_transaction_id}. Balance update error. Please contact support.") # Use escape_md
                return False

        # 2. Update main transaction status to 'completed'
        if not update_transaction_status(main_transaction_id, 'completed'):
            logger.warning(f"finalize_successful_crypto_purchase: Failed to update main transaction {main_transaction_id} status to 'completed'. Balance adjustment (if any) was done. User: {user_id}.")
            # Continue, as payment is confirmed. Fulfillment is next.

        # 3. Increment user's overall transaction count for this purchase (if not already done by balance update)
        # The `update_user_balance` in the balance purchase path does `increment_transactions=True`.
        # For crypto, this is the place to do it.
        if not increment_user_transaction_count(user_id):
            logger.warning(f"finalize_successful_crypto_purchase: Failed to increment transaction count for user {user_id} (tx {main_transaction_id}).")

        # 4. Move the specific item instance to purchased folder using product_fs_utils
        move_success = product_fs_utils.move_item_instance_to_purchased(original_instance_path, str(user_id))

        if not move_success:
            logger.error(f"finalize_successful_crypto_purchase: CRITICAL - Filesystem move FAILED for TXID {main_transaction_id}, instance path {original_instance_path}, user {user_id}.")
            bot_instance.send_message(chat_id, f"Payment confirmed for {escape_md(item_display_name)}, TXID {main_transaction_id}. There was an issue with item delivery. Please contact support.") # Use escape_md
            update_transaction_status(main_transaction_id, 'completed_fs_move_error') # Update status to reflect this
            return False

        logger.info(f"finalize_successful_crypto_purchase: Item instance '{original_instance_path}' moved for tx {main_transaction_id}, user {user_id}.")

        # 5. Send confirmation and delivery messages to user
        # Item details for delivery message (description, images) should be fetched from the *original* instance path *before* it's moved,
        # or this information should be part of item_purchase_info if it's comprehensive enough.
        # For now, let's assume item_purchase_info contains enough, or we fetch from original_instance_path one last time (if it's still accessible before move fully completes - risky)
        # Better: get_item_instance_details was called before payment to show to user. That data should be in user_state.

        # Re-fetch details from the *original* path for delivery message just before it's gone, or rely on state.
        # The spec says "sends item delivery message with generic details ... and item images (if any)."
        # The `item_details_json` in transaction record should have 'description' and 'image_paths' if we stored them.
        # Let's assume `item_purchase_info` has 'description' and 'image_paths' (relative to instance or full if stored).
        # For simplicity, we'll use the description from `item_purchase_info`.

        item_final_description = item_purchase_info.get('description', "Your item is ready.") # Fallback
        item_final_images = item_purchase_info.get('image_paths', []) # These should be paths that were valid in original location

        current_flow_state = get_user_state(user_id, 'current_flow')
        if current_flow_state and 'buy_' in current_flow_state:
            clear_user_state(user_id)
            logger.info(f"finalize_successful_crypto_purchase: Cleared user state for user {user_id} after successful purchase {main_transaction_id}.")

        last_bot_msg_id_before_clear = get_user_state(user_id, 'last_bot_message_id')
        if last_bot_msg_id_before_clear:
            try:
                 delete_message(bot_instance, chat_id, last_bot_msg_id_before_clear)
            except Exception as e_del:
                logger.warning(f"finalize_successful_crypto_purchase: Could not delete last bot message {last_bot_msg_id_before_clear} for user {user_id}, tx {main_transaction_id}: {e_del}")


        bot_instance.send_message(chat_id, f"✅ Payment confirmed for TXID {main_transaction_id}!")
        bot_instance.send_message(chat_id, f"Funds have been successfully processed for your purchase of *{escape_md(item_display_name)}*\\.", parse_mode="MarkdownV2") # Use escape_md

        delivery_text = f"Item Details:\n{escape_md(item_final_description)}" # Use escape_md
        delivery_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        new_msg_id_for_state = None
        delivery_images = item_final_images
        if delivery_images and isinstance(delivery_images, list) and len(delivery_images) > 0 and os.path.exists(delivery_images[0]):
            try:
                with open(delivery_images[0], 'rb') as photo:
                    sent_delivery_msg = bot_instance.send_photo(chat_id, photo, caption=delivery_text, reply_markup=delivery_markup, parse_mode="MarkdownV2")
                    new_msg_id_for_state = sent_delivery_msg.message_id
            except Exception as e_photo:
                logger.error(f"finalize_successful_crypto_purchase: Error sending delivery photo for tx {main_transaction_id} (User {user_id}): {e_photo}")
                sent_delivery_msg = bot_instance.send_message(chat_id, delivery_text, reply_markup=delivery_markup, parse_mode="MarkdownV2")
                new_msg_id_for_state = sent_delivery_msg.message_id
        else:
            sent_delivery_msg = bot_instance.send_message(chat_id, delivery_text, reply_markup=delivery_markup, parse_mode="MarkdownV2")
            new_msg_id_for_state = sent_delivery_msg.message_id

        if new_msg_id_for_state:
            update_user_state(user_id, 'last_bot_message_id', new_msg_id_for_state)

        logger.info(f"finalize_successful_crypto_purchase: Successfully processed and delivered item for user {user_id}, tx {main_transaction_id}.")
        return True

    except sqlite3.Error as e_sql: # More specific for database issues during finalization
        logger.exception(f"finalize_successful_crypto_purchase: SQLite error for user {user_id}, tx {main_transaction_id}: {e_sql}")
        bot_instance.send_message(chat_id, f"A database error occurred while finalizing your purchase (TXID {main_transaction_id}). Please contact support.")
        update_transaction_status(main_transaction_id, 'error_finalizing_db')
        return False
    except Exception as e:
        logger.exception(f"finalize_successful_crypto_purchase: Unexpected error for user {user_id}, tx {main_transaction_id}: {e}")
        bot_instance.send_message(chat_id, f"An unexpected error occurred while finalizing your purchase (TXID {main_transaction_id}). Please contact support.")
        update_transaction_status(main_transaction_id, 'error_finalizing_unexpected')
        return False
