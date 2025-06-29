# Telegram E-Commerce Bot with HD Wallet Crypto Payments

This is a Python-based Telegram bot designed for e-commerce. It allows users to browse items, purchase them using cryptocurrencies (BTC, LTC, USDT-TRC20) via Hierarchical Deterministic (HD) wallets, manage their account balance, and interact with a support ticket system. Administrators have functionalities for managing items, users, and support tickets.

## Features

*   **User Features:**
    *   Browse items categorized by city.
    *   View item details (description, price, images).
    *   Purchase items using supported cryptocurrencies (BTC, LTC, USDT TRC20).
        *   Partial payment from internal balance is supported if available.
        *   Unique payment address generated for each transaction.
        *   Automatic payment confirmation and order fulfillment.
    *   Add funds to an internal balance using supported cryptocurrencies.
    *   View account overview: current balance and transaction history (paginated).
    *   Integrated support ticket system: create tickets, send messages, and close tickets.
*   **Admin Features (Restricted to `ADMIN_ID`):**
    *   View and reply to support tickets.
    *   Close support tickets.
    *   Manage item catalog:
        *   Add new item types (city, name, description, price, images for the first instance).
        *   Edit existing item details (name, price, city).
        *   Toggle item availability.
        *   Delete item types (removes from filesystem and database).
    *   Manage users:
        *   View a list of all registered users with pagination.
        *   View detailed information for a specific user (ID, balance, transaction count, transaction history).
        *   Adjust a user's balance with a mandatory reason for auditing.

## Setup

1.  **Prerequisites:**
    *   Python 3.8 or higher.
    *   `pip` for package installation.
    *   `virtualenv` (recommended).

2.  **Clone the Repository:**
    ```bash
    git clone <your_repository_url>
    cd <your_repository_directory>
    ```

3.  **Create and Activate Virtual Environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

4.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

5.  **Configuration (`config.py` and Environment Variables):**

    It is **highly recommended** to use environment variables for sensitive information in a production environment.

    *   **Required:**
        *   `BOT_TOKEN`: Your Telegram Bot Token from BotFather.
            *   Set in `config.py` OR as environment variable `TELEGRAM_BOT_TOKEN`.
        *   `ADMIN_ID`: Your numerical Telegram User ID.
            *   Set in `config.py` OR as environment variable `TELEGRAM_ADMIN_ID`.
        *   `SEED_PHRASE`: **CRITICAL SECURITY WARNING!** This is your BIP39 mnemonic for the HD wallet.
            *   **DO NOT store valuable seed phrases directly in `config.py` for production.**
            *   Set as environment variable `HD_WALLET_SEED_PHRASE`.
            *   If using `config.py` for development:
                *   NEVER use a seed phrase that holds significant real funds.
                *   Use a testnet seed phrase or one with negligible value.
                *   Ensure `config.py` has strict file permissions (e.g., `chmod 600 config.py`).

    *   **Optional (for enhanced functionality/rate limits):**
        *   `BLOCKCYPHER_API_TOKEN`: API token for BlockCypher.
            *   Set in `config.py` OR as environment variable `BLOCKCYPHER_TOKEN`.
        *   `TRONGRID_API_KEY`: API key for TronGrid.
            *   Set in `config.py` OR as environment variable `TRONGRID_APIKEY`.

    *   **Other settings in `config.py` (can be left as defaults or customized):**
        *   `SERVICE_FEE_EUR`, `ADD_BALANCE_SERVICE_FEE_EUR`
        *   `PAYMENT_WINDOW_MINUTES`
        *   `MIN_CONFIRMATIONS_BTC`, `MIN_CONFIRMATIONS_LTC`, `MIN_CONFIRMATIONS_TRX`
        *   `USDT_TRC20_CONTRACT_ADDRESS` (pre-configured for mainnet)
        *   Scheduler intervals (see comments in `config.py`)
        *   `DATABASE_NAME`, `ITEMS_BASE_DIR`, `PURCHASED_ITEMS_BASE_DIR`
        *   `ACCOUNT_IMAGE_PATH`, `BUY_FLOW_IMAGE_PATH`

    **To use environment variables (recommended for sensitive data):**
    You would need to modify `config.py` or `bot.py` to read these, for example:
    ```python
    # In config.py or bot.py
    import os
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_FALLBACK_TOKEN_IF_ANY')
    ADMIN_ID = os.environ.get('TELEGRAM_ADMIN_ID', 'YOUR_FALLBACK_ADMIN_ID_IF_ANY')
    SEED_PHRASE = os.environ.get('HD_WALLET_SEED_PHRASE', 'default test seed phrase ...')
    # etc.
    ```
    *(Note: The bot code currently reads directly from `config.py` variables. Modification is needed to prioritize environment variables.)*

6.  **Directory Structure for Items:**
    The bot expects item data to be structured under `data/items/` as follows:
    ```
    data/
      items/
        CityA/
          ItemTypeX/  <-- Product Type Folder (e.g., PizzaLarge)
            instance_001/
              description.txt
              image1.jpg
            instance_002/
              description.txt
        CityB/
          ...
    ```
    *   Each subfolder under a `City` is a **Product Type**.
    *   Each subfolder under a `ProductType` is an **Instance** of that item.
    *   Each instance folder *must* contain a `description.txt` file and can contain image files.
    *   The bot serves details from the alphabetically first available instance.

7.  **Initial Run & Data Sync:**
    *   Ensure the `data/items/` directory is structured correctly if pre-loading items.
    *   On first run, the bot will create `data/database/bot_database.db` if it doesn't exist and perform an initial sync from the `data/items/` filesystem to the database.

