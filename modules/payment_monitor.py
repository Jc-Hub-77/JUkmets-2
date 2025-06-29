import logging
import time
import datetime
from decimal import Decimal, InvalidOperation
import requests

from modules import db_utils
from modules import blockchain_apis # Imports the module with custom exceptions
from modules.blockchain_apis import ( # Import custom exceptions
    BlockchainAPIError, BlockchainAPITimeoutError,
    BlockchainAPIUnavailableError, BlockchainAPIRateLimitError,
    BlockchainAPIInvalidAddressError, BlockchainAPIBadResponseError
)
from modules import file_system_utils
import config
import sqlite3

from handlers.add_balance_handler import finalize_successful_top_up
from handlers.buy_flow_handler import finalize_successful_crypto_purchase
from modules.text_utils import escape_md

logger = logging.getLogger(__name__)

USDT_DECIMALS = 6

def _get_min_confirmations(coin_symbol_from_db: str) -> int:
    base_coin_symbol = coin_symbol_from_db.split('_')[0].upper()
    default_confirmations = 1
    confirmations = getattr(config, f"MIN_CONFIRMATIONS_{base_coin_symbol}", default_confirmations)
    if not isinstance(confirmations, int) or confirmations < 0:
        logger.warning(f"Invalid MIN_CONFIRMATIONS_{base_coin_symbol} value: {confirmations}. Defaulting to {default_confirmations}.")
        return default_confirmations
    return confirmations

def _handle_api_error_for_payment_check(payment_id, address, coin_symbol, error):
    """Specific error handling for check_pending_payments context."""
    logger.error(f"API Error during payment check for payment_id {payment_id} ({coin_symbol} @ {address}): {type(error).__name__} - {error}")

    # Based on the error type, decide if the payment should be marked with a specific error status
    # or if it's a transient issue allowing retries.
    if isinstance(error, BlockchainAPITimeoutError):
        logger.warning(f"API Timeout for payment_id {payment_id}. Will retry next cycle.")
        # No status change, allow retry by default
    elif isinstance(error, BlockchainAPIUnavailableError):
        logger.warning(f"API Unavailable for payment_id {payment_id}. Will retry next cycle.")
        # No status change
    elif isinstance(error, BlockchainAPIRateLimitError):
        logger.warning(f"API Rate Limit hit for payment_id {payment_id}. Will retry next cycle. Consider increasing API call delay.")
        # No status change, but admin should monitor logs for frequent rate limits
    elif isinstance(error, BlockchainAPIInvalidAddressError):
        logger.error(f"Invalid address for payment_id {payment_id} according to API. Marking payment as error.")
        db_utils.update_pending_payment_status(payment_id, 'error_monitoring_invalid_address')
    elif isinstance(error, BlockchainAPIBadResponseError):
        logger.error(f"Bad API response for payment_id {payment_id}. Marking payment as error.")
        db_utils.update_pending_payment_status(payment_id, 'error_monitoring_bad_response')
    elif isinstance(error, BlockchainAPIError): # Generic custom API error
        logger.error(f"Generic BlockchainAPIError for payment_id {payment_id}. Marking as error.")
        db_utils.update_pending_payment_status(payment_id, 'error_monitoring_api_generic')
    else: # Other unexpected exceptions
        logger.exception(f"Unhandled exception during API call for payment_id {payment_id}: {error}")
        db_utils.update_pending_payment_status(payment_id, 'error_monitoring_unexpected')


