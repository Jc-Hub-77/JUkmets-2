# handlers/add_balance_handler.py
import logging
import os
import datetime
from decimal import Decimal, ROUND_UP

from telebot import types

from modules.db_utils import (
    get_or_create_user, update_user_balance, record_transaction,
    update_transaction_status, get_pending_payment_by_transaction_id,
    update_pending_payment_status, get_next_address_index,
    create_pending_payment, update_main_transaction_for_hd_payment,
    get_transaction_by_id, increment_user_transaction_count
)
from modules import hd_wallet_utils, exchange_rate_utils, payment_monitor
from modules.message_utils import send_or_edit_message, delete_message
from modules.text_utils import escape_md # Import escape_md
import config
from handlers.main_menu_handler import get_main_menu_text_and_markup
import sqlite3 # For specific exception handling

logger = logging.getLogger(__name__)


def handle_add_balance_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    existing_message_id = call.message.message_id
    logger.info(f"User {user_id} initiated 'Add Balance' flow.")

    try:
        get_or_create_user(user_id) # Ensure user exists
        clear_user_state(user_id) # Clear any previous flow state
        update_user_state(user_id, 'current_flow', 'add_balance_awaiting_amount')

        # Removed pre-escaped characters
        prompt_text = "Please enter the EUR amount you wish to add to your balance (e.g., 20.00):"

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='back_to_main'))

        # Escape the text once before sending
        sent_message_id = send_or_edit_message(
            bot_instance, chat_id, escape_md(prompt_text),
            reply_markup=markup,
            existing_message_id=existing_message_id,
            parse_mode="MarkdownV2"
        )

        if sent_message_id:
            update_user_state(user_id, 'last_bot_message_id', sent_message_id)

    except Exception as e:
        logger.exception(f"Error in handle_add_balance_callback for user {user_id}: {e}")
        bot_instance.send_message(chat_id, "An error occurred. Please try returning to the main menu.",
                         reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")))
    finally:
        bot_instance.answer_callback_query(call.id)


def handle_amount_input_for_add_balance(bot_instance, clear_user_state, get_user_state, update_user_state, message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    message_text = message.text
    logger.info(f"User {user_id} entered amount for add balance: {message_text}")

    existing_message_id = get_user_state(user_id, 'last_bot_message_id')

    try:
        float_amount_str = message_text.strip().replace(',', '.')
        requested_eur_decimal = Decimal(float_amount_str).quantize(Decimal('0.01'))

        if requested_eur_decimal <= Decimal('0.00'):
            raise ValueError("Amount must be positive.")
        if requested_eur_decimal > Decimal('5000.00'): # Max top-up
             raise ValueError("Maximum top-up amount is 5000 EUR.")

        try:
            # Access ADD_BALANCE_SERVICE_FEE_EUR from config directly
            service_fee_decimal = Decimal(str(config.ADD_BALANCE_SERVICE_FEE_EUR)).quantize(Decimal('0.01'))
        except (AttributeError, ValueError, TypeError):
            logger.critical(f"ADD_BALANCE_SERVICE_FEE_EUR ('{getattr(config, 'ADD_BALANCE_SERVICE_FEE_EUR', 'NOT SET')}') is not valid. Defaulting to 0.0.")
            service_fee_decimal = Decimal('0.00')

        total_due_eur_decimal = requested_eur_decimal + service_fee_decimal

        update_user_state(user_id, 'add_balance_requested_eur', float(requested_eur_decimal))
        update_user_state(user_id, 'add_balance_total_due_eur', float(total_due_eur_decimal))
        update_user_state(user_id, 'current_flow', 'add_balance_awaiting_payment_method')

        # Construct the raw confirmation text first
        raw_confirmation_text = (f"Amount to Add: {requested_eur_decimal:.2f} EUR\n"
                                 f"Service Fee: {service_fee_decimal:.2f} EUR\n"
                                 f"Total Due: {total_due_eur_decimal:.2f} EUR\n\n"
                                 f"Please select your preferred payment method.") # Removed pre-escaped backslash

        markup_select_payment = types.InlineKeyboardMarkup(row_width=1)
        markup_select_payment.add(types.InlineKeyboardButton("🪙 USDT (TRC20)", callback_data="pay_balance_USDT"))
        markup_select_payment.add(types.InlineKeyboardButton("🪙 BTC (Bitcoin)", callback_data="pay_balance_BTC"))
        markup_select_payment.add(types.InlineKeyboardButton("🪙 LTC (Litecoin)", callback_data="pay_balance_LTC"))
        markup_select_payment.add(types.InlineKeyboardButton("⬅️ Change Amount", callback_data="main_add_balance"))
        markup_select_payment.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        # Escape the final text once before sending
        sent_message_id = send_or_edit_message(
            bot_instance, chat_id, escape_md(raw_confirmation_text),
            reply_markup=markup_select_payment,
            existing_message_id=existing_message_id,
            parse_mode="MarkdownV2"
        )
        if sent_message_id:
            update_user_state(user_id, 'last_bot_message_id', sent_message_id)

    except ValueError as e_val:
        logger.warning(f"User {user_id} entered invalid amount: {message_text}. Error: {e_val}")
        # Construct the raw error text - removed pre-escaped periods
        raw_error_text = f"{str(e_val)}\nPlease enter a valid positive amount (e.g., 10.00 or 25.50)."
        raw_prompt_text_with_error = f"{raw_error_text}\n\nPlease re-enter the EUR amount:"

        markup_retry = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
        # Escape the final text once before sending
        sent_message_id_err = send_or_edit_message(
            bot_instance, chat_id, escape_md(raw_prompt_text_with_error),
            reply_markup=markup_retry,
            existing_message_id=existing_message_id,
            parse_mode="MarkdownV2"
        )
        if sent_message_id_err:
            update_user_state(user_id, 'last_bot_message_id', sent_message_id_err)
    except Exception as e:
        logger.exception(f"Error in handle_amount_input_for_add_balance for user {user_id}: {e}")
        markup_back_to_main_err = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
        bot_instance.send_message(chat_id, "An unexpected error occurred. Please try again or return to the main menu.", reply_markup=markup_back_to_main_err)
        update_user_state(user_id, 'current_flow', None) # Reset flow


def handle_pay_balance_crypto_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    original_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id
    ack_msg = None

    try:
        crypto_currency_selected = call.data.split('pay_balance_')[1]
        if crypto_currency_selected.upper() not in ["USDT", "BTC", "LTC"]: raise IndexError("Invalid crypto")
        logger.info(f"User {user_id} selected {crypto_currency_selected} for adding balance (HD Wallet flow).")
    except IndexError:
        logger.warning(f"Error processing pay_balance_ callback for user {user_id}: {call.data}")
        bot_instance.answer_callback_query(call.id, "Error processing your selection.", show_alert=True)
        return

    requested_eur_float = get_user_state(user_id, 'add_balance_requested_eur')
    total_due_eur_float = get_user_state(user_id, 'add_balance_total_due_eur')

    if requested_eur_float is None or total_due_eur_float is None:
       logger.warning(f"Missing session data for pay_balance (HD Wallet) for user {user_id}.")
       error_text = "Your session seems to have expired or some data is missing. Please start over."
       markup_error = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
       if original_message_id:
           send_or_edit_message(bot_instance, chat_id, error_text, reply_markup=markup_error, existing_message_id=original_message_id, parse_mode="MarkdownV2")
       else:
           bot_instance.send_message(chat_id, error_text, reply_markup=markup_error, parse_mode="MarkdownV2")
       clear_user_state(user_id)
       bot_instance.answer_callback_query(call.id, "Error: Missing session data.", show_alert=True)
       return

    bot_instance.answer_callback_query(call.id)
    if original_message_id:
        ack_msg = send_or_edit_message(bot_instance, chat_id, escape_md("⏳ Generating your payment address..."), existing_message_id=original_message_id, reply_markup=None)
    else:
        ack_msg = bot_instance.send_message(chat_id, escape_md("⏳ Generating your payment address..."))
    # Safely get the message_id, checking if ack_msg is a Message object
    current_message_id_for_invoice = ack_msg.message_id if isinstance(ack_msg, types.Message) else original_message_id


    transaction_notes = f"User adding {requested_eur_float:.2f} EUR to balance. Total due: {total_due_eur_float:.2f} EUR via {crypto_currency_selected}."
    main_transaction_id = record_transaction(
        user_id=user_id, type='balance_top_up',
        eur_amount=total_due_eur_float,
        original_add_balance_amount=requested_eur_float, # Store the original amount user wanted to add
        payment_status='pending_address_generation',
        notes=transaction_notes
    )
    if not main_transaction_id:
        logger.error(f"HD Wallet: Failed to create transaction record for add balance, user {user_id}.")
        send_or_edit_message(bot_instance, chat_id, escape_md("Database error creating transaction. Please try again."), existing_message_id=current_message_id_for_invoice)
        return
    update_user_state(user_id, 'add_balance_transaction_id', main_transaction_id)

    coin_symbol_for_hd_wallet = crypto_currency_selected
    display_coin_symbol = crypto_currency_selected
    network_for_db = crypto_currency_selected

    if crypto_currency_selected == "USDT":
        coin_symbol_for_hd_wallet = "TRX"
        network_for_db = "TRC20 (Tron)"

    try:
        next_idx = get_next_address_index(coin_symbol_for_hd_wallet)
    except Exception as e_idx:
        logger.exception(f"HD Wallet: Error getting next address index for {coin_symbol_for_hd_wallet} (user {user_id}, tx {main_transaction_id}): {e_idx}")
        send_or_edit_message(bot_instance, chat_id, escape_md("Error generating payment address (index). Please try again later or contact support."), existing_message_id=current_message_id_for_invoice)
        update_transaction_status(main_transaction_id, 'error_address_generation')
        return

    unique_address = hd_wallet_utils.generate_address(coin_symbol_for_hd_wallet, next_idx)
    if not unique_address:
        logger.error(f"HD Wallet: Failed to generate address for {coin_symbol_for_hd_wallet}, index {next_idx} (user {user_id}, tx {main_transaction_id}).")
        send_or_edit_message(bot_instance, chat_id, escape_md("Error generating payment address (HD). Please try again later or contact support."), existing_message_id=current_message_id_for_invoice)
        update_transaction_status(main_transaction_id, 'error_address_generation')
        return

    rate = exchange_rate_utils.get_current_exchange_rate("EUR", display_coin_symbol)
    if not rate:
        logger.error(f"HD Wallet: Could not get exchange rate for EUR to {display_coin_symbol} (user {user_id}, tx {main_transaction_id}).")
        send_or_edit_message(bot_instance, chat_id, escape_md(f"Could not retrieve exchange rate for {escape_md(display_coin_symbol)}. Please try again or contact support."), existing_message_id=current_message_id_for_invoice, parse_mode='MarkdownV2')
        update_transaction_status(main_transaction_id, 'error_exchange_rate')
        return

    precision_map = {"BTC": 8, "LTC": 8, "USDT": 6}
    num_decimals = precision_map.get(display_coin_symbol, 8)
    total_due_eur_decimal = Decimal(str(total_due_eur_float))
    expected_crypto_amount_decimal_hr = (total_due_eur_decimal / rate).quantize(Decimal('1e-' + str(num_decimals)), rounding=ROUND_UP)
    smallest_unit_multiplier = Decimal('1e-' + str(num_decimals))
    expected_crypto_amount_smallest_unit_str = str(int(expected_crypto_amount_decimal_hr * smallest_unit_multiplier))

    payment_window_minutes = getattr(config, 'PAYMENT_WINDOW_MINUTES', 60)
    expires_at_dt = datetime.datetime.utcnow() + datetime.timedelta(minutes=payment_window_minutes)

    update_success = update_main_transaction_for_hd_payment(
       main_transaction_id,
       status='awaiting_payment',
       crypto_amount=str(expected_crypto_amount_decimal_hr),
       currency=display_coin_symbol
    )
    if not update_success:
        logger.error(f"HD Wallet: Failed to update main transaction {main_transaction_id} for add balance, user {user_id}.")
        send_or_edit_message(bot_instance, chat_id, escape_md("Database error updating transaction. Please try again."), existing_message_id=current_message_id_for_invoice)
        return
    logger.debug(f"handle_pay_balance_crypto_callback: Main transaction {main_transaction_id} updated with crypto_amount: {expected_crypto_amount_decimal_hr}, currency: {display_coin_symbol}")


    # Check if a pending payment already exists for this transaction ID
    existing_pending_payment = get_pending_payment_by_transaction_id(main_transaction_id)

    if existing_pending_payment:
        logger.warning(f"HD Wallet: Pending payment already exists for main_tx {main_transaction_id} (user {user_id}). Reusing existing record.")
        # Use details from the existing pending payment
        unique_address = existing_pending_payment['address']
        db_coin_symbol_for_pending = existing_pending_payment['coin_symbol']
        network_for_db = existing_pending_payment['network']
        expected_crypto_amount_smallest_unit_str = existing_pending_payment['expected_crypto_amount']
        expires_at_dt = datetime.datetime.fromisoformat(existing_pending_payment['expires_at']) # Assuming ISO format storage
        pending_payment_id = existing_pending_payment['payment_id'] # Keep track of the ID

        # Need to recalculate expected_crypto_amount_decimal_hr for display if needed,
        # or fetch it from the main transaction if stored there.
        # For now, let's assume the main transaction has the human-readable amount.
        main_tx_details = get_transaction_by_id(main_transaction_id)
        if main_tx_details and main_tx_details['crypto_amount']:
             expected_crypto_amount_decimal_hr = Decimal(main_tx_details['crypto_amount'])
        else:
             # Fallback: attempt to convert smallest unit back (might lose precision)
             precision_map = {"BTC": 8, "LTC": 8, "USDT_TRX": 6} # Use DB symbol for lookup
             num_decimals = precision_map.get(db_coin_symbol_for_pending, 8)
             smallest_unit_multiplier = Decimal('1e-' + str(num_decimals))
             expected_crypto_amount_decimal_hr = Decimal(expected_crypto_amount_smallest_unit_str) / smallest_unit_multiplier
             logger.warning(f"HD Wallet: Reconstructed human-readable amount for tx {main_transaction_id} from smallest unit. Precision loss possible.")

        # Ensure display_coin_symbol is correct based on db_coin_symbol_for_pending
        display_coin_symbol = "USDT" if db_coin_symbol_for_pending == "USDT_TRX" else db_coin_symbol_for_pending
        logger.debug(f"handle_pay_balance_crypto_callback: Reused pending payment {pending_payment_id} for tx {main_transaction_id}. Address: {unique_address}, Expected Amount: {expected_crypto_amount_decimal_hr}")


    else:
        # No existing pending payment, create a new one
        db_coin_symbol_for_pending = "USDT_TRX" if crypto_currency_selected == "USDT" else display_coin_symbol
        pending_payment_id = create_pending_payment(
           transaction_id=main_transaction_id,
           user_id=user_id,
           address=unique_address, # unique_address was generated earlier
           coin_symbol=db_coin_symbol_for_pending,
           network=network_for_db,
           expected_crypto_amount=expected_crypto_amount_smallest_unit_str, # calculated earlier
           expires_at=expires_at_dt, # calculated earlier
           paid_from_balance_eur=0.0
        )
        if not pending_payment_id:
           logger.error(f"HD Wallet: Failed to create pending_crypto_payment for add balance main_tx {main_transaction_id} (user {user_id}).")
           update_transaction_status(main_transaction_id, 'error_creating_pending_payment')
           send_or_edit_message(bot_instance, chat_id, escape_md("Error preparing payment record. Please try again or contact support."), existing_message_id=current_message_id_for_invoice)
           return # Exit if creation failed
        logger.debug(f"handle_pay_balance_crypto_callback: Created new pending payment {pending_payment_id} for tx {main_transaction_id}. Address: {unique_address}, Expected Amount (smallest unit): {expected_crypto_amount_smallest_unit_str}")


    # Proceed with generating QR code and sending invoice using the obtained/reused details
    qr_code_path = None
    try:
        # Use the potentially reused unique_address and expected_crypto_amount_decimal_hr
        qr_code_path = hd_wallet_utils.generate_qr_code_for_address(
           unique_address,
           str(expected_crypto_amount_decimal_hr),
           display_coin_symbol # Use the display symbol
        )
    except Exception as e_qr_gen:
        logger.error(f"HD Wallet (add balance): QR code generation failed for {unique_address} (user {user_id}, tx {main_transaction_id}): {e_qr_gen}")

    requested_eur_decimal_for_display = Decimal(str(requested_eur_float)).quantize(Decimal('0.01')) # Convert float to Decimal for display
    service_fee_display = total_due_eur_decimal - requested_eur_decimal_for_display
    # Construct the raw invoice text first - removed pre-escaped characters
    # Decimal amounts are formatted directly, escape_md will handle periods
    raw_invoice_text = (
        f"🧾 INVOICE - Add Balance\n\n"
        f"Amount to Add: {requested_eur_decimal_for_display:.2f} EUR\n"
        f"Service Fee: {service_fee_display:.2f} EUR\n"
        f"Total Due: {total_due_eur_decimal:.2f} EUR\n\n"
        f"🏦 Payment Details\n"
        f"Currency: {display_coin_symbol}\n"
        f"Network: {network_for_db}\n"
        f"Address: `{unique_address}`\n\n"
        f"AMOUNT TO SEND:\n`{expected_crypto_amount_decimal_hr} {display_coin_symbol}`\n\n"
        f"⚠️ Send the exact amount using the correct network. This address is for single use only."
    )

    # Escape the entire raw text once for MarkdownV2
    invoice_text_md = escape_md(raw_invoice_text)

    markup_invoice = types.InlineKeyboardMarkup(row_width=1)
    markup_invoice.add(types.InlineKeyboardButton("✅ Check Payment", callback_data=f"check_bal_payment_{main_transaction_id}"))
    markup_invoice.add(types.InlineKeyboardButton("🚫 Cancel Payment", callback_data=f"cancel_bal_payment_{main_transaction_id}"))
    # Change callback data for "Change Amount / Method" to trigger confirmation flow
    markup_invoice.add(types.InlineKeyboardButton("⬅️ Change Amount / Method", callback_data=f"confirm_change_payment_{main_transaction_id}"))

    if current_message_id_for_invoice:
         try:
             logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Attempting to delete previous message {current_message_id_for_invoice}.")
             delete_message(bot_instance, chat_id, current_message_id_for_invoice)
             logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Previous message deleted successfully.")
         except Exception as e_del:
             logger.warning(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Could not delete previous message {current_message_id_for_invoice}: {e_del}")


    sent_invoice_msg = None
    if qr_code_path and os.path.exists(qr_code_path):
        logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Sending invoice with QR code from {qr_code_path}.")
        try:
            with open(qr_code_path, 'rb') as qr_photo:
                # Use the escaped invoice_text_md for the caption
                sent_invoice_msg = bot_instance.send_photo(chat_id, photo=qr_photo, caption=invoice_text_md, reply_markup=markup_invoice, parse_mode="MarkdownV2")
            logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Invoice with QR code sent successfully.")
        except Exception as e_qr_send:
            logger.error(f"HD Wallet (add balance): Failed to send QR code photo for {unique_address} (user {user_id}, tx {main_transaction_id}): {e_qr_send}. Sending text only.")
            # Use the escaped invoice_text_md for the text message
            sent_invoice_msg = bot_instance.send_message(chat_id, invoice_text_md, reply_markup=markup_invoice, parse_mode="MarkdownV2")
        finally:
            if os.path.exists(qr_code_path):
                try:
                    logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Attempting to remove QR code file {qr_code_path}.")
                    os.remove(qr_code_path)
                    logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - QR code file removed.")
                except Exception as e_rm_qr: logger.error(f"Failed to remove QR code file {qr_code_path}: {e_rm_qr}")
    else:
        logger.warning(f"HD Wallet (add balance): QR code not generated or not found for {unique_address} (user {user_id}, tx {main_transaction_id}). Sending text invoice.")
        # Use the escaped invoice_text_md for the text message
        sent_invoice_msg = bot_instance.send_message(chat_id, invoice_text_md, reply_markup=markup_invoice, parse_mode="MarkdownV2")
        logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Text invoice sent successfully.")


    if sent_invoice_msg:
        update_user_state(user_id, 'last_bot_message_id', sent_invoice_msg.message_id)
        logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Updated last_bot_message_id to {sent_invoice_msg.message_id}.")
    update_user_state(user_id, 'current_flow', 'add_balance_awaiting_hd_payment_confirmation')
    logger.info(f"HD Wallet: User {user_id}, tx {main_transaction_id} - Updated current_flow to add_balance_awaiting_hd_payment_confirmation. Payment process initiated.")


def handle_check_add_balance_payment_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    ack_msg = None
    original_invoice_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id
    logger.info(f"User {user_id} checking add balance payment status for callback data: {call.data}")

    try:
        transaction_id_str = call.data.split('check_bal_payment_')[1]
        transaction_id = int(transaction_id_str)
    except (IndexError, ValueError):
        logger.warning(f"Invalid transaction ID in callback data for check_bal_payment: {call.data} for user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error: Invalid transaction reference.", show_alert=True)
        return

    pending_payment_record = get_pending_payment_by_transaction_id(transaction_id)

    status_msg = "Checking payment status..." # Initialize status_msg

    if not pending_payment_record:
        main_tx = get_transaction_by_id(transaction_id) # Corrected function call
        status_msg = "Payment record not found or already processed."
        if main_tx: status_msg = f"Payment status: {escape_md(main_tx['payment_status'])}."
        bot_instance.answer_callback_query(call.id, status_msg, show_alert=True)
        if main_tx and main_tx['payment_status'] in ['completed', 'cancelled_by_user', 'expired_payment_window', 'error_finalizing_data']:
            new_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            # Need to ensure new_markup is defined if this path is taken and used later
            reply_markup_to_use = new_markup # Assign to a variable used later
        else:
             reply_markup_to_use = call.message.reply_markup # Use existing markup if no final state
    else:
        # Pending payment record found, proceed with checking status via payment_monitor
        # status_msg will be updated later based on payment_monitor.check_specific_pending_payment result
        pass # No need to set status_msg here yet, it's done after the check

    # The code that uses status_msg to edit the message is further down,
    # after the call to payment_monitor.check_specific_pending_payment.
    # The original logic had an early return inside the 'if not pending_payment_record' block
    # which prevented reaching the rest of the function where status_msg was intended to be used.
    # I will remove that early return and adjust the logic flow.

    # Removed the early return from the 'if not pending_payment_record' block.
    # The rest of the function will now execute, and status_msg will be updated
    # based on the result of payment_monitor.check_specific_pending_payment.

    # The original code had a redundant check and message editing block here.
    # I will remove this redundant block and rely on the message editing
    # that happens after the payment_monitor.check_specific_payment call.
    # This simplifies the logic and ensures status_msg is correctly populated.

    # Original redundant block:
    # if original_invoice_message_id and call.message.message_id == original_invoice_message_id:
    #     try:
    #         if call.message.photo: bot_instance.edit_message_caption(caption=escape_markdownv2(status_msg), chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=new_markup, parse_mode="MarkdownV2")
    #         else: bot_instance.edit_message_text(text=escape_markdownv2(status_msg), chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=new_markup, parse_mode="MarkdownV2")
    #     except Exception as e_edit_final: logger.error(f"Error editing final state message {original_invoice_message_id} for user {user_id}, tx {transaction_id}: {e_edit_final}")
    #     return # <--- This return is inside the outer if block

    # The logic for editing the message based on the payment status check
    # is already present further down in the function. I will ensure that
    # the variables used there (like reply_markup_to_use) are correctly
    # handled in the case where pending_payment_record is None.

    # The logic after the payment_monitor.check_specific_pending_payment call
    # correctly determines the status_info and updates the message.
    # I just need to ensure that if pending_payment_record was None,
    # the subsequent logic uses the status_msg and reply_markup_to_use
    # determined in the 'if not pending_payment_record' block.

    # I will restructure the function slightly to make the flow clearer and
    # ensure status_msg and reply_markup_to_use are correctly set before
    # the final message editing logic.

    # Re-reading the function, the logic after the payment_monitor call
    # already handles setting new_status_line and reply_markup_to_use
    # based on status_info. The issue was the early return.
    # By removing the early return, the code will now proceed to the
    # payment_monitor call even if pending_payment_record is None.
    # This might cause issues if payment_monitor.check_specific_pending_payment
    # expects a valid pending record.

    # Let's re-examine the payment_monitor.check_specific_pending_payment function signature and usage.
    # It takes transaction_id. It likely fetches the pending payment record itself.
    # So, the check 'if not pending_payment_record' at the beginning of
    # handle_check_add_balance_payment_callback is somewhat redundant if
    # payment_monitor.check_specific_pending_payment handles the case
    # where the record is not found.

    # Let's simplify the logic. Remove the initial 'if not pending_payment_record' block
    # and rely on payment_monitor.check_specific_pending_payment to determine the status.
    # The message editing logic after the payment_monitor call already handles
    # different status outcomes.

    # Removing the initial 'if not pending_payment_record' block and the redundant message editing.
    # The function will now directly call payment_monitor.check_specific_pending_payment
    # and then update the message based on the result.

    # The original code had this structure:
    # 1. Get pending_payment_record
    # 2. if not pending_payment_record: handle not found case, set status_msg, maybe new_markup, return
    # 3. Answer callback, send "Checking..." message
    # 4. Call payment_monitor.check_specific_pending_payment
    # 5. Delete "Checking..." message
    # 6. Based on status_info from payment_monitor: set new_status_line, alert_message, show_alert_flag, reply_markup_to_use
    # 7. Construct updated_text_for_invoice using base_invoice_text and new_status_line
    # 8. Edit the original invoice message with updated_text_for_invoice and reply_markup_to_use
    # 9. Answer callback query again (redundant?)

    # The error was in step 2's return, preventing step 4-8.
    # The fix is to remove the return in step 2 and ensure variables are correctly scoped.
    # However, the logic in step 6-8 already handles the message update based on status_info.
    # So, the initial 'if not pending_payment_record' block is only needed to answer the callback
    # and potentially show an alert if the record is not found *before* the potentially longer
    # check in payment_monitor.

    # Let's keep the initial check but remove the early return and the redundant message edit.
    # We'll set a flag or status_info early if the record is not found, and the later logic
    # will use this information.

    # Initialize variables that will be used later
    status_info = 'checking' # Default status while checking
    alert_message = "Checking payment status..."
    show_alert_flag = False
    reply_markup_to_use = call.message.reply_markup # Default to existing markup

    pending_payment_record = get_pending_payment_by_transaction_id(transaction_id)

    if not pending_payment_record:
        main_tx = get_transaction_by_id(transaction_id)
        status_info = 'not_found' # Set status_info to indicate not found
        alert_message = "Payment record not found or already processed."
        show_alert_flag = True
        if main_tx:
            alert_message = f"Payment status: {escape_markdownv2(main_tx['payment_status'])}."
            if main_tx['payment_status'] in ['completed', 'cancelled_by_user', 'expired_payment_window', 'error_finalizing_data']:
                 reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
        else:
             reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        # Answer the callback query immediately for the 'not found' case
        bot_instance.answer_callback_query(call.id, alert_message, show_alert=show_alert_flag)

        # Now, proceed to update the message with the 'not found' status.
        # This part was missing or incorrectly placed before.
        current_invoice_text = call.message.caption if call.message.photo else call.message.text
        # Find the start of the payment details section to insert status before it
        payment_details_start_index = (current_invoice_text or "").find("🏦 *Payment Details*")
        if payment_details_start_index != -1:
             base_invoice_text = (current_invoice_text or "")[payment_details_start_index:]
             intro_text = (current_invoice_text or "")[:payment_details_start_index].strip()
        else:
             # If payment details section not found, just append status at the beginning
             base_invoice_text = current_invoice_text or ""
             intro_text = ""

        new_status_line = f"Status: {alert_message}" # Use the alert message as the status line

        updated_text_for_invoice = f"{intro_text}\n\n{new_status_line}\n\n{base_invoice_text}".strip()
        if len(updated_text_for_invoice) > (1024 if call.message.photo else 4096):
            updated_text_for_invoice = updated_text_for_invoice[:(1021 if call.message.photo else 4093)] + "..."

        try:
            if call.message.photo:
                bot_instance.edit_message_caption(caption=escape_md(updated_text_for_invoice), chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=reply_markup_to_use, parse_mode="MarkdownV2")
            else:
                bot_instance.edit_message_text(text=escape_md(updated_text_for_invoice), chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=reply_markup_to_use, parse_mode="MarkdownV2")
        except Exception as e_edit_final:
            logger.error(f"Error editing final state message {original_invoice_message_id} for user {user_id}, tx {transaction_id} (not found case): {e_edit_final}")

        return # Exit after handling the 'not found' case

    # If pending_payment_record was found, proceed with the payment monitor check
    bot_instance.answer_callback_query(call.id) # Answer the callback query
    ack_msg = bot_instance.send_message(chat_id, escape_md("⏳ Checking payment status (on-demand)..."))

    try:
        # This call will now only happen if pending_payment_record was found
        newly_confirmed, status_info = payment_monitor.check_specific_pending_payment(transaction_id)
        if ack_msg: delete_message(bot_instance, chat_id, ack_msg.message_id)

        pending_payment_latest = get_pending_payment_by_transaction_id(transaction_id) # Re-fetch for latest confirmations

        current_invoice_text = call.message.caption if call.message.photo else call.message.text
        # Find the start of the payment details section to insert status before it
        payment_details_start_index = (current_invoice_text or "").find("🏦 *Payment Details*")
        if payment_details_start_index != -1:
             base_invoice_text = (current_invoice_text or "")[payment_details_start_index:]
             intro_text = (current_invoice_text or "")[:payment_details_start_index].strip()
        else:
             # If payment details section not found, just append status at the beginning
             base_invoice_text = current_invoice_text or ""
             intro_text = ""


        new_status_line = ""
        alert_message = ""
        show_alert_flag = True
        reply_markup_to_use = call.message.reply_markup # Default to existing markup

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
            new_markup.add(types.InlineKeyboardButton("⬅️ Try Again", callback_data="main_add_balance"))
            new_markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            reply_markup_to_use = new_markup
        elif status_info == 'error_api':
            new_status_line = f"Status: Could not check status due to a temporary API error. Please try again in a moment."
            alert_message = "Could not check status due to an API error. Please try again."
        elif status_info == 'not_found':
             # This case should ideally not be reached if pending_payment_record was found initially,
             # but handle defensively.
             new_status_line = f"Status: Payment record not found during check."
             alert_message = "Payment record not found during check."
             reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
        elif status_info in ['processed', 'cancelled_by_user', 'expired_payment_window', 'error_finalizing_data', 'error_finalizing', 'error_monitoring_unsupported', 'error_processing_tx_missing', 'processed_tx_already_complete']:
            new_status_line = f"Status: Payment is in a final state: {escape_md(status_info)}."
            alert_message = f"Payment status: {escape_md(status_info)}."
            reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
        else:
            new_status_line = f"Status: Current status: {escape_md(status_info)}."
            alert_message = f"Current status: {escape_md(status_info)}."
            if status_info != 'monitoring':
                 reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        updated_text_for_invoice = f"{intro_text}\n\n{new_status_line}\n\n{base_invoice_text}".strip()
        if len(updated_text_for_invoice) > (1024 if call.message.photo else 4096):
            updated_text_for_invoice = updated_text_for_invoice[:(1021 if call.message.photo else 4093)] + "..."

        try:
            if call.message.photo:
                bot_instance.edit_message_caption(caption=escape_md(updated_text_for_invoice), chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=reply_markup_to_use, parse_mode="MarkdownV2")
            else:
                bot_instance.edit_message_text(text=escape_md(updated_text_for_invoice), chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=reply_markup_to_use, parse_mode="MarkdownV2")
            bot_instance.answer_callback_query(call.id, alert_message, show_alert=show_alert_flag)
        except Exception as e_edit:
            logger.error(f"Error editing message {original_invoice_message_id} for on-demand add balance check (tx {transaction_id}): {e_edit}")
            bot_instance.answer_callback_query(call.id, "Status updated, but message display failed to refresh.", show_alert=True)

    except Exception as e:
        logger.exception(f"Error in handle_check_add_balance_payment_callback (on-demand) for user {user_id}, tx {transaction_id_str}: {e}")
        if ack_msg and hasattr(ack_msg, 'message_id'): delete_message(bot_instance, chat_id, ack_msg.message_id)
        bot_instance.answer_callback_query(call.id, "An error occurred while checking payment status. Please try again.", show_alert=True)

    bot_instance.answer_callback_query(call.id)
    ack_msg = bot_instance.send_message(chat_id, escape_md("⏳ Checking payment status (on-demand)..."))

    try:
        newly_confirmed, status_info = payment_monitor.check_specific_pending_payment(transaction_id)
        if ack_msg: delete_message(bot_instance, chat_id, ack_msg.message_id)

        if newly_confirmed and status_info == 'confirmed_unprocessed':
            logger.info(f"On-demand check for add balance tx {transaction_id} (user {user_id}) resulted in new confirmation. Processing...")
            bot_instance.send_message(chat_id, escape_md("✅ Payment detected! Processing your balance update..."))
            payment_monitor.process_confirmed_payments(bot_instance)
        else:
            logger.info(f"On-demand check for add balance tx {transaction_id} (user {user_id}): newly_confirmed={newly_confirmed}, status_info='{status_info}'")
            pending_payment_latest = get_pending_payment_by_transaction_id(transaction_id)

            current_invoice_text = call.message.caption if call.message.photo else call.message.text
            base_invoice_text = "\n".join([line for line in (current_invoice_text or "").split('\n') if not line.strip().startswith("Status:")])

            new_status_line = ""
            alert_message = ""
            show_alert_flag = True
            reply_markup_to_use = call.message.reply_markup

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
                new_markup.add(types.InlineKeyboardButton("⬅️ Try Again", callback_data="main_add_balance"))
                new_markup.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
                reply_markup_to_use = new_markup
            elif status_info == 'error_api':
                new_status_line = f"Status: Could not check status due to a temporary API error. Please try again in a moment."
                alert_message = "Could not check status due to an API error. Please try again."
            elif status_info == 'not_found':
                new_status_line = f"Status: Payment record not found."
                alert_message = "Payment record not found. This is unexpected."
                reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            elif status_info in ['processed', 'cancelled_by_user', 'expired_payment_window', 'error_finalizing_data']:
                new_status_line = f"Status: Payment record not found or already processed." # This case should ideally not be reached if pending_payment_record is None
                alert_message = "Payment record not found or already processed."
                reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            elif status_info in ['processed', 'cancelled_by_user', 'error_finalizing', 'error_finalizing_data', 'error_monitoring_unsupported', 'error_processing_tx_missing', 'processed_tx_already_complete']:
                new_status_line = f"Status: Payment is in a final state: {escape_md(status_info)}."
                alert_message = f"Payment status: {escape_md(status_info)}."
                reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))
            else:
                new_status_line = f"Status: Current status: {escape_md(status_info)}."
                alert_message = f"Current status: {escape_md(status_info)}."
                if status_info != 'monitoring':
                     reply_markup_to_use = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

            updated_text_for_invoice = f"{new_status_line}\n\n{base_invoice_text}".strip()
            if len(updated_text_for_invoice) > (1024 if call.message.photo else 4096):
                updated_text_for_invoice = updated_text_for_invoice[:(1021 if call.message.photo else 4093)] + "..."

            current_content = call.message.caption if call.message.photo else call.message.text
            current_markup = call.message.reply_markup

            # Check if the content or markup has actually changed before attempting to edit
            if escape_md(updated_text_for_invoice) != current_content or reply_markup_to_use != current_markup:
                try:
                    if call.message.photo:
                        bot_instance.edit_message_caption(caption=escape_md(updated_text_for_invoice), chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=reply_markup_to_use, parse_mode="MarkdownV2")
                    else:
                        bot_instance.edit_message_text(text=escape_md(updated_text_for_invoice), chat_id=chat_id, message_id=original_invoice_message_id, reply_markup=reply_markup_to_use, parse_mode="MarkdownV2")
                    bot_instance.answer_callback_query(call.id, alert_message, show_alert=show_alert_flag)
                except Exception as e_edit:
                    logger.error(f"Error editing message {original_invoice_message_id} for on-demand add balance check (tx {transaction_id}): {e_edit}")
                    bot_instance.answer_callback_query(call.id, "Status updated, but message display failed to refresh.", show_alert=True)
            else:
                # If content and markup are the same, just answer the callback query without editing
                logger.debug(f"Skipping message edit for tx {transaction_id} as content and markup are unchanged.")
                bot_instance.answer_callback_query(call.id, alert_message, show_alert=show_alert_flag)


    except Exception as e:
        logger.exception(f"Error in handle_check_add_balance_payment_callback (on-demand) for user {user_id}, tx {transaction_id_str}: {e}")
        if ack_msg and hasattr(ack_msg, 'message_id'): delete_message(bot_instance, chat_id, ack_msg.message_id)
        bot_instance.answer_callback_query(call.id, "An error occurred while checking payment status. Please try again.", show_alert=True)


def handle_cancel_add_balance_payment_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    original_invoice_message_id = get_user_state(user_id, 'last_bot_message_id') or call.message.message_id
    logger.info(f"User {user_id} initiated cancel for add balance payment: {call.data}")

    try:
        transaction_id_str = call.data.split('cancel_bal_payment_')[1]
        transaction_id = int(transaction_id_str)
    except (IndexError, ValueError):
        logger.warning(f"Invalid transaction ID in cancel_bal_payment callback: {call.data} for user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error: Invalid transaction reference.", show_alert=True)
        return

    user_cancel_message = "Payment process cancelled by user."

    pending_payment = get_pending_payment_by_transaction_id(transaction_id)
    if pending_payment:
        if pending_payment['status'] == 'monitoring':
            if update_pending_payment_status(pending_payment['payment_id'], 'user_cancelled'):
                logger.info(f"HD Pending Payment {pending_payment['payment_id']} for add balance TX_ID {transaction_id} marked as user_cancelled.")
                user_cancel_message = "Payment (HD Wallet) successfully cancelled."
            else:
                logger.error(f"Failed to update HD Pending Payment {pending_payment['payment_id']} for add balance TX_ID {transaction_id} status to user_cancelled.")
                user_cancel_message = "Payment cancellation processed, but there was an issue updating pending record."
        else:
            logger.info(f"HD Pending Payment {pending_payment['payment_id']} for add balance TX_ID {transaction_id} was not 'monitoring' (was {pending_payment['status']}). Main transaction will be cancelled.")
            user_cancel_message = f"Payment already in state '{pending_payment['status']}'. Marked as cancelled by you."
    else:
        logger.warning(f"No HD pending payment record found for add balance TX_ID {transaction_id} upon user cancellation. Main transaction will be marked cancelled.")

    if transaction_id:
        update_transaction_status(transaction_id, 'cancelled_by_user')
    else:
        logger.error(f"Cancel add balance callback triggered with no valid transaction_id from data: {call.data}")

    if original_invoice_message_id:
        try:
            delete_message(bot_instance, chat_id, original_invoice_message_id)
        except Exception as e_del:
            logger.error(f"Error deleting invoice message {original_invoice_message_id} on cancel for add balance, user {user_id}, tx {transaction_id}: {e_del}")

    user_cancel_message_final = user_cancel_message + "\nReturning to the main menu."
    markup_main_menu = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

    final_msg_sent = None
    try:
        final_msg_sent = bot_instance.send_message(chat_id, escape_md(user_cancel_message_final), reply_markup=markup_main_menu, parse_mode="MarkdownV2")
    except Exception as e_send:
        logger.error(f"Error sending cancel confirmation (Markdown) for add balance tx {transaction_id}: {e_send}. Sending plain text.")
        final_msg_sent = bot_instance.send_message(chat_id, user_cancel_message_final.replace('*','').replace('_','').replace('`','').replace('[','').replace(']','').replace('(','').replace(')',''), reply_markup=markup_main_menu)

    bot_instance.answer_callback_query(call.id, "Payment Cancelled.")

    clear_user_state(user_id)
    welcome_text, markup = get_main_menu_text_and_markup()
    new_main_menu_msg_id = send_or_edit_message(
        bot_instance, chat_id, welcome_text,
        reply_markup=markup,
        existing_message_id=None, # Send as a new message
        parse_mode=None
    )
    if new_main_menu_msg_id:
        update_user_state(user_id, 'last_bot_message_id', new_main_menu_msg_id)


# --- Change Payment Confirmation Flow ---
# Decorator moved to bot.py
def handle_confirm_change_payment_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    original_invoice_message_id = call.message.message_id # The message with the invoice and "Change" button
    logger.info(f"User {user_id} initiated change payment confirmation for callback data: {call.data}")
    # Store the original invoice message ID before sending the confirmation message
    original_invoice_message_id_from_state = get_user_state(user_id, 'last_bot_message_id')
    if original_invoice_message_id_from_state:
        update_user_state(user_id, 'add_balance_invoice_message_id', original_invoice_message_id_from_state)
        logger.debug(f"Stored original invoice message ID {original_invoice_message_id_from_state} for user {user_id}.")
    else:
        logger.warning(f"Could not find last_bot_message_id in state for user {user_id} when initiating change payment.")

    try:
        transaction_id_str = call.data.split('confirm_change_payment_')[1]
        transaction_id = int(transaction_id_str)
        update_user_state(user_id, 'confirm_change_payment_tx_id', transaction_id) # Store tx_id for confirmation step
    except (IndexError, ValueError):
        logger.warning(f"Invalid transaction ID in confirm_change_payment callback: {call.data} for user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error: Invalid transaction reference.", show_alert=True)
        return

    bot_instance.answer_callback_query(call.id) # Acknowledge the callback

    confirmation_text = "Are you sure you want to change the payment method? This will cancel the current payment request."
    markup_confirm = types.InlineKeyboardMarkup(row_width=2)
    markup_confirm.add(
        types.InlineKeyboardButton("✅ Yes, Change", callback_data=f"execute_change_payment_{transaction_id}"),
        types.InlineKeyboardButton("❌ No, Keep Current", callback_data=f"cancel_change_payment_{transaction_id}")
    )

    # Send the confirmation message
    sent_confirm_msg = bot_instance.send_message(chat_id, escape_md(confirmation_text), reply_markup=markup_confirm, parse_mode="MarkdownV2")
    update_user_state(user_id, 'last_bot_message_id', sent_confirm_msg.message_id) # Store confirmation message ID


# Decorator moved to bot.py
def handle_execute_change_payment_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    confirmation_message_id = call.message.message_id # The message with the confirmation buttons
    logger.info(f"User {user_id} confirmed change payment for callback data: {call.data}")

    try:
        transaction_id_str = call.data.split('execute_change_payment_')[1]
        transaction_id = int(transaction_id_str)
        # Verify the transaction ID matches the one stored in state, if necessary for security
        # stored_tx_id = get_user_state(user_id, 'confirm_change_payment_tx_id')
        # if stored_tx_id is None or stored_tx_id != transaction_id:
        #     logger.warning(f"User {user_id} execute_change_payment tx_id mismatch. State: {stored_tx_id}, Callback: {transaction_id}")
        #     bot_instance.answer_callback_query(call.id, "Error: Session mismatch.", show_alert=True)
        #     return
    except (IndexError, ValueError):
        logger.warning(f"Invalid transaction ID in execute_change_payment callback: {call.data} for user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error: Invalid transaction reference.", show_alert=True)
        return

    bot_instance.answer_callback_query(call.id, "Changing payment method...") # Acknowledge the callback

    # 1. Cancel the current pending payment and main transaction
    pending_payment = get_pending_payment_by_transaction_id(transaction_id)
    if pending_payment:
        if pending_payment['status'] == 'monitoring':
            update_pending_payment_status(pending_payment['payment_id'], 'user_cancelled')
            logger.info(f"HD Pending Payment {pending_payment['payment_id']} for add balance TX_ID {transaction_id} marked as user_cancelled during change.")
        else:
             logger.warning(f"HD Pending Payment {pending_payment['payment_id']} for add balance TX_ID {transaction_id} was not 'monitoring' ({pending_payment['status']}) during change.")
    else:
        logger.warning(f"No HD pending payment record found for add balance TX_ID {transaction_id} upon change confirmation.")

    update_transaction_status(transaction_id, 'cancelled_by_user')
    logger.info(f"Main transaction {transaction_id} marked as cancelled by user during change payment flow.")

    # 2. Delete the original invoice message
    # Need to retrieve the original invoice message ID. It should be the message *before* the confirmation message.
    # This is tricky with stateless bots. A better approach is to store the original invoice message ID in user state
    # when the invoice is sent. Let's assume 'last_bot_message_id' in state *before* sending the confirmation
    # was the invoice message ID.
    # However, the state was updated with the confirmation message ID.
    # Let's modify handle_confirm_change_payment_callback to store the original invoice message ID.
    original_invoice_message_id = get_user_state(user_id, 'add_balance_invoice_message_id') # Retrieve stored ID
    if original_invoice_message_id:
        try:
            delete_message(bot_instance, chat_id, original_invoice_message_id)
            logger.debug(f"Deleted original invoice message {original_invoice_message_id} for user {user_id} during change payment.")
        except Exception as e_del:
            logger.error(f"Error deleting original invoice message {original_invoice_message_id} on change payment for user {user_id}, tx {transaction_id}: {e_del}")
    else:
        logger.warning(f"Original invoice message ID not found in state for user {user_id} during change payment.")


    # 3. Delete the confirmation message
    try:
        delete_message(bot_instance, chat_id, confirmation_message_id)
        logger.debug(f"Deleted confirmation message {confirmation_message_id} for user {user_id} during change payment.")
    except Exception as e_del_confirm:
        logger.error(f"Error deleting confirmation message {confirmation_message_id} on change payment for user {user_id}: {e_del_confirm}")


    # 4. Redirect the user back to the payment method selection step
    # This is essentially the state after handle_amount_input_for_add_balance.
    # We need the requested_eur_float and total_due_eur_float from state.
    requested_eur_float = get_user_state(user_id, 'add_balance_requested_eur')
    total_due_eur_float = get_user_state(user_id, 'add_balance_total_due_eur')

    if requested_eur_float is None or total_due_eur_float is None:
       logger.warning(f"Missing session data for execute_change_payment for user {user_id}. Returning to main menu.")
       clear_user_state(user_id)
       welcome_text, markup = get_main_menu_text_and_markup()
       bot_instance.send_message(chat_id, welcome_text, reply_markup=markup)
       return

    update_user_state(user_id, 'current_flow', 'add_balance_awaiting_payment_method') # Set flow state

    # Re-send the payment method selection message
    requested_eur_decimal = Decimal(str(requested_eur_float)).quantize(Decimal('0.01'))
    total_due_eur_decimal = Decimal(str(total_due_eur_float)).quantize(Decimal('0.01'))
    service_fee_decimal = total_due_eur_decimal - requested_eur_decimal # Recalculate fee for display

    confirmation_text = (f"Amount to Add: {requested_eur_decimal:.2f} EUR\n"
                        f"Service Fee: {service_fee_decimal:.2f} EUR\n"
                        f"Total Due: {total_due_eur_decimal:.2f} EUR\n\n"
                        f"Please select your preferred payment method.") # Removed bolding around amounts

    markup_select_payment = types.InlineKeyboardMarkup(row_width=1)
    markup_select_payment.add(types.InlineKeyboardButton("🪙 USDT (TRC20)", callback_data="pay_balance_USDT"))
    markup_select_payment.add(types.InlineKeyboardButton("🪙 BTC (Bitcoin)", callback_data="pay_balance_BTC"))
    markup_select_payment.add(types.InlineKeyboardButton("🪙 LTC (Litecoin)", callback_data="pay_balance_LTC"))
    markup_select_payment.add(types.InlineKeyboardButton("⬅️ Change Amount", callback_data="main_add_balance")) # Option to change amount again
    markup_select_payment.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

    sent_message_id = bot_instance.send_message(
        chat_id, escape_md(confirmation_text),
        reply_markup=markup_select_payment
    )
    if sent_message_id:
        update_user_state(user_id, 'last_bot_message_id', sent_message_id) # Store the new message ID


# Decorator moved to bot.py
def handle_cancel_change_payment_callback(bot_instance, clear_user_state, get_user_state, update_user_state, call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    confirmation_message_id = call.message.message_id # The message with the confirmation buttons
    logger.info(f"User {user_id} cancelled change payment for callback data: {call.data}")

    try:
        transaction_id_str = call.data.split('cancel_change_payment_')[1]
        transaction_id = int(transaction_id_str)
        # Verify the transaction ID matches the one stored in state, if necessary
        # stored_tx_id = get_user_state(user_id, 'confirm_change_payment_tx_id')
        # if stored_tx_id is None or stored_tx_id != transaction_id:
        #     logger.warning(f"User {user_id} cancel_change_payment tx_id mismatch. State: {stored_tx_id}, Callback: {transaction_id}")
        #     bot_instance.answer_callback_query(call.id, "Error: Invalid transaction reference.", show_alert=True)
        #     return
    except (IndexError, ValueError):
        logger.warning(f"Invalid transaction ID in cancel_change_payment callback: {call.data} for user {user_id}")
        bot_instance.answer_callback_query(call.id, "Error: Invalid transaction reference.", show_alert=True)
        return

    bot_instance.answer_callback_query(call.id, "Keeping current payment method.") # Acknowledge the callback

    # Delete the confirmation message
    try:
        delete_message(bot_instance, chat_id, confirmation_message_id)
        logger.debug(f"Deleted confirmation message {confirmation_message_id} for user {user_id} after cancellation.")
    except Exception as e_del_confirm:
        logger.error(f"Error deleting confirmation message {confirmation_message_id} on cancel change payment for user {user_id}: {e_del_confirm}")

    # No need to do anything else, the original invoice message remains.
    # Clear the specific state related to the change confirmation flow
    update_user_state(user_id, 'confirm_change_payment_tx_id', None)
    # Ensure the main flow state is still awaiting payment confirmation
    update_user_state(user_id, 'current_flow', 'add_balance_awaiting_hd_payment_confirmation')


# --- Payment Finalization Function (called by payment_monitor) ---
def finalize_successful_top_up(bot_instance, main_transaction_id: int, user_id: int,
                               original_add_balance_amount_str: str, # From main_transaction.original_add_balance_amount
                               received_crypto_amount_str: str,
                               coin_symbol: str,
                               blockchain_tx_id: str
                               ) -> bool:
    logger.info(f"Finalizing successful top-up for user {user_id}, main_tx_id {main_transaction_id}. Amount: {original_add_balance_amount_str}")
    chat_id = user_id

    try:
        try:
            original_add_balance_amount_decimal = Decimal(original_add_balance_amount_str).quantize(Decimal('0.01'))
        except Exception as e_dec:
            logger.error(f"finalize_successful_top_up: Invalid amount format '{original_add_balance_amount_str}' for tx {main_transaction_id}. Error: {e_dec}")
            update_transaction_status(main_transaction_id, 'error_finalizing_data')
            return False

        user_data = get_or_create_user(user_id)
        if not user_data:
            logger.error(f"finalize_successful_top_up: Failed to get/create user {user_id} for tx {main_transaction_id}.")
            update_transaction_status(main_transaction_id, 'error_finalizing_user_data')
            return False

        current_balance_decimal = Decimal(str(user_data['balance'])).quantize(Decimal('0.01'))
        new_balance_decimal = current_balance_decimal + original_add_balance_amount_decimal

        if not update_user_balance(user_id, float(new_balance_decimal), increment_transactions=True): # Main transaction already created, this increments user's total count
            logger.error(f"finalize_successful_top_up: Failed to update balance for user {user_id}, tx {main_transaction_id}.")
            update_transaction_status(main_transaction_id, 'error_finalizing_balance_update')
            return False

        if not update_transaction_status(main_transaction_id, 'completed'):
            logger.warning(f"finalize_successful_top_up: Failed to update main transaction {main_transaction_id} status to completed, but balance was updated for user {user_id}.")

        success_text = (f"✅ Payment confirmed for Transaction ID {main_transaction_id}\\!\n"
                        f"Your balance has been updated by *{escape_md(f'{original_add_balance_amount_decimal:.2f}')} EUR*\\.\n\n"
                        f"New balance: *{escape_md(f'{new_balance_decimal:.2f}')} EUR*")

        markup_main_menu = types.InlineKeyboardMarkup()
        markup_main_menu.add(types.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main"))

        current_flow_state = get_user_state(user_id, 'current_flow')
        if current_flow_state and 'add_balance' in current_flow_state:
            clear_user_state(user_id)
            logger.info(f"finalize_successful_top_up: Cleared user state for user {user_id} after successful top-up {main_transaction_id}.")

        last_bot_msg_id_before_clear = get_user_state(user_id, 'last_bot_message_id')
        if last_bot_msg_id_before_clear:
            try:
                 delete_message(bot_instance, chat_id, last_bot_msg_id_before_clear)
                 logger.debug(f"finalize_successful_top_up: Deleted last bot message {last_bot_msg_id_before_clear} for user {user_id}.")
            except Exception as e_del_msg:
                logger.warning(f"finalize_successful_top_up: Could not delete last bot message for user {user_id}, tx {main_transaction_id}: {e_del_msg}")

        sent_msg = bot_instance.send_message(chat_id, escape_md(success_text), reply_markup=markup_main_menu, parse_mode="MarkdownV2")
        update_user_state(user_id, 'last_bot_message_id', sent_msg.message_id) # Store the new message ID
        logger.info(f"finalize_successful_top_up: Successfully processed top-up for user {user_id}, tx {main_transaction_id}. New balance: {new_balance_decimal:.2f} EUR.")
        return True

    except sqlite3.Error as e_sql:
        logger.exception(f"finalize_successful_top_up: SQLite error for user {user_id}, tx {main_transaction_id}: {e_sql}")
        update_transaction_status(main_transaction_id, 'error_finalizing_db')
        return False
    except Exception as e:
        logger.exception(f"finalize_successful_top_up: Unexpected error for user {user_id}, tx {main_transaction_id}: {e}")
        update_transaction_status(main_transaction_id, 'error_finalizing_unexpected')
        return False
