"""
La duracion declarada de cero significa "no la se".

    .venv\\Scripts\\python tests/test_duracion_desconocida.py

El Agent construye la ficha de una grabacion escaneando la carpeta, y
el sistema de archivos solo sabe tamano y fecha de modificacion. Asi
que declara 0 segundos. Mientras la validacion leyo ese 0 como "dura
cero", todo intento de archivar una grabacion ya existente se
rechazaba, tambien las buenas.

Estas pruebas comprueban las dos mitades del cambio: que un cero ya no
tumba una grabacion valida, y que NO se ha debilitado nada de lo que
mide ffmpeg por su cuenta.

Los MP4 se generan con ffmpeg de verdad; los defectuosos se construyen
byte a byte. Todo en carpetas temporales: no se toca ninguna grabacion
real ni la base de datos de produccion.
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import backend.recordings as _almacen  # noqa: E402

# Las subidas de estas pruebas escriben archivos de verdad. Sin esto van
# al server_recordings del proyecto, al lado de las grabaciones reales:
# la base de datos ya era temporal, la carpeta no lo era.
#
# Todo el backend resuelve la ruta a traves de este atributo del modulo,
# asi que sustituirlo basta. La carpeta se borra al terminar.
CARPETA_SERVIDOR = Path(tempfile.mkdtemp(prefix="servidor-pruebas-"))
_almacen.RECORDINGS_DIR = CARPETA_SERVIDOR

atexit.register(shutil.rmtree, str(CARPETA_SERVIDOR), True)

from backend.media import (  # noqa: E402
    DECLARED_DURATION_UNKNOWN,
    ValidationResult,
    declared_duration_is_known,
    duration_for_storage,
    duration_within_tolerance,
    validate_recording
)


resultados = []


def comprobar(descripcion, condicion, detalle=""):
    resultados.append((descripcion, bool(condicion), detalle))


# ==============================
# ARCHIVOS DE PRUEBA
# ==============================

def crear_mp4_real(ruta, segundos=5, fps=15, tamano="320x240"):
    """Un MP4 autentico, generado por ffmpeg."""

    import imageio_ffmpeg

    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner",
         "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size={tamano}:rate={fps}",
         "-t", str(segundos), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         ruta],
        capture_output=True, timeout=120
    )

    return os.path.exists(ruta) and os.path.getsize(ruta) > 0


def crear_truncado(ruta, origen, fraccion=0.4):
    """
    Los primeros bytes de un MP4 bueno.

    Es el caso real que motivo F4: el Agent muere antes de que ffmpeg
    escriba el indice 'moov' al final del archivo. El resultado pesa
    megabytes y no se puede abrir.
    """

    datos = open(origen, "rb").read()

    with open(ruta, "wb") as archivo:
        archivo.write(datos[:int(len(datos) * fraccion)])

    return os.path.getsize(ruta) > 0


# ==============================
# EL HELPER
# ==============================

def test_cero_y_ausente_son_lo_mismo():

    comprobar("Cero es desconocido",
              not declared_duration_is_known(0))

    comprobar("Ausente es desconocido",
              not declared_duration_is_known(None))

    comprobar("La constante del protocolo es cero",
              DECLARED_DURATION_UNKNOWN == 0)


def test_una_duracion_positiva_si_se_conoce():

    for valor in (1, 5, 900, 0.5):
        comprobar(f"{valor} s es una duracion conocida",
                  declared_duration_is_known(valor))


def test_un_negativo_no_pasa_por_desconocido():
    """
    Un negativo no lo produce ningun camino legitimo. Si se tratara
    como desconocido, un dato corrupto entraria sin que nadie lo
    mirara.
    """

    for valor in (-1, -900):
        comprobar(f"{valor} s no es una duracion conocida",
                  not declared_duration_is_known(valor))


def test_la_tolerancia_no_opina_sin_datos():

    comprobar("Sin duracion declarada no afirma que encaje",
              not duration_within_tolerance(None, 5.0))

    comprobar("Con cero declarado tampoco",
              not duration_within_tolerance(0, 5.0))

    comprobar("Sin duracion real tampoco",
              not duration_within_tolerance(5, None))

    comprobar("Y con ambas, sigue comparando",
              duration_within_tolerance(5, 5.1))

    comprobar("Un disparate sigue sin encajar",
              not duration_within_tolerance(5, 999.0))


# ==============================
# LA VALIDACION, CON ARCHIVOS REALES
# ==============================

def test_un_video_bueno_se_acepta_con_cualquier_duracion_desconocida():

    carpeta = tempfile.mkdtemp(prefix="duracion-")

    try:
        bueno = os.path.join(carpeta, "bueno.mp4")

        if not crear_mp4_real(bueno, segundos=5):
            comprobar("ffmpeg genera un MP4 de prueba", False,
                      "no se pudo generar el archivo")
            return

        comprobar("ffmpeg genera un MP4 de prueba", True)

        # El caso que estaba roto
        cero = validate_recording(bueno, declared_duration=0)

        comprobar("Duracion 0: la grabacion se acepta",
                  cero.valid, str(cero.reason))

        comprobar("Y se registra la duracion REAL, no el cero",
                  cero.duration is not None and cero.duration > 4,
                  str(cero.duration))

        # Un Agent que no manda el dato
        ausente = validate_recording(bueno, declared_duration=None)

        comprobar("Duracion ausente: se acepta",
                  ausente.valid, str(ausente.reason))

        comprobar("Con la misma duracion medida",
                  ausente.duration == cero.duration)

        # Un Agent que si lo sabe
        positiva = validate_recording(bueno, declared_duration=5)

        comprobar("Duracion 5 declarada: se acepta",
                  positiva.valid, str(positiva.reason))

        comprobar("Se detecta el codec",
                  positiva.codec == "h264", str(positiva.codec))

        comprobar("Y la resolucion",
                  positiva.resolution == "320x240",
                  str(positiva.resolution))

        # Lo declarado sigue contrastandose cuando se conoce
        mentira = validate_recording(bueno, declared_duration=9000)

        comprobar("Una duracion declarada disparatada se sigue rechazando",
                  not mentira.valid
                  and mentira.reason == "duracion_no_coincide",
                  str(mentira.reason))

        # Un negativo no es desconocido
        negativa = validate_recording(bueno, declared_duration=-5)

        comprobar("Una duracion declarada negativa se rechaza",
                  not negativa.valid
                  and negativa.reason == "duracion_declarada_invalida",
                  str(negativa.reason))

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def test_ffmpeg_sigue_rechazando_lo_que_rechazaba():
    """
    La parte importante del cambio es la que NO cambia.

    Cada uno de estos casos se prueba con duracion declarada 0, que es
    justo el valor que ahora pasa de largo en el paso 8: si alguno se
    colara, el cambio habria abierto un agujero.
    """

    carpeta = tempfile.mkdtemp(prefix="duracion-malos-")

    try:
        # Archivo vacio
        vacio = os.path.join(carpeta, "vacio.mp4")
        open(vacio, "wb").close()

        resultado = validate_recording(vacio, declared_duration=0)

        comprobar("Un archivo vacio se rechaza",
                  not resultado.valid and resultado.reason == "archivo_vacio",
                  str(resultado.reason))

        # No es un MP4
        basura = os.path.join(carpeta, "basura.mp4")
        with open(basura, "wb") as archivo:
            archivo.write(b"esto no es un video, es texto" * 50)

        resultado = validate_recording(basura, declared_duration=0)

        comprobar("Un contenedor invalido se rechaza",
                  not resultado.valid and resultado.reason == "no_es_mp4",
                  str(resultado.reason))

        # El de 261 bytes de la auditoria
        cortito = os.path.join(carpeta, "cortito.mp4")
        with open(cortito, "wb") as archivo:
            archivo.write(b"\x00\x00\x00\x18ftypmp42")
            archivo.write(b"\x00" * 240)

        resultado = validate_recording(cortito, declared_duration=0)

        comprobar("Un MP4 con cabecera pero sin contenido se rechaza",
                  not resultado.valid
                  and resultado.reason in ("contenedor_incompleto",
                                           "contenedor_invalido",
                                           "sin_video"),
                  str(resultado.reason))

        # Truncado: pesa de verdad pero le falta el indice
        bueno = os.path.join(carpeta, "bueno.mp4")

        if crear_mp4_real(bueno, segundos=5):

            truncado = os.path.join(carpeta, "truncado.mp4")
            crear_truncado(truncado, bueno)

            resultado = validate_recording(truncado, declared_duration=0)

            comprobar("Un archivo truncado se rechaza",
                      not resultado.valid
                      and resultado.reason in ("contenedor_incompleto",
                                               "contenedor_invalido",
                                               "sin_duracion",
                                               "sin_video"),
                      str(resultado.reason))

            comprobar("Y no por el tamano: pesaba "
                      f"{os.path.getsize(truncado)} bytes",
                      os.path.getsize(truncado) > 1000)

        # Una duracion real nula: un MP4 valido de duracion cero
        nulo = os.path.join(carpeta, "nulo.mp4")

        import imageio_ffmpeg
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner",
             "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
             "-frames:v", "0", "-c:v", "libx264", nulo],
            capture_output=True, timeout=120
        )

        if os.path.exists(nulo) and os.path.getsize(nulo) > 0:

            resultado = validate_recording(nulo, declared_duration=0)

            comprobar("Una duracion REAL nula se sigue rechazando",
                      not resultado.valid
                      and resultado.reason in ("duracion_nula",
                                               "sin_duracion",
                                               "sin_video",
                                               "contenedor_incompleto"),
                      str(resultado.reason))

        # Inexistente
        resultado = validate_recording(
            os.path.join(carpeta, "no-existe.mp4"), declared_duration=0
        )

        comprobar("Un archivo que no existe se rechaza",
                  not resultado.valid
                  and resultado.reason == "archivo_inexistente",
                  str(resultado.reason))

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


# ==============================
# QUE DURACION SE GUARDA
# ==============================

def test_gana_la_duracion_medida_sobre_la_declarada():
    """
    ffmpeg mide el archivo que de verdad esta en el servidor; lo
    declarado es lo que dijo un equipo remoto. Cuando hay medicion,
    gana la medicion.
    """

    medido = ValidationResult(True, duration=51.27)

    comprobar("Con medicion y declaracion, se guarda la medida",
              duration_for_storage(medido, 62) == 51,
              str(duration_for_storage(medido, 62)))

    comprobar("Con medicion y cero declarado, se guarda la medida",
              duration_for_storage(ValidationResult(True, duration=5.0), 0)
              == 5)

    comprobar("Con medicion y nada declarado, se guarda la medida",
              duration_for_storage(ValidationResult(True, duration=5.0), None)
              == 5)


def test_nunca_se_guarda_un_cero_como_duracion_conocida():
    """
    En este esquema el 0 significa "desconocida". Una grabacion que SI
    tiene duracion no puede acabar guardada como 0, porque entonces
    seria indistinguible de las que no se saben.
    """

    corta = ValidationResult(True, duration=0.4)

    comprobar("Una duracion por debajo del segundo no se redondea a cero",
              duration_for_storage(corta, 0) == 1,
              str(duration_for_storage(corta, 0)))

    comprobar("Una declaracion por debajo del segundo tampoco",
              duration_for_storage(ValidationResult(True), 0.4) == 1)


def test_sin_dato_no_se_escribe_nada():
    """None es la senal de "deja el valor que ya hubiera"."""

    comprobar("Sin medicion ni declaracion conocida, None",
              duration_for_storage(ValidationResult(True), 0) is None)

    comprobar("Sin medicion y sin declarar, None",
              duration_for_storage(ValidationResult(True), None) is None)

    comprobar("Sin resultado de validacion, None",
              duration_for_storage(None, None) is None)

    comprobar("Una medicion nula no cuenta como medicion",
              duration_for_storage(ValidationResult(True, duration=0), 0)
              is None)


def test_se_conserva_lo_declarado_cuando_es_lo_unico_que_hay():
    """
    Compatibilidad: un Agent que si sabe la duracion sigue viendola
    guardada aunque la medicion no estuviera disponible.
    """

    comprobar("Con 900 declarados y sin medir, se guardan 900",
              duration_for_storage(ValidationResult(True), 900) == 900)

    comprobar("Un negativo declarado no se guarda",
              duration_for_storage(ValidationResult(True), -5) is None)


def test_la_persistencia_usa_el_helper():

    backend = open(os.path.join(RAIZ, "backend", "main.py"),
                   encoding="utf-8").read()

    comprobar("main.py importa el helper",
              "duration_for_storage" in backend)

    comprobar("Y ya no convierte el cero en None al archivar",
              "duration_sec=duration_sec or None" not in backend)

    recordings = open(os.path.join(RAIZ, "backend", "recordings.py"),
                      encoding="utf-8").read()

    comprobar("mark_stored sigue aceptando la duracion",
              "def mark_stored(recording_id, size_bytes=None, "
              "duration_sec=None)" in recordings)

    comprobar("Y la escribe con COALESCE, que un valor real sobrescribe",
              "duration_sec = COALESCE(?, duration_sec)" in recordings)


# ==============================
# EL CAMINO COMPLETO
# ==============================

def test_la_subida_real_funciona_con_cero_y_no_duplica():
    """
    La validacion aislada no basta: lo que importa es que una subida
    con duration_sec=0 —exactamente lo que manda un Agent instalado—
    llegue a archivarse, y que reintentarla no cree filas nuevas.

    Base copiada y carpeta temporal. No se toca ninguna grabacion real.
    """

    sys.path.insert(0, os.path.join(RAIZ, "tests"))

    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    import users_harness as h

    import backend.database as database
    import backend.devices as devices
    import backend.organizations as orgs
    import backend.users as users

    h.reiniciar()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "organizations",
                  "enrollment_tokens"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    org = orgs.create_organization(
        "Empresa", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, org["id"])

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-prueba', 'PC', ?)", (org["id"],)
    )
    conexion.commit()
    conexion.close()

    token = devices.issue_agent_token("eq-prueba")

    comprobar("Se emite un token de equipo", bool(token))

    carpeta = tempfile.mkdtemp(prefix="duracion-e2e-")

    try:
        video = os.path.join(carpeta, "seg.mp4")

        if not crear_mp4_real(video, segundos=5):
            comprobar("Se genera el video de la subida", False)
            return

        datos = open(video, "rb").read()

        cliente = h.cliente(h.OWNER)

        ruta = (
            "/api/devices/eq-prueba/recordings/upload"
            "?filename=seg.mp4"
            "&started_at=2026-10-09T10:00:00%2B00:00"
            "&ended_at=2026-10-09T10:00:05%2B00:00"
            "&duration_sec=0"
        )

        respuesta = cliente.post(
            ruta, content=datos, headers={"X-Agent-Token": token}
        )

        cuerpo = respuesta.json()

        comprobar("Una subida con duration_sec=0 se archiva",
                  respuesta.status_code == 200
                  and cuerpo.get("status") == "stored",
                  f"{respuesta.status_code} {cuerpo.get('status')} "
                  f"{cuerpo.get('reason')}")

        conexion = database.get_connection()
        filas = [dict(f) for f in conexion.execute(
            "SELECT id, storage_state, duration_sec, size_bytes "
            "FROM recordings"
        )]
        conexion.close()

        comprobar("Y queda en el servidor",
                  len(filas) == 1
                  and filas[0]["storage_state"] == "server_stored",
                  str(filas))

        # El video de prueba dura 5 s. Lo que se guarda tiene que ser esa
        # duracion real medida, no el 0 que mando el Agent.
        guardada = filas[0]["duration_sec"] if filas else None

        comprobar("No se guarda el cero que mando el Agent",
                  guardada != 0, str(guardada))

        comprobar("Se guarda una duracion positiva",
                  guardada is not None and guardada > 0, str(guardada))

        comprobar("Y es la real, de unos 5 s",
                  guardada is not None and 4 <= guardada <= 7,
                  f"{guardada} s para un video de 5 s")

        identificador = filas[0]["id"] if filas else None

        # Reintentos: el indice unico (device_id, path) es la garantia
        for intento in (2, 3):

            repetida = cliente.post(
                ruta, content=datos, headers={"X-Agent-Token": token}
            )

            conexion = database.get_connection()
            total = conexion.execute(
                "SELECT COUNT(*) FROM recordings"
            ).fetchone()[0]
            conexion.close()

            comprobar(f"Reintento {intento}: sigue habiendo una sola fila",
                      total == 1, str(total))

            comprobar(f"Reintento {intento}: se reconoce como duplicado",
                      repetida.json().get("duplicate") is True,
                      str(repetida.json().get("status")))

            conexion = database.get_connection()
            despues = dict(list(conexion.execute(
                "SELECT id, storage_state, duration_sec, size_bytes "
                "FROM recordings WHERE id = ?", (identificador,)
            ))[0])
            conexion.close()

            comprobar(f"Reintento {intento}: la fila no se altera",
                      despues == filas[0], f"{despues} != {filas[0]}")

        # Un archivo roto con el mismo cero sigue cayendo
        # Cabecera MP4 valida seguida de relleno: el caso real de
        # 261 bytes de la auditoria. Se construye por valores
        # para no meter bytes nulos en el codigo fuente.
        roto = bytes([0, 0, 0, 0x18]) + b"ftypmp42" + bytes(240)

        respuesta = cliente.post(
            "/api/devices/eq-prueba/recordings/upload"
            "?filename=roto.mp4"
            "&started_at=2026-10-09T11:00:00%2B00:00"
            "&ended_at=2026-10-09T11:00:05%2B00:00"
            "&duration_sec=0",
            content=roto, headers={"X-Agent-Token": token}
        )

        cuerpo = respuesta.json()

        comprobar("Un archivo roto con duration_sec=0 va a cuarentena",
                  cuerpo.get("status") == "invalid",
                  str(cuerpo.get("status")))

        comprobar("Con el motivo real del archivo, no el de la duracion",
                  cuerpo.get("reason") not in ("duracion_declarada_invalida",),
                  str(cuerpo.get("reason")))

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def test_una_ficha_local_con_cero_se_completa_con_la_duracion_real():
    """
    El camino que seguiran las 67 grabaciones del panel.

    Ya existe una FICHA (storage_state=local_only, duration_sec=0)
    creada desde el catalogo del Agent. Al archivarla no se crea otra
    fila: se completa esa misma. Aqui es donde el COALESCE podia
    conservar el cero, asi que se comprueba el id, la duracion y que no
    aparezca una fila nueva.
    """

    sys.path.insert(0, os.path.join(RAIZ, "tests"))

    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    import users_harness as h

    import backend.database as database
    import backend.devices as devices
    import backend.organizations as orgs
    import backend.users as users

    h.reiniciar()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "organizations",
                  "enrollment_tokens"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    org = orgs.create_organization(
        "Empresa", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, org["id"])

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-ficha', 'PC', ?)", (org["id"],)
    )

    # La ficha tal como la crea el catalogo del Agent: sin archivo en el
    # servidor, sin duracion y con inicio igual a fin, porque lo unico
    # que sabia era la fecha de modificacion.
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, ended_at, "
        "duration_sec, size_bytes, status, storage_state, keep) VALUES "
        "('eq-ficha', 'eq-ficha/2026/10/09/seg.mp4', "
        "'2026-10-09T10:00:00+00:00', '2026-10-09T10:00:00+00:00', "
        "0, 22170, 'local', 'local_only', 0)"
    )
    conexion.commit()

    ficha = dict(list(conexion.execute(
        "SELECT id, duration_sec, storage_state FROM recordings"
    ))[0])
    conexion.close()

    comprobar("Se parte de una ficha local con duracion 0",
              ficha["duration_sec"] == 0
              and ficha["storage_state"] == "local_only",
              str(ficha))

    token = devices.issue_agent_token("eq-ficha")

    carpeta = tempfile.mkdtemp(prefix="duracion-ficha-")

    try:
        video = os.path.join(carpeta, "seg.mp4")

        if not crear_mp4_real(video, segundos=5):
            comprobar("Se genera el video de la ficha", False)
            return

        datos = open(video, "rb").read()

        respuesta = h.cliente(h.OWNER).post(
            "/api/devices/eq-ficha/recordings/upload"
            "?filename=seg.mp4"
            "&started_at=2026-10-09T10:00:00%2B00:00"
            "&ended_at=2026-10-09T10:00:05%2B00:00"
            "&duration_sec=0",
            content=datos, headers={"X-Agent-Token": token}
        )

        cuerpo = respuesta.json()

        comprobar("La ficha se archiva",
                  cuerpo.get("status") == "stored",
                  f"{respuesta.status_code} {cuerpo}")

        conexion = database.get_connection()
        filas = [dict(f) for f in conexion.execute(
            "SELECT id, duration_sec, storage_state, size_bytes "
            "FROM recordings"
        )]
        conexion.close()

        comprobar("No se crea una fila nueva: sigue habiendo una",
                  len(filas) == 1, str(filas))

        comprobar("Y es la MISMA ficha, no otra",
                  len(filas) == 1 and filas[0]["id"] == ficha["id"],
                  f"{filas} frente a id={ficha['id']}")

        comprobar("Ahora esta en el servidor",
                  len(filas) == 1
                  and filas[0]["storage_state"] == "server_stored",
                  str(filas))

        # El punto del cambio: el COALESCE no conserva el cero
        guardada = filas[0]["duration_sec"] if filas else None

        comprobar("El COALESCE no conservo el cero anterior",
                  guardada != 0, str(guardada))

        comprobar("Se escribio la duracion real, de unos 5 s",
                  guardada is not None and 4 <= guardada <= 7,
                  f"{guardada} s para un video de 5 s")

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


# ==============================
# COMPATIBILIDAD CON LOS AGENTS INSTALADOS
# ==============================

def test_lo_que_mandan_los_agents_ya_instalados():
    """
    Tres formas en que un Agent ya desplegado presenta la duracion.
    Ninguna se puede cambiar sin reinstalarlo, asi que las tres tienen
    que funcionar contra el servidor nuevo.
    """

    codigo = open(os.path.join(RAIZ, "agent", "agent.py"),
                  encoding="utf-8").read()

    comprobar("El catalogo local declara 0 por escanear el disco",
              '"duration_sec": 0,' in codigo)

    comprobar("Y el archivado bajo demanda tambien",
              codigo.count('"duration_sec": 0,') >= 2)

    # El endpoint tiene 0 por defecto: omitirlo llega como 0
    backend = open(os.path.join(RAIZ, "backend", "main.py"),
                   encoding="utf-8").read()

    comprobar("El endpoint recibe 0 cuando el Agent omite el dato",
              "duration_sec: int = 0" in backend)

    comprobar("Por eso omitir y declarar cero no se distinguen, "
              "y los dos son desconocido",
              not declared_duration_is_known(0)
              and not declared_duration_is_known(None))


def test_el_cambio_vive_solo_en_el_servidor():
    """
    Todo se corrige en el servidor a proposito: un cambio en el Agent
    no llegaria a los equipos ya instalados, que son justamente los que
    mandan el cero.
    """

    cambiados = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=RAIZ
    ).stdout.split()

    comprobar("No se toca el Agent",
              "agent/agent.py" not in cambiados, str(cambiados))

    comprobar("Se corrige la validacion",
              "backend/media.py" in cambiados, str(cambiados))

    comprobar("Y el registro de la grabacion",
              "backend/main.py" in cambiados, str(cambiados))

    comprobar("Sin tocar el almacenamiento de grabaciones",
              "backend/recordings.py" not in cambiados, str(cambiados))


# ==============================

def main():

    pruebas = [
        test_cero_y_ausente_son_lo_mismo,
        test_una_duracion_positiva_si_se_conoce,
        test_un_negativo_no_pasa_por_desconocido,
        test_la_tolerancia_no_opina_sin_datos,
        test_un_video_bueno_se_acepta_con_cualquier_duracion_desconocida,
        test_ffmpeg_sigue_rechazando_lo_que_rechazaba,
        test_gana_la_duracion_medida_sobre_la_declarada,
        test_nunca_se_guarda_un_cero_como_duracion_conocida,
        test_sin_dato_no_se_escribe_nada,
        test_se_conserva_lo_declarado_cuando_es_lo_unico_que_hay,
        test_la_persistencia_usa_el_helper,
        test_la_subida_real_funciona_con_cero_y_no_duplica,
        test_una_ficha_local_con_cero_se_completa_con_la_duracion_real,
        test_lo_que_mandan_los_agents_ya_instalados,
        test_el_cambio_vive_solo_en_el_servidor
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:90])

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
