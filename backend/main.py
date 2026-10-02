import asyncio
import base64
import json
import uuid

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from backend.database import init_db, get_connection
from backend.models import DeviceRegister, DeviceHeartbeat
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
    mark_all_alerts_read
)
from backend.settings import (
    get_settings,
    get_retention_days,
    save_settings,
    UnknownSettingError
)
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
    verify_agent_token,
    require_security_config,
    TOKEN_HOURS
)
from backend.recordings import (
    add_recording,
    add_invalid_recording,
    quarantine_recording,
    RecordingAlreadyExists,
    cleanup_orphan_parts,
    PART_SUFFIX,
    list_recordings,
    query_recordings,
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
    device_exists
)
from backend.media import validate_recording
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
    QUERY_TIMEOUT_SECONDS
)
from backend.audit import (
    log_audit,
    list_audit,
    count_audit,
    get_request_context,
    STATUS_REQUESTED,
    STATUS_SUCCESS,
    STATUS_ERROR
)
from backend.users import (
    ensure_owner_migrated,
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
        # La subida de grabaciones la hace el Agent con su AGENT_TOKEN,
        # no con sesión de panel; se valida dentro del endpoint.
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

    return await call_next(request)


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


@app.on_event("startup")
def startup():
    # init_db PRIMERO: desde G4a la comprobacion de la contrasena por defecto
    # mira el hash vigente en auth_state, que no existe hasta crear las
    # tablas. Si la configuracion es invalida, el arranque falla igual un
    # momento despues; crear tablas vacias no cambia nada.
    init_db()
    # La cuenta unica anterior pasa a ser el owner. Idempotente: si ya hay
    # usuarios, no hace nada.
    ensure_owner_migrated()
    # Exige AUTH_SECRET_KEY y AGENT_TOKEN en el .env; si faltan, el arranque falla
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
            dias = get_retention_days()

            result = await loop.run_in_executor(None, apply_retention, dias)

            if result["deleted"] or result["skipped_traversal"]:
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
        # puede enrolar aunque presente el AGENT_TOKEN correcto.
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

    # Agent NUEVO: el AGENT_TOKEN compartido sirve SOLO para el alta.
    if not verify_agent_token(x_agent_token):

        # Solo se anota la IP y la hora: nunca el token presentado.
        register_failure(client_ip, scope="enroll")

        return _agent_unauthorized()

    device_id, agent_token = enroll_device(
        data.hostname,
        data.operating_system,
        data.ip_address
    )

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
    # Token individual. El AGENT_TOKEN compartido ya no vale aquí: solo
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

    _, error = require_permission(request, "devices.view")

    if error:
        return error

    # Detecta cambios de estado antes de devolver la lista
    detect_alerts()
    return get_devices()


@app.get("/api/alerts")
def alerts(request: Request):

    _, error = require_permission(request, "dashboard.view")

    if error:
        return error

    detect_alerts()
    return get_alerts()


@app.post("/api/alerts/{alert_id}/read")
def alert_read(alert_id: int):
    return mark_alert_read(alert_id)


@app.post("/api/alerts/read-all")
def alerts_read_all():
    return mark_all_alerts_read()


@app.get("/api/settings")
def settings(request: Request):

    _, error = require_permission(request, "settings.view")

    if error:
        return error

    return get_settings()


@app.post("/api/settings")
async def update_settings(data: dict, request: Request):
    """
    Guarda ajustes. Solo las claves de la lista blanca de settings.py.

    Una clave desconocida devuelve 400 y NO escribe nada: este endpoint no
    puede usarse para colar estado de autenticacion en la base.
    """

    _, error = require_permission(request, "settings.edit")

    if error:
        return error

    anterior = get_retention_days()

    try:
        resultado = save_settings(data)

    except UnknownSettingError as error:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(error)}
        )

    # Si han cambiado los dias de retencion, los Agents conectados deben
    # enterarse ya; si no, seguirian aplicando el periodo anterior hasta
    # su proxima reconexion.
    if get_retention_days() != anterior:
        await broadcast_retention_policy()

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
            try:
                await websocket.send_text(
                    "set_schedule:"
                    + json.dumps(recording_schedule.get_schedule(device_id))
                )
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


            elif message.startswith("recording_status:"):

                try:
                    latest_recording_status[device_id] = json.loads(
                        message.split(":", 1)[1]
                    )
                except json.JSONDecodeError:
                    pass


            elif message == "pong":

                print(
                    f"Pong received from {device_id}"
                )

    except WebSocketDisconnect:

        if device_id:

            # Quien esté esperando una consulta recibe el error ya, en vez
            # de agotar el tiempo de espera para saber algo que ya se sabe.
            fail_device_queries(device_id)

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

            latest_recording_status.pop(
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


@app.post("/api/devices/{device_id}/screen")
async def request_screen(device_id: str, request: Request):

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

    await websocket.send_text(
        "start_screen_stream"
    )

    return {
        "status": "sent",
        "device_id": device_id
    }


@app.get("/api/devices/{device_id}/screen")
def get_screen(device_id: str):

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
async def screen_stream(device_id: str):

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
async def mouse_move(device_id: str, data: dict):

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
async def mouse_click(device_id: str, data: dict):

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
async def mouse_down(device_id: str, data: dict):

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
async def mouse_up(device_id: str, data: dict):

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

# Menor que el limite de 1 MB por mensaje de la libreria websockets del Agent
FILE_CHUNK_SIZE = 256 * 1024

file_transfers = {}
file_transfer_locks = {}


class FileTransferError(Exception):

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def file_transfer_error(status_code, message):

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "message": message
        }
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

