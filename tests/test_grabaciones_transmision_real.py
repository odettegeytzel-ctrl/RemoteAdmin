"""
Transmision local con MP4 REALES y el codigo REAL del Agent.

    .venv\\Scripts\\python tests/test_grabaciones_transmision_real.py

Que tiene de distinto esto respecto a test_grabaciones_paginadas.py:
alli el Agent era un simulacro escrito para la prueba. Aqui se
importa agent/agent.py y se ejecutan sus funciones de verdad
—handle_recording_stream_message(), _enviar_grabacion(),
find_managed_recording(), storage.is_managed_recording()— sobre
archivos MP4 generados con ffmpeg y leidos del disco de esta maquina
Windows.

QUE PRUEBA ESTO
  - El codigo real del Agent, en Windows real, leyendo MP4 reales.
  - Los dos ordenes posibles del indice de un MP4: 'moov' al
    principio (faststart) y 'moov' al final, que es lo que produce
    ffmpeg por defecto y lo que obliga al reproductor a leer el
    final antes de poder empezar.
  - El camino completo peticion -> WebSocket -> lectura -> respuesta.

QUE NO PRUEBA, Y NO SE PUEDE AFIRMAR A PARTIR DE AQUI
  - El servicio RemoteAdminAgent instalado en un equipo.
  - Un WebSocket de verdad por la red, con su latencia y sus cortes.
  - El reproductor de un navegador.
Eso sigue necesitando una prueba en un equipo Windows registrado.

AISLAMIENTO
  La variable ProgramData se cambia a una carpeta temporal ANTES de
  importar el Agent, que es de donde saca get_recordings_dir(). El
  Agent instalado en esta maquina, su identidad, su configuracion y
  sus grabaciones no se tocan ni se leen.
"""

import asyncio
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==============================
# AISLAMIENTO: antes de importar nada del Agent
# ==============================

CARPETA = tempfile.mkdtemp(prefix="veyra_transmision_")

os.environ["ProgramData"] = CARPETA


def cargar_agente():
    """
    Importa agent/agent.py de verdad.

    No vale 'import agent.agent': el modulo hace importaciones planas
    ('from recorder import ...') porque dentro del .exe vive sin
    paquete. Se carga por ruta, con agent/ en sys.path para que esas
    importaciones planas se resuelvan.
    """

    sys.path.insert(0, str(RAIZ / "agent"))

    spec = importlib.util.spec_from_file_location(
        "agente_real", RAIZ / "agent" / "agent.py"
    )

    modulo = importlib.util.module_from_spec(spec)
    sys.modules["agente_real"] = modulo
    spec.loader.exec_module(modulo)

    return modulo


AGENTE = cargar_agente()

# El Agent manda bloques de 256 KB. Los MP4 de prueba pesan unos 46 KB,
# asi que con el tamano real caben en UN bloque y el recorrido de
# varios —que es donde viven la cancelacion a media transmision y la
# desconexion a medias— no se ejercitaria nunca. Se baja el bloque
# para que un archivo pequeno produzca muchos, que es exactamente lo
# que pasa con una grabacion de verdad y el bloque de verdad.
AGENTE.DOWNLOAD_CHUNK_SIZE = 4096

import backend.database as database
import backend.main as servidor
import backend.media as media
import backend.organizations as orgs
import backend.recordings as grabaciones
import backend.users as users

import users_harness as h


CLAVE = "ClaveTransmisionReal2026!"

DIRECTORIO = AGENTE.get_recordings_dir()


# ==============================
# MP4 REALES
# ==============================

def generar_mp4(destino, faststart):
    """
    Un MP4 de verdad, hecho con ffmpeg.

    Dos segundos de una carta de ajuste y un tono: ninguna captura de
    pantalla, ningun dato de nadie. Con faststart el indice 'moov'
    queda al principio; sin el, ffmpeg lo escribe al final.
    """

    ffmpeg = media.get_ffmpeg_path()

    if not ffmpeg or not os.path.isfile(ffmpeg):
        return False

    orden = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac"
    ]

    if faststart:
        orden += ["-movflags", "+faststart"]

    orden.append(str(destino))

    try:
        proceso = subprocess.run(
            orden, capture_output=True, timeout=180
        )

    except (subprocess.TimeoutExpired, OSError):
        return False

    return proceso.returncode == 0 and os.path.isfile(destino)