def check_pending_payments():
    logger.info("Starting check_pending_payments cycle.")
    pending_payments = db_utils.get_pending_payments_to_monitor()

    if not pending_payments:
        logger.info("No payments currently in 'monitoring' state and not expired.")
        return

    logger.info(f"Found {len(pending_payments)} payments to check.")

    for payment in pending_payments:
        payment_id = payment['payment_id']
        address = payment['address']
        coin_symbol = payment['coin_symbol']
        expected_amount_str = payment['expected_crypto_amount']
        created_at_dt = datetime.datetime.fromisoformat(payment['created_at'])
        current_db_confirmations = payment['confirmations']
        current_db_blockchain_tx_id = payment['blockchain_tx_id']
        # current_db_received_amount = payment['received_crypto_amount'] # Not needed for direct check logic here

        logger.debug(f"Checking payment_id: {payment_id}, address: {address}, coin: {coin_symbol}")

        api_transactions = [] # Initialize to empty list
        time.sleep(getattr(config, 'BLOCKCHAIN_API_CALL_DELAY_SECONDS', 2.0))

        try:
            if coin_symbol == "BTC":
                api_transactions = blockchain_apis.get_address_transactions_btc(address)
            elif coin_symbol == "LTC":
                api_transactions = blockchain_apis.get_address_transactions_ltc(address)
            elif coin_symbol == "USDT_TRX":
                since_ts_ms = int(created_at_dt.timestamp() * 1000) - (60 * 1000 * 5)
                api_transactions = blockchain_apis.get_trc20_transfers_usdt_trx(address, since_timestamp_ms=since_ts_ms)
            else:
                logger.warning(f"Unsupported coin_symbol '{coin_symbol}' for payment_id {payment_id}. Skipping.")
                db_utils.update_pending_payment_status(payment_id, 'error_monitoring_unsupported')
                continue
        except BlockchainAPIError as e_api: # Catch specific custom exceptions
            _handle_api_error_for_payment_check(payment_id, address, coin_symbol, e_api)
            continue
        except Exception as e_generic: # Catch any other unexpected error from the API call layer
             _handle_api_error_for_payment_check(payment_id, address, coin_symbol, e_generic)
             continue


        found_matching_tx_for_confirmation = False
        if api_transactions: # api_transactions is now guaranteed to be a list
            logger.debug(f"Found {len(api_transactions)} API transactions for address {address} ({coin_symbol}).")

            for tx_data_from_api in api_transactions:
                tx_confirmations_api = tx_data_from_api.get('confirmations', 0)
                blockchain_tx_id_api = tx_data_from_api.get('txid')

                # Determine amount key based on coin_symbol more robustly
                amount_key_map = {"BTC": "amount_satoshi", "LTC": "amount_litoshi", "USDT_TRX": "amount_smallest_unit"}
                amount_key = amount_key_map.get(coin_symbol)
                if not amount_key: # Should have been caught by unsupported coin_symbol earlier
                    logger.error(f"Logic error: Undefined amount key for coin_symbol {coin_symbol}, payment_id {payment_id}"); continue

                received_amount_smallest_unit_api_str = tx_data_from_api.get(amount_key)

                if not received_amount_smallest_unit_api_str or not blockchain_tx_id_api:
                    logger.warning(f"Skipping tx for payment_id {payment_id} due to missing amount or txid. Data: {tx_data_from_api}")
                    continue

                try:
                    received_decimal_api = Decimal(received_amount_smallest_unit_api_str)
                    expected_decimal_db = Decimal(expected_amount_str)
                except InvalidOperation:
                    logger.error(f"Could not convert amounts to Decimal for payment_id {payment_id}, tx {blockchain_tx_id_api}. API_RX: '{received_amount_smallest_unit_api_str}', DB_EXP: '{expected_amount_str}'. Skipping tx.")
                    continue

                if current_db_blockchain_tx_id and current_db_blockchain_tx_id == blockchain_tx_id_api:
                    found_matching_tx_for_confirmation = True
                    logger.info(f"Re-checking known tx {blockchain_tx_id_api} for payment_id {payment_id}. API_Confs: {tx_confirmations_api}, DB_Confs: {current_db_confirmations}")

                    db_utils.update_pending_payment_check_details(payment_id, tx_confirmations_api, str(received_decimal_api), blockchain_tx_id_api)

                    min_confs_needed = _get_min_confirmations(coin_symbol)
                    if tx_confirmations_api >= min_confs_needed:
                        logger.info(f"Payment {payment_id} (tx {blockchain_tx_id_api}) CONFIRMED with {tx_confirmations_api} confs.")
                        db_utils.update_pending_payment_status(payment_id, 'confirmed_unprocessed')
                    break
                elif not current_db_blockchain_tx_id:
                    if received_decimal_api >= expected_decimal_db:
                        logger.info(f"Found NEW potential matching tx for payment_id {payment_id}: txid {blockchain_tx_id_api}, received {received_decimal_api}, expected {expected_decimal_db}.")
                        found_matching_tx_for_confirmation = True
                        min_confs_needed = _get_min_confirmations(coin_symbol)

                        db_utils.update_pending_payment_check_details(payment_id, tx_confirmations_api, str(received_decimal_api), blockchain_tx_id_api)
                        # current_db_blockchain_tx_id = blockchain_tx_id_api # No need to set here, will be re-fetched next cycle if not confirmed

                        if tx_confirmations_api >= min_confs_needed:
                            logger.info(f"Payment {payment_id} (tx {blockchain_tx_id_api}) CONFIRMED with {tx_confirmations_api} confs.")
                            db_utils.update_pending_payment_status(payment_id, 'confirmed_unprocessed')
                        else:
                            logger.info(f"Payment {payment_id} (tx {blockchain_tx_id_api}) found, but only {tx_confirmations_api}/{min_confs_needed} confirmations. Now tracking this TX.")
                        break

                    elif received_decimal_api > 0:
                        logger.warning(f"UNDERPAYMENT detected for payment_id {payment_id}, address {address}. Expected: {expected_decimal_db}, Received: {received_decimal_api} in tx {blockchain_tx_id_api}.")
                        db_utils.update_pending_payment_check_details(payment_id, tx_confirmations_api, str(received_decimal_api), blockchain_tx_id_api)
                        db_utils.update_pending_payment_status(payment_id, 'underpaid')
                        found_matching_tx_for_confirmation = True
                        break

        if not found_matching_tx_for_confirmation:
            logger.debug(f"No new or tracked matching tx found for payment_id {payment_id}. Updating last_checked_at.")
            db_utils.update_pending_payment_check_details(payment_id, current_db_confirmations)

    logger.info("Finished check_pending_payments cycle.")


