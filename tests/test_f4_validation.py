"""
Pruebas de la validación multimedia (bloque F4).

Dos partes:
  1. El módulo backend/media.py de forma aislada, con archivos sintéticos y
     con un MP4 real generado por ffmpeg.
  2. El endpoint completo por HTTPS: subida válida, subida inválida,
     cuarentena, estado en la base y listado.

Se trabaja siempre sobre una COPIA de la base y una carpeta temporal: las
grabaciones reales, incluidas id=4 e id=8, no se tocan.

Uso, desde la raíz del proyecto:

    python tests/test_f4_validation.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

import requests

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from backend.media import (  # noqa: E402
    validate_recording,
    duration_within_tolerance,
    has_ftyp_header,
    FFMPEG_TIMEOUT_SECONDS,
    DURATION_TOLERANCE_RATIO,
    DURATION_TOLERANCE_MIN_SECONDS
)

CA = os.path.join(RAIZ, "certs", "dev-ca-cert.pem")
PUERTO = 8591
BASE = f"https://localhost:{PUERTO}"

resultados = []


def comprobar(descripcion, condicion):
    resultados.append((descripcion, bool(condicion)))
    print(f"  [{'OK  ' if condicion else 'FALLA'}] {descripcion}")


def crear_mp4_real(ruta, segundos=2, fps=15, tamano="320x240"):
    """Genera un MP4 auténtico con ffmpeg para las pruebas de camino feliz."""

    import imageio_ffmpeg

    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size={tamano}:rate={fps}",
         "-t", str(segundos), "-c:v", "libx264", "-pix_fmt", "yuv420p", ruta],
        capture_output=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    )

    return ruta


# ==============================================================
# PARTE 1: el módulo aislado
# ==============================================================

def pruebas_modulo():

    print("\n" + "=" * 62)
    print("PARTE 1: módulo backend/media.py")
    print("=" * 62)

    temporal = tempfile.mkdtemp(prefix="remoteadmin_f4_mod_")

    try:

        print("\n=== Archivos que NO son vídeo ===")

        casos = [
            ("0 bytes", b"", "archivo_vacio"),
            ("1 byte", b"X", "no_es_mp4"),
            ("texto renombrado .mp4", b"Esto no es un video\n" * 10, "no_es_mp4"),
            ("JPEG renombrado", b"\xff\xd8\xff\xe0" + b"\x00" * 500, "no_es_mp4"),
            ("ZIP renombrado", b"PK\x03\x04" + b"\x00" * 300, "no_es_mp4"),
        ]

        for etiqueta, datos, motivo in casos:
            ruta = os.path.join(temporal, "caso.mp4")
            with open(ruta, "wb") as h:
                h.write(datos)

            r = validate_recording(ruta, 10)
            comprobar(f"{etiqueta} -> inválido ({r.reason})",
                      not r.valid and r.reason == motivo)

        print("\n=== MP4 estructuralmente incompletos ===")

        ftyp = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"

        ruta = os.path.join(temporal, "truncado.mp4")
        with open(ruta, "wb") as h:
            h.write(ftyp)
        r = validate_recording(ruta, 10)
        comprobar(f"MP4 truncado (solo ftyp) -> inválido ({r.reason})", not r.valid)

        # Réplica del caso real id=4: datos sin índice 'moov'
        ruta = os.path.join(temporal, "sin_moov.mp4")
        with open(ruta, "wb") as h:
            h.write(ftyp)
            h.write(b"\x00\x00\x00\x08free")
            h.write(b"\x00\x01\x00\x00mdat")
            h.write(b"\x00" * 65000)
        r = validate_recording(ruta, 120)
        comprobar(f"MP4 sin moov (como id=4) -> inválido ({r.reason})",
                  not r.valid and r.reason == "contenedor_incompleto")

        print("\n=== MP4 REAL generado con ffmpeg ===")

        valido = crear_mp4_real(os.path.join(temporal, "valido.mp4"), segundos=2)
        comprobar("el MP4 de prueba se generó", os.path.exists(valido)
                  and os.path.getsize(valido) > 0)

        r = validate_recording(valido, 2)
        comprobar("MP4 real con duración correcta -> VÁLIDO", r.valid)
        print(f"        duración real {r.duration}s, códec {r.codec}, "
              f"resolución {r.resolution}, streams {r.streams}")

        comprobar("detecta la cabecera ftyp", has_ftyp_header(valido))

        print("\n=== Duración declarada ===")

        r = validate_recording(valido, 2)
        comprobar("2s declarados, 2s reales -> válido", r.valid)

        r = validate_recording(valido, 999999)
        comprobar(f"999999s declarados -> inválido ({r.reason})",
                  not r.valid and r.reason == "duracion_no_coincide")

        r = validate_recording(valido, -50)
        comprobar(f"duración negativa -> inválido ({r.reason})",
                  not r.valid and r.reason == "duracion_declarada_invalida")

        r = validate_recording(valido, 0)
        comprobar(f"duración 0 -> inválido ({r.reason})", not r.valid)

        r = validate_recording(valido, None)
        comprobar("sin duración declarada -> válido (no hay nada que contrastar)",
                  r.valid)

        print("\n=== La tolerancia refleja los datos reales ===")

        comprobar("id=10 real (62 declarados, 51,3 reales) entra",
                  duration_within_tolerance(62, 51.27))
        comprobar("id=9 real (160 declarados, 143,7 reales) entra",
                  duration_within_tolerance(160, 143.73))
        comprobar("id=7 real (4 declarados, 3,87 reales) entra",
                  duration_within_tolerance(4, 3.87))
        comprobar("una desviación del 50% NO entra",
                  not duration_within_tolerance(100, 50))
        comprobar("segmentos cortos usan el mínimo de 3 s",
                  duration_within_tolerance(2, 4.5))
        print(f"        tolerancia: {int(DURATION_TOLERANCE_RATIO * 100)}% "
              f"o {DURATION_TOLERANCE_MIN_SECONDS}s, lo que sea mayor")

        print("\n=== ffmpeg no disponible: comportamiento explícito ===")

        import backend.media as media
        original = media.get_ffmpeg_path
        media.get_ffmpeg_path = lambda: None

        r = media.validate_recording(valido, 2)
        comprobar(f"sin ffmpeg -> '{r.reason}', no se afirma que sea malo",
                  not r.valid and r.reason == "validacion_no_disponible")

        media.get_ffmpeg_path = original

        print("\n=== Timeout de ffmpeg ===")

        original_timeout = media.FFMPEG_TIMEOUT_SECONDS
        media.FFMPEG_TIMEOUT_SECONDS = 0.001     # imposible de cumplir

        r = media.validate_recording(valido, 2)
        comprobar(f"timeout -> inválido ({r.reason})",
                  not r.valid and r.reason == "timeout_validacion")

        media.FFMPEG_TIMEOUT_SECONDS = original_timeout
        print(f"        timeout real configurado: {FFMPEG_TIMEOUT_SECONDS}s")

        print("\n=== Archivo inexistente ===")
        r = validate_recording(os.path.join(temporal, "no_existe.mp4"), 10)
        comprobar(f"no existe -> inválido ({r.reason})",
                  not r.valid and r.reason == "archivo_inexistente")

    finally:
        shutil.rmtree(temporal, ignore_errors=True)


# ==============================================================
# PARTE 2: el endpoint completo
# ==============================================================

class Entorno:

    def __init__(self):
        self.temporal = tempfile.mkdtemp(prefix="remoteadmin_f4_srv_")
        self.db = os.path.join(self.temporal, "f4.db")
        self.grabaciones = os.path.join(self.temporal, "server_recordings")
        os.makedirs(self.grabaciones, exist_ok=True)
        shutil.copy(os.path.join(RAIZ, "remoteadmin.db"), self.db)
        self.proceso = None

    def preparar(self):
        import backend.database as db
        db.DATABASE_PATH = self.db
        db.init_db()
        import backend.devices as dv
        self.device_id, self.token = dv.enroll_device("PC-F4", "Win", "10.0.0.4")

    def arrancar(self):
        lanzador = os.path.join(self.temporal, "srvapp.py")
        with open(lanzador, "w", encoding="utf-8") as h:
            h.write(
                "import os\n"
                "from pathlib import Path\n"
                "import backend.database as db\n"
                "db.DATABASE_PATH = os.environ['TEST_DB']\n"
                "import backend.recordings as rec\n"
                "rec.RECORDINGS_DIR = Path(os.environ['TEST_RECORDINGS'])\n"
                "from backend.main import app  # noqa: E402\n"
            )

        sys.path.insert(0, os.path.join(RAIZ, "backend"))
        from auth import generate_password_hash

        entorno = dict(os.environ)
        entorno.update({
            "PYTHONPATH": self.temporal + os.pathsep + RAIZ,
            "TEST_DB": self.db,
            "TEST_RECORDINGS": self.grabaciones,
            "AUTH_USERNAME": "admin",
            "AUTH_PASSWORD_HASH": generate_password_hash("PruebaF4!"),
            "AUTH_SECRET_KEY": "0" * 64,
            "AGENT_TOKEN": "token-de-alta"
        })

        self.log = open(os.path.join(self.temporal, "servidor.log"),
                        "w", encoding="utf-8")

        self.proceso = subprocess.Popen(
            [os.path.join(RAIZ, ".venv", "Scripts", "python.exe"),
             "-m", "uvicorn", "srvapp:app", "--host", "127.0.0.1",
             "--port", str(PUERTO),
             "--ssl-certfile", os.path.join(RAIZ, "certs", "dev-server-cert.pem"),
             "--ssl-keyfile", os.path.join(RAIZ, "certs", "dev-server-key.pem")],
            cwd=RAIZ, env=entorno,
            # La salida va a un archivo, NO a una tubería: si nadie lee la
            # tubería, el búfer del sistema se llena y el servidor se queda
            # bloqueado escribiendo su propio log.
            stdout=self.log, stderr=subprocess.STDOUT)

        for _ in range(40):
            try:
                requests.get(f"{BASE}/api/health", verify=CA, timeout=2)
                return True
            except Exception:
                time.sleep(0.5)
        return False

    def subir(self, nombre, datos, duracion):
        return requests.post(
            f"{BASE}/api/devices/{self.device_id}/recordings/upload",
            params={"filename": nombre,
                    "started_at": "2026-09-28T10:00:00+00:00",
                    "ended_at": "2026-09-28T10:00:30+00:00",
                    "duration_sec": duracion},
            data=datos,
            headers={"X-Agent-Token": self.token,
                     "Content-Type": "application/octet-stream"},
            verify=CA, timeout=90)

    def fila(self, nombre_parcial):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        f = c.execute(
            "SELECT * FROM recordings WHERE path LIKE ? ORDER BY id DESC LIMIT 1",
            (f"%{nombre_parcial}%",)).fetchone()
        c.close()
        return dict(f) if f else None

    def en_cuarentena(self):
        carpeta = os.path.join(self.grabaciones, "_invalid")
        if not os.path.isdir(carpeta):
            return []
        return sorted(os.listdir(carpeta))

    def limpiar(self):
        if self.proceso:
            self.proceso.terminate()
            try:
                self.proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proceso.kill()
        shutil.rmtree(self.temporal, ignore_errors=True)


def pruebas_endpoint():

    print("\n" + "=" * 62)
    print("PARTE 2: endpoint completo por HTTPS")
    print("=" * 62)

    e = Entorno()
    e.preparar()

    if not e.arrancar():
        print("  No se pudo arrancar el backend de pruebas")
        e.limpiar()
        return

    temporal = tempfile.mkdtemp(prefix="remoteadmin_f4_files_")

    try:

        print("\n=== Grabación VÁLIDA ===")
        valido = crear_mp4_real(os.path.join(temporal, "v.mp4"), segundos=3)
        with open(valido, "rb") as h:
            r = e.subir("rec_9100000001.mp4", h.read(), 3)

        datos = r.json()
        print(f"        respuesta: {r.status_code} {datos.get('status')}")
        comprobar("responde 200 stored",
                  r.status_code == 200 and datos.get("status") == "stored")

        fila = e.fila("rec_9100000001")
        comprobar("queda registrada con status='stored'",
                  fila and fila["status"] == "stored")
        comprobar("NO va a cuarentena", "_invalid" not in (fila["path"] if fila else ""))

        print("\n=== Grabación INVÁLIDA (texto renombrado) ===")
        r = e.subir("rec_9100000002.mp4", b"esto no es un video\n" * 20, 30)
        datos = r.json()
        print(f"        respuesta: {r.status_code} {datos.get('status')} "
              f"motivo={datos.get('reason')}")

        comprobar("responde 200 (no 4xx, para que el Agent no reintente)",
                  r.status_code == 200)
        comprobar("el estado es 'invalid'", datos.get("status") == "invalid")
        comprobar("informa del motivo", bool(datos.get("reason")))

        fila_inv = e.fila("rec_9100000002")
        comprobar("queda registrada con status='invalid'",
                  fila_inv and fila_inv["status"] == "invalid")
        comprobar("guarda el motivo en la base",
                  fila_inv and fila_inv["invalid_reason"] == datos.get("reason"))
        comprobar("la ruta apunta a la cuarentena",
                  fila_inv and fila_inv["path"].startswith("_invalid/"))

        cuarentena = e.en_cuarentena()
        comprobar("el archivo está físicamente en _invalid", len(cuarentena) == 1)
        print(f"        en cuarentena: {cuarentena}")

        print("\n=== Otros contenidos inválidos ===")
        for i, (etiqueta, datos_archivo, dur) in enumerate([
            ("0 bytes", b"", 10),
            ("1 byte", b"X", 10),
            ("JPEG renombrado", b"\xff\xd8\xff\xe0" + b"\x00" * 400, 10),
            ("MP4 truncado", b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41", 10),
        ]):
            r = e.subir(f"rec_920000000{i}.mp4", datos_archivo, dur)
            d = r.json()
            comprobar(f"{etiqueta} -> invalid ({d.get('reason')})",
                      r.status_code == 200 and d.get("status") == "invalid")

        print("\n=== Duración fuera de tolerancia ===")
        with open(valido, "rb") as h:
            contenido = h.read()

        r = e.subir("rec_9300000001.mp4", contenido, 9999)
        d = r.json()
        comprobar(f"3s reales declarados como 9999s -> invalid ({d.get('reason')})",
                  d.get("status") == "invalid"
                  and d.get("reason") == "duracion_no_coincide")

        r = e.subir("rec_9300000002.mp4", contenido, -50)
        d = r.json()
        comprobar(f"duración negativa -> invalid ({d.get('reason')})",
                  d.get("status") == "invalid")

        print("\n=== Dos inválidas con el mismo nombre no se pisan ===")
        antes = len(e.en_cuarentena())
        e.subir("rec_9400000001.mp4", b"basura uno\n" * 10, 10)
        e.subir("rec_9400000001.mp4", b"basura dos distinta\n" * 10, 10)
        despues = e.en_cuarentena()
        comprobar("se guardan las dos por separado", len(despues) == antes + 2)

        print("\n=== Las inválidas NO aparecen en el listado normal ===")
        import backend.database as db
        import backend.recordings as rec
        from pathlib import Path
        db.DATABASE_PATH = e.db
        rec.RECORDINGS_DIR = Path(e.grabaciones)

        listado = rec.list_recordings()
        invalidas = rec.list_invalid_recordings()

        estados = {l["status"] for l in listado}
        comprobar("el listado solo trae 'stored'", estados == {"stored"})
        comprobar("hay inválidas registradas aparte", len(invalidas) > 0)
        print(f"        listado normal: {len(listado)} | en cuarentena: {len(invalidas)}")
        comprobar("ninguna inválida se coló en el listado",
                  not any(l["path"].startswith("_invalid/") for l in listado))

        print("\n=== F3 sigue intacto ===")
        with open(valido, "rb") as h:
            contenido = h.read()
        r1 = e.subir("rec_9500000001.mp4", contenido, 3)
        r2 = e.subir("rec_9500000001.mp4", contenido, 3)
        comprobar("duplicado sigue respondiendo 'duplicate'",
                  r2.json().get("duplicate") is True)

        partes = []
        for raiz, _, nombres in os.walk(e.grabaciones):
            partes += [n for n in nombres if n.endswith(".part")]
        comprobar("no quedan archivos .part", len(partes) == 0)

    finally:
        shutil.rmtree(temporal, ignore_errors=True)
        e.limpiar()


def main():

    pruebas_modulo()
    pruebas_endpoint()

    fallos = [d for d, ok in resultados if not ok]

    print("\n" + "=" * 62)
    print(f"  {len(resultados) - len(fallos)} de {len(resultados)} comprobaciones correctas")

    if fallos:
        print("  FALLOS:")
        for f in fallos:
            print("   -", f)

    print("=" * 62)

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