def donde_esta_moov(ruta):
    """
    'inicio' o 'final', mirando los atomos de nivel superior del MP4.

    Se recorre la cabecera real del archivo: cada atomo lleva delante
    su tamano en 4 bytes y su nombre en otros 4.
    """

    orden = []

    with open(ruta, "rb") as manejador:

        total = os.path.getsize(ruta)
        posicion = 0

        while posicion < total and len(orden) < 20:

            manejador.seek(posicion)
            cabecera = manejador.read(8)

            if len(cabecera) < 8:
                break

            tamano = int.from_bytes(cabecera[:4], "big")
            nombre = cabecera[4:8].decode("ascii", "replace")

            orden.append(nombre)

            if tamano == 1:
                tamano = int.from_bytes(manejador.read(8), "big")

            if tamano <= 0:
                break

            posicion += tamano

    if "moov" not in orden or "mdat" not in orden:
        return "desconocido"

    return (
        "inicio" if orden.index("moov") < orden.index("mdat")
        else "final"
    )


VIDEOS = {}


def preparar_videos():

    for nombre, faststart in (
        ("rec_1000000001.mp4", True),
        ("rec_1000000002.mp4", False)
    ):
        ruta = os.path.join(DIRECTORIO, nombre)

        if generar_mp4(ruta, faststart):
            VIDEOS[nombre] = {
                "ruta": ruta,
                "bytes": open(ruta, "rb").read(),
                "moov": donde_esta_moov(ruta)
            }

    return VIDEOS


# ==============================
# PUENTE: el Agent real contra el backend real
# ==============================

class PuenteAlAgente:
    """
    Lo que el backend cree que es el WebSocket del equipo.

    Cada send_text() entra en el manejador REAL del Agent. Lo que el
    Agent responde se devuelve al backend por sus propias funciones:
    resolve_recording_stream() para el texto y feed_download_chunk()
    para los bloques binarios. Lo unico que no es real es el cable.
    """

    def __init__(self, retraso=0.0, cortar_tras=None):
        self.retraso = retraso
        self.cortar_tras = cortar_tras
        self.bloques = 0
        self.cortado = False
        self.recibidos = []
        self.tareas = []

    # --- lo que usa el backend ---

    async def send_text(self, mensaje):

        self.recibidos.append(mensaje)

        await AGENTE.handle_recording_stream_message(self, mensaje)

        # El Agent lanza el envio en una tarea de fondo. Se recogen
        # para poder esperarlas al final y no dejar nada suelto.
        for tarea in list(asyncio.all_tasks()):
            if tarea not in self.tareas and tarea.get_name().startswith(
                "Task"
            ):
                self.tareas.append(tarea)

    # --- lo que usa el Agent ---

    async def send(self, mensaje):

        if self.cortado:
            raise ConnectionError("conexion cerrada")

        if isinstance(mensaje, bytes):

            self.bloques += 1

            if self.retraso:
                await asyncio.sleep(self.retraso)

            if (self.cortar_tras is not None
                    and self.bloques > self.cortar_tras):

                # Simula que el equipo desaparece a media
                # transmision: deja de llegar nada.
                self.cortado = True
                servidor.fail_device_recording_streams("equipo-real")
                raise ConnectionError("el equipo se desconecto")

            servidor.feed_download_chunk(mensaje)
            return

        servidor.resolve_recording_stream(mensaje)


def con_puente(puente):

    class Contexto:

        def __enter__(self):
            servidor.connected_agents["equipo-real"] = puente
            servidor.agent_capabilities["equipo-real"] = {
                "version": "1.4.0",
                "capabilities": ["recording_stream"]
            }
            return puente

        def __exit__(self, *_):
            servidor.connected_agents.pop("equipo-real", None)
            servidor.agent_capabilities.pop("equipo-real", None)
            servidor.recording_streams.clear()

    return Contexto()


# ==============================
# ESCENARIO
# ==============================

fichas = {}


