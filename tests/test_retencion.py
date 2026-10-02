"""
Pruebas de la retencion configurable de grabaciones (15/30/90 dias) y de
la marca 'conservar'.

    .venv\\Scripts\\python tests/test_retencion.py

Trabaja con archivos REALES en una carpeta temporal y una base temporal.
Nunca toca server_recordings ni las grabaciones de produccion.
"""

import os
import sys
import tempfile

from datetime import datetime, timezone, timedelta
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-retencion")

import backend.database as database

BASE_TEMPORAL = Path(tempfile.mkdtemp(prefix="retencion_"))
database.DATABASE_PATH = BASE_TEMPORAL / "prueba.db"
database.init_db()

import backend.recordings as recordings
import backend.settings as settings

# Las grabaciones de prueba viven en su propia carpeta temporal
CARPETA = BASE_TEMPORAL / "grabaciones"
CARPETA.mkdir(parents=True, exist_ok=True)
recordings.RECORDINGS_DIR = CARPETA


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def limpiar():

    conexion = database.get_connection()
    conexion.execute("DELETE FROM recordings")
    conexion.execute("DELETE FROM settings")
    conexion.commit()
    conexion.close()

    for archivo in CARPETA.glob("*.mp4"):
        archivo.unlink()


def crear_grabacion(nombre, dias_de_antiguedad, keep=False,
                    device_id="equipo-1"):
    """Crea un archivo real y su fila, con la antiguedad que se pida."""

    ruta = CARPETA / nombre
    ruta.write_bytes(b"contenido de prueba")

    fin = datetime.now(timezone.utc) - timedelta(days=dias_de_antiguedad)

    conexion = database.get_connection()

    conexion.execute(
        """
        INSERT INTO recordings (
            device_id, started_at, ended_at, duration_sec,
            size_bytes, path, status, keep
        )
        VALUES (?, ?, ?, 10, 19, ?, 'stored', ?)
        """,
        (device_id, fin.isoformat(), fin.isoformat(), str(ruta),
         1 if keep else 0)
    )

    conexion.commit()
    conexion.close()

    return ruta


def filas():

    conexion = database.get_connection()
    total = conexion.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    conexion.close()

    return total


# ==============================
# 1. Configuracion
# ==============================

def test_valor_por_defecto():

    limpiar()

    comprobar("Por defecto se conservan 90 dias",
              settings.get_retention_days() == 90)


def test_los_tres_periodos_se_guardan():

    limpiar()

    for dias in ("15", "30", "90"):

        settings.save_settings({"recording_retention_days": dias})

        comprobar(f"Se puede configurar {dias} dias",
                  settings.get_retention_days() == int(dias))


def test_un_periodo_invalido_se_rechaza():

    limpiar()

    rechazados = 0

    for valor in ("0", "1", "7", "365", "-30", "abc", ""):
        try:
            settings.save_settings({"recording_retention_days": valor})
        except settings.UnknownSettingError:
            rechazados += 1

    comprobar("Solo se admiten 15, 30 y 90", rechazados == 7,
              str(rechazados))

    comprobar("Y el valor vigente no cambia",
              settings.get_retention_days() == 90)


def test_un_valor_corrupto_no_borra_de_mas():
    """Si alguien mete a mano un valor raro, se vuelve al predeterminado."""

    limpiar()

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO settings (key, value) VALUES "
        "('recording_retention_days', '0')"
    )
    conexion.commit()
    conexion.close()

    comprobar("Un valor invalido en la base no se obedece",
              settings.get_retention_days() == 90)


# ==============================
# 2. Limpieza
# ==============================

def test_retencion_de_15_dias():

    limpiar()

    vieja = crear_grabacion("rec_vieja.mp4", 20)
    nueva = crear_grabacion("rec_nueva.mp4", 10)

    recordings.apply_retention(days=15)

    comprobar("Con 15 dias se borra la de 20 dias", not vieja.exists())
    comprobar("Y se conserva la de 10", nueva.exists())
    comprobar("La fila de la borrada desaparece", filas() == 1)


def test_retencion_de_30_dias():

    limpiar()

    vieja = crear_grabacion("rec_vieja.mp4", 40)
    media = crear_grabacion("rec_media.mp4", 20)

    recordings.apply_retention(days=30)

    comprobar("Con 30 dias se borra la de 40", not vieja.exists())
    comprobar("Y sobrevive la de 20", media.exists())


def test_retencion_de_90_dias():

    limpiar()

    vieja = crear_grabacion("rec_vieja.mp4", 100)
    media = crear_grabacion("rec_media.mp4", 40)

    recordings.apply_retention(days=90)

    comprobar("Con 90 dias se borra la de 100", not vieja.exists())
    comprobar("Y sobrevive la de 40", media.exists())


def test_no_borra_lo_que_esta_dentro_del_periodo():

    limpiar()

    dentro = [
        crear_grabacion(f"rec_{i}.mp4", dias)
        for i, dias in enumerate((0, 1, 14, 14))
    ]

    recordings.apply_retention(days=15)

    comprobar("Nada dentro del periodo se borra",
              all(r.exists() for r in dentro))

    comprobar("Ni se pierde ninguna fila", filas() == 4)


