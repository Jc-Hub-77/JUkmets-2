import telebot
import logging
import os
import re

# Configure logging for this module
logger = logging.getLogger(__name__)

# Removed local escape_markdownv2 function.
# Relying on escape_md from modules.text_utils

def send_or_edit_message(bot, chat_id, text, reply_markup=None, parse_mode="MarkdownV2", photo_url=None, existing_message_id=None, local_photo_path=None):
    """
    Sends a new message or edits an existing one.
    Supports sending a local photo file (local_photo_path) which takes precedence.
    If local_photo_path is used with existing_message_id, the old message is deleted first.
    If photo_url (web URL) is provided with existing_message_id, it also attempts to delete and resend.
    Returns the message_id of the sent or edited message, or None on failure.

    NOTE: Text escaping for MarkdownV2 must be done by the caller using modules.text_utils.escape_md.
    """
    # Text escaping is now the responsibility of the calling handler.
    # The text passed here should already be escaped if parse_mode is MarkdownV2.

    try:
        # Priority 1: Local photo path provided
        if local_photo_path and isinstance(local_photo_path, str):
            if existing_message_id:
                # Delete the old message before sending a new one with the photo
                delete_message(bot, chat_id, existing_message_id)
                # After deletion, there's no existing message to edit, so set to None
                existing_message_id = None

            try:
                with open(local_photo_path, 'rb') as photo_file:
                    logger.info(f"Sending local photo: {local_photo_path} to chat_id: {chat_id}")
                    msg = bot.send_photo(chat_id, photo=photo_file, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
                    return msg.message_id
            except FileNotFoundError:
                logger.warning(f"Local photo not found at {local_photo_path}. Falling back to text or URL photo.")
                # Fall through to try sending text-only or URL-based photo if local file fails
            except telebot.apihelper.ApiException as e_photo_send:
                logger.error(f"Telegram API Exception sending local photo {local_photo_path}: {e_photo_send}")
                return None # Explicitly return None on photo send failure
            except Exception as e_general_photo:
                logger.error(f"Unexpected error sending local photo {local_photo_path}: {e_general_photo}", exc_info=True)
                return None # Explicitly return None

        # Priority 2: photo_url (web URL) or text-only message logic (if local_photo_path was not used or failed)
        if existing_message_id:
            if photo_url: # Web URL photo
                # If a web photo is involved with an existing message, delete and resend.
                delete_message(bot, chat_id, existing_message_id)
                # Proceed to send a new photo message
                message = bot.send_photo(chat_id, photo=photo_url, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
                return message.message_id
            else: # Text-only edit
                message = bot.edit_message_text(text, chat_id, existing_message_id, reply_markup=reply_markup, parse_mode=parse_mode)
                return message.message_id
        else: # No existing_message_id, send a new message
            if photo_url: # Web URL photo
                message = bot.send_photo(chat_id, photo=photo_url, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
            else: # Text-only send
                message = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
            return message.message_id

    except telebot.apihelper.ApiException as e:
        logger.error(f"Telegram API Exception in send_or_edit_message (outer try): {e}")
        # Fallback for text messages if editing failed and local_photo_path was not involved
        if existing_message_id and not photo_url and not local_photo_path:
            logger.info(f"Editing failed for message {existing_message_id}. Attempting to send a new text message.")
            try:
                # This fallback should only send a text message, photo fallbacks are handled above
                message = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                return message.message_id
            except telebot.apihelper.ApiException as e_send:
                logger.error(f"Telegram API Exception on fallback send_message: {e_send}")
                return None
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred in send_or_edit_message (outer try): {e}", exc_info=True)
        return None

def delete_message(bot, chat_id, message_id):
    """
    Deletes a message.
    Returns True on success, False on failure.
    """
    if not message_id:
        logger.warning("delete_message called with no message_id.")
        return False
    try:
        bot.delete_message(chat_id, message_id)
        return True
    except telebot.apihelper.ApiException as e:
        # Common errors: "message to delete not found", "message can't be deleted"
        logger.warning(f"Could not delete message {message_id} in chat {chat_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred in delete_message: {e}", exc_info=True)
        return None

if __name__ == '__main__':
    # This section is for illustrative purposes.
    # To run this, you'd need to set up a mock bot or a real bot token.
    print("Message utilities defined. To test, you would need a TeleBot instance.")
    # Example (conceptual):
    # mock_bot = telebot.TeleBot("YOUR_TOKEN") # Replace with your token if testing for real
    # chat_id_test = 123456789 # Replace with your chat_id

    # print("Simulating send_or_edit_message (new text message)...")
    # new_msg_id = send_or_edit_message(mock_bot, chat_id_test, "Hello from message_utils!")
    # if new_msg_id:
    #     print(f"Message sent, ID: {new_msg_id}")
    #     time.sleep(2)
    #     print("Simulating send_or_edit_message (editing text message)...")
    #     edited_msg_id = send_or_edit_message(mock_bot, chat_id_test, "Hello again (edited)!", existing_message_id=new_msg_id)
    #     if edited_msg_id:
    #         print(f"Message edited, ID: {edited_msg_id}")
    #         time.sleep(2)
    #         print("Simulating delete_message...")
    #         if delete_message(mock_bot, chat_id_test, edited_msg_id):
    #             print("Message deleted successfully.")
    # else:
    #     print("Failed to send initial message.")

    # print("\nConsider adding more comprehensive mock tests for different scenarios,")
    # print("especially for photo handling and various API error conditions.")

def send_loading_acknowledgment(bot, chat_id, callback_query_id=None, message_object=None):
    """
    Sends a loading acknowledgment to the user.
    Uses answer_callback_query if callback_query_id is provided,
    and send_chat_action for a 'typing...' indicator.
    """
    try:
        if callback_query_id:
            bot.answer_callback_query(callback_query_id, text="Processing...")

        # Show "typing..." status
        bot.send_chat_action(chat_id, action='typing')

    except telebot.apihelper.ApiException as e:
        logger.warning(f"Telegram API Exception in send_loading_acknowledgment: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred in send_loading_acknowledgment: {e}", exc_info=True)

# Example usage within the module (for illustration or direct testing if needed)
if __name__ == '__main__':
    # ... (previous __main__ content for send_or_edit_message and delete_message) ...

    print("\nIllustrative test for send_loading_acknowledgment (requires mock bot):")
    # mock_bot = telebot.TeleBot("YOUR_TOKEN") # Replace with your token
    # chat_id_test = 123456789 # Replace with your chat_id
    # mock_callback_query_id = "dummy_query_id" # Dummy ID for testing

    # print("Simulating send_loading_acknowledgment with callback_query_id...")
    # send_loading_acknowledgment(mock_bot, chat_id_test, callback_query_id=mock_callback_query_id)
    # print("Check Telegram for 'Processing...' popup and 'typing...' status.")
    # time.sleep(3) # Keep alive for visual check

    # print("\nSimulating send_loading_acknowledgment without callback_query_id (typing only)...")
    # send_loading_acknowledgment(mock_bot, chat_id_test)
    # print("Check Telegram for 'typing...' status.")
    # time.sleep(3)

def send_or_edit_message(bot, chat_id, text, reply_markup=None, parse_mode="MarkdownV2", photo_url=None, existing_message_id=None, local_photo_path=None):
    """
    Sends a new message or edits an existing one.
    Supports sending a local photo file (local_photo_path) which takes precedence.
    If local_photo_path is used with existing_message_id, the old message is deleted first.
    If photo_url (web URL) is provided with existing_message_id, it also attempts to delete and resend.
    Returns the message_id of the sent or edited message, or None on failure.
    """
    try:
        # Priority 1: Local photo path provided
        if local_photo_path and isinstance(local_photo_path, str):
            if existing_message_id:
                # Delete the old message before sending a new one with the photo
                delete_message(bot, chat_id, existing_message_id)
                # After deletion, there's no existing message to edit, so set to None
                existing_message_id = None

            try:
                with open(local_photo_path, 'rb') as photo_file:
                    logger.info(f"Sending local photo: {local_photo_path} to chat_id: {chat_id}")
                    msg = bot.send_photo(chat_id, photo=photo_file, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
                    return msg.message_id
            except FileNotFoundError:
                logger.warning(f"Local photo not found at {local_photo_path}. Falling back to text or URL photo.")
                # Fall through to try sending text-only or URL-based photo if local file fails
            except telebot.apihelper.ApiException as e_photo_send:
                logger.error(f"Telegram API Exception sending local photo {local_photo_path}: {e_photo_send}")
                return None # Explicitly return None on photo send failure
            except Exception as e_general_photo:
                logger.error(f"Unexpected error sending local photo {local_photo_path}: {e_general_photo}", exc_info=True)
                return None # Explicitly return None

        # Priority 2: photo_url (web URL) or text-only message logic (if local_photo_path was not used or failed)
        if existing_message_id:
            if photo_url: # Web URL photo
                # If a web photo is involved with an existing message, delete and resend.
                delete_message(bot, chat_id, existing_message_id)
                # Proceed to send a new photo message
                message = bot.send_photo(chat_id, photo=photo_url, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
                return message.message_id
            else: # Text-only edit
                message = bot.edit_message_text(text, chat_id, existing_message_id, reply_markup=reply_markup, parse_mode=parse_mode)
                return message.message_id
        else: # No existing_message_id, send a new message
            if photo_url: # Web URL photo
                message = bot.send_photo(chat_id, photo=photo_url, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
            else: # Text-only send
                message = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
            return message.message_id

    except telebot.apihelper.ApiException as e:
        logger.error(f"Telegram API Exception in send_or_edit_message (outer try): {e}")
        # Fallback for text messages if editing failed and local_photo_path was not involved
        if existing_message_id and not photo_url and not local_photo_path:
            logger.info(f"Editing failed for message {existing_message_id}. Attempting to send a new text message.")
            try:
                # This fallback should only send a text message, photo fallbacks are handled above
                message = bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                return message.message_id
            except telebot.apihelper.ApiException as e_send:
                logger.error(f"Telegram API Exception on fallback send_message: {e_send}")
                return None
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred in send_or_edit_message (outer try): {e}", exc_info=True)
        return None

def delete_message(bot, chat_id, message_id):
    """
    Deletes a message.
    Returns True on success, False on failure.
    """
    if not message_id:
        logger.warning("delete_message called with no message_id.")
        return False
    try:
        bot.delete_message(chat_id, message_id)
        return True
    except telebot.apihelper.ApiException as e:
        # Common errors: "message to delete not found", "message can't be deleted"
        logger.warning(f"Could not delete message {message_id} in chat {chat_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred in delete_message: {e}", exc_info=True)
        return False

if __name__ == '__main__':
    # This section is for illustrative purposes.
    # To run this, you'd need to set up a mock bot or a real bot token.
    print("Message utilities defined. To test, you would need a TeleBot instance.")
    # Example (conceptual):
    # mock_bot = telebot.TeleBot("YOUR_TOKEN") # Replace with your token if testing for real
    # chat_id_test = 123456789 # Replace with your chat_id

    # print("Simulating send_or_edit_message (new text message)...")
    # new_msg_id = send_or_edit_message(mock_bot, chat_id_test, "Hello from message_utils!")
    # if new_msg_id:
    #     print(f"Message sent, ID: {new_msg_id}")
    #     time.sleep(2)
    #     print("Simulating send_or_edit_message (editing text message)...")
    #     edited_msg_id = send_or_edit_message(mock_bot, chat_id_test, "Hello again (edited)!", existing_message_id=new_msg_id)
    #     if edited_msg_id:
    #         print(f"Message edited, ID: {edited_msg_id}")
    #         time.sleep(2)
    #         print("Simulating delete_message...")
    #         if delete_message(mock_bot, chat_id_test, edited_msg_id):
    #             print("Message deleted successfully.")
    # else:
    #     print("Failed to send initial message.")

    # print("\nConsider adding more comprehensive mock tests for different scenarios,")
    # print("especially for photo handling and various API error conditions.")

def send_loading_acknowledgment(bot, chat_id, callback_query_id=None, message_object=None):
    """
    Sends a loading acknowledgment to the user.
    Uses answer_callback_query if callback_query_id is provided,
    and send_chat_action for a 'typing...' indicator.
    """
    try:
        if callback_query_id:
            bot.answer_callback_query(callback_query_id, text="Processing...")

        # Show "typing..." status
        bot.send_chat_action(chat_id, action='typing')

    except telebot.apihelper.ApiException as e:
        logger.warning(f"Telegram API Exception in send_loading_acknowledgment: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred in send_loading_acknowledgment: {e}", exc_info=True)

# Example usage within the module (for illustration or direct testing if needed)
if __name__ == '__main__':
    # ... (previous __main__ content for send_or_edit_message and delete_message) ...

    print("\nIllustrative test for send_loading_acknowledgment (requires mock bot):")
    # mock_bot = telebot.TeleBot("YOUR_TOKEN") # Replace with your token
    # chat_id_test = 123456789 # Replace with your chat_id
    # mock_callback_query_id = "dummy_query_id" # Dummy ID for testing

    # print("Simulating send_loading_acknowledgment with callback_query_id...")
    # send_loading_acknowledgment(mock_bot, chat_id_test, callback_query_id=mock_callback_query_id)
    # print("Check Telegram for 'Processing...' popup and 'typing...' status.")
    # time.sleep(3) # Keep alive for visual check

    # print("\nSimulating send_loading_acknowledgment without callback_query_id (typing only)...")
    # send_loading_acknowledgment(mock_bot, chat_id_test)
    # print("Check Telegram for 'typing...' status.")
    # time.sleep(3)