def preparar():

    h.reiniciar()

    conexion = database.get_connection()

    for tabla in ("recordings", "devices", "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")

    conexion.commit()
    conexion.close()

    empresa = orgs.create_organization(
        "Real", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, empresa["id"])

    conexion = database.get_connection()

    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('equipo-real', 'PC-REAL', ?)", (empresa["id"],)
    )

    fichas.clear()

    for nombre, datos in VIDEOS.items():

        cursor = conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, "
            "storage_state, keep) VALUES ('equipo-real', ?, "
            "'2026-10-28T10:00:00+00:00', '2026-10-28T10:00:02+00:00', "
            "2, ?, 'local', 'local_only', 0)",
            (f"equipo-real/2026/10/28/{nombre}", len(datos["bytes"]))
        )

        fichas[nombre] = cursor.lastrowid

    # Una que la base conoce pero que no existe en el disco.
    cursor = conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, "
        "ended_at, duration_sec, size_bytes, status, storage_state, "
        "keep) VALUES ('equipo-real', "
        "'equipo-real/2026/10/28/rec_9999999999.mp4', "
        "'2026-10-28T11:00:00+00:00', '2026-10-28T11:00:02+00:00', "
        "2, 1234, 'local', 'local_only', 0)"
    )

    fichas["fantasma"] = cursor.lastrowid

    conexion.commit()
    conexion.close()


def pedir(recording_id, cabeceras=None, puente=None):

    with con_puente(puente or PuenteAlAgente()):

        return h.cliente(h.OWNER).get(
            f"/api/recordings/{recording_id}/local-video",
            headers=cabeceras or {}
        )


# ==============================
# PRUEBAS
# ==============================

def test_los_mp4_son_de_verdad():

    comprobar("ffmpeg genero los dos MP4", len(VIDEOS) == 2,
              f"{len(VIDEOS)} generados")

    for nombre, datos in VIDEOS.items():

        comprobar(f"{nombre} es un MP4 valido",
                  media.has_ftyp_header(datos["ruta"]),
                  "sin cabecera ftyp")

        comprobar(f"{nombre} pesa algo", len(datos["bytes"]) > 1000,
                  f"{len(datos['bytes'])} bytes")

    rapido = VIDEOS.get("rec_1000000001.mp4", {}).get("moov")
    lento = VIDEOS.get("rec_1000000002.mp4", {}).get("moov")

    comprobar("Uno tiene el indice al principio (faststart)",
              rapido == "inicio", str(rapido))

    comprobar("Y el otro al final, que es el caso dificil",
              lento == "final", str(lento))


def test_el_video_entero_llega_identico():

    preparar()

    for nombre, datos in VIDEOS.items():

        respuesta = pedir(fichas[nombre])

        comprobar(f"{nombre}: se entrega",
                  respuesta.status_code == 200,
                  str(respuesta.status_code))

        comprobar(f"{nombre}: byte a byte igual al del disco",
                  respuesta.content == datos["bytes"],
                  f"{len(respuesta.content)} de {len(datos['bytes'])}")

        comprobar(f"{nombre}: la longitud declarada es la real",
                  respuesta.headers.get("content-length")
                  == str(len(datos["bytes"])),
                  str(respuesta.headers.get("content-length")))


def test_el_moov_al_final_se_puede_leer():
    """
    El caso que preocupaba: un MP4 cuyo indice esta al final.

    El reproductor pide primero el final del archivo para encontrar
    el 'moov'. Eso es una peticion Range por el final, 'bytes=-N'.
    """

    preparar()

    nombre = "rec_1000000002.mp4"
    datos = VIDEOS[nombre]

    # Lo que hace un reproductor real: leer el final para buscar el
    # indice. Si no lo encuentra en el primer mordisco, pide mas. Se
    # reproduce ese tanteo hasta dar con el 'moov'.
    encontrado = False
    sufijo = 0

    for sufijo in (2048, 8192, 32768):

        respuesta = pedir(fichas[nombre], {"Range": f"bytes=-{sufijo}"})

        esperado = datos["bytes"][-sufijo:]

        comprobar(f"Pedir los ultimos {sufijo} bytes funciona",
                  respuesta.status_code == 206
                  and respuesta.content == esperado,
                  f"{respuesta.status_code}, {len(respuesta.content)} bytes")

        comprobar(f"El rango de -{sufijo} se declara bien",
                  respuesta.headers.get("content-range")
                  == f"bytes {len(datos['bytes']) - sufijo}-"
                     f"{len(datos['bytes']) - 1}/{len(datos['bytes'])}",
                  str(respuesta.headers.get("content-range")))

        if b"moov" in respuesta.content:
            encontrado = True
            break

    comprobar("Leyendo el final se acaba encontrando el indice 'moov'",
              encontrado, f"no aparecio ni en los ultimos {sufijo} bytes")


