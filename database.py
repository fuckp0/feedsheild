import sqlite3

DB_PATH = "D:/NewApp/backend/crm.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Drop any existing 'users' table
try:
    cursor.execute("DROP TABLE IF EXISTS users")
    conn.commit()
    print("Dropped 'users' table if it existed")
except sqlite3.Error as e:
    print(f"Error dropping 'users' table: {str(e)}")

# Drop any index that might conflict (e.g., named 'users')
try:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'users%';")
    indexes = cursor.fetchall()
    for index in indexes:
        cursor.execute(f"DROP INDEX IF EXISTS {index['name']}")
        print(f"Dropped index: {index['name']}")
    conn.commit()
except sqlite3.Error as e:
    print(f"Error dropping indexes: {str(e)}")

# Rename 'Users' to 'users'
try:
    cursor.execute("ALTER TABLE Users RENAME TO users")
    conn.commit()
    print("Renamed 'Users' to 'users'")
except sqlite3.Error as e:
    print(f"Error renaming 'Users' to 'users': {str(e)}")

# Verify tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
print("Existing tables:", [table[0] for table in tables])

# Verify schema for 'users'
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users';")
schema = cursor.fetchone()
print("Schema for users:", schema['sql'] if schema else "Not found")

# Verify indexes
cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
indexes = cursor.fetchall()
print("Indexes:", [index[0] for index in indexes])

conn.close()