"""
Andamiaje compartido por las pruebas de G2 (procesos) y G3 (servicios).

Levanta la aplicación real contra una base de datos temporal y sustituye el
WebSocket del Agent por uno falso. Así se prueban los endpoints de verdad
—middleware de autenticación incluido— sin tocar la base real ni necesitar
un equipo Windows conectado.
"""

import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Deben estar puestas ANTES de importar backend.auth: el módulo lee la
# configuración al cargarse. load_dotenv no pisa lo que ya existe.
os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-g2-g3")
os.environ.setdefault("AUTH_USERNAME", "odette")

import backend.database as database

BASE_TEMPORAL = Path(tempfile.mkdtemp(prefix="g23_")) / "prueba.db"
database.DATABASE_PATH = BASE_TEMPORAL
database.init_db()

import backend.main as main
import backend.queries as queries

from fastapi.testclient import TestClient


COOKIE = "remoteadmin_token"

DEVICE_OK = "equipo-de-prueba-1"
DEVICE_OTRO = "equipo-de-prueba-2"
DEVICE_INEXISTENTE = "equipo-que-no-existe"


def preparar_dispositivos():
    """Dos equipos dados de alta: el nuestro y otro, para el spoofing."""

    conexion = database.get_connection()

    for device_id, hostname in (
        (DEVICE_OK, "PRUEBA-1"),
        (DEVICE_OTRO, "PRUEBA-2")
    ):
        conexion.execute(
            "INSERT OR IGNORE INTO devices (device_id, hostname) "
            "VALUES (?, ?)",
            (device_id, hostname)
        )

    conexion.commit()
    conexion.close()


def limpiar_auditoria():

    conexion = database.get_connection()
    conexion.execute("DELETE FROM audit_log")
    conexion.commit()
    conexion.close()


def registros_auditoria(action=None):

    from backend import audit

    return audit.list_audit(limit=500, action=action)


class WebSocketFalso:
    """
    Sustituto del WebSocket del Agent.

    Cuando el backend le envía una consulta, responde al instante con lo que
    se le haya configurado. 'responder_como' permite contestar en nombre de
    otro equipo, que es justo lo que debe rechazarse.
    """

    def __init__(self, device_id, respuesta=None, responder_como=None,
                 callar=False):

        self.device_id = device_id
        self.respuesta = respuesta or {}
        self.responder_como = responder_como or device_id
        self.callar = callar
        self.enviados = []

    async def send_text(self, texto):

        import json

        self.enviados.append(texto)

        comando, cuerpo = texto.split(":", 1)
        peticion = json.loads(cuerpo)

        if self.callar:
            return

        kind = (
            queries.KIND_PROCESSES
            if comando == "get_processes"
            else queries.KIND_SERVICES
        )

        carga = dict(self.respuesta)
        carga["query_id"] = peticion.get("query_id")

        queries.resolve_query(self.responder_como, kind, carga)


def conectar(websocket_falso):
    main.connected_agents[websocket_falso.device_id] = websocket_falso


def desconectar_todos():
    main.connected_agents.clear()
    queries.pending_queries.clear()


def cliente(autenticado=True):
    """TestClient sobre la app real. Sin 'with': no se lanza el startup."""

    from backend.auth import create_token

    c = TestClient(main.app, base_url="https://testserver")

    if autenticado:
        c.cookies.set(COOKIE, create_token("odette"))

    return c


def acelerar_timeout(segundos=1):
    """Baja la espera máxima para no alargar las pruebas 30 segundos."""

    anterior = main.QUERY_TIMEOUT_SECONDS
    main.QUERY_TIMEOUT_SECONDS = segundos

    return anterior


preparar_dispositivos()