def test_saltar_adelante_y_atras():

    preparar()

    nombre = "rec_1000000001.mp4"
    datos = VIDEOS[nombre]
    total = len(datos["bytes"])

    medio = total // 2

    adelante = pedir(fichas[nombre], {"Range": f"bytes={medio}-{medio + 999}"})

    comprobar("Saltar al medio devuelve 206",
              adelante.status_code == 206, str(adelante.status_code))

    comprobar("Y el trozo es el correcto",
              adelante.content == datos["bytes"][medio:medio + 1000],
              f"{len(adelante.content)} bytes")

    atras = pedir(fichas[nombre], {"Range": "bytes=0-999"})

    comprobar("Volver al principio tambien",
              atras.status_code == 206 and
              atras.content == datos["bytes"][:1000],
              f"{atras.status_code}, {len(atras.content)} bytes")

    hasta_el_final = pedir(
        fichas[nombre], {"Range": f"bytes={total - 100}-"}
    )

    comprobar("Un rango abierto llega hasta el final",
              hasta_el_final.content == datos["bytes"][-100:],
              f"{len(hasta_el_final.content)} bytes")


def test_un_rango_imposible_da_416():

    preparar()

    nombre = "rec_1000000001.mp4"
    total = len(VIDEOS[nombre]["bytes"])

    respuesta = pedir(
        fichas[nombre],
        {"Range": f"bytes={total + 5000}-{total + 6000}"}
    )

    comprobar("Un trozo fuera del archivo da 416, no 502",
              respuesta.status_code == 416,
              str(respuesta.status_code))

    comprobar("Y dice cual es el tamano real",
              respuesta.headers.get("content-range") == f"bytes */{total}",
              str(respuesta.headers.get("content-range")))


def test_una_sola_ida_y_vuelta_por_peticion():
    """
    Un video se reproduce a base de decenas de peticiones Range.

    Si cada una costara dos viajes al equipo, el coste se duplicaria
    en cada salto del reproductor.
    """

    preparar()

    puente = PuenteAlAgente()

    with con_puente(puente):

        h.cliente(h.OWNER).get(
            f"/api/recordings/{fichas['rec_1000000001.mp4']}/local-video",
            headers={"Range": "bytes=100-199"}
        )

    arranques = [
        m for m in puente.recibidos
        if m.startswith("recording_stream_start:")
    ]

    comprobar("Una peticion Range pide el trozo una sola vez",
              len(arranques) == 1, f"{len(arranques)} peticiones")


def test_un_archivo_que_no_existe():

    preparar()

    respuesta = pedir(fichas["fantasma"])

    comprobar("Un archivo que ya no esta no da 200",
              respuesta.status_code == 502,
              str(respuesta.status_code))

    comprobar("Y se explica, sin inventar un video",
              "no esta" in respuesta.text.lower(),
              respuesta.text[:100])


def test_cerrar_el_reproductor_detiene_la_lectura():
    """
    El navegador corta la conexion. El equipo debe dejar de leer.
    """

    preparar()

    puente = PuenteAlAgente()

    with con_puente(puente):

        h.cliente(h.OWNER).get(
            f"/api/recordings/{fichas['rec_1000000001.mp4']}/local-video"
        )

    abortos = [
        m for m in puente.recibidos
        if m.startswith("recording_stream_abort:")
    ]

    comprobar("Al terminar se le dice al equipo que pare",
              len(abortos) >= 1, str(len(abortos)))

    comprobar("Y el Agent no deja la transmision en su registro",
              not AGENTE.recording_streams,
              str(list(AGENTE.recording_streams)))

    comprobar("Ni el servidor en el suyo",
              not servidor.recording_streams,
              str(list(servidor.recording_streams)))


