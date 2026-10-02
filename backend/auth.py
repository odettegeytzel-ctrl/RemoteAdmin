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
# ALMACEN DE LA CREDENCIAL (tabla auth_state)
# ==============================
#
# La fuente de verdad de la contrasena es la base de datos, no el .env.
# AUTH_PASSWORD_HASH queda como SEMILLA: la primera vez que se consulta el
# almacen y esta vacio, se copia de ahi. A partir de ese momento manda la
# tabla, y editar el .env ya no cambia nada.
#
# Se lee en cada intento, no al importar: asi un cambio de contrasena tiene
# efecto de inmediato, sin reiniciar el servidor.


def _utc_now_iso():
    """Momento actual en UTC, en texto ISO-8601."""

    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def get_auth_state():
    """
    Devuelve la fila de auth_state, sembrandola desde el .env si hace falta.

    None si no hay credencial configurada por ninguna via; en ese caso el
    login sigue negando el acceso, igual que antes.
    """

    from backend.database import get_connection

    connection = get_connection()

    try:

        fila = connection.execute(
            "SELECT password_hash, password_changed_at, sessions_valid_from "
            "FROM auth_state WHERE id = 1"
        ).fetchone()

        if fila is None and AUTH_PASSWORD_HASH:

            # Siembra unica. INSERT OR IGNORE por si dos peticiones llegan a
            # la vez: la segunda no debe pisar a la primera.
            connection.execute(
                """
                INSERT OR IGNORE INTO auth_state (
                    id, password_hash, password_changed_at, sessions_valid_from
                )
                VALUES (1, ?, NULL, 0)
                """,
                (AUTH_PASSWORD_HASH,)
            )

            connection.commit()

            fila = connection.execute(
                "SELECT password_hash, password_changed_at, "
                "sessions_valid_from FROM auth_state WHERE id = 1"
            ).fetchone()

    finally:
        connection.close()

    if fila is None:
        return None

    return {
        "password_hash": fila["password_hash"],
        "password_changed_at": fila["password_changed_at"],
        "sessions_valid_from": int(fila["sessions_valid_from"] or 0)
    }


def get_stored_password_hash():
    """Hash vigente. Nunca se imprime ni se registra."""

    estado = get_auth_state()

    if estado and estado["password_hash"]:
        return estado["password_hash"]

    # Solo antes de la siembra (por ejemplo, si aun no existe la tabla)
    return AUTH_PASSWORD_HASH


def get_sessions_valid_from():
    """Fecha de corte de sesiones en segundos epoch. 0 = sin corte."""

    estado = get_auth_state()

    return estado["sessions_valid_from"] if estado else 0


def _nueva_fecha_de_corte():
    """
    Siguiente fecha de corte, siempre por delante de la vigente.

    No basta con time.time() + 1: dos operaciones sobre la credencial
    dentro del mismo segundo (cambiar la contrasena y acto seguido
    restablecerla) calculaban el mismo corte, y la sesion reemitida por la
    primera sobrevivia a la segunda. Una invalidacion que deja sesiones
    vivas no invalida nada, asi que cada corte supera al anterior.
    """

    estado = get_auth_state()

    actual = estado["sessions_valid_from"] if estado else 0

    return max(int(time.time()) + 1, actual + 1)


def invalidate_all_sessions(moment=None):
    """
    Cierra TODAS las sesiones abiertas moviendo la fecha de corte.

    No hace falta tocar ningun token: los que se emitieron antes dejan de
    superar la comprobacion de verify_token().

    La fecha de corte tiene precision de un segundo, asi que por defecto se
    toma el segundo SIGUIENTE: de lo contrario un token emitido en el mismo
    segundo del corte sobreviviria, y una invalidacion que deja sesiones
    vivas no sirve de nada. Como efecto, tambien muere la sesion de quien
    provoca el corte: quien llame a esto debe reemitir la suya despues.

    Devuelve la fecha de corte aplicada.
    """

    from backend.database import get_connection

    corte = int(moment) if moment is not None else _nueva_fecha_de_corte()

    # Asegura que la fila existe antes de actualizarla
    get_auth_state()

    connection = get_connection()

    try:
        connection.execute(
            "UPDATE auth_state SET sessions_valid_from = ? WHERE id = 1",
            (corte,)
        )
        connection.commit()

    finally:
        connection.close()

    return corte