def process_confirmed_payments(bot_instance=None):
    logger.info("Starting process_confirmed_payments cycle.")
    confirmed_payments = db_utils.get_confirmed_unprocessed_payments(limit=20)

    if not confirmed_payments:
        logger.info("No 'confirmed_unprocessed' payments to process.")
        return

    logger.info(f"Found {len(confirmed_payments)} 'confirmed_unprocessed' payments to process.")

    for payment in confirmed_payments:
        payment_id = payment['payment_id']
        user_id = payment['user_id']
        main_tx_id = payment['transaction_id']
        coin_symbol = payment['coin_symbol']
        received_amount_str = payment['received_crypto_amount']
        blockchain_tx_id = payment['blockchain_tx_id']
        paid_from_balance_eur_str_from_payment = str(payment.get('paid_from_balance_eur', '0.0'))


        logger.info(f"Processing confirmed payment_id {payment_id} for main_transaction_id {main_tx_id} (user {user_id}).")

        main_tx_details = db_utils.get_transaction_by_id(main_tx_id)

        if not main_tx_details:
            logger.error(f"Main transaction {main_tx_id} not found for confirmed payment_id {payment_id}. Marking as error.")
            db_utils.update_pending_payment_status(payment_id, 'error_processing_tx_missing')
            continue

        if main_tx_details['payment_status'] == 'completed':
            logger.warning(f"Main transaction {main_tx_id} already marked 'completed'. Pending payment {payment_id} might be a duplicate signal. Marking 'processed'.")
            db_utils.update_pending_payment_status(payment_id, 'processed_tx_already_complete')
            continue

        processing_success = False
        finalization_notes = (f"Crypto payment confirmed. Coin: {coin_symbol}, "
                              f"Blockchain TXID: {blockchain_tx_id}, "
                              f"Received (smallest unit): {received_amount_str}. "
                              f"Processed by payment_monitor.")


        if main_tx_details['type'] == 'balance_top_up':
            amount_to_add_str = str(main_tx_details['original_add_balance_amount'])
            if main_tx_details['original_add_balance_amount'] is None:
                logger.error(f"Critical: original_add_balance_amount is NULL for balance_top_up tx {main_tx_id}, payment_id {payment_id}.")
                db_utils.update_transaction_status(main_tx_id, 'failed_data_error')
                db_utils.update_pending_payment_status(payment_id, 'error_finalizing_data')
                continue

            logger.info(f"Calling finalize_successful_top_up for payment_id {payment_id}, main_tx_id {main_tx_id}, user {user_id}, amount {amount_to_add_str}.")
            processing_success = finalize_successful_top_up(
                bot_instance=bot_instance,
                main_transaction_id=main_tx_id,
                user_id=user_id,
                original_add_balance_amount_str=amount_to_add_str,
                received_crypto_amount_str=received_amount_str,
                coin_symbol=coin_symbol,
                blockchain_tx_id=blockchain_tx_id
            )
            if not processing_success:
                 logger.error(f"finalize_successful_top_up handler failed for main_tx_id {main_tx_id}, payment_id {payment_id}.")
                 db_utils.update_transaction_status(main_tx_id, 'failed_finalization_handler')


        elif main_tx_details['type'] == 'purchase_crypto':
            product_id = main_tx_details['product_id']
            if product_id is None:
                logger.error(f"Critical: product_id is NULL for purchase_crypto tx {main_tx_id}, payment_id {payment_id}.")
                db_utils.update_transaction_status(main_tx_id, 'failed_data_error')
                db_utils.update_pending_payment_status(payment_id, 'error_finalizing_data')
                continue

            logger.info(f"Calling finalize_successful_crypto_purchase for payment_id {payment_id}, main_tx_id {main_tx_id}, user {user_id}, product {product_id}.")
            processing_success = finalize_successful_crypto_purchase(
                bot_instance=bot_instance,
                main_transaction_id=main_tx_id,
                user_id=user_id,
                product_id=product_id,
                paid_from_balance_eur_str=paid_from_balance_eur_str_from_payment,
                received_crypto_amount_str=received_amount_str,
                coin_symbol=coin_symbol,
                blockchain_tx_id=blockchain_tx_id
            )
            if not processing_success:
                logger.error(f"finalize_successful_crypto_purchase handler failed for main_tx_id {main_tx_id}, payment_id {payment_id}.")
                db_utils.update_transaction_status(main_tx_id, 'failed_finalization_handler')
        else:
            logger.error(f"Unknown transaction type '{main_tx_details['type']}' for main_tx_id {main_tx_id}, payment_id {payment_id}. Payment: {payment}")
            db_utils.update_pending_payment_status(payment_id, 'error_unknown_type')
            continue

        if processing_success:
            db_utils.update_pending_payment_status(payment_id, 'processed')
            updated_notes = (main_tx_details['notes'] + " | " + finalization_notes).strip(" | ") if main_tx_details['notes'] else finalization_notes
            conn = db_utils.get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE transactions SET notes = ?, updated_at = CURRENT_TIMESTAMP WHERE transaction_id = ?", (updated_notes, main_tx_id))
                conn.commit()
            except sqlite3.Error as e_notes:
                logger.error(f"Failed to update notes for main tx {main_tx_id}: {e_notes}")
            finally:
                conn.close()
        else:
            logger.error(f"Failed to finalize main transaction {main_tx_id} (type: {main_tx_details['type']}) after payment {payment_id} was confirmed.")
            db_utils.update_pending_payment_status(payment_id, 'error_finalizing')

    logger.info("Finished process_confirmed_payments cycle.")


