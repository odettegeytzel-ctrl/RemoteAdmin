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
    connection.execute("""
        CREATE TABLE IF NOT EXISTS installed_software (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            name TEXT NOT NULL,
            version TEXT,
            publisher TEXT,
            install_date TEXT
        )
    """)

    # Tabla de alertas: una fila por cambio de estado de un dispositivo
    connection.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            hostname TEXT,
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabla de configuracion: pares clave/valor
    connection.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Tabla de grabaciones: una fila por segmento MP4
    connection.execute("""
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            duration_sec INTEGER,
            size_bytes INTEGER,
            path TEXT NOT NULL,
            status TEXT DEFAULT 'stored',
            keep INTEGER DEFAULT 0,
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
        "cpu_count": "INTEGER",
        "ram_total": "INTEGER",
        "ram_available": "INTEGER",
        "ram_used": "INTEGER",
        "ram_percent": "REAL",
        "storage_total": "INTEGER",
        "storage_free": "INTEGER",
        "storage_used": "INTEGER",
        "windows_version": "TEXT",
        "architecture": "TEXT",
        "manufacturer": "TEXT",
        "model": "TEXT",
        "last_alert_status": "TEXT"
    }

    for column_name, column_type in new_columns.items():
        if column_name not in column_names:
            connection.execute(
                f"ALTER TABLE devices ADD COLUMN {column_name} {column_type}"
            )

    connection.commit()
    connection.close()