r"""
El servidor sigue la union: anota el dia y retira los trozos.

    .venv\Scripts\python tests/test_jornada_en_el_servidor.py

Cuando el equipo une los segmentos de un dia y borra los trozos, el
servidor tiene que enterarse. Si no, pasa lo peor de los dos mundos:
el catalogo del equipo solo ANADE -nunca retira-, asi que las fichas
de los treinta y seis segmentos se quedan para siempre apuntando a
archivos que ya no existen, y el panel enseña la jornada MAS treinta
y seis fantasmas que no se pueden reproducir.

Por eso el aviso es explicito y no se deduce comparando catalogos.

Las dos reglas que se comprueban aqui:

  no se borra nada        las fichas de los trozos cambian de estado,
                          no desaparecen. Los listados filtran con
                          lista blanca -status IN ('stored','local')-,
                          asi que un estado nuevo sale del panel sin
                          tocar ninguna consulta, y queda el rastro de
                          que ese trozo existio y en que dia acabo

  la ruta no se cree      la calcula el equipo, asi que se valida
                          entera: carpeta, nombre y fecha anunciada
                          tienen que concordar, y el equipo de la
                          ruta tiene que ser el de la conexion
                          autenticada

Y una que protege lo ya archivado: un segmento con copia EN EL
SERVIDOR no se marca. Esa copia sigue existiendo y sigue siendo
reproducible; esconderla seria perderla de vista teniendo archivo.
"""

import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))

os.environ.setdefault(
    "AUTH_SECRET_KEY", "clave-solo-para-jornadas-" + "x" * 20
)

from backend import database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="jornada-srv-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

from backend import recordings as grabaciones  # noqa: E402
from backend import main as servidor  # noqa: E402


EQUIPO = "a9424c593aa98316"


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def ruta_de_segmento(n):
    return "%s/2026/11/09/rec_179000%04d.mp4" % (EQUIPO, n)


def sembrar_segmentos(cuantos):
    """Las fichas que el equipo fue informando al cerrar cada trozo."""

    rutas = []

    for i in range(cuantos):

        ruta = ruta_de_segmento(i)

        grabaciones.register_local_recording(
            device_id=EQUIPO,
            path=ruta,
            started_at="2026-11-09T14:%02d:00+00:00" % i,
            ended_at="2026-11-09T14:%02d:00+00:00" % (i + 1),
            duration_sec=900,
            size_bytes=5 * 1024 * 1024
        )

        rutas.append(ruta)

    return rutas


def filas_visibles():
    """Lo que el panel enseñaria: la lista blanca de estados."""

    conexion = database.get_connection()

    try:
        return [
            f["path"] for f in conexion.execute(
                "SELECT path FROM recordings "
                "WHERE status IN ('stored','local') ORDER BY path"
            )
        ]
    finally:
        conexion.close()


def cuantas_filas():
    conexion = database.get_connection()

    try:
        return conexion.execute(
            "SELECT COUNT(*) c FROM recordings"
        ).fetchone()["c"]
    finally:
        conexion.close()


def prueba_la_ruta_se_valida():

    buena = "%s/2026/11/09/jornada_2026-11-09.mp4" % EQUIPO

    comprobar("la ruta correcta se acepta",
              servidor.ruta_de_jornada_valida(EQUIPO, buena, "2026-11-09"))

    casos = [
        ("otro equipo",
         "otroequipo/2026/11/09/jornada_2026-11-09.mp4", "2026-11-09"),
        ("la fecha del nombre no es la anunciada",
         buena, "2026-11-08"),
        ("el nombre no cuadra con la carpeta",
         "%s/2026/11/09/jornada_2026-11-08.mp4" % EQUIPO, "2026-11-08"),
        ("una ruta con dos puntos",
         "%s/2026/11/09/../../x/jornada_2026-11-09.mp4" % EQUIPO,
         "2026-11-09"),
        ("un segmento disfrazado",
         "%s/2026/11/09/rec_1790000000.mp4" % EQUIPO, "2026-11-09"),
        ("ruta absoluta",
         "C:/x/jornada_2026-11-09.mp4", "2026-11-09"),
        ("vacia", "", "2026-11-09"),
    ]

    for nombre, ruta, fecha in casos:
        comprobar("se rechaza: %s" % nombre,
                  not servidor.ruta_de_jornada_valida(EQUIPO, ruta, fecha),
                  ruta)


