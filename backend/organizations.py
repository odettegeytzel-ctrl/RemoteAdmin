"""
Organizaciones: la empresa cliente es la unidad de aislamiento.

RemoteAdmin pasa de administrar un parque de equipos a administrar
varios, uno por empresa, sin que ninguna vea nada de las demas. Hay dos
niveles bien separados:

  - PLATAFORMA: quien opera RemoteAdmin. Crea organizaciones, las activa
    y las suspende. No pertenece a ninguna empresa cliente.

  - ORGANIZACION: la empresa cliente. Dentro viven sus usuarios, sus
    equipos, sus grabaciones y su configuracion. Su Owner administra lo
    suyo y nada mas.

Lo que NO hay aqui: cobros, pasarelas de pago ni licencias. El plan y el
estado de suscripcion se guardan y se consultan, pero nadie cobra nada.
La estructura esta preparada para que eso se anada despues sin rehacer
el modelo.

Tres conceptos que se mantienen separados a proposito:

  1. PLAN         que puede hacer la organizacion (limites, capacidades)
  2. ESTADO       si puede usarse ahora mismo (activa, suspendida...)
  3. PERMISOS     que puede hacer cada usuario dentro de ella

Mezclarlos es el error clasico: acabas con un "usuario premium" en vez
de un usuario con permisos dentro de una empresa con un plan.
"""

import re
import sqlite3

from datetime import datetime, timezone

from backend.database import get_connection


# ==============================
# PLANES
# ==============================
#
# Catalogo en codigo, no en la base: los planes son decisiones de
# producto, cambian poco y conviene revisarlos en el control de
# versiones. Lo que si vive en la base es QUE plan tiene cada
# organizacion y los limites concretos que se le hayan ajustado.
#
# 0 o None en un limite significa "sin limite".

PLAN_FREE = "free"
PLAN_BASIC = "basic"
PLAN_PRO = "pro"
PLAN_ENTERPRISE = "enterprise"

PLANS = {

    PLAN_FREE: {
        "label": "Free",
        "max_devices": 2,
        "max_users": 2,
        "storage_mb": 1024,
        "features": ("recordings.store",)
    },

    PLAN_BASIC: {
        "label": "Basic",
        "max_devices": 10,
        "max_users": 5,
        "storage_mb": 10240,
        "features": ("recordings.store", "remote.actions")
    },

    PLAN_PRO: {
        "label": "Pro",
        "max_devices": 50,
        "max_users": 20,
        "storage_mb": 102400,
        "features": ("recordings.store", "remote.actions", "scheduling")
    },

    PLAN_ENTERPRISE: {
        "label": "Enterprise",
        # Sin limite
        "max_devices": 0,
        "max_users": 0,
        "storage_mb": 0,
        "features": ("recordings.store", "remote.actions", "scheduling",
                     "self_hosted")
    }
}

PLAN_NAMES = tuple(PLANS)


# ==============================
# ESTADO DE SUSCRIPCION
# ==============================
#
# Independiente del plan: una organizacion con plan Pro puede estar
# suspendida, y una con plan Free puede estar perfectamente activa.

STATUS_TRIAL = "trial"
STATUS_ACTIVE = "active"
STATUS_PAST_DUE = "past_due"
STATUS_SUSPENDED = "suspended"
STATUS_CANCELLED = "cancelled"

SUBSCRIPTION_STATUSES = (
    STATUS_TRIAL, STATUS_ACTIVE, STATUS_PAST_DUE,
    STATUS_SUSPENDED, STATUS_CANCELLED
)

# Estados en los que la organizacion puede trabajar con normalidad.
#
# past_due entra aqui a proposito: un pago atrasado no es motivo para
# dejar a una empresa sin acceso a sus equipos de un dia para otro. El
# dia que haya cobros, ese estado servira para avisar; cortar el acceso
# es decision de suspenderla.
USABLE_STATUSES = (STATUS_TRIAL, STATUS_ACTIVE, STATUS_PAST_DUE)


# Modalidad de facturacion. 'courtesy' permite que una organizacion use
# RemoteAdmin sin pagar: es lo que hace falta mientras se desarrolla, y
# tambien para cuentas internas o de demostracion.
BILLING_STANDARD = "standard"
BILLING_COURTESY = "courtesy"

BILLING_MODES = (BILLING_STANDARD, BILLING_COURTESY)


# ==============================
# MODALIDAD DE DESPLIEGUE
# ==============================
#
# Donde corre RemoteAdmin para esta organizacion:
#
#   cloud        en la infraestructura del proveedor, compartida con
#                otras empresas y aislada por organizacion
#   self_hosted  en infraestructura del propio cliente
#
# Es una clasificacion administrativa. El software es el mismo y no se
# bifurca: cambiarla no mueve datos, no toca equipos y no reconfigura
# nada.

DEPLOYMENT_CLOUD = "cloud"
DEPLOYMENT_SELF_HOSTED = "self_hosted"

