"""
Límite de intentos fallidos por IP.

Se usa en dos sitios independientes, identificados por su "scope":
  - "login":  contraseñas del panel.
  - "enroll": altas de Agents con el AGENT_TOKEN compartido.

Cada scope lleva su propio recuento, así que agotar los intentos de uno no
afecta al otro.

Objetivo: que un atacante no pueda probar credenciales de forma ilimitada.
Solo cuentan los intentos FALLIDOS; uno correcto borra el historial de esa IP,
así que quien se equivoca alguna vez no se ve afectado.

Estado en memoria del proceso: suficiente para desarrollo local y un único
servidor. Se pierde al reiniciar y no se comparte entre procesos; para varios
servidores haría falta un almacén común (Redis o base de datos).

Nunca se guardan usuarios, contraseñas ni tokens: solo la IP y las marcas de
tiempo de los fallos.
"""

import threading
import time


# --- scope "login" ---
# Fallos permitidos por IP dentro de la ventana antes de bloquear
MAX_FAILURES = 5

# Ventana en segundos: los fallos más antiguos dejan de contar.
# 5 minutos: frena la fuerza bruta sin castigar de más en desarrollo.
WINDOW_SECONDS = 300

# --- scope "enroll" ---
# Las altas legítimas son raras (una por equipo nuevo), así que la ventana es
# más larga: limita el alta masiva de dispositivos con el token compartido.
ENROLL_MAX_FAILURES = 5
ENROLL_WINDOW_SECONDS = 600

# --- scope "password" ---
# Cambiar la contrasena exige volver a teclear la actual, asi que un fallo
# aqui es un intento de adivinarla con una sesion ya abierta (por ejemplo,
# con una cookie robada). Se cuenta APARTE del login: bloquear el cambio de
# contrasena no debe bloquear el acceso al panel, ni al reves.
PASSWORD_MAX_FAILURES = 3
PASSWORD_WINDOW_SECONDS = 900


# (scope, IP) -> lista de marcas de tiempo de los fallos recientes
_failures = {}

# Los endpoints síncronos de FastAPI corren en varios hilos
_lock = threading.Lock()


def _limits(scope):
    """Máximo de fallos y ventana, según el scope."""

    if scope == "enroll":
        return ENROLL_MAX_FAILURES, ENROLL_WINDOW_SECONDS

    if scope == "password":
        return PASSWORD_MAX_FAILURES, PASSWORD_WINDOW_SECONDS

    return MAX_FAILURES, WINDOW_SECONDS


def _recent(timestamps, now, window):
    """Deja solo los fallos que siguen dentro de la ventana."""
    return [t for t in timestamps if now - t < window]


def seconds_until_unblocked(ip, scope="login"):
    """
    Segundos que faltan para que esta IP pueda volver a intentarlo.
    Devuelve 0 si no está bloqueada.
    """

    now = time.time()
    maximo, ventana = _limits(scope)

    with _lock:

        timestamps = _recent(_failures.get((scope, ip), []), now, ventana)

        if len(timestamps) < maximo:
            return 0

        # El bloqueo dura hasta que el fallo más antiguo salga de la ventana
        remaining = ventana - (now - min(timestamps))

        return max(1, int(remaining))


def register_failure(ip, scope="login"):
    """Anota un intento fallido de esta IP en ese scope."""

    now = time.time()
    _, ventana = _limits(scope)

    with _lock:

        timestamps = _recent(_failures.get((scope, ip), []), now, ventana)
        timestamps.append(now)
        _failures[(scope, ip)] = timestamps

        # Limpieza oportunista: evita que el diccionario crezca sin control
        # con IPs que ya no tienen fallos vigentes.
        caducados = [
            clave
            for clave, marcas in _failures.items()
            if not _recent(marcas, now, _limits(clave[0])[1])
        ]

        for clave in caducados:
            del _failures[clave]


def reset(ip, scope="login"):
    """Borra el historial de fallos de esa IP tras un intento correcto."""

    with _lock:
        _failures.pop((scope, ip), None)
