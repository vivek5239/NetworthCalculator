from sqlalchemy import create_engine, text
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.getenv('DB_FILE_PATH', os.path.join(BASE_DIR, 'finance.db'))
DATABASE_URL = f"sqlite:///{DB_FILE}"

engine = create_engine(DATABASE_URL)

def run_migration():
    print(f"Checking database at {DB_FILE}...")
    with engine.connect() as conn:
        # 1. Check Assets table
        columns = conn.execute(text("PRAGMA table_info(assets)")).fetchall()
        col_names = [c[1] for c in columns]
        
        updates = [
            ("daily_change_pct", "FLOAT"),
            ("original_unit_price", "FLOAT"),
            ("original_currency", "VARCHAR"),
            ("price_30d", "FLOAT"),
            ("last_updated", "DATETIME") # Should exist but checking
        ]
        
        for col, type_ in updates:
            if col not in col_names:
                print(f"Adding column '{col}' to assets table...")
                try:
                    conn.execute(text(f"ALTER TABLE assets ADD COLUMN {col} {type_}"))
                except Exception as e:
                    print(f"Error adding {col}: {e}")

        # 2. Check App Settings
        columns = conn.execute(text("PRAGMA table_info(app_settings)")).fetchall()
        col_names = [c[1] for c in columns]
        
        updates = [
            ("gotify_enabled", "BOOLEAN DEFAULT 0"),
            ("gotify_url", "VARCHAR"),
            ("gotify_token", "VARCHAR"),
            ("groq_api_key", "VARCHAR"),
            ("ai_context_columns", "VARCHAR"),
            ("report_time", "VARCHAR DEFAULT '18:00'"),
            ("last_run_date", "DATE"),
            ("notification_threshold", "FLOAT DEFAULT 5.0")
        ]
        
        for col, type_ in updates:
            if col not in col_names:
                print(f"Adding column '{col}' to app_settings table...")
                try:
                    conn.execute(text(f"ALTER TABLE app_settings ADD COLUMN {col} {type_}"))
                except Exception as e:
                    print(f"Error adding {col}: {e}")

        # 3. Check Investment Transactions
        columns = conn.execute(text("PRAGMA table_info(investment_transactions)")).fetchall()
        col_names = [c[1] for c in columns]
        
        if "quantity_change" not in col_names:
             print("Adding 'quantity_change' to investment_transactions...")
             # SQLite doesn't support renaming columns easily in older versions, 
             # but we can add the new one.
             # If 'quantity' exists but 'quantity_change' doesn't, we might want to migrate data?
             # For now, just add the column to prevent crashes.
             try:
                conn.execute(text("ALTER TABLE investment_transactions ADD COLUMN quantity_change FLOAT DEFAULT 0"))
             except Exception as e:
                print(f"Error adding quantity_change: {e}")

        conn.commit()
        print("Database schema check complete.")

if __name__ == "__main__":
    run_migration()