def test_la_cancelacion_llega_mientras_se_envia():
    """
    Lo que fallaba si el envio corriera dentro del bucle de mensajes.

    Se manda un abort con el envio a medias y se comprueba que el
    Agent se entera, es decir, que seguia escuchando.
    """

    preparar()

    async def escenario():

        puente = PuenteAlAgente(retraso=0.05)

        servidor.connected_agents["equipo-real"] = puente

        transfer_id = "cancelacionamediotransmision0001"

        bucle = asyncio.get_running_loop()

        servidor.recording_streams[transfer_id] = {
            "device_id": "equipo-real",
            "meta": bucle.create_future(),
            "queue": asyncio.Queue()
        }

        await AGENTE.handle_recording_stream_message(
            puente,
            "recording_stream_start:" + json.dumps({
                "transfer_id": transfer_id,
                "filename": "rec_1000000001.mp4",
                "offset": 0,
                "length": None
            })
        )

        # El envio esta en marcha en su propia tarea.
        await asyncio.sleep(0.08)

        enviados_antes = puente.bloques
        escuchaba = transfer_id in AGENTE.recording_streams

        await AGENTE.handle_recording_stream_message(
            puente,
            "recording_stream_abort:" + json.dumps(
                {"transfer_id": transfer_id}
            )
        )

        await asyncio.sleep(0.2)

        servidor.connected_agents.pop("equipo-real", None)
        servidor.recording_streams.clear()

        return escuchaba, enviados_antes, puente.bloques

    escuchaba, antes, despues = asyncio.run(escenario())

    comprobar("El Agent sigue atendiendo mensajes mientras transmite",
              escuchaba, "la transmision no estaba registrada")

    comprobar("Y la cancelacion detiene el envio",
              despues - antes <= 2,
              f"siguio mandando {despues - antes} bloques tras el aborto")


def test_una_conexion_lenta_no_rompe_nada():

    preparar()

    nombre = "rec_1000000001.mp4"
    datos = VIDEOS[nombre]

    puente = PuenteAlAgente(retraso=0.02)

    comenzado = time.time()

    respuesta = pedir(fichas[nombre], puente=puente)

    tardanza = time.time() - comenzado

    comprobar("Con la conexion lenta el video llega igual",
              respuesta.content == datos["bytes"],
              f"{len(respuesta.content)} de {len(datos['bytes'])}")

    comprobar("Y de verdad fue mas lento",
              tardanza > 0.02 * max(1, puente.bloques - 1),
              f"{tardanza:.2f}s en {puente.bloques} bloques")


def test_el_equipo_se_cae_a_media_transmision():

    preparar()

    nombre = "rec_1000000002.mp4"
    datos = VIDEOS[nombre]

    puente = PuenteAlAgente(cortar_tras=1)

    respuesta = pedir(fichas[nombre], puente=puente)

    comprobar("Una desconexion a medias no entrega el video completo",
              len(respuesta.content) < len(datos["bytes"]),
              f"{len(respuesta.content)} de {len(datos['bytes'])}")

    comprobar("Y no queda ninguna transmision colgada en el servidor",
              not servidor.recording_streams,
              str(list(servidor.recording_streams)))


def test_el_agente_no_sale_de_su_carpeta():
    """
    Con el Agent de verdad y rutas reales de esta maquina.
    """

    preparar()

    fuera = os.path.join(CARPETA, "rec_7777777777.mp4")

    with open(fuera, "wb") as manejador:
        manejador.write(b"esto no es una grabacion administrada")

    comprobar("Un MP4 fuera de la carpeta no es administrado",
              not AGENTE.storage.is_managed_recording(fuera),
              fuera)

    for intento in (
        "../rec_7777777777.mp4",
        "..\\..\\rec_7777777777.mp4",
        os.path.join(CARPETA, "rec_7777777777.mp4"),
        "C:\\Windows\\System32\\config\\SAM",
        "rec_0000000000.mp4"
    ):
        comprobar(f"No se resuelve '{intento[:34]}'",
                  AGENTE.find_managed_recording(intento) is None,
                  str(AGENTE.find_managed_recording(intento)))

    # Y el nombre bueno si.
    comprobar("El nombre legitimo si se encuentra",
              AGENTE.find_managed_recording("rec_1000000001.mp4")
              is not None,
              "no se encontro la grabacion real")


