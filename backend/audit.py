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


def resolve_organization(device_id=None, username=None):
    """
    Organizacion a la que pertenece una accion.

    Se mira primero el EQUIPO y despues el USUARIO. El equipo manda
    porque una accion sobre un equipo pertenece a la empresa dueña de
    ese equipo, aunque quien la ejecute sea el operador de la
    plataforma dando soporte.

    None significa una de dos cosas, y las dos son legitimas:
      - la accion es de plataforma (crear una organizacion, por ejemplo)
      - no hay forma segura de saberlo

    Nunca se inventa: antes NULL que una organizacion equivocada.
    """

    from backend.database import get_connection

    connection = get_connection()

    try:

        if device_id:

            fila = connection.execute(
                "SELECT organization_id FROM devices WHERE device_id = ?",
                (device_id,)
            ).fetchone()

            if fila and fila["organization_id"]:
                return fila["organization_id"]

        if username:

            fila = connection.execute(
                "SELECT organization_id FROM users "
                "WHERE username = ? COLLATE NOCASE",
                (username,)
            ).fetchone()

            if fila and fila["organization_id"]:
                return fila["organization_id"]

    except Exception:
        # Un fallo al deducir la organizacion no debe impedir registrar
        # la accion: es peor perder el rastro que perder el contexto.
        return None

    finally:
        connection.close()

    return None


def backfill_organizations():
    """
    Rellena la organizacion de los registros historicos.

    Solo cuando se puede deducir con seguridad del equipo o del usuario
    que ya constan en la fila. Lo que no se pueda deducir se queda en
    NULL: una organizacion inventada en un historial de auditoria es
    peor que un hueco.

    Idempotente: solo toca filas con organization_id NULL.
    """

    from backend.database import get_connection

    connection = get_connection()

    try:

        # Por equipo
        connection.execute(
            """
            UPDATE audit_log
            SET organization_id = (
                SELECT d.organization_id FROM devices d
                WHERE d.device_id = audit_log.device_id
            )
            WHERE organization_id IS NULL
              AND device_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM devices d
                  WHERE d.device_id = audit_log.device_id
                    AND d.organization_id IS NOT NULL
              )
            """
        )

        # Por usuario, para las acciones que no tienen equipo
        connection.execute(
            """
            UPDATE audit_log
            SET organization_id = (
                SELECT u.organization_id FROM users u
                WHERE u.username = audit_log.username COLLATE NOCASE
            )
            WHERE organization_id IS NULL
              AND device_id IS NULL
              AND username IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM users u
                  WHERE u.username = audit_log.username COLLATE NOCASE
                    AND u.organization_id IS NOT NULL
              )
            """
        )

        connection.commit()

    except Exception as error:
        print(f"[auditoria] No se pudo completar el relleno: {error}")

    finally:
        connection.close()

    return True


def log_audit(action, request=None, device_id=None, status=STATUS_REQUESTED,
              details=None, username=None, source_ip=None,
              organization_id=None):
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

        # Si quien llama no la indica, se deduce del equipo o del
        # usuario. Indicarla explicitamente sirve para las acciones de
        # plataforma, donde lo correcto es dejarla vacia.
        if organization_id is None:
            organization_id = resolve_organization(device_id, username)

        registro = (
            datetime.now(timezone.utc).isoformat(),
            str(action),
            device_id,
            username,
            source_ip,
            str(status),
            _format_details(details),
            organization_id
        )

        connection = get_connection()

        try:
            connection.execute(
                """
                INSERT INTO audit_log (
                    timestamp, action, device_id, username,
                    source_ip, status, details, organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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


def list_audit(limit=100, device_id=None, action=None, offset=0,
               organization_id=None):
    """
    Consulta el registro, de más reciente a más antiguo.

    Filtros opcionales por equipo y por acción. El límite se acota para que
    una consulta no pueda arrastrar el historial entero.

    organization_id ACOTA la consulta a una empresa: solo se devuelven
    sus registros, y los de plataforma o sin organizacion quedan fuera.
    Sin ese argumento se devuelve todo, asi que quien llame debe haber
    comprobado antes que tiene derecho a verlo.
    """

    limite = max(1, min(int(limit or 100), 500))
    desplazamiento = max(0, int(offset or 0))

    condiciones = []
    parametros = []

    if organization_id is not None:
        condiciones.append("organization_id = ?")
        parametros.append(organization_id)

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
                   source_ip, status, details, organization_id
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


def count_audit(device_id=None, action=None, organization_id=None):
    """Número total de registros que cumplen el filtro."""

    condiciones = []
    parametros = []

    if organization_id is not None:
        condiciones.append("organization_id = ?")
        parametros.append(organization_id)

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
