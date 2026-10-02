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

    # Sesiones de panel revocadas (logout). Los tokens son sin estado, así que
    # el servidor guarda el SHA-256 de los que ya no debe aceptar. Nunca el
    # token en claro. Las filas se purgan cuando el token caduca por sí solo.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS revoked_sessions (
            token_hash TEXT PRIMARY KEY,
            expires_at INTEGER NOT NULL,
            revoked_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Estado de la credencial del panel. Una sola fila logica (CHECK id = 1).
    #
    # Hasta ahora el hash vivia unicamente en el .env, leido al importar: para
    # cambiar la contrasena habia que editar un archivo y reiniciar el
    # servidor, cortando a todos los Agents. Aqui se puede cambiar en
    # caliente.
    #
    # sessions_valid_from es la fecha de corte de sesiones, en segundos epoch.
    # Los tokens emitidos antes dejan de valer. Es la unica forma de cerrar
    # TODAS las sesiones de golpe: los tokens son sin estado y no se pueden
    # enumerar, asi que no hay lista que recorrer. 0 = nunca se ha cortado.
    #
    # El hash nunca se imprime ni se registra en ninguna parte.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS auth_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            password_hash TEXT NOT NULL,
            password_changed_at TEXT,
            sessions_valid_from INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Usuarios del panel. Migracion aditiva: la cuenta unica anterior se
    # convierte en el owner conservando su hash (backend/users.py).
    #
    # username es unico sin distinguir mayusculas: "Odette" y "odette" no
    # pueden ser dos cuentas distintas, seria una trampa evidente.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            email TEXT,
            email_verified INTEGER NOT NULL DEFAULT 0,
            role TEXT NOT NULL DEFAULT 'subadmin',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            password_changed_at TEXT,
            sessions_valid_from INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Permisos concedidos a cada usuario. Una fila por permiso: anadir uno
    # nuevo no obliga a migrar ninguna columna.
    #
    # El owner no aparece aqui: tiene control total por su rol, no por una
    # lista que alguien pudiera vaciar por error.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS user_permissions (
            user_id INTEGER NOT NULL,
            permission TEXT NOT NULL,
            PRIMARY KEY (user_id, permission)
        )
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_permissions_user
        ON user_permissions (user_id)
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_email
        ON users (email)
    """)

    # Enlaces de recuperacion de contrasena.
    #
    # Solo se guarda el SHA-256 del token: quien lea esta tabla no puede
    # fabricar un enlace valido. Caducidad corta y un solo uso; used_at
    # marca el momento en que se gasto o se anulo.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used_at INTEGER
        )
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_reset_tokens_user
        ON password_reset_tokens (user_id)
    """)

    # Programacion de grabacion por equipo. Una fila por dispositivo.
    #
    # Los dias se guardan como una cadena de numeros separados por comas
    # (0 = lunes ... 6 = domingo) para no crear otra tabla por algo que
    # siempre se lee y se escribe entero.
    #
    # timezone se guarda junto al horario: "de 8 a 17" no significa nada
    # sin saber de donde es ese reloj.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS recording_schedule (
            device_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            start_time TEXT NOT NULL DEFAULT '08:00',
            end_time TEXT NOT NULL DEFAULT '17:00',
            days TEXT NOT NULL DEFAULT '0,1,2,3,4',
            timezone TEXT NOT NULL DEFAULT 'local',
            updated_at TEXT
        )
    """)

    # Registro de auditoría: una fila por acción administrativa solicitada.
    #
    # Responde a "quién pidió qué, sobre qué equipo, desde dónde y cuándo".
    # Hoy hay una sola cuenta, así que username siempre será la misma, pero
    # la columna existe desde el principio para que el día que haya varios
    # usuarios no haya que migrar el historial.
    #
    # Nunca se guardan contraseñas, tokens, cookies ni contenido de archivos.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            device_id TEXT,
            username TEXT,
            source_ip TEXT,
            status TEXT NOT NULL,
            details TEXT
        )
    """)

    # Consultas habituales: los últimos registros, por equipo y por acción
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp
        ON audit_log (timestamp DESC)
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_device
        ON audit_log (device_id)
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_action
        ON audit_log (action)
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

    # Motivo por el que una grabación quedó en cuarentena (status='invalid').
    # Aditiva: las filas existentes quedan con NULL y no se tocan.
    columnas_recordings = {
        column["name"]
        for column in connection.execute("PRAGMA table_info(recordings)")
    }

    if "invalid_reason" not in columnas_recordings:
        connection.execute(
            "ALTER TABLE recordings ADD COLUMN invalid_reason TEXT"
        )

    # Una grabación = un archivo. El índice impide que dos subidas
    # simultáneas del mismo segmento creen varias filas para el mismo
    # archivo: la comprobación previa del endpoint es solo un atajo, la
    # garantía real está aquí.
    #
    # Si la base tuviera duplicados previos, CREATE UNIQUE INDEX falla y se
    # deja constancia en vez de romper el arranque: los datos existentes no
    # se tocan nunca.
    try:
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_recordings_device_path
            ON recordings (device_id, path)
        """)

    except sqlite3.IntegrityError as error:
        print(
            "[db] No se pudo crear el índice único de grabaciones porque hay "
            f"filas duplicadas (device_id, path): {error}. "
            "Revísalas manualmente; no se ha borrado nada."
        )

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
        "last_alert_status": "TEXT",
        "continuous_recording": "INTEGER DEFAULT 0",

        # Credencial individual del Agent (token propio por dispositivo).
        # Se guarda SOLO el SHA-256 del token: el valor en claro se entrega
        # una única vez al Agent en el alta y no se conserva en el servidor.
        "agent_token_hash": "TEXT",
        "agent_token_issued_at": "TEXT",

        # Salud del equipo: último estado de RAM y disco por el que YA se
        # avisó (ok / warning / critical). NULL = sin observación previa.
        # Van separados porque son recursos independientes: el disco puede
        # estar crítico con la RAM correcta, y cada uno se recupera aparte.
        "last_ram_status": "TEXT",
        "last_disk_status": "TEXT",
        # 1 = activo, 0 = revocado. NULL en dispositivos aún sin token
        # individual (los registrados con el esquema anterior).
        "agent_token_active": "INTEGER"
    }

    for column_name, column_type in new_columns.items():
        if column_name not in column_names:
            connection.execute(
                f"ALTER TABLE devices ADD COLUMN {column_name} {column_type}"
            )

    connection.commit()
    connection.close()