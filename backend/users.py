"""
Usuarios, roles y permisos del panel.

Hasta ahora RemoteAdmin tenia una sola cuenta, definida en el .env y luego
en auth_state. Aqui pasa a tener varias, con dos roles:

  - owner:    control total. No se le piden permisos, los tiene todos.
  - subadmin: solo puede hacer aquello para lo que tenga permiso explicito.

La autorizacion se decide SIEMPRE aqui, en el servidor, a partir del
usuario que firma el token de sesion. Nada de lo que envie el navegador
—ni el rol, ni el identificador, ni la lista de permisos— se tiene en
cuenta: ocultar un boton es una comodidad visual, no una medida de
seguridad.

Relacion con auth_state
-----------------------
auth_state sigue existiendo y guarda la fecha de corte GLOBAL de sesiones,
la que cierra las de todo el mundo de una vez. Su password_hash queda como
rastro historico de la epoca de cuenta unica: en cuanto existe la tabla
users, la credencial que manda es la de la fila del usuario. La cuenta
antigua se convierte automaticamente en el owner conservando su hash, asi
que nadie tiene que volver a escribir su contrasena.
"""

import time

from backend.database import get_connection


# Nivel plataforma: quien opera RemoteAdmin. No pertenece a ninguna
# empresa cliente y por eso su organization_id es NULL. Administra
# organizaciones, no equipos.
ROLE_PLATFORM_OWNER = "platform_owner"

# Nivel organizacion: dentro de una empresa cliente.
ROLE_OWNER = "owner"
ROLE_SUBADMIN = "subadmin"

ROLES = (ROLE_PLATFORM_OWNER, ROLE_OWNER, ROLE_SUBADMIN)


# Permisos que se pueden conceder a un subadmin. Lista cerrada: conceder
# algo que no este aqui se rechaza, para que un error de escritura no cree
# en silencio un permiso que luego nadie comprueba.
#
# La administracion de usuarios NO esta en la lista a proposito: es
# exclusiva del owner. El dia que haga falta delegarla, se anade aqui un
# permiso nuevo y se cambia la comprobacion en un unico sitio.
PERMISSIONS = (
    "dashboard.view",
    "devices.view",
    "recordings.view",
    "recordings.download",
    "recordings.manage",
    "processes.view",
    "services.view",
    "settings.view",
    "settings.edit",
    "device.lock",
    "device.logoff",
    "device.restart",
    "device.shutdown"
)

PERMISSION_SET = frozenset(PERMISSIONS)


class UserError(ValueError):
    """Operacion de usuarios rechazada, con un motivo que se puede ensenar."""


def _ahora_iso():

    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _fila_a_usuario(fila, permisos=None):
    """
    Convierte una fila en un diccionario apto para enviar al panel.

    El hash NUNCA sale de aqui: no se incluye en el resultado, asi no puede
    acabar por accidente en una respuesta HTTP ni en un registro.
    """

    if fila is None:
        return None

    usuario = {
        "id": fila["id"],
        "username": fila["username"],
        "organization_id": fila["organization_id"],
        "email": fila["email"],
        "email_verified": bool(fila["email_verified"]),
        "role": fila["role"],
        "active": bool(fila["active"]),
        "created_at": fila["created_at"],
        "updated_at": fila["updated_at"],
        "password_changed_at": fila["password_changed_at"],
        "sessions_valid_from": int(fila["sessions_valid_from"] or 0)
    }

    if permisos is not None:
        usuario["permissions"] = sorted(permisos)

    return usuario


# ==============================
# MIGRACION DE LA CUENTA UNICA
# ==============================