def test_justo_en_el_limite():

    limpiar()

    crear_grabacion("rec_limite.mp4", 15)

    recordings.apply_retention(days=15)

    comprobar("Una grabacion de exactamente 15 dias ya ha vencido",
              filas() == 0)


# ==============================
# 3. Conservar
# ==============================

def test_keep_protege_de_la_retencion():

    limpiar()

    protegida = crear_grabacion("rec_protegida.mp4", 500, keep=True)
    normal = crear_grabacion("rec_normal.mp4", 500)

    recordings.apply_retention(days=15)

    comprobar("Una grabacion marcada como conservar NO se borra",
              protegida.exists())

    comprobar("Aunque tenga 500 dias y el periodo sea de 15",
              filas() == 1)

    comprobar("La no marcada si se borra", not normal.exists())


def test_quitar_keep_la_devuelve_a_la_retencion():

    limpiar()

    archivo = crear_grabacion("rec_protegida.mp4", 500, keep=True)

    conexion = database.get_connection()
    recording_id = conexion.execute(
        "SELECT id FROM recordings"
    ).fetchone()[0]
    conexion.close()

    recordings.apply_retention(days=15)

    comprobar("Mientras esta marcada, sobrevive", archivo.exists())

    recordings.set_keep(recording_id, False)

    recordings.apply_retention(days=15)

    comprobar("Al desmarcarla, la siguiente limpieza se la lleva",
              not archivo.exists())

    comprobar("Y su fila tambien", filas() == 0)


def test_volver_a_marcar():

    limpiar()

    archivo = crear_grabacion("rec_x.mp4", 500)

    conexion = database.get_connection()
    recording_id = conexion.execute("SELECT id FROM recordings").fetchone()[0]
    conexion.close()

    recordings.set_keep(recording_id, True)

    comprobar("La marca se guarda",
              recordings.get_recording(recording_id)["keep"] == 1)

    recordings.apply_retention(days=15)

    comprobar("Y protege el archivo", archivo.exists())


# ==============================
# 4. Seguridad del borrado
# ==============================

def test_no_borra_fuera_de_su_carpeta():
    """La retencion nunca debe tocar un archivo ajeno."""

    limpiar()

    intruso = BASE_TEMPORAL / "no_tocar.mp4"
    intruso.write_bytes(b"archivo ajeno")

    fin = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat()

    conexion = database.get_connection()
    conexion.execute(
        """
        INSERT INTO recordings (
            device_id, started_at, ended_at, duration_sec,
            size_bytes, path, status, keep
        )
        VALUES ('equipo-1', ?, ?, 10, 13, ?, 'stored', 0)
        """,
        (fin, fin, str(intruso))
    )
    conexion.commit()
    conexion.close()

    recordings.apply_retention(days=15)

    comprobar(
        "Un archivo fuera de la carpeta de grabaciones no se borra",
        intruso.exists()
    )

    intruso.unlink()


def test_path_traversal_en_la_ruta():

    limpiar()

    victima = BASE_TEMPORAL / "victima.mp4"
    victima.write_bytes(b"no tocar")

    fin = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat()

    ruta_trampa = str(CARPETA / ".." / "victima.mp4")

    conexion = database.get_connection()
    conexion.execute(
        """
        INSERT INTO recordings (
            device_id, started_at, ended_at, duration_sec,
            size_bytes, path, status, keep
        )
        VALUES ('equipo-1', ?, ?, 10, 8, ?, 'stored', 0)
        """,
        (fin, fin, ruta_trampa)
    )
    conexion.commit()
    conexion.close()

    recordings.apply_retention(days=15)

    comprobar("Una ruta con '..' no saca el borrado de su carpeta",
              victima.exists())

    if victima.exists():
        victima.unlink()


def test_no_toca_grabaciones_que_no_estan_almacenadas():

    limpiar()

    archivo = crear_grabacion("rec_invalida.mp4", 500)

    conexion = database.get_connection()
    conexion.execute("UPDATE recordings SET status = 'invalid'")
    conexion.commit()
    conexion.close()

    recordings.apply_retention(days=15)

    comprobar("Una grabacion en cuarentena no la borra la retencion",
              archivo.exists())


# ==============================

def main():

    pruebas = [
        test_valor_por_defecto,
        test_los_tres_periodos_se_guardan,
        test_un_periodo_invalido_se_rechaza,
        test_un_valor_corrupto_no_borra_de_mas,
        test_retencion_de_15_dias,
        test_retencion_de_30_dias,
        test_retencion_de_90_dias,
        test_no_borra_lo_que_esta_dentro_del_periodo,
        test_justo_en_el_limite,
        test_keep_protege_de_la_retencion,
        test_quitar_keep_la_devuelve_a_la_retencion,
        test_volver_a_marcar,
        test_no_borra_fuera_de_su_carpeta,
        test_path_traversal_en_la_ruta,
        test_no_toca_grabaciones_que_no_estan_almacenadas
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:80])

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