DEPLOYMENT_TYPES = (DEPLOYMENT_CLOUD, DEPLOYMENT_SELF_HOSTED)


# Longitud maxima de la direccion informativa del servidor
MAX_SERVER_URL_LENGTH = 300


def validate_server_url(valor):
    """
    Comprueba la direccion informativa del servidor.

    Se exige http o https para que no pueda colarse un 'javascript:' ni
    una ruta de archivo: este texto acaba en la interfaz y en las
    instrucciones que se le dan a un cliente.

    NO es un mecanismo de control: el servidor no la usa para decidir
    nada, no redirige Agents y no toca su configuracion. Un Agent
    apunta a donde diga su propio REMOTEADMIN_SERVER.

    Devuelve la direccion limpia, o None si viene vacia.
    """

    if valor is None:
        return None

    if not isinstance(valor, str):
        raise OrganizationError("La direccion del servidor no es valida")

    direccion = valor.strip().rstrip("/")

    if not direccion:
        return None

    if len(direccion) > MAX_SERVER_URL_LENGTH:
        raise OrganizationError(
            "La direccion del servidor es demasiado larga"
        )

    if not direccion.startswith(("http://", "https://")):
        raise OrganizationError(
            "La direccion del servidor debe empezar por http:// o https://"
        )

    return direccion


# Nombre de la primera organizacion: la instalacion que ya existe.
DEFAULT_ORGANIZATION_NAME = "Plastika"
DEFAULT_ORGANIZATION_SLUG = "plastika"


# Centinela para distinguir "no se indica" de "ponlo a NULL". Con None
# a secas no se podria borrar una direccion ya guardada.
_SIN_CAMBIO = object()


class OrganizationError(ValueError):
    """Operacion sobre organizaciones rechazada, con motivo legible."""


SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def slugify(nombre):
    """
    Identificador corto y estable a partir del nombre.

    Se usa en rutas y en la interfaz, asi que se limita a letras, numeros
    y guiones: nada que pueda confundirse con una ruta o colarse en una
    URL.
    """

    base = (nombre or "").strip().lower()

    base = base.replace("á", "a").replace("é", "e").replace("í", "i")
    base = base.replace("ó", "o").replace("ú", "u").replace("ñ", "n")

    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")

    return base[:40]


def _fila_a_organizacion(fila):

    if fila is None:
        return None

    plan = fila["plan"] if fila["plan"] in PLANS else PLAN_FREE

    return {
        "id": fila["id"],
        "name": fila["name"],
        "slug": fila["slug"],
        "plan": plan,
        "plan_label": PLANS[plan]["label"],
        "subscription_status": fila["subscription_status"],
        "billing_mode": fila["billing_mode"],
        "active": bool(fila["active"]),
        "usable": is_usable(fila),
        "suspended_at": fila["suspended_at"],
        "deployment_type": fila["deployment_type"] or DEPLOYMENT_CLOUD,
        "server_url": fila["server_url"],
        "created_at": fila["created_at"],
        "updated_at": fila["updated_at"],
        "notes": fila["notes"]
    }


# Texto que se le ensena a quien pertenece a una organizacion que no
# puede operar. Describe el estado, no inventa condiciones comerciales.
MENSAJES_SIN_ACCESO = {
    STATUS_SUSPENDED: (
        "Esta organizacion esta suspendida. Sus datos, equipos y "
        "grabaciones se conservan intactos. Ponte en contacto con el "
        "administrador de RemoteAdmin para restablecer el acceso."
    ),
    STATUS_CANCELLED: (
        "Esta organizacion esta cancelada. Sus datos, equipos y "
        "grabaciones se conservan. Ponte en contacto con el "
        "administrador de RemoteAdmin si necesitas recuperarla."
    )
}

MENSAJE_SIN_ACCESO_GENERICO = (
    "Esta organizacion no tiene acceso en este momento. Ponte en "
    "contacto con el administrador de RemoteAdmin."
)


def access_message(organizacion):
    """Explicacion de por que una organizacion no puede operar."""

    if organizacion is None:
        return MENSAJE_SIN_ACCESO_GENERICO

    estado = organizacion.get("subscription_status") \
        if isinstance(organizacion, dict) \
        else organizacion["subscription_status"]

    return MENSAJES_SIN_ACCESO.get(estado, MENSAJE_SIN_ACCESO_GENERICO)


def is_usable(fila):
    """
    True si la organizacion puede operar ahora mismo.

    Hacen falta las dos cosas: estar activa y tener un estado de
    suscripcion que lo permita. Suspender es una decision explicita y se
    nota aqui, sin tocar ni un dato de la empresa.
    """

    if fila is None:
        return False

    activa = bool(fila["active"]) if not isinstance(fila, dict) \
        else bool(fila.get("active"))

    estado = fila["subscription_status"] if not isinstance(fila, dict) \
        else fila.get("subscription_status")

    return activa and estado in USABLE_STATUSES


# ==============================
# CONSULTA
# ==============================

