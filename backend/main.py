import asyncio
import base64
import json
import time
import uuid

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse
)

from backend.database import init_db, get_connection
from backend.models import DeviceRegister, DeviceHeartbeat
from backend import devices as devices_mod
from backend.devices import (
    register_device,
    enroll_device,
    get_device_id_for_token,
    update_heartbeat,
    update_system_info,
    update_installed_software,
    get_installed_software,
    get_devices
)
from backend.alerts import (
    detect_alerts,
    get_alerts,
    mark_alert_read,
    mark_all_alerts_read,
    alert_exists,
    alert_organization,
    backfill_alert_organizations
)
from backend.settings import (
    get_settings,
    get_retention_days,
    save_settings,
    migrate_settings_to_organization,
    UnknownSettingError
)
from backend import settings as ajustes_mod
from backend import recordings as recordings_mod
from backend.auth import (
    authenticate,
    change_password,
    validate_new_password,
    create_token,
    CHANGE_OK,
    CHANGE_CURRENT_INVALID,
    CHANGE_POLICY,
    verify_token,
    revoke_session_token,
    token_from_header,
    require_security_config,
    TOKEN_HOURS
)
from backend.recordings import (
    marcar_segmentos_unidos,
    extremos_de_los_segmentos,
    guardar_tramos,
    tramos_de_grabacion,
    add_recording,
    register_local_recording,
    get_recording_by_path,
    set_storage_state,
    storage_state_of,
    mark_stored,
    STORAGE_LOCAL_ONLY,
    STORAGE_PENDING,
    STORAGE_STORED,
    STORAGE_ERROR,
    add_invalid_recording,
    quarantine_recording,
    RecordingAlreadyExists,
    cleanup_orphan_parts,
    PART_SUFFIX,
    list_recordings,
    list_recordings_page,
    count_recordings,
    query_recordings,
    jornadas,
    get_recording,
    get_recordings_dir,
    set_keep,
    apply_retention,
    find_recording_by_path,
    RETENTION_DAYS
)
from backend.devices import (
    set_continuous_recording,
    get_continuous_recording,
    device_exists,
    get_device_organization,
    decommission_device,
    is_decommissioned,
    reinstate_device,
    link_device_history,
    unlink_device_history,
    device_history_chain,
    decommissioned_devices,
    LinkError
)
from backend.media import duration_for_storage, validate_recording
from backend import mailer
from backend import recovery
from backend import schedule as recording_schedule
from backend import power
from backend.queries import (
    create_query,
    discard_query,
    resolve_query,
    fail_device_queries,
    normalize_processes,
    normalize_services,
    RESPONSE_PREFIXES,
    KIND_PROCESSES,
    KIND_SERVICES,
    KIND_POWER,
    KIND_STORE,
    KIND_DELETE,
    QUERY_TIMEOUT_SECONDS
)
from backend.audit import (
    log_audit,
    backfill_organizations,
    list_audit,
    count_audit,
    get_request_context,
    STATUS_REQUESTED,
    STATUS_SUCCESS,
    STATUS_ERROR
)
from backend import installation
from backend import agent_releases
from backend import packaging
from backend import organizations
from backend import enrollment
from backend.users import (
    ensure_owner_migrated,
    grant_remote_control_to_existing_subadmins,
    is_platform_owner,
    organization_of,
    same_organization,
    set_organization,
    ROLE_PLATFORM_OWNER,
    get_user,
    get_user_by_id,
    find_by_email,
    list_users,
    create_user,
    delete_user,
    set_active,
    set_email,
    set_permissions,
    set_user_password,
    has_permission,
    is_owner,
    UserError,
    PERMISSIONS,
    ROLE_OWNER,
    ROLE_SUBADMIN
)
from backend.ratelimit import (
    seconds_until_unblocked,
    register_failure,
    reset as reset_failures
)


app = FastAPI(title="RemoteAdmin")


# Nombre de la cookie donde el navegador guarda el token de sesion
AUTH_COOKIE_NAME = "remoteadmin_token"

# Rutas /api/* accesibles sin autenticacion:
# - login/logout/me: flujo de sesion
# - health: comprobacion del servidor
# - register/heartbeat: las usa el Agent, que todavia no se autentica
OPEN_API_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/devices/register",
    "/api/devices/heartbeat",
    # Actualizacion del Agent: igual que el latido, lo pide el propio
    # Agent con su token individual, no una persona con sesion
    # abierta. Cada endpoint comprueba ese token por su cuenta.
    "/api/agent/version",
    "/api/agent/package",
    # Recuperacion: por definicion se usa SIN sesion. Ambos endpoints
    # tienen su propio limite de intentos y responden siempre lo mismo.
    "/api/auth/forgot",
    "/api/auth/reset",
    "/api/auth/reset/check"
}


@app.middleware("http")
async def require_authentication(request: Request, call_next):

    path = request.url.path

    # Solo se protege /api/*. El WebSocket /ws/agent usa otro scope y no pasa por aqui.
    needs_auth = (
        path.startswith("/api/")
        and path not in OPEN_API_PATHS
        # La subida de grabaciones la hace el Agent con su token
        # individual, no con sesión de panel; se valida dentro del
        # endpoint.
        and not path.endswith("/recordings/upload")
        and request.method != "OPTIONS"
    )

    if needs_auth:

        # El token puede venir en el header (frontend) o en la cookie
        # (peticiones del navegador como <img>, XHR y fetch).
        token = (
            token_from_header(request.headers.get("Authorization"))
            or request.cookies.get(AUTH_COOKIE_NAME)
        )

        if not verify_token(token):

            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "message": "No autenticado"
                }
            )

        # Suspension: una organizacion que no puede operar pierde el
        # panel entero, no endpoint por endpoint. Se comprueba antes
        # que el aislamiento porque es una condicion mas general.
        sin_acceso = _suspension_bloquea(request, path)

        if sin_acceso is not None:
            return sin_acceso

        # Aislamiento: si la ruta apunta a un equipo o a una grabacion,
        # tiene que ser de la organizacion de quien pide. Aqui, una sola
        # vez, para todas las rutas presentes y futuras.
        negado = _organizacion_bloquea(request, path)

        if negado is not None:
            return negado

        # Capacidades del plan. Va DESPUES del aislamiento: primero
        # se decide si puede tocar ese recurso, y solo entonces si su
        # plan incluye la funcion. Al reves se filtraria que existe
        # un equipo de otra empresa.
        #
        # En modo observacion esto no bloquea nada: solo anota.
        sin_plan = _plan_bloquea(request, path)

        if sin_plan is not None:
            return sin_plan

    return await call_next(request)


# Rutas con device_id que NO pasan por la sesion del panel: las usa el
# propio Agent con su token individual, que ya ata la peticion a su
# equipo. Comprobar aqui la organizacion no aportaria nada.
RUTAS_DE_AGENT = ("/recordings/upload",)


# Lo unico que sigue disponible cuando una organizacion no puede operar.
#
# Son las rutas que permiten ENTENDER la situacion y salir de ella: el
# panel necesita saber quien eres y en que estado esta tu empresa para
# poder ensenar el aviso, y nadie debe quedarse sin poder cerrar sesion.
# Todo lo demas se bloquea.
#
# El resto de rutas de sesion (login, logout, me) ni siquiera llegan
# aqui: estan en OPEN_API_PATHS y no pasan por esta comprobacion.
RUTAS_PERMITIDAS_SIN_ACCESO = (
    "/api/users/me",
)


def _suspension_bloquea(request, path):
    """
    Devuelve una respuesta de rechazo si la organizacion no puede operar.

    La suspension es una politica de acceso al PANEL, no una orden para
    apagar la infraestructura del cliente. Por eso vive aqui, en el
    camino de la sesion: los Agents entran por otro sitio —token
    individual, WebSocket, subida de grabaciones— y siguen funcionando
    con normalidad. Castigar al cliente dejando sus equipos sin vigilar
    por un asunto administrativo seria desproporcionado.

    Nada se borra. Al reactivar, el acceso vuelve tal cual estaba.

    El operador de la plataforma no se ve afectado: es quien tiene que
    poder mirar y reactivar una organizacion suspendida.
    """

    if path in RUTAS_PERMITIDAS_SIN_ACCESO:
        return None

    usuario = current_user(request)

    if usuario is None:
        return None

    if is_platform_owner(usuario):
        return None

    organizacion_id = organization_of(usuario)

    if organizacion_id is None:
        return None

    organizacion = organizations.get_organization(organizacion_id)

    if organizacion is None or organizacion["usable"]:
        return None

    return JSONResponse(
        status_code=403,
        content={
            "status": "error",
            # Marca estable para que el panel distinga esto de un
            # "no tienes permiso" corriente y pueda ensenar su aviso.
            "code": "organization_suspended",
            "organization_status": organizacion["subscription_status"],
            "suspended_at": organizacion["suspended_at"],
            "message": organizations.access_message(organizacion)
        }
    )


# ==============================
# CAPACIDADES DEL PLAN
# ==============================
#
# Que capacidad exige cada ruta. Lo que no aparece aqui no exige
# ninguna.
#
# Criterio de lo que NO entra, que es tan importante como lo que si:
#
#   - Las consultas de SOLO LECTURA (procesos, servicios, listados)
#     se quedan fuera de remote.actions. Mirar no es actuar, y un
#     plan basico sin poder ver nada no tendria sentido.
#
#   - La SUBIDA de grabaciones del Agent (/recordings/upload) se
#     queda fuera a proposito. La hace el equipo con su token, no una
#     persona: bloquearla dejaria al equipo acumulando archivos que
#     no puede entregar, y acabaria perdiendolos. Una restriccion
#     comercial no deberia costar datos del cliente.
#
#   - El ARCHIVADO a peticion (/recordings/{id}/store) si entra: eso
#     lo pide una persona desde el panel.

# Cuanto sigue viendose el aviso de mantenimiento sin que nadie lo
# renueve. Cinco minutos: lo bastante para que una reconexion normal
# no lo haga parpadear, y lo bastante poco para que un olvido no deje
# el aviso puesto en la pantalla de un cliente toda la tarde.
MANTENIMIENTO_GRACIA_SEGUNDOS = 300


CAPACIDAD_POR_RUTA = (
    # (sufijo de la ruta, metodos, capacidad)
    ("/recording/schedule", ("POST",), "scheduling"),
    ("/recording/continuous", ("POST",), "scheduling"),

    ("/screen", ("POST",), "remote.actions"),
    ("/maintenance", ("POST",), "remote.actions"),
    ("/clipboard", ("POST",), "remote.actions"),
    ("/input-lock", ("POST",), "remote.actions"),
    ("/screen/stop", ("POST",), "remote.actions"),
    ("/keyboard", ("POST",), "remote.actions"),
    ("/mouse/move", ("POST",), "remote.actions"),
    ("/mouse/click", ("POST",), "remote.actions"),
    ("/mouse/down", ("POST",), "remote.actions"),
    ("/mouse/up", ("POST",), "remote.actions"),
    ("/files/upload", ("POST",), "remote.actions"),

    ("/store", ("POST",), "recordings.store"),
)

# Rutas de energia: /api/devices/{id}/power/{accion}
PREFIJO_ENERGIA = "/power/"


def capacidad_requerida(path, metodo):
    """
    Que capacidad exige esta ruta, o None si no exige ninguna.

    Devolver None NO es un descuido: la mayoria de las rutas son de
    consulta o de administracion y no dependen del plan.
    """

    if not path.startswith("/api/"):
        return None

    if PREFIJO_ENERGIA in path and metodo == "POST":
        return "remote.actions"

    for sufijo, metodos, capacidad in CAPACIDAD_POR_RUTA:

        if path.endswith(sufijo) and metodo in metodos:
            return capacidad

    return None


# Modo de las capacidades.
#
#   "observacion"  anota lo que habria bloqueado y deja pasar
#   "activo"       bloquea de verdad
#
# Arranca en observacion a proposito, y cambiar esto es una decision
# comercial: con el bloqueo activo, una organizacion en un plan que
# no incluya una capacidad dejaria de poder usarla, aunque la venga
# usando.
MODO_CAPACIDADES = "observacion"


def _plan_bloquea(request, path):
    """
    Comprueba que el plan de la organizacion permita esta operacion.

    En modo observacion devuelve siempre None —no bloquea— pero deja
    anotado lo que habria bloqueado. Es lo que permite ver a quien
    afectaria el cambio antes de aplicarselo.

    El operador de la plataforma queda exento, como en las otras dos
    puertas: su trabajo es poder mirarlo todo.
    """

    capacidad = capacidad_requerida(path, request.method)

    if capacidad is None:
        return None

    usuario = current_user(request)

    if usuario is None:
        return None

    if is_platform_owner(usuario):
        return None

    organizacion = organization_of(usuario)

    if organizacion is None:
        return None

    if organizations.has_feature(organizacion, capacidad):
        return None

    plan = organizations.plan_efectivo(organizacion)

    if MODO_CAPACIDADES == "observacion":

        # No se bloquea. Se anota para poder mirarlo despues.
        log_audit(
            "plan.feature_observed", request=request,
            status=STATUS_ERROR,
            organization_id=organizacion,
            details=f"Se habria bloqueado {request.method} {path}: "
                    f"el plan {plan['label']} no incluye "
                    f"'{capacidad}'"
        )

        return None

    log_audit(
        "plan.feature_denied", request=request,
        status=STATUS_ERROR,
        organization_id=organizacion,
        details=f"{request.method} {path} rechazada: el plan "
                f"{plan['label']} no incluye '{capacidad}'"
    )

    return JSONResponse(
        status_code=403,
        content={
            "status": "error",
            # Marca estable para que el panel lo distinga de un "no
            # tienes permiso" corriente.
            "code": "plan_feature_required",
            "feature": capacidad,
            "plan": plan["label"],
            "message": f"El plan {plan['label']} no incluye esta "
                       "funcion. Amplia el plan para usarla."
        }
    )


def _organizacion_bloquea(request, path):
    """
    Devuelve una respuesta de rechazo si la ruta toca otra organizacion.

    None significa que puede seguir. Ante cualquier duda —usuario sin
    organizacion, recurso sin organizacion— se rechaza: es preferible
    que algo deje de verse a que una empresa vea lo de otra.
    """

    if path.endswith(RUTAS_DE_AGENT):
        return None

    partes = [t for t in path.split("/") if t]

    # /api/devices/{device_id}/...  y  /api/recordings/{id}/...
    if len(partes) < 3 or partes[0] != "api":
        return None

    recurso = partes[1]
    identificador = partes[2]

    if recurso not in ("devices", "recordings"):
        return None

    usuario = current_user(request)

    if usuario is None:
        return JSONResponse(
            status_code=401,
            content={"status": "error", "message": "No autenticado"}
        )

    # El operador de la plataforma trabaja con todas las organizaciones
    if is_platform_owner(usuario):
        return None

    if recurso == "devices":

        # Un equipo que no existe se deja pasar: que conteste el
        # endpoint con su propio 404. Asi un identificador inventado
        # y uno de otra empresa responden exactamente lo mismo.
        if not device_exists(identificador):
            return None

        if not same_organization(
            usuario, organization_of_device(identificador)
        ):
            return _no_encontrado()

        return None

    organizacion, existe = organization_of_recording(identificador)

    if not existe:
        return None

    if not same_organization(usuario, organizacion):
        return _no_encontrado()

    return None


# ==============================
# AUTORIZACION
# ==============================
#
# El usuario de una peticion se deduce SIEMPRE del token de sesion firmado.
# Nada de lo que mande el navegador —rol, identificador o lista de
# permisos— se tiene en cuenta: ocultar un boton es comodidad visual, no
# una medida de seguridad.

def current_user(request):
    """Usuario autenticado de esta peticion, o None."""

    token = (
        token_from_header(request.headers.get("Authorization"))
        or request.cookies.get(AUTH_COOKIE_NAME)
    )

    username = verify_token(token)

    return get_user(username) if username else None


# ==============================
# AISLAMIENTO ENTRE ORGANIZACIONES
# ==============================
#
# Cada empresa cliente ve lo suyo y nada mas. La comprobacion vive en un
# unico sitio —el middleware de abajo— y no repartida por los treinta y
# tantos endpoints que llevan un device_id en la ruta: con una sola
# puerta hay una sola cosa que revisar, y un endpoint nuevo queda
# protegido sin que nadie se acuerde de anadirle nada.
#
# La organizacion se deduce SIEMPRE del usuario de la sesion. Nunca se
# acepta un organization_id del navegador: seria pedirle al visitante
# que diga de que casa es.

def organization_of_device(device_id):
    """Organizacion a la que pertenece un equipo. None si no la tiene."""

    if not device_id:
        return None

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT organization_id FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()

    finally:
        connection.close()

    return fila["organization_id"] if fila else None


def organization_of_recording(recording_id):
    """
    Organizacion de una grabacion, derivada de su equipo.

    No se guarda la organizacion en la grabacion: se deriva del equipo,
    que es su dueno. Duplicarla abriria la puerta a que las dos copias
    discrepasen, y entonces habria que decidir cual manda.
    """

    try:
        identificador = int(recording_id)

    except (TypeError, ValueError):
        return None, False

    connection = get_connection()

    try:
        fila = connection.execute(
            """
            SELECT d.organization_id AS organization_id
            FROM recordings r
            LEFT JOIN devices d ON d.device_id = r.device_id
            WHERE r.id = ?
            """,
            (identificador,)
        ).fetchone()

    finally:
        connection.close()

    if fila is None:
        return None, False

    return fila["organization_id"], True


def _no_encontrado():
    """
    Respuesta para un recurso de otra organizacion.

    Se responde 404 y no 403 a proposito: un 403 confirmaria que el
    recurso existe, y con eso se puede recorrer la numeracion de otra
    empresa para saber cuantos equipos o grabaciones tiene.
    """

    return JSONResponse(
        status_code=404,
        content={"status": "error", "message": "Recurso no encontrado"}
    )


def _denegado(mensaje="No tienes permiso para esta accion"):

    return JSONResponse(
        status_code=403,
        content={"status": "error", "message": mensaje}
    )


def require_permission(request, permission):
    """
    Devuelve (usuario, None) si puede, o (None, respuesta 403) si no.

    El owner pasa siempre; al subadmin se le exige el permiso concreto. Un
    usuario desactivado no pasa nunca, aunque conserve una sesion.
    """

    usuario = current_user(request)

    if usuario is None:
        return None, JSONResponse(
            status_code=401,
            content={"status": "error", "message": "No autenticado"}
        )

    if not has_permission(usuario, permission):
        return None, _denegado()

    return usuario, None


def require_owner(request):
    """La administracion de usuarios es exclusiva del owner."""

    usuario = current_user(request)

    if usuario is None:
        return None, JSONResponse(
            status_code=401,
            content={"status": "error", "message": "No autenticado"}
        )

    if not is_owner(usuario) or not usuario.get("active"):
        return None, _denegado(
            "Solo el Owner puede administrar usuarios"
        )

    return usuario, None


connected_agents = {}
latest_screens = {}
latest_cursors = {}
latest_recording_status = {}

# Que sabe hacer el Agent de cada equipo conectado, segun lo que el
# mismo declara nada mas conectarse. En memoria y no en la base: vale
# solo mientras dure la conexion, y al reconectar lo vuelve a decir.
#
# Un Agent anterior no manda nada, asi que su equipo no aparece aqui.
# Esa ausencia es la senal: no hace falta tocar los equipos ya
# instalados ni guardar su version en ningun sitio.
agent_capabilities = {}


def agent_supports(device_id, capacidad):
    """Si el Agent conectado declaro saber hacer algo."""

    datos = agent_capabilities.get(device_id) or {}

    return capacidad in (datos.get("capabilities") or [])


# Si hay alguien con la sesion iniciada en cada equipo.
#
# El Agent corre partido en dos: un servicio, que funciona desde que
# arranca Windows, y un ayudante, que solo existe mientras hay un
# usuario con la sesion abierta. Todo lo que toca la pantalla -el
# aviso de mantenimiento, el teclado, el raton, el portapapeles, la
# grabacion- lo hace el ayudante.
#
# En memoria, como las capacidades: vale mientras dure la conexion.
# ==============================
# CUANTO CUESTA LA PANTALLA EN VIVO
# ==============================
#
# Para que: hay que decidir si merece la pena la conexion directa,
# que son unos 90 MB en cada equipo. Esa decision no se toma con una
# impresion -"va lento"- sino con numeros, y hoy no hay ninguno.
#
# Lo que se mide y por que ESOS tres:
#
#   fotogramas y bytes   lo que cuesta mantener una sesion abierta,
#                        que es lo que decide cuantas aguanta un
#                        servidor de un nucleo;
#
#   captura              lo que tarda el EQUIPO en hacer la foto y
#                        comprimirla, que no depende de la red;
#
#   ida y vuelta al      lo que se ahorraria yendo directo. Es el
#   equipo               numero que decide si los 90 MB valen la pena.
#
# Se guarda en memoria y por equipo. Es una medida, no un historial:
# al desconectarse se olvida.

import collections as _collections

VENTANA_DE_MEDIDA = 50

mediciones_de_pantalla = {}

# Cronometros abiertos: un ping por equipo como mucho. Pedir la
# medida dos veces a la vez no tiene sentido y la segunda se queda
# con la respuesta.
pings_pendientes = {}

# Lo que se espera un pong antes de darlo por perdido. Si un equipo
# tarda mas de dos segundos en contestar "pong" a "ping", el numero
# exacto da igual: ese remoto es inservible.
PLAZO_DEL_PING = 2.0


def anotar_fotograma(device_id, tamano, captura_ms=None):
    """Un fotograma mas de este equipo."""

    medida = mediciones_de_pantalla.get(device_id)

    if medida is None:
        medida = {
            "momentos": _collections.deque(maxlen=VENTANA_DE_MEDIDA),
            "bytes": _collections.deque(maxlen=VENTANA_DE_MEDIDA),
            "captura": _collections.deque(maxlen=VENTANA_DE_MEDIDA),
            "ida_y_vuelta_ms": None,
        }
        mediciones_de_pantalla[device_id] = medida

    medida["momentos"].append(time.monotonic())
    medida["bytes"].append(int(tamano or 0))

    if captura_ms is not None:

        try:
            medida["captura"].append(float(captura_ms))
        except (TypeError, ValueError):
            pass


def medida_de_pantalla(device_id):
    """
    Lo medido, en numeros que se puedan leer.

    Devuelve None si no hay nada medido todavia: es mejor no decir
    nada que inventar un cero que parece un dato.
    """

    medida = mediciones_de_pantalla.get(device_id)

    if not medida or len(medida["momentos"]) < 2:
        return None

    momentos = list(medida["momentos"])
    transcurrido = momentos[-1] - momentos[0]

    if transcurrido <= 0:
        return None

    fotogramas = len(momentos) - 1

    capturas = list(medida["captura"])

    return {
        "fps": round(fotogramas / transcurrido, 1),

        # Lo que viaja de verdad, ya en base64: es lo que paga el
        # servidor, no el tamano del JPEG antes de codificarlo.
        "kb_por_segundo": round(
            sum(list(medida["bytes"])[1:]) / transcurrido / 1024, 1
        ),

        "captura_ms": (
            round(sum(capturas) / len(capturas), 1) if capturas else None
        ),

        "ida_y_vuelta_ms": medida["ida_y_vuelta_ms"],
        "muestras": fotogramas,
    }


interactive_sessions = {}

# Si hay alguien con la sesion ABIERTA, que es otra cosa.
#
# Un equipo puede tener a una persona trabajando delante y aun asi
# no tener ayudante: su tarea solo se dispara al iniciar sesion, y
# en un equipo que no se apaga nunca ese momento no vuelve.
#
# Confundir las dos cosas hizo que el panel dijera "Nadie tiene la
# sesion iniciada" sobre dos kioscos en uso, con su pantalla
# encendida. Mandar a alguien a iniciar una sesion que ya esta
# abierta es peor que no decir nada.
#
# None = no se sabe (Agent anterior), que no es False.
interactive_logins = {}


def motivo_sin_escritorio(device_id):
    """
    Por que no se puede tocar la pantalla de este equipo, en
    palabras que se puedan leer. None si si se puede.
    """

    if hay_sesion_interactiva(device_id):
        return None

    if interactive_logins.get(device_id):

        return (
            "El equipo tiene la sesion iniciada, pero el ayudante de "
            "VEYRA no esta activo. Se esta intentando arrancar; "
            "vuelve a probar en un minuto."
        )

    if interactive_logins.get(device_id) is None:

        # Agent anterior: solo consta que no hay ayudante.
        return (
            "El equipo no tiene el ayudante de VEYRA activo. Suele "
            "ser que nadie ha iniciado sesion."
        )

    return (
        "Nadie tiene la sesion iniciada en el equipo: no hay "
        "pantalla que mostrar."
    )


def hay_sesion_interactiva(device_id):
    """
    Si el equipo tiene escritorio ahora mismo.

    Ante la duda, SI. Un Agent anterior a este cambio nunca manda el
    aviso, y su equipo no aparece aqui: suponer que no tiene sesion
    dejaria sin control remoto a todos los equipos ya instalados por
    haber añadido una comprobacion.
    """

    return interactive_sessions.get(device_id, True)


