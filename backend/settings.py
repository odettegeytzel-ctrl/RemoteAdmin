from backend.database import get_connection


# Valores por defecto que se muestran si no hay nada guardado
DEFAULT_SETTINGS = {
    "server_name": "RemoteAdmin",
    "offline_after_seconds": "30",
    "alerts_enabled": "true",

    # Umbrales de salud (porcentaje de uso). Se guardan como texto, igual que
    # el resto de settings; quien los consuma es responsable de convertirlos.
    # RAM y disco van por separado: un 85% de RAM es normal en Windows,
    # mientras que un 85% de disco sí merece atención.
    "ram_warning_percent": "75",
    "ram_critical_percent": "90",
    "disk_warning_percent": "75",
    "disk_critical_percent": "90",

    # Dias que se conservan las grabaciones antes de la limpieza
    # automatica. Configurable: un periodo fijo obliga a tocar el codigo
    # cada vez que cambia la politica de la casa.
    "recording_retention_days": "90"
}

# Valores admitidos para la retencion. Lista cerrada: evita que un "0" o un
# "1" mal tecleados conviertan la limpieza en un borrado masivo.
RETENTION_CHOICES = ("15", "30", "90")


# Unicas claves que se pueden escribir. Lista blanca explicita, no una
# lista negra: una clave nueva desconocida se rechaza por defecto.
#
# Importa mas de lo que parece. Este endpoint aceptaba cualquier diccionario
# y escribia lo que fuera en la tabla settings. Mientras solo hubiera
# umbrales daba igual, pero el dia que un estado de autenticacion acabara
# ahi, "guardar ajustes" seria "cambiar la credencial sin saber la actual".
# La credencial vive en auth_state, que esta funcion no toca, y ademas
# ninguna clave de fuera de esta lista llega a escribirse.
ALLOWED_SETTINGS = frozenset(DEFAULT_SETTINGS)


class UnknownSettingError(ValueError):
    """Se intento guardar una clave que no esta permitida."""

    def __init__(self, keys):
        self.keys = sorted(keys)

        super().__init__(
            "Ajustes no reconocidos: " + ", ".join(self.keys)
        )


def get_retention_days():
    """Dias de retencion configurados, acotados a los valores permitidos."""

    valor = get_settings().get("recording_retention_days")

    if str(valor) not in RETENTION_CHOICES:
        return int(DEFAULT_SETTINGS["recording_retention_days"])

    return int(valor)


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

    if not isinstance(values, dict):
        raise UnknownSettingError(["(cuerpo no valido)"])

    # Se rechaza la peticion ENTERA si hay una clave desconocida, en vez de
    # ignorarla en silencio: guardar a medias sin avisar es peor que fallar.
    desconocidas = set(values) - ALLOWED_SETTINGS

    if desconocidas:
        raise UnknownSettingError(desconocidas)

    retencion = values.get("recording_retention_days")

    if retencion is not None and str(retencion) not in RETENTION_CHOICES:
        raise UnknownSettingError(
            ["recording_retention_days (solo "
             + ", ".join(RETENTION_CHOICES) + ")"]
        )

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