def test_reproducir_no_sube_ni_copia():

    preparar()

    nombre = "rec_1000000001.mp4"
    recording_id = fichas[nombre]

    antes = set()
    destino = grabaciones.get_recordings_dir()

    if os.path.isdir(destino):
        for raiz, _, archivos in os.walk(destino):
            for archivo in archivos:
                antes.add(os.path.join(raiz, archivo))

    pedir(recording_id)
    pedir(recording_id, {"Range": "bytes=0-999"})

    fila = grabaciones.get_recording(recording_id)

    comprobar("Reproducir no cambia el estado a guardada",
              fila["status"] == "local"
              and fila["storage_state"] == "local_only",
              f"{fila['status']} / {fila['storage_state']}")

    despues = set()

    if os.path.isdir(destino):
        for raiz, _, archivos in os.walk(destino):
            for archivo in archivos:
                despues.add(os.path.join(raiz, archivo))

    comprobar("Y no deja ninguna copia en el servidor",
              despues == antes,
              str(sorted(despues - antes))[:120])


def test_conservar_sigue_funcionando():

    preparar()

    recording_id = fichas["rec_1000000001.mp4"]

    respuesta = h.cliente(h.OWNER).post(
        f"/api/recordings/{recording_id}/keep", json={"keep": True}
    )

    comprobar("Conservar responde", respuesta.status_code == 200,
              str(respuesta.status_code))

    fila = grabaciones.get_recording(recording_id)

    comprobar("Marca la grabacion", fila["keep"] == 1, str(fila["keep"]))

    comprobar("Y el archivo sigue en el disco del equipo",
              os.path.isfile(VIDEOS["rec_1000000001.mp4"]["ruta"]),
              "el archivo ha desaparecido")


def test_un_agente_antiguo_no_da_un_falso_error_de_archivo():
    """
    Un Agent anterior no declara saber transmitir.
    """

    preparar()

    servidor.connected_agents["equipo-real"] = PuenteAlAgente()
    servidor.agent_capabilities.pop("equipo-real", None)

    comenzado = time.time()

    respuesta = h.cliente(h.OWNER).get(
        f"/api/recordings/{fichas['rec_1000000001.mp4']}/local-video"
    )

    tardanza = time.time() - comenzado

    servidor.connected_agents.pop("equipo-real", None)

    comprobar("No se contesta con un error de archivo",
              respuesta.status_code == 409,
              str(respuesta.status_code))

    cuerpo = respuesta.json()

    comprobar("Se distingue por un codigo, no por el texto",
              cuerpo.get("code") == "agent_sin_transmision",
              str(cuerpo.get("code")))

    comprobar("Se dice que la version del Agent es la que falta",
              "agent" in (cuerpo.get("message") or "").lower(),
              str(cuerpo.get("message"))[:100])

    comprobar("Y se ofrece la alternativa que si funciona",
              "servidor" in (cuerpo.get("message") or "").lower(),
              str(cuerpo.get("message"))[:100])

    comprobar("Sin hacer esperar el tiempo de espera entero",
              tardanza < 2,
              f"{tardanza:.1f}s")


