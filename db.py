import sqlite3
import os
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database configuration
DB_PATH = "D:/NewApp/backend/crm.db"

def update_database():
    """
    Update the SQLite database schema by adding missing columns and ensuring all tables exist.
    """
    try:
        # Ensure the database directory exists
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)
            logger.info(f"Created database directory: {db_dir}")

        # Connect to the database
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Step 1: Ensure all required tables exist
        # Users table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cursor.fetchone():
            logger.info("Creating 'users' table")
            cursor.execute('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    stripe_customer_id TEXT
                )
            ''')

        # Accounts table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'")
        if not cursor.fetchone():
            logger.info("Creating 'accounts' table")
            cursor.execute('''
                CREATE TABLE accounts (
                    user_id INTEGER,
                    username TEXT,
                    is_connected BOOLEAN DEFAULT FALSE,
                    PRIMARY KEY (user_id, username),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')
        else:
            # Check if is_connected column exists in accounts table
            cursor.execute("PRAGMA table_info(accounts)")
            columns = [col["name"] for col in cursor.fetchall()]
            if "is_connected" not in columns:
                logger.info("Adding 'is_connected' column to 'accounts' table")
                cursor.execute("ALTER TABLE accounts ADD COLUMN is_connected BOOLEAN")
                cursor.execute("UPDATE accounts SET is_connected = 0")  # Set default for existing rows
            else:
                logger.info("'is_connected' column already exists in 'accounts' table")

        # Blocked accounts table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='blocked_accounts'")
        if not cursor.fetchone():
            logger.info("Creating 'blocked_accounts' table")
            cursor.execute('''
                CREATE TABLE blocked_accounts (
                    user_id INTEGER,
                    username TEXT,
                    PRIMARY KEY (user_id, username),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')

        # Payment history table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payment_history'")
        if not cursor.fetchone():
            logger.info("Creating 'payment_history' table")
            cursor.execute('''
                CREATE TABLE payment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount REAL NOT NULL,
                    package TEXT NOT NULL,
                    date TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')

        # Step 2: Verify the schema
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row["name"] for row in cursor.fetchall()]
        logger.info(f"Tables in database: {tables}")

        # Verify columns in accounts table
        cursor.execute("PRAGMA table_info(accounts)")
        account_columns = [col["name"] for col in cursor.fetchall()]
        logger.info(f"Columns in 'accounts' table: {account_columns}")

        # Commit changes
        conn.commit()
        logger.info("Database schema updated successfully")

    except sqlite3.Error as e:
        logger.error(f"Failed to update database: {str(e)}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    logger.info("Starting database update script")
    update_database()
    logger.info("Database update script completed")