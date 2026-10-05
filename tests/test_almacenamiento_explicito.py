"""
Modelo de almacenamiento: el equipo guarda sus grabaciones y solo se
archivan en el servidor cuando alguien lo pide.

    .venv\\Scripts\\python tests/test_almacenamiento_explicito.py

Ninguna prueba sube un archivo real ni toca las grabaciones de
produccion: el Agent es un WebSocket simulado y los archivos viven en una
carpeta temporal.
"""

import io
import os
import shutil
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))
sys.path.insert(0, str(RAIZ / "agent"))

import users_harness as h

import backend.database as database
import backend.main as servidor
import backend.queries as queries
import backend.recordings as recordings
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


EQUIPO = "equipo-alm"
OTRO = "equipo-alm-otro"

NOMBRE = "rec_1700000001.mp4"


class AgentFalso:
    """Agent simulado: contesta a la peticion de archivado."""

    def __init__(self, device_id=EQUIPO, respuesta=None, callar=False,
                 responder_como=None):

        self.device_id = device_id
        self.respuesta = respuesta
        self.callar = callar
        self.responder_como = responder_como or device_id
        self.enviados = []
        self.al_subir = None

    async def send_text(self, texto):

        import json

        self.enviados.append(texto)

        if ":" not in texto:
            return

        comando, cuerpo = texto.split(":", 1)

        if comando != "store_recording" or self.callar:
            return

        peticion = json.loads(cuerpo)

        # El Agent real sube por HTTPS y el servidor marca la fila. Aqui
        # se simula ese efecto para no mover bytes de verdad.
        if self.al_subir is not None:
            self.al_subir(peticion.get("filename"))

        carga = dict(self.respuesta or {"stored": True, "size_bytes": 100})
        carga["query_id"] = peticion.get("query_id")

        queries.resolve_query(self.responder_como, queries.KIND_STORE, carga)


def preparar(permisos=None):

    h.reiniciar()

    servidor.connected_agents.clear()
    queries.pending_queries.clear()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM recordings")

    for device_id in (EQUIPO, OTRO):
        conexion.execute(
            "INSERT OR IGNORE INTO devices (device_id, hostname) "
            "VALUES (?, ?)",
            (device_id, device_id.upper())
        )

    conexion.commit()
    conexion.close()

    # Los equipos recien insertados se adjuntan a la organizacion por
    # defecto, igual que hace el servidor al arrancar. Sin esto el
    # aislamiento los trata como equipos sin dueno.
    from backend import organizations

    organizations.ensure_default_organization()

    if permisos is not None:
        h.crear_subadmin("ana", permisos=permisos)


def ficha(device_id=EQUIPO, nombre=NOMBRE, started="2026-10-03T10:00:00+00:00"):
    """
    Crea la ficha de una grabacion que solo esta en el equipo.

    Se usa la misma funcion de ruta relativa que el servidor, para que la
    prueba y el producto no puedan discrepar en donde cae cada archivo.
    """

    rel_path = servidor.recording_rel_path(device_id, nombre, started)

    return recordings.register_local_recording(
        device_id=device_id,
        path=rel_path,
        started_at=started,
        ended_at=started,
        duration_sec=60,
        size_bytes=1234
    )


def fila(recording_id):
    return recordings.get_recording(recording_id)


def estado(recording_id):
    return recordings.storage_state_of(fila(recording_id))


def conectar(agente):
    servidor.connected_agents[agente.device_id] = agente
    return agente


def marcar_archivada(recording_id):
    """Simula el efecto de una subida que paso F3 y F4."""

    recordings.mark_stored(recording_id, size_bytes=4096)


# ==============================
# A-C. No hay subida automatica
# ==============================

def test_cerrar_un_segmento_no_lo_sube():

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    bloque = codigo.split("def on_segment_complete(", 1)[1]
    bloque = bloque.split("\ndef ", 1)[0]

    comprobar("Cerrar un segmento ya no lo encola para subir",
              "enqueue_pending" not in bloque)

    comprobar("Ni dispara el procesado de la cola",
              "_process_pending" not in bloque)

    comprobar("Solo manda la ficha al servidor",
              "report_recording_catalog" in bloque)


