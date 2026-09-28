"""
Pruebas del bloque F3: robustez de la subida de grabaciones.

Levanta un backend real con TLS sobre una COPIA de la base y una carpeta de
grabaciones temporal: la instalación real no se toca en ningún momento.

Uso, desde la raíz del proyecto:

    python tests/test_f3_upload.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import requests

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "agent"))

CA = os.path.join(RAIZ, "certs", "dev-ca-cert.pem")
PUERTO = 8571
BASE = f"https://localhost:{PUERTO}"

resultados = []


def comprobar(descripcion, condicion):
    resultados.append((descripcion, bool(condicion)))
    print(f"  [{'OK  ' if condicion else 'FALLA'}] {descripcion}")


class Entorno:
    """Backend de pruebas aislado: base copiada y carpeta temporal."""

    def __init__(self):
        self.temporal = tempfile.mkdtemp(prefix="remoteadmin_f3_")
        self.db = os.path.join(self.temporal, "f3.db")
        self.grabaciones = os.path.join(self.temporal, "server_recordings")
        os.makedirs(self.grabaciones, exist_ok=True)
        shutil.copy(os.path.join(RAIZ, "remoteadmin.db"), self.db)
        self.proceso = None
        self.device_id = None
        self.token = None

    def _escribir_lanzador(self):

        ruta = os.path.join(self.temporal, "srvapp.py")

        with open(ruta, "w", encoding="utf-8") as handle:
            handle.write(
                "import os\n"
                "from pathlib import Path\n"
                "import backend.database as db\n"
                "db.DATABASE_PATH = os.environ['TEST_DB']\n"
                "import backend.recordings as rec\n"
                "rec.RECORDINGS_DIR = Path(os.environ['TEST_RECORDINGS'])\n"
                "from backend.main import app  # noqa: E402\n"
            )

        return ruta

    def preparar_dispositivo(self):

        import backend.database as db
        db.DATABASE_PATH = self.db
        db.init_db()

        import backend.devices as dv
        self.device_id, self.token = dv.enroll_device(
            "PC-F3", "Windows 11", "10.0.0.3"
        )

    def arrancar(self):

        self._escribir_lanzador()

        entorno = dict(os.environ)
        entorno.update({
            "PYTHONPATH": self.temporal + os.pathsep + RAIZ,
            "TEST_DB": self.db,
            "TEST_RECORDINGS": self.grabaciones,
            "AUTH_USERNAME": "admin",
            "AUTH_PASSWORD_HASH": self._hash(),
            "AUTH_SECRET_KEY": "0" * 64,
            "AGENT_TOKEN": "token-de-alta-de-prueba"
        })

        self.log = open(os.path.join(self.temporal, "servidor.log"),
                        "w", encoding="utf-8")

        self.proceso = subprocess.Popen(
            [os.path.join(RAIZ, ".venv", "Scripts", "python.exe"),
             "-m", "uvicorn", "srvapp:app",
             "--host", "127.0.0.1", "--port", str(PUERTO),
             "--ssl-certfile", os.path.join(RAIZ, "certs", "dev-server-cert.pem"),
             "--ssl-keyfile", os.path.join(RAIZ, "certs", "dev-server-key.pem")],
            cwd=RAIZ, env=entorno,
            # La salida va a un archivo, NO a una tubería: si nadie lee la
            # tubería, el búfer del sistema se llena y el servidor se queda
            # bloqueado escribiendo su propio log.
            stdout=self.log, stderr=subprocess.STDOUT
        )

        for _ in range(40):
            try:
                requests.get(f"{BASE}/api/health", verify=CA, timeout=2)
                return True
            except Exception:
                time.sleep(0.5)

        return False

    def _hash(self):
        sys.path.insert(0, os.path.join(RAIZ, "backend"))
        from auth import generate_password_hash
        return generate_password_hash("PruebaF3!")

    def parar(self):
        if self.proceso:
            self.proceso.terminate()
            try:
                self.proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proceso.kill()

    def limpiar(self):
        self.parar()
        shutil.rmtree(self.temporal, ignore_errors=True)

    # --- utilidades de consulta ---

    def archivos(self, sufijo=None):
        salida = []
        for raiz, _, nombres in os.walk(self.grabaciones):
            for n in nombres:
                if sufijo and not n.endswith(sufijo):
                    continue
                p = os.path.join(raiz, n)
                salida.append((os.path.relpath(p, self.grabaciones).replace(os.sep, "/"),
                               os.path.getsize(p)))
        return sorted(salida)

    def filas(self):
        c = sqlite3.connect(self.db)
        r = c.execute(
            "SELECT id, path, size_bytes FROM recordings WHERE device_id=?",
            (self.device_id,)
        ).fetchall()
        c.close()
        return r

    def subir(self, nombre, cuerpo, token=None, device=None, duracion=2):
        return requests.post(
            f"{BASE}/api/devices/{device or self.device_id}/recordings/upload",
            params={"filename": nombre,
                    "started_at": "2026-09-26T10:00:00+00:00",
                    "ended_at": "2026-09-26T10:15:00+00:00",
                    "duration_sec": duracion},
            data=cuerpo,
            headers={"X-Agent-Token": token or self.token,
                     "Content-Type": "application/octet-stream"},
            verify=CA, timeout=60)


_mp4_cache = {}


def mp4(n=None, segundos=2):
    """
    Devuelve los bytes de un MP4 REAL, generado una vez con ffmpeg.

    Antes bastaba con datos sintéticos, porque el endpoint solo miraba la
    extensión. Desde F4 el servidor valida el contenido, así que las pruebas
    de F3 —atomicidad, duplicados, concurrencia— necesitan vídeos auténticos:
    de lo contrario se irían todos a cuarentena y no probarían nada.

    El parámetro n se conserva por compatibilidad con las llamadas
    existentes; el tamaño real lo marca la duración.
    """

    import imageio_ffmpeg

    if segundos in _mp4_cache:
        return _mp4_cache[segundos]

    destino = os.path.join(tempfile.gettempdir(), f"f3_fixture_{segundos}s.mp4")

    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner",
         "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc=size=320x240:rate=15",
         "-t", str(segundos), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         destino],
        capture_output=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    )

    with open(destino, "rb") as handle:
        _mp4_cache[segundos] = handle.read()

    return _mp4_cache[segundos]


def main():

    e = Entorno()
    e.preparar_dispositivo()

    if not e.arrancar():
        print("No se pudo arrancar el backend de pruebas")
        e.limpiar()
        return 1

    try:

        print("\n=== 1. Subida normal: el .part desaparece y queda el .mp4 ===")
        r = e.subir("rec_7000000001.mp4", mp4(20000))
        print("     respuesta:", r.status_code, r.json().get("status"))
        comprobar("responde 200 stored", r.status_code == 200)
        comprobar("no queda ningún .part", len(e.archivos(".part")) == 0)
        comprobar("existe el .mp4 final", len(e.archivos(".mp4")) == 1)
        comprobar("hay una fila en la base", len(e.filas()) == 1)
        comprobar("el tamaño de la fila coincide con el disco",
                  e.filas()[0][2] == e.archivos(".mp4")[0][1])

        print("\n=== 2. Corte a mitad: queda .part, ni .mp4 ni fila ===")
        mp4_antes = len(e.archivos(".mp4"))
        filas_antes = len(e.filas())

        def cuerpo_cortado():
            for _ in range(3):
                yield b"X" * 100000
                time.sleep(0.05)
            raise ConnectionError("el Agent se desconecta")

        try:
            e.subir("rec_7000000002.mp4", cuerpo_cortado())
        except Exception:
            pass

        time.sleep(1.0)

        partes = e.archivos(".part")

        # El backend detecta la desconexión y borra el .part en el acto, así
        # que no queda ni siquiera el temporal. Lo esencial se cumple: nunca
        # aparece un .mp4 truncado ni una fila falsa.
        comprobar("NO se creó ningún .mp4 nuevo",
                  len(e.archivos(".mp4")) == mp4_antes)
        comprobar("NO se creó ninguna fila", len(e.filas()) == filas_antes)
        comprobar("el temporal se limpia solo al cortarse la conexión",
                  len(partes) == 0)
        print(f"        .part restantes: {len(partes)} "
              "(se limpian en el acto; los de un proceso muerto los recoge "
              "cleanup_orphan_parts al arrancar)")

        print("\n=== 3. Reintento tras el corte: se completa bien ===")
        r = e.subir("rec_7000000002.mp4", mp4(30000))
        comprobar("el reintento responde 200", r.status_code == 200)
        comprobar("ya no queda .part", len(e.archivos(".part")) == 0)
        comprobar("existe el .mp4 definitivo",
                  any(p.endswith("rec_7000000002.mp4") for p, _ in e.archivos(".mp4")))
        comprobar("y su fila", len(e.filas()) == filas_antes + 1)

        print("\n=== 4. Duplicado legítimo ===")
        r = e.subir("rec_7000000001.mp4", mp4(99999))
        datos = r.json()
        comprobar("responde duplicate", datos.get("duplicate") is True)
        comprobar("no crea una segunda fila",
                  sum(1 for _, p, _ in e.filas() if p.endswith("rec_7000000001.mp4")) == 1)
        comprobar("no deja .part", len(e.archivos(".part")) == 0)

        print("\n=== 5. Subidas simultáneas del mismo archivo ===")
        nombre = "rec_7000000003.mp4"
        hilos = 8
        barrera = threading.Barrier(hilos)
        respuestas = []
        cerrojo = threading.Lock()

        contenido_real = mp4()      # MP4 auténtico: F4 lo acepta

        def concurrente(i):

            def cuerpo():
                # Se envía troceado para alargar la escritura y ensanchar la
                # ventana en la que dos peticiones pueden solaparse.
                for inicio in range(0, len(contenido_real), 8192):
                    yield contenido_real[inicio:inicio + 8192]

            barrera.wait()
            try:
                resp = e.subir(nombre, cuerpo())
                with cerrojo:
                    respuestas.append((resp.status_code, resp.json()))
            except Exception as error:
                with cerrojo:
                    respuestas.append(("error", str(error)[:40]))

        lista = [threading.Thread(target=concurrente, args=(i,)) for i in range(hilos)]
        for h in lista:
            h.start()
        for h in lista:
            h.join()

        filas_nombre = [f for f in e.filas() if f[1].endswith(nombre)]
        duplicados = sum(
            1 for c, d in respuestas
            if isinstance(d, dict) and d.get("duplicate")
        )

        print(f"        {hilos} subidas simultáneas -> "
              f"{len(filas_nombre)} fila(s), {duplicados} respuestas duplicate")
        for c, d in respuestas[:3]:
            print("        respuesta:", c, str(d)[:110])
        comprobar("UNA SOLA fila en la base", len(filas_nombre) == 1)

        archivos_nombre = [p for p, _ in e.archivos(".mp4") if p.endswith(nombre)]
        comprobar("UN SOLO archivo final en disco", len(archivos_nombre) == 1)

        comprobar("todas las respuestas son 200",
                  all(c == 200 for c, _ in respuestas))

        # El fallo que motivó mover la validación antes de publicar: ffmpeg
        # mantenía abierto el archivo definitivo y otra subida simultánea no
        # podía reemplazarlo.
        errores_acceso = [
            d for c, d in respuestas
            if isinstance(d, dict) and "Acceso denegado" in str(d)
        ]
        comprobar("ningún 'Acceso denegado' (WinError 5)",
                  len(errores_acceso) == 0)

        comprobar("no quedan .part huérfanos", len(e.archivos(".part")) == 0)

        # Una gana y las demás reciben la respuesta idempotente
        comprobar("las demás reciben 'duplicate'", duplicados == hilos - 1)

        comprobar("ninguna acabó en cuarentena",
                  not any(isinstance(d, dict) and d.get("status") == "invalid"
                          for _, d in respuestas))

        disco = dict(e.archivos(".mp4"))
        ruta_nombre = [p for p in disco if p.endswith(nombre)][0]
        comprobar("el tamaño de la fila coincide con el archivo",
                  filas_nombre[0][2] == disco[ruta_nombre])

        print("\n=== 6. Límite de 200 MB: 413 y sin residuos ===")
        partes_antes = len(e.archivos(".part"))

        def cuerpo_enorme():
            trozo = b"Y" * (1024 * 1024)
            for _ in range(210):
                yield trozo

        try:
            r = e.subir("rec_7000000004.mp4", cuerpo_enorme())
            codigo = r.status_code
        except Exception:
            codigo = "conexión cortada por el servidor"

        time.sleep(0.8)
        comprobar(f"rechazada ({codigo})", codigo in (413, "conexión cortada por el servidor"))
        comprobar("no queda .part de la subida rechazada",
                  len(e.archivos(".part")) == partes_antes)
        comprobar("no se creó .mp4",
                  not any(p.endswith("rec_7000000004.mp4") for p, _ in e.archivos(".mp4")))
        comprobar("no se creó fila",
                  not any(p.endswith("rec_7000000004.mp4") for _, p, _ in e.filas()))

        print("\n=== 7. Errores permanentes: token ajeno ===")
        r = e.subir("rec_7000000005.mp4", mp4(1000), token="token-que-no-existe")
        comprobar(f"token inválido -> {r.status_code}", r.status_code == 401)
        comprobar("no deja .part", len(e.archivos(".part")) == 0)
        comprobar("no deja .mp4",
                  not any(p.endswith("rec_7000000005.mp4") for p, _ in e.archivos(".mp4")))

        print("\n=== 8. Retención sigue funcionando ===")
        import backend.database as db
        db.DATABASE_PATH = e.db
        import backend.recordings as rec
        from pathlib import Path
        rec.RECORDINGS_DIR = Path(e.grabaciones)

        antes = len(e.filas())
        resultado = rec.apply_retention(90)
        comprobar("apply_retention se ejecuta sin error",
                  isinstance(resultado, dict))
        comprobar("no borra grabaciones recientes", len(e.filas()) == antes)
        print(f"        resultado: {resultado}")

    finally:
        e.limpiar()

    # --- limpieza de .part, sin servidor ---
    print("\n=== 9. Limpieza de .part al arrancar ===")

    temporal = tempfile.mkdtemp(prefix="remoteadmin_f3_part_")

    try:
        import backend.recordings as rec
        from pathlib import Path

        carpeta = os.path.join(temporal, "server_recordings", "dev", "2026", "09", "26")
        os.makedirs(carpeta, exist_ok=True)
        rec.RECORDINGS_DIR = Path(os.path.join(temporal, "server_recordings"))

        antiguo = os.path.join(carpeta, "rec_1.mp4.part")
        reciente = os.path.join(carpeta, "rec_2.mp4.part")
        valido = os.path.join(carpeta, "rec_3.mp4")
        huerfano = os.path.join(carpeta, "rec_huerfano.mp4")

        for p in (antiguo, reciente, valido, huerfano):
            with open(p, "wb") as h:
                h.write(b"x" * 500)

        # El antiguo se envejece 10 horas
        viejo = time.time() - 10 * 3600
        os.utime(antiguo, (viejo, viejo))

        resultado = rec.cleanup_orphan_parts()

        print(f"        resultado: {resultado}")
        comprobar("el .part antiguo se elimina", not os.path.exists(antiguo))
        comprobar("el .part reciente se conserva", os.path.exists(reciente))
        comprobar("el .mp4 válido NO se toca", os.path.exists(valido))
        comprobar("el .mp4 huérfano TAMPOCO se toca", os.path.exists(huerfano))
        comprobar("el recuento es correcto",
                  resultado["deleted"] == 1 and resultado["kept_recent"] == 1)

    finally:
        shutil.rmtree(temporal, ignore_errors=True)

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
