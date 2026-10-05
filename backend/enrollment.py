"""
Credenciales de alta de Agents, por organizacion.

Hasta ahora todo equipo nuevo se daba de alta con un unico AGENT_TOKEN
compartido, y por tanto entraba siempre en la misma empresa. Con varias
organizaciones eso no sirve: hace falta que la credencial diga a que
empresa pertenece el equipo que se esta registrando.

La regla que ordena todo esto:

    la organizacion se deriva de la CREDENCIAL, nunca de lo que envie
    el Agent.

Un Agent no tiene forma de elegir su empresa. Aunque mande un
organization_id en el cuerpo de la peticion, no se lee.

Como los tokens individuales de los Agents, aqui solo se guarda el
SHA-256: quien lea la base no puede fabricar una credencial valida. El
valor en claro se entrega una sola vez, al crearla.

Compatibilidad: el AGENT_TOKEN del .env sigue valiendo mientras dure la
transicion, y lleva a la organizacion por defecto. Quitarlo ahora
dejaria fuera a las instalaciones que ya existen.
"""

import hashlib
import secrets
import time

from datetime import datetime, timezone

from backend.database import get_connection


# 32 bytes = 256 bits. Lo mismo que los tokens individuales: no hay nada
# que adivinar por fuerza bruta ni por diccionario.
TOKEN_BYTES = 32

# Prefijo visible para distinguirlas de un vistazo en una configuracion
# y no confundirlas con un token individual.
TOKEN_PREFIX = "rae_"


class EnrollmentError(ValueError):
    """Credencial de alta rechazada, con un motivo legible."""


def _ahora_iso():
    return datetime.now(timezone.utc).isoformat()


def hash_token(token):
    """SHA-256 del valor en claro. Lo unico que se guarda."""

    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def generate_token():
    """Credencial nueva, en claro. Solo se devuelve una vez."""

    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def create_token(organization_id, label=None, expires_in_days=None):
    """
    Crea una credencial de alta para una organizacion.

    Devuelve (ficha, valor_en_claro). El valor no se puede volver a
    consultar: si se pierde, se revoca y se crea otra.
    """

    if not organization_id:
        raise EnrollmentError("Hace falta indicar la organizacion")

    token = generate_token()

    expira = None

    if expires_in_days:

        try:
            dias = float(expires_in_days)

        except (TypeError, ValueError):
            raise EnrollmentError("La caducidad debe ser un numero de dias")

        if dias <= 0:
            raise EnrollmentError("La caducidad debe ser mayor que cero")

        expira = int(time.time() + dias * 86400)

    connection = get_connection()

    try:
        cursor = connection.execute(
            """
            INSERT INTO enrollment_tokens (
                organization_id, token_hash, label, active,
                created_at, expires_at, uses
            )
            VALUES (?, ?, ?, 1, ?, ?, 0)
            """,
            (
                organization_id,
                hash_token(token),
                (label or "").strip()[:80] or None,
                _ahora_iso(),
                expira
            )
        )

        connection.commit()

        identificador = cursor.lastrowid

    finally:
        connection.close()

    return get_token(identificador), token


def _fila_a_ficha(fila):
    """
    Datos publicables de una credencial.

    El hash NO sale de aqui: no sirve para nada fuera y exponerlo
    permitiria comprobar candidatos sin tocar el servidor.
    """

    if fila is None:
        return None

    ahora = int(time.time())

    caducada = bool(fila["expires_at"]) and fila["expires_at"] < ahora

    return {
        "id": fila["id"],
        "organization_id": fila["organization_id"],
        "label": fila["label"],
        "active": bool(fila["active"]),
        "created_at": fila["created_at"],
        "expires_at": fila["expires_at"],
        "last_used_at": fila["last_used_at"],
        "uses": fila["uses"],
        "expired": caducada,
        "usable": bool(fila["active"]) and not caducada
    }


def get_token(token_id):

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT * FROM enrollment_tokens WHERE id = ?",
            (token_id,)
        ).fetchone()

    finally:
        connection.close()

    return _fila_a_ficha(fila)


def list_tokens(organization_id=None):
    """Credenciales de una organizacion, o de todas."""

    connection = get_connection()

    try:
        if organization_id is None:
            filas = connection.execute(
                "SELECT * FROM enrollment_tokens ORDER BY id DESC"
            ).fetchall()

        else:
            filas = connection.execute(
                "SELECT * FROM enrollment_tokens WHERE organization_id = ? "
                "ORDER BY id DESC",
                (organization_id,)
            ).fetchall()

    finally:
        connection.close()

    return [_fila_a_ficha(fila) for fila in filas]


def revoke_token(token_id):
    """Desactiva una credencial. No se borra: queda su rastro de uso."""

    connection = get_connection()

    try:
        cursor = connection.execute(
            "UPDATE enrollment_tokens SET active = 0 WHERE id = ?",
            (token_id,)
        )
        connection.commit()
        cambiada = cursor.rowcount

    finally:
        connection.close()

    if not cambiada:
        raise EnrollmentError("La credencial no existe")

    return get_token(token_id)


def organization_for_token(token):
    """
    Organizacion a la que da acceso una credencial, o None.

    Es el unico punto donde se decide a que empresa entra un equipo
    nuevo. Devuelve None si la credencial no existe, esta revocada o ha
    caducado: en los tres casos el alta se rechaza igual, sin decir cual
    de ellos es.

    Tambien anota el uso, que es lo que permite ver despues cuantos
    equipos se dieron de alta con cada credencial.
    """

    if not token:
        return None

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT * FROM enrollment_tokens WHERE token_hash = ?",
            (hash_token(token),)
        ).fetchone()

        if fila is None or not fila["active"]:
            return None

        if fila["expires_at"] and fila["expires_at"] < int(time.time()):
            return None

        connection.execute(
            "UPDATE enrollment_tokens SET uses = uses + 1, "
            "last_used_at = ? WHERE id = ?",
            (_ahora_iso(), fila["id"])
        )

        connection.commit()

        return fila["organization_id"]

    finally:
        connection.close()