def prueba_anota_el_dia_y_retira_los_trozos():

    rutas = sembrar_segmentos(4)

    antes = cuantas_filas()

    comprobar("los cuatro trozos se ven en el panel",
              len(filas_visibles()) == 4, filas_visibles())

    jornada_ruta = "%s/2026/11/09/jornada_2026-11-09.mp4" % EQUIPO

    bien = servidor.anotar_jornada_unida(EQUIPO, {
        "fecha": "2026-11-09",
        "path": jornada_ruta,
        "filename": "jornada_2026-11-09.mp4",
        "size_bytes": 20 * 1024 * 1024,
        "duration_sec": 3600,
        "segmentos": rutas
    })

    comprobar("la jornada se anota", bien)

    visibles = filas_visibles()

    comprobar("en el panel queda UN solo archivo",
              visibles == [jornada_ruta], visibles)

    comprobar("y NO se borro ninguna fila",
              cuantas_filas() == antes + 1,
              "%s filas, antes %s" % (cuantas_filas(), antes))

    conexion = database.get_connection()

    try:
        fila = conexion.execute(
            "SELECT status, invalid_reason FROM recordings WHERE path = ?",
            (rutas[0],)
        ).fetchone()

        comprobar("el trozo queda como unido",
                  fila["status"] == grabaciones.ESTADO_UNIDO, dict(fila))

        comprobar("y dice en que jornada acabo",
                  "2026-11-09" in (fila["invalid_reason"] or ""),
                  fila["invalid_reason"])

        dia = conexion.execute(
            "SELECT started_at, ended_at, duration_sec FROM recordings "
            "WHERE path = ?", (jornada_ruta,)
        ).fetchone()

        # Las fechas del dia salen de las fichas que ya tenia el
        # servidor, no de lo que diga el equipo.
        comprobar("el dia empieza cuando el primer trozo",
                  dia["started_at"] == "2026-11-09T14:00:00+00:00",
                  dia["started_at"])

        comprobar("y acaba cuando el ultimo",
                  dia["ended_at"] == "2026-11-09T14:04:00+00:00",
                  dia["ended_at"])

        comprobar("con su duracion real, no cero",
                  dia["duration_sec"] == 3600, dia["duration_sec"])

    finally:
        conexion.close()


def prueba_no_toca_lo_ya_archivado():
    """
    Un trozo con copia en el servidor no se esconde.

    Esa copia existe y se puede reproducir aunque el equipo borre la
    suya. Marcarla seria perderla de vista teniendo archivo.
    """

    ruta = "%s/2026/11/10/rec_1790009999.mp4" % EQUIPO

    grabaciones.register_local_recording(
        device_id=EQUIPO, path=ruta,
        started_at="2026-11-10T14:00:00+00:00",
        ended_at="2026-11-10T14:15:00+00:00",
        duration_sec=900, size_bytes=5 * 1024 * 1024
    )

    conexion = database.get_connection()

    try:
        conexion.execute(
            "UPDATE recordings SET status = 'stored' WHERE path = ?",
            (ruta,)
        )
        conexion.commit()
    finally:
        conexion.close()

    marcadas = grabaciones.marcar_segmentos_unidos(
        EQUIPO, [ruta], "2026-11-10"
    )

    comprobar("una grabacion archivada no se marca", marcadas == 0)

    comprobar("y sigue viendose en el panel", ruta in filas_visibles())


def prueba_no_toca_los_de_otro_equipo():

    ajeno = "otroequipo/2026/11/09/rec_1790000001.mp4"

    grabaciones.register_local_recording(
        device_id="otroequipo", path=ajeno,
        started_at="2026-11-09T14:00:00+00:00",
        ended_at="2026-11-09T14:15:00+00:00",
        duration_sec=900, size_bytes=1024
    )

    marcadas = grabaciones.marcar_segmentos_unidos(
        EQUIPO, [ajeno], "2026-11-09"
    )

    comprobar("no se marca la grabacion de otro equipo", marcadas == 0)

    comprobar("que sigue visible", ajeno in filas_visibles())


def prueba_una_ruta_mala_no_anota_nada():

    antes = cuantas_filas()

    bien = servidor.anotar_jornada_unida(EQUIPO, {
        "fecha": "2026-11-09",
        "path": "otroequipo/2026/11/09/jornada_2026-11-09.mp4",
        "segmentos": [ruta_de_segmento(0)]
    })

    comprobar("una ruta de otro equipo se rechaza", not bien)

    comprobar("y no deja ninguna fila nueva",
              cuantas_filas() == antes)


def principal():

    prueba_la_ruta_se_valida()
    prueba_anota_el_dia_y_retira_los_trozos()
    prueba_no_toca_lo_ya_archivado()
    prueba_no_toca_los_de_otro_equipo()
    prueba_una_ruta_mala_no_anota_nada()

    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  bien   %s" % nombre)
        else:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
