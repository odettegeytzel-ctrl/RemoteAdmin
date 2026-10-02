"""
Andamiaje compartido por las pruebas de G4b y G4c.

Levanta la aplicacion real contra una base de datos temporal, con una
contrasena inventada para la prueba. Nunca se toca la base real ni la
contrasena real de produccion.
"""

import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Antes de importar backend.auth: lee la configuracion al cargarse
os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-g4")
os.environ.setdefault("AUTH_USERNAME", "odette")

import backend.database as database

BASE_TEMPORAL = Path(tempfile.mkdtemp(prefix="g4_")) / "prueba.db"
database.DATABASE_PATH = BASE_TEMPORAL
database.init_db()

import backend.auth as auth
import backend.ratelimit as ratelimit
import backend.main as main

from backend import audit

from fastapi.testclient import TestClient


COOKIE = "remoteadmin_token"

# Contrasenas inventadas SOLO para las pruebas
CLAVE_INICIAL = "ClaveInicialDePrueba-G4!"
CLAVE_NUEVA = "ClaveNuevaDePrueba-G4!"
CLAVE_TERCERA = "TerceraClaveDePrueba-G4!"


def poner_clave(clave):
    """
    Deja la credencial en un estado conocido, sin cortar sesiones.

    Se escribe directamente para no depender de lo que se esta probando.
    """

    conexion = database.get_connection()

    conexion.execute(
        """
        INSERT INTO auth_state (
            id, password_hash, password_changed_at, sessions_valid_from
        )
        VALUES (1, ?, NULL, 0)
        ON CONFLICT(id) DO UPDATE SET
            password_hash = excluded.password_hash,
            password_changed_at = NULL,
            sessions_valid_from = 0
        """,
        (auth.generate_password_hash(clave),)
    )

    conexion.commit()
    conexion.close()


def fila_auth_state():

    conexion = database.get_connection()

    fila = conexion.execute(
        "SELECT * FROM auth_state WHERE id = 1"
    ).fetchone()

    conexion.close()

    return fila


def limpiar_auditoria():

    conexion = database.get_connection()
    conexion.execute("DELETE FROM audit_log")
    conexion.commit()
    conexion.close()


def registros_auditoria(action=None):
    return audit.list_audit(limit=500, action=action)


def limpiar_rate_limit():
    ratelimit._failures.clear()


def cliente(con_sesion=True, token=None):
    """TestClient sobre la app real. Sin 'with': no se lanza el startup."""

    c = TestClient(main.app, base_url="https://testserver")

    if con_sesion:
        c.cookies.set(COOKIE, token or auth.create_token("odette"))

    return c


def reiniciar(clave=CLAVE_INICIAL):
    """Estado limpio antes de cada prueba."""

    poner_clave(clave)
    limpiar_auditoria()
    limpiar_rate_limit()