def ensure_owner_migrated():
    """
    Convierte la cuenta unica en el owner. Idempotente y sin pasos manuales.

    Solo actua si no hay ningun usuario todavia. Copia el hash vigente de
    auth_state (que a su vez pudo venir del .env), de modo que la contrasena
    de siempre sigue funcionando. Si no hay credencial por ninguna via, no
    se inventa ninguna: no crear usuario es mas seguro que crear uno vacio.

    Devuelve el username del owner creado, o None si no hizo falta.
    """

    from backend.auth import AUTH_USERNAME, get_auth_state

    connection = get_connection()

    try:

        total = connection.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        if total:
            return None

    finally:
        connection.close()

    estado = get_auth_state()

    if not estado or not estado.get("password_hash"):
        return None

    ahora = _ahora_iso()

    connection = get_connection()

    try:

        # INSERT OR IGNORE: si dos peticiones entran a la vez, la segunda no
        # duplica ni pisa al owner recien creado.
        connection.execute(
            """
            INSERT OR IGNORE INTO users (
                username, password_hash, email, email_verified,
                role, active, created_at, updated_at,
                password_changed_at, sessions_valid_from
            )
            VALUES (?, ?, NULL, 0, ?, 1, ?, ?, ?, ?)
            -- organization_id lo rellena ensure_default_organization()
            """,
            (
                AUTH_USERNAME,
                estado["password_hash"],
                ROLE_OWNER,
                ahora,
                ahora,
                estado.get("password_changed_at"),
                int(estado.get("sessions_valid_from") or 0)
            )
        )

        connection.commit()

    finally:
        connection.close()

    # El owner recien creado tiene que pertenecer a una organizacion.
    #
    # Al arrancar, la migracion de organizaciones corre justo despues y
    # lo adjunta. Pero esta funcion tambien se llama en caliente (la
    # dispara el primer get_user), y entonces la migracion ya habia
    # pasado: sin esto el owner se quedaria sin organizacion y el
    # aislamiento lo dejaria sin ver sus propios equipos.
    try:
        from backend.organizations import ensure_default_organization

        ensure_default_organization()

    except Exception as error:
        print(f"[usuarios] No se pudo asociar el owner: {error}")

    return AUTH_USERNAME


# ==============================
# CONSULTA
# ==============================

def get_user(username):
    """Usuario por nombre, con sus permisos. None si no existe."""

    if not username:
        return None

    ensure_owner_migrated()

    connection = get_connection()

    try:

        fila = connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,)
        ).fetchone()

        if fila is None:
            return None

        permisos = {
            p["permission"]
            for p in connection.execute(
                "SELECT permission FROM user_permissions WHERE user_id = ?",
                (fila["id"],)
            )
        }

    finally:
        connection.close()

    return _fila_a_usuario(fila, permisos)


def get_user_by_id(user_id):

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT username FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

    finally:
        connection.close()

    return get_user(fila["username"]) if fila else None


def get_password_hash(username):
    """Hash de un usuario. Solo para verificar; nunca se devuelve al panel."""

    ensure_owner_migrated()

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (username,)
        ).fetchone()

    finally:
        connection.close()

    return fila["password_hash"] if fila else None


def list_users(organization_id=None):
    """
    Usuarios con sus permisos, ordenados por nombre.

    Con organization_id se devuelven SOLO los de esa empresa. Sin el, se
    devuelven todos: es la vista de la plataforma, y quien llama debe
    haber comprobado que tiene derecho a verla.
    """

    ensure_owner_migrated()

    connection = get_connection()

    try:

        if organization_id is not None:
            filas = connection.execute(
                "SELECT * FROM users WHERE organization_id = ? "
                "ORDER BY role = 'owner' DESC, username",
                (organization_id,)
            ).fetchall()

        else:
            filas = connection.execute(
                "SELECT * FROM users ORDER BY role = 'owner' DESC, username"
            ).fetchall()

        permisos = {}

        for p in connection.execute(
            "SELECT user_id, permission FROM user_permissions"
        ):
            permisos.setdefault(p["user_id"], set()).add(p["permission"])

    finally:
        connection.close()

    return [
        _fila_a_usuario(fila, permisos.get(fila["id"], set()))
        for fila in filas
    ]


def count_active_owners(excluding_id=None, organization_id=None):
    """
    Cuantos owner activos quedan, sin contar a uno concreto.

    Con organization_id se cuentan solo los de esa empresa: cada
    organizacion debe conservar su propio Owner, y que otra tenga el
    suyo no la salva de quedarse sin administrador.
    """

    connection = get_connection()

    try:

        condiciones = ["role = ?", "active = 1"]
        parametros = [ROLE_OWNER]

        if excluding_id is not None:
            condiciones.append("id != ?")
            parametros.append(excluding_id)

        if organization_id is not None:
            condiciones.append("organization_id = ?")
            parametros.append(organization_id)

        fila = connection.execute(
            "SELECT COUNT(*) FROM users WHERE " + " AND ".join(condiciones),
            tuple(parametros)
        ).fetchone()

    finally:
        connection.close()

    return fila[0]


# ==============================
# AUTORIZACION
# ==============================

def is_platform_owner(usuario):
    """True si opera la plataforma, por encima de las organizaciones."""

    return bool(usuario) and usuario.get("role") == ROLE_PLATFORM_OWNER


def is_owner(usuario):
    """
    True si administra SU organizacion.

    El usuario de plataforma tambien cuenta: puede hacer todo lo que
    hace un Owner, pero dentro de la organizacion que este mirando. Lo
    que lo distingue es que puede mirar cualquiera.
    """

    return bool(usuario) and usuario.get("role") in (
        ROLE_OWNER, ROLE_PLATFORM_OWNER
    )