@app.on_event("startup")
def startup():
    # init_db PRIMERO: desde G4a la comprobacion de la contrasena por defecto
    # mira el hash vigente en auth_state, que no existe hasta crear las
    # tablas. Si la configuracion es invalida, el arranque falla igual un
    # momento despues; crear tablas vacias no cambia nada.
    init_db()
    # Identidad de esta instalacion. Se crea una sola vez y su
    # identificador sobrevive a los reinicios; no se crea ninguna
    # organizacion por el hecho de existir.
    installation.ensure_installation()
    # La cuenta unica anterior pasa a ser el owner. Idempotente: si ya hay
    # usuarios, no hace nada.
    ensure_owner_migrated()
    # Y la instalacion existente pasa a ser la primera organizacion, con
    # sus equipos y usuarios asociados. Tambien idempotente.
    organizations.ensure_default_organization()
    # Con las organizaciones ya asignadas, se rellena la organizacion de
    # los registros historicos de auditoria y de avisos. Solo donde se
    # puede deducir con seguridad; el resto se queda en NULL.
    backfill_organizations()
    backfill_alert_organizations()
    # El control remoto pasa a exigir permiso de usuario. A quien ya
    # daba soporte se le concede, para no quitarselo sin avisar.
    grant_remote_control_to_existing_subadmins()
    # La configuracion que habia era de la unica empresa que existia:
    # se copia a su organizacion para que no cambie nada de lo que ya
    # estaba funcionando.
    migrate_settings_to_organization(
        organizations.ensure_default_organization()
    )
    # Exige AUTH_SECRET_KEY en el .env; si falta, el arranque falla
    require_security_config()
    # Subidas interrumpidas por un reinicio o una caída anterior
    cleanup_orphan_parts()
    # Marca visible en la consola para confirmar que ESTE código está corriendo
    print("[AUTH] Middleware de autenticación ACTIVO (protege /api/*)")


# Retención automática: limpia grabaciones más antiguas que el periodo
# configurado (15, 30 o 90 días) una vez
# al día. Corre en el event loop del proceso; con --reload se recrea en cada
# recarga sin dejar hilos colgados.
_retention_task = None


async def _retention_loop():

    while True:

        try:
            loop = asyncio.get_running_loop()
            # Se lee en cada pasada: cambiar la retencion desde el panel
            # surte efecto en la siguiente limpieza, sin reiniciar.
            #
            # Una pasada por organizacion: cada empresa tiene su propio
            # periodo, y aplicar el de una a las grabaciones de otra
            # borraria material que todavia debia conservarse.
            result = {}

            for organizacion in organizations.list_organizations():

                dias = get_retention_days(organizacion["id"])

                parcial = await loop.run_in_executor(
                    None, apply_retention, dias, organizacion["id"]
                )

                result[organizacion["name"]] = parcial

            # result esta indexado por NOMBRE de empresa, y las claves
            # "deleted" y "skipped_traversal" viven dentro de cada parcial.
            # Antes se pedian al contenedor y saltaba KeyError: 'deleted'
            # en cada pasada, de modo que la retencion borraba en silencio:
            # se ejecutaba bien, pero su resumen nunca llegaba al registro.
            borradas = sum(
                len(parcial["deleted"]) for parcial in result.values()
            )
            fuera = sum(
                len(parcial["skipped_traversal"])
                for parcial in result.values()
            )

            if borradas or fuera:
                print(f"[RETENCIÓN] {result}")

        except Exception as error:
            print(f"[RETENCIÓN] error: {error}")

        # Una vez al día
        await asyncio.sleep(24 * 3600)


@app.on_event("startup")
async def start_retention():
    global _retention_task
    _retention_task = asyncio.create_task(_retention_loop())


@app.on_event("shutdown")
async def stop_retention():
    global _retention_task
    if _retention_task is not None:
        _retention_task.cancel()
        _retention_task = None


@app.get("/api/health")
def health():
    # El campo "auth" permite comprobar en vivo si el servidor tiene esta versión
    return {"status": "ok", "auth": "required"}


def _agent_unauthorized():
    return JSONResponse(
        status_code=401,
        content={
            "status": "error",
            "message": "Agent no autorizado"
        }
    )


@app.post("/api/devices/register")
def register(
    data: DeviceRegister,
    request: Request,
    x_agent_token: str = Header(default=None)
):
    # Agent YA enrolado: se autentica con su token individual y la identidad
    # se deriva del token, nunca del device_id que envíe el cliente.
    authenticated_device_id = get_device_id_for_token(x_agent_token)

    if authenticated_device_id:

        return register_device(
            DeviceRegister(
                device_id=authenticated_device_id,
                hostname=data.hostname,
                operating_system=data.operating_system,
                ip_address=data.ip_address
            )
        )

    # A partir de aquí es un ALTA: solo esta rama está limitada por IP. Un
    # Agent ya enrolado (rama de arriba) nunca se ve afectado, aunque comparta
    # IP con quien esté agotando intentos.
    client_ip = request.client.host if request.client else "desconocido"

    blocked_for = seconds_until_unblocked(client_ip, scope="enroll")

    if blocked_for:

        # Se responde ANTES de validar el token: estando bloqueada, la IP no
        # puede enrolar aunque presente una credencial valida.
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(blocked_for)},
            content={
                "status": "error",
                "message": (
                    "Demasiados intentos de alta fallidos. "
                    f"Inténtalo de nuevo en {blocked_for} segundos."
                )
            }
        )

    # La organizacion sale de la CREDENCIAL, nunca del cuerpo de la
    # peticion. Un Agent que enviara organization_id no conseguiria
    # nada: ese campo no se lee en ningun punto de este endpoint.
    #
    # El AGENT_TOKEN compartido YA NO da de alta: era una puerta global
    # con la que cualquiera que lo tuviera podia meter equipos en el
    # sistema, y estaba en el .env de cada equipo administrado. Los
    # Agents ya enrolados no se ven afectados: se autentican con su
    # token individual, que se comprueba mas arriba.
    organizacion = enrollment.organization_for_token(x_agent_token)

    # Agent NUEVO: hace falta una credencial de alta de la organizacion
    # en la que debe entrar. No hay otra via.
    if organizacion is None:

        # Solo se anota la IP y la hora: nunca el token presentado.
        register_failure(client_ip, scope="enroll")

        return _agent_unauthorized()

    # El plan de la organizacion marca cuantos equipos admite
    try:
        organizations.check_limit(organizacion, "devices")

    except organizations.OrganizationError as limite:

        log_audit(
            "device.enroll", status=STATUS_ERROR,
            source_ip=client_ip, details=str(limite)
        )

        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": str(limite)}
        )

    device_id, agent_token = enroll_device(
        data.hostname,
        data.operating_system,
        data.ip_address,
        organization_id=organizacion
    )

    # El equipo nace grabando, si la empresa lo quiere asi.
    #
    # Antes nacia sin horario y con el interruptor apagado, y habia
    # que configurarlo a mano uno por uno. Con treinta equipos eso es
    # una tarde y la certeza de que alguno se queda sin grabar hasta
    # el dia que haga falta esa grabacion.
    #
    # Es un valor por DEFECTO: se aplica aqui, en el alta, y a partir
    # de ese momento el equipo tiene el suyo. Apagarlo despues desde
    # el panel no lo deshace nada.
    #
    # Si esto falla, el alta NO se cae: un equipo dado de alta sin
    # horario se arregla con dos clics, pero un alta que revienta
    # deja la maquina fuera del sistema.
    try:

        por_defecto = ajustes_mod.grabacion_por_defecto(organizacion)

        if por_defecto is not None:

            recording_schedule.set_schedule(device_id, por_defecto)
            set_continuous_recording(device_id, True)

            print(
                f"[alta] {device_id} nace con horario "
                f"{por_defecto['start_time']}-{por_defecto['end_time']}"
            )

    except Exception as problema:
        print(f"[alta] No se pudo aplicar el horario por defecto: {problema}")

    # Alta correcta: se limpia el historial de esa IP, igual que en el login.
    reset_failures(client_ip, scope="enroll")

    return {
        "status": "enrolled",
        "device_id": device_id,
        # Único momento en que el token viaja en claro: el servidor
        # guarda solo su hash y no puede volver a mostrarlo.
        "agent_token": agent_token
    }


@app.post("/api/devices/heartbeat")
def heartbeat(
    data: DeviceHeartbeat,
    x_agent_token: str = Header(default=None)
):
    # Token individual. La credencial de alta no vale aquí: solo
    # autoriza el alta de un Agent nuevo.
    device_id = get_device_id_for_token(x_agent_token)

    if device_id is None:
        return _agent_unauthorized()

    # La identidad viene del token, no del cuerpo: un Agent no puede enviar
    # heartbeats en nombre de otro dispositivo.
    return update_heartbeat(
        DeviceHeartbeat(
            device_id=device_id,
            ip_address=data.ip_address
        )
    )


@app.get("/api/devices")
def devices(request: Request):

    usuario, error = require_permission(request, "devices.view")

    if error:
        return error

    # Cada empresa ve sus equipos. El operador de la plataforma los ve
    # todos, que es lo que necesita para dar soporte.
    if not is_platform_owner(usuario):
        return [
            equipo for equipo in get_devices()
            if equipo.get("organization_id") == organization_of(usuario)
        ]

    # Detecta cambios de estado antes de devolver la lista
    detect_alerts()
    return get_devices()


@app.put("/api/devices/{device_id}/alias")
def set_device_alias(device_id: str, data: dict, request: Request):
    """
    Pone, cambia o quita el nombre con que la empresa llama al equipo.

    Es una etiqueta de VEYRA. No toca el nombre de Windows, ni el
    device_id, ni el token, ni la organizacion, ni el historial: solo
    como se le llama en el panel.

    Exige devices.manage y no devices.view a proposito: ver el
    inventario y renombrar lo que hay en el no son la misma
    responsabilidad. Es el mismo permiso que dar de baja un equipo.

    Un alias vacio lo QUITA, y el panel vuelve a enseñar el nombre de
    Windows. No se borra nada mas.
    """

    usuario, error = require_permission(request, "devices.manage")

    if error:
        return error

    if not device_exists(device_id):
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Dispositivo no encontrado"}
        )

    # Un equipo de otra empresa no se renombra, ni aunque se conozca
    # su identificador.
    if not same_organization(usuario, organization_of_device(device_id)):
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Dispositivo no encontrado"}
        )

    try:
        resultado = devices_mod.set_alias(device_id, data.get("alias"))

    except devices_mod.AliasError as problema:

        log_audit(
            "device.alias", request=request, device_id=device_id,
            status=STATUS_ERROR, details="Nombre rechazado: %s" % problema
        )

        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(problema)}
        )

    if resultado is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Dispositivo no encontrado"}
        )

    log_audit(
        "device.alias", request=request, device_id=device_id,
        status=STATUS_SUCCESS,
        details={"alias": resultado["alias"] or "(sin nombre propio)"}
    )

    return {"status": "ok", **resultado}


@app.post("/api/devices/{device_id}/decommission")
def device_decommission(device_id: str, request: Request):
    """
    Da de baja un equipo. No borra nada.

    Se conservan la fila, el hostname, las grabaciones, las alertas y
    el rastro de auditoria. Lo que se retira es el acceso: la
    credencial del Agent queda revocada y el equipo no puede volver a
    conectarse con ella.

    La plaza que ocupaba en el plan queda libre.

    Que este equipo sea de la organizacion de quien lo pide ya lo ha
    comprobado el middleware, que mira todas las rutas con un
    device_id. Aqui se comprueba el permiso, que es lo otro.
    """

    usuario, error = require_permission(request, "devices.manage")

    if error:
        return error

    if not device_exists(device_id):

        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "El equipo no existe"}
        )

    if not decommission_device(device_id):

        # Ya estaba de baja: no es un error, pero tampoco una baja
        # nueva. Se dice tal cual en vez de fingir que se hizo algo.
        return {
            "status": "ok",
            "device_id": device_id,
            "already_decommissioned": True,
            "message": "El equipo ya estaba dado de baja"
        }

    # La baja YA ocurrio. Que la anotacion falle no la deshace, y
    # devolver un error haria creer que no se hizo nada: el equipo
    # quedaria sin acceso mientras el panel dice que sigue en
    # servicio, que es peor que no avisar.
    #
    # Asi que se informa de las dos cosas por separado: la operacion
    # salio bien, y si no quedo anotada tambien se dice. El evento no
    # se pierde: log_audit lo guarda en el respaldo.
    anotado = log_audit(
        "device.decommissioned", request=request,
        device_id=device_id,
        details="Baja del equipo: credencial revocada, "
                "historial conservado"
    )

    respuesta = {
        "status": "ok",
        "device_id": device_id,
        "already_decommissioned": False,
        "audit_recorded": bool(anotado),
        "message": "Equipo dado de baja. Se conservan sus grabaciones "
                   "y su historial."
    }

    if not anotado:
        respuesta["message"] += (
            " Aviso: la operacion no se pudo anotar en el registro de "
            "auditoria y quedo guardada aparte."
        )

    return respuesta


@app.post("/api/devices/{device_id}/reinstate")
def device_reinstate(device_id: str, request: Request):
    """
    Devuelve un equipo al servicio.

    Quita la marca de baja y recupera su historial, pero NO devuelve
    la credencial: sigue revocada. Para volver a conectarse, el equipo
    tiene que darse de alta otra vez con una credencial de alta
    valida.

    Asi, reincorporar no es nunca una forma de resucitar un token
    revocado.
    """

    usuario, error = require_permission(request, "devices.manage")

    if error:
        return error

    if not device_exists(device_id):

        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "El equipo no existe"}
        )

    if not reinstate_device(device_id):

        return {
            "status": "ok",
            "device_id": device_id,
            "was_decommissioned": False,
            "message": "El equipo ya estaba en servicio"
        }

    anotado = log_audit(
        "device.reinstated", request=request,
        device_id=device_id,
        details="Reincorporacion: la credencial sigue revocada, "
                "hay que volver a dar de alta el equipo"
    )

    respuesta = {
        "status": "ok",
        "device_id": device_id,
        "was_decommissioned": True,
        "audit_recorded": bool(anotado),
        "message": "Equipo reincorporado. Vuelve a instalarlo con una "
                   "credencial de alta para que se conecte."
    }

    if not anotado:
        respuesta["message"] += (
            " Aviso: la operacion no se pudo anotar en el registro de "
            "auditoria y quedo guardada aparte."
        )

    return respuesta


@app.get("/api/devices/decommissioned")
def devices_decommissioned(request: Request):
    """
    Equipos dados de baja que todavia pueden ceder su historial.

    Es lo que se ofrece al vincular un equipo recien instalado. Solo
    de la organizacion de quien pregunta: la organizacion se deduce de
    la sesion y nunca se acepta del navegador.

    Los que ya tienen heredero no salen: un historial, un sucesor.
    """

    usuario, error = require_permission(request, "devices.manage")

    if error:
        return error

    if is_platform_owner(usuario):

        # El operador no pertenece a ninguna organizacion, asi que no
        # hay un conjunto suyo que ofrecer.
        return {"status": "ok", "devices": []}

    organizacion = organization_of(usuario)

    if organizacion is None:
        return {"status": "ok", "devices": []}

    return {
        "status": "ok",
        "devices": decommissioned_devices(organizacion)
    }


@app.post("/api/devices/{device_id}/link-history")
def device_link_history(device_id: str, data: dict, request: Request):
    """
    Vincula este equipo con el historial de uno dado de baja.

    No se mueve ninguna grabacion: el equipo nuevo anota a quien
    sucede, y cada grabacion conserva su device_id, su ruta y su
    nombre. Por eso la operacion es reversible sin perder nada.

    La credencial del equipo viejo NO se reactiva nunca. El equipo
    nuevo sigue con la suya, que es la que obtuvo al darse de alta.

    Que este equipo sea de la organizacion de quien lo pide ya lo
    comprueba el middleware. El equipo ANTERIOR se comprueba aqui,
    porque llega en el cuerpo y el middleware no lo ve.
    """

    usuario, error = require_permission(request, "devices.manage")

    if error:
        return error

    anterior = (data or {}).get("previous_device_id")

    if not anterior:

        return JSONResponse(
            status_code=400,
            content={"status": "error",
                     "message": "Falta el equipo anterior"}
        )

    # El equipo anterior tiene que ser de la MISMA organizacion que
    # quien pide. link_device_history vuelve a compararlo contra el
    # equipo nuevo, pero esto evita siquiera confirmar que existe un
    # identificador de otra empresa.
    if not is_platform_owner(usuario):

        if get_device_organization(anterior) != organization_of(usuario):

            log_audit("device.history_linked", request=request,
                      device_id=device_id, status=STATUS_ERROR,
                      details="Rechazada: el equipo anterior es de "
                              "otra organizacion")

            return JSONResponse(
                status_code=404,
                content={"status": "error",
                         "message": "El equipo anterior no existe"}
            )

    try:
        link_device_history(device_id, anterior)

    except LinkError as problema:

        log_audit("device.history_linked", request=request,
                  device_id=device_id, status=STATUS_ERROR,
                  details=f"Rechazada: {problema}")

        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": str(problema)}
        )

    anotado = log_audit(
        "device.history_linked", request=request,
        device_id=device_id,
        details=f"Hereda el historial de {anterior}. "
                "No se movio ninguna grabacion."
    )

    respuesta = {
        "status": "ok",
        "device_id": device_id,
        "previous_device_id": anterior,
        "audit_recorded": bool(anotado),
        "message": "Historial vinculado. Las grabaciones anteriores "
                   "siguen donde estaban."
    }

    if not anotado:
        respuesta["message"] += (
            " Aviso: la operacion no se pudo anotar en el registro de "
            "auditoria y quedo guardada aparte."
        )

    return respuesta


@app.post("/api/devices/{device_id}/unlink-history")
def device_unlink_history(device_id: str, request: Request):
    """
    Deshace la vinculacion.

    Como al vincular no se movio nada, deshacerlo no puede perder
    nada: los dos historiales vuelven a estar separados.
    """

    usuario, error = require_permission(request, "devices.manage")

    if error:
        return error

    anterior = unlink_device_history(device_id)

    if anterior is None:

        return {
            "status": "ok",
            "device_id": device_id,
            "was_linked": False,
            "message": "Este equipo no tenia historial vinculado"
        }

    log_audit("device.history_unlinked", request=request,
              device_id=device_id,
              details=f"Deja de heredar el historial de {anterior}")

    return {
        "status": "ok",
        "device_id": device_id,
        "previous_device_id": anterior,
        "was_linked": True,
        "message": "Vinculacion deshecha. No se perdio ninguna "
                   "grabacion."
    }


@app.get("/api/organizations/{organization_id}/plan")
def organization_plan(organization_id: int, request: Request):
    """
    El plan efectivo de una organizacion y sus excepciones.

    Solo la plataforma. Una organizacion no puede consultar —ni
    cambiar— las excepciones de otra, ni las suyas: son un acuerdo
    comercial, no un ajuste del cliente.
    """

    _, error = require_platform_owner(request)

    if error:
        return error

    if organizations.get_organization(organization_id) is None:

        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "La organizacion no existe"}
        )

    plan = organizations.plan_efectivo(organization_id)

    return {
        "status": "ok",
        "organization_id": organization_id,
        "plan": {
            clave: (sorted(valor) if isinstance(valor, frozenset)
                    else valor)
            for clave, valor in plan.items()
        },
        "usage": organizations.organization_usage(organization_id),
        "exceptions": organizations.get_plan_exceptions(organization_id),
        "available_exceptions": list(organizations.EXCEPCIONES)
    }


@app.post("/api/organizations/{organization_id}/plan")
def organization_plan_set(organization_id: int, data: dict,
                          request: Request):
    """
    Concede, cambia o retira una excepcion del plan.

    Reservado al operador de la plataforma. Es la UNICA via: las
    claves plan.* no estan en ALLOWED_SETTINGS, asi que /api/settings
    las rechaza, y un cliente no puede concederse capacidades.

    Un valor vacio retira la excepcion. Retirarla no borra nada de la
    organizacion: solo deja de concederse lo que concedia.
    """

    _, error = require_platform_owner(request)

    if error:

        log_audit("plan.exception_set", request=request,
                  status=STATUS_ERROR,
                  details="Rechazada: no es operador de la plataforma")

        return error

    clave = (data or {}).get("key")
    valor = (data or {}).get("value")

    try:
        organizations.set_plan_exception(organization_id, clave, valor)

    except organizations.OrganizationError as problema:

        log_audit("plan.exception_set", request=request,
                  status=STATUS_ERROR,
                  organization_id=organization_id,
                  details=f"Rechazada: {problema}")

        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(problema)}
        )

    # Queda quien, cuando y que. El "por que" va en
    # plan.courtesy_note, que se guarda igual que las demas.
    anotado = log_audit(
        "plan.exception_set", request=request,
        organization_id=organization_id,
        details=(f"{clave} = {valor}" if valor
                 else f"{clave} retirada")
    )

    plan = organizations.plan_efectivo(organization_id)

    return {
        "status": "ok",
        "organization_id": organization_id,
        "key": clave,
        "value": valor,
        "audit_recorded": bool(anotado),
        "features": sorted(plan["features"]),
        "message": "Excepcion aplicada" if valor else "Excepcion retirada"
    }


@app.get("/api/alerts")
def alerts(request: Request):

    usuario, error = require_permission(request, "dashboard.view")

    if error:
        return error

    if not is_platform_owner(usuario):

        detect_alerts()

        # Se acota en la consulta, con la columna propia del aviso: es
        # mas directo que cruzar con la lista de equipos y no depende de
        # que el equipo siga existiendo.
        return get_alerts(organization_id=organization_of(usuario))

    detect_alerts()
    return get_alerts()


@app.post("/api/alerts/{alert_id}/read")
def alert_read(alert_id: int, request: Request):

    usuario, error = require_permission(request, "dashboard.view")

    if error:
        return error

    if not is_platform_owner(usuario):

        # Un aviso de otra organizacion responde igual que uno que no
        # existe: confirmar su existencia ya seria decir de mas.
        if not alert_exists(alert_id) \
                or alert_organization(alert_id) != organization_of(usuario):

            return JSONResponse(
                status_code=404,
                content={"status": "error",
                         "message": "Aviso no encontrado"}
            )

    return mark_alert_read(alert_id)


@app.post("/api/alerts/read-all")
def alerts_read_all(request: Request):

    usuario, error = require_permission(request, "dashboard.view")

    if error:
        return error

    # Cada uno da por leidos los suyos. Sin acotarlo, un Owner silenciaria
    # los avisos de todas las empresas.
    ambito = None if is_platform_owner(usuario) else organization_of(usuario)

    return mark_all_alerts_read(organization_id=ambito)


def _ambito_de_settings(usuario):
    """
    Sobre que configuracion actua este usuario.

    Cada empresa tiene la suya; el operador de la plataforma trabaja con
    la de la instalacion. La organizacion sale SIEMPRE de la sesion: un
    organization_id en el cuerpo de la peticion no se lee en ningun
    punto, asi que no hay forma de escribir en la configuracion de otra
    empresa.
    """

    if is_platform_owner(usuario):
        return None

    return organization_of(usuario)


@app.get("/api/settings")
def settings(request: Request):

    usuario, error = require_permission(request, "settings.view")

    if error:
        return error

    return get_settings(_ambito_de_settings(usuario))


@app.post("/api/settings")
async def update_settings(data: dict, request: Request):
    """
    Guarda ajustes. Solo las claves de la lista blanca de settings.py.

    Una clave desconocida devuelve 400 y NO escribe nada: este endpoint no
    puede usarse para colar estado de autenticacion en la base.
    """

    usuario, error = require_permission(request, "settings.edit")

    if error:
        return error

    ambito = _ambito_de_settings(usuario)

    anterior = get_retention_days(ambito)

    try:
        resultado = save_settings(data, organization_id=ambito)

    except UnknownSettingError as error:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(error)}
        )

    # Si han cambiado los dias de retencion, los Agents afectados deben
    # enterarse ya; si no, seguirian aplicando el periodo anterior hasta
    # su proxima reconexion. Solo los de esa organizacion.
    if get_retention_days(ambito) != anterior:
        await broadcast_retention_policy(organization_id=ambito)

    return resultado


# ==============================
# AUTENTICACION
# ==============================