def get_organization(organization_id):

    if not organization_id:
        return None

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT * FROM organizations WHERE id = ?",
            (organization_id,)
        ).fetchone()

    finally:
        connection.close()

    return _fila_a_organizacion(fila)


def get_organization_by_slug(slug):

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT * FROM organizations WHERE slug = ? COLLATE NOCASE",
            (slug,)
        ).fetchone()

    finally:
        connection.close()

    return _fila_a_organizacion(fila)


def list_organizations():
    """Todas las organizaciones, con su uso actual frente a los limites."""

    connection = get_connection()

    try:
        filas = connection.execute(
            "SELECT * FROM organizations ORDER BY name COLLATE NOCASE"
        ).fetchall()

    finally:
        connection.close()

    organizaciones = []

    for fila in filas:

        organizacion = _fila_a_organizacion(fila)
        organizacion["usage"] = organization_usage(organizacion["id"])

        organizaciones.append(organizacion)

    return organizaciones


def organization_usage(organization_id):
    """Cuantos equipos y usuarios tiene ahora mismo."""

    connection = get_connection()

    try:
        # Los equipos dados de baja NO cuentan para el plan: la fila
        # sigue ahi para conservar su historial, pero la plaza queda
        # libre. Si contaran, dar de baja no serviria de nada.
        equipos = connection.execute(
            "SELECT COUNT(*) FROM devices "
            "WHERE organization_id = ? AND decommissioned_at IS NULL",
            (organization_id,)
        ).fetchone()[0]

        usuarios = connection.execute(
            "SELECT COUNT(*) FROM users WHERE organization_id = ?",
            (organization_id,)
        ).fetchone()[0]

        # Almacenamiento REAL de la organizacion: la suma de sus
        # grabaciones, atadas a ella por el equipo al que pertenecen.
        # Antes no se contaba, asi que el storage_mb del plan no
        # significaba nada.
        bytes_usados = connection.execute(
            """
            SELECT COALESCE(SUM(r.size_bytes), 0)
            FROM recordings r
            JOIN devices d ON d.device_id = r.device_id
            WHERE d.organization_id = ?
            """,
            (organization_id,)
        ).fetchone()[0]

    finally:
        connection.close()

    return {
        "devices": equipos,
        "users": usuarios,
        "storage_mb": round((bytes_usados or 0) / (1024 * 1024), 2),
    }


# ==============================
# ALTA Y CAMBIOS
# ==============================