def organization_of(usuario):
    """Organizacion del usuario. None en el usuario de plataforma."""

    return (usuario or {}).get("organization_id")


def same_organization(usuario, organization_id):
    """
    True si el usuario puede actuar sobre esa organizacion.

    El de plataforma pasa siempre; el resto, solo sobre la suya. Un
    recurso sin organizacion (datos a medio migrar) queda reservado a la
    plataforma: ante la duda, no se ensena.
    """

    if not usuario or not usuario.get("active"):
        return False

    if is_platform_owner(usuario):
        return True

    propia = organization_of(usuario)

    return bool(propia) and propia == organization_id


def has_permission(usuario, permission):
    """
    True si este usuario puede hacer eso.

    Un usuario desactivado no puede nada, sea cual sea su rol: es la ultima
    red por si una sesion suya sobreviviera a la desactivacion.
    """

    if not usuario or not usuario.get("active"):
        return False

    if usuario.get("role") in (ROLE_OWNER, ROLE_PLATFORM_OWNER):
        return True

    return permission in set(usuario.get("permissions") or ())


def validate_permissions(permisos):
    """Deja la lista en permisos conocidos, o lanza si hay alguno inventado."""

    if permisos is None:
        return set()

    if isinstance(permisos, str) or not hasattr(permisos, "__iter__"):
        raise UserError("La lista de permisos no es valida")

    pedidos = {str(p) for p in permisos}

    desconocidos = pedidos - PERMISSION_SET

    if desconocidos:
        raise UserError(
            "Permisos desconocidos: " + ", ".join(sorted(desconocidos))
        )

    return pedidos


# ==============================
# ALTA Y MODIFICACION
# ==============================

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32


def validate_username(username):

    if not isinstance(username, str):
        raise UserError("El nombre de usuario no es valido")

    nombre = username.strip()

    if len(nombre) < USERNAME_MIN_LENGTH:
        raise UserError(
            f"El nombre de usuario debe tener al menos "
            f"{USERNAME_MIN_LENGTH} caracteres"
        )

    if len(nombre) > USERNAME_MAX_LENGTH:
        raise UserError(
            f"El nombre de usuario no puede superar los "
            f"{USERNAME_MAX_LENGTH} caracteres"
        )

    # Letras, numeros, punto, guion y guion bajo. Sin espacios ni simbolos
    # raros: el nombre aparece en rutas de auditoria y en la interfaz.
    for caracter in nombre:
        if not (caracter.isalnum() or caracter in "._-"):
            raise UserError(
                "El nombre de usuario solo admite letras, numeros, "
                "punto, guion y guion bajo"
            )

    return nombre


def validate_email(email):
    """Validacion deliberadamente laxa: solo descarta lo que no es un correo."""

    if email is None or email == "":
        return None

    if not isinstance(email, str):
        raise UserError("El correo no es valido")

    correo = email.strip()

    if len(correo) > 254 or correo.count("@") != 1:
        raise UserError("El correo no es valido")

    local, dominio = correo.split("@")

    if not local or not dominio or "." not in dominio or " " in correo:
        raise UserError("El correo no es valido")

    return correo