@app.post("/api/auth/login")
def auth_login(data: dict, request: Request):

    # Identifica al cliente por IP. Sin cliente (algunos tests) se usa
    # "desconocido" como clave única en vez de saltarse el límite.
    client_ip = request.client.host if request.client else "desconocido"

    blocked_for = seconds_until_unblocked(client_ip)

    if blocked_for:

        # No se registra el usuario tecleado: en un login fallido no está
        # verificado y, si alguien se equivoca de campo, la contraseña
        # acabaría escrita en la auditoría. Solo la IP y la hora.
        log_audit(
            "auth.login", status=STATUS_ERROR, source_ip=client_ip,
            details="Bloqueado por demasiados intentos fallidos"
        )

        # Se responde ANTES de comprobar la contraseña: un intento bloqueado
        # no puede iniciar sesión aunque las credenciales sean correctas.
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(blocked_for)},
            content={
                "status": "error",
                "message": (
                    "Demasiados intentos fallidos. "
                    f"Inténtalo de nuevo en {blocked_for} segundos."
                )
            }
        )

    token = authenticate(
        data.get("username", ""),
        data.get("password", "")
    )

    if not token:

        # Solo se anota la IP y la hora: nunca el usuario ni la contraseña.
        register_failure(client_ip)

        log_audit(
            "auth.login", status=STATUS_ERROR, source_ip=client_ip,
            details="Credenciales incorrectas"
        )

        return JSONResponse(
            status_code=401,
            content={
                "status": "error",
                "message": "Usuario o contraseña incorrectos"
            }
        )

    # Login correcto: se limpia el historial para no penalizar al usuario
    # legítimo que se equivocó antes de acertar.
    reset_failures(client_ip)

    # Aquí el usuario SÍ está verificado: authenticate() acaba de comprobar
    # la contraseña. Aún no hay cookie, así que se pasa explícitamente.
    log_audit(
        "auth.login", status=STATUS_SUCCESS,
        username=verify_token(token), source_ip=client_ip,
        details="Inicio de sesión"
    )

    # La cookie la emite el backend, no JavaScript: así puede ser HttpOnly y
    # queda fuera del alcance de cualquier script de la página.
    # El token viaja SOLO en la cookie: no se incluye en el cuerpo para que
    # ningún script de la página pueda leerlo ni volver a almacenarlo.
    response = JSONResponse(
        content={
            "status": "ok",
            "username": data.get("username", "")
        }
    )

    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=TOKEN_HOURS * 3600,  # misma duración que la expiración del token
        path="/",
        httponly=True,
        secure=True,
        samesite="strict"
    )

    return response


@app.post("/api/auth/password")
def auth_change_password(data: dict, request: Request):
    """
    Cambia la contrasena del panel.

    Exige sesion valida (lo garantiza el middleware) Y volver a teclear la
    contrasena actual: con solo una cookie robada no se puede tomar la
    cuenta.

    Al cambiarla se cierran TODAS las sesiones y se reemite solo la de quien
    hizo el cambio, para que no se eche a si mismo.
    """

    # Usuario e IP del contexto real de la peticion, nunca del cuerpo. Se
    # leen ANTES del cambio: despues, el token actual ya no sera valido y no
    # habria forma de saber quien lo hizo.
    token_actual = (
        token_from_header(request.headers.get("Authorization"))
        or request.cookies.get(AUTH_COOKIE_NAME)
    )

    usuario = verify_token(token_actual)
    ip_origen = request.client.host if request.client else "desconocido"

    def auditar(estado, detalle):
        log_audit("auth.password_change", status=estado,
                  username=usuario, source_ip=ip_origen, details=detalle)

    bloqueado = seconds_until_unblocked(ip_origen, scope="password")

    if bloqueado:

        auditar(STATUS_ERROR, "Bloqueado por demasiados intentos fallidos")

        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(bloqueado)},
            content={
                "status": "error",
                "message": (
                    "Demasiados intentos fallidos. "
                    f"Intentalo de nuevo en {bloqueado} segundos."
                )
            }
        )

    # Cada usuario cambia la SUYA. El nombre sale del token, nunca del
    # cuerpo: si no, cualquiera podria cambiar la de otro.
    resultado, detalle = change_password(
        data.get("current_password", ""),
        data.get("new_password", ""),
        username=usuario
    )

    if resultado == CHANGE_CURRENT_INVALID:

        # Cuenta como intento fallido: es un intento de adivinar la
        # contrasena, aunque venga de una sesion abierta.
        register_failure(ip_origen, scope="password")

        auditar(STATUS_ERROR, "Contrasena actual incorrecta")

        return JSONResponse(
            status_code=403,
            content={"status": "error",
                     "message": "La contrasena actual no es correcta"}
        )

    if resultado == CHANGE_POLICY:

        # No cuenta como intento fallido: quien llega aqui ya demostro
        # conocer la contrasena actual. El motivo describe la regla
        # incumplida, nunca la contrasena.
        auditar(STATUS_ERROR, f"Contrasena nueva rechazada: {detalle}")

        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": detalle}
        )

    # Cambio correcto. 'detalle' es la fecha de corte de sesiones.
    corte = detalle

    reset_failures(ip_origen, scope="password")

    auditar(
        STATUS_SUCCESS,
        "Contrasena cambiada; se cerraron las demas sesiones"
    )

    respuesta = JSONResponse(
        content={
            "status": "ok",
            "message": "Contrasena actualizada. Las demas sesiones se han cerrado."
        }
    )

    # La sesion propia se reemite: el corte acaba de matar la anterior, y
    # create_token() nace ya por encima de esa fecha.
    respuesta.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=create_token(usuario, issued_at=corte),
        max_age=TOKEN_HOURS * 3600,
        path="/",
        httponly=True,
        secure=True,
        samesite="strict"
    )

    return respuesta


# Respuesta unica de la recuperacion. Se escribe una sola vez y se usa en
# todos los caminos: si cada rama redactara la suya, antes o despues una
# acabaria delatando si la direccion existe.
RESPUESTA_RECUPERACION = (
    "Si esa direccion corresponde a una cuenta, se ha enviado un correo "
    "con las instrucciones para restablecer la contrasena."
)


@app.post("/api/auth/forgot")
def auth_forgot(data: dict, request: Request):
    """
    Pide un enlace de recuperacion.

    Responde SIEMPRE lo mismo, exista o no la direccion y falle o no el
    envio. Un panel que contesta distinto segun el caso es un comprobador
    de cuentas para cualquiera.
    """

    ip_origen = request.client.host if request.client else "desconocido"

    generica = {"status": "ok", "message": RESPUESTA_RECUPERACION}

    bloqueado = seconds_until_unblocked(ip_origen, scope="recovery")

    if bloqueado:

        log_audit(
            "auth.password_recovery", status=STATUS_ERROR,
            source_ip=ip_origen,
            details="Bloqueado por demasiadas peticiones"
        )

        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(bloqueado)},
            content={
                "status": "error",
                "message": (
                    "Demasiadas peticiones. "
                    f"Intentalo de nuevo en {bloqueado} segundos."
                )
            }
        )

    # Cuenta antes de saber el resultado: si solo se contaran los aciertos,
    # el propio contador revelaria que direcciones existen.
    register_failure(ip_origen, scope="recovery")

    correo = (data.get("email") or "").strip()

    usuario = find_by_email(correo) if correo else None

    if usuario is None:

        # Se deja constancia del intento, sin la direccion tecleada: podria
        # ser la de cualquiera y no aporta nada al historial.
        log_audit(
            "auth.password_recovery", status=STATUS_ERROR,
            source_ip=ip_origen,
            details="Peticion para una direccion sin cuenta activa"
        )

        return generica

    token = recovery.create_token(usuario["id"])

    try:
        mailer.send_password_reset(usuario["email"], token)

        log_audit(
            "auth.password_recovery", request=None, status=STATUS_SUCCESS,
            username=usuario["username"], source_ip=ip_origen,
            details="Enlace de recuperacion enviado"
        )

    except mailer.MailError as error:

        # El enlace no sirve de nada si no ha salido: se anula.
        recovery.invalidate_user_tokens(usuario["id"])

        log_audit(
            "auth.password_recovery", status=STATUS_ERROR,
            username=usuario["username"], source_ip=ip_origen,
            details=f"No se pudo enviar el correo: {error}"
        )

    # Misma respuesta en los tres casos
    return generica


@app.post("/api/auth/reset/check")
def auth_reset_check(data: dict):
    """
    Dice si un enlace sigue sirviendo, sin gastarlo.

    Permite que el panel avise antes de que el usuario teclee una
    contrasena nueva para nada.
    """

    _, motivo = recovery.peek_token(data.get("token"))

    if motivo:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": motivo}
        )

    return {"status": "ok"}


@app.post("/api/auth/reset")
def auth_reset(data: dict, request: Request):
    """
    Establece una contrasena nueva con un enlace de recuperacion.

    El enlace se gasta, los demas del mismo usuario se anulan y todas sus
    sesiones se cierran: si se llega aqui es porque la contrasena anterior
    ya no es de fiar.
    """

    ip_origen = request.client.host if request.client else "desconocido"

    bloqueado = seconds_until_unblocked(ip_origen, scope="recovery")

    if bloqueado:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(bloqueado)},
            content={
                "status": "error",
                "message": (
                    "Demasiadas peticiones. "
                    f"Intentalo de nuevo en {bloqueado} segundos."
                )
            }
        )

    nueva = data.get("new_password")

    # La politica se comprueba ANTES de gastar el enlace: si no, una
    # contrasena demasiado corta quemaria el unico enlace que tenia.
    user_id, motivo = recovery.peek_token(data.get("token"))

    if motivo:
        register_failure(ip_origen, scope="recovery")

        log_audit("auth.password_recovery", status=STATUS_ERROR,
                  source_ip=ip_origen, details=f"Enlace rechazado: {motivo}")

        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": motivo}
        )

    problema = validate_new_password(nueva)

    if problema:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": problema}
        )

    user_id, motivo = recovery.consume_token(data.get("token"))

    if motivo:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": motivo}
        )

    usuario = get_user_by_id(user_id)

    if usuario is None:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "El enlace no es valido"}
        )

    set_user_password(usuario["username"], nueva)

    reset_failures(ip_origen, scope="recovery")

    log_audit(
        "auth.password_recovery", status=STATUS_SUCCESS,
        username=usuario["username"], source_ip=ip_origen,
        details="Contrasena restablecida por correo; "
                "se cerraron sus sesiones"
    )

    return {
        "status": "ok",
        "message": "Contrasena actualizada. Ya puedes iniciar sesion."
    }


@app.get("/api/auth/me")
def auth_me(request: Request, authorization: str = Header(default=None)):

    # Mismo orden que el middleware: primero la cabecera (frontend actual),
    # luego la cookie HttpOnly que emite /api/auth/login. La cabecera se
    # mantiene por compatibilidad mientras dura la migración.
    token = (
        token_from_header(authorization)
        or request.cookies.get(AUTH_COOKIE_NAME)
    )

    username = verify_token(token)

    if not username:

        return JSONResponse(
            status_code=401,
            content={
                "status": "error",
                "message": "Sesión no válida"
            }
        )

    return {
        "status": "ok",
        "username": username
    }


@app.post("/api/auth/logout")
def auth_logout(request: Request, authorization: str = Header(default=None)):

    # Revocación real: el token deja de valer en el servidor, no solo en el
    # navegador. Sin esto, una copia de la cookie seguiría abriendo /api/*
    # hasta que el token expirase por sí solo.
    # Solo se revoca ESTE token: otras sesiones del mismo usuario siguen vivas.
    token = (
        token_from_header(authorization)
        or request.cookies.get(AUTH_COOKIE_NAME)
    )

    if token:
        # El usuario se lee del token que se está revocando, antes de
        # invalidarlo; después ya no sería verificable.
        log_audit(
            "auth.logout", status=STATUS_SUCCESS,
            username=verify_token(token),
            source_ip=request.client.host if request.client else None,
            details="Cierre de sesión"
        )

        revoke_session_token(token)

    response = JSONResponse(
        content={
            "status": "ok"
        }
    )

    # Los atributos deben coincidir con los de /api/auth/login: si no, el
    # navegador considera que es otra cookie y no la borra.
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        secure=True,
        samesite="strict"
    )

    return response


@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):

    # El token individual viaja en una cabecera del handshake, NUNCA en la
    # URL: las query strings quedan registradas en logs, proxies e historiales.
    # Se valida ANTES de aceptar la conexión (rechazo = 1008).
    token = websocket.headers.get("x-agent-token")

    # La identidad se DERIVA del token, no se acepta la que diga el cliente:
    # así un Agent no puede reclamar el device_id de otro equipo.
    authenticated_device_id = get_device_id_for_token(token)

    if authenticated_device_id is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    device_id = None

    try:

        message = await websocket.receive_text()

        if message.startswith("Agent connected:"):

            # El saludo se conserva por compatibilidad, pero su contenido es
            # informativo: la identidad válida es la del token.
            device_id = authenticated_device_id

            connected_agents[device_id] = websocket

            print(
                f"Agent connected: {device_id}"
            )

            # Al conectar, se le informa si debe grabar de forma continua
            try:
                enabled = get_continuous_recording(device_id)
                await websocket.send_text(
                    "set_continuous:" + json.dumps({"enabled": bool(enabled)})
                )
            except Exception as error:
                print(f"[continuo] no se pudo enviar el flag inicial: {error}")

            # Y su horario: el Agent lo cumple con su propio reloj, asi
            # que debe tenerlo desde el primer momento, no cuando alguien
            # abra el panel.
            # El horario y la franja suprimida van en el MISMO
            # mensaje.
            #
            # En dos mensajes habia un hueco: el Agent evalua la
            # decision nada mas recibir el horario, asi que entre uno
            # y otro arrancaba la grabacion que el operador habia
            # parado y la detenia un instante despues.
            #
            # Y no vale invertir el orden: un Agent recien arrancado
            # tiene el horario vacio, el que llega siempre "cambia" y
            # set_schedule retiraria la supresion.
            try:
                carga = dict(recording_schedule.get_schedule(device_id))

                carga["_suppressed_slot"] = (
                    devices_mod.get_schedule_suppression(device_id)
                )

                await websocket.send_text("set_schedule:" + json.dumps(carga))

            except Exception as error:
                print(f"[horario] no se pudo enviar el horario inicial: {error}")

            # La politica de retencion local. Hasta recibirla, el Agent no
            # borra nada: ante la duda, conserva.
            await send_retention_policy(device_id)

        while True:

            raw = await websocket.receive()

            if raw["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(raw.get("code", 1000))

            # Frames binarios: solo chunks de descarga (PC remota -> PC tecnica)
            if raw.get("bytes") is not None:
                feed_download_chunk(raw["bytes"])
                continue

            message = raw.get("text")

            if message is None:
                continue

            # No se imprimen los frames para no saturar la consola
            if not message.startswith("screen_info:"):

                print(
                    f"Message from {device_id}: {message}"
                )


            if message.startswith("screen_info:"):

                screen_data = message.split(
                    ":",
                    1
                )[1]

                try:

                    screen_json = json.loads(
                        screen_data
                    )

                except json.JSONDecodeError:

                    # Formato antiguo: solo base64
                    screen_json = {
                        "image": screen_data
                    }

                if screen_json.get("image"):

                    latest_screens[device_id] = screen_json["image"]

                    # Cuanto cuesta de verdad la pantalla en vivo.
                    #
                    # Se mide aqui, donde pasan todos los fotogramas,
                    # y no se deduce de nada: hasta ahora la unica
                    # forma de saber si el remoto iba lento era
                    # mirarlo y opinar.
                    anotar_fotograma(
                        device_id,
                        len(screen_json["image"]),
                        screen_json.get("captura_ms")
                    )

                mouse = screen_json.get("mouse")

                if mouse:

                    latest_cursors[device_id] = {
                        "x": mouse.get("x", 0),
                        "y": mouse.get("y", 0),
                        "width": screen_json.get("width"),
                        "height": screen_json.get("height"),
                        "cursor": mouse.get("cursor", "arrow")
                    }


            elif message.startswith("system_info:"):

                system_info = json.loads(
                    message.split(
                        ":",
                        1
                    )[1]
                )

                print(
                    f"System info from {device_id}:"
                )

                print(system_info)

                update_system_info(
                    device_id,
                    system_info
                )


            elif message.startswith("software_info:"):

                software = json.loads(
                    message.split(
                        ":",
                        1
                    )[1]
                )

                print(
                    f"Software recibido de {device_id}: "
                    f"{len(software)} programas"
                )

                update_installed_software(
                    device_id,
                    software
                )


            elif message.startswith((
                "file_transfer_ready:",
                "file_transfer_complete:",
                "file_transfer_error:"
            )):

                resolve_file_transfer(
                    message
                )


            elif message.startswith((
                "file_download_ready:",
                "file_download_complete:",
                "file_download_error:"
            )):

                resolve_file_download(
                    message
                )


            elif message.startswith("agent_capabilities:"):

                # Lo declara el Agent al conectar. El equipo es el de
                # la CONEXION autenticada, no uno que venga dentro del
                # mensaje: un equipo no puede declarar por otro.
                try:
                    datos = json.loads(message.split(":", 1)[1])

                    if isinstance(datos, dict):
                        agent_capabilities[device_id] = {
                            "version": str(datos.get("version") or ""),
                            "capabilities": [
                                str(c) for c in
                                (datos.get("capabilities") or [])
                            ][:50]
                        }

                except Exception as error:
                    print(f"[capacidades] carga ilegible: {error}")


            elif message.startswith("clipboard_data:"):

                # Lo que hay en el portapapeles del equipo, pedido
                # con un boton. No se registra el contenido.
                try:
                    datos = json.loads(message.split(":", 1)[1])

                except Exception:
                    datos = {"error": "Respuesta ilegible del equipo"}

                resolve_clipboard(device_id, datos)


            elif message.startswith("input_lock_state:"):

                # El equipo confirma si quedo bloqueado. Se anota el
                # hecho, no hay contenido que proteger aqui.
                try:
                    datos = json.loads(message.split(":", 1)[1])

                    print(
                        f"[bloqueo] {device_id}: "
                        f"{'bloqueado' if datos.get('locked') else 'libre'}"
                    )

                except Exception as error:
                    print(f"[bloqueo] respuesta ilegible: {error}")


            elif message.startswith((
                "recording_stream_ready:",
                "recording_stream_complete:",
                "recording_stream_error:"
            )):

                resolve_recording_stream(
                    message
                )


            elif message.startswith(tuple(RESPONSE_PREFIXES)):

                # Respuesta a una consulta de procesos o servicios.
                # El equipo que se pasa es el AUTENTICADO por token, no el
                # que venga dentro del mensaje: así un Agent no puede
                # contestar en nombre de otro.
                prefijo, cuerpo = message.split(":", 1)

                try:
                    resolve_query(
                        device_id,
                        RESPONSE_PREFIXES[prefijo + ":"],
                        json.loads(cuerpo)
                    )

                except json.JSONDecodeError:
                    print(f"[consulta] respuesta ilegible de {device_id}")


            elif message.startswith("recording_catalog:"):

                # Ficha de una grabacion nueva: el servidor se entera de
                # que existe, pero el video se queda en el equipo.
                try:
                    registrar_ficha_local(
                        device_id,
                        json.loads(message.split(":", 1)[1])
                    )

                except json.JSONDecodeError:
                    print(f"[grabaciones] ficha ilegible de {device_id}")


            elif message.startswith("schedule_suppression:"):

                # El Agent informa que franja dejo suprimida una
                # parada manual. Se guarda para devolversela cuando
                # vuelva a conectar: en memoria se perdia con
                # cualquier corte de red y la grabacion se reanudaba
                # sola.
                #
                # El device_id es el de la CONEXION autenticada, no
                # uno que venga en el mensaje: un equipo no puede
                # suprimir el horario de otro.
                try:
                    datos = json.loads(message.split(":", 1)[1])

                    devices_mod.set_schedule_suppression(
                        device_id, datos.get("slot")
                    )

                except Exception as error:
                    print(f"[supresion] no se pudo guardar: {error}")

            elif message.startswith("recording_intervals:"):

                # Tramos de actividad de cada modalidad.
                #
                # Llegan por el WebSocket del Agent, que ya esta
                # autenticado con su token individual: device_id es
                # el de la conexion, NO uno que mande el mensaje. Un
                # equipo no puede informar tramos de otro.
                try:
                    carga = json.loads(message.split(":", 1)[1])

                    nuevos, actualizados, malos = guardar_tramos(
                        device_id, carga.get("intervals") or []
                    )

                    if nuevos or malos:
                        print(
                            f"[tramos] {device_id}: {nuevos} nuevos, "
                            f"{actualizados} al dia, {malos} descartados"
                        )

                except json.JSONDecodeError:
                    print(f"[tramos] carga ilegible de {device_id}")

                except Exception as error:
                    print(f"[tramos] no se pudieron guardar: {error}")


            elif message.startswith("recording_catalog_full:"):

                # Catalogo completo, al reconectar: pone al dia las fichas
                # de lo que se grabo mientras no habia conexion.
                try:
                    datos = json.loads(message.split(":", 1)[1])

                except json.JSONDecodeError:
                    datos = {}

                nuevas = 0

                for ficha in (datos.get("recordings") or [])[:5000]:
                    if registrar_ficha_local(device_id, ficha):
                        nuevas += 1

                if nuevas:
                    print(
                        f"[grabaciones] {nuevas} fichas nuevas de {device_id}"
                    )


            elif message.startswith("jornada_unida:"):

                # El equipo unio los trozos de un dia en un solo
                # video y borro los trozos. Hay que hacer dos cosas:
                # anotar el video del dia y retirar del panel las
                # fichas de los segmentos, que ya no tienen archivo.
                #
                # Llega explicito y no se deduce comparando
                # catalogos: el catalogo solo anade, nunca retira.
                try:
                    carga = json.loads(message.split(":", 1)[1])

                except json.JSONDecodeError:
                    carga = None

                if isinstance(carga, dict):

                    try:
                        anotar_jornada_unida(device_id, carga)

                    except Exception as error:
                        print(f"[jornada] no se pudo anotar: {error}")


            elif message.startswith("recording_status:"):

                try:
                    latest_recording_status[device_id] = json.loads(
                        message.split(":", 1)[1]
                    )
                except json.JSONDecodeError:
                    pass


            elif message.startswith("interactive_session:"):

                # El equipo dice si hay alguien con la sesion
                # iniciada. Con esto el panel puede explicar por que
                # una accion de escritorio no va a funcionar, en vez
                # de aceptarla y no hacer nada.
                try:
                    datos = json.loads(message.split(":", 1)[1])
                except json.JSONDecodeError:
                    datos = {}

                interactive_sessions[device_id] = bool(
                    datos.get("available")
                )

                # Dos hechos distintos. Un Agent anterior solo manda
                # el primero, y entonces no se sabe nada del segundo:
                # se deja en None, que no es lo mismo que False.
                interactive_logins[device_id] = (
                    bool(datos.get("logged_in"))
                    if "logged_in" in datos else None
                )

                print(
                    "[sesion] %s: ayudante %s, sesion %s" % (
                        device_id,
                        "si" if interactive_sessions[device_id] else "NO",
                        {True: "si", False: "no",
                         None: "?"}[interactive_logins[device_id]]
                    )
                )


            elif message.startswith("interactive_unavailable:"):

                # Una orden concreta no se pudo entregar porque no
                # hay escritorio. Hasta ahora este mensaje llegaba y
                # NADIE lo leia: el panel daba la accion por buena.
                #
                # Lo que este aviso dice es que FALTA EL AYUDANTE, y
                # nada mas. No dice si hay alguien con la sesion
                # abierta: eso lo informa 'interactive_session:', que
                # es otro mensaje y mide otra cosa.
                #
                # Durante un tiempo esto se registraba como "no hay
                # sesion iniciada", y mando a una persona a iniciar
                # sesion en dos equipos que ya la tenian abierta. Lo
                # que les faltaba era el ayudante, que es justo lo
                # unico que este mensaje prueba.
                try:
                    datos = json.loads(message.split(":", 1)[1])
                except json.JSONDecodeError:
                    datos = {}

                interactive_sessions[device_id] = False

                orden = str(datos.get("command") or "")[:60]

                log_audit(
                    "remote.sin_sesion",
                    device_id=device_id,
                    status=STATUS_ERROR,
                    details={"orden": orden}
                )

                # Si consta que hay alguien dentro, se dice: es la
                # diferencia entre "manda a alguien a iniciar sesion"
                # y "al equipo le falta el ayudante".
                if interactive_logins.get(device_id):
                    detalle = "falta el ayudante (la sesion SI esta iniciada)"

                elif interactive_logins.get(device_id) is None:
                    detalle = (
                        "falta el ayudante; no consta si hay sesion iniciada"
                    )

                else:
                    detalle = "falta el ayudante y nadie tiene sesion abierta"

                print(f"[sesion] {device_id} no pudo con '{orden}': {detalle}")


            elif message == "pong":

                # Si alguien estaba cronometrando, aqui se cierra el
                # cronometro. Es el numero que dice cuanto se
                # ahorraria con una conexion directa.
                pendiente = pings_pendientes.pop(device_id, None)

                if pendiente is not None and not pendiente["futuro"].done():

                    pendiente["futuro"].set_result(
                        (time.monotonic() - pendiente["desde"]) * 1000
                    )

                print(
                    f"Pong received from {device_id}"
                )

    except WebSocketDisconnect:

        if device_id:

            # Quien esté esperando una consulta recibe el error ya, en vez
            # de agotar el tiempo de espera para saber algo que ya se sabe.
            fail_device_queries(device_id)

            fail_device_clipboard(device_id)

            connected_agents.pop(
                device_id,
                None
            )

            latest_screens.pop(
                device_id,
                None
            )

            latest_cursors.pop(
                device_id,
                None
            )

            fail_device_file_transfers(
                device_id
            )

            fail_device_file_downloads(
                device_id
            )

            fail_device_recording_streams(
                device_id
            )

            agent_capabilities.pop(
                device_id,
                None
            )

            latest_recording_status.pop(
                device_id,
                None
            )

            interactive_sessions.pop(
                device_id,
                None
            )

            interactive_logins.pop(
                device_id,
                None
            )

            print(
                f"Agent disconnected: {device_id}"
            )


@app.post("/api/devices/{device_id}/ping")
async def ping_agent(device_id: str, request: Request):

    # Queda constancia de que la acción se PIDIÓ. No se marca como
    # completada: el Agent recibe la orden por WebSocket y no confirma.
    log_audit(
        "device.ping",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details="Comprobación de conexión"
    )

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "ping"
    )

    return {
        "status": "sent",
        "device_id": device_id
    }