def test_una_grabacion_vacia_no_se_presenta_como_un_corte_de_red():
    """
    El mensaje que mandaba a buscar una averia que no existia.

    Un MP4 de doscientos y pico bytes -solo la cabecera, sin un
    fotograma dentro- lo produce un equipo que abrio el archivo y no
    llego a capturar nada. El panel lo trataba asi:

      1. la sonda pide UN byte (Range: bytes=0-0) y el equipo se lo
         manda sin problema, porque el archivo existe;
      2. el <video> carga los 261 bytes, no encuentra nada que
         decodificar y dispara onerror;
      3. el panel escribe "Se interrumpio la transmision desde el
         equipo".

    La transmision habia funcionado perfectamente. Buscar el corte
    de red que ese mensaje anuncia cuesta una tarde y no lleva a
    ninguna parte.
    """

    JS = io.open(
        RAIZ / "frontend" / "app.js", encoding="utf-8"
    ).read()

    comprobar("El panel sabe reconocer una grabacion sin contenido",
              "function grabacionSinContenido(" in JS)

    comprobar("Y lo mira ANTES de intentar reproducirla",
              JS.index("grabacionSinContenido(rec)")
              < JS.index("const sonda ="),
              "se comprueba despues de pedir el video")

    comprobar("Lo dice con sus palabras",
              "Esta grabación está vacía" in JS)

    comprobar("Y descarta expresamente la causa equivocada",
              "No es un problema de conexión." in JS)

    # Y si aun asi falla la reproduccion, no se afirma lo que no
    # consta: se dicen las dos posibilidades.
    comprobar("Ya no se culpa a la transmision a ciegas",
              '"Se interrumpió la transmisión desde el equipo."' not in JS)

    comprobar("Se nombra primero el archivo danado",
              "el archivo está dañado" in JS)

    # El umbral: un segundo de pantalla ocupa decenas de kB, asi que
    # por debajo de 10 kB no hay video posible. Lo que no puede
    # pasar es que una ficha SIN tamano se de por vacia: eso es "no
    # se sabe", y dar por vacia una grabacion buena seria peor que
    # el fallo que se arregla.
    bloque = JS.split("function grabacionSinContenido(", 1)[1][:900]

    comprobar("Una ficha sin tamano no se da por vacia",
              "tamano > 0" in bloque,
              bloque[:200].replace(chr(10), " | "))


def test_el_catalogo_real_encuentra_los_videos():

    fichas_locales = AGENTE.local_recordings_catalog()

    nombres = {f["filename"] for f in fichas_locales}

    comprobar("El catalogo real lista los dos MP4",
              {"rec_1000000001.mp4", "rec_1000000002.mp4"} <= nombres,
              str(sorted(nombres))[:120])

    comprobar("Con su tamano real",
              all(
                  f["size_bytes"] == len(VIDEOS[f["filename"]]["bytes"])
                  for f in fichas_locales
                  if f["filename"] in VIDEOS
              ),
              "algun tamano no coincide")

    comprobar("Y no cuela el archivo de fuera de la carpeta",
              "rec_7777777777.mp4" not in nombres,
              "se colo un archivo de fuera")


# ==============================

def main():

    preparar_videos()

    if len(VIDEOS) < 2:
        print("No se pudieron generar los MP4 de prueba con ffmpeg.")
        print(f"Generados: {sorted(VIDEOS)}")
        return 1

    pruebas = [
        test_los_mp4_son_de_verdad,
        test_el_video_entero_llega_identico,
        test_el_moov_al_final_se_puede_leer,
        test_saltar_adelante_y_atras,
        test_un_rango_imposible_da_416,
        test_una_sola_ida_y_vuelta_por_peticion,
        test_un_archivo_que_no_existe,
        test_cerrar_el_reproductor_detiene_la_lectura,
        test_la_cancelacion_llega_mientras_se_envia,
        test_una_conexion_lenta_no_rompe_nada,
        test_el_equipo_se_cae_a_media_transmision,
        test_el_agente_no_sale_de_su_carpeta,
        test_reproducir_no_sube_ni_copia,
        test_conservar_sigue_funcionando,
        test_un_agente_antiguo_no_da_un_falso_error_de_archivo,
        test_una_grabacion_vacia_no_se_presenta_como_un_corte_de_red,
        test_el_catalogo_real_encuentra_los_videos
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:140])

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLO"
        print(f"[{marca}] {nombre}" + (f"  -> {detalle}" if not ok else ""))
        if not ok:
            fallos += 1

    print(f"\n{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    print(f"\nMP4 de prueba: {', '.join(sorted(VIDEOS))}")

    for nombre, datos in sorted(VIDEOS.items()):
        print(f"  {nombre}  {len(datos['bytes'])} bytes  "
              f"indice al {datos['moov']}")

    return 1 if fallos else 0


if __name__ == "__main__":

    try:
        codigo = main()

    finally:
        shutil.rmtree(CARPETA, ignore_errors=True)

    sys.exit(codigo)
