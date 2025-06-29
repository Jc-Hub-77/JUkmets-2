import logging

logger = logging.getLogger(__name__)

# --- User State Management ---
user_states = {}

def update_user_state(user_id, key, value):
    if user_id not in user_states:
        user_states[user_id] = {}
    user_states[user_id][key] = value
    logger.debug(f"State updated for user {user_id}: {key} = {value}")

def get_user_state(user_id, key, default=None):
    return user_states.get(user_id, {}).get(key, default)

def clear_user_state(user_id):
    if user_id in user_states:
        logger.info(f"Clearing state for user {user_id}. Old state: {user_states[user_id]}")
        del user_states[user_id]
    else:
        logger.info(f"No state to clear for user {user_id}.")
# --- End User State Management ---
