"""
Registro de auditoría de acciones administrativas (bloque G1).

Responde a una pregunta que hasta ahora el sistema no podía contestar: quién
pidió qué, sobre qué equipo, desde dónde y cuándo.

Hoy las acciones remotas —teclado, ratón, archivos, grabación— no dejan
ningún rastro persistente: solo líneas por consola que se pierden al
reiniciar, y sin identificar al operador. Con una sola cuenta eso ya era
incómodo; con apagados y reinicios por delante, sería un incidente
imposible de investigar.

Qué NO se guarda aquí, nunca: contraseñas, tokens de sesión o de Agent,
cookies, claves privadas ni contenido de los archivos transferidos. Los
detalles se limitan a lo mínimo para entender la acción.
"""

import json
import re

from datetime import datetime, timezone

from backend.database import get_connection


# Resultados posibles de una acción.
#
# 'requested' existe porque la mayoría de las acciones actuales se envían al
# Agent por WebSocket sin esperar respuesta: se sabe que se pidieron, no que
# se completaran. Registrarlas como 'success' sería mentir.
STATUS_REQUESTED = "requested"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

# Longitud máxima de los detalles: son una ayuda para entender el registro,
# no un volcado de datos.
MAX_DETAILS_LENGTH = 500

# Claves que nunca deben acabar en el registro, por si alguien pasa un dict
# de detalles con más de la cuenta.
SENSITIVE_KEYS = {
    "password", "contrasena", "contraseña", "token", "agent_token",
    "x-agent-token", "authorization", "cookie", "secret", "key",
    "auth_secret_key", "password_hash", "hash", "private_key"
}

# Valores que parecen un token aunque la clave no lo diga
TOKEN_LIKE = re.compile(r"^[A-Za-z0-9_\-]{24,}$")


def _scrub(valor, clave=None):
    """
    Deja un valor en condiciones de ser registrado.

    Dos filtros: por nombre de clave y por aspecto del valor. El segundo
    atrapa un token colado en un campo con nombre inocente.
    """

    if clave and clave.lower() in SENSITIVE_KEYS:
        return "[oculto]"

    if isinstance(valor, dict):
        return {k: _scrub(v, k) for k, v in valor.items()}

    if isinstance(valor, (list, tuple)):
        return [_scrub(v) for v in valor]

    if isinstance(valor, str):

        if TOKEN_LIKE.match(valor) and len(valor) >= 32:
            return "[posible secreto oculto]"

        return valor

    return valor


def _format_details(details):
    """Convierte los detalles en un texto corto y sin secretos."""

    if details is None:
        return None

    limpio = _scrub(details)

    if isinstance(limpio, str):
        texto = limpio
    else:
        try:
            texto = json.dumps(limpio, ensure_ascii=False)
        except (TypeError, ValueError):
            texto = str(limpio)

    if len(texto) > MAX_DETAILS_LENGTH:
        texto = texto[:MAX_DETAILS_LENGTH - 3] + "..."

    return texto


def get_request_context(request):
    """
    Extrae el usuario y la IP de la petición real.

    Nunca se aceptan del cuerpo ni de la cabecera: falsificar un registro de
    auditoría sería tan fácil como enviar otro nombre. El usuario sale del
    token de sesión ya validado y la IP, de la conexión.
    """

    # Import local: auth no depende de audit, y así se evita un ciclo
    from backend.auth import verify_token, token_from_header

    username = None
    source_ip = None

    if request is not None:

        token = (
            token_from_header(request.headers.get("Authorization"))
            or request.cookies.get("remoteadmin_token")
        )

        # verify_token devuelve el usuario del token firmado, no lo que
        # diga el cliente por otra vía.
        username = verify_token(token)

        if request.client:
            source_ip = request.client.host

    return username, source_ip


def log_audit(action, request=None, device_id=None, status=STATUS_REQUESTED,
              details=None, username=None, source_ip=None):
    """
    Registra una acción administrativa.

    Los parámetros username y source_ip solo se usan cuando no hay objeto
    request disponible (por ejemplo desde una tarea interna). Si hay
    request, manda siempre el contexto real de la petición.

    Nunca lanza: un fallo al auditar no debe tumbar la acción del usuario,
    pero sí queda anotado por consola para que no pase inadvertido.
    """

    try:

        if request is not None:
            username, source_ip = get_request_context(request)

        registro = (
            datetime.now(timezone.utc).isoformat(),
            str(action),
            device_id,
            username,
            source_ip,
            str(status),
            _format_details(details)
        )

        connection = get_connection()

        try:
            connection.execute(
                """
                INSERT INTO audit_log (
                    timestamp, action, device_id, username,
                    source_ip, status, details
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                registro
            )
            connection.commit()

        finally:
            connection.close()

        return True

    except Exception as error:
        print(f"[auditoría] No se pudo registrar '{action}': {error}")
        return False


def list_audit(limit=100, device_id=None, action=None, offset=0):
    """
    Consulta el registro, de más reciente a más antiguo.

    Filtros opcionales por equipo y por acción. El límite se acota para que
    una consulta no pueda arrastrar el historial entero.
    """

    limite = max(1, min(int(limit or 100), 500))
    desplazamiento = max(0, int(offset or 0))

    condiciones = []
    parametros = []

    if device_id:
        condiciones.append("device_id = ?")
        parametros.append(device_id)

    if action:
        condiciones.append("action = ?")
        parametros.append(action)

    filtro = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""

    connection = get_connection()

    try:
        filas = connection.execute(
            f"""
            SELECT id, timestamp, action, device_id, username,
                   source_ip, status, details
            FROM audit_log
            {filtro}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (*parametros, limite, desplazamiento)
        ).fetchall()

    finally:
        connection.close()

    return [dict(fila) for fila in filas]


def count_audit(device_id=None, action=None):
    """Número total de registros que cumplen el filtro."""

    condiciones = []
    parametros = []

    if device_id:
        condiciones.append("device_id = ?")
        parametros.append(device_id)

    if action:
        condiciones.append("action = ?")
        parametros.append(action)

    filtro = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""

    connection = get_connection()

    try:
        total = connection.execute(
            f"SELECT COUNT(*) FROM audit_log{filtro}",
            tuple(parametros)
        ).fetchone()[0]

    finally:
        connection.close()

    return total
