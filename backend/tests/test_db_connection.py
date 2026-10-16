import sys
from pathlib import Path

# Add backend directory to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text
from app.config import settings
from app.database import engine

def test_connection():
    print(f"Connecting to MySQL database '{settings.DB_NAME}' on {settings.DB_HOST}:{settings.DB_PORT} as '{settings.DB_USER}'...")
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT VERSION()"))
            version = result.scalar()
            print(f"[SUCCESS] Connected to MySQL successfully! Version: {version}")
            
            # Check existing tables in smart_parking_db
            tables = conn.execute(text("SHOW TABLES")).fetchall()
            table_names = [t[0] for t in tables]
            print(f"[INFO] Tables found ({len(table_names)}): {table_names}")
            
            if len(table_names) == 0:
                print("[WARNING] No tables found in database. Please run database/schema.sql and database/seed.sql!")
            else:
                print(f"[SUCCESS] Database '{settings.DB_NAME}' is ready with {len(table_names)} tables!")
            
            print("[SUCCESS] Milestone 3.1 Verification Passed!")
            return True
    except Exception as e:
        print(f"[ERROR] Database connection failed: {e}")
        return False

if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