@app.post("/api/devices/{device_id}/system-info")
async def request_system_info(device_id: str, request: Request):

    # Pedir la ficha manda una orden al Agent, asi que exige permiso
    # igual que las demas. No lo exigia: cualquier usuario con sesion
    # en la organizacion podia hacer trabajar a cualquier equipo.
    _, error = require_permission(request, "devices.view")

    if error:
        return error

    # Queda constancia de que la acción se PIDIÓ. No se marca como
    # completada: el Agent recibe la orden por WebSocket y no confirma.
    log_audit(
        "device.system_info",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details="Actualizar información del sistema"
    )

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "get_system_info"
    )

    return {
        "status": "sent",
        "device_id": device_id,
        "command": "get_system_info"
    }


@app.post("/api/devices/{device_id}/software")
async def request_installed_software(device_id: str, request: Request):

    # Queda constancia de que la acción se PIDIÓ. No se marca como
    # completada: el Agent recibe la orden por WebSocket y no confirma.
    log_audit(
        "device.software",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details="Consultar software instalado"
    )

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "get_installed_software"
    )

    return {
        "status": "sent",
        "device_id": device_id,
        "command": "get_installed_software"
    }


@app.get("/api/devices/{device_id}/software")
def installed_software(device_id: str):

    return get_installed_software(
        device_id
    )


@app.post("/api/devices/{device_id}/maintenance")
async def set_maintenance(device_id: str, data: dict, request: Request):
    """
    Enciende o apaga el aviso de mantenimiento del equipo.

    El aviso se muestra HASTA UNA HORA, no indefinidamente. El panel
    renueva ese plazo mientras el tecnico sigue trabajando; si se
    desconecta, se le cae la red o cierra el navegador, nadie renueva
    y el aviso se retira solo al cumplirse el plazo de gracia.

    Es deliberado que el plazo lo lleve el EQUIPO y no el servidor:
    asi el aviso se quita aunque el servidor desaparezca. Un aviso en
    la pantalla de un cliente no puede depender de que alguien
    siga vivo para venir a quitarlo.
    """

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    encendido = bool(data.get("enabled"))

    # Plazo de gracia, acotado por el servidor. Lo pide el panel,
    # pero no se acepta cualquier cosa: ni tan corto que una
    # desconexion de un segundo lo retire, ni tan largo que un
    # descuido lo deje puesto toda la noche.
    segundos = data.get("grace_seconds", MANTENIMIENTO_GRACIA_SEGUNDOS)

    try:
        segundos = int(segundos)

    except (TypeError, ValueError):
        segundos = MANTENIMIENTO_GRACIA_SEGUNDOS

    segundos = max(60, min(segundos, 8 * 3600))

    log_audit(
        "remote.maintenance",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details={"encendido": encendido, "gracia_segundos": segundos}
    )

    websocket = connected_agents.get(device_id)

    if not websocket:
        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "message": "El equipo no esta conectado"}
        )

    # El aviso es una ventana en el escritorio del usuario. Sin nadie
    # con la sesion iniciada no hay donde pintarla, y antes esto se
    # contestaba "ok": el tecnico creia que el cliente estaba viendo
    # el aviso y no habia ningun aviso.
    if not hay_sesion_interactiva(device_id):

        log_audit(
            "remote.maintenance",
            request=request,
            device_id=device_id,
            status=STATUS_ERROR,
            details="Sin sesion iniciada en el equipo"
        )

        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "code": "sin_sesion_iniciada",
                "message": motivo_sin_escritorio(device_id)
            }
        )

    if encendido:
        await websocket.send_text(
            "maintenance_on:" + json.dumps({"seconds": segundos})
        )

    else:
        await websocket.send_text("maintenance_off")

    return {
        "status": "ok",
        "enabled": encendido,
        "grace_seconds": segundos
    }


# Lo que cada equipo tiene pendiente de contestar sobre su
# portapapeles. Una peticion por equipo: pedirlo dos veces a la vez
# no tiene sentido y la segunda se queda con la respuesta.
clipboard_requests = {}

# Mas que esto no se acepta del navegador. El Agent aplica su propio
# tope; este evita que el WebSocket cargue con algo enorme antes
# siquiera de salir del servidor.
CLIPBOARD_MAXIMO = 256 * 1024

CLIPBOARD_TIMEOUT = 10


def resolve_clipboard(device_id, contenido):
    """Entrega al panel lo que contesto el equipo."""

    pendiente = clipboard_requests.pop(device_id, None)

    if pendiente is not None and not pendiente.done():
        pendiente.set_result(contenido)


def fail_device_clipboard(device_id):
    """Al desconectarse un equipo, su peticion no llegara nunca."""

    resolve_clipboard(device_id, {"error": "El equipo se desconecto"})


@app.post("/api/devices/{device_id}/clipboard")
async def device_clipboard(device_id: str, data: dict, request: Request):
    """
    El portapapeles del equipo, en los dos sentidos.

        {"action": "set", "text": "..."}   lo que copio el tecnico
        {"action": "get"}                  lo que hay en el equipo

    Los dos sentidos NO son simetricos a proposito. 'set' lo dispara
    el panel solo, cada vez que cambia el portapapeles de quien
    administra: es lo que hace util pegar en el equipo remoto. 'get'
    es siempre a peticion expresa, con un boton, porque traerse de
    fondo lo que el usuario del equipo copia -una contraseña de su
    gestor, un dato personal- es vigilarle el portapapeles, y eso
    nadie lo ha pedido.

    El CONTENIDO no se registra en la auditoria, ni de ida ni de
    vuelta. Solo que hubo transferencia y de cuantos caracteres.
    """

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    accion = str(data.get("action") or "").strip()

    if accion not in ("set", "get"):
        return JSONResponse(
            status_code=400,
            content={"status": "error",
                     "message": "Accion de portapapeles no valida"}
        )

    websocket = connected_agents.get(device_id)

    if not websocket:
        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "message": "El equipo no esta conectado"}
        )

    if not agent_supports(device_id, "clipboard"):
        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "code": "agent_sin_portapapeles",
                     "message": "Este equipo tiene una version del "
                                "Agent que todavia no comparte el "
                                "portapapeles. Actualiza el Agent "
                                "del equipo."}
        )

    if accion == "set":

        texto = data.get("text")

        if not isinstance(texto, str):
            return JSONResponse(
                status_code=400,
                content={"status": "error",
                         "message": "El portapapeles solo lleva texto"}
            )

        texto = texto[:CLIPBOARD_MAXIMO]

        log_audit(
            "remote.clipboard",
            request=request,
            device_id=device_id,
            status=STATUS_REQUESTED,
            details={"sentido": "al_equipo", "caracteres": len(texto)}
        )

        await websocket.send_text(
            "clipboard_set:" + json.dumps({"text": texto})
        )

        return {"status": "ok", "characters": len(texto)}

    # --- get ---

    log_audit(
        "remote.clipboard",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details={"sentido": "del_equipo"}
    )

    # Una peticion anterior sin contestar se descarta: quien pregunta
    # ahora quiere lo de ahora.
    anterior = clipboard_requests.pop(device_id, None)

    if anterior is not None and not anterior.done():
        anterior.cancel()

    espera = asyncio.get_running_loop().create_future()
    clipboard_requests[device_id] = espera

    await websocket.send_text("clipboard_get")

    try:
        contenido = await asyncio.wait_for(espera, CLIPBOARD_TIMEOUT)

    except asyncio.TimeoutError:
        clipboard_requests.pop(device_id, None)

        return JSONResponse(
            status_code=504,
            content={"status": "error",
                     "message": "El equipo no contesto a tiempo"}
        )

    except asyncio.CancelledError:
        # Otra peticion para el mismo equipo llego despues y se quedo
        # con la respuesta. Sin esto, pulsar el boton dos veces
        # seguidas devolveria un error 500 sin explicacion.
        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "message": "Otra consulta del portapapeles la "
                                "reemplazo"}
        )

    if contenido.get("error"):
        return JSONResponse(
            status_code=502,
            content={"status": "error",
                     "message": contenido["error"]}
        )

    texto = contenido.get("text")

    return {
        "status": "ok",
        "text": texto,
        "empty": bool(contenido.get("empty")) or not texto
    }


@app.post("/api/devices/{device_id}/input-lock")
async def device_input_lock(device_id: str, data: dict, request: Request):
    """
    Bloquea el teclado y el raton FISICOS del equipo.

    Solo lo quita quien lo puso, desde el panel. Quien esta delante
    del equipo no puede: ese es el sentido de la funcion.

    Con una excepcion que no es negociable: el EQUIPO se libera solo
    al cumplirse un plazo que el panel va renovando. Si se cae la
    red, el servidor o el navegador del tecnico, la persona que esta
    delante recupera su teclado sin que nadie tenga que venir. El
    plazo lo lleva el Agent, no el servidor, por lo mismo que el
    aviso de mantenimiento: tiene que sobrevivir a que el servidor
    desaparezca.

    Ctrl+Alt+Supr sigue funcionando. Windows se la reserva y ningun
    programa puede quitarsela.
    """

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    bloquear = bool(data.get("enabled"))

    segundos = data.get("grace_seconds", 60)

    try:
        segundos = int(segundos)

    except (TypeError, ValueError):
        segundos = 60

    # Tope mas corto que el del aviso: un aviso puesto de mas molesta,
    # un teclado bloqueado de mas deja a alguien sin poder trabajar.
    segundos = max(10, min(segundos, 300))

    log_audit(
        "remote.input_lock",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details={"bloqueado": bloquear, "gracia_segundos": segundos}
    )

    websocket = connected_agents.get(device_id)

    if not websocket:
        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "message": "El equipo no esta conectado"}
        )

    if not agent_supports(device_id, "input_lock"):
        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "code": "agent_sin_bloqueo",
                     "message": "Este equipo tiene una version del "
                                "Agent que todavia no sabe bloquear "
                                "el teclado. Actualiza el Agent del "
                                "equipo."}
        )

    if bloquear:
        await websocket.send_text(
            "input_lock:" + json.dumps({"seconds": segundos})
        )

    else:
        await websocket.send_text("input_unlock")

    return {
        "status": "ok",
        "enabled": bloquear,
        "grace_seconds": segundos
    }


@app.post("/api/devices/{device_id}/screen")
async def request_screen(device_id: str, request: Request):

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    # Queda constancia de que la acción se PIDIÓ. No se marca como
    # completada: el Agent recibe la orden por WebSocket y no confirma.
    log_audit(
        "remote.session_start",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details="Inicio de control remoto"
    )

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    # La pantalla la captura el ayudante, que vive en la sesion del
    # usuario. Sin nadie dentro no hay escritorio que capturar.
    #
    # Antes esto se contestaba "sent" igual: el panel abria el
    # rectangulo y se quedaba esperando fotogramas que no iban a
    # llegar. Negro para siempre y sin una palabra, que es la peor
    # forma de fallar: parece una averia del equipo o de la red, y
    # no hay ninguna.
    if not hay_sesion_interactiva(device_id):

        log_audit(
            "remote.session_start",
            request=request,
            device_id=device_id,
            status=STATUS_ERROR,
            details="Sin sesion iniciada en el equipo"
        )

        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "code": "sin_sesion_iniciada",
                "message": motivo_sin_escritorio(device_id)
            }
        )

    await websocket.send_text(
        "start_screen_stream"
    )

    return {
        "status": "sent",
        "device_id": device_id
    }


@app.get("/api/devices/{device_id}/screen")
def get_screen(device_id: str, request: Request):

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    screen = latest_screens.get(
        device_id
    )

    if not screen:

        return {
            "status": "error",
            "message": "No screen available"
        }

    return {
        "status": "ok",
        "image": screen
    }


@app.get("/api/devices/{device_id}/diagnostico-remoto")
async def diagnostico_remoto(device_id: str, request: Request):
    """
    Cuanto cuesta y cuanto tarda la pantalla en vivo de este equipo.

    Existe para poder decidir con numeros en vez de con impresiones.
    La pregunta concreta que responde es si merece la pena la
    conexion directa, que son unos 90 MB en cada equipo: si el viaje
    a la nube y vuelta cuesta poco, no hace falta; si cuesta mucho,
    es lo primero que hay que hacer.

    'ida_y_vuelta_ms' es el numero que decide. Se mide de verdad,
    mandando un ping por el WebSocket y cronometrando el pong, no se
    estima.
    """

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    websocket = connected_agents.get(device_id)

    if not websocket:
        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "message": "El equipo no esta conectado"}
        )

    loop = asyncio.get_running_loop()
    futuro = loop.create_future()

    anterior = pings_pendientes.get(device_id)

    if anterior is not None and not anterior["futuro"].done():
        anterior["futuro"].cancel()

    pings_pendientes[device_id] = {
        "futuro": futuro,
        "desde": time.monotonic()
    }

    ida_y_vuelta = None

    try:
        await websocket.send_text("ping")
        ida_y_vuelta = round(
            await asyncio.wait_for(futuro, PLAZO_DEL_PING), 1
        )

    except asyncio.TimeoutError:
        # No se inventa un numero: no contestar a tiempo ES el dato.
        pings_pendientes.pop(device_id, None)

    except asyncio.CancelledError:
        pass

    except Exception:
        pings_pendientes.pop(device_id, None)

    medida = medida_de_pantalla(device_id) or {}

    if ida_y_vuelta is not None:

        medida["ida_y_vuelta_ms"] = ida_y_vuelta

        if device_id in mediciones_de_pantalla:
            mediciones_de_pantalla[device_id]["ida_y_vuelta_ms"] = ida_y_vuelta

    return {
        "status": "ok",
        "device_id": device_id,
        **medida
    }


@app.get("/api/devices/{device_id}/cursor")
def get_cursor(device_id: str):

    cursor = latest_cursors.get(
        device_id
    )

    if not cursor:

        return {
            "status": "error",
            "message": "No cursor available"
        }

    return {
        "status": "ok",
        **cursor
    }


async def screen_generator(device_id):

    last_screen = None

    while True:

        screen = latest_screens.get(
            device_id
        )

        if screen and screen != last_screen:

            image = base64.b64decode(
                screen
            )

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"\r\n"
                + image
                + b"\r\n"
            )

            last_screen = screen

        await asyncio.sleep(
            0.05
        )


@app.get("/api/devices/{device_id}/screen-stream")
async def screen_stream(device_id: str, request: Request):
    """
    La transmision de la pantalla.

    Exige el mismo permiso que iniciarla. Antes no exigia ninguno: el
    middleware comprueba la sesion y la organizacion, pero dentro de
    una empresa cualquier usuario podia ver la pantalla de cualquier
    equipo con solo conocer su identificador.

    Ver no es menos que actuar: en esta pantalla aparece el correo,
    las contraseñas al teclearlas y todo lo que la persona tenga
    abierto.
    """

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    if device_id not in connected_agents:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    return StreamingResponse(
        screen_generator(device_id),
        media_type=(
            "multipart/x-mixed-replace; "
            "boundary=frame"
        )
    )


@app.post("/api/devices/{device_id}/screen/stop")
async def stop_screen(device_id: str, request: Request):

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    # Queda constancia de que la acción se PIDIÓ. No se marca como
    # completada: el Agent recibe la orden por WebSocket y no confirma.
    log_audit(
        "remote.session_stop",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details="Fin de control remoto"
    )

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "stop_screen_stream"
    )

    return {
        "status": "stopped",
        "device_id": device_id
    }


@app.post("/api/devices/{device_id}/mouse/move")
async def mouse_move(device_id: str, data: dict,
                 request: Request):

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"mouse_move:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


@app.post("/api/devices/{device_id}/mouse/click")
async def mouse_click(device_id: str, data: dict,
                 request: Request):

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"mouse_click:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


@app.post("/api/devices/{device_id}/mouse/down")
async def mouse_down(device_id: str, data: dict,
                 request: Request):

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"mouse_down:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


@app.post("/api/devices/{device_id}/mouse/up")
async def mouse_up(device_id: str, data: dict,
                 request: Request):

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"mouse_up:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


# ==============================
# TRANSFERENCIA DE ARCHIVOS (PC tecnica -> Agent)
# ==============================

MAX_FILE_SIZE = 200 * 1024 * 1024

# Espera maxima al archivar una grabacion. Es generosa a proposito: el
# Agent tiene que leer el archivo y subirlo entero, y un segmento de
# varios MB por una conexion lenta tarda bastante mas que una consulta.
STORE_TIMEOUT_SECONDS = 300

# Menor que el limite de 1 MB por mensaje de la libreria websockets del Agent
FILE_CHUNK_SIZE = 256 * 1024

file_transfers = {}
file_transfer_locks = {}


class FileTransferError(Exception):

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def file_transfer_error(status_code, message, codigo=None):
    """
    Error de transferencia, con el motivo en texto.

    'codigo' es opcional y sirve para que el panel distinga un caso
    concreto sin tener que leer el mensaje: el texto esta escrito
    para una persona y puede cambiar, el codigo no.
    """

    contenido = {
        "status": "error",
        "message": message
    }

    if codigo:
        contenido["code"] = codigo

    return JSONResponse(
        status_code=status_code,
        content=contenido
    )


def resolve_file_transfer(message):

    command, _, payload = message.partition(":")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    if not isinstance(data, dict):
        return

    transfer = file_transfers.get(
        data.get("transfer_id")
    )


    if not transfer:
        return

    if command == "file_transfer_ready":

        if not transfer["ready"].done():
            transfer["ready"].set_result({})

    elif command == "file_transfer_complete":

        if not transfer["done"].done():
            transfer["done"].set_result(data)

    elif command == "file_transfer_error":

        error = {
            "error": data.get("message") or "Error en el Agent"
        }

        for future in (transfer["ready"], transfer["done"]):
            if not future.done():
                future.set_result(error)


def fail_device_file_transfers(device_id):

    error = {
        "error": "El Agent se desconectó"
    }

    for transfer in file_transfers.values():

        if transfer["device_id"] != device_id:
            continue

        for future in (transfer["ready"], transfer["done"]):
            if not future.done():
                future.set_result(error)


@app.post("/api/devices/{device_id}/files/upload")
async def upload_file(
    device_id: str,
    request: Request,
    filename: str,
    size: int
):

    # Se auditan los metadatos de la transferencia (nombre y tamaño).
    # El contenido del archivo NUNCA se registra.
    def auditar(estado, detalle=None):
        log_audit(
            "file.upload", request=request, device_id=device_id,
            status=estado,
            details={"filename": filename, "size": size, **(detalle or {})}
        )

    auditar(STATUS_REQUESTED)

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:
        auditar(STATUS_ERROR, {"error": "Agent no conectado"})
        return file_transfer_error(404, "Agent is not connected")

    if not filename.strip() or size < 0:
        auditar(STATUS_ERROR, {"error": "Nombre o tamaño inválido"})
        return file_transfer_error(400, "Nombre o tamaño de archivo inválido")

    if size > MAX_FILE_SIZE:
        auditar(STATUS_ERROR, {"error": "Supera el tamaño máximo"})
        return file_transfer_error(
            413,
            f"El archivo supera el límite de {MAX_FILE_SIZE // (1024 * 1024)} MB"
        )

    lock = file_transfer_locks.setdefault(
        device_id,
        asyncio.Lock()
    )

    if lock.locked():
        auditar(STATUS_ERROR, {"error": "Transferencia ya en curso"})
        return file_transfer_error(409, "Ya hay una transferencia en curso para este equipo")

    async with lock:

        loop = asyncio.get_running_loop()
        transfer_id = uuid.uuid4().hex
        prefix = transfer_id.encode("ascii")

        transfer = {
            "device_id": device_id,
            "ready": loop.create_future(),
            "done": loop.create_future()
        }

        file_transfers[transfer_id] = transfer

        try:

            # 1-3. Inicio con nombre y tamano; el Agent confirma que esta listo
            await websocket.send_text(
                "file_transfer_start:"
                + json.dumps({
                    "transfer_id": transfer_id,
                    "filename": filename,
                    "size": size
                })
            )


            ready = await asyncio.wait_for(transfer["ready"], 15)


            if ready.get("error"):
                raise FileTransferError(400, ready["error"])

            # 4. Datos binarios en trozos, a medida que llegan del navegador
            received = 0
            buffer = bytearray()


            async for chunk in request.stream():

                received += len(chunk)

                if received > size:
                    raise FileTransferError(400, "El archivo es mayor que el tamaño indicado")

                buffer.extend(chunk)

                while len(buffer) >= FILE_CHUNK_SIZE:
                    await websocket.send_bytes(prefix + bytes(buffer[:FILE_CHUNK_SIZE]))
                    del buffer[:FILE_CHUNK_SIZE]

                if transfer["done"].done():
                    break

            if transfer["done"].done():
                result = transfer["done"].result()
                raise FileTransferError(502, result.get("error") or "El Agent canceló la transferencia")

            if buffer:
                await websocket.send_bytes(prefix + bytes(buffer))


            if received != size:
                raise FileTransferError(400, "La subida se interrumpió antes de terminar")

            # 5. Fin; 6. confirmacion del Agent
            await websocket.send_text(
                "file_transfer_end:"
                + json.dumps({"transfer_id": transfer_id})
            )


            result = await asyncio.wait_for(transfer["done"], 60)

            if result.get("error"):
                raise FileTransferError(502, result["error"])


            auditar(STATUS_SUCCESS)

            return {
                "status": "file_transfer_complete",
                "file": result
            }

        except FileTransferError as error:


            await send_file_transfer_abort(websocket, transfer_id)

            auditar(STATUS_ERROR, {"error": error.message})

            return file_transfer_error(error.status_code, error.message)

        except asyncio.TimeoutError:


            await send_file_transfer_abort(websocket, transfer_id)

            auditar(STATUS_ERROR, {"error": "El Agent no respondió a tiempo"})

            return file_transfer_error(504, "El Agent no respondió a tiempo")

        except Exception as error:


            await send_file_transfer_abort(websocket, transfer_id)

            auditar(STATUS_ERROR, {"error": f"Error en la transferencia: {error}"})

            return file_transfer_error(500, f"Error en la transferencia: {error}")

        finally:

            file_transfers.pop(
                transfer_id,
                None
            )


async def send_file_transfer_abort(websocket, transfer_id):

    try:
        await websocket.send_text(
            "file_transfer_abort:"
            + json.dumps({"transfer_id": transfer_id})
        )
    except Exception:
        pass


# ==============================
# DESCARGA DE ARCHIVOS (PC remota -> PC tecnica)
# ==============================

import os as _os
import urllib.parse as _urlparse

file_downloads = {}


# Transmisiones de grabaciones locales en curso, por transfer_id.
# Mismo esquema que file_downloads: un futuro para los metadatos y una
# cola para los bloques.
recording_streams = {}


def resolve_recording_stream(message):
    """Encamina los avisos del Agent sobre una transmision en curso."""

    command, _, payload = message.partition(":")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    if not isinstance(data, dict):
        return

    transmision = recording_streams.get(data.get("transfer_id"))

    if not transmision:
        return

    if command == "recording_stream_ready":

        if not transmision["meta"].done():
            transmision["meta"].set_result(data)

    elif command == "recording_stream_complete":

        transmision["queue"].put_nowait(None)

    elif command == "recording_stream_error":

        # 'code' y 'size' viajan con el error, no solo el texto. Sin
        # ellos, un Range imposible —que el equipo marca con
        # code='range'— llegaba al endpoint como un error cualquiera
        # y se contestaba 502, que significa "el equipo ha fallado",
        # en vez de 416, que es lo que el reproductor espera y sabe
        # interpretar.
        error = {
            "error": data.get("message") or "Error en el equipo",
            "code": data.get("code"),
            "size": data.get("size")
        }

        if not transmision["meta"].done():
            transmision["meta"].set_result(error)
        else:
            transmision["queue"].put_nowait(error)


def fail_device_recording_streams(device_id):
    """El equipo se desconecto: sus transmisiones no van a continuar."""

    for transmision in recording_streams.values():

        if transmision["device_id"] != device_id:
            continue

        error = {"error": "El equipo se desconecto"}

        if not transmision["meta"].done():
            transmision["meta"].set_result(error)
        else:
            transmision["queue"].put_nowait(error)


def feed_download_chunk(chunk):

    transfer_id = chunk[:32].decode("ascii", "replace")

    download = file_downloads.get(transfer_id)

    if download:
        download["queue"].put_nowait(chunk[32:])
        return

    # Mismo formato de trama —32 caracteres de transfer_id y detras los
    # bytes—, otro registro: los dos canales conviven sin estorbarse
    # porque el identificador es un uuid4 y no se repite.
    transmision = recording_streams.get(transfer_id)

    if transmision:
        transmision["queue"].put_nowait(chunk[32:])


