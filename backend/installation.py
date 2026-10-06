"""
Identidad de esta instalacion de RemoteAdmin.

Una instalacion no es una organizacion. En la modalidad cloud hay una
sola instalacion con varias empresas dentro; en self-hosted habra una
instalacion por cliente, en su propia infraestructura, cada una con su
identidad.

El mismo software sirve para las dos: la modalidad es un dato, no una
version distinta ni una rama del codigo.

Lo que NO guarda este modulo: claves, tokens, licencias ni credenciales
de ningun tipo. Es identificacion, no autorizacion. 'licensed_to' es un
texto descriptivo —a nombre de quien esta la instalacion— y no debe
usarse nunca para guardar un secreto.
"""

import uuid

from datetime import datetime, timezone

from backend.database import get_connection


MODE_CLOUD = "cloud"
MODE_SELF_HOSTED = "self_hosted"

MODES = (MODE_CLOUD, MODE_SELF_HOSTED)

# Modalidad de una instalacion que todavia no ha dicho cual es. La
# actual es cloud, y es el valor conservador: no supone que nadie haya
# instalado el software por su cuenta.
DEFAULT_MODE = MODE_CLOUD


class InstallationError(ValueError):
    """Operacion sobre la instalacion rechazada, con motivo legible."""


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def ensure_installation(mode=None, licensed_to=None):
    """
    Devuelve la identidad de esta instalacion, creandola si hace falta.

    Idempotente y ESTABLE: el identificador se genera una sola vez y
    sobrevive a todos los reinicios. Si se regenerase en cada arranque
    no serviria para identificar nada.

    La clave primaria con CHECK = 1 ya impide una segunda fila; el
    INSERT OR IGNORE evita ademas que dos arranques simultaneos choquen.
    """

    modo = mode if mode in MODES else DEFAULT_MODE

    connection = get_connection()

    try:

        connection.execute(
            """
            INSERT OR IGNORE INTO installation (
                singleton, id, mode, licensed_to, created_at
            )
            VALUES (1, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), modo, licensed_to, _ahora())
        )

        connection.commit()

        fila = connection.execute(
            "SELECT * FROM installation WHERE singleton = 1"
        ).fetchone()

    finally:
        connection.close()

    return _fila_a_instalacion(fila)


def _fila_a_instalacion(fila):
    """
    Datos publicables de la instalacion.

    Todo lo que hay aqui es informacion de identificacion. No se filtra
    nada porque no hay nada que filtrar: la tabla no guarda secretos.
    """

    if fila is None:
        return None

    return {
        "id": fila["id"],
        "mode": fila["mode"],
        "licensed_to": fila["licensed_to"],
        "created_at": fila["created_at"]
    }


def get_installation():
    """Identidad actual, o None si todavia no se ha creado."""

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT * FROM installation WHERE singleton = 1"
        ).fetchone()

    finally:
        connection.close()

    return _fila_a_instalacion(fila)


def set_mode(mode):
    """
    Cambia la modalidad de la instalacion.

    Es un cambio de clasificacion: no mueve datos, no toca equipos y no
    reconfigura nada. Solo dice que clase de despliegue es este.
    """

    if mode not in MODES:
        raise InstallationError("Modalidad de instalacion desconocida")

    ensure_installation()

    connection = get_connection()

    try:
        connection.execute(
            "UPDATE installation SET mode = ? WHERE singleton = 1",
            (mode,)
        )
        connection.commit()

    finally:
        connection.close()

    return get_installation()


def set_licensed_to(licensed_to):
    """
    Anota a nombre de quien esta la instalacion.

    Texto descriptivo. No es una licencia ni una credencial: no
    concede ni restringe nada.
    """

    ensure_installation()

    texto = (licensed_to or "").strip()[:120] or None

    connection = get_connection()

    try:
        connection.execute(
            "UPDATE installation SET licensed_to = ? WHERE singleton = 1",
            (texto,)
        )
        connection.commit()

    finally:
        connection.close()

    return get_installation()