def test_la_cola_y_los_reintentos_siguen_existiendo():
    """La infraestructura de F3 no se elimina: la usa el archivado."""

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    for pieza in ("def enqueue_pending(", "def _process_pending(",
                  "def _upload_one(", "def _retry_loop(",
                  "RETRYABLE_STATUS"):
        comprobar(f"Sigue existiendo {pieza.strip('def (')}",
                  pieza in codigo)

    bloque = codigo.split("def store_recording_on_server(", 1)[1]
    bloque = bloque.split("\n# Códigos HTTP", 1)[0]

    comprobar("El archivado explicito reutiliza _upload_one",
              "_upload_one(segmento)" in bloque)

    comprobar("Y pasa por la cola, para sobrevivir a un corte",
              "enqueue_pending(segmento)" in bloque)


def test_la_grabacion_sigue_sin_servidor():
    """El grabador no depende del WebSocket ni de la subida."""

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    bloque = codigo.split("def report_recording_catalog(", 1)[1]
    bloque = bloque.split("\ndef ", 1)[0]

    comprobar("Enviar la ficha no rompe si no hay conexion",
              "send_ws_threadsafe" in bloque)

    helper = codigo.split("def send_ws_threadsafe(", 1)[1]
    helper = helper.split("\ndef ", 1)[0]

    comprobar("Sin WebSocket simplemente no se envia",
              "return False" in helper)


# ==============================
# Estados
# ==============================

def test_una_grabacion_nueva_queda_solo_local():

    preparar()

    recording_id, creada = ficha()

    comprobar("Se crea la ficha", creada is True and recording_id)

    comprobar("Con estado solo local",
              estado(recording_id) == recordings.STORAGE_LOCAL_ONLY)

    comprobar("Y sin archivo en el servidor",
              fila(recording_id)["status"] == "local")


def test_la_ficha_no_se_duplica():

    preparar()

    primero, creada1 = ficha()
    segundo, creada2 = ficha()

    comprobar("La segunda ficha no crea otra fila", primero == segundo)
    comprobar("Y se informa de que no era nueva", creada2 is False)


def test_una_ficha_repetida_no_degrada_una_archivada():

    preparar()

    recording_id, _ = ficha()
    marcar_archivada(recording_id)

    ficha()

    comprobar("Una ficha repetida no devuelve a 'solo local' lo archivado",
              estado(recording_id) == recordings.STORAGE_STORED)


