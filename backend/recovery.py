"""
Recuperacion de contrasena por correo.

El flujo es el clasico y las decisiones delicadas son tres:

1. La respuesta es SIEMPRE la misma, exista o no la direccion. Un panel que
   contesta distinto segun el caso es un comprobador de cuentas gratuito
   para quien quiera averiguar quien usa el sistema.

2. El token se entrega una sola vez, por correo, y en la base solo queda su
   SHA-256. Quien lea la base no puede fabricar un enlace valido. Es el
   mismo criterio que con los tokens de los Agents: valores aleatorios de
   256 bits, donde no hay nada que adivinar por fuerza bruta.

3. Un solo uso y caducidad corta. Al usarlo se marca gastado, y de paso se
   invalidan los demas tokens de ese usuario: si alguien pidio tres enlaces
   porque no le llegaba el correo, los otros dos dejan de valer en cuanto
   se usa uno.

El token nunca se escribe en un registro, ni en auditoria, ni en la
respuesta HTTP.
"""

import hashlib
import secrets
import time

from backend.database import get_connection


# 32 bytes = 256 bits de entropia
TOKEN_BYTES = 32

# Una hora. Suficiente para leer el correo, corto para que un enlace
# olvidado en una bandeja no siga sirviendo manana.
TOKEN_TTL_SECONDS = 3600


def _ahora():
    return int(time.time())


def hash_token(token):
    """SHA-256 del token. Lo unico que se guarda."""

    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def create_token(user_id):
    """
    Crea un token de recuperacion y devuelve el valor EN CLARO.

    Ese valor solo se usa para construir el enlace del correo; quien llame
    no debe guardarlo, registrarlo ni devolverlo por la API.
    """

    token = secrets.token_urlsafe(TOKEN_BYTES)

    ahora = _ahora()

    connection = get_connection()

    try:

        connection.execute(
            """
            INSERT INTO password_reset_tokens (
                user_id, token_hash, created_at, expires_at, used_at
            )
            VALUES (?, ?, ?, ?, NULL)
            """,
            (user_id, hash_token(token), ahora, ahora + TOKEN_TTL_SECONDS)
        )

        # Limpieza oportunista de lo que ya no sirve
        connection.execute(
            "DELETE FROM password_reset_tokens WHERE expires_at < ?",
            (ahora - TOKEN_TTL_SECONDS,)
        )

        connection.commit()

    finally:
        connection.close()

    return token


def peek_token(token):
    """
    Devuelve (user_id, motivo) sin gastar el token.

    motivo es None si vale. Sirve para que el panel pueda decir "este
    enlace ya no sirve" antes de que el usuario teclee nada.
    """

    if not token:
        return None, "El enlace no es valido"

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT * FROM password_reset_tokens WHERE token_hash = ?",
            (hash_token(token),)
        ).fetchone()

    finally:
        connection.close()

    if fila is None:
        return None, "El enlace no es valido"

    if fila["used_at"] is not None:
        return None, "Este enlace ya se ha usado"

    if int(fila["expires_at"]) < _ahora():
        return None, "El enlace ha caducado"

    return fila["user_id"], None


def consume_token(token):
    """
    Gasta el token. Devuelve (user_id, motivo).

    Se marca como usado en la MISMA sentencia que lo busca, condicionada a
    que siga sin usar: asi dos peticiones simultaneas con el mismo enlace
    no pueden pasar las dos. La que llegue segunda no cambiara ninguna fila
    y recibira un rechazo.
    """

    user_id, motivo = peek_token(token)

    if motivo:
        return None, motivo

    ahora = _ahora()

    connection = get_connection()

    try:

        cursor = connection.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE token_hash = ? AND used_at IS NULL AND expires_at >= ?
            """,
            (ahora, hash_token(token), ahora)
        )

        gastado = cursor.rowcount == 1

        if gastado:
            # Los demas enlaces de ese usuario dejan de valer
            connection.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = ?
                WHERE user_id = ? AND used_at IS NULL
                """,
                (ahora, user_id)
            )

        connection.commit()

    finally:
        connection.close()

    if not gastado:
        return None, "Este enlace ya se ha usado"

    return user_id, None


def invalidate_user_tokens(user_id):
    """Anula los enlaces pendientes de un usuario."""

    connection = get_connection()

    try:
        cursor = connection.execute(
            "UPDATE password_reset_tokens SET used_at = ? "
            "WHERE user_id = ? AND used_at IS NULL",
            (_ahora(), user_id)
        )
        connection.commit()

    finally:
        connection.close()

    return cursor.rowcount


def pending_tokens(user_id):
    """Cuantos enlaces siguen vivos para ese usuario."""

    connection = get_connection()

    try:
        total = connection.execute(
            "SELECT COUNT(*) FROM password_reset_tokens "
            "WHERE user_id = ? AND used_at IS NULL AND expires_at >= ?",
            (user_id, _ahora())
        ).fetchone()[0]

    finally:
        connection.close()

    return total
