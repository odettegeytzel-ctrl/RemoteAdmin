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

# Si no hay clave en .env se genera una temporal (las sesiones no sobreviven al reinicio)
SECRET_KEY = os.getenv("AUTH_SECRET_KEY") or secrets.token_hex(32)

TOKEN_HOURS = int(os.getenv("AUTH_TOKEN_HOURS", "12"))

PBKDF2_ITERATIONS = 200000


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


def verify_token(token):

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


def verify_agent_token(token):

    # Sin AGENT_TOKEN configurado en .env no se autoriza ningun Agent
    if not AGENT_TOKEN:
        return False

    return hmac.compare_digest(
        (token or "").encode("utf-8"),
        AGENT_TOKEN.encode("utf-8")
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