def resolve_file_download(message):

    command, _, payload = message.partition(":")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    if not isinstance(data, dict):
        return

    download = file_downloads.get(
        data.get("transfer_id")
    )


    if not download:
        return

    if command == "file_download_ready":

        if not download["meta"].done():
            download["meta"].set_result(data)

    elif command == "file_download_complete":

        # Sentinela de fin de stream
        download["queue"].put_nowait(None)

    elif command == "file_download_error":

        error = {"error": data.get("message") or "Error en el Agent"}

        if not download["meta"].done():
            download["meta"].set_result(error)
        else:
            download["queue"].put_nowait(error)


def fail_device_file_downloads(device_id):

    for download in file_downloads.values():

        if download["device_id"] != device_id:
            continue

        error = {"error": "El Agent se desconectó"}

        if not download["meta"].done():
            download["meta"].set_result(error)
        else:
            download["queue"].put_nowait(error)


@app.get("/api/devices/{device_id}/files/download")
async def download_file(device_id: str, path: str, request: Request):

    # El usuario y la IP se toman una sola vez del contexto real de la
    # petición: el resultado se conoce dentro del generador, cuando la
    # petición ya puede no estar disponible. Se registra la ruta pedida,
    # nunca el contenido del archivo.
    usuario, ip_origen = get_request_context(request)

    def auditar(estado, detalle=None):
        log_audit(
            "file.download", device_id=device_id, status=estado,
            username=usuario, source_ip=ip_origen,
            details={"path": path, **(detalle or {})}
        )

    # Sacar CUALQUIER archivo de un equipo ajeno pide el mismo
    # permiso que tomar el control de esa maquina.
    #
    # Antes no pedia ninguno, y se sostenia porque solo se podia
    # sacar lo que hubiera en una carpeta concreta. Desde que se
    # puede pedir cualquier ruta, quien mira el inventario no tiene
    # por que poder llevarse los documentos de nadie.
    _, denegado = require_permission(request, "remote.control")

    if denegado:
        auditar(STATUS_ERROR, {"error": "Sin permiso remote.control"})
        return denegado

    auditar(STATUS_REQUESTED)

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:
        auditar(STATUS_ERROR, {"error": "Agent no conectado"})
        return file_transfer_error(404, "Agent is not connected")

    # Se puede traer cualquier archivo del equipo. Lo que decide que
    # eso sea legitimo no es la ruta, sino quien lo pide: hace falta
    # 'remote.actions' -la misma capacidad que mover el raton o
    # escribir en el equipo- y queda en la auditoria con el usuario,
    # su IP y la ruta exacta.
    #
    # La restriccion anterior a C:\RemoteAdmin\Downloads convertia la
    # funcion en un buzon: solo servia para recoger lo que alguien
    # hubiera dejado ahi a mano, que es justo lo que no se puede
    # hacer cuando el equipo esta a cien kilometros.

    loop = asyncio.get_running_loop()
    transfer_id = uuid.uuid4().hex

    download = {
        "device_id": device_id,
        "meta": loop.create_future(),
        "queue": asyncio.Queue()
    }

    file_downloads[transfer_id] = download

    await websocket.send_text(
        "file_download_start:"
        + json.dumps({"transfer_id": transfer_id, "path": path})
    )


    try:
        meta = await asyncio.wait_for(download["meta"], 20)
    except asyncio.TimeoutError:
        file_downloads.pop(transfer_id, None)
        auditar(STATUS_ERROR, {"error": "El Agent no respondió a tiempo"})
        return file_transfer_error(504, "El Agent no respondió a tiempo")

    if meta.get("error"):
        file_downloads.pop(transfer_id, None)
        auditar(STATUS_ERROR, {"error": meta["error"]})
        return file_transfer_error(502, meta["error"])

    filename = meta.get("filename") or "archivo"
    size = meta.get("size", 0)


    async def stream_file():

        received = 0

        try:

            while True:

                try:
                    item = await asyncio.wait_for(download["queue"].get(), 60)
                except asyncio.TimeoutError:
                    break

                if item is None:
                    break

                if isinstance(item, dict):
                    break

                received += len(item)
                yield item

        finally:
            file_downloads.pop(transfer_id, None)

            # Se anota lo que realmente salió del equipo. Si el flujo se
            # cortó antes de tiempo, los bytes enviados lo delatan.
            if size > 0 and received >= size:
                auditar(STATUS_SUCCESS, {"bytes": received})
            else:
                auditar(
                    STATUS_ERROR,
                    {"error": "Descarga incompleta", "bytes": received}
                )

    encoded = _urlparse.quote(filename)

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        "Content-Length": str(size)
    }

    return StreamingResponse(
        stream_file(),
        media_type="application/octet-stream",
        headers=headers
    )


@app.post("/api/devices/{device_id}/keyboard")
async def keyboard_input(device_id: str, data: dict,
                 request: Request):

    _, error = require_permission(request, "remote.control")

    if error:
        return error

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"keyboard:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


# ==============================
# GRABACION DE PANTALLA (start/stop remoto; los MP4 quedan en el Agent)
# ==============================

@app.post("/api/devices/{device_id}/recording/start")
async def recording_start(device_id: str, request: Request):

    # Queda constancia de que la acción se PIDIÓ. No se marca como
    # completada: el Agent recibe la orden por WebSocket y no confirma.
    log_audit(
        "recording.start",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details="Iniciar grabación"
    )

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:
        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "start_recording"
    )

    return {
        "status": "sent"
    }


@app.post("/api/devices/{device_id}/recording/stop")
async def recording_stop(device_id: str, request: Request):

    # Queda constancia de que la acción se PIDIÓ. No se marca como
    # completada: el Agent recibe la orden por WebSocket y no confirma.
    log_audit(
        "recording.stop",
        request=request,
        device_id=device_id,
        status=STATUS_REQUESTED,
        details="Detener grabación"
    )

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:
        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "stop_recording"
    )

    return {
        "status": "sent"
    }


@app.post("/api/devices/{device_id}/recordings/upload")
async def upload_recording(
    device_id: str,
    request: Request,
    filename: str = "",
    started_at: str = "",
    ended_at: str = "",
    duration_sec: int = 0,
    x_agent_token: str = Header(default=None)
):

    # Token individual del Agent (no sesión de panel).
    authenticated_device_id = get_device_id_for_token(x_agent_token)

    if authenticated_device_id is None:
        return _agent_unauthorized()

    # La grabación solo puede atribuirse al dispositivo dueño del token: así
    # un Agent no puede subir material en nombre de otro equipo.
    if authenticated_device_id != device_id:
        return _agent_unauthorized()

    # Nombre seguro (solo el nombre de archivo, sin rutas)
    safe_name = _os.path.basename(filename or "").strip()

    if not safe_name.lower().endswith(".mp4"):
        return file_transfer_error(400, "Nombre de archivo inválido")

    # Carpeta destino: server_recordings/{device_id}/AAAA/MM/DD/
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(started_at) if started_at else datetime.now(timezone.utc)
    except ValueError:
        when = datetime.now(timezone.utc)

    rel_dir = _os.path.join(
        device_id,
        when.strftime("%Y"),
        when.strftime("%m"),
        when.strftime("%d")
    )

    dest_dir = _os.path.join(str(get_recordings_dir()), rel_dir)
    _os.makedirs(dest_dir, exist_ok=True)

    dest_path = _os.path.join(dest_dir, safe_name)
    rel_path = _os.path.join(rel_dir, safe_name).replace("\\", "/")

    # Idempotencia: si el Agent reintenta subir el mismo segmento, no se
    # duplica. Con el modelo nuevo hay un matiz importante: puede existir
    # ya una FICHA de esa grabacion (solo local, sin archivo). En ese caso
    # no es un duplicado: es justo la que hay que completar.
    existente = get_recording_by_path(device_id, rel_path)

    if existente is not None \
            and storage_state_of(existente) == STORAGE_STORED:

        return {
            "status": "stored",
            "id": existente["id"],
            "duplicate": True,
            "path": rel_path
        }

    # Fila a completar al terminar (ficha local, pendiente o con error)
    ficha_previa = existente["id"] if existente else None

    # Escritura atómica: se recibe en un .part y solo al terminar se publica
    # con el nombre definitivo. Un corte a mitad deja un .part reconocible,
    # nunca un .mp4 truncado que parezca una grabación buena.
    #
    # El temporal lleva un sufijo único: si dos subidas del mismo segmento
    # coinciden, cada una escribe en su propio archivo y no se pisan. La
    # extensión .part se conserva para que la limpieza las reconozca.
    part_path = f"{dest_path}.{uuid.uuid4().hex}{PART_SUFFIX}"

    received = 0

    def _descartar_part():
        try:
            if _os.path.exists(part_path):
                _os.remove(part_path)
        except OSError as error:
            print(f"[grabaciones] No se pudo borrar {part_path}: {error}")

    try:

        with open(part_path, "wb") as handle:

            async for chunk in request.stream():

                received += len(chunk)

                if received > MAX_FILE_SIZE:
                    handle.close()
                    _descartar_part()
                    return file_transfer_error(
                        413,
                        "La grabación supera el límite de tamaño"
                    )

                handle.write(chunk)

            # Los bytes llegan al disco ANTES de publicar el archivo: sin
            # esto, un corte de luz podría dejar un .mp4 del tamaño correcto
            # con contenido sin escribir.
            handle.flush()
            _os.fsync(handle.fileno())

    except Exception as error:

        _descartar_part()

        return file_transfer_error(
            500,
            f"Error guardando la grabación: {error}"
        )

    # El archivo llegó entero (F3). Antes de publicarlo con su nombre
    # definitivo: ¿es un vídeo utilizable? (F4)
    #
    # Se valida el .part, NO el archivo ya publicado. Dos motivos:
    #
    #   - una grabación inválida nunca llega a existir con el nombre
    #     definitivo, ni siquiera un instante;
    #   - en Windows, ffmpeg mantiene abierto el archivo que analiza, y eso
    #     impedía que una subida simultánea del mismo segmento pudiera
    #     reemplazarlo (fallaba con "Acceso denegado").
    #
    # En un hilo aparte: validate_recording lanza ffmpeg y espera a que
    # termine. Hacerlo aquí mismo bloquearía el bucle de eventos y dejaría al
    # servidor sin atender ninguna otra petición mientras tanto.
    validacion = await asyncio.to_thread(
        validate_recording,
        part_path,
        duration_sec
    )

    if not validacion.valid:

        # No se borra: la grabación defectuosa es la evidencia de que algo
        # va mal en el equipo de origen. El .part se aparta a cuarentena
        # directamente, sin pasar por el nombre definitivo.
        ruta_cuarentena = quarantine_recording(
            part_path,
            device_id,
            safe_name
        )

        if ruta_cuarentena is None:
            _descartar_part()
            return file_transfer_error(
                500,
                "No se pudo apartar la grabación inválida"
            )

        try:
            invalid_id = add_invalid_recording(
                device_id=device_id,
                path=ruta_cuarentena,
                started_at=started_at or when.isoformat(),
                ended_at=ended_at,
                duration_sec=duration_sec,
                size_bytes=received,
                reason=validacion.reason
            )

        except RecordingAlreadyExists as existente:
            invalid_id = existente.recording_id

        print(
            f"[grabaciones] Grabación inválida de {device_id} "
            f"({safe_name}): {validacion.reason}. "
            f"En cuarentena como {ruta_cuarentena}"
        )

        # 200, NO un error: para el Agent la subida está resuelta y puede
        # borrar su copia local. Un 4xx haría que la conservara para siempre
        # reintentando o esperando intervención.
        return {
            "status": "invalid",
            "id": invalid_id,
            "reason": validacion.reason,
            "detail": validacion.detail,
            "path": ruta_cuarentena
        }

    # Validada: ahora sí se publica de forma atómica
    try:
        _os.replace(part_path, dest_path)

    except OSError as error:

        _descartar_part()

        return file_transfer_error(
            500,
            f"No se pudo publicar la grabación: {error}"
        )

    # Comprobación antes de registrar: el archivo final existe y pesa lo que
    # se recibió. Si no, no se crea la fila.
    try:
        tamano_final = _os.path.getsize(dest_path)

    except OSError as error:

        return file_transfer_error(
            500,
            f"La grabación publicada no es accesible: {error}"
        )

    if tamano_final != received:

        try:
            _os.remove(dest_path)
        except OSError:
            pass

        return file_transfer_error(
            500,
            "El archivo publicado no coincide con lo recibido"
        )

    # Duración que se guarda. ffmpeg acaba de medir el archivo que está en
    # el servidor, así que esa medida es mejor dato que lo que declaró un
    # equipo remoto —y es la única que existe cuando el Agent manda 0, que es
    # lo que hace al construir la ficha escaneando su carpeta.
    #
    # None significa "no se sabe"; en ese caso no se escribe nada y la fila
    # conserva el valor que ya tuviera.
    duracion_guardada = duration_for_storage(validacion, duration_sec)

    try:

        if ficha_previa is not None:

            # Ya habia ficha: se completa en lugar de crear otra fila. Asi
            # el panel conserva la misma grabacion, con su historial y su
            # marca de conservar, y no aparece duplicada.
            #
            # La ficha venia del catalogo del Agent, con duracion 0 por no
            # poder saberla. Pasar la medida aqui es lo que evita que el
            # COALESCE conserve ese cero.
            mark_stored(
                ficha_previa,
                size_bytes=received,
                duration_sec=duracion_guardada
            )

            recording_id = ficha_previa

        else:

            recording_id = add_recording(
                device_id=device_id,
                path=rel_path,
                started_at=started_at or when.isoformat(),
                ended_at=ended_at,
                duration_sec=(
                    duracion_guardada
                    if duracion_guardada is not None
                    else duration_sec
                ),
                size_bytes=received
            )

            mark_stored(recording_id)

    except RecordingAlreadyExists as existente:

        # Otra subida del mismo segmento ganó la carrera. El archivo en disco
        # es válido y ya está registrado por ella: no se borra ni se duplica.
        return {
            "status": "stored",
            "id": existente.recording_id,
            "duplicate": True,
            "path": rel_path
        }

    except Exception as error:

        # Sin fila no debe quedar archivo: sería un huérfano invisible para
        # el listado y para la retención.
        try:
            _os.remove(dest_path)
        except OSError:
            pass

        return file_transfer_error(
            500,
            f"No se pudo registrar la grabación: {error}"
        )

    return {
        "status": "stored",
        "id": recording_id,
        "size_bytes": received,
        "path": rel_path
    }


def _respuesta_paginada(device_id, start, end, limit, offset,
                        device_ids):
    """
    Una pagina del listado, con lo que el panel necesita para paginar.

    El total y las filas salen del MISMO filtro (ver
    _filtro_de_listado), asi que 'has_more' nunca contradice a lo que
    se acaba de pintar.
    """

    filas = list_recordings_page(
        device_id=device_id, start=start, end=end,
        limit=limit, offset=offset, device_ids=device_ids
    )

    total = count_recordings(
        device_id=device_id, start=start, end=end,
        device_ids=device_ids
    )

    # offset puede llegar negativo o con basura; list_recordings_page
    # ya lo normaliza, y aqui se repite el mismo criterio para que el
    # 'offset' que se devuelve sea el que de verdad se uso.
    try:
        desde = max(0, int(offset))
    except (TypeError, ValueError):
        desde = 0

    return {
        "status": "ok",
        "recordings": filas,
        "total": total,
        "offset": desde,
        "limit": len(filas),
        "has_more": (desde + len(filas)) < total
    }


@app.get("/api/recordings/days")
def recordings_days(request: Request, device_id: str = None,
                    start: str = None, end: str = None):
    """
    El historial por JORNADAS: una fila por equipo y dia.

    El equipo graba en segmentos de quince minutos, y eso no cambia:
    un unico archivo de nueve horas no se puede reproducir mientras
    se escribe, ni subir a trozos, y un corte de luz a las cuatro se
    llevaria el dia entero.

    Pero asi es como se GUARDA, no como se busca. Quien abre el
    panel quiere "el lunes de Recepcion", no treinta y seis archivos
    que abrir uno a uno hasta dar con el momento que busca.

    No agrupa nada en disco: los segmentos siguen donde estaban, con
    su fila y su archivo. Lo unico que cambia es como se presentan.
    """

    usuario, error = require_permission(request, "recordings.view")

    if error:
        return error

    dias = jornadas(device_id, start, end)

    # Cada empresa ve lo suyo. Se filtra por EQUIPO y no por jornada
    # para no tener que repetir la regla: un equipo ajeno
    # sencillamente no aparece, igual que en el listado normal.
    if not is_platform_owner(usuario):

        propia = organization_of(usuario)

        equipos = {
            equipo["device_id"] for equipo in get_devices()
            if equipo.get("organization_id") == propia
        }

        dias = [dia for dia in dias if dia["device_id"] in equipos]

    return {
        "status": "ok",
        "days": dias
    }


@app.get("/api/recordings/clips")
def recordings_clips(request: Request, device_id: str = None,
                     start: str = None, end: str = None):
    """
    Los trozos grabados a mano, cada uno con sus segmentos en orden.

    El boton de grabar a mano sirve para capturar algo concreto -un
    error que enseñar, una gestion que documentar-, y esos trozos se
    perdian entre los treinta y seis segmentos del dia. Aqui salen
    por separado.

    No son archivos aparte: el equipo corta el segmento al empezar y
    al terminar, asi que el trozo son segmentos enteros que se
    reproducen seguidos. Sin duplicar disco y sin que la jornada
    cuente dos veces lo mismo.
    """

    usuario, error = require_permission(request, "recordings.view")

    if error:
        return error

    trozos = recordings_mod.trozos_manuales(device_id, start, end)

    if not is_platform_owner(usuario):

        propia = organization_of(usuario)

        equipos = {
            equipo["device_id"] for equipo in get_devices()
            if equipo.get("organization_id") == propia
        }

        trozos = [t for t in trozos if t["device_id"] in equipos]

    return {"status": "ok", "clips": trozos}


@app.get("/api/recordings")
def recordings_list(request: Request, device_id: str = None,
                    start: str = None, end: str = None,
                    limit: int = None, offset: int = 0):
    """
    El listado de grabaciones, por paginas.

    'limit' es OPCIONAL a proposito. Sin el, la respuesta es la de
    siempre —el historial completo— porque hay llamadas que lo
    necesitan entero: la ficha de un equipo y el resumen del panel. El
    listado de Grabaciones si lo manda, y asi solo esa pantalla pagina.

    Cuando hay 'limit' la respuesta trae ademas 'total' y 'has_more',
    que es lo que el panel usa para decidir si ensena el boton "Ver
    mas" y para escribir "10 de 173".
    """

    usuario, error = require_permission(request, "recordings.view")

    if error:
        return error

    # El rango se valida ANTES de consultar. Un intervalo imposible
    # —fin anterior al inicio— devolvia una lista vacia que no se
    # distingue de "no hay grabaciones ese dia", y quien lo veia
    # concluia que sus grabaciones se habian perdido.
    if start and end and str(start).strip() and str(end).strip():

        if str(end).strip() < str(start).strip():
            return {
                "status": "error",
                "error": "invalid_range",
                "message": "La hora final es anterior a la inicial. "
                           "Para consultar un intervalo que cruza la "
                           "medianoche, elige la fecha del dia "
                           "siguiente."
            }

    paginado = limit is not None

    if not is_platform_owner(usuario):

        propia = organization_of(usuario)

        equipos = {
            equipo["device_id"] for equipo in get_devices()
            if equipo.get("organization_id") == propia
        }

        if paginado:

            # Filtrar por un equipo ajeno no da error: devuelve vacio,
            # como si no hubiera nada.
            permitidos = (
                [] if (device_id is not None and device_id not in equipos)
                else sorted(equipos)
            )

            return _respuesta_paginada(
                device_id, start, end, limit, offset, permitidos
            )

        # Filtrar por un equipo ajeno no da error: devuelve vacio, como
        # si no hubiera nada. No se confirma que ese equipo exista.
        #
        # La forma de la respuesta es la MISMA que la de abajo, y no es
        # un detalle: el panel lee data.recordings. Devolver aqui una
        # lista desnuda dejaba ese campo en undefined, asi que quien
        # entraba como Owner de una empresa veia la seccion vacia
        # aunque hubiera grabaciones, mientras el operador de la
        # plataforma —que pasa por la rama de abajo— las veia todas.
        if device_id is not None and device_id not in equipos:
            return {"status": "ok", "recordings": []}

        return {
            "status": "ok",
            "recordings": [
                grabacion
                for grabacion in query_recordings(device_id, start, end)
                if grabacion["device_id"] in equipos
            ]
        }


    # Protegido por sesión (el middleware exige token en /api/* salvo rutas abiertas).
    # device_id se usa solo como filtro parametrizado (a prueba de inyección).
    if device_id is not None and not device_id.strip():
        device_id = None

    if paginado:
        return _respuesta_paginada(
            device_id, start, end, limit, offset, None
        )

    if (start and start.strip()) or (end and end.strip()):
        # Historial temporal: filtra por rango [start, end]
        recordings = query_recordings(device_id, start, end)
    else:
        recordings = list_recordings(device_id)

    return {
        "status": "ok",
        "recordings": recordings
    }


@app.post("/api/recordings/{recording_id}/keep")
async def recording_keep(recording_id: int, data: dict, request: Request):

    _, error = require_permission(request, "recordings.manage")

    if error:
        return error


    # Protegido por sesión. Marca/desmarca "Conservar".
    keep = 1 if data.get("keep") else 0

    if not set_keep(recording_id, keep):
        return file_transfer_error(404, "Grabación no encontrada")

    # El Agent tiene su propia copia: hay que decirle que esta marcada,
    # o su retencion la borraria igual al cumplir los dias. Al quitar la
    # marca, la lista nueva ya no la incluye y vuelve a estar sujeta a la
    # retencion, que es justo lo que se espera.
    grabacion = get_recording(recording_id)

    if grabacion:
        await send_retention_policy(grabacion["device_id"])

    return {
        "status": "ok",
        "id": recording_id,
        "keep": keep
    }


@app.get("/api/recordings/{recording_id}/video")
def recording_video(recording_id: int, request: Request):

    _, error = require_permission(request, "recordings.view")

    if error:
        return error


    # Protegido por sesión (el middleware exige token en /api/*).
    recording = get_recording(recording_id)

    if not recording:
        return file_transfer_error(404, "Grabación no encontrada")

    # Solo en el equipo: el servidor no tiene el archivo. Se dice con
    # claridad en vez de devolver un 404 que parece un error del sistema.
    if storage_state_of(recording) != STORAGE_STORED:
        return file_transfer_error(
            409,
            "Esta grabacion solo esta en el equipo. Guardala en el "
            "servidor para poder verla o descargarla desde aqui."
        )

    rel_path = recording.get("path") or ""

    base = _os.path.realpath(str(get_recordings_dir()))
    target = _os.path.realpath(_os.path.join(base, rel_path))

    # Defensa contra path traversal: el archivo debe quedar dentro de la carpeta
    if target != base and not target.startswith(base + _os.sep):
        return file_transfer_error(403, "Ruta de grabación no permitida")

    if not _os.path.isfile(target):
        return file_transfer_error(404, "El archivo de la grabación no existe")

    return FileResponse(
        target,
        media_type="video/mp4",
        filename=_os.path.basename(target)
    )


# Cuanto se espera a que el equipo conteste que tiene el archivo. Es
# una lectura de disco local: si en 20 s no ha contestado, o el equipo
# esta saturado o la conexion no da mas de si.
RECORDING_STREAM_META_TIMEOUT = 20

# Cuanto se espera entre dos bloques antes de dar la transmision por
# perdida. Con bloques de 256 KB, 60 s de silencio no es lentitud: es
# que ya no viene nada.
RECORDING_STREAM_CHUNK_TIMEOUT = 60


def _rango_pedido(cabecera):
    """
    Interpreta una cabecera Range de las que manda un reproductor.

    Devuelve lo que PIDE el cliente, sin saber todavia el tamano del
    archivo: eso lo sabe el equipo, y preguntarselo antes costaba un
    viaje de ida y vuelta de mas por cada salto del reproductor. Un
    video se reproduce a base de decenas de peticiones Range, asi que
    ese viaje extra se pagaba decenas de veces.

    Devuelve un diccionario con:
        offset   desde donde (None si es un rango por el final)
        length   cuantos bytes (None = hasta el final)
        suffix   los ultimos N bytes, para 'bytes=-500'

    O None si no hay cabecera o no se entiende, y entonces se sirve
    el archivo entero.
    """

    if not cabecera:
        return None

    texto = str(cabecera).strip().lower()

    if not texto.startswith("bytes="):
        return None

    # Solo el primer intervalo. Un navegador no pide varios para un
    # video, y servir multipart/byteranges no aportaria nada aqui.
    trozo = texto[len("bytes="):].split(",")[0].strip()

    if "-" not in trozo:
        return None

    crudo_inicio, _, crudo_fin = trozo.partition("-")

    try:

        if not crudo_inicio:

            # 'bytes=-500': los ultimos 500 bytes. Cuantos son en
            # realidad depende del tamano, que solo conoce el equipo.
            sufijo = int(crudo_fin)

            if sufijo <= 0:
                return None

            return {"offset": None, "length": None, "suffix": sufijo}

        inicio = int(crudo_inicio)

        if inicio < 0:
            return None

        if not crudo_fin:
            return {"offset": inicio, "length": None, "suffix": None}

        fin = int(crudo_fin)

    except ValueError:
        return None

    if fin < inicio:
        return None

    return {
        "offset": inicio,
        "length": fin - inicio + 1,
        "suffix": None
    }


