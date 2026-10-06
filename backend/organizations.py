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
        equipos = connection.execute(
            "SELECT COUNT(*) FROM devices WHERE organization_id = ?",
            (organization_id,)
        ).fetchone()[0]

        usuarios = connection.execute(
            "SELECT COUNT(*) FROM users WHERE organization_id = ?",
            (organization_id,)
        ).fetchone()[0]

    finally:
        connection.close()

    return {"devices": equipos, "users": usuarios}


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


def has_feature(organization_id, feature):
    """True si el plan de la organizacion incluye esa capacidad."""

    return feature in plan_of(organization_id)["features"]


def limit_reached(organization_id, recurso):
    """
    True si la organizacion ya esta en el limite de su plan.

    'recurso' es 'devices' o 'users'. Un limite de 0 significa sin
    limite. La comprobacion vive aqui, en el servidor: un boton
    deshabilitado no impide nada a quien sepa escribir una peticion.
    """

    plan = plan_of(organization_id)

    maximo = plan.get(f"max_{recurso}") or 0

    if maximo <= 0:
        return False

    return organization_usage(organization_id).get(recurso, 0) >= maximo


def check_limit(organization_id, recurso):
    """Lanza OrganizationError si el limite del plan ya esta alcanzado."""

    if not limit_reached(organization_id, recurso):
        return True

    plan = plan_of(organization_id)

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
