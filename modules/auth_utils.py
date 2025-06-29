import sys
import logging
import traceback # For logging exceptions if needed by logger.exception

logger = logging.getLogger(__name__)

ADMIN_ID_INT = None
try:
    # Changed from 'from bot import ADMIN_ID' to 'from config import ADMIN_ID'
    from config import ADMIN_ID
    if ADMIN_ID:
        ADMIN_ID_INT = int(ADMIN_ID)
        logger.info(f"ADMIN_ID loaded and parsed as int: {ADMIN_ID_INT}")
    else:
        logger.warning("ADMIN_ID from config.py is None or empty.")
except (ImportError, ValueError) as e:
    logger.error(f"Could not load or parse ADMIN_ID from config.py: {e}", exc_info=True)
except Exception as e_generic:
    logger.error(f"Unexpected error loading ADMIN_ID: {e_generic}", exc_info=True)


def is_admin(message_or_call_or_id):
    """
    Checks if the user associated with a message, callback query, or a direct ID is the admin.
    """
    if ADMIN_ID_INT is None:
        logger.warning("ADMIN_ID not configured or failed to load. is_admin check will always return False.")
        return False

    user_id = None
    if hasattr(message_or_call_or_id, 'from_user'):
        user_id = message_or_call_or_id.from_user.id
    elif isinstance(message_or_call_or_id, int):
        user_id = message_or_call_or_id
    elif isinstance(message_or_call_or_id, str) and message_or_call_or_id.isdigit():
         user_id = int(message_or_call_or_id)

    if user_id is not None:
        is_auth = (user_id == ADMIN_ID_INT)
        # logger.debug(f"is_admin check for user_id {user_id} against ADMIN_ID {ADMIN_ID_INT}: {is_auth}") # Verbose
        return is_auth

    logger.warning(f"is_admin check: could not determine user_id from input type {type(message_or_call_or_id)}")
    return False

if __name__ == '__main__':
    # This block is for testing the auth_utils.py module directly.
    # It won't run when imported by the bot.
    # For print statements here to show up, ensure basicConfig is set for root logger if testing outside bot context.
    logging.basicConfig(level=logging.INFO) # Example for direct run

    print("\n--- Testing auth_utils.py ---")

    print(f"Initial ADMIN_ID_INT: {ADMIN_ID_INT} (This will be None if ADMIN_ID not set in config.py)")

    class MockUser:
        def __init__(self, id_val): # Renamed 'id' to 'id_val' to avoid clash
            self.id = id_val
    class MockMessage:
        def __init__(self, user_id):
            self.from_user = MockUser(user_id)

    if ADMIN_ID_INT is not None:
        print(f"Testing with ADMIN_ID_INT = {ADMIN_ID_INT}")
        admin_message = MockMessage(ADMIN_ID_INT)
        non_admin_message = MockMessage(123456789)

        print(f"Check admin_message: {is_admin(admin_message)}")
        print(f"Check non_admin_message: {is_admin(non_admin_message)}")
        print(f"Check admin ID (int): {is_admin(ADMIN_ID_INT)}")
        print(f"Check admin ID (str): {is_admin(str(ADMIN_ID_INT))}")
        print(f"Check non-admin ID (int): {is_admin(987654321)}")
    else:
        print("ADMIN_ID_INT is None. Skipping direct tests that rely on it being set.")
        test_message_no_admin_id = MockMessage(111222333)
        print(f"is_admin check when ADMIN_ID_INT is None: {is_admin(test_message_no_admin_id)}")

    print(f"is_admin check with invalid type (list): {is_admin([1,2,3])}")
    print("--- End auth_utils.py Test ---")