def expire_stale_monitoring_payments(bot_instance=None):
    logger.info("Starting expire_stale_monitoring_payments cycle.")
    stale_payments = db_utils.get_stale_monitoring_payments()

    if not stale_payments:
        logger.info("No stale monitoring payments to expire.")
        return

    logger.info(f"Found {len(stale_payments)} stale payments to mark as expired.")
    for payment_summary in stale_payments:
        payment_id = payment_summary['payment_id']
        user_id = payment_summary['user_id']
        main_tx_id = payment_summary['transaction_id']
        address = payment_summary['address']

        pending_payment_full = db_utils.get_pending_payment_by_transaction_id(main_tx_id)

        logger.info(f"Expiring stale payment_id {payment_id} (main_tx_id {main_tx_id}) for user {user_id}, address {address}.")

        if not db_utils.update_pending_payment_status(payment_id, 'expired'):
            logger.error(f"Failed to update pending payment {payment_id} status to 'expired'. Skipping associated main transaction update for now.")
            continue

        main_tx_status_update = 'failed_expired_notfound'
        expiry_notification_suffix = "as no payment was detected in time."
        if pending_payment_full and pending_payment_full['blockchain_tx_id']:
            main_tx_status_update = 'failed_expired_unconfirmed'
            expiry_notification_suffix = (f"as the detected transaction \\(`{escape_md(pending_payment_full['blockchain_tx_id'][:10])}\\.\\.\\.`\\) "
                                          f"did not receive enough confirmations in time \\({pending_payment_full['confirmations']}\\)\\.")


        if not db_utils.update_transaction_status(main_tx_id, main_tx_status_update):
            logger.error(f"Failed to update main transaction {main_tx_id} status to '{main_tx_status_update}' for expired pending payment {payment_id}.")

        if bot_instance:
            try:
                main_tx_details = db_utils.get_transaction_by_id(main_tx_id)
                if main_tx_details:
                    type_escaped = escape_md(main_tx_details['type'].replace('_', ' ').title())
                    bot_instance.send_message(user_id,
                        f"⚠️ Your payment attempt \\(Order \\#{main_tx_id}, Type: {type_escaped}\\) for address `{escape_md(address)}` has expired {expiry_notification_suffix} "
                        f"Please try again or contact support if you believe this is an error\\.",
                        parse_mode="MarkdownV2"
                    )
                    logger.info(f"Notified user {user_id} about expired payment {payment_id} for main_tx_id {main_tx_id}.")
                else:
                    logger.warning(f"Could not fetch main_tx_details for {main_tx_id} to notify user {user_id} about expired payment {payment_id}.")
            except Exception as e_notify_expire:
                logger.error(f"Failed to notify user {user_id} about expired payment {payment_id} (main_tx_id {main_tx_id}): {e_notify_expire}")

    logger.info("Finished expire_stale_monitoring_payments cycle.")