@app.get("/api/recordings/{recording_id}/local-video")
async def recording_local_video(recording_id: int, request: Request):
    """
    Reproduce una grabacion que sigue EN EL EQUIPO, sin subirla.

    El video no pasa por el disco del servidor: llega del Agent por el
    WebSocket que ya esta abierto y sale hacia el navegador segun
    llega. No se guarda ninguna copia intermedia.

    Lo que el servidor manda al equipo es el NOMBRE del archivo que
    tiene en su propia base para esa fila, nunca una ruta que venga
    del navegador. El navegador solo manda un identificador numerico.
    """

    usuario, error = require_permission(request, "recordings.view")

    if error:
        return error

    grabacion = get_recording(recording_id)

    if not grabacion:
        return file_transfer_error(404, "Grabación no encontrada")

    device_id = grabacion["device_id"]

    # El permiso general no basta: hay que poder ver ESTE equipo. Sin
    # esta comprobacion, cualquiera con 'recordings.view' en su empresa
    # podria reproducir las grabaciones de otra probando numeros.
    if not is_platform_owner(usuario):

        if get_device_organization(device_id) != organization_of(usuario):
            return file_transfer_error(404, "Grabación no encontrada")

    websocket = connected_agents.get(device_id)

    if not websocket:
        return file_transfer_error(
            409,
            "El equipo esta desconectado. No se puede abrir una "
            "grabacion que solo esta en su disco hasta que vuelva a "
            "conectarse."
        )

    # Un Agent anterior no entiende 'recording_stream_start' y se
    # limita a ignorarlo. Sin esta comprobacion habria que esperar a
    # que venciera el tiempo de espera para decir "no respondio a
    # tiempo", que suena a averia y no lo es: el equipo esta
    # perfectamente, lo que le falta es la version del Agent que sabe
    # transmitir. Se dice en el acto y con sus palabras.
    if not agent_supports(device_id, "recording_stream"):
        return file_transfer_error(
            409,
            "Este equipo tiene una version del Agent que todavia no "
            "sabe transmitir grabaciones. Guardala en el servidor "
            "para verla, o actualiza el Agent del equipo.",
            codigo="agent_sin_transmision"
        )

    # El nombre sale de la ruta que guardo el propio servidor al
    # anotar la ficha. basename() descarta las carpetas: lo que viaja
    # al equipo es 'rec_1793133350.mp4' y nada mas.
    filename = _os.path.basename(str(grabacion.get("path") or ""))

    if not filename.lower().endswith(".mp4"):
        return file_transfer_error(404, "Grabación no encontrada")

    pedido = _rango_pedido(request.headers.get("range"))

    loop = asyncio.get_running_loop()
    transfer_id = uuid.uuid4().hex

    transmision = {
        "device_id": device_id,
        "meta": loop.create_future(),
        "queue": asyncio.Queue()
    }

    recording_streams[transfer_id] = transmision

    async def cancelar(identificador):
        """Le dice al equipo que deje de enviar."""

        recording_streams.pop(identificador, None)

        try:
            await websocket.send_text(
                "recording_stream_abort:"
                + json.dumps({"transfer_id": identificador})
            )

        except Exception:
            # Si no se puede avisar es que la conexion ya no esta, y
            # entonces no hay nada que cancelar.
            pass

    # Una sola peticion, con el trozo que pide el reproductor. El
    # tamano total no hace falta saberlo de antemano: lo devuelve el
    # equipo en la respuesta, junto con el trozo que de verdad va a
    # mandar despues de ajustarlo al archivo.
    #
    # Antes se preguntaba primero el tamano y se pedia el trozo
    # despues. Un video se reproduce a base de decenas de peticiones
    # Range —cada salto del usuario es una—, asi que ese viaje de ida
    # y vuelta de mas se pagaba decenas de veces por cada video.
    await websocket.send_text(
        "recording_stream_start:"
        + json.dumps({
            "transfer_id": transfer_id,
            "filename": filename,
            "offset": (pedido or {}).get("offset") or 0,
            "length": (pedido or {}).get("length"),
            "suffix": (pedido or {}).get("suffix")
        })
    )

    try:
        meta = await asyncio.wait_for(
            transmision["meta"], RECORDING_STREAM_META_TIMEOUT
        )

    except asyncio.TimeoutError:
        await cancelar(transfer_id)
        return file_transfer_error(
            504, "El equipo no respondió a tiempo"
        )

    if meta.get("error"):

        recording_streams.pop(transfer_id, None)

        # El equipo dice que el trozo pedido no cabe en el archivo.
        # Eso no es una averia: es la respuesta normal a un Range
        # imposible, y el reproductor sabe interpretarla.
        if meta.get("code") == "range":
            return Response(
                status_code=416,
                headers={
                    "Content-Range": f"bytes */{int(meta.get('size') or 0)}"
                }
            )

        return file_transfer_error(502, meta["error"])

    # El equipo manda el tamano real y el trozo ya ajustado. Las
    # cabeceras se construyen con ESO, no con lo que pidio el
    # navegador: si se declara un Content-Length que no coincide con
    # lo que luego sale, el reproductor se queda colgado esperando
    # bytes que no llegan.
    total = int(meta.get("size") or 0)
    inicio = int(meta.get("offset") or 0)
    longitud = int(meta.get("length") or 0)

    fin = inicio + longitud - 1 if longitud else inicio

    identificador = transfer_id

    async def emitir():

        try:

            while True:

                try:
                    pieza = await asyncio.wait_for(
                        transmision["queue"].get(),
                        RECORDING_STREAM_CHUNK_TIMEOUT
                    )

                except asyncio.TimeoutError:
                    break

                if pieza is None or isinstance(pieza, dict):
                    break

                yield pieza

        finally:

            # Pase lo que pase —fin normal, error, o el usuario que
            # cierra el reproductor y el navegador corta la
            # conexion—, la transmision se da de baja y se le dice al
            # equipo que no siga leyendo el archivo.
            if recording_streams.pop(identificador, None) is not None:

                try:
                    await websocket.send_text(
                        "recording_stream_abort:"
                        + json.dumps({"transfer_id": identificador})
                    )

                except Exception:
                    pass

    cabeceras = {
        "Content-Length": str(longitud),
        "Accept-Ranges": "bytes",

        # El video sale del disco de otra maquina y puede cambiar o
        # desaparecer: que no se quede en ninguna cache intermedia.
        "Cache-Control": "no-store"
    }

    if pedido is not None:
        cabeceras["Content-Range"] = f"bytes {inicio}-{fin}/{total}"

    return StreamingResponse(
        emitir(),
        status_code=206 if pedido is not None else 200,
        media_type="video/mp4",
        headers=cabeceras
    )


@app.get("/api/recordings/{recording_id}/download")
def recording_download(recording_id: int, request: Request):

    _, error = require_permission(request, "recordings.download")

    if error:
        return error


    # Protegido por sesión. Descarga el MP4 por streaming, sin borrar ni modificar
    # el original. Se resuelve por recording_id, nunca por una ruta del frontend.
    recording = get_recording(recording_id)

    if not recording:
        return file_transfer_error(404, "Grabación no encontrada")

    # Solo en el equipo: el servidor no tiene el archivo. Se dice con
    # claridad en vez de devolver un 404 que parece un error del sistema.
    if storage_state_of(recording) != STORAGE_STORED:
        return file_transfer_error(
            409,
            "Esta grabacion solo esta en el equipo. Guardala en el "
            "servidor para poder verla o descargarla desde aqui."
        )

    rel_path = recording.get("path") or ""

    base = _os.path.realpath(str(get_recordings_dir()))
    target = _os.path.realpath(_os.path.join(base, rel_path))

    # Defensa contra path traversal: el archivo debe quedar dentro de la carpeta
    if target != base and not target.startswith(base + _os.sep):
        return file_transfer_error(403, "Ruta de grabación no permitida")

    if not _os.path.isfile(target):
        return file_transfer_error(404, "El archivo de la grabación no existe")

    # Nombre de descarga legible: incluye id y hostname si está disponible
    host = recording.get("hostname") or recording.get("device_id") or "grabacion"
    download_name = f"{host}_{recording_id}.mp4"

    # FileResponse transmite el archivo por bloques (soporta archivos grandes)
    return FileResponse(
        target,
        media_type="video/mp4",
        filename=download_name,
        content_disposition_type="attachment"
    )


@app.get("/api/devices/{device_id}/recording/status")
def recording_status(device_id: str):

    status = latest_recording_status.get(
        device_id
    )

    if status is None:
        return {
            "status": "unknown",
            "recording": False
        }

    return {
        "status": "ok",
        **status
    }


# ==============================
# GRABACION CONTINUA (tipo cámara de seguridad, por dispositivo)
# ==============================

@app.get("/api/devices/{device_id}/recording/continuous")
def get_continuous(device_id: str):

    # Protegido por sesión. Devuelve el flag y el último estado reportado.
    enabled = get_continuous_recording(device_id)

    if enabled is None:
        return {
            "status": "error",
            "message": "Dispositivo no encontrado"
        }

    reported = latest_recording_status.get(device_id) or {}

    return {
        "status": "ok",
        "continuous_recording_enabled": bool(enabled),
        "online": device_id in connected_agents,
        "recording": bool(reported.get("recording")),
        "state": reported.get("state"),
        "last_segment": reported.get("last_segment"),
        "last_upload": reported.get("last_upload"),
        "pending_uploads": reported.get("pending_uploads")
    }


@app.post("/api/devices/{device_id}/recording/continuous")
async def set_continuous(device_id: str, data: dict, request: Request):

    # Protegido por sesión (función administrativa sensible).
    if get_continuous_recording(device_id) is None:
        log_audit(
            "recording.continuous", request=request, device_id=device_id,
            status=STATUS_ERROR, details="Dispositivo no encontrado"
        )
        return file_transfer_error(404, "Dispositivo no encontrado")

    enabled = 1 if data.get("enabled") else 0

    set_continuous_recording(device_id, enabled)

    # Aquí sí hay resultado explícito: el cambio queda guardado en la base
    # del servidor, esté o no conectado el Agent.
    log_audit(
        "recording.continuous", request=request, device_id=device_id,
        status=STATUS_SUCCESS,
        details={"enabled": bool(enabled)}
    )

    # Si el Agent está conectado, se le notifica de inmediato
    websocket = connected_agents.get(device_id)
    if websocket:
        try:
            await websocket.send_text(
                "set_continuous:" + json.dumps({"enabled": bool(enabled)})
            )
        except Exception:
            pass

    return {
        "status": "ok",
        "device_id": device_id,
        "continuous_recording_enabled": bool(enabled)
    }


# ==============================
# CONSULTA DE PROCESOS Y SERVICIOS (solo lectura)
# ==============================

async def _consultar_agente(device_id, request, kind, comando, accion,
                            normalizar):
    """
    Pregunta algo al Agent y espera su respuesta.

    Solo consulta: el Agent no recibe ningún parámetro del frontend, solo
    un nombre de comando fijo y un identificador generado aquí. No hay
    forma de que el texto que escriba un usuario llegue a ejecutarse.

    El resultado se audita como success o error, con el número de filas,
    nunca la lista entera: el registro de auditoría no es un almacén de
    inventario.
    """

    def auditar(estado, detalle):
        log_audit(accion, request=request, device_id=device_id,
                  status=estado, details=detalle)

    # El equipo debe existir. Si no, ni se pregunta: evita que un
    # device_id inventado desde el navegador llegue a ninguna parte.
    if not device_exists(device_id):
        auditar(STATUS_ERROR, "Dispositivo no encontrado")
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Dispositivo no encontrado"}
        )

    websocket = connected_agents.get(device_id)

    if not websocket:
        auditar(STATUS_ERROR, "Agent no conectado")
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Agent is not connected"}
        )

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    query_id = create_query(device_id, kind, future)

    try:

        await websocket.send_text(
            comando + ":" + json.dumps({"query_id": query_id})
        )

        respuesta = await asyncio.wait_for(future, QUERY_TIMEOUT_SECONDS)

    except asyncio.TimeoutError:
        discard_query(query_id)
        auditar(STATUS_ERROR, "El Agent no respondió a tiempo")
        return JSONResponse(
            status_code=504,
            content={"status": "error",
                     "message": "El Agent no respondió a tiempo"}
        )

    except Exception as error:
        discard_query(query_id)
        auditar(STATUS_ERROR, f"Error al consultar: {error}")
        return JSONResponse(
            status_code=502,
            content={"status": "error",
                     "message": f"Error al consultar: {error}"}
        )

    finally:
        discard_query(query_id)

    resultado = normalizar(respuesta)

    if resultado.get("error"):
        auditar(STATUS_ERROR, resultado["error"])
        return JSONResponse(
            status_code=502,
            content={"status": "error", "message": resultado["error"]}
        )

    # Solo el resumen va al registro; la lista se devuelve al panel.
    auditar(STATUS_SUCCESS, {"count": resultado.get("count", 0)})

    return {"status": "ok", "device_id": device_id, **resultado}


@app.get("/api/devices/{device_id}/processes")
async def list_device_processes(device_id: str, request: Request):
    """Procesos activos del equipo. Solo lectura (G2)."""

    _, error = require_permission(request, "processes.view")

    if error:
        return error

    return await _consultar_agente(
        device_id, request, KIND_PROCESSES,
        "get_processes", "device.processes", normalize_processes
    )


@app.get("/api/devices/{device_id}/services")
async def list_device_services(device_id: str, request: Request):
    """Servicios de Windows del equipo. Solo lectura (G3)."""

    _, error = require_permission(request, "services.view")

    if error:
        return error

    return await _consultar_agente(
        device_id, request, KIND_SERVICES,
        "get_services", "device.services", normalize_services
    )


# ==============================
# EMPUJAR UNA ACTUALIZACION
# ==============================
#
# El Agent comprueba su version cada cierto tiempo por su cuenta. Eso
# basta para el dia a dia y no basta para una correccion urgente: sin
# forma de empujarla, la ultima maquina tarda hasta un intervalo
# entero en recibirla y no hay nada que hacer mientras.
#
# Esto paso de verdad: una semana de grabaciones perdidas, la
# correccion publicada y lista en el servidor, y una tarde entera
# mirando como cada equipo la recogia a su hora.
#
# La orden no lleva ninguna version ni ninguna ruta: dice "comprueba
# ahora", y el Agent hace exactamente lo que ya hacia solo -pedir el
# manifiesto, verificar la firma, decidir-. No hay forma de usarla
# para instalar nada que no estuviera ya publicado y firmado.


async def pedir_que_compruebe_version(device_id):
    """
    Manda 'check_update' a un equipo. True si se pudo mandar.

    No espera respuesta: si hay version nueva, el Agent se actualiza
    y se reinicia, asi que la confirmacion que importa es la version
    que informe al volver a conectarse.
    """

    websocket = connected_agents.get(device_id)

    if not websocket:
        return False

    try:
        await websocket.send_text("check_update")
        return True

    except Exception as error:
        print(f"[update] no se pudo avisar a {device_id}: {error}")
        return False


@app.post("/api/devices/{device_id}/check-update")
async def device_check_update(device_id: str, request: Request):
    """Pide a UN equipo que compruebe su version ahora."""

    usuario, error = require_permission(request, "devices.manage")

    if error:
        return error

    if not await pedir_que_compruebe_version(device_id):

        log_audit(
            "agent.check_update", request=request, device_id=device_id,
            status=STATUS_ERROR, details="El equipo no esta conectado"
        )

        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "message": "El equipo no esta conectado"}
        )

    log_audit(
        "agent.check_update", request=request, device_id=device_id,
        details="Comprobacion de version pedida desde el panel"
    )

    return {
        "status": "ok",
        "message": (
            "Se le pidio al equipo que compruebe su version. Si hay una "
            "nueva se actualizara y se reiniciara solo."
        )
    }


@app.post("/api/agent/check-update")
async def agent_check_update_all(request: Request):
    """
    Se lo pide a TODOS los equipos conectados.

    Es el caso real: se publica una correccion y se quiere en toda la
    flota ya, no en seis horas y en desorden.
    """

    usuario, error = require_permission(request, "devices.manage")

    if error:
        return error

    # Una copia de las claves: mandar mensajes puede cerrar
    # conexiones y no se puede recorrer el diccionario mientras
    # cambia de tamano.
    equipos = list(connected_agents.keys())

    avisados = 0

    for device_id in equipos:

        if await pedir_que_compruebe_version(device_id):
            avisados += 1

    log_audit(
        "agent.check_update", request=request,
        details="Comprobacion pedida a %d de %d equipos conectados"
                % (avisados, len(equipos))
    )

    return {
        "status": "ok",
        "avisados": avisados,
        "conectados": len(equipos),
        "message": "Se le pidio a %d equipo(s) que comprueben su version."
                   % avisados
    }


# ==============================
# ACCIONES SOBRE EL EQUIPO (bloquear, cerrar sesion, reiniciar, apagar)
# ==============================

@app.post("/api/devices/{device_id}/power/{action}")
async def device_power_action(device_id: str, action: str, request: Request):
    """
    Pide al equipo que se bloquee, cierre sesion, se reinicie o se apague.

    La accion viaja en la RUTA y se busca en una lista cerrada. No se lee
    nada del cuerpo: no existe ningun parametro por el que pueda colarse
    un comando, un script ni un argumento.

    El orden importa: primero se comprueba que la accion existe, luego el
    permiso del usuario autenticado, luego que el equipo exista y este
    conectado, y solo entonces se envia nada. Un usuario sin permiso no
    llega a provocar ni un mensaje al Agent.
    """

    # 1. Accion conocida
    try:
        definicion = power.get_action(action)

    except power.PowerError as problema:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": str(problema)}
        )

    # El nombre ya se valido tal cual contra la lista cerrada
    nombre = action
    accion_auditada = definicion["audit"]

    # 2. Permiso real del usuario de la sesion. El permiso es propio de
    #    cada accion: poder bloquear no da derecho a apagar.
    usuario, error = require_permission(request, definicion["permission"])

    if error:

        # Un intento sin permiso se registra: es justo lo que interesa
        # ver en el historial.
        log_audit(
            accion_auditada, request=request, device_id=device_id,
            status=STATUS_ERROR,
            details="Rechazada: el usuario no tiene permiso"
        )

        return error

    def auditar(estado, detalle):
        log_audit(accion_auditada, request=request, device_id=device_id,
                  status=estado, details=detalle)

    # 3. El equipo debe existir
    if not device_exists(device_id):
        auditar(STATUS_ERROR, "Dispositivo no encontrado")
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Dispositivo no encontrado"}
        )

    # 4. Proteccion contra la repeticion accidental. Va ANTES de enviar
    #    nada: el doble clic no debe llegar al equipo.
    espera = power.seconds_until_repeat_allowed(device_id, nombre)

    if espera:

        auditar(STATUS_ERROR,
                "Repeticion ignorada: la misma accion se pidio hace un "
                "momento")

        return JSONResponse(
            status_code=409,
            headers={"Retry-After": str(espera)},
            content={
                "status": "error",
                "message": (
                    f"Esa accion ya se envio hace unos segundos. "
                    f"Espera {espera} segundos si de verdad quieres "
                    "repetirla."
                )
            }
        )

    # 5. El equipo debe estar conectado. El WebSocket se busca por el
    #    device_id, y esa tabla la llena el propio Agent con la identidad
    #    derivada de su token: no hay forma de que la orden acabe en otro
    #    equipo.
    websocket = connected_agents.get(device_id)

    if not websocket:
        auditar(STATUS_ERROR, "El equipo no esta conectado")
        return JSONResponse(
            status_code=409,
            content={"status": "error",
                     "message": "El equipo no esta conectado"}
        )

    # A partir de aqui la orden se considera lanzada
    power.register_action(device_id, nombre)

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    query_id = create_query(device_id, KIND_POWER, future)

    try:

        await websocket.send_text(
            "power_action:" + json.dumps({
                "query_id": query_id,
                "action": nombre
            })
        )

        respuesta = await asyncio.wait_for(
            future, power.ACK_TIMEOUT_SECONDS
        )

    except asyncio.TimeoutError:

        discard_query(query_id)

        # Para reiniciar y apagar NO se olvida la anotacion: el equipo
        # puede haber recibido la orden y estar apagandose justo ahora.
        # Reintentar a ciegas es exactamente lo que no se debe hacer.
        if not definicion["destructive"]:
            power.forget_action(device_id, nombre)

        auditar(
            STATUS_ERROR,
            "Sin confirmacion del equipo: no se sabe si llego a ejecutarse"
        )

        return JSONResponse(
            status_code=504,
            content={
                "status": "error",
                "message": (
                    "El equipo no confirmo la accion. No se puede saber si "
                    "llego a ejecutarse; no se reintenta por si acaso."
                )
            }
        )

    except Exception as error:

        discard_query(query_id)
        power.forget_action(device_id, nombre)

        auditar(STATUS_ERROR, f"No se pudo enviar la orden: {error}")

        return JSONResponse(
            status_code=502,
            content={"status": "error",
                     "message": f"No se pudo enviar la orden: {error}"}
        )

    finally:
        discard_query(query_id)

    resultado = power.normalize_result(respuesta, nombre)

    if resultado.get("error"):

        # El equipo contesto que no pudo: la accion no ocurrio, asi que
        # se permite volver a intentarlo.
        power.forget_action(device_id, nombre)

        auditar(STATUS_ERROR, resultado["error"])

        return JSONResponse(
            status_code=502,
            content={"status": "error", "message": resultado["error"]}
        )

    descripcion = power.describe_outcome(resultado, nombre)

    auditar(STATUS_SUCCESS, descripcion)

    return {
        "status": "ok",
        "device_id": device_id,
        "action": nombre,
        # 'executed' es lo comprobado; 'pending' es lo aceptado pero aun
        # por ocurrir. El panel necesita distinguirlos para no decirle al
        # operador que un equipo esta apagado cuando solo lo prometio.
        "executed": resultado.get("executed", False),
        "pending": resultado.get("pending", False),
        "message": descripcion
    }


def recording_rel_path(device_id, filename, started_at=""):
    """
    Ruta relativa con la que se archiva una grabacion en el servidor:

        <device_id>/AAAA/MM/DD/<nombre>.mp4

    La calculan igual la ficha que llega por WebSocket y la subida real,
    de modo que las dos apuntan a la MISMA fila. Si cada una usara su
    criterio, archivar crearia una fila nueva en vez de completar la que
    ya existe, y acabariamos con la grabacion duplicada en el panel.

    Devuelve None si el nombre no es valido. Solo se queda con el nombre
    de archivo: cualquier ruta que venga se descarta.
    """

    from datetime import datetime, timezone

    safe_name = _os.path.basename(str(filename or "")).strip()

    if not safe_name.lower().endswith(".mp4"):
        return None

    try:
        cuando = (
            datetime.fromisoformat(started_at) if started_at
            else datetime.now(timezone.utc)
        )
    except ValueError:
        cuando = datetime.now(timezone.utc)

    return "/".join([
        device_id,
        cuando.strftime("%Y"),
        cuando.strftime("%m"),
        cuando.strftime("%d"),
        safe_name
    ])


def registrar_ficha_local(device_id, ficha):
    """
    Anota la ficha de una grabacion que esta SOLO en el equipo.

    Es metadato: nombre, fechas y tamano. El video no viaja.
    """

    if not isinstance(ficha, dict):
        return None

    started_at = str(ficha.get("started_at") or "")

    rel_path = recording_rel_path(
        device_id, ficha.get("filename"), started_at
    )

    if rel_path is None:
        return None

    try:
        recording_id, creada = register_local_recording(
            device_id=device_id,
            path=rel_path,
            started_at=started_at,
            ended_at=str(ficha.get("ended_at") or ""),
            duration_sec=int(ficha.get("duration_sec") or 0),
            size_bytes=int(ficha.get("size_bytes") or 0)
        )

    except Exception as error:
        print(f"[grabaciones] No se pudo anotar la ficha: {error}")
        return None

    return recording_id if creada else None


# ==============================
# LA JORNADA UNIDA: UN VIDEO POR DIA Y EQUIPO
# ==============================

import re as _re

# <equipo>/AAAA/MM/DD/jornada_AAAA-MM-DD.mp4 y nada mas.
#
# La ruta la calcula el equipo, asi que se valida entera aqui en vez
# de confiar en ella: es lo unico que separa "anotar el video del
# dia" de "dejar que un equipo escriba una fila apuntando a donde
# quiera".
RUTA_DE_JORNADA = _re.compile(
    r"^(?P<equipo>[A-Za-z0-9_-]{1,64})/"
    r"(?P<anio>\d{4})/(?P<mes>\d{2})/(?P<dia>\d{2})/"
    r"jornada_(?P=anio)-(?P=mes)-(?P=dia)\.mp4$"
)


