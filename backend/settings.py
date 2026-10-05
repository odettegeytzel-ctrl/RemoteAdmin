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


def get_retention_days(organization_id=None):
    """
    Dias de retencion configurados, acotados a los valores permitidos.

    Son por organizacion: cada empresa decide cuanto conserva sus
    grabaciones. Sin organizacion se devuelven los de la instalacion,
    que es lo que corresponde a una tarea global.
    """

    valor = get_settings(organization_id).get("recording_retention_days")

    if str(valor) not in RETENTION_CHOICES:
        return int(DEFAULT_SETTINGS["recording_retention_days"])

    return int(valor)


def get_settings(organization_id=None):
    """
    Configuracion efectiva.

    Con organizacion, los valores de esa empresa sobre los de la
    instalacion; sin ella, solo los de la instalacion. Asi una empresa
    que no ha tocado nada hereda los valores por defecto en lugar de
    quedarse sin configuracion.
    """

    connection = get_connection()

    try:

        result = dict(DEFAULT_SETTINGS)

        for row in connection.execute("SELECT key, value FROM settings"):
            result[row["key"]] = row["value"]

        if organization_id is not None:

            for row in connection.execute(
                "SELECT key, value FROM organization_settings "
                "WHERE organization_id = ?",
                (organization_id,)
            ):
                result[row["key"]] = row["value"]

    finally:
        connection.close()

    return result


def save_settings(values, organization_id=None):
    """
    Guarda ajustes.

    Con organizacion se escriben en SU tabla y no tocan a nadie mas:
    cambiar lo de una empresa no puede cambiar lo de otra. Sin
    organizacion se escriben los de la instalacion, reservado a la
    plataforma.

    La organizacion la decide SIEMPRE quien llama a partir de la sesion;
    nunca llega del cuerpo de la peticion.
    """

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

    try:

        for key, value in values.items():

            if organization_id is None:
                connection.execute(
                    """
                    INSERT INTO settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, str(value))
                )

            else:
                connection.execute(
                    """
                    INSERT INTO organization_settings (
                        organization_id, key, value
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(organization_id, key)
                    DO UPDATE SET value = excluded.value
                    """,
                    (organization_id, key, str(value))
                )

        connection.commit()

    finally:
        connection.close()

    return {"status": "saved"}


def migrate_settings_to_organization(organization_id):
    """
    Lleva la configuracion existente a la primera organizacion.

    Los valores guardados hasta ahora eran de la unica empresa que
    habia, asi que le pertenecen. Se copian, no se mueven: la tabla
    global conserva lo suyo y nada se pierde.

    Idempotente: no pisa un valor que la organizacion ya tenga propio.
    """

    if not organization_id:
        return False

    connection = get_connection()

    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO organization_settings (
                organization_id, key, value
            )
            SELECT ?, key, value FROM settings
            """,
            (organization_id,)
        )

        connection.commit()

    except Exception as error:
        print(f"[ajustes] No se pudo migrar la configuracion: {error}")

    finally:
        connection.close()

    return True