def create_user(username, password, email=None, role=ROLE_SUBADMIN,
                permissions=None, active=True, organization_id=None):
    """
    Crea un usuario. Devuelve el usuario creado (sin hash).

    El rol owner no se puede asignar por esta via: un alta no debe poder
    fabricar un segundo control total. Los owner salen de la migracion de
    la cuenta original.

    organization_id lo decide SIEMPRE quien llama a partir de la sesion,
    nunca el cuerpo de la peticion: si no, un Owner podria crear
    usuarios dentro de otra empresa.
    """

    from backend.auth import validate_new_password, generate_password_hash

    nombre = validate_username(username)
    correo = validate_email(email)

    if role != ROLE_SUBADMIN:
        raise UserError("Solo se pueden crear usuarios con el rol subadmin")

    problema = validate_new_password(password)

    if problema:
        raise UserError(problema)

    permisos = validate_permissions(permissions)

    ensure_owner_migrated()

    ahora = _ahora_iso()

    connection = get_connection()

    try:

        existe = connection.execute(
            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE",
            (nombre,)
        ).fetchone()

        if existe:
            raise UserError("Ya existe un usuario con ese nombre")

        cursor = connection.execute(
            """
            INSERT INTO users (
                username, password_hash, email, email_verified,
                role, active, created_at, updated_at,
                password_changed_at, sessions_valid_from,
                organization_id
            )
            VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                nombre,
                generate_password_hash(password),
                correo,
                ROLE_SUBADMIN,
                1 if active else 0,
                ahora,
                ahora,
                ahora,
                organization_id
            )
        )

        user_id = cursor.lastrowid

        for permiso in sorted(permisos):
            connection.execute(
                "INSERT OR IGNORE INTO user_permissions (user_id, permission) "
                "VALUES (?, ?)",
                (user_id, permiso)
            )

        connection.commit()

    finally:
        connection.close()

    return get_user(nombre)


def _tocar(connection, user_id):
    connection.execute(
        "UPDATE users SET updated_at = ? WHERE id = ?",
        (_ahora_iso(), user_id)
    )


def set_permissions(username, permissions):
    """Reemplaza la lista de permisos de un subadmin."""

    usuario = get_user(username)

    if usuario is None:
        raise UserError("El usuario no existe")

    if usuario["role"] == ROLE_OWNER:
        raise UserError(
            "El owner tiene control total; no se le asignan permisos"
        )

    permisos = validate_permissions(permissions)

    connection = get_connection()

    try:
        connection.execute(
            "DELETE FROM user_permissions WHERE user_id = ?",
            (usuario["id"],)
        )

        for permiso in sorted(permisos):
            connection.execute(
                "INSERT INTO user_permissions (user_id, permission) "
                "VALUES (?, ?)",
                (usuario["id"], permiso)
            )

        _tocar(connection, usuario["id"])

        connection.commit()

    finally:
        connection.close()

    return get_user(username)


def set_active(username, active):
    """
    Activa o desactiva un usuario.

    Al desactivar se adelanta su fecha de corte: pierde el acceso en el
    acto, sin esperar a que caduque su token. Nunca se puede desactivar al
    ultimo owner activo, o el panel quedaria sin nadie que lo administre.
    """

    usuario = get_user(username)

    if usuario is None:
        raise UserError("El usuario no existe")

    if not active and usuario["role"] == ROLE_OWNER:
        if count_active_owners(
            excluding_id=usuario["id"],
            organization_id=usuario["organization_id"]
        ) == 0:
            raise UserError(
                "No se puede desactivar al ultimo owner activo "
                "de la organizacion"
            )

    connection = get_connection()

    try:

        if active:
            connection.execute(
                "UPDATE users SET active = 1, updated_at = ? WHERE id = ?",
                (_ahora_iso(), usuario["id"])
            )

        else:
            connection.execute(
                "UPDATE users SET active = 0, updated_at = ?, "
                "sessions_valid_from = ? WHERE id = ?",
                (
                    _ahora_iso(),
                    max(int(time.time()) + 1,
                        usuario["sessions_valid_from"] + 1),
                    usuario["id"]
                )
            )

        connection.commit()

    finally:
        connection.close()

    return get_user(username)


def delete_user(username):
    """Elimina un usuario. Nunca al ultimo owner activo."""

    usuario = get_user(username)

    if usuario is None:
        raise UserError("El usuario no existe")

    if usuario["role"] == ROLE_OWNER:
        if count_active_owners(
            excluding_id=usuario["id"],
            organization_id=usuario["organization_id"]
        ) == 0:
            raise UserError(
                "No se puede eliminar al ultimo owner activo "
                "de la organizacion"
            )

    connection = get_connection()

    try:
        connection.execute(
            "DELETE FROM user_permissions WHERE user_id = ?",
            (usuario["id"],)
        )
        connection.execute("DELETE FROM users WHERE id = ?", (usuario["id"],))
        connection.commit()

    finally:
        connection.close()

    return True


def set_email(username, email):

    usuario = get_user(username)

    if usuario is None:
        raise UserError("El usuario no existe")

    correo = validate_email(email)

    connection = get_connection()

    try:
        # Cambiar el correo lo deja sin verificar: si no, bastaria con
        # apuntarlo a otra direccion para heredar la verificacion anterior.
        connection.execute(
            "UPDATE users SET email = ?, email_verified = 0, updated_at = ? "
            "WHERE id = ?",
            (correo, _ahora_iso(), usuario["id"])
        )
        connection.commit()

    finally:
        connection.close()

    return get_user(username)


def find_by_email(email):
    """
    Usuario con ese correo, o None.

    Se usa en la recuperacion por correo. Quien llame NO debe revelar el
    resultado: la respuesta al usuario es la misma exista o no.
    """

    correo = (email or "").strip()

    if not correo:
        return None

    ensure_owner_migrated()

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT username FROM users "
            "WHERE email = ? COLLATE NOCASE AND active = 1",
            (correo,)
        ).fetchone()

    finally:
        connection.close()

    return get_user(fila["username"]) if fila else None


# ==============================
# CONTRASENA POR USUARIO
# ==============================

def set_user_password(username, new_password, invalidate_sessions=True):
    """
    Guarda la contrasena de un usuario y cierra sus demas sesiones.

    Solo afecta a ESE usuario: la fecha de corte es suya, no global, asi
    que cambiar la contrasena de un subadmin no echa a nadie mas.
    """

    from backend.auth import generate_password_hash

    usuario = get_user(username)

    if usuario is None:
        raise UserError("El usuario no existe")

    ahora = _ahora_iso()

    corte = (
        max(int(time.time()) + 1, usuario["sessions_valid_from"] + 1)
        if invalidate_sessions
        else usuario["sessions_valid_from"]
    )

    connection = get_connection()

    try:
        connection.execute(
            """
            UPDATE users
            SET password_hash = ?, password_changed_at = ?, updated_at = ?,
                sessions_valid_from = ?
            WHERE id = ?
            """,
            (
                generate_password_hash(new_password),
                ahora,
                ahora,
                corte,
                usuario["id"]
            )
        )
        connection.commit()

    finally:
        connection.close()

    return corte


def set_platform_owner(username, enabled=True):
    """
    Convierte a un usuario en operador de la plataforma, o lo devuelve a
    su organizacion.

    No hay endpoint para esto a proposito: quien opera la plataforma se
    designa desde la consola del servidor. Un ascenso por HTTP seria el
    camino mas corto para que un Owner se promocione solo.

    Al ascender se desliga de su organizacion (organization_id = NULL):
    deja de ser de una empresa para estar por encima de todas.
    """

    usuario = get_user(username)

    if usuario is None:
        raise UserError("El usuario no existe")

    if not enabled and usuario["role"] != ROLE_PLATFORM_OWNER:
        return usuario

    # Ascender al unico Owner dejaria a su organizacion sin nadie que la
    # administre: el ascenso lo desliga de ella. Es la misma proteccion
    # que impide desactivar o eliminar al ultimo Owner.
    if enabled and usuario["role"] == ROLE_OWNER             and usuario["organization_id"]:

        if count_active_owners(
            excluding_id=usuario["id"],
            organization_id=usuario["organization_id"]
        ) == 0:
            raise UserError(
                "Es el unico owner activo de su organizacion. Nombra "
                "antes otro owner, o la organizacion se quedaria sin "
                "quien la administre."
            )

    connection = get_connection()

    try:

        if enabled:
            connection.execute(
                "UPDATE users SET role = ?, organization_id = NULL, "
                "updated_at = ? WHERE id = ?",
                (ROLE_PLATFORM_OWNER, _ahora_iso(), usuario["id"])
            )

        else:
            connection.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                (ROLE_OWNER, _ahora_iso(), usuario["id"])
            )

        connection.commit()

    finally:
        connection.close()

    return get_user(username)


def set_organization(username, organization_id):
    """
    Mueve un usuario a una organizacion.

    Reservado a la plataforma. No existe endpoint que lo exponga: el
    organization_id no se acepta nunca del navegador.
    """

    usuario = get_user(username)

    if usuario is None:
        raise UserError("El usuario no existe")

    if usuario["role"] == ROLE_PLATFORM_OWNER:
        raise UserError(
            "El operador de la plataforma no pertenece a ninguna "
            "organizacion"
        )

    connection = get_connection()

    try:
        connection.execute(
            "UPDATE users SET organization_id = ?, updated_at = ? "
            "WHERE id = ?",
            (organization_id, _ahora_iso(), usuario["id"])
        )
        connection.commit()

    finally:
        connection.close()

    return get_user(username)


def get_sessions_valid_from(username):
    """Fecha de corte propia del usuario. 0 si no existe o no tiene."""

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT sessions_valid_from FROM users "
            "WHERE username = ? COLLATE NOCASE",
            (username,)
        ).fetchone()

    finally:
        connection.close()

    return int(fila["sessions_valid_from"] or 0) if fila else 0


def is_active(username):
    """
    True si el usuario existe y esta activo.

    Devuelve True tambien cuando no hay ninguna fila para ese nombre: es el
    caso de las instalaciones anteriores a esta tabla, donde la unica cuenta
    vivia en auth_state. Asi nadie se queda fuera por actualizar.
    """

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT active FROM users WHERE username = ? COLLATE NOCASE",
            (username,)
        ).fetchone()

    finally:
        connection.close()

    if fila is None:
        return True

    return bool(fila["active"])