DOWNLOADS_BASE = r"C:\RemoteAdmin\Downloads"

file_downloads = {}


def path_within_downloads(path):

    # Validacion ligera en backend; el Agent hace la validacion definitiva
    if not path or ".." in path.replace("/", "\\").split("\\"):
        return False

    base = _os.path.normpath(DOWNLOADS_BASE).lower()
    target = _os.path.normpath(path).lower()

    return target == base or target.startswith(base + "\\")


def feed_download_chunk(chunk):

    transfer_id = chunk[:32].decode("ascii", "replace")

    download = file_downloads.get(transfer_id)

    if download:
        download["queue"].put_nowait(chunk[32:])


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

    auditar(STATUS_REQUESTED)

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:
        auditar(STATUS_ERROR, {"error": "Agent no conectado"})
        return file_transfer_error(404, "Agent is not connected")

    if not path_within_downloads(path):
        auditar(STATUS_ERROR, {"error": "Ruta fuera de la carpeta permitida"})
        return file_transfer_error(403, "Solo se permiten archivos dentro de C:\\RemoteAdmin\\Downloads")

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
async def keyboard_input(device_id: str, data: dict):

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

    # Token individual del Agent (no sesión de panel, no AGENT_TOKEN).
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

    # Idempotencia: si el Agent reintenta subir el mismo segmento, no se duplica
    existing_id = find_recording_by_path(device_id, rel_path)
    if existing_id is not None:
        return {
            "status": "stored",
            "id": existing_id,
            "duplicate": True,
            "path": rel_path
        }

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

    try:

        recording_id = add_recording(
            device_id=device_id,
            path=rel_path,
            started_at=started_at or when.isoformat(),
            ended_at=ended_at,
            duration_sec=duration_sec,
            size_bytes=received
        )

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


@app.get("/api/recordings")
def recordings_list(request: Request, device_id: str = None,
                    start: str = None, end: str = None):

    _, error = require_permission(request, "recordings.view")

    if error:
        return error


    # Protegido por sesión (el middleware exige token en /api/* salvo rutas abiertas).
    # device_id se usa solo como filtro parametrizado (a prueba de inyección).
    if device_id is not None and not device_id.strip():
        device_id = None

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
        "days": get_retention_days(),
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


async def broadcast_retention_policy():
    """
    Reenvia la politica a todos los Agents conectados.

    Se usa al cambiar los dias de retencion desde el panel: afecta a
    todos, no solo al equipo que se este mirando.
    """

    for device_id in list(connected_agents):
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

    entregado = False

    websocket = connected_agents.get(device_id)

    if websocket:
        try:
            await websocket.send_text("set_schedule:" + json.dumps(horario))
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


# ==============================
# USUARIOS, ROLES Y PERMISOS
# ==============================

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
    """Usuarios del panel. Solo el owner."""

    _, error = require_owner(request)

    if error:
        return error

    return {
        "status": "ok",
        "users": list_users(),
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

    return {
        "status": "ok",
        "username": usuario["username"],
        "role": usuario["role"],
        "email": usuario["email"],
        "is_owner": is_owner(usuario),
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

    usuario, error = _usuario_o_error(
        create_user,
        username=data.get("username"),
        password=data.get("password"),
        email=data.get("email"),
        # El rol NO se toma del cuerpo: un alta nunca puede fabricar un
        # segundo control total.
        role=ROLE_SUBADMIN,
        permissions=data.get("permissions"),
        active=bool(data.get("active", True))
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

    objetivo = get_user(username)

    if objetivo is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "El usuario no existe"}
        )

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
    """

    _, error = require_owner(request)

    if error:
        return error

    registros = list_audit(
        limit=limit,
        device_id=device_id,
        action=action,
        offset=offset
    )

    return {
        "status": "ok",
        "total": count_audit(device_id=device_id, action=action),
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

    return FileResponse(
        "frontend/index.html"
    )