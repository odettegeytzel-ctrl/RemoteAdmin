from backend.database import get_connection


# Valores por defecto que se muestran si no hay nada guardado
DEFAULT_SETTINGS = {
    "server_name": "RemoteAdmin",
    "offline_after_seconds": "30",
    "alerts_enabled": "true"
}


def get_settings():
    connection = get_connection()

    rows = connection.execute(
        "SELECT key, value FROM settings"
    ).fetchall()

    connection.close()

    result = dict(DEFAULT_SETTINGS)

    for row in rows:
        result[row["key"]] = row["value"]

    return result


def save_settings(values):
    connection = get_connection()

    for key, value in values.items():
        connection.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value))
        )

    connection.commit()
    connection.close()

    return {"status": "saved"}
