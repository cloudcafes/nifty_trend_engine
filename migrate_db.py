import sqlite3
from config import DB_NAME

def migrate_database():
    print(f"Migrating {DB_NAME} to preserve backtesting data...\n")
    
    # Connect to your existing database
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # List of all the new state variables we added recently
    # Format: (Column Name, SQLite Data Type, Default Value)
    columns_to_add = [
        ("cooldown_bars", "INTEGER", "0"),
        ("call_bias_bars", "INTEGER", "0"),
        ("put_bias_bars", "INTEGER", "0"),
        ("pcr_strong_call_bars", "INTEGER", "0"),
        ("pcr_strong_put_bars", "INTEGER", "0"),
        ("consecutive_opposite_bias", "INTEGER", "0"),
        ("exit_confirm_bars", "INTEGER", "0"),
        ("pcr_history", "TEXT", "'[]'"),
        ("oi_bias_history", "TEXT", "'[]'")
    ]

    for col_name, col_type, default_val in columns_to_add:
        try:
            # SQL command to add a new column to an existing table
            query = f"ALTER TABLE engine_state ADD COLUMN {col_name} {col_type} DEFAULT {default_val}"
            cursor.execute(query)
            print(f"✅ Added missing column: {col_name}")
            
        except sqlite3.OperationalError as e:
            # If the column already exists, SQLite will throw an error. We just catch it and skip.
            if "duplicate column name" in str(e).lower():
                print(f"⏩ Column already exists (skipping): {col_name}")
            else:
                print(f"❌ Error adding {col_name}: {e}")

    # Save changes and close
    conn.commit()
    conn.close()
    
    print("\n🎉 Migration complete! You can now run main.py or replay.py safely.")

if __name__ == "__main__":
    migrate_database()