"""
Andamiaje compartido por las pruebas de usuarios, roles y permisos.

Levanta la aplicacion real contra una base de datos temporal. Nunca se toca
la base real ni la contrasena real.
"""

import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-usuarios")
os.environ.setdefault("AUTH_USERNAME", "odette")

import backend.database as database

BASE_TEMPORAL = Path(tempfile.mkdtemp(prefix="usuarios_")) / "prueba.db"
database.DATABASE_PATH = BASE_TEMPORAL
database.init_db()

import backend.auth as auth
import backend.users as users
import backend.main as main
import backend.ratelimit as ratelimit

from backend import audit

from fastapi.testclient import TestClient


COOKIE = "remoteadmin_token"

# El propietario es QUIEN DIGA backend.auth, no un nombre fijo.
#
# auth lee AUTH_USERNAME al importarse. Este banco lo fija con
# setdefault, pero si otra suite importo backend.auth ANTES, la
# variable ya valia su valor por defecto y el propietario que se crea
# no se llama "odette". Entonces get_user("odette") devuelve None y
# todas las peticiones responden 401, lejos de la causa.
OWNER = auth.AUTH_USERNAME
CLAVE_OWNER = "ClaveDelOwnerDePrueba!"
CLAVE_SUBADMIN = "ClaveDelSubadminPrueba!"


def _exigir_base_de_pruebas():
    """
    Se niega a trabajar sobre una base que no sea temporal.

    reiniciar() hace DELETE FROM users y DELETE FROM audit_log. Si
    DATABASE_PATH apuntara a la base de desarrollo —porque una suite
    anterior la dejo ahi— esto borraria datos de verdad.

    Paso: una suite devolvia la ruta a donde estaba antes al limpiar
    sus temporales, y la siguiente vacio el registro de auditoria de
    la base local.

    La comprobacion es barata y la alternativa es perder datos sin
    enterarse hasta mucho despues.
    """

    ruta = str(database.DATABASE_PATH)

    temporal = str(Path(tempfile.gettempdir()).resolve()).lower()

    if not str(Path(ruta).resolve()).lower().startswith(temporal):
        raise RuntimeError(
            "Las pruebas no se ejecutan sobre %s: no es una base "
            "temporal. Apunta DATABASE_PATH a un directorio temporal "
            "antes de llamar a reiniciar()." % ruta
        )


def reiniciar():
    """Estado limpio: solo el owner, sembrado como en una instalacion real."""

    # El banco usa SIEMPRE su propia base, pase lo que pase antes.
    #
    # Otra suite puede haber movido DATABASE_PATH a la suya, o a un
    # temporal vacio al limpiar. Si se diera por buena esa ruta, aqui
    # se trabajaria sobre una base ajena o sobre uno sin tablas.
    #
    # Reclamar la propia es lo unico que hace que estas pruebas den
    # el mismo resultado solas que en un lanzador unico.
    if database.DATABASE_PATH != BASE_TEMPORAL:
        database.DATABASE_PATH = BASE_TEMPORAL
        database.init_db()

    _exigir_base_de_pruebas()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM user_permissions")
    conexion.execute("DELETE FROM users")
    conexion.execute("DELETE FROM auth_state")
    conexion.execute("DELETE FROM audit_log")
    conexion.execute("DELETE FROM revoked_sessions")

    conexion.execute(
        "INSERT INTO auth_state (id, password_hash, password_changed_at, "
        "sessions_valid_from) VALUES (1, ?, NULL, 0)",
        (auth.generate_password_hash(CLAVE_OWNER),)
    )

    conexion.commit()
    conexion.close()

    ratelimit._failures.clear()

    users.ensure_owner_migrated()


def cliente(username=None):
    """TestClient con la sesion de un usuario concreto (o sin sesion)."""

    c = TestClient(main.app, base_url="https://testserver")

    if username:
        c.cookies.set(COOKIE, auth.create_token(username))

    return c


def crear_subadmin(nombre="subadmin1", permisos=None, activo=True,
                   email=None, organization_id=None):
    """
    Crea un subadministrador dentro de la organizacion del owner.

    En produccion el endpoint toma la organizacion de la sesion de quien
    crea; aqui se hace lo mismo a mano. Un usuario sin organizacion no
    podria ver ni un equipo, porque el aislamiento lo trataria como
    ajeno a todas.
    """

    if organization_id is None:

        from backend import organizations

        organization_id = organizations.ensure_default_organization()

        propietario = users.get_user(OWNER)

        if propietario and propietario.get("organization_id"):
            organization_id = propietario["organization_id"]

    return users.create_user(
        nombre,
        CLAVE_SUBADMIN,
        email=email,
        permissions=permisos or [],
        active=activo,
        organization_id=organization_id
    )


def registros(action=None):
    return audit.list_audit(limit=500, action=action)
