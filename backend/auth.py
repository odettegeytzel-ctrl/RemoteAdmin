import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from dotenv import load_dotenv


load_dotenv()


# ==============================
# CONFIGURACION (desde .env, nunca en el codigo)
# ==============================

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH", "")

# Token compartido que autentica a los Agents (register, heartbeat y WebSocket)
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")

# AUTH_SECRET_KEY es OBLIGATORIO (se valida al arrancar en require_security_config).
# El fallback solo evita un fallo de import (p. ej. el generador de hash de abajo);
# con el backend en marcha nunca se usa porque el arranque falla si falta.
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "")
SECRET_KEY = AUTH_SECRET_KEY or secrets.token_hex(32)

TOKEN_HOURS = int(os.getenv("AUTH_TOKEN_HOURS", "12"))

PBKDF2_ITERATIONS = 200000

# Contraseña por defecto conocida: nunca se permite en una instalación real
DEFAULT_PASSWORD = "remoteadmin"


# ==============================
# CONTRASENAS (hash PBKDF2-SHA256, nunca texto plano)
# ==============================

def generate_password_hash(password, iterations=PBKDF2_ITERATIONS):

    salt = secrets.token_bytes(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations
    )

    return (
        f"pbkdf2_sha256${iterations}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(password, stored_hash):

    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$")
    except (ValueError, AttributeError):
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        int(iterations)
    )

    # Comparacion en tiempo constante
    return hmac.compare_digest(digest.hex(), digest_hex)


# ==============================
# TOKENS DE SESION (firmados con HMAC, sin estado en el servidor)
# ==============================

def _b64encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text):
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def create_token(username):

    payload = {
        "sub": username,
        "exp": int(time.time()) + TOKEN_HOURS * 3600
    }

    payload_b64 = _b64encode(
        json.dumps(payload).encode("utf-8")
    )

    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256
    ).digest()

    return f"{payload_b64}.{_b64encode(signature)}"


def _session_token_hash(token):
    """SHA-256 del token de sesión. Nunca se guarda el token en claro."""

    return hashlib.sha256(
        (token or "").encode("utf-8")
    ).hexdigest()


