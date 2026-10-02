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

OWNER = "odette"
CLAVE_OWNER = "ClaveDelOwnerDePrueba!"
CLAVE_SUBADMIN = "ClaveDelSubadminPrueba!"


def reiniciar():
    """Estado limpio: solo el owner, sembrado como en una instalacion real."""

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
                   email=None):

    return users.create_user(
        nombre,
        CLAVE_SUBADMIN,
        email=email,
        permissions=permisos or [],
        active=activo
    )


def registros(action=None):
    return audit.list_audit(limit=500, action=action)
