r"""
El trozo grabado a mano, como una cosa aparte.

    .venv\Scripts\python tests/test_trozo_manual.py

El boton de grabar a mano sirve para capturar algo concreto: un
error que enseñar, una gestion que documentar. Esos trozos se
perdian entre los treinta y seis segmentos de la jornada, y no habia
forma de encontrarlos.

Lo que NO se hizo, y por que
----------------------------

Lo primero que se penso fue recortar un archivo nuevo con ffmpeg.
Se descarto por dos razones que aparecieron al construirlo:

  - un archivo recortado DUPLICA el disco de esos minutos, en
    equipos que andan justos de espacio;

  - y la jornada contaria dos veces el mismo material, porque el
    trozo y los segmentos de los que sale viven en la misma
    carpeta y el catalogo los ve a los dos.

Lo que se hizo
--------------

El equipo corta el segmento al EMPEZAR y al TERMINAR el trozo. Entre
esos dos cortes quedan segmentos ENTEROS, que son exactamente su
material. El trozo es entonces una forma de mirarlos -una fila que
se reproduce seguida-, igual que la jornada.

Sin archivo nuevo, sin disco duplicado, sin recortar por dentro de
nada, y sin tabla nueva: un trozo manual ES una fila de
recording_intervals con origen 'manual', que el Agent ya informaba.

El precio es un corte mas en la jornada. Ya son treinta y seis al
dia y se reproducen seguidos: nadie nota el treinta y siete.
"""

import io
import os
import sys
import tempfile

from datetime import datetime, timedelta, timezone
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-trozos")

from backend import database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="trozos-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

from backend import recordings as grabaciones  # noqa: E402

AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
GRABADOR = io.open(RAIZ / "agent" / "recorder.py", encoding="utf-8").read()
SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def limpiar():

    conexion = database.get_connection()
    conexion.execute("DELETE FROM recordings")
    conexion.execute("DELETE FROM recording_intervals")
    conexion.execute("DELETE FROM devices")
    conexion.commit()
    conexion.close()


BASE = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


def t(minutos):
    return (BASE + timedelta(minutes=minutos)).isoformat()


def equipo(identificador="eq1", hostname="RECEPCION"):

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR REPLACE INTO devices (device_id, hostname) VALUES (?, ?)",
        (identificador, hostname)
    )
    conexion.commit()
    conexion.close()


def segmento(desde, hasta, tamano=45_000_000, identificador="eq1"):

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO recordings (device_id, started_at, ended_at, "
        "duration_sec, size_bytes, path, status, storage_state) "
        "VALUES (?, ?, ?, ?, ?, ?, 'local', 'local_only')",
        (identificador, t(desde), t(hasta), (hasta - desde) * 60, tamano,
         "%s/rec_%d.mp4" % (identificador, 1_000_000 + desde))
    )
    conexion.commit()
    conexion.close()


def tramo(desde, hasta, origen="manual", identificador="eq1"):

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO recording_intervals (device_id, origin, started_at, "
        "ended_at) VALUES (?, ?, ?, ?)",
        (identificador, origen,
         t(desde), t(hasta) if hasta is not None else None)
    )
    conexion.commit()
    conexion.close()


# ==================================================================
# 1. EL CORTE
# ==================================================================

def test_el_grabador_sabe_cortar_cuando_se_le_pide():

    comprobar("Existe el corte bajo demanda",
              "def cortar_segmento(" in GRABADOR)

    cuerpo = GRABADOR.split("def cortar_segmento(", 1)[1][:900]

    comprobar("Si no se graba, no hay nada que cortar",
              "if not self._running:" in cuerpo
              and "return False" in cuerpo)

    # El corte usa el MISMO camino que el de los quince minutos: el
    # segmento se cierra bien, con su archivo terminado y su ficha.
    # Un segundo camino para cerrar un archivo seria un segundo
    # sitio donde dejarlo a medias.
    comprobar("Se corta por el mismo sitio que el corte por tiempo",
              "self._cortar_ya.is_set()" in GRABADOR
              and "self._cortar_ya.clear()" in GRABADOR)

    bucle = GRABADOR.split("def _record_loop(", 1)[1]

    comprobar("Y dentro del bucle, junto al corte por duracion",
              bucle.index("segment_start >= self.segment_seconds")
              < bucle.index("self._cortar_ya.is_set()"))


def test_el_trozo_manual_corta_al_empezar_y_al_terminar():

    arranque = AGENTE.split("def start_screen_recording(", 1)[1][:2600]

    comprobar("Al empezar se corta, si la jornada ya grababa",
              "origen == owners.MANUAL and duenos.grabando()" in arranque
              and "screen_recorder.cortar_segmento()" in arranque)

    # Si no se cortara, el trozo arrastraria los minutos que el
    # segmento en curso llevara abiertos, que nadie pidio grabar.
    comprobar("Y se corta ANTES de tomar el dueno",
              arranque.index("cortar_segmento()")
              < arranque.index("duenos.tomar(origen)"))

    parada = AGENTE.split("def stop_screen_recording(", 1)[1][:3000]

    comprobar("Al terminar tambien se corta",
              "cortar_segmento()" in parada)

    # Y este orden importa de verdad: si se soltara el dueno
    # primero y la jornada no sostuviera la captura, el grabador se
    # pararia y no quedaria segmento que cortar.
    comprobar("Antes de soltar al dueno",
              parada.index("cortar_segmento()")
              < parada.index("duenos.soltar(origen)"),
              "se suelta el dueno antes de cortar")


# ==================================================================
# 2. QUE SEGMENTOS SON DEL TROZO
# ==================================================================