def revoke_session_token(token):
    """
    Invalida un token de sesión en el servidor (logout real).

    Los tokens son sin estado y siguen siendo criptográficamente válidos hasta
    su expiración, así que se anota su hash para dejar de aceptarlo. Se guarda
    también su fecha de expiración para poder purgar la fila después: pasada
    esa fecha el token ya no vale por sí mismo y la anotación sobra.

    Devuelve True si se revocó, False si el token no era válido.
    """

    # Import local: database no depende de auth, pero así se evita acoplar
    # este módulo a la base para el resto de sus funciones.
    from backend.database import get_connection

    payload = _decode_payload(token)

    if payload is None:
        return False

    connection = get_connection()

    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO revoked_sessions (token_hash, expires_at)
            VALUES (?, ?)
            """,
            (
                _session_token_hash(token),
                int(payload.get("exp", 0))
            )
        )

        # Purga oportunista: los tokens ya caducados no necesitan anotación
        connection.execute(
            "DELETE FROM revoked_sessions WHERE expires_at < ?",
            (int(time.time()),)
        )

        connection.commit()

    finally:
        connection.close()

    return True


def is_session_revoked(token):
    """True si el token fue revocado con un logout."""

    from backend.database import get_connection

    connection = get_connection()

    try:
        row = connection.execute(
            "SELECT 1 FROM revoked_sessions WHERE token_hash = ?",
            (_session_token_hash(token),)
        ).fetchone()

    finally:
        connection.close()

    return row is not None


def _decode_payload(token):
    """
    Comprueba firma y expiración y devuelve el payload, o None.
    No consulta la lista de revocación: la usan verify_token y el propio
    revoke_session_token, que necesita leer la expiración de un token válido.
    """

    if not token or "." not in token:
        return None

    payload_b64, signature_b64 = token.split(".", 1)

    expected = hmac.new(
        SECRET_KEY.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256
    ).digest()

    try:
        given = _b64decode(signature_b64)
    except Exception:
        return None

    if not hmac.compare_digest(expected, given):
        return None

    try:
        payload = json.loads(_b64decode(payload_b64))
    except Exception:
        return None

    if payload.get("exp", 0) < int(time.time()):
        return None

    return payload


def verify_token(token):
    """
    Devuelve el usuario del token, o None si no es válido.

    Además de firma y expiración, comprueba que la sesión no se haya cerrado:
    tras un logout el token sigue estando bien firmado, pero ya no sirve.
    """

    payload = _decode_payload(token)

    if payload is None:
        return None

    if is_session_revoked(token):
        return None

    return payload.get("sub")


# ==============================
# LOGIN
# ==============================

def authenticate(username, password):

    if not AUTH_PASSWORD_HASH:
        # Sin credenciales configuradas en .env no se permite el acceso
        return None

    username_ok = hmac.compare_digest(
        (username or "").encode("utf-8"),
        AUTH_USERNAME.encode("utf-8")
    )

    password_ok = verify_password(password or "", AUTH_PASSWORD_HASH)

    if username_ok and password_ok:
        return create_token(username)

    return None


def require_security_config():
    """
    Exige que AUTH_SECRET_KEY y AGENT_TOKEN estén definidos en el .env, y que
    AUTH_PASSWORD_HASH no corresponda a la contraseña por defecto.
    Si algo falla, lanza RuntimeError con un mensaje claro para que el
    backend falle al arrancar en vez de correr con seguridad incompleta.
    """

    missing = []

    if not AUTH_SECRET_KEY:
        missing.append("AUTH_SECRET_KEY")

    if not AGENT_TOKEN:
        missing.append("AGENT_TOKEN")

    if missing:
        raise RuntimeError(
            "Configuración de seguridad incompleta: falta(n) "
            + ", ".join(missing)
            + " en el archivo .env. El backend no puede arrancar sin estos valores. "
            "Genera AUTH_SECRET_KEY con: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    # Impide arrancar si la contraseña configurada sigue siendo la de por defecto
    if AUTH_PASSWORD_HASH and verify_password(DEFAULT_PASSWORD, AUTH_PASSWORD_HASH):
        raise RuntimeError(
            "La contraseña del usuario '" + AUTH_USERNAME + "' sigue siendo la "
            "contraseña por defecto ('" + DEFAULT_PASSWORD + "'). El backend no puede "
            "arrancar así. Genera un hash nuevo con: python backend/auth.py "
            "y reemplaza AUTH_PASSWORD_HASH en el archivo .env."
        )


def verify_agent_token(token):

    # Sin AGENT_TOKEN configurado en .env no se autoriza ningun Agent
    if not AGENT_TOKEN:
        return False

    return hmac.compare_digest(
        (token or "").encode("utf-8"),
        AGENT_TOKEN.encode("utf-8")
    )


# ==============================
# CREDENCIALES INDIVIDUALES POR DISPOSITIVO
# ==============================
#
# Cada Agent tiene su propio token, distinto del AGENT_TOKEN compartido (que
# queda como credencial de ALTA). El servidor guarda solo el SHA-256 del token.
#
# SHA-256 a secas es suficiente aquí, a diferencia de las contraseñas: estos
# tokens son aleatorios de 256 bits, así que no hay nada que adivinar por
# fuerza bruta ni por diccionario. Además se verifican en cada heartbeat (cada
# 10 s por equipo), donde el coste de PBKDF2 sería desproporcionado.

# Bytes de entropía de cada token individual (32 bytes = 256 bits)
AGENT_TOKEN_BYTES = 32

# Longitud del device_id en caracteres hexadecimales. Se mantiene en 16 para
# ser compatible con los identificadores ya existentes en la base.
DEVICE_ID_HEX_CHARS = 16


def generate_device_id():
    """
    device_id aleatorio criptográficamente seguro.

    Lo genera el SERVIDOR, no el Agent: así el cliente no puede elegir su
    identidad ni reclamar la de otro equipo.
    """

    return secrets.token_hex(DEVICE_ID_HEX_CHARS // 2)


def generate_agent_token():
    """
    Token individual de un Agent. Se devuelve en claro UNA sola vez, en el
    alta; el servidor guarda únicamente su hash.
    """

    return secrets.token_urlsafe(AGENT_TOKEN_BYTES)


def hash_agent_token(token):
    """SHA-256 en hexadecimal del token individual."""

    return hashlib.sha256(
        (token or "").encode("utf-8")
    ).hexdigest()


def agent_token_matches(token, stored_hash):
    """
    Compara un token con el hash guardado, en tiempo constante para no
    filtrar información por el tiempo de respuesta.
    """

    if not token or not stored_hash:
        return False

    return hmac.compare_digest(
        hash_agent_token(token),
        stored_hash
    )


def token_from_header(authorization):

    # Espera "Bearer <token>"
    if not authorization:
        return None

    parts = authorization.split(" ", 1)

    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()

    return None


if __name__ == "__main__":

    import getpass

    pwd = getpass.getpass("Nueva contraseña: ")

    print("\nAgrega esta línea a tu archivo .env:\n")
    print(f"AUTH_PASSWORD_HASH={generate_password_hash(pwd)}")