def check_specific_pending_payment(transaction_id: int) -> tuple[bool, str | None]:
    logger.info(f"On-demand check initiated for transaction_id: {transaction_id}")
    pending_payment = db_utils.get_pending_payment_by_transaction_id(transaction_id)

    if not pending_payment:
        logger.warning(f"On-demand check: No pending_payment record found for transaction_id: {transaction_id}")
        main_tx = db_utils.get_transaction_by_id(transaction_id)
        if main_tx: return False, main_tx['payment_status']
        return False, 'not_found'

    payment_id = pending_payment['payment_id']
    current_status = pending_payment['status']
    current_db_confirmations = pending_payment['confirmations']
    current_db_blockchain_tx_id = pending_payment['blockchain_tx_id']

    if current_status not in ['monitoring', 'underpaid']:
        logger.info(f"On-demand check: Payment {payment_id} (tx: {transaction_id}) is already in status '{current_status}'. No API check needed.")
        return False, current_status

    expires_at_dt = datetime.datetime.fromisoformat(pending_payment['expires_at'])
    if datetime.datetime.utcnow() >= expires_at_dt and current_status == 'monitoring':
        logger.info(f"On-demand check: Payment {payment_id} (tx: {transaction_id}) has expired. Updating status.")
        db_utils.update_pending_payment_status(payment_id, 'expired')
        status_to_set = 'failed_expired_notfound'
        if current_db_blockchain_tx_id: status_to_set = 'failed_expired_unconfirmed'
        db_utils.update_transaction_status(transaction_id, status_to_set)
        return False, 'expired'

    address = pending_payment['address']
    coin_symbol = pending_payment['coin_symbol']
    expected_amount_str = pending_payment['expected_crypto_amount']
    created_at_dt = datetime.datetime.fromisoformat(pending_payment['created_at'])

    logger.debug(f"On-demand check: Performing blockchain API call for payment_id: {payment_id}, address: {address}, coin: {coin_symbol}")

    api_call_delay = getattr(config, 'BLOCKCHAIN_API_CALL_DELAY_SECONDS', 2.0)
    time.sleep(api_call_delay / 2 if api_call_delay > 1 else 0.5)


    api_transactions = []
    try:
        if coin_symbol == "BTC":
            api_transactions = blockchain_apis.get_address_transactions_btc(address)
        elif coin_symbol == "LTC":
            api_transactions = blockchain_apis.get_address_transactions_ltc(address)
        elif coin_symbol == "USDT_TRX":
            since_ts_ms = int(created_at_dt.timestamp() * 1000) - (60 * 1000 * 5)
            api_transactions = blockchain_apis.get_trc20_transfers_usdt_trx(address, since_timestamp_ms=since_ts_ms)
        else: # Should be caught by earlier validation
            logger.error(f"On-demand check: Unsupported coin_symbol '{coin_symbol}' for payment_id {payment_id}.")
            return False, 'error_config'
    except BlockchainAPIError as e_api:
        _handle_api_error_for_payment_check(payment_id, address, coin_symbol, e_api)
        return False, 'error_api' # Return a generic API error status for the caller
    except Exception as e_generic:
        _handle_api_error_for_payment_check(payment_id, address, coin_symbol, e_generic)
        return False, 'error_api'


    if not api_transactions:
        logger.info(f"On-demand check: No transactions found from API for address {address} ({coin_symbol}), payment_id {payment_id}.")
        db_utils.update_pending_payment_check_details(payment_id, current_db_confirmations)
        return False, current_status

    logger.debug(f"On-demand check: Found {len(api_transactions)} API transactions for address {address} ({coin_symbol}).")

    newly_confirmed_this_check = False
    status_after_check = current_status

    for tx_data_from_api in api_transactions:
        tx_confirmations_api = tx_data_from_api.get('confirmations', 0)
        blockchain_tx_id_api = tx_data_from_api.get('txid')

        amount_key_map = {"BTC": "amount_satoshi", "LTC": "amount_litoshi", "USDT_TRX": "amount_smallest_unit"}
        amount_key = amount_key_map.get(coin_symbol)
        if not amount_key: continue
        received_amount_smallest_unit_api_str = tx_data_from_api.get(amount_key)

        if not received_amount_smallest_unit_api_str or not blockchain_tx_id_api:
            continue

        try:
            received_decimal_api = Decimal(received_amount_smallest_unit_api_str)
            expected_decimal_db = Decimal(expected_amount_str)
        except InvalidOperation:
            continue

        if current_db_blockchain_tx_id and current_db_blockchain_tx_id == blockchain_tx_id_api:
            db_utils.update_pending_payment_check_details(payment_id, tx_confirmations_api, str(received_decimal_api), blockchain_tx_id_api)
            min_confs_needed = _get_min_confirmations(coin_symbol)

            if tx_confirmations_api >= min_confs_needed:
                if current_status == 'monitoring':
                     if received_decimal_api >= expected_decimal_db:
                        db_utils.update_pending_payment_status(payment_id, 'confirmed_unprocessed')
                        newly_confirmed_this_check = True
                        status_after_check = 'confirmed_unprocessed'
                     else:
                        db_utils.update_pending_payment_status(payment_id, 'underpaid')
                        status_after_check = 'underpaid'
                elif current_status == 'underpaid':
                    status_after_check = 'underpaid'
            else:
                status_after_check = 'monitoring_updated' if current_status == 'monitoring' else 'underpaid'
            break

        elif not current_db_blockchain_tx_id:
            if received_decimal_api >= expected_decimal_db:
                min_confs_needed = _get_min_confirmations(coin_symbol)
                db_utils.update_pending_payment_check_details(payment_id, tx_confirmations_api, str(received_decimal_api), blockchain_tx_id_api)
                if tx_confirmations_api >= min_confs_needed:
                    db_utils.update_pending_payment_status(payment_id, 'confirmed_unprocessed')
                    newly_confirmed_this_check = True
                    status_after_check = 'confirmed_unprocessed'
                else:
                    status_after_check = 'monitoring_updated'
                break
            elif received_decimal_api > 0:
                logger.warning(f"On-demand check: Potential UNDERPAYMENT for payment_id {payment_id}. Expected: {expected_decimal_db}, Received: {received_decimal_api} in tx {blockchain_tx_id_api}.")
                db_utils.update_pending_payment_check_details(payment_id, tx_confirmations_api, str(received_decimal_api), blockchain_tx_id_api)
                db_utils.update_pending_payment_status(payment_id, 'underpaid')
                status_after_check = 'underpaid'
                break

    if not newly_confirmed_this_check and status_after_check == current_status :
        db_utils.update_pending_payment_check_details(payment_id, current_db_confirmations)

    return newly_confirmed_this_check, status_after_check


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    logger.info("Payment Monitor module - Self-Test Mode")
    logger.info("Self-test placeholders finished.")