# ==============================
# TOKENS DE SESION (firmados con HMAC, sin estado en el servidor)
# ==============================

def _b64encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text):
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def create_token(username, issued_at=None):
    """
    Emite un token de sesion.

    El iat nunca queda por debajo de la fecha de corte vigente. Sin ese
    tope, iniciar sesion en el mismo segundo en que se cambio la contrasena
    producia un token que no pasaba verify_token(): la sesion parecia
    abierta y la siguiente peticion devolvia 401. Emitir un token exige
    haberse autenticado, asi que una sesion nacida despues del corte es
    legitima por definicion.

    'issued_at' fuerza un momento concreto; se usa al reemitir la sesion
    tras un cambio de contrasena.
    """

    if issued_at is not None:
        emitido = int(issued_at)
    else:
        emitido = max(int(time.time()), get_sessions_valid_from())

    payload = {
        "sub": username,

        # 'iat' (issued at) permite cerrar todas las sesiones de golpe
        # comparandolo con sessions_valid_from. Sin el, un token sin estado
        # no se puede invalidar mas que uno a uno.
        "iat": emitido,

        # 'jti' hace unico cada token. Sin el, dos inicios de sesion dentro
        # del mismo segundo producian payloads identicos y, por tanto, el
        # MISMO token: cerrar sesion en un equipo cerraba tambien la del
        # otro, porque la revocacion va por hash del token.
        "jti": secrets.token_urlsafe(8),

        "exp": emitido + TOKEN_HOURS * 3600
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

    Tres comprobaciones, ademas de firma y expiracion:
      - logout individual: el token concreto esta en revoked_sessions;
      - corte global: se emitio antes de sessions_valid_from;
      - los tokens antiguos, sin 'iat', cuentan como anteriores a cualquier
        corte, asi que caen con la primera invalidacion global. Mientras no
        haya habido ninguna (corte = 0) siguen siendo validos.
    """

    payload = _decode_payload(token)

    if payload is None:
        return None

    if is_session_revoked(token):
        return None

    try:
        emitido = int(payload.get("iat", 0) or 0)
    except (TypeError, ValueError):
        emitido = 0

    if emitido < get_sessions_valid_from():
        return None

    return payload.get("sub")


# ==============================
# LOGIN
# ==============================

def authenticate(username, password):

    # Se lee en cada intento, no al importar: un cambio de contrasena debe
    # valer de inmediato, sin reiniciar el servidor.
    stored_hash = get_stored_password_hash()

    if not stored_hash:
        # Sin credencial configurada no se permite el acceso
        return None

    username_ok = hmac.compare_digest(
        (username or "").encode("utf-8"),
        AUTH_USERNAME.encode("utf-8")
    )

    password_ok = verify_password(password or "", stored_hash)

    if username_ok and password_ok:
        return create_token(username)

    return None


# ==============================
# POLITICA DE CONTRASENA Y CAMBIO
# ==============================

# Minimo: 12 caracteres. Este panel controla equipos Windows con permisos
# de administrador, asi que el listen minimo de "8 con complejidad" se queda
# corto; una frase de 12 es facil de recordar y cara de romper.
PASSWORD_MIN_LENGTH = 12

# Maximo: PBKDF2 con 200.000 iteraciones sobre una entrada enorme es un
# consumidor de CPU gratuito para quien la envie. 128 no estorba a nadie.
PASSWORD_MAX_LENGTH = 128


# Resultados posibles de un cambio de contrasena
CHANGE_OK = "ok"
CHANGE_CURRENT_INVALID = "current_invalid"
CHANGE_POLICY = "policy"


def validate_new_password(new_password, current_hash=None):
    """
    Comprueba la politica. Devuelve el motivo del rechazo, o None si vale.

    El motivo se puede ensenar al usuario y guardar en auditoria: describe
    la regla incumplida, nunca la contrasena.
    """

    if not isinstance(new_password, str) or not new_password:
        return "La contrasena nueva no puede estar vacia"

    if len(new_password) < PASSWORD_MIN_LENGTH:
        return (
            f"La contrasena debe tener al menos {PASSWORD_MIN_LENGTH} "
            "caracteres"
        )

    if len(new_password) > PASSWORD_MAX_LENGTH:
        return (
            f"La contrasena no puede superar los {PASSWORD_MAX_LENGTH} "
            "caracteres"
        )

    if new_password == DEFAULT_PASSWORD:
        return "No se permite la contrasena por defecto"

    if current_hash and verify_password(new_password, current_hash):
        return "La contrasena nueva debe ser distinta de la actual"

    return None


def set_password(new_password, invalidate_sessions=True):
    """
    Guarda la contrasena nueva y, por defecto, cierra todas las sesiones.

    Se guarda solo el hash PBKDF2-SHA256, en el mismo formato de siempre.
    La contrasena en claro no se escribe en ninguna parte ni se devuelve.

    Devuelve la fecha de corte aplicada (0 si no se invalido nada).
    """

    from backend.database import get_connection

    nuevo_hash = generate_password_hash(new_password)
    cambiado_en = _utc_now_iso()

    corte = _nueva_fecha_de_corte() if invalidate_sessions else None

    connection = get_connection()

    try:

        if corte is None:

            connection.execute(
                """
                INSERT INTO auth_state (
                    id, password_hash, password_changed_at, sessions_valid_from
                )
                VALUES (1, ?, ?, 0)
                ON CONFLICT(id) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    password_changed_at = excluded.password_changed_at
                """,
                (nuevo_hash, cambiado_en)
            )

        else:

            connection.execute(
                """
                INSERT INTO auth_state (
                    id, password_hash, password_changed_at, sessions_valid_from
                )
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    password_changed_at = excluded.password_changed_at,
                    sessions_valid_from = excluded.sessions_valid_from
                """,
                (nuevo_hash, cambiado_en, corte)
            )

        connection.commit()

    finally:
        connection.close()

    return corte or 0


def change_password(current_password, new_password):
    """
    Cambia la contrasena verificando antes la actual.

    Devuelve (resultado, detalle):
      CHANGE_CURRENT_INVALID -> la contrasena actual no es correcta
      CHANGE_POLICY          -> la nueva incumple la politica (detalle = motivo)
      CHANGE_OK              -> cambiada (detalle = fecha de corte)

    La actual se comprueba SIEMPRE, aunque quien llame ya tenga una sesion
    valida: una cookie robada no debe bastar para apropiarse de la cuenta.
    """

    stored_hash = get_stored_password_hash()

    if not stored_hash:
        return CHANGE_CURRENT_INVALID, "No hay ninguna credencial configurada"

    if not verify_password(current_password or "", stored_hash):
        return CHANGE_CURRENT_INVALID, "La contrasena actual no es correcta"

    problema = validate_new_password(new_password, stored_hash)

    if problema:
        return CHANGE_POLICY, problema

    return CHANGE_OK, set_password(new_password)


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

    # Impide arrancar si la contrasena VIGENTE sigue siendo la de por defecto.
    # Se comprueba la del almacen, no la del .env: desde G4a el .env es solo
    # la semilla, y mirar ahi daria un veredicto equivocado.
    vigente = get_stored_password_hash()

    if vigente and verify_password(DEFAULT_PASSWORD, vigente):
        raise RuntimeError(
            "La contraseña del usuario '" + AUTH_USERNAME + "' sigue siendo la "
            "contraseña por defecto ('" + DEFAULT_PASSWORD + "'). El backend "
            "no puede arrancar asi. "
            "Cambiala desde la consola del servidor con: "
            "python reset_password.py"
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