def test_un_trozo_son_segmentos_enteros():

    limpiar()
    equipo()

    # Jornada en segmentos de 15 minutos, con cortes en el 20 y el
    # 35 por el trozo manual.
    segmento(0, 15)
    segmento(15, 20)     # cortado al empezar el trozo
    segmento(20, 35)     # <- el trozo
    segmento(35, 50)

    tramo(20, 35)

    trozos = grabaciones.trozos_manuales()

    comprobar("Sale un trozo", len(trozos) == 1, str(len(trozos)))

    if not trozos:
        return

    uno = trozos[0]

    comprobar("Con los segmentos que caen dentro",
              uno["segmentos"] == 1, str(uno["segmentos"]))

    comprobar("Dura lo que duro",
              uno["duracion_sec"] == 15 * 60, str(uno["duracion_sec"]))

    comprobar("Y lleva el nombre del equipo",
              uno["hostname"] == "RECEPCION", str(uno["hostname"]))


def test_no_se_cuela_un_segmento_que_empezo_antes():
    """
    query_recordings devuelve los que SE SOLAPAN. Un segmento que
    empezo antes del corte se solapa, pero no es material del
    trozo: contiene minutos que nadie pidio grabar a mano.
    """

    limpiar()
    equipo()

    segmento(10, 25)     # empieza antes del trozo
    segmento(25, 40)     # este si

    tramo(25, 40)

    trozos = grabaciones.trozos_manuales()

    comprobar("Solo cuenta el que cae dentro",
              trozos and trozos[0]["segmentos"] == 1,
              str(trozos[0]["segmentos"]) if trozos else "sin trozos")


def test_un_trozo_todavia_abierto_no_se_presenta():
    """
    Sin fin no se sabe cuanto dura ni estan todos sus segmentos
    cerrados. Aparece en cuanto se detenga.
    """

    limpiar()
    equipo()

    segmento(0, 15)
    tramo(0, None)

    comprobar("Un trozo sin terminar no sale",
              grabaciones.trozos_manuales() == [],
              str(grabaciones.trozos_manuales()))


def test_la_jornada_no_cuenta_dos_veces_el_material():
    """
    La razon por la que el trozo NO es un archivo nuevo.

    Si se recortara una copia, viviria en la misma carpeta, el
    catalogo la veria y la jornada sumaria dos veces los mismos
    minutos y los mismos bytes.
    """

    limpiar()
    equipo()

    segmento(0, 15, tamano=1000)
    segmento(15, 30, tamano=1000)

    tramo(15, 30)

    dias = grabaciones.jornadas()

    comprobar("La jornada tiene los segmentos que hay",
              dias and dias[0]["segmentos"] == 2,
              str(dias[0]["segmentos"]) if dias else "sin jornadas")

    comprobar("Y suma lo que ocupan, sin duplicar",
              dias and dias[0]["size_bytes"] == 2000,
              str(dias[0]["size_bytes"]) if dias else "")

    # El trozo existe igualmente, mirando los mismos archivos.
    trozos = grabaciones.trozos_manuales()

    comprobar("El trozo sale sin añadir nada a la jornada",
              len(trozos) == 1 and trozos[0]["segmentos"] == 1,
              str(trozos))


def test_los_segmentos_van_en_orden():
    """
    Es lo que permite reproducirlo seguido.
    """

    limpiar()
    equipo()

    for desde in (45, 15, 30):
        segmento(desde, desde + 15)

    tramo(15, 60)

    trozos = grabaciones.trozos_manuales()

    conexion = database.get_connection()

    horas = [
        conexion.execute(
            "SELECT started_at FROM recordings WHERE id = ?", (uno,)
        ).fetchone()[0]
        for uno in trozos[0]["ids"]
    ]

    conexion.close()

    comprobar("Los segmentos salen en orden de reloj",
              horas == sorted(horas), str(horas))


def test_solo_los_manuales():

    limpiar()
    equipo()

    segmento(0, 15)
    segmento(15, 30)

    tramo(0, 15, origen="continua")
    tramo(15, 30, origen="manual")

    trozos = grabaciones.trozos_manuales()

    comprobar("La jornada no se presenta como trozo manual",
              len(trozos) == 1, str(len(trozos)))


# ==================================================================
# 3. EL ENDPOINT
# ==================================================================

def test_el_panel_los_puede_pedir():

    comprobar("Existe el endpoint",
              '"/api/recordings/clips"' in SERVIDOR)

    cuerpo = SERVIDOR.split("def recordings_clips(", 1)[1][:1800]

    comprobar("Hace falta poder ver grabaciones",
              'require_permission(request, "recordings.view")' in cuerpo)

    comprobar("Y cada empresa ve lo suyo",
              'equipo.get("organization_id") == propia' in cuerpo)


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("EL TROZO GRABADO A MANO")
    print("=" * 66)
    print()

    database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
    database.init_db()

    pruebas = [
        test_el_grabador_sabe_cortar_cuando_se_le_pide,
        test_el_trozo_manual_corta_al_empezar_y_al_terminar,
        test_un_trozo_son_segmentos_enteros,
        test_no_se_cuela_un_segmento_que_empezo_antes,
        test_un_trozo_todavia_abierto_no_se_presenta,
        test_la_jornada_no_cuenta_dos_veces_el_material,
        test_los_segmentos_van_en_orden,
        test_solo_los_manuales,
        test_el_panel_los_puede_pedir,
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      "%s: %s" % (type(error).__name__, error))

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  ok     %s" % nombre)

        else:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    database.DATABASE_PATH = os.path.join(
        tempfile.mkdtemp(prefix="apartado-"), "vacia.db"
    )

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