def test_el_historico_sin_columna_cuenta_como_archivado():

    preparar()

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO recordings (device_id, started_at, ended_at, "
        "duration_sec, size_bytes, path, status, keep) "
        "VALUES (?, '2026-01-01', '2026-01-01', 10, 100, ?, 'stored', 0)",
        (EQUIPO, f"{EQUIPO}/2026/01/01/rec_1600000000.mp4")
    )
    conexion.commit()
    recording_id = conexion.execute(
        "SELECT id FROM recordings ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conexion.close()

    comprobar(
        "Una grabacion anterior a la columna se interpreta como archivada",
        estado(recording_id) == recordings.STORAGE_STORED
    )


def test_el_listado_incluye_las_locales():

    preparar()

    recording_id, _ = ficha()

    listado = recordings.list_recordings(EQUIPO)

    comprobar("La grabacion solo local aparece en el panel",
              any(r["id"] == recording_id for r in listado))

    comprobar("Con su estado",
              listado[0]["storage_state"] == recordings.STORAGE_LOCAL_ONLY)


# ==============================
# D-E. Guardar en servidor
# ==============================

def guardar(cliente, recording_id):
    return cliente.post(f"/api/recordings/{recording_id}/store")


def test_guardar_solicita_esa_grabacion():

    import json

    preparar()

    recording_id, _ = ficha()

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(recording_id)

    respuesta = guardar(h.cliente(h.OWNER), recording_id)

    comprobar("La peticion se acepta", respuesta.status_code == 200,
              str(respuesta.status_code))

    comprobar("Se manda un unico mensaje al equipo",
              len(agente.enviados) == 1)

    comando, cuerpo = agente.enviados[0].split(":", 1)
    peticion = json.loads(cuerpo)

    comprobar("Con el prefijo esperado", comando == "store_recording")

    comprobar("Y solo con el nombre del archivo y el identificador",
              set(peticion) == {"filename", "query_id"},
              str(sorted(peticion)))

    comprobar("El nombre es el de esa grabacion",
              peticion["filename"] == NOMBRE)

    comprobar("No viaja ninguna ruta",
              "/" not in peticion["filename"]
              and "\\\\" not in peticion["filename"])


def test_solo_se_transfiere_la_pedida():

    preparar()

    uno, _ = ficha(nombre="rec_1700000001.mp4")
    dos, _ = ficha(nombre="rec_1700000002.mp4")

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(uno)

    guardar(h.cliente(h.OWNER), uno)

    comprobar("Solo se pidio una grabacion", len(agente.enviados) == 1)

    comprobar("La otra sigue solo en el equipo",
              estado(dos) == recordings.STORAGE_LOCAL_ONLY)


def test_transferencia_correcta_termina_archivada():

    preparar()

    recording_id, _ = ficha()

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(recording_id)

    datos = guardar(h.cliente(h.OWNER), recording_id).json()

    comprobar("La respuesta confirma el archivado",
              datos.get("storage_state") == recordings.STORAGE_STORED)

    comprobar("Y la fila tambien",
              estado(recording_id) == recordings.STORAGE_STORED)

    comprobar("El mensaje aclara que la copia local se conserva",
              "se conserva" in datos.get("message", ""))


def test_si_el_agent_no_sube_no_se_marca_archivada():
    """No se da por buena una respuesta que no se puede comprobar."""

    preparar()

    recording_id, _ = ficha()

    # El Agent dice que si, pero la fila no quedo archivada
    conectar(AgentFalso(respuesta={"stored": True}))

    respuesta = guardar(h.cliente(h.OWNER), recording_id)

    comprobar("Se responde error", respuesta.status_code == 502,
              str(respuesta.status_code))

    comprobar("Y la grabacion NO queda como archivada",
              estado(recording_id) == recordings.STORAGE_ERROR)


def test_un_fallo_del_agent_deja_estado_de_error():

    preparar()

    recording_id, _ = ficha()

    conectar(AgentFalso(respuesta={"stored": False,
                                   "error": "no se pudo leer"}))

    respuesta = guardar(h.cliente(h.OWNER), recording_id)

    comprobar("Se responde 502", respuesta.status_code == 502)

    comprobar("Con el motivo", "no se pudo leer" in respuesta.text)

    comprobar("Y el estado queda en error",
              estado(recording_id) == recordings.STORAGE_ERROR)

    comprobar("Que permite volver a intentarlo",
              "server_error" in respuesta.text)


# ==============================
# F-K. Validaciones
# ==============================

def test_grabacion_inexistente():

    preparar()
    conectar(AgentFalso())

    comprobar("Una grabacion que no existe da 404",
              guardar(h.cliente(h.OWNER), 999999).status_code == 404)


def test_la_grabacion_se_pide_a_SU_equipo():
    """El device_id sale de la fila, no de quien llama."""

    preparar()

    recording_id, _ = ficha(device_id=OTRO, nombre="rec_1700000009.mp4")

    delequipo = conectar(AgentFalso(device_id=EQUIPO))
    delotro = conectar(AgentFalso(device_id=OTRO))
    delotro.al_subir = lambda nombre: marcar_archivada(recording_id)

    guardar(h.cliente(h.OWNER), recording_id)

    comprobar("La peticion va al equipo dueno de la grabacion",
              len(delotro.enviados) == 1)

    comprobar("Y el otro equipo no recibe nada",
              delequipo.enviados == [])


def test_el_cliente_no_elige_ni_equipo_ni_ruta():

    codigo = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()

    bloque = codigo.split("async def recording_store(", 1)[1]
    bloque = bloque.split("\n# ==========", 1)[0]

    comprobar("El endpoint solo recibe el identificador de la grabacion",
              "recording_id: int" in bloque and "device_id: str" not in bloque)

    comprobar("El equipo se saca de la fila",
              'grabacion["device_id"]' in bloque)

    comprobar("Y el nombre tambien, quedandose solo con el basename",
              '_os.path.basename(grabacion["path"]' in bloque)

    comprobar("No se lee ninguna ruta del cuerpo",
              '"path"' not in bloque.split("def auditar", 1)[0]
              or "data.get" not in bloque)


def test_path_traversal_en_el_agent():
    """
    El guardian que decide si un archivo es una grabacion administrada.

    find_managed_recording() nunca une lo que llega con una ruta: se queda
    con el nombre y recorre su propia carpeta. La barrera final es
    is_managed_recording(), que se comprueba aqui directamente con rutas
    maliciosas reales.
    """

    import storage as agent_storage
    import paths as agent_paths

    temporal = Path(tempfile.mkdtemp(prefix="alm_traversal_"))
    base = temporal / "recordings"
    base.mkdir(parents=True, exist_ok=True)

    victima = temporal / "rec_1700000000.mp4"
    victima.write_bytes(b"no tocar")

    original_paths = agent_paths.get_recordings_dir
    original_storage = agent_storage.get_recordings_dir

    agent_paths.get_recordings_dir = lambda: str(base)
    agent_storage.get_recordings_dir = lambda: str(base)

    try:

        maliciosas = [
            str(base / ".." / "rec_1700000000.mp4"),
            str(base / ".." / ".." / "rec_1700000000.mp4"),
            "C:\Windows\win.ini",
            "/etc/passwd",
            str(temporal / "rec_1700000000.mp4")
        ]

        aceptadas = [
            r for r in maliciosas
            if agent_storage.is_managed_recording(r)
        ]

        comprobar("Ninguna ruta de fuera se considera administrada",
                  not aceptadas, str(aceptadas))

        comprobar("Y el archivo de fuera sigue intacto", victima.exists())

        # Una de dentro, con el nombre correcto, si pasa
        dentro = base / "rec_1700000001.mp4"
        dentro.write_bytes(b"x")

        comprobar("Una grabacion real de la carpeta si se reconoce",
                  agent_storage.is_managed_recording(str(dentro)))

    finally:
        agent_paths.get_recordings_dir = original_paths
        agent_storage.get_recordings_dir = original_storage
        shutil.rmtree(temporal, ignore_errors=True)


def test_el_agent_solo_busca_en_su_carpeta():

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    bloque = codigo.split("def find_managed_recording(", 1)[1]
    bloque = bloque.split("\ndef ", 1)[0]

    comprobar("Se queda solo con el nombre del archivo",
              "os.path.basename" in bloque)

    comprobar("Recorre su propia carpeta administrada",
              "get_recordings_dir()" in bloque)

    comprobar("Y comprueba la contencion real antes de devolver nada",
              "storage.is_managed_recording" in bloque)

    comprobar("No hay comandos ni shell",
              "subprocess" not in bloque and "os.system" not in bloque
              and "powershell" not in bloque.lower())


# ==============================
# L. Equipo desconectado
# ==============================

def test_equipo_offline():

    preparar()

    recording_id, _ = ficha()

    respuesta = guardar(h.cliente(h.OWNER), recording_id)

    comprobar("Con el equipo offline se responde 409",
              respuesta.status_code == 409, str(respuesta.status_code))

    comprobar("Se dice claramente que esta desconectado",
              "desconectado" in respuesta.json().get("message", "").lower())

    comprobar("NO se marca como archivada",
              estado(recording_id) == recordings.STORAGE_LOCAL_ONLY)

    comprobar("Ni se deja en 'guardando': no hay cola de solicitudes",
              estado(recording_id) != recordings.STORAGE_PENDING)


def test_se_puede_reintentar_al_volver():

    preparar()

    recording_id, _ = ficha()

    guardar(h.cliente(h.OWNER), recording_id)

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(recording_id)

    comprobar("Al conectarse el equipo, el reintento funciona",
              guardar(h.cliente(h.OWNER), recording_id).status_code == 200)


def test_sin_respuesta_del_equipo():

    preparar()

    recording_id, _ = ficha()
    conectar(AgentFalso(callar=True))

    anterior = servidor.STORE_TIMEOUT_SECONDS
    servidor.STORE_TIMEOUT_SECONDS = 1

    try:
        respuesta = guardar(h.cliente(h.OWNER), recording_id)
    finally:
        servidor.STORE_TIMEOUT_SECONDS = anterior

    comprobar("Sin respuesta se devuelve 504",
              respuesta.status_code == 504, str(respuesta.status_code))

    comprobar("Y queda en error, no archivada",
              estado(recording_id) == recordings.STORAGE_ERROR)


def test_otro_agent_no_puede_confirmar():

    preparar()

    recording_id, _ = ficha()
    conectar(AgentFalso(responder_como=OTRO))

    anterior = servidor.STORE_TIMEOUT_SECONDS
    servidor.STORE_TIMEOUT_SECONDS = 1

    try:
        respuesta = guardar(h.cliente(h.OWNER), recording_id)
    finally:
        servidor.STORE_TIMEOUT_SECONDS = anterior

    comprobar("Una confirmacion de otro equipo no vale",
              respuesta.status_code == 504)

    comprobar("Y la grabacion no queda archivada",
              estado(recording_id) != recordings.STORAGE_STORED)


# ==============================
# P-R. Duplicados e idempotencia
# ==============================

def test_guardar_dos_veces_es_idempotente():

    preparar()

    recording_id, _ = ficha()

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(recording_id)

    cliente = h.cliente(h.OWNER)

    primera = guardar(cliente, recording_id)
    segunda = guardar(cliente, recording_id)

    comprobar("La primera archiva", primera.status_code == 200)

    comprobar("La segunda responde correctamente",
              segunda.status_code == 200)

    comprobar("Avisando de que ya estaba",
              segunda.json().get("already_stored") is True)

    comprobar("Y NO se vuelve a pedir el archivo al equipo",
              len(agente.enviados) == 1, str(len(agente.enviados)))


def test_no_se_crean_dos_filas():

    preparar()

    recording_id, _ = ficha()

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(recording_id)

    cliente = h.cliente(h.OWNER)

    for _ in range(4):
        guardar(cliente, recording_id)

    conexion = database.get_connection()
    total = conexion.execute(
        "SELECT COUNT(*) FROM recordings WHERE device_id = ?",
        (EQUIPO,)
    ).fetchone()[0]
    conexion.close()

    comprobar("Cuatro peticiones no crean copias", total == 1, str(total))


def test_mientras_se_guarda_no_arranca_otra():

    preparar()

    recording_id, _ = ficha()

    recordings.set_storage_state(recording_id, recordings.STORAGE_PENDING)

    agente = conectar(AgentFalso())

    respuesta = guardar(h.cliente(h.OWNER), recording_id)

    comprobar("Una segunda peticion simultanea se rechaza con 409",
              respuesta.status_code == 409, str(respuesta.status_code))

    comprobar("Y no se manda nada al equipo", agente.enviados == [])


def test_el_indice_unico_sigue_protegiendo():

    preparar()

    ficha()

    conexion = database.get_connection()

    try:
        conexion.execute(
            "INSERT INTO recordings (device_id, started_at, ended_at, "
            "duration_sec, size_bytes, path, status, keep) "
            "VALUES (?, '', '', 0, 0, ?, 'stored', 0)",
            (EQUIPO, f"{EQUIPO}/2026/10/03/{NOMBRE}")
        )
        conexion.commit()
        colo = True

    except Exception:
        colo = False

    finally:
        conexion.close()

    comprobar("El indice unico (device_id, path) impide la fila repetida",
              not colo)


# ==============================
# S-U. Copia local y retencion
# ==============================

def test_la_copia_local_no_se_borra_al_archivar():

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    bloque = codigo.split("def store_recording_on_server(", 1)[1]
    bloque = bloque.split("\n# Códigos HTTP", 1)[0]

    comprobar("Archivar no borra el archivo local",
              "delete_recording_file" not in bloque
              and "os.remove" not in bloque)

    comprobar("Solo se retira de la cola de pendientes",
              "_remove_pending(ruta)" in bloque)

    comprobar("Y se dice por que",
              "retencion local" in bloque.lower())


def test_la_retencion_local_sigue_intacta():

    import storage as agent_storage

    comprobar("La retencion local sigue existiendo",
              callable(agent_storage.apply_local_retention))

    comprobar("Con sus protecciones",
              callable(agent_storage.is_managed_recording)
              and callable(agent_storage.delete_recording_file))

    codigo = io.open(RAIZ / "agent" / "storage.py",
                     encoding="utf-8").read()

    bloque = codigo.split("def apply_local_retention(", 1)[1]
    bloque = bloque.split("\ndef ", 1)[0]

    for regla in ("is_managed_recording", "is_protected", "is_pending",
                  "keep_names"):
        comprobar(f"La retencion sigue comprobando {regla}",
                  regla in bloque)

    comprobar("Archivar en el servidor no la hace inmune",
              "storage_state" not in bloque and "server" not in bloque)


def test_keep_sigue_funcionando():

    preparar()

    recording_id, _ = ficha()

    comprobar("Se puede marcar como conservada",
              recordings.set_keep(recording_id, 1))

    comprobar("Y queda marcada", fila(recording_id)["keep"] == 1)

    comprobar("Se puede desmarcar",
              recordings.set_keep(recording_id, 0)
              and fila(recording_id)["keep"] == 0)


def test_la_retencion_del_servidor_no_toca_las_locales():

    preparar()

    recording_id, _ = ficha()

    conexion = database.get_connection()
    filas = conexion.execute(
        "SELECT COUNT(*) FROM recordings WHERE keep = 0 AND status = 'stored'"
    ).fetchone()[0]
    conexion.close()

    comprobar(
        "Una grabacion solo local queda fuera de la retencion del servidor",
        filas == 0, str(filas)
    )


# ==============================
# Ver y descargar
# ==============================

def test_no_se_puede_ver_ni_descargar_lo_que_no_esta():

    preparar()

    recording_id, _ = ficha()

    cliente = h.cliente(h.OWNER)

    ver = cliente.get(f"/api/recordings/{recording_id}/video")
    descargar = cliente.get(f"/api/recordings/{recording_id}/download")

    comprobar("Ver una grabacion solo local da 409",
              ver.status_code == 409, str(ver.status_code))

    comprobar("Descargarla tambien",
              descargar.status_code == 409, str(descargar.status_code))

    comprobar("Con una explicacion util",
              "solo esta en el equipo" in ver.text)


# ==============================
# V-Y. Permisos y sesion
# ==============================

def test_sin_permiso():

    preparar(permisos=["recordings.view"])

    recording_id, _ = ficha()
    agente = conectar(AgentFalso())

    respuesta = guardar(h.cliente("ana"), recording_id)

    comprobar("Un usuario sin recordings.manage recibe 403",
              respuesta.status_code == 403, str(respuesta.status_code))

    comprobar("Y no se manda nada al equipo", agente.enviados == [])

    comprobar("La grabacion sigue solo local",
              estado(recording_id) == recordings.STORAGE_LOCAL_ONLY)


def test_subadmin_con_permiso():

    preparar(permisos=["recordings.manage"])

    recording_id, _ = ficha()

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(recording_id)

    comprobar("Un subadmin con recordings.manage si puede",
              guardar(h.cliente("ana"), recording_id).status_code == 200)


def test_owner():

    preparar()

    recording_id, _ = ficha()

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(recording_id)

    comprobar("El Owner puede",
              guardar(h.cliente(h.OWNER), recording_id).status_code == 200)


def test_sin_sesion_y_sesion_revocada():

    preparar()

    recording_id, _ = ficha()
    conectar(AgentFalso())

    comprobar("Sin sesion se responde 401",
              guardar(h.cliente(), recording_id).status_code == 401)

    cliente = h.cliente(h.OWNER)
    cliente.post("/api/auth/logout")

    comprobar("Con la sesion cerrada tambien",
              guardar(cliente, recording_id).status_code == 401)


def test_usuario_desactivado():

    preparar(permisos=["recordings.manage"])

    recording_id, _ = ficha()
    conectar(AgentFalso())

    sesion = h.cliente("ana")

    users.set_active("ana", False)

    comprobar("Un usuario desactivado no puede archivar",
              guardar(sesion, recording_id).status_code in (401, 403))


# ==============================
# Z. Auditoria
# ==============================

def test_auditoria():

    preparar()

    recording_id, _ = ficha()

    agente = conectar(AgentFalso())
    agente.al_subir = lambda nombre: marcar_archivada(recording_id)

    guardar(h.cliente(h.OWNER), recording_id)

    filas = h.registros(action="recording.store")

    comprobar("El archivado queda auditado", len(filas) >= 1)

    if filas:

        registro = filas[0]

        comprobar("Como exito", registro["status"] == "success")
        comprobar("Con quien lo pidio", registro["username"] == h.OWNER)
        comprobar("Con el equipo", registro["device_id"] == EQUIPO)
        comprobar("Y con la grabacion",
                  str(recording_id) in (registro["details"] or ""))


def test_auditoria_de_errores():

    preparar()

    recording_id, _ = ficha()

    # Sin equipo conectado
    guardar(h.cliente(h.OWNER), recording_id)

    filas = h.registros(action="recording.store")

    comprobar("El fallo queda auditado",
              any(r["status"] == "error" for r in filas))

    comprobar("Con el motivo",
              any("conectado" in (r["details"] or "") for r in filas))


def test_la_auditoria_no_guarda_nada_sensible():

    preparar(permisos=[])

    recording_id, _ = ficha()
    conectar(AgentFalso())

    guardar(h.cliente("ana"), recording_id)

    todo = " ".join(str(r["details"]) for r in h.registros())

    for secreto in (h.CLAVE_OWNER, h.CLAVE_SUBADMIN, "pbkdf2_sha256",
                    "ProgramData", "C:\\\\"):
        comprobar(f"La auditoria no guarda {secreto[:14]}",
                  secreto not in todo)


# ==============================
# AA. Frontend
# ==============================

def test_frontend():

    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

    for clave in ("local_only", "server_pending", "server_stored",
                  "server_error"):
        comprobar(f"El panel conoce el estado {clave}",
                  f"{clave}: {{" in js)

    comprobar("Hay una insignia por estado",
              "insigniaDeAlmacenamiento" in js)

    comprobar("Y la accion de guardar",
              "guardarEnServidor" in js)

    comprobar("El boton dice claramente que guarda en el SERVIDOR",
              "Guardar en servidor" in js)

    comprobar("Y se distingue de descargar",
              "Descargar" in js and "/download" in js)

    comprobar("El boton respeta el permiso",
              'puede("recordings.manage")' in js)

    comprobar("Solo aparece si se puede guardar",
              "sePuedeGuardar" in js)

    comprobar("Se evita el doble clic",
              "guardadosEnCurso" in js)

    comprobar("La lista se recarga al terminar",
              "loadRecordings()" in js)

    comprobar("No se ensenan rutas internas",
              "ProgramData" not in js and "server_recordings" not in js)


# ==============================
# Produccion
# ==============================

def test_no_se_toco_produccion():

    reales = RAIZ / "server_recordings"

    comprobar("Esta suite usa una base temporal",
              "usuarios_" in str(database.DATABASE_PATH)
              or "prueba" in str(database.DATABASE_PATH),
              str(database.DATABASE_PATH))

    comprobar("Y equipos inventados", EQUIPO.startswith("equipo-alm"))

    if reales.exists():
        comprobar("Las grabaciones del servidor siguen donde estaban",
                  len(list(reales.rglob("*.mp4"))) >= 1)


# ==============================

def main():

    pruebas = [
        test_cerrar_un_segmento_no_lo_sube,
        test_la_cola_y_los_reintentos_siguen_existiendo,
        test_la_grabacion_sigue_sin_servidor,
        test_una_grabacion_nueva_queda_solo_local,
        test_la_ficha_no_se_duplica,
        test_una_ficha_repetida_no_degrada_una_archivada,
        test_el_historico_sin_columna_cuenta_como_archivado,
        test_el_listado_incluye_las_locales,
        test_guardar_solicita_esa_grabacion,
        test_solo_se_transfiere_la_pedida,
        test_transferencia_correcta_termina_archivada,
        test_si_el_agent_no_sube_no_se_marca_archivada,
        test_un_fallo_del_agent_deja_estado_de_error,
        test_grabacion_inexistente,
        test_la_grabacion_se_pide_a_SU_equipo,
        test_el_cliente_no_elige_ni_equipo_ni_ruta,
        test_path_traversal_en_el_agent,
        test_el_agent_solo_busca_en_su_carpeta,
        test_equipo_offline,
        test_se_puede_reintentar_al_volver,
        test_sin_respuesta_del_equipo,
        test_otro_agent_no_puede_confirmar,
        test_guardar_dos_veces_es_idempotente,
        test_no_se_crean_dos_filas,
        test_mientras_se_guarda_no_arranca_otra,
        test_el_indice_unico_sigue_protegiendo,
        test_la_copia_local_no_se_borra_al_archivar,
        test_la_retencion_local_sigue_intacta,
        test_keep_sigue_funcionando,
        test_la_retencion_del_servidor_no_toca_las_locales,
        test_no_se_puede_ver_ni_descargar_lo_que_no_esta,
        test_sin_permiso,
        test_subadmin_con_permiso,
        test_owner,
        test_sin_sesion_y_sesion_revocada,
        test_usuario_desactivado,
        test_auditoria,
        test_auditoria_de_errores,
        test_la_auditoria_no_guarda_nada_sensible,
        test_frontend,
        test_no_se_toco_produccion
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:70])

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLO"
        print(f"[{marca}] {nombre}" + (f"  -> {detalle}" if not ok else ""))
        if not ok:
            fallos += 1

    print(f"\n{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
