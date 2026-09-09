import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "remoteadmin.db"


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    return connection


def init_db():
    connection = get_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT UNIQUE NOT NULL,
            hostname TEXT NOT NULL,
            operating_system TEXT,
            ip_address TEXT,
            username TEXT,
            processor TEXT,
            cpu_count INTEGER,
            status TEXT DEFAULT 'offline',
            last_seen TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    existing_columns = connection.execute(
        "PRAGMA table_info(devices)"
    ).fetchall()

    column_names = {
        column["name"]
        for column in existing_columns
    }

    new_columns = {
        "username": "TEXT",
        "processor": "TEXT",
        "cpu_count": "INTEGER"
    }

    for column_name, column_type in new_columns.items():
        if column_name not in column_names:
            connection.execute(
                f"ALTER TABLE devices ADD COLUMN {column_name} {column_type}"
            )

    connection.commit()
    connection.close()