8.  **Run the Bot:**
    ```bash
    python main.py
    ```

## Project Structure

*   `bot.py`: Main application script.
*   `config.py`: Configuration variables.
*   `requirements.txt`: Dependencies.
*   `handlers/`: Telegram command/callback handlers.
*   `modules/`: Core logic utilities.
*   `data/`: For database, item files, logs.
    *   `items/`, `purchased_items/`, `database/`
*   `bot_activity.log`: Log file.

## Usage

### User Interaction

1.  **Start**: Send `/start`.
2.  **Browse & Buy Items**: Via "Browse Items 🛍️".
3.  **Add Balance**: Via "Account 🏦" -> "Add Balance 💰".
4.  **View Account**: Via "Account 🏦" -> "View Account Info ℹ️".
5.  **Support**: Via "Support 💬".

### Admin Commands (Restricted)

*   `/start`: Accesses main menu (may show admin panel).
*   `/tickets`: List and manage support tickets.
*   `/additem`: Add new item types.
*   `/edititem`: Edit existing item types (name, price, city, availability).
*   `/deleteitem`: Delete item types.
*   `/viewusers`: View users, their details, and adjust balances.
*   `/cancel_admin_action`: Cancels multi-step admin operations like adding/editing items or replying to tickets.

## Deployment Considerations

1.  **Security is Paramount:**
    *   **`SEED_PHRASE` Protection**: This is the most critical aspect. **DO NOT hardcode a valuable production seed phrase in `config.py`.**
        *   **Use Environment Variables:** Store `BOT_TOKEN`, `ADMIN_ID`, and especially `SEED_PHRASE` as environment variables on your server. Modify `config.py` or `bot.py` to read them using `os.environ.get()`.
        *   **Vaults/Secrets Management:** For higher security, use a secrets management tool (e.g., HashiCorp Vault, AWS Secrets Manager).
        *   **Hardware Wallets/Dedicated Services:** For significant funds, consider solutions where the private keys never touch the bot server directly.
    *   **File Permissions**: If `config.py` must contain any data, ensure it has restrictive permissions (e.g., `chmod 600 config.py`). The `data/` directory, especially `data/database/bot_database.db`, should also be protected.
    *   **Server Security**: Keep your server operating system and software updated. Use firewalls and minimize exposed services.

2.  **Process Management:**
    To ensure the bot runs continuously and restarts on failure:
    *   **`systemd` (Linux - Recommended):** Create a service file.
        Example (`mybot.service` in `/etc/systemd/system/`):
        ```ini
        [Unit]
        Description=My Telegram E-Commerce Bot
        After=network.target

        [Service]
        User=your_user                 # User the bot will run as
        Group=your_group               # Group for the bot
        WorkingDirectory=/path/to/your_bot_directory
        Environment="TELEGRAM_BOT_TOKEN=your_actual_token"
        Environment="TELEGRAM_ADMIN_ID=your_actual_admin_id"
        Environment="HD_WALLET_SEED_PHRASE=your actual seed phrase here"
        # Add other environment variables as needed (API keys, etc.)
        ExecStart=/path/to/your_bot_directory/venv/bin/python bot.py
        Restart=always
        RestartSec=10
        StandardOutput=append:/path/to/your_bot_directory/data/logs/bot_stdout.log
        StandardError=append:/path/to/your_bot_directory/data/logs/bot_stderr.log

        [Install]
        WantedBy=multi-user.target
        ```
        Then enable and start:
        ```bash
        sudo systemctl daemon-reload
        sudo systemctl enable mybot.service
        sudo systemctl start mybot.service
        sudo systemctl status mybot.service
        ```
    *   **`supervisor`**: Another robust process control system.
    *   **`screen` / `tmux`**: Simpler for basic persistence, but less robust for automatic restarts and management.
        ```bash
        screen -S bot_session  # Start a new screen session
        # Navigate to your bot directory, activate venv
        python bot.py
        # Detach: Ctrl+A then D
        # Reattach: screen -r bot_session
        ```

3.  **Data Directory (`data/`)**:
    *   The `data/` directory (database, items, logs, purchased items) is stateful. Ensure it's on persistent storage.
    *   **Backup Strategy**: Implement regular, automated backups of the `data/database/bot_database.db` file and the `data/items/` directory. Store backups securely and preferably off-site.

4.  **Logging**:
    *   The bot logs to `bot_activity.log` (and `data/logs/` if using the `systemd` example).
    *   Monitor these logs for errors, warnings, and operational status.
    *   For production, consider setting up log rotation (e.g., using `logrotate` on Linux) to manage log file sizes.

5.  **API Keys**:
    *   If using public blockchain explorers for extended periods or high volume, obtain API keys (e.g., BlockCypher, TronGrid) and provide them via environment variables (preferred) or `config.py` to avoid rate limiting.

## Troubleshooting

*   **"HD Wallet seed phrase is invalid"**: Ensure `SEED_PHRASE` (preferably from env var) is a valid 12 or 24-word BIP39 mnemonic.
*   **Payments not detected**:
    *   Check `bot_activity.log` (and `stdout`/`stderr` if using `systemd`) for errors from `payment_monitor.py` or `blockchain_apis.py`.
    *   Ensure the correct `MIN_CONFIRMATIONS_` are set in `config.py`.
    *   Verify API keys (if used) are correct and active.
    *   Check server network connectivity and DNS resolution.
*   **Admin commands not working**: Ensure your `ADMIN_ID` (preferably from env var) is correct and is your numerical Telegram User ID.
*   **File/Directory Permissions**: If the bot fails to write to the `data/` directory or log files, check filesystem permissions for the user the bot is running as.
```