def create_organization(name, plan=PLAN_FREE, billing_mode=BILLING_STANDARD,
                        subscription_status=STATUS_TRIAL, notes=None,
                        deployment_type=DEPLOYMENT_CLOUD, server_url=None):
    """Crea una organizacion. Solo la plataforma deberia llamar aqui."""

    nombre = (name or "").strip()

    if len(nombre) < 2 or len(nombre) > 80:
        raise OrganizationError(
            "El nombre debe tener entre 2 y 80 caracteres"
        )

    if plan not in PLANS:
        raise OrganizationError("Plan desconocido")

    if subscription_status not in SUBSCRIPTION_STATUSES:
        raise OrganizationError("Estado de suscripcion desconocido")

    if billing_mode not in BILLING_MODES:
        raise OrganizationError("Modalidad de facturacion desconocida")

    if deployment_type not in DEPLOYMENT_TYPES:
        raise OrganizationError("Modalidad de despliegue desconocida")

    direccion = validate_server_url(server_url)

    slug = slugify(nombre)

    if not SLUG_PATTERN.match(slug):
        raise OrganizationError(
            "El nombre no produce un identificador valido; "
            "usa letras y numeros"
        )

    ahora = _ahora()

    connection = get_connection()

    try:
        cursor = connection.execute(
            """
            INSERT INTO organizations (
                name, slug, plan, subscription_status, billing_mode,
                active, created_at, updated_at, notes,
                deployment_type, server_url
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (nombre, slug, plan, subscription_status, billing_mode,
             ahora, ahora, notes, deployment_type, direccion)
        )

        connection.commit()

        return get_organization(cursor.lastrowid)

    except sqlite3.IntegrityError:
        raise OrganizationError("Ya existe una organizacion con ese nombre")

    finally:
        connection.close()


def update_organization(organization_id, plan=None, subscription_status=None,
                        billing_mode=None, active=None, notes=None,
                        deployment_type=None, server_url=_SIN_CAMBIO):
    """
    Cambia plan, estado o modalidad. Nunca toca los datos de la empresa.

    Suspender es cambiar un campo: ni se borran equipos, ni grabaciones,
    ni usuarios. Reactivar devuelve el acceso tal y como estaba.
    """

    organizacion = get_organization(organization_id)

    if organizacion is None:
        raise OrganizationError("La organizacion no existe")

    if plan is not None and plan not in PLANS:
        raise OrganizationError("Plan desconocido")

    if subscription_status is not None \
            and subscription_status not in SUBSCRIPTION_STATUSES:
        raise OrganizationError("Estado de suscripcion desconocido")

    if billing_mode is not None and billing_mode not in BILLING_MODES:
        raise OrganizationError("Modalidad de facturacion desconocida")

    if deployment_type is not None \
            and deployment_type not in DEPLOYMENT_TYPES:
        raise OrganizationError("Modalidad de despliegue desconocida")

    campos = []
    valores = []

    for columna, valor in (
        ("plan", plan),
        ("subscription_status", subscription_status),
        ("billing_mode", billing_mode),
        ("notes", notes),
        ("deployment_type", deployment_type)
    ):
        if valor is not None:
            campos.append(f"{columna} = ?")
            valores.append(valor)

    # server_url se trata aparte porque None es un valor legitimo
    # (borrar la direccion), distinto de "no lo cambies".
    if server_url is not _SIN_CAMBIO:
        campos.append("server_url = ?")
        valores.append(validate_server_url(server_url))

    if active is not None:
        campos.append("active = ?")
        valores.append(1 if active else 0)

    if not campos:
        return organizacion

    # Se anota cuando deja de poder operar y se limpia cuando vuelve.
    # Se calcula sobre el estado RESULTANTE, no sobre el que llega: si
    # la llamada solo cambia el plan, el momento de suspension no debe
    # moverse.
    resultante_estado = (
        subscription_status if subscription_status is not None
        else organizacion["subscription_status"]
    )

    resultante_activa = (
        bool(active) if active is not None else organizacion["active"]
    )

    podra_operar = (
        resultante_activa and resultante_estado in USABLE_STATUSES
    )

    if podra_operar:
        campos.append("suspended_at = NULL")

    elif organizacion["suspended_at"] is None:
        campos.append("suspended_at = ?")
        valores.append(_ahora())

    campos.append("updated_at = ?")
    valores.append(_ahora())

    valores.append(organization_id)

    connection = get_connection()

    try:
        connection.execute(
            f"UPDATE organizations SET {', '.join(campos)} WHERE id = ?",
            tuple(valores)
        )
        connection.commit()

    finally:
        connection.close()

    return get_organization(organization_id)


# ==============================
# LIMITES DEL PLAN
# ==============================

def plan_of(organization_id):

    organizacion = get_organization(organization_id)

    if organizacion is None:
        return PLANS[PLAN_FREE]

    return PLANS[organizacion["plan"]]


# ==============================
# EXCEPCIONES POR ORGANIZACION
# ==============================
#
# Viven en organization_settings, la tabla clave/valor que ya existe.
# No se toca el esquema.
#
# Un cliente no puede escribirlas: set_settings() solo admite las
# claves de ALLOWED_SETTINGS y estas no estan ahi. La escritura va
# por set_plan_exception(), que el endpoint reserva al operador de la
# plataforma.

PREFIJO_EXCEPCION = "plan."

EXCEPCIONES = (
    "plan.features_extra",
    "plan.features_denied",
    "plan.limit.devices",
    "plan.limit.users",
    "plan.limit.storage_mb",
    "plan.courtesy_until",
    "plan.courtesy_note",
    "plan.trial_until",
)


def get_plan_exceptions(organization_id):
    """Las excepciones de una organizacion, tal cual estan guardadas."""

    connection = get_connection()

    try:
        filas = connection.execute(
            "SELECT key, value FROM organization_settings "
            "WHERE organization_id = ? AND key LIKE ?",
            (organization_id, PREFIJO_EXCEPCION + "%")
        ).fetchall()

    finally:
        connection.close()

    return {f["key"]: f["value"] for f in filas}


def set_plan_exception(organization_id, clave, valor):
    """
    Concede, cambia o retira una excepcion.

    valor None la retira. Reservado al operador de la plataforma: el
    endpoint lo exige, y esta funcion no se expone por ninguna otra
    via.
    """

    if clave not in EXCEPCIONES:
        raise OrganizationError(f"Excepcion desconocida: {clave}")

    if get_organization(organization_id) is None:
        raise OrganizationError("La organizacion no existe")

    connection = get_connection()

    try:
        if valor is None or str(valor).strip() == "":

            connection.execute(
                "DELETE FROM organization_settings "
                "WHERE organization_id = ? AND key = ?",
                (organization_id, clave)
            )

        else:
            connection.execute(
                "INSERT INTO organization_settings "
                "(organization_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(organization_id, key) "
                "DO UPDATE SET value = excluded.value",
                (organization_id, clave, str(valor).strip())
            )

        connection.commit()

    finally:
        connection.close()

    return True


def _lista(texto):
    """Una cadena separada por comas, como conjunto limpio."""

    if not texto:
        return set()

    return {t.strip() for t in str(texto).split(",") if t.strip()}


def _entero(texto):

    try:
        return int(str(texto).strip())

    except (TypeError, ValueError):
        return None


def _vigente(hasta, ahora=None):
    """
    True si una excepcion con fecha sigue en vigor.

    Sin fecha, no caduca. Con una fecha ilegible se considera
    CADUCADA: ante la duda, no se conceden capacidades de mas.
    """

    if not hasta:
        return True

    from datetime import datetime, timezone

    try:
        limite = datetime.fromisoformat(str(hasta).strip())

    except ValueError:
        return False

    if limite.tzinfo is None:
        limite = limite.replace(tzinfo=timezone.utc)

    momento = ahora or datetime.now(timezone.utc)

    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)

    return momento < limite


def plan_efectivo(organization_id, ahora=None):
    """
    Lo que esta organizacion puede hacer AHORA MISMO.

    El plan base mas lo que se le haya concedido o retirado a ella en
    concreto. Es el unico sitio donde se resuelve esto: has_feature()
    y check_limit() leen de aqui, asi que una excepcion se concede en
    un sitio y vale en todos.

    Las capacidades concedidas caducan con plan.courtesy_until. Las
    retiradas no: quitar algo no deberia deshacerse solo.
    """

    organizacion = get_organization(organization_id)

    if organizacion is None:
        base = PLANS[PLAN_FREE]
        clave_plan = PLAN_FREE
    else:
        clave_plan = organizacion["plan"]
        base = PLANS[clave_plan]

    excepciones = get_plan_exceptions(organization_id)

    capacidades = set(base["features"])

    cortesia_hasta = excepciones.get("plan.courtesy_until")
    cortesia_vigente = _vigente(cortesia_hasta, ahora)

    extra = _lista(excepciones.get("plan.features_extra"))

    if cortesia_vigente:
        capacidades |= extra

    # Lo retirado se aplica siempre y manda sobre lo concedido: si
    # una capacidad aparece en las dos listas, no se tiene.
    retiradas = _lista(excepciones.get("plan.features_denied"))
    capacidades -= retiradas

    def tope(nombre, por_defecto):

        propio = _entero(excepciones.get(f"plan.limit.{nombre}"))

        if propio is None or propio < 0:
            return por_defecto

        return propio

    return {
        "plan": clave_plan,
        "label": base["label"],
        "max_devices": tope("devices", base["max_devices"]),
        "max_users": tope("users", base["max_users"]),
        "storage_mb": tope("storage_mb", base["storage_mb"]),
        "features": frozenset(capacidades),

        # Para poder explicarlo en el panel y en la auditoria
        "features_base": frozenset(base["features"]),
        "features_extra": frozenset(extra if cortesia_vigente else ()),
        "features_denied": frozenset(retiradas),
        "courtesy_until": cortesia_hasta,
        "courtesy_active": bool(extra) and cortesia_vigente,
        "courtesy_note": excepciones.get("plan.courtesy_note"),
        "trial_until": excepciones.get("plan.trial_until"),
    }


# ==============================
# QUE SE USA DE VERDAD
# ==============================
#
# Señales por capacidad. Para cada una, como saber si esta
# CONFIGURADA (hay un ajuste puesto) y si esta USADA (hay constancia
# de una operacion completada).
#
# Las de uso salen de la auditoria, que registra lo que OCURRIO. No
# se usa 'plan.feature_observed': eso son INTENTOS, y basarse en
# ellos permitiria ganarse una capacidad a fuerza de intentarla.

ACCIONES_DE_USO = {
    "scheduling": ("recording.schedule",),
    "remote.actions": (
        "device.lock", "device.logoff", "device.restart",
        "device.shutdown", "remote.session_start",
    ),
    "recordings.store": ("recording.store",),
    # self_hosted no es una operacion: es una forma de desplegar. Su
    # señal es el estado, no la auditoria.
    "self_hosted": (),
}


def capacidades_en_uso(organization_id, desde=None, hasta=None):
    """
    Que capacidades tiene configuradas y cuales ha usado de verdad.

    'desde' y 'hasta' acotan el periodo del uso (ISO).

    HASTA es el importante, y hay que usarlo siempre que esto sirva
    para decidir protecciones:

        Mientras MODO_CAPACIDADES este en "observacion", una funcion
        que el plan no incluye NO se bloquea: la operacion se
        completa y queda registrada como uso de verdad. Es lo
        correcto —observar no debe romper nada— pero significa que
        cualquiera puede fabricarse uso de algo que no le toca con
        solo usarlo.

        Por eso la proteccion transitoria se decide sobre el uso
        ANTERIOR a una fecha de corte: el momento en que la politica
        se anuncia. Lo que pase despues ya no da derecho a nada.

    Solo LEE. No concede nada: la proteccion la decide una persona
    mirando este informe, no el sistema por su cuenta.
    """

    organizacion = get_organization(organization_id)

    if organizacion is None:
        return {}

    connection = get_connection()

    informe = {}

    try:
        # --- scheduling: horario guardado y activo ---
        horarios = connection.execute(
            """
            SELECT COUNT(*) FROM recording_schedule s
            JOIN devices d ON d.device_id = s.device_id
            WHERE d.organization_id = ? AND s.enabled = 1
            """,
            (organization_id,)
        ).fetchone()[0]

        # --- recordings.store: grabaciones ya archivadas ---
        # Las filas anteriores a la columna storage_state llevan NULL
        # y el servidor las interpreta como archivadas si tienen
        # archivo (storage_state_of). Mirar solo la columna en crudo
        # daria cero para una instalacion antigua que SI usa la
        # funcion, que es justo al reves de lo que hace falta aqui.
        archivadas = connection.execute(
            """
            SELECT COUNT(*) FROM recordings r
            JOIN devices d ON d.device_id = r.device_id
            WHERE d.organization_id = ?
              AND (
                    r.storage_state = 'server_stored'
                 OR (r.storage_state IS NULL AND r.status = 'stored')
              )
            """,
            (organization_id,)
        ).fetchone()[0]

        # --- self_hosted: como esta desplegada ---
        propio = organizacion["deployment_type"] == DEPLOYMENT_SELF_HOSTED

        configurado = {
            "scheduling": horarios > 0,
            "remote.actions": False,   # no deja estado persistente
            "recordings.store": archivadas > 0,
            "self_hosted": propio,
        }

        detalle_config = {
            "scheduling": f"{horarios} horario(s) activo(s)",
            "remote.actions": "no deja configuracion persistente",
            "recordings.store": f"{archivadas} grabacion(es) archivada(s)",
            "self_hosted": organizacion["deployment_type"],
        }

        # --- Uso real, desde la auditoria ---
        usado = {}
        detalle_uso = {}

        for capacidad, acciones in ACCIONES_DE_USO.items():

            if not acciones:
                usado[capacidad] = False
                detalle_uso[capacidad] = "sin operaciones que registrar"
                continue

            marcas = ",".join("?" for _ in acciones)

            parametros = list(acciones) + [organization_id]

            consulta = (
                f"SELECT COUNT(*), MAX(timestamp) FROM audit_log "
                f"WHERE action IN ({marcas}) "
                f"AND organization_id = ? "
                f"AND status != 'error'"
            )

            if desde:
                consulta += " AND timestamp >= ?"
                parametros.append(desde)

            if hasta:
                consulta += " AND timestamp < ?"
                parametros.append(hasta)

            veces, ultima = connection.execute(
                consulta, parametros
            ).fetchone()

            usado[capacidad] = bool(veces)

            detalle_uso[capacidad] = (
                f"{veces} operacion(es), la ultima {ultima}"
                if veces else "sin uso registrado"
            )

    finally:
        connection.close()

    plan = plan_efectivo(organization_id)

    for capacidad in ACCIONES_DE_USO:

        informe[capacidad] = {
            "en_el_plan": capacidad in plan["features"],
            "configurada": configurado.get(capacidad, False),
            "usada": usado.get(capacidad, False),
            "detalle_configuracion": detalle_config.get(capacidad, ""),
            "detalle_uso": detalle_uso.get(capacidad, ""),
        }

        # Lo que de verdad importa: capacidades que la organizacion
        # tiene en marcha y su plan NO incluye. Son las que se
        # cortarian al activar los bloqueos.
        informe[capacidad]["en_riesgo"] = (
            not informe[capacidad]["en_el_plan"]
            and (informe[capacidad]["configurada"]
                 or informe[capacidad]["usada"])
        )

    return informe


def capacidades_en_riesgo(organization_id, desde=None, hasta=None):
    """
    Solo las que se cortarian al activar los bloqueos.

    Es la lista que hay que revisar antes de activar nada, y de la
    que saldrian las protecciones transitorias. Que una capacidad
    aparezca aqui NO la concede: solo señala que alguien tendria que
    decidir sobre ella.

    Para decidir protecciones, pasa SIEMPRE 'hasta' con la fecha de
    corte. Sin ella se cuenta tambien el uso posterior al anuncio, y
    entonces basta con usar una funcion ajena para ganarsela.
    """

    informe = capacidades_en_uso(organization_id, desde, hasta)

    return {
        capacidad: datos
        for capacidad, datos in informe.items()
        if datos["en_riesgo"]
    }


# Cuanto duran las protecciones transitorias, en dias.
#
# No son un privilegio: son tiempo para regularizar. Por eso llevan
# fecha de fin desde el primer dia, y por eso el aviso se manda antes
# de que venza y no cuando ya se corto.
DIAS_DE_PROTECCION = 90

# Con cuanta antelacion se avisa de que una proteccion va a vencer.
DIAS_DE_AVISO = 15


class FaltaFechaDeCorte(OrganizationError):
    """
    Se pidio una propuesta de proteccion sin fecha de corte.

    Es un error a proposito y no un valor por defecto: sin corte se
    contaria el uso posterior al anuncio, y en modo observacion
    cualquiera puede generarlo usando la funcion que no le toca.
    """


def propuesta_de_protecciones(fecha_corte, dias=None,
                              organization_id=None):
    """
    Que habria que proteger, y hasta cuando.

    Devuelve una PROPUESTA. No escribe nada, no concede nada: la
    decision es de una persona que mire este informe.

    'fecha_corte' (ISO) es obligatoria: solo cuenta el uso anterior
    a ella. Es el momento en que la politica se anuncia.
    """

    if not fecha_corte or not str(fecha_corte).strip():
        raise FaltaFechaDeCorte(
            "Hace falta una fecha de corte explicita: sin ella se "
            "contaria tambien el uso posterior al anuncio"
        )

    from datetime import datetime, timedelta, timezone

    try:
        corte = datetime.fromisoformat(str(fecha_corte).strip())

    except ValueError:
        raise FaltaFechaDeCorte(
            f"La fecha de corte no se entiende: {fecha_corte}"
        )

    if corte.tzinfo is None:
        corte = corte.replace(tzinfo=timezone.utc)

    if corte > datetime.now(timezone.utc):
        raise FaltaFechaDeCorte(
            "La fecha de corte no puede estar en el futuro: se "
            "protegeria tambien lo que aun no ha pasado"
        )

    duracion = int(dias or DIAS_DE_PROTECCION)

    vence = (datetime.now(timezone.utc)
             + timedelta(days=duracion)).isoformat()

    objetivo = (
        [get_organization(organization_id)]
        if organization_id is not None
        else list_organizations()
    )

    propuesta = []

    for organizacion in objetivo:

        if organizacion is None:
            continue

        riesgo = capacidades_en_riesgo(
            organizacion["id"], hasta=corte.isoformat()
        )

        if not riesgo:
            continue

        plan = plan_efectivo(organizacion["id"])

        propuesta.append({
            "organization_id": organizacion["id"],
            "organization": organizacion["name"],
            "plan": plan["label"],
            "features": sorted(riesgo),
            "motivo": {
                capacidad: {
                    "configurada": datos["detalle_configuracion"],
                    "usada": datos["detalle_uso"],
                }
                for capacidad, datos in riesgo.items()
            },

            # Lo que habria que escribir para concederla. Se devuelve
            # preparado, pero NO se aplica.
            "aplicar": {
                "plan.features_extra": ",".join(sorted(riesgo)),
                "plan.courtesy_until": vence,
                "plan.courtesy_note":
                    f"Proteccion transitoria por uso anterior a "
                    f"{corte.date().isoformat()}. "
                    f"Vigencia {duracion} dias.",
            },
        })

    return {
        "fecha_corte": corte.isoformat(),
        "vigencia_dias": duracion,
        "vence": vence,
        "organizaciones": propuesta,
        "aplicado": False,
    }


def protecciones_vigentes(organization_id):
    """
    Que protecciones tiene puestas esta organizacion y hasta cuando.

    Distingue las que siguen en vigor de las ya caducadas: una
    caducada deja de conceder, pero su rastro se conserva para poder
    explicar que paso.
    """

    excepciones = get_plan_exceptions(organization_id)

    extra = _lista(excepciones.get("plan.features_extra"))

    if not extra:
        return None

    hasta = excepciones.get("plan.courtesy_until")

    return {
        "organization_id": organization_id,
        "features": sorted(extra),
        "hasta": hasta,
        "vigente": _vigente(hasta),
        "nota": excepciones.get("plan.courtesy_note"),
    }


def protecciones_por_vencer(dias=None):
    """
    Protecciones que caducan dentro de 'dias'.

    Para avisar ANTES de que se corten solas. Una proteccion que
    vence sin aviso es lo mismo que un corte inesperado, que es justo
    lo que se queria evitar.
    """

    from datetime import datetime, timedelta, timezone

    margen = int(dias or DIAS_DE_AVISO)

    limite = datetime.now(timezone.utc) + timedelta(days=margen)
    ahora = datetime.now(timezone.utc)

    avisos = []

    for organizacion in list_organizations():

        proteccion = protecciones_vigentes(organizacion["id"])

        if not proteccion or not proteccion["hasta"]:
            continue

        if not proteccion["vigente"]:
            continue

        try:
            vence = datetime.fromisoformat(proteccion["hasta"])

        except ValueError:
            continue

        if vence.tzinfo is None:
            vence = vence.replace(tzinfo=timezone.utc)

        if vence <= limite:

            proteccion["organization"] = organizacion["name"]
            proteccion["dias_restantes"] = max(
                0, (vence - ahora).days
            )

            avisos.append(proteccion)

    return avisos


def retirar_proteccion(organization_id):
    """
    Retira la proteccion transitoria de una organizacion.

    No borra nada suyo: solo deja de concederse lo que concedia. Los
    equipos, las grabaciones y la configuracion se quedan como
    estaban; lo que cambia es si la capacidad se le reconoce.
    """

    antes = protecciones_vigentes(organization_id)

    if antes is None:
        return None

    set_plan_exception(organization_id, "plan.features_extra", None)
    set_plan_exception(organization_id, "plan.courtesy_until", None)

    # La nota se conserva: explica que hubo una proteccion y por que.

    return antes


# ==============================
# ALMACENAMIENTO
# ==============================

def storage_limit_reached(organization_id):
    """
    True si la organizacion supera el almacenamiento de su plan.

    Un limite de 0 significa sin limite.
    """

    plan = plan_efectivo(organization_id)

    tope = plan.get("storage_mb") or 0

    if tope <= 0:
        return False

    usado = organization_usage(organization_id).get("storage_mb", 0)

    return usado >= tope


def storage_status(organization_id):
    """Cuanto se usa, cuanto cabe y cuanto sobra."""

    plan = plan_efectivo(organization_id)
    uso = organization_usage(organization_id)

    tope = plan.get("storage_mb") or 0
    usado = uso.get("storage_mb", 0)

    return {
        "used_mb": usado,
        "limit_mb": tope,
        "unlimited": tope <= 0,
        "over_limit": tope > 0 and usado >= tope,
        "free_mb": None if tope <= 0 else round(tope - usado, 2),
    }


def has_feature(organization_id, feature):
    """
    True si la organizacion puede usar esa capacidad.

    Mira el plan EFECTIVO, no el de la tabla: una organizacion puede
    tener concedida una capacidad que su plan base no incluye.
    """

    return feature in plan_efectivo(organization_id)["features"]


def limit_reached(organization_id, recurso):
    """
    True si la organizacion ya esta en el limite de su plan.

    'recurso' es 'devices' o 'users'. Un limite de 0 significa sin
    limite. La comprobacion vive aqui, en el servidor: un boton
    deshabilitado no impide nada a quien sepa escribir una peticion.
    """

    plan = plan_efectivo(organization_id)

    maximo = plan.get(f"max_{recurso}") or 0

    if maximo <= 0:
        return False

    return organization_usage(organization_id).get(recurso, 0) >= maximo


def check_limit(organization_id, recurso):
    """Lanza OrganizationError si el limite del plan ya esta alcanzado."""

    if not limit_reached(organization_id, recurso):
        return True

    plan = plan_efectivo(organization_id)

    etiquetas = {"devices": "equipos", "users": "usuarios"}

    raise OrganizationError(
        f"El plan {plan['label']} permite como maximo "
        f"{plan.get('max_' + recurso)} {etiquetas.get(recurso, recurso)}. "
        "Amplia el plan para anadir mas."
    )


# ==============================
# MIGRACION DE LA INSTALACION EXISTENTE
# ==============================

def ensure_default_organization():
    """
    Convierte la instalacion actual en la primera organizacion.

    Idempotente: si ya hay organizaciones no hace nada, y si la primera
    ya existe no la duplica. Los registros que se quedaron sin
    organizacion se asocian a ella.

    Los equipos conservan su device_id y su token: aqui solo se rellena
    una columna nueva. Nada se regenera y nada se borra.

    Devuelve el id de la organizacion por defecto, o None si no habia
    nada que migrar.
    """

    connection = get_connection()

    try:

        fila = connection.execute(
            "SELECT id FROM organizations WHERE slug = ?",
            (DEFAULT_ORGANIZATION_SLUG,)
        ).fetchone()

        if fila is None:

            total = connection.execute(
                "SELECT COUNT(*) FROM organizations"
            ).fetchone()[0]

            hay_datos = connection.execute(
                "SELECT (SELECT COUNT(*) FROM devices) "
                "+ (SELECT COUNT(*) FROM users)"
            ).fetchone()[0]

            # Solo se crea si hace falta: una instalacion nueva y vacia
            # no tiene por que arrancar con una organizacion de ejemplo.
            if total == 0 and hay_datos == 0:
                return None

            if total == 0:

                ahora = _ahora()

                cursor = connection.execute(
                    """
                    INSERT INTO organizations (
                        name, slug, plan, subscription_status,
                        billing_mode, active, created_at, updated_at, notes
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        DEFAULT_ORGANIZATION_NAME,
                        DEFAULT_ORGANIZATION_SLUG,
                        PLAN_PRO,
                        STATUS_ACTIVE,
                        BILLING_COURTESY,
                        ahora,
                        ahora,
                        "Primera organizacion de la instalacion."
                    )
                )

                connection.commit()

                organization_id = cursor.lastrowid

            else:
                # Hay organizaciones pero ninguna es la primera: no se
                # inventa nada, los huerfanos se dejan como estan.
                return None

        else:
            organization_id = fila["id"]

        # Registros sin organizacion: se asocian a la primera. El WHERE
        # hace que repetir la migracion no toque lo ya asignado.
        connection.execute(
            "UPDATE devices SET organization_id = ? "
            "WHERE organization_id IS NULL",
            (organization_id,)
        )

        connection.execute(
            "UPDATE users SET organization_id = ? "
            "WHERE organization_id IS NULL AND role != 'platform_owner'",
            (organization_id,)
        )

        connection.commit()

        return organization_id

    finally:
        connection.close()