def ruta_de_jornada_valida(device_id, ruta, fecha):
    """
    True si esa ruta es la del video de ese dia y de ESE equipo.

    Se exige que las tres cosas concuerden -carpeta, nombre y fecha
    anunciada-, porque cada una viene del mismo sitio y una sola no
    prueba nada.
    """

    encaje = RUTA_DE_JORNADA.match(str(ruta or ""))

    if not encaje:
        return False

    if encaje.group("equipo") != device_id:
        return False

    del_nombre = "%s-%s-%s" % (
        encaje.group("anio"), encaje.group("mes"), encaje.group("dia")
    )

    return del_nombre == str(fecha or "")


def anotar_jornada_unida(device_id, carga):
    """
    Anota el video del dia y retira las fichas de sus trozos.

    El orden importa y es este: primero se anota la jornada, luego se
    marcan los segmentos. Si se hiciera al reves y algo fallara en
    medio, el dia se quedaria sin ninguna ficha visible -ni los
    trozos ni la jornada- y en el panel pareceria que ese dia no se
    grabo nada.

    Las fechas del dia no las manda el equipo: se sacan de las fichas
    que el servidor ya tiene de cada segmento, que son las mejor
    medidas que existen -llegaron una a una, al cerrarse cada trozo-.
    """

    fecha = str(carga.get("fecha") or "")
    ruta = str(carga.get("path") or "")

    if not ruta_de_jornada_valida(device_id, ruta, fecha):
        print(f"[jornada] ruta rechazada de {device_id}: {ruta!r}")
        return False

    # Solo rutas de este equipo, y con la forma que escribe el Agent.
    segmentos = [
        s for s in (carga.get("segmentos") or [])
        if isinstance(s, str) and s.startswith(device_id + "/")
    ]

    desde, hasta = extremos_de_los_segmentos(device_id, segmentos)

    try:
        register_local_recording(
            device_id=device_id,
            path=ruta,
            started_at=desde or "",
            ended_at=hasta or "",
            duration_sec=int(carga.get("duration_sec") or 0),
            size_bytes=int(carga.get("size_bytes") or 0)
        )

    except Exception as error:
        print(f"[jornada] no se pudo anotar el video del dia: {error}")
        return False

    retiradas = marcar_segmentos_unidos(device_id, segmentos, fecha)

    print(
        f"[jornada] {device_id} {fecha}: un video de "
        f"{int(carga.get('size_bytes') or 0) // 1048576} MB, "
        f"{retiradas} trozos retirados del panel"
    )

    return True


# ==============================
# ARCHIVAR UNA GRABACION EN EL SERVIDOR
# ==============================

@app.post("/api/recordings/{recording_id}/store")
async def recording_store(recording_id: int, request: Request):
    """
    Archiva en el servidor UNA grabacion concreta.

    Las grabaciones nuevas se quedan en el equipo; esta es la unica via
    por la que un archivo llega al servidor, y siempre porque alguien lo
    ha pedido para esa grabacion en particular.

    La peticion no lleva ninguna ruta: solo el identificador de la fila.
    El nombre del archivo lo saca el servidor de su propia base, y el
    Agent lo busca dentro de su carpeta administrada.
    """

    usuario, error = require_permission(request, "recordings.manage")

    if error:

        log_audit(
            "recording.store", request=request, status=STATUS_ERROR,
            details={"recording_id": recording_id,
                     "error": "El usuario no tiene permiso"}
        )

        return error

    grabacion = get_recording(recording_id)

    if grabacion is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "La grabacion no existe"}
        )

    device_id = grabacion["device_id"]

    def auditar(estado, detalle):
        log_audit("recording.store", request=request, device_id=device_id,
                  status=estado,
                  details={"recording_id": recording_id, **detalle})

    estado_actual = storage_state_of(grabacion)

    # Idempotente: si ya esta archivada no se vuelve a transferir
    if estado_actual == STORAGE_STORED:
        return {
            "status": "ok",
            "recording_id": recording_id,
            "storage_state": STORAGE_STORED,
            "already_stored": True,
            "message": "Esta grabacion ya estaba guardada en el servidor"
        }

    # Doble clic y peticiones simultaneas: mientras una transferencia
    # sigue en curso, otra no arranca.
    if estado_actual == STORAGE_PENDING:
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "storage_state": STORAGE_PENDING,
                "message": "Esta grabacion ya se esta guardando"
            }
        )

    nombre = _os.path.basename(grabacion["path"] or "")

    if not nombre.lower().endswith(".mp4"):
        auditar(STATUS_ERROR, {"error": "Nombre de grabacion no valido"})
        return JSONResponse(
            status_code=400,
            content={"status": "error",
                     "message": "Nombre de grabacion no valido"}
        )

    websocket = connected_agents.get(device_id)

    if not websocket:

        auditar(STATUS_ERROR, {"error": "El equipo no esta conectado"})

        # No se deja 'pendiente': no hay cola persistente de solicitudes,
        # y marcarla asi seria prometer un reintento que nadie hara.
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "storage_state": estado_actual,
                "message": (
                    "El equipo esta desconectado. Vuelve a intentarlo "
                    "cuando este en linea."
                )
            }
        )

    set_storage_state(recording_id, STORAGE_PENDING)

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    query_id = create_query(device_id, KIND_STORE, future)

    try:

        await websocket.send_text(
            "store_recording:" + json.dumps({
                "query_id": query_id,
                "filename": nombre
            })
        )

        respuesta = await asyncio.wait_for(future, STORE_TIMEOUT_SECONDS)

    except asyncio.TimeoutError:

        discard_query(query_id)
        set_storage_state(recording_id, STORAGE_ERROR)

        auditar(STATUS_ERROR, {"error": "El equipo no respondio a tiempo"})

        return JSONResponse(
            status_code=504,
            content={
                "status": "error",
                "storage_state": STORAGE_ERROR,
                "message": "El equipo no respondio a tiempo"
            }
        )

    except Exception as error:

        discard_query(query_id)
        set_storage_state(recording_id, STORAGE_ERROR)

        auditar(STATUS_ERROR, {"error": f"No se pudo pedir el archivo: {error}"})

        return JSONResponse(
            status_code=502,
            content={"status": "error",
                     "storage_state": STORAGE_ERROR,
                     "message": f"No se pudo pedir el archivo: {error}"}
        )

    finally:
        discard_query(query_id)

    if not isinstance(respuesta, dict) or not respuesta.get("stored"):

        motivo = "El equipo no pudo enviar la grabacion"

        if isinstance(respuesta, dict) and respuesta.get("error"):
            motivo = str(respuesta["error"])[:200]

        set_storage_state(recording_id, STORAGE_ERROR)

        auditar(STATUS_ERROR, {"error": motivo})

        return JSONResponse(
            status_code=502,
            content={"status": "error",
                     "storage_state": STORAGE_ERROR,
                     "message": motivo}
        )

    # El Agent subio el archivo por el endpoint de siempre, que ya aplico
    # F3 y F4 y dejo la fila como archivada. Se relee para no fiarse de
    # lo que diga el Agent.
    final = get_recording(recording_id)
    estado_final = storage_state_of(final) if final else None

    if estado_final != STORAGE_STORED:

        set_storage_state(recording_id, STORAGE_ERROR)

        auditar(STATUS_ERROR,
                {"error": "La grabacion no quedo validada en el servidor"})

        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "storage_state": STORAGE_ERROR,
                "message": (
                    "La grabacion llego pero no paso la validacion; "
                    "no se ha archivado."
                )
            }
        )

    auditar(STATUS_SUCCESS, {"detalle": "Grabacion archivada en el servidor"})

    return {
        "status": "ok",
        "recording_id": recording_id,
        "storage_state": STORAGE_STORED,
        "message": (
            "Grabacion guardada en el servidor. La copia del equipo se "
            "conserva."
        )
    }


# ==============================
# RETENCION LOCAL DEL AGENT
# ==============================
#
# El Agent conserva sus grabaciones despues de subirlas y las retira
# cuando cumplen los dias configurados. Para eso necesita dos cosas del
# servidor: cuantos dias, y cuales estan marcadas para conservar.
#
# La correspondencia entre la copia del servidor y la local se hace por el
# NOMBRE del archivo (rec_<marca de tiempo>.mp4), que el Agent genera y el
# servidor conserva tal cual al publicarlo.

def build_retention_policy(device_id):
    """Dias de retencion y nombres de archivo marcados para conservar."""

    connection = get_connection()

    try:
        filas = connection.execute(
            "SELECT path FROM recordings WHERE device_id = ? AND keep = 1",
            (device_id,)
        ).fetchall()

    finally:
        connection.close()

    return {
        # Los dias son los de la organizacion dueña del equipo: cada
        # empresa decide cuanto conserva sus grabaciones.
        "days": get_retention_days(get_device_organization(device_id)),
        "keep": [
            fila["path"].replace("\\", "/").rsplit("/", 1)[-1]
            for fila in filas
        ]
    }


async def send_retention_policy(device_id):
    """Manda la politica al Agent, si esta conectado."""

    websocket = connected_agents.get(device_id)

    if not websocket:
        return False

    try:
        await websocket.send_text(
            "set_retention:" + json.dumps(build_retention_policy(device_id))
        )
        return True

    except Exception as error:
        print(f"[retencion] no se pudo avisar a {device_id}: {error}")
        return False


async def broadcast_retention_policy(organization_id=None):
    """
    Reenvia la politica a los Agents conectados.

    Con organizacion, solo a los suyos: cambiar la retencion de una
    empresa no debe tocar los equipos de otra. Sin ella (cambio de la
    instalacion), a todos.
    """

    for device_id in list(connected_agents):

        if organization_id is not None \
                and get_device_organization(device_id) != organization_id:
            continue

        await send_retention_policy(device_id)


# ==============================
# PROGRAMACION DE GRABACION
# ==============================

@app.get("/api/devices/{device_id}/recording/schedule")
def recording_schedule_get(device_id: str, request: Request):

    _, error = require_permission(request, "recordings.view")

    if error:
        return error

    horario = recording_schedule.get_schedule(device_id)

    return {
        "status": "ok",
        "schedule": horario,
        "description": recording_schedule.describe(horario)
    }


@app.post("/api/devices/{device_id}/recording/schedule")
async def recording_schedule_set(device_id: str, data: dict,
                                 request: Request):
    """
    Guarda el horario de un equipo y se lo manda al Agent.

    Si el Agent no esta conectado, el horario queda guardado igualmente y
    se le entrega en cuanto vuelva: la programacion no depende de que
    nadie tenga el panel abierto.
    """

    _, error = require_permission(request, "recordings.manage")

    if error:
        return error

    if not device_exists(device_id):
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Dispositivo no encontrado"}
        )

    try:
        horario = recording_schedule.set_schedule(device_id, data)

    except recording_schedule.ScheduleError as problema:

        log_audit("recording.schedule", request=request, device_id=device_id,
                  status=STATUS_ERROR, details=f"Horario rechazado: {problema}")

        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(problema)}
        )

    # Guardar el horario desde el panel es una decision expresa del
    # operador: retira la supresion que hubiera dejado una parada
    # manual.
    #
    # Se hace aqui y no en el Agent porque el Agent solo olvida la
    # excepcion cuando el CONTENIDO del horario cambia -tuvo que ser
    # asi: el servidor le reenvia el mismo horario en cada
    # reconexion, y borrarla siempre anulaba la parada manual con
    # cualquier corte de red-. Guardar el mismo horario sin cambios
    # sigue siendo una decision, y por ese camino si debe retirarse.
    devices_mod.set_schedule_suppression(device_id, None)

    entregado = False

    websocket = connected_agents.get(device_id)

    if websocket:
        try:
            await websocket.send_text("set_schedule:" + json.dumps(horario))

            await websocket.send_text(
                "schedule_suppression:" + json.dumps({"slot": None})
            )

            entregado = True
        except Exception as error:
            print(f"[horario] no se pudo avisar al Agent: {error}")

    log_audit(
        "recording.schedule", request=request, device_id=device_id,
        status=STATUS_SUCCESS,
        details={"resumen": recording_schedule.describe(horario),
                 "entregado_al_agent": entregado}
    )

    return {
        "status": "ok",
        "schedule": horario,
        "description": recording_schedule.describe(horario),
        "delivered": entregado
    }


# Por debajo de esto no hay video posible: un segundo de pantalla
# ocupa decenas de kilobytes. El mismo numero que usa el panel para
# no prometer la reproduccion de un archivo vacio.
TAMANO_MINIMO_DE_UN_VIDEO = 10 * 1024

DELETE_TIMEOUT_SECONDS = 30


async def _borrar_grabacion(recording_id, request, auditar):
    """
    Borra una grabacion de verdad: primero del equipo, luego la ficha.

    El orden NO es un detalle. El Agent manda su catalogo de
    grabaciones locales al conectar, y el servidor crea una ficha
    por cada archivo que encuentra. Borrar la ficha con el archivo
    todavia en disco no borra nada: vuelve en la siguiente conexion.

    Paso de verdad en produccion. Se borraron 58 fichas y once
    minutos despues estaban las 58 otra vez. De ahi este camino.

    Devuelve (borrada, respuesta_de_error). Si el equipo no esta
    conectado NO se borra la ficha: dejarla es correcto, porque el
    archivo sigue existiendo y la ficha dice la verdad.
    """

    grabacion = get_recording(recording_id)

    if not grabacion:
        return False, JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Grabación no encontrada"}
        )

    # Una marca de conservar es una decision de una persona. No se
    # pisa por una limpieza automatica, ni aunque la grabacion este
    # vacia: quien la marco sabra por que.
    if grabacion.get("keep"):
        return False, JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "code": "marcada_para_conservar",
                "message": (
                    "Esta grabación está marcada como Conservar. "
                    "Quita la marca antes de borrarla."
                )
            }
        )

    device_id = grabacion["device_id"]
    nombre = _os.path.basename(str(grabacion.get("path") or ""))

    # --- 1. La copia del servidor, si la hay ---
    if grabacion.get("storage_state") == STORAGE_STORED:

        try:
            ruta = _os.path.join(str(get_recordings_dir()), grabacion["path"])

            if _os.path.isfile(ruta):
                _os.remove(ruta)

        except OSError as problema:
            auditar(STATUS_ERROR, {"error": f"No se pudo borrar del servidor: {problema}"})

            return False, JSONResponse(
                status_code=500,
                content={"status": "error",
                         "message": "No se pudo borrar el archivo del servidor"}
            )

    # --- 2. La copia del equipo ---
    #
    # Siempre se pide, aunque la ficha diga que esta archivada: el
    # equipo conserva su copia hasta que se le dice que la suelte.
    websocket = connected_agents.get(device_id)

    if not websocket:
        auditar(STATUS_ERROR, {"error": "El equipo no esta conectado"})

        return False, JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "code": "equipo_desconectado",
                "message": (
                    "El equipo está desconectado. La grabación sigue "
                    "en su disco, así que borrar solo la ficha no "
                    "serviría: volvería a aparecer al reconectarse."
                )
            }
        )

    if not agent_supports(device_id, "delete_recording"):
        auditar(STATUS_ERROR, {"error": "Agent sin borrado"})

        return False, JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "code": "agent_sin_borrado",
                "message": (
                    "Este equipo tiene una versión del Agent que "
                    "todavía no sabe borrar grabaciones."
                )
            }
        )

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    query_id = create_query(device_id, KIND_DELETE, future)

    try:
        await websocket.send_text(
            "delete_recording:" + json.dumps({
                "query_id": query_id,
                "filename": nombre
            })
        )

        respuesta = await asyncio.wait_for(future, DELETE_TIMEOUT_SECONDS)

    except asyncio.TimeoutError:
        discard_query(query_id)
        auditar(STATUS_ERROR, {"error": "El equipo no respondio a tiempo"})

        return False, JSONResponse(
            status_code=504,
            content={"status": "error",
                     "message": "El equipo no respondió a tiempo"}
        )

    except Exception as error:
        discard_query(query_id)
        auditar(STATUS_ERROR, {"error": str(error)})

        return False, JSONResponse(
            status_code=502,
            content={"status": "error",
                     "message": f"No se pudo pedir el borrado: {error}"}
        )

    finally:
        discard_query(query_id)

    if not isinstance(respuesta, dict) or not respuesta.get("ok"):

        motivo = "El equipo no pudo borrarla"

        if isinstance(respuesta, dict) and respuesta.get("message"):
            motivo = str(respuesta["message"])[:200]

        auditar(STATUS_ERROR, {"error": motivo})

        return False, JSONResponse(
            status_code=502,
            content={"status": "error", "message": motivo}
        )

    # --- 3. Y AHORA la ficha ---
    recordings_mod.borrar_ficha(recording_id)

    auditar(STATUS_SUCCESS, {
        "archivo": nombre,
        "ya_no_estaba_en_el_equipo": bool(respuesta.get("ya_no_estaba"))
    })

    return True, None


@app.delete("/api/recordings/{recording_id}")
async def borrar_grabacion(recording_id: int, request: Request):
    """
    Borra una grabacion del equipo y del servidor.

    No existia ninguna forma de borrar una grabacion, y por eso una
    limpieza hecha a mano en la base se deshizo sola: el catalogo
    del equipo volvio a crear las fichas.
    """

    usuario, error = require_permission(request, "recordings.manage")

    if error:
        return error

    grabacion = get_recording(recording_id)

    if grabacion and not is_platform_owner(usuario):

        if (get_device_organization(grabacion["device_id"])
                != organization_of(usuario)):

            return JSONResponse(
                status_code=404,
                content={"status": "error",
                         "message": "Grabación no encontrada"}
            )

    def auditar(estado, detalle=None):
        log_audit("recording.delete", request=request,
                  device_id=(grabacion or {}).get("device_id"),
                  status=estado,
                  details={"recording_id": recording_id, **(detalle or {})})

    borrada, fallo = await _borrar_grabacion(recording_id, request, auditar)

    if not borrada:
        return fallo

    return {"status": "ok", "borrada": recording_id}


@app.post("/api/recordings/cleanup-empty")
async def limpiar_grabaciones_vacias(request: Request):
    """
    Borra las grabaciones que no tienen video dentro.

    Son archivos de doscientos y pico bytes -solo la cabecera- que
    produjo un equipo que abrio el archivo y no llego a capturar
    nada. No se pueden reproducir y no van a poder nunca.

    Se borran del EQUIPO y del servidor, en ese orden. Las marcadas
    como Conservar no se tocan, y los equipos desconectados se
    dejan para otra vez: su archivo sigue ahi y la ficha dice la
    verdad.
    """

    usuario, error = require_permission(request, "recordings.manage")

    if error:
        return error

    propia = None if is_platform_owner(usuario) else organization_of(usuario)

    borradas = 0
    conservadas = 0
    sin_conexion = 0
    fallidas = 0

    for grabacion in list_recordings():

        if int(grabacion.get("size_bytes") or 0) >= TAMANO_MINIMO_DE_UN_VIDEO:
            continue

        if propia is not None:
            if get_device_organization(grabacion["device_id"]) != propia:
                continue

        if grabacion.get("keep"):
            conservadas += 1
            continue

        if grabacion["device_id"] not in connected_agents:
            sin_conexion += 1
            continue

        def auditar(estado, detalle=None, _g=grabacion):
            log_audit("recording.delete", request=request,
                      device_id=_g["device_id"], status=estado,
                      details={"recording_id": _g["id"],
                               "limpieza": True, **(detalle or {})})

        borrada, _fallo = await _borrar_grabacion(
            grabacion["id"], request, auditar
        )

        if borrada:
            borradas += 1
        else:
            fallidas += 1

    return {
        "status": "ok",
        "borradas": borradas,
        "conservadas": conservadas,
        "sin_conexion": sin_conexion,
        "fallidas": fallidas
    }


@app.post("/api/recording/schedule/apply-default")
async def aplicar_horario_por_defecto(data: dict, request: Request):
    """
    Pone el horario de la empresa en sus equipos, de una vez.

    Existe porque configurar treinta equipos a mano es una tarde
    perdida y la certeza de olvidarse de alguno. El valor por
    defecto solo se aplica en el ALTA -a proposito, para que apagar
    un equipo concreto no se deshaga solo-, asi que los que ya
    estaban necesitan algo expreso.

    Y expreso quiere decir expreso: esto se ejecuta cuando alguien
    lo pulsa, nunca por su cuenta. Lo contrario convertiria el
    ajuste de la empresa en algo que reescribe decisiones ajenas
    cada cierto tiempo.

    Por omision NO pisa a quien ya tiene horario propio. Un equipo
    que alguien configuro a mano -el turno de noche del almacen, por
    ejemplo- tiene su razon de ser, y rellenar huecos es una cosa y
    borrar el trabajo de otro es otra. Para pisarlos hay que pedirlo
    aparte, y entonces se dice cuantos se van a pisar.
    """

    usuario, error = require_permission(request, "recordings.manage")

    if error:
        return error

    pisar = bool(data.get("incluir_configurados"))

    organizacion = organization_of(usuario)

    por_defecto = ajustes_mod.grabacion_por_defecto(organizacion)

    if por_defecto is None:
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "message": (
                    "Esta empresa tiene desactivado el horario por "
                    "defecto. Actívalo en Ajustes antes de aplicarlo."
                )
            }
        )

    aplicados = []
    respetados = []

    for equipo in get_devices():

        if (not is_platform_owner(usuario)
                and equipo.get("organization_id") != organizacion):
            continue

        if equipo.get("decommissioned_at"):
            continue

        device_id = equipo["device_id"]

        actual = recording_schedule.get_schedule(device_id)

        if actual.get("enabled") and not pisar:
            respetados.append(device_id)
            continue

        try:
            horario = recording_schedule.set_schedule(device_id, por_defecto)

        except recording_schedule.ScheduleError:
            continue

        set_continuous_recording(device_id, True)

        # Al Agent, si esta conectado. Si no lo esta, lo recibira al
        # reconectar: el servidor le manda el horario en cada
        # conexion, asi que no hace falta recordar nada.
        websocket = connected_agents.get(device_id)

        if websocket:

            try:
                await websocket.send_text(
                    "set_schedule:" + json.dumps(horario)
                )
                await websocket.send_text(
                    "set_continuous:" + json.dumps({"enabled": True})
                )

            except Exception as problema:
                print(f"[horario] no se pudo avisar a {device_id}: {problema}")

        aplicados.append(device_id)

    log_audit(
        "recording.schedule.apply_default",
        request=request,
        status=STATUS_SUCCESS,
        details={
            "aplicados": len(aplicados),
            "respetados": len(respetados),
            "piso_los_configurados": pisar,
            "horario": recording_schedule.describe(por_defecto)
        }
    )

    return {
        "status": "ok",
        "aplicados": len(aplicados),
        "respetados": len(respetados),
        "descripcion": recording_schedule.describe(por_defecto)
    }


# ==============================
# ORGANIZACIONES (nivel plataforma)
# ==============================
#
# Administrarlas es cosa de quien opera RemoteAdmin, no de las empresas
# clientes. El Owner de una organizacion no puede crear otras ni ver las
# demas: su mundo empieza y acaba en la suya.

def require_platform_owner(request):
    """(usuario, None) si opera la plataforma; (None, respuesta) si no."""

    usuario = current_user(request)

    if usuario is None:
        return None, JSONResponse(
            status_code=401,
            content={"status": "error", "message": "No autenticado"}
        )

    if not is_platform_owner(usuario) or not usuario.get("active"):
        return None, _denegado(
            "Solo el operador de la plataforma administra organizaciones"
        )

    return usuario, None


@app.get("/api/installation")
def installation_info(request: Request):
    """
    Identidad de esta instalacion de RemoteAdmin.

    Reservado al operador de la plataforma, por coherencia con el resto
    de informacion global: la lista de organizaciones y la auditoria
    completa ya siguen esa regla. Una empresa cliente no tiene por que
    saber nada del despliegue que la aloja, y menos aun en cloud, donde
    comparte instalacion con otras.

    Lo que devuelve es identificacion, no autorizacion: no hay claves,
    tokens ni credenciales que exponer, porque la tabla no los guarda.
    """

    _, error = require_platform_owner(request)

    if error:
        return error

    return {
        "status": "ok",
        "installation": installation.ensure_installation()
    }


@app.get("/api/organizations")
def organizations_list(request: Request):
    """Todas las organizaciones, con su plan, estado y uso."""

    _, error = require_platform_owner(request)

    if error:
        return error

    return {
        "status": "ok",
        "organizations": organizations.list_organizations(),
        "plans": [
            {
                "name": nombre,
                "label": datos["label"],
                "max_devices": datos["max_devices"],
                "max_users": datos["max_users"],
                "storage_mb": datos["storage_mb"],
                "features": list(datos["features"])
            }
            for nombre, datos in organizations.PLANS.items()
        ],
        "subscription_statuses": list(organizations.SUBSCRIPTION_STATUSES),
        "billing_modes": list(organizations.BILLING_MODES),
        "deployment_types": list(organizations.DEPLOYMENT_TYPES)
    }


@app.post("/api/organizations")
def organizations_create(data: dict, request: Request):
    """Crea una organizacion. Solo la plataforma."""

    _, error = require_platform_owner(request)

    if error:

        log_audit("organization.created", request=request,
                  status=STATUS_ERROR,
                  details="Rechazada: no es operador de la plataforma")

        return error

    try:
        organizacion = organizations.create_organization(
            name=data.get("name"),
            plan=data.get("plan", organizations.PLAN_FREE),
            billing_mode=data.get(
                "billing_mode", organizations.BILLING_STANDARD
            ),
            subscription_status=data.get(
                "subscription_status", organizations.STATUS_TRIAL
            ),
            notes=data.get("notes"),
            deployment_type=data.get(
                "deployment_type", organizations.DEPLOYMENT_CLOUD
            ),
            server_url=data.get("server_url")
        )

    except organizations.OrganizationError as problema:

        log_audit("organization.created", request=request,
                  status=STATUS_ERROR, details=str(problema))

        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(problema)}
        )

    # Sin organizacion a proposito: crear una empresa es una accion de
    # la plataforma, no de ninguna empresa.
    log_audit(
        "organization.created", request=request, status=STATUS_SUCCESS,
        organization_id=None,
        details={"organization": organizacion["name"],
                 "plan": organizacion["plan"],
                 "deployment_type": organizacion["deployment_type"],
                 "scope": "platform"}
    )

    return {"status": "ok", "organization": organizacion}


@app.post("/api/organizations/{organization_id}")
def organizations_update(organization_id: int, data: dict,
                         request: Request):
    """
    Cambia plan, estado de suscripcion, modalidad o actividad.

    Suspender es cambiar un campo: no se borra ni un equipo, ni una
    grabacion, ni un usuario. Reactivar devuelve el acceso tal cual.
    """

    _, error = require_platform_owner(request)

    if error:
        return error

    try:
        # server_url se pasa solo si viene en la peticion: asi se
        # distingue "no lo cambies" de "dejalo vacio".
        cambios = {}

        if "server_url" in data:
            cambios["server_url"] = data.get("server_url")

        organizacion = organizations.update_organization(
            organization_id,
            plan=data.get("plan"),
            subscription_status=data.get("subscription_status"),
            billing_mode=data.get("billing_mode"),
            active=data.get("active"),
            notes=data.get("notes"),
            deployment_type=data.get("deployment_type"),
            **cambios
        )

    except organizations.OrganizationError as problema:

        log_audit("organization.updated", request=request,
                  status=STATUS_ERROR, details=str(problema))

        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(problema)}
        )

    log_audit(
        "organization.updated", request=request, status=STATUS_SUCCESS,
        organization_id=None,
        details={"organization": organizacion["name"],
                 "plan": organizacion["plan"],
                 "subscription_status":
                     organizacion["subscription_status"],
                 "deployment_type": organizacion["deployment_type"],
                 "active": organizacion["active"],
                 "scope": "platform"}
    )

    return {"status": "ok", "organization": organizacion}


# ==============================
# CREDENCIALES DE ALTA DE AGENTS
# ==============================
#
# Cada organizacion tiene las suyas. Un Owner administra las de su
# empresa; el operador de la plataforma, las de cualquiera.

def _organizacion_de_credenciales(request, organization_id=None):
    """
    Organizacion sobre la que se van a administrar credenciales.

    Devuelve (organizacion, None) o (None, respuesta de rechazo). Un
    Owner solo puede con la suya, aunque pida otra por parametro.
    """

    usuario, error = require_owner(request)

    if error:
        return None, error

    if is_platform_owner(usuario):

        if organization_id is None:
            return None, JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": (
                        "Indica la organizacion sobre la que actuar"
                    )
                }
            )

        if organizations.get_organization(organization_id) is None:
            return None, JSONResponse(
                status_code=404,
                content={"status": "error",
                         "message": "La organizacion no existe"}
            )

        return organization_id, None

    propia = organization_of(usuario)

    # Pedir otra organizacion no la concede: se trabaja siempre sobre la
    # propia, y pedir una ajena se rechaza en vez de ignorarse en
    # silencio.
    if organization_id is not None and organization_id != propia:
        return None, _denegado(
            "Solo puedes administrar las credenciales de tu organizacion"
        )

    return propia, None


@app.get("/api/agent/version")
def agent_version(x_agent_token: str = Header(default=None)):
    """
    Version publicada del Agent, para que un Agent sepa si actualizar.

    Se autentica con el token INDIVIDUAL del equipo, igual que el
    latido: es el Agent quien pregunta, no una persona desde el panel.
    Se exige token para no publicar a cualquiera que pase que version
    corre en los equipos administrados.

    Lo que se devuelve es el manifiesto tal cual se publico, con su
    firma. El servidor no firma nada ni comprueba la firma: eso lo
    hace el Agent con su clave publica, y asi tiene que ser, porque
    una comprobacion hecha aqui no protegeria de este mismo servidor.
    """

    if get_device_id_for_token(x_agent_token) is None:
        return _agent_unauthorized()

    try:
        manifiesto, firma = agent_releases.load_published()

    except agent_releases.ReleaseNotPublished:

        # No publicar nada es normal, no un error: los Agents siguen
        # con la version que tengan.
        return {"status": "ok", "manifest": None, "signature": None}

    return {
        "status": "ok",
        "manifest": manifiesto,
        "signature": firma
    }


@app.get("/api/agent/package")
def agent_package_download(
    version: str = None,
    x_agent_token: str = Header(default=None)
):
    """
    Paquete de actualizacion del Agent.

    La version que se sirve es SIEMPRE la del manifiesto publicado, no
    la que pida el cliente: el parametro solo se usa para avisar de
    que se pide otra cosa. Construir la ruta con texto del cliente
    seria dejarle elegir que archivo se lee del servidor.

    Que vaya firmado es lo que permite servirlo sin mas ceremonia: aun
    si alguien cambiara el archivo en disco, el Agent lo rechazaria al
    comprobar la firma del manifiesto y el hash.
    """

    if get_device_id_for_token(x_agent_token) is None:
        return _agent_unauthorized()

    try:
        manifiesto, _ = agent_releases.load_published()

        publicada = manifiesto.get("version")

        if version is not None and version != publicada:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error",
                    "message": (
                        f"La version publicada es {publicada}"
                    )
                }
            )

        contenido = agent_releases.load_package(publicada)

    except agent_releases.ReleaseNotPublished as problema:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": str(problema)}
        )

    return Response(
        content=contenido,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="agent-{publicada}.zip"'
        }
    )


@app.get("/api/agent-package")
def agent_package(request: Request, organization_id: int = None):
    """
    Descarga del instalador del Agent para Windows.

    La organizacion sale SIEMPRE de la sesion. El organization_id del
    parametro solo lo puede usar el operador de plataforma, que no
    tiene organizacion propia; para un Owner se ignora por completo,
    asi que pedir el paquete de otra empresa no lleva a ninguna parte.

    Lo que se entrega es un ZIP armado en memoria desde una lista fija
    de archivos (ver backend/packaging.py). No contiene credenciales:
    solo el Agent, sus dependencias, el instalador y la direccion del
    servidor.
    """

    organizacion, error = _organizacion_de_credenciales(
        request, organization_id
    )

    if error:
        return error

    try:
        contenido = packaging.build_agent_package(organizacion)

    except packaging.PackagingError as problema:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": str(problema)}
        )

    # Solo se anota QUE se descargo y por quien. El paquete no lleva
    # secretos, pero la descarga es el principio de un alta y conviene
    # que quede rastro.
    log_audit(
        "agent_package.download", request=request,
        status=STATUS_SUCCESS,
        organization_id=organizacion,
        details={"bytes": len(contenido)}
    )

    # El nombre es una constante del servidor: nunca se construye con
    # el nombre de la organizacion ni con nada que venga del usuario.
    return Response(
        content=contenido,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="{packaging.PACKAGE_FILENAME}"'
        }
    )


@app.get("/api/recordings/{recording_id}/intervals")
def recording_intervals(recording_id: int, request: Request):
    """
    Como se reparte un archivo entre las modalidades.

    Exige el mismo permiso que ver la grabacion: los tramos dicen
    cuando alguien estuvo mirando una pantalla ajena, que no es menos
    sensible que el video.

    Para las grabaciones historicas no hay tramos, y se responde
    'sin_datos' en vez de atribuirlas a una modalidad inventada.
    """

    usuario, error = require_permission(request, "recordings.view")

    if error:
        return error

    grabacion = get_recording(recording_id)

    if grabacion is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Grabación no encontrada"}
        )

    # El aislamiento entre empresas: una grabacion de otra
    # organizacion no se mira, ni siquiera su reparto por modalidad.
    if not same_organization(
        usuario, organization_of_device(grabacion["device_id"])
    ):
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Grabación no encontrada"}
        )

    reparto = tramos_de_grabacion(recording_id)

    if reparto is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "Grabación no encontrada"}
        )

    return {"status": "ok", **reparto}


@app.get("/api/agent-installer/info")
def agent_installer_info(request: Request, organization_id: int = None):
    """
    Que version instalaria el .exe, y cuanto pesa.

    Se consulta ANTES de descargar: bajar 76 MB sin saber que version
    traen es justo lo que lleva a instalar la equivocada.

    La version sale del MANIFIESTO FIRMADO, no del nombre del
    archivo: es el unico dato que no se puede cambiar sin la clave
    privada.
    """

    _, error = _organizacion_de_credenciales(request, organization_id)

    if error:
        return error

    try:
        datos = agent_releases.installer_info()

    except agent_releases.InstallerNotPublished:
        return {"status": "ok", "available": False, "installer": None}

    except agent_releases.ReleaseNotPublished:
        # Hay .exe pero no hay version firmada: no se ofrece.
        return {"status": "ok", "available": False, "installer": None}

    return {"status": "ok", "available": True, "installer": datos}


@app.get("/api/agent-installer")
def agent_installer_download(request: Request, organization_id: int = None):
    """
    El instalador de Windows, para descargarlo desde el panel.

    Misma autenticacion y misma autorizacion que el paquete ZIP: se
    reutiliza _organizacion_de_credenciales y no se crea ninguna ruta
    publica. Un .exe que cualquiera pueda bajar es un .exe que
    cualquiera puede estudiar para atacar a los equipos.

    El archivo NO lleva credenciales: la de alta se teclea al
    instalar. Lo que lleva es el Agent, el instalador y el interprete.
    """

    organizacion, error = _organizacion_de_credenciales(
        request, organization_id
    )

    if error:
        return error

    try:
        datos = agent_releases.installer_info()

    except (agent_releases.InstallerNotPublished,
            agent_releases.ReleaseNotPublished) as problema:

        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": str(problema)}
        )

    log_audit(
        "agent_installer.download", request=request,
        status=STATUS_SUCCESS,
        organization_id=organizacion,
        details={"version": datos["version"], "bytes": datos["size"]}
    )

    # FileResponse transmite por bloques: 76 MB no caben comodos en
    # memoria, y menos con varias descargas a la vez. Ademas pone
    # Content-Length y Accept-Ranges, asi que el navegador sabe
    # cuanto espera y puede reanudar una descarga cortada.
    #
    # application/octet-stream y no el tipo propio de los ejecutables
    # de Windows: es el tipo estandar para "esto se descarga, no se
    # interpreta". Un tipo especifico de ejecutable invita a que
    # proxies, antivirus de correo y extensiones le den un trato
    # distinto, y aqui no aporta nada: el nombre del archivo ya va en
    # Content-Disposition.
    return FileResponse(
        agent_releases.installer_path(),
        media_type="application/octet-stream",
        filename=agent_releases.INSTALLER_NAME
    )


@app.get("/api/enrollment-tokens")
def enrollment_tokens_list(request: Request, organization_id: int = None):
    """Credenciales de alta, sin exponer nunca su valor."""

    organizacion, error = _organizacion_de_credenciales(
        request, organization_id
    )

    if error:
        return error

    return {
        "status": "ok",
        "organization_id": organizacion,
        "tokens": enrollment.list_tokens(organizacion)
    }


@app.post("/api/enrollment-tokens")
def enrollment_tokens_create(data: dict, request: Request):
    """
    Crea una credencial de alta.

    El valor en claro se devuelve UNA sola vez, aqui: el servidor guarda
    unicamente su SHA-256 y no puede volver a mostrarlo.
    """

    organizacion, error = _organizacion_de_credenciales(
        request, data.get("organization_id")
    )

    if error:
        return error

    try:
        ficha, valor = enrollment.create_token(
            organizacion,
            label=data.get("label"),
            expires_in_days=data.get("expires_in_days")
        )

    except enrollment.EnrollmentError as problema:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(problema)}
        )

    log_audit(
        "enrollment.token_created", request=request,
        status=STATUS_SUCCESS,
        organization_id=organizacion,
        details={"token_id": ficha["id"], "label": ficha["label"]}
    )

    return {
        "status": "ok",
        "token": ficha,
        # Unica vez que viaja en claro
        "value": valor,
        "message": (
            "Guarda este valor ahora: no se puede volver a consultar."
        )
    }


@app.delete("/api/enrollment-tokens/{token_id}")
def enrollment_tokens_revoke(token_id: int, request: Request):
    """Revoca una credencial. No se borra: queda su rastro de uso."""

    ficha = enrollment.get_token(token_id)

    if ficha is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error",
                     "message": "La credencial no existe"}
        )

    organizacion, error = _organizacion_de_credenciales(
        request, ficha["organization_id"]
    )

    if error:
        return error

    enrollment.revoke_token(token_id)

    log_audit(
        "enrollment.token_revoked", request=request,
        status=STATUS_SUCCESS,
        organization_id=organizacion,
        details={"token_id": token_id}
    )

    return {"status": "ok", "token": enrollment.get_token(token_id)}


# ==============================
# USUARIOS, ROLES Y PERMISOS
# ==============================

def usuario_del_mismo_ambito(actor, username):
    """
    Devuelve el usuario objetivo si el actor puede administrarlo.

    (usuario, None) si puede; (None, respuesta) si no. Un Owner solo
    administra a los de su empresa; el operador de la plataforma, a
    cualquiera. Se responde 404 y no 403 para no confirmar que ese
    nombre existe en otra organizacion.
    """

    objetivo = get_user(username)

    if objetivo is None:
        return None, JSONResponse(
            status_code=404,
            content={"status": "error", "message": "El usuario no existe"}
        )

    if is_platform_owner(actor):
        return objetivo, None

    if objetivo.get("organization_id") != organization_of(actor):
        return None, JSONResponse(
            status_code=404,
            content={"status": "error", "message": "El usuario no existe"}
        )

    return objetivo, None


def _usuario_o_error(funcion, *args, **kwargs):
    """Ejecuta una operacion de usuarios y traduce su rechazo a un 400."""

    try:
        return funcion(*args, **kwargs), None

    except UserError as error:
        return None, JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(error)}
        )


@app.get("/api/users")
def users_list(request: Request):
    """Usuarios del panel. Solo el owner, y solo los de su organizacion."""

    usuario, error = require_owner(request)

    if error:
        return error

    # El Owner de una empresa ve a los suyos. El operador de la
    # plataforma los ve todos, que es lo que necesita para dar soporte.
    ambito = None if is_platform_owner(usuario) else organization_of(usuario)

    return {
        "status": "ok",
        "users": list_users(organization_id=ambito),
        "permissions": list(PERMISSIONS),
        "roles": [ROLE_OWNER, ROLE_SUBADMIN]
    }


@app.get("/api/users/me")
def users_me(request: Request):
    """
    Quien soy y que puedo hacer.

    El panel lo usa para ocultar lo que no procede. Es una comodidad: cada
    endpoint vuelve a comprobar el permiso por su cuenta.
    """

    usuario = current_user(request)

    if usuario is None:
        return JSONResponse(
            status_code=401,
            content={"status": "error", "message": "No autenticado"}
        )

    organizacion = (
        organizations.get_organization(organization_of(usuario))
        if organization_of(usuario) else None
    )

    return {
        "status": "ok",
        "username": usuario["username"],
        "role": usuario["role"],
        "email": usuario["email"],
        "is_owner": is_owner(usuario),
        "is_platform_owner": is_platform_owner(usuario),
        "organization": (
            {
                "id": organizacion["id"],
                "name": organizacion["name"],
                "plan": organizacion["plan"],
                "plan_label": organizacion["plan_label"],
                "subscription_status": organizacion["subscription_status"],
                "billing_mode": organizacion["billing_mode"],
                "usable": organizacion["usable"],
                "suspended_at": organizacion["suspended_at"],
                # Solo lo de SU organizacion: nada de la instalacion ni
                # de las demas empresas.
                "deployment_type": organizacion["deployment_type"],
                "server_url": organizacion["server_url"],
                # Solo cuando hace falta: si la organizacion opera con
                # normalidad no hay nada que explicar.
                "access_message": (
                    None if organizacion["usable"]
                    else organizations.access_message(organizacion)
                )
            }
            if organizacion else None
        ),
        "permissions": (
            list(PERMISSIONS) if is_owner(usuario)
            else usuario.get("permissions", [])
        )
    }


@app.post("/api/users")
def users_create(data: dict, request: Request):
    """Alta de un subadmin. Solo el owner; el rol owner no se puede asignar."""

    actor, error = require_owner(request)

    if error:
        return error

    # La organizacion sale de la sesion de quien crea, nunca del cuerpo:
    # asi un Owner no puede colocar usuarios dentro de otra empresa.
    destino = organization_of(actor)

    if destino is None:
        return _denegado(
            "El operador de la plataforma debe crear los usuarios dentro "
            "de una organizacion concreta"
        )

    try:
        organizations.check_limit(destino, "users")

    except organizations.OrganizationError as limite:

        log_audit("user.created", request=request, status=STATUS_ERROR,
                  details=str(limite))

        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": str(limite)}
        )

    usuario, error = _usuario_o_error(
        create_user,
        username=data.get("username"),
        password=data.get("password"),
        email=data.get("email"),
        # El rol NO se toma del cuerpo: un alta nunca puede fabricar un
        # segundo control total.
        role=ROLE_SUBADMIN,
        permissions=data.get("permissions"),
        active=bool(data.get("active", True)),
        organization_id=destino
    )

    if error:
        log_audit("user.created", request=request, status=STATUS_ERROR,
                  details="Alta rechazada")
        return error

    log_audit(
        "user.created", request=request, status=STATUS_SUCCESS,
        details={"username": usuario["username"],
                 "role": usuario["role"],
                 "permissions": len(usuario.get("permissions", []))}
    )

    return {"status": "ok", "user": usuario}


@app.post("/api/users/{username}/permissions")
def users_permissions(username: str, data: dict, request: Request):

    actor, error = require_owner(request)

    if error:
        return error

    # Solo se administra a quien pertenece a la organizacion del actor
    _, fuera = usuario_del_mismo_ambito(actor, username)

    if fuera:
        return fuera

    usuario, error = _usuario_o_error(
        set_permissions, username, data.get("permissions")
    )

    if error:
        log_audit("user.permissions_changed", request=request,
                  status=STATUS_ERROR,
                  details={"username": username, "error": "rechazado"})
        return error

    log_audit(
        "user.permissions_changed", request=request, status=STATUS_SUCCESS,
        details={"username": usuario["username"],
                 "permissions": usuario.get("permissions", [])}
    )

    return {"status": "ok", "user": usuario}


@app.post("/api/users/{username}/active")
def users_active(username: str, data: dict, request: Request):

    actor, error = require_owner(request)

    if error:
        return error

    # Solo se administra a quien pertenece a la organizacion del actor
    _, fuera = usuario_del_mismo_ambito(actor, username)

    if fuera:
        return fuera

    activo = bool(data.get("active"))

    usuario, error = _usuario_o_error(set_active, username, activo)

    if error:
        log_audit(
            "user.enabled" if activo else "user.disabled",
            request=request, status=STATUS_ERROR,
            details={"username": username, "error": "rechazado"}
        )
        return error

    log_audit(
        "user.enabled" if activo else "user.disabled",
        request=request, status=STATUS_SUCCESS,
        details={"username": usuario["username"]}
    )

    return {"status": "ok", "user": usuario}


@app.post("/api/users/{username}/email")
def users_email(username: str, data: dict, request: Request):

    actor, error = require_owner(request)

    if error:
        return error

    # Solo se administra a quien pertenece a la organizacion del actor
    _, fuera = usuario_del_mismo_ambito(actor, username)

    if fuera:
        return fuera

    usuario, error = _usuario_o_error(set_email, username, data.get("email"))

    if error:
        return error

    log_audit("user.updated", request=request, status=STATUS_SUCCESS,
              details={"username": usuario["username"], "campo": "email"})

    return {"status": "ok", "user": usuario}


@app.post("/api/users/{username}/password")
def users_reset_password(username: str, data: dict, request: Request):
    """
    El owner restablece la contrasena de un subadmin.

    Accion administrativa distinta del cambio propio: aqui no se pide la
    contrasena anterior, porque el owner no la conoce. Cierra todas las
    sesiones de ese usuario.
    """

    actor, error = require_owner(request)

    if error:
        return error

    objetivo, fuera = usuario_del_mismo_ambito(actor, username)

    if fuera:
        return fuera

    if objetivo["role"] == ROLE_OWNER and objetivo["username"] != actor["username"]:
        return _denegado(
            "La contrasena de otro propietario no se restablece desde aqui"
        )

    problema = validate_new_password(data.get("new_password"))

    if problema:
        log_audit("user.password_reset", request=request, status=STATUS_ERROR,
                  details={"username": username, "error": problema})

        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": problema}
        )

    set_user_password(objetivo["username"], data.get("new_password"))

    log_audit(
        "user.password_reset", request=request, status=STATUS_SUCCESS,
        details={"username": objetivo["username"],
                 "detalle": "Restablecida por el propietario; "
                            "se cerraron sus sesiones"}
    )

    return {"status": "ok",
            "message": "Contrasena restablecida. "
                       "Las sesiones de ese usuario se han cerrado."}


@app.delete("/api/users/{username}")
def users_delete(username: str, request: Request):

    actor, error = require_owner(request)

    if error:
        return error

    # Solo se administra a quien pertenece a la organizacion del actor
    _, fuera = usuario_del_mismo_ambito(actor, username)

    if fuera:
        return fuera

    _, error = _usuario_o_error(delete_user, username)

    if error:
        log_audit("user.deleted", request=request, status=STATUS_ERROR,
                  details={"username": username, "error": "rechazado"})
        return error

    log_audit("user.deleted", request=request, status=STATUS_SUCCESS,
              details={"username": username})

    return {"status": "ok"}


# ==============================
# CONSULTA DE AUDITORÍA (solo lectura)
# ==============================

@app.get("/api/audit")
def audit_list(
    request: Request,
    limit: int = 100,
    device_id: str = None,
    action: str = None,
    offset: int = 0
):
    """
    Últimos registros de auditoría, de más reciente a más antiguo.

    Solo lectura: no existe ningún endpoint para crear, modificar ni borrar
    registros. Un historial que se puede editar desde fuera no sirve de nada.

    Protegido por el middleware de sesion, como el resto de /api/.
    Reservado al owner: el historial dice quien hizo que, y no es algo que
    deba ver cualquiera que tenga una sesion abierta.

    Y acotado a su organizacion: un Owner ve lo que ha pasado en SU
    empresa. Solo el operador de la plataforma ve el historial completo,
    incluidas las acciones de plataforma, que no pertenecen a ninguna.
    """

    usuario, error = require_owner(request)

    if error:
        return error

    ambito = None if is_platform_owner(usuario) else organization_of(usuario)

    if ambito is None and not is_platform_owner(usuario):
        # Un usuario de organizacion sin organizacion no deberia existir;
        # si ocurre, no se le ensena nada en vez de ensenarselo todo.
        return {"status": "ok", "total": 0, "count": 0, "records": []}

    registros = list_audit(
        limit=limit,
        device_id=device_id,
        action=action,
        offset=offset,
        organization_id=ambito
    )

    return {
        "status": "ok",
        "total": count_audit(
            device_id=device_id, action=action, organization_id=ambito
        ),
        "count": len(registros),
        "records": registros
    }


app.mount(
    "/frontend",
    StaticFiles(
        directory="frontend"
    ),
    name="frontend"
)


@app.get("/")
def root():
    """
    La entrada publica: la presentacion de VEYRA.

    Antes aqui salia directamente el panel. Se cambia porque quien
    llega sin conocer el producto necesita saber que es antes de que
    le pidan unas credenciales.

    El panel NO se mueve de sitio para nadie que ya lo use: sigue en
    /frontend/index.html, que es exactamente la direccion que llevan
    los enlaces de recuperacion por correo, y ademas responde en
    /panel. Esta ruta no cambia ninguna API.
    """

    return FileResponse(
        "frontend/inicio.html"
    )


@app.get("/panel")
def panel():
    """El panel de administracion."""

    return FileResponse(
        "frontend/index.html"
    )


@app.get("/inicio")
def inicio():
    """Alias de la pagina publica, para enlazarla con nombre propio."""

    return FileResponse(
        "frontend/inicio.html"
    )