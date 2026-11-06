r"""
Una fila por equipo y dia, no treinta y seis de quince minutos.

    .venv\Scripts\python tests/test_jornada_por_dia.py

El equipo graba en segmentos de quince minutos, y eso NO cambia. Un
unico archivo de nueve horas no se puede reproducir mientras se
escribe, no se puede subir a trozos, y un corte de luz a las cuatro
de la tarde se llevaria la jornada entera.

Pero asi es como se guarda, no como se busca. Quien abre el panel
quiere "el lunes de Recepcion", no treinta y seis archivos que
abrir uno a uno hasta dar con el momento que busca. Antes veia:

    3/11  10:15 - 10:30   45 MB
    3/11  10:00 - 10:15   44 MB
    3/11  09:45 - 10:00   45 MB
    ... treinta y tres mas

Lo que vigila este archivo
--------------------------

Sobre todo, que agrupar NO sea perder. Ningun segmento se une, se
reescribe ni se borra: siguen todos en su fila y en su archivo, y
la jornada es una forma de mirarlos. Si algun dia se quita esta
vista, las grabaciones siguen enteras.

Y el dia de quien
-----------------

El dia se corta en la zona horaria DEL EQUIPO. Una jornada mexicana
de 8 a 17 son las 14:00-23:00 UTC en invierno y 13:00-22:00 en
verano: agrupar por UTC partiria unas jornadas si y otras no, segun
la epoca del año, que es la clase de fallo que nadie relaciona con
su causa.
"""

import io
import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-jornadas")

from backend import database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="jornadas-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

from backend import recordings as grabaciones  # noqa: E402


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def limpiar():

    conexion = database.get_connection()
    conexion.execute("DELETE FROM recordings")
    conexion.execute("DELETE FROM recording_schedule")
    conexion.execute("DELETE FROM devices")
    conexion.commit()
    conexion.close()


def equipo(identificador, hostname="PC", zona=None):

    conexion = database.get_connection()

    conexion.execute(
        "INSERT OR REPLACE INTO devices (device_id, hostname) VALUES (?, ?)",
        (identificador, hostname)
    )

    if zona:
        conexion.execute(
            "INSERT OR REPLACE INTO recording_schedule "
            "(device_id, enabled, timezone) VALUES (?, 1, ?)",
            (identificador, zona)
        )

    conexion.commit()
    conexion.close()


BASE = "2026-11-02T10:%02d:00+00:00"


def t(minutos):
    """Un instante del 2 de noviembre, a tantos minutos de las 10:00."""

    return BASE % minutos


def segmento(identificador, inicio, fin, duracion=900, tamano=45_000_000,
             estado="local_only", status="local"):

    conexion = database.get_connection()

    conexion.execute(
        "INSERT INTO recordings (device_id, started_at, ended_at, "
        "duration_sec, size_bytes, path, status, storage_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (identificador, inicio, fin, duracion, tamano,
         "%s/%s.mp4" % (identificador, inicio.replace(":", "")),
         status, estado)
    )

    conexion.commit()
    conexion.close()


# ==================================================================
# 1. LA JORNADA
# ==================================================================

def test_una_jornada_es_una_fila():

    limpiar()
    equipo("eq1", "RECEPCION", "America/Mexico_City")

    # Lunes 2 de noviembre, de 8 a 17 hora de Mexico (UTC-6):
    # nueve horas en segmentos de una hora, por brevedad.
    for hora in range(14, 23):
        segmento(
            "eq1",
            "2026-11-02T%02d:00:00+00:00" % hora,
            "2026-11-02T%02d:00:00+00:00" % (hora + 1),
            duracion=3600
        )

    dias = grabaciones.jornadas()

    comprobar("Nueve segmentos son UNA jornada",
              len(dias) == 1, "salieron %d" % len(dias))

    if not dias:
        return

    dia = dias[0]

    comprobar("Con la fecha del equipo, no la del servidor",
              dia["fecha"] == "2026-11-02", dia["fecha"])

    comprobar("Y dice cuantos trozos son",
              dia["segmentos"] == 9, str(dia["segmentos"]))

    comprobar("Empieza cuando empezo el primero",
              dia["inicio"] == "2026-11-02T14:00:00+00:00", dia["inicio"])

    comprobar("Y acaba cuando acabo el ultimo",
              dia["fin"] == "2026-11-02T23:00:00+00:00", str(dia["fin"]))

    comprobar("Suma lo grabado",
              dia["duracion_grabada_sec"] == 9 * 3600,
              str(dia["duracion_grabada_sec"]))

    comprobar("Y lo que ocupa",
              dia["size_bytes"] == 9 * 45_000_000,
              str(dia["size_bytes"]))

    comprobar("Lleva el nombre del equipo",
              dia["hostname"] == "RECEPCION", str(dia["hostname"]))


def test_una_ficha_a_medias_se_completa_cuando_el_segmento_termina():
    """
    Por que cada grabacion salia con "Duracion: Desconocida".

    El equipo manda su catalogo cada vez que conecta, y ahi ve el
    archivo del segmento que se esta grabando AHORA, a medio
    escribir. Se creaba la ficha con esa foto -medio mega, cero
    segundos- y quince minutos despues, cuando llegaba el aviso del
    segmento TERMINADO con su duracion de verdad, se descartaba por
    existir ya.

    Visto en produccion con la primera grabacion buena en tres
    semanas:

        el equipo informo:  21:35:46 -> 21:50:47   (15 min)
        en la base quedo:   21:36:38 -> 21:36:38   0.5 MB  0 s

    Mismo archivo. La ficha se quedo congelada en el instante en
    que empezo.
    """

    limpiar()
    equipo("eq1")

    # La foto de cuando se estaba escribiendo
    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO recordings (device_id, started_at, ended_at, "
        "duration_sec, size_bytes, path, status, storage_state, keep) "
        "VALUES ('eq1', ?, ?, 0, 524288, 'eq1/rec_1.mp4', "
        "'local', 'local_only', 0)",
        (t(0), t(0))
    )
    conexion.commit()
    conexion.close()

    # Y ahora el aviso del segmento terminado, con lo de verdad
    identificador, creada = grabaciones.register_local_recording(
        "eq1", "eq1/rec_1.mp4", t(0), t(15), 900, 47_000_000
    )

    comprobar("No se duplica la ficha", creada is False)

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT * FROM recordings WHERE id = ?", (identificador,)
    ).fetchone()
    total = conexion.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    conexion.close()

    comprobar("Y sigue habiendo una sola", total == 1, str(total))

    comprobar("Ahora tiene su duracion",
              fila["duration_sec"] == 900, str(fila["duration_sec"]))

    comprobar("Y su tamano de verdad",
              fila["size_bytes"] == 47_000_000, str(fila["size_bytes"]))

    comprobar("Y un fin que no es su inicio",
              fila["ended_at"] == t(15), str(fila["ended_at"]))


def test_completar_medidas_no_resucita_una_grabacion_archivada():
    """
    La regla original existia por algo: una grabacion ya archivada
    en el servidor no puede volver a "solo local" porque llegue un
    aviso del equipo, que conserva su copia.

    Eso se respeta: se completan las MEDIDAS y nunca el estado.
    """

    limpiar()
    equipo("eq1")

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO recordings (device_id, started_at, ended_at, "
        "duration_sec, size_bytes, path, status, storage_state, keep) "
        "VALUES ('eq1', ?, ?, 0, 100, 'eq1/rec_2.mp4', "
        "'stored', 'server_stored', 1)",
        (t(0), t(0))
    )
    conexion.commit()
    conexion.close()

    grabaciones.register_local_recording(
        "eq1", "eq1/rec_2.mp4", t(0), t(15), 900, 47_000_000
    )

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT * FROM recordings WHERE path = 'eq1/rec_2.mp4'"
    ).fetchone()
    conexion.close()

    comprobar("Sigue archivada en el servidor",
              fila["storage_state"] == "server_stored"
              and fila["status"] == "stored",
              "%s / %s" % (fila["status"], fila["storage_state"]))

    comprobar("Y sigue marcada para conservar", fila["keep"] == 1)

    comprobar("Pero sus medidas si se completan",
              fila["duration_sec"] == 900)


def test_una_medida_mas_vieja_no_pisa_a_una_mejor():
    """
    Un archivo que se escribe CRECE. Si llega un tamano menor es
    una foto mas vieja que la que ya hay, y hacerle caso seria
    retroceder.
    """

    limpiar()
    equipo("eq1")

    grabaciones.register_local_recording(
        "eq1", "eq1/rec_3.mp4", t(0), t(15), 900, 47_000_000
    )

    # Llega una foto antigua, del archivo a medio escribir
    grabaciones.register_local_recording(
        "eq1", "eq1/rec_3.mp4", t(0), t(0), 0, 500_000
    )

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT * FROM recordings WHERE path = 'eq1/rec_3.mp4'"
    ).fetchone()
    conexion.close()

    comprobar("El tamano no retrocede",
              fila["size_bytes"] == 47_000_000, str(fila["size_bytes"]))

    comprobar("Ni la duracion",
              fila["duration_sec"] == 900, str(fila["duration_sec"]))

    comprobar("Ni el fin",
              fila["ended_at"] == t(15), str(fila["ended_at"]))


def test_los_segmentos_van_en_orden():
    """
    Es lo que permite reproducir el dia seguido. Si llegaran
    desordenados, el video saltaria de las once a las nueve.
    """

    limpiar()
    equipo("eq1")

    # Se insertan al reves a proposito
    for hora in (12, 9, 15, 10):
        segmento("eq1",
                 "2026-11-02T%02d:00:00+00:00" % hora,
                 "2026-11-02T%02d:15:00+00:00" % hora)

    dia = grabaciones.jornadas()[0]

    conexion = database.get_connection()

    horas = [
        conexion.execute(
            "SELECT started_at FROM recordings WHERE id = ?", (uno,)
        ).fetchone()[0]
        for uno in dia["ids"]
    ]

    conexion.close()

    comprobar("Los trozos salen en orden de reloj",
              horas == sorted(horas), str(horas))


def test_cada_dia_por_su_lado():

    limpiar()
    equipo("eq1")

    segmento("eq1", "2026-11-02T10:00:00+00:00", "2026-11-02T10:15:00+00:00")
    segmento("eq1", "2026-11-03T10:00:00+00:00", "2026-11-03T10:15:00+00:00")
    segmento("eq1", "2026-11-03T11:00:00+00:00", "2026-11-03T11:15:00+00:00")

    dias = grabaciones.jornadas()

    comprobar("Dos dias, dos filas", len(dias) == 2, str(len(dias)))

    comprobar("La mas reciente primero",
              dias[0]["fecha"] == "2026-11-03", dias[0]["fecha"])

    comprobar("Y cada una con lo suyo",
              dias[0]["segmentos"] == 2 and dias[1]["segmentos"] == 1,
              "%d y %d" % (dias[0]["segmentos"], dias[1]["segmentos"]))


def test_cada_equipo_por_su_lado():

    limpiar()
    equipo("eq1", "RECEPCION")
    equipo("eq2", "ALMACEN")

    segmento("eq1", "2026-11-02T10:00:00+00:00", "2026-11-02T10:15:00+00:00")
    segmento("eq2", "2026-11-02T10:00:00+00:00", "2026-11-02T10:15:00+00:00")

    dias = grabaciones.jornadas()

    comprobar("El mismo dia de dos equipos son dos jornadas",
              len(dias) == 2, str(len(dias)))

    comprobar("Y se pueden pedir las de uno solo",
              len(grabaciones.jornadas(device_id="eq1")) == 1)


# ==================================================================
# 2. EL DIA DE QUIEN
# ==================================================================

def test_el_dia_se_corta_en_la_hora_del_equipo():
    """
    El caso que obliga a hacerlo asi.

    Un equipo en Mexico que grabe hasta las 20:00 locales: eso son
    las 02:00 UTC del dia SIGUIENTE. Agrupando por UTC, esa parte de
    la tarde aparecia como una jornada aparte al dia siguiente.
    """

    limpiar()
    equipo("mx", "PC-MX", "America/Mexico_City")

    # 18:00 locales del lunes = 00:00 UTC del martes
    segmento("mx", "2026-11-03T00:00:00+00:00", "2026-11-03T00:15:00+00:00")

    # 10:00 locales del lunes = 16:00 UTC del lunes
    segmento("mx", "2026-11-02T16:00:00+00:00", "2026-11-02T16:15:00+00:00")

    dias = grabaciones.jornadas()

    comprobar("La tarde no se va al dia siguiente",
              len(dias) == 1, "salieron %d jornadas" % len(dias))

    if dias:
        comprobar("Todo cae en el lunes del equipo",
                  dias[0]["fecha"] == "2026-11-02"
                  and dias[0]["segmentos"] == 2,
                  "%s con %d" % (dias[0]["fecha"], dias[0]["segmentos"]))

    # Y sin zona configurada no se inventa ninguna: se agrupa por
    # UTC, que es lo unico que el servidor sabe con certeza.
    limpiar()
    equipo("sinzona", "PC-SZ")

    segmento("sinzona", "2026-11-03T00:00:00+00:00",
             "2026-11-03T00:15:00+00:00")
    segmento("sinzona", "2026-11-02T16:00:00+00:00",
             "2026-11-02T16:15:00+00:00")

    comprobar("Sin zona conocida se agrupa por UTC, sin inventar",
              len(grabaciones.jornadas()) == 2)


def test_el_dia_se_corta_con_el_desfase_que_informa_el_equipo():
    """
    El caso REAL, que la zona configurada no cubria.

    La zona del horario suele estar puesta como "local" -que
    significa "la hora del equipo"- y eso el servidor no lo puede
    resolver. Acababa agrupando por UTC, y una jornada mexicana cae
    a caballo de dos fechas UTC.

    En el panel salia esto:

        2026-10-14   inicio 06:02 p.m.   fin 02:21 p.m.

    Un fin ANTERIOR a su inicio, porque la fila mezclaba dos tardes
    locales. No era un dato mal guardado: era el dia cortado por el
    sitio equivocado.

    Ahora el propio equipo informa cuantos minutos va respecto a
    UTC, y el corte se hace con eso.
    """

    limpiar()
    equipo("mx", "PC-MX")          # sin zona configurada

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET utc_offset_minutes = ? WHERE device_id = ?",
        (-360, "mx")               # Mexico: seis horas por detras
    )
    conexion.commit()
    conexion.close()

    # 18:00 locales del lunes = 00:00 UTC del martes
    segmento("mx", "2026-11-03T00:00:00+00:00",
             "2026-11-03T00:15:00+00:00")

    # 10:00 locales del lunes = 16:00 UTC del lunes
    segmento("mx", "2026-11-02T16:00:00+00:00",
             "2026-11-02T16:15:00+00:00")

    dias = grabaciones.jornadas()

    comprobar("Las dos caen en el mismo dia del equipo",
              len(dias) == 1, "salieron %d jornadas" % len(dias))

    if not dias:
        return

    comprobar("Y es el lunes, su dia",
              dias[0]["fecha"] == "2026-11-02", dias[0]["fecha"])

    comprobar("Con los dos segmentos",
              dias[0]["segmentos"] == 2, str(dias[0]["segmentos"]))

    # Y lo que se veia mal: el fin no puede ir antes que el inicio.
    inicio = grabaciones._momento(dias[0]["inicio"])
    fin = grabaciones._momento(dias[0]["fin"])

    comprobar("El fin no va antes que el inicio",
              fin >= inicio,
              "%s -> %s" % (dias[0]["inicio"], dias[0]["fin"]))


def test_un_equipo_que_no_ha_dicho_su_hora_no_rompe_nada():
    """
    Un Agent anterior no informa el desfase. Ahi se agrupa por UTC,
    que es lo unico que se sabe, y no se inventa ninguno.
    """

    limpiar()
    equipo("viejo", "PC-VIEJO")

    segmento("viejo", "2026-11-02T16:00:00+00:00",
             "2026-11-02T16:15:00+00:00")

    dias = grabaciones.jornadas()

    comprobar("Se agrupa igual, sin inventar hora",
              len(dias) == 1 and dias[0]["fecha"] == "2026-11-02",
              str(dias))


def test_una_zona_que_no_existe_no_revienta():
    """
    La zona la escribe una persona en el panel. Si se equivoca, la
    jornada no puede dejar de verse.
    """

    limpiar()
    equipo("raro", "PC-R", "Marte/Olympus")

    segmento("raro", "2026-11-02T16:00:00+00:00",
             "2026-11-02T16:15:00+00:00")

    dias = grabaciones.jornadas()

    comprobar("Con una zona inventada se sigue viendo la jornada",
              len(dias) == 1, str(len(dias)))


# ==================================================================
# 3. AGRUPAR NO ES PERDER
# ==================================================================

def test_no_se_toca_ni_un_segmento():
    """
    Lo mas importante del archivo.

    La jornada es una forma de MIRAR. Si para conseguirla se
    uniesen archivos, se reescribiesen filas o se borrase algo, no
    valdria la pena: el dia que se quite esta vista, las
    grabaciones tienen que seguir enteras.
    """

    limpiar()
    equipo("eq1")

    for hora in range(9, 13):
        segmento("eq1",
                 "2026-11-02T%02d:00:00+00:00" % hora,
                 "2026-11-02T%02d:15:00+00:00" % hora)

    conexion = database.get_connection()

    antes = conexion.execute(
        "SELECT id, path, started_at, size_bytes, storage_state "
        "FROM recordings ORDER BY id"
    ).fetchall()

    conexion.close()

    grabaciones.jornadas()
    grabaciones.jornadas(device_id="eq1")

    conexion = database.get_connection()

    despues = conexion.execute(
        "SELECT id, path, started_at, size_bytes, storage_state "
        "FROM recordings ORDER BY id"
    ).fetchall()

    conexion.close()

    comprobar("Siguen los mismos segmentos",
              len(antes) == len(despues) == 4,
              "%d antes, %d despues" % (len(antes), len(despues)))

    comprobar("Con su id, su ruta, su hora y su tamano intactos",
              [tuple(fila) for fila in antes]
              == [tuple(fila) for fila in despues])

    comprobar("Y se siguen pudiendo pedir uno a uno",
              len(grabaciones.query_recordings(device_id="eq1")) == 4)


def test_una_jornada_a_medias_se_ve_a_medias():
    """
    Parte en el servidor y parte todavia en el equipo es lo normal:
    solo sube lo que alguien archiva. El reproductor necesita
    saberlo pieza a pieza, asi que la jornada lo cuenta.
    """

    limpiar()
    equipo("eq1")

    segmento("eq1", "2026-11-02T09:00:00+00:00", "2026-11-02T09:15:00+00:00",
             estado="server_stored", status="stored")
    segmento("eq1", "2026-11-02T10:00:00+00:00", "2026-11-02T10:15:00+00:00")
    segmento("eq1", "2026-11-02T11:00:00+00:00", "2026-11-02T11:15:00+00:00")

    dia = grabaciones.jornadas()[0]

    comprobar("Dice cuantos trozos estan en el servidor",
              dia["en_el_servidor"] == 1, str(dia["en_el_servidor"]))

    comprobar("Y cuantos solo en el equipo",
              dia["solo_en_el_equipo"] == 2,
              str(dia["solo_en_el_equipo"]))


def test_un_dia_con_huecos_no_se_disimula():
    """
    El equipo apagado, la jornada interrumpida, un corte de luz.

    De punta a punta dura mas que lo grabado, y esa diferencia es
    informacion: no se rellena ni se esconde.
    """

    limpiar()
    equipo("eq1")

    # 09:00-09:15 y 16:00-16:15: quince minutos grabados de siete
    # horas de jornada.
    segmento("eq1", "2026-11-02T09:00:00+00:00",
             "2026-11-02T09:15:00+00:00", duracion=900)
    segmento("eq1", "2026-11-02T16:00:00+00:00",
             "2026-11-02T16:15:00+00:00", duracion=900)

    dia = grabaciones.jornadas()[0]

    comprobar("Lo grabado es lo grabado, media hora",
              dia["duracion_grabada_sec"] == 1800,
              str(dia["duracion_grabada_sec"]))

    comprobar("Y de punta a punta son siete horas y cuarto",
              dia["inicio"] == "2026-11-02T09:00:00+00:00"
              and dia["fin"] == "2026-11-02T16:15:00+00:00",
              "%s -> %s" % (dia["inicio"], dia["fin"]))


def test_el_panel_abre_por_dia_y_reproduce_seguido():
    """
    La jornada no sirve de nada si hay que pedirla.

    Quien abre Grabaciones quiere "el lunes de Recepcion". Los
    treinta y seis segmentos sueltos son la respuesta a una
    pregunta que nadie hace, asi que la vista por dia es la que
    sale primero y las otras quedan a un clic.
    """

    JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()

    comprobar("Hay tres vistas",
              'data-vista="jornadas"' in HTML
              and 'data-vista="trozos"' in HTML
              and 'data-vista="segmentos"' in HTML)

    comprobar("Y se abre por dia",
              'let vistaDeGrabaciones = "jornadas";' in JS)

    comprobar("Al entrar se pinta la vista elegida, no la lista cruda",
              "cambiarVistaDeGrabaciones(vistaDeGrabaciones)" in JS)

    # --- reproducir seguido ---

    cuerpo = JS.split("function reproducirSeguido(", 1)[1][:1800]

    comprobar("Se reproduce la lista entera, no un segmento",
              "colaDeReproduccion = ids.slice()" in cuerpo)

    cola = JS.split("function siguienteDeLaCola(", 1)[1][:1600]

    comprobar("Al terminar uno empieza el siguiente",
              "video.onended" in cola
              and "siguienteDeLaCola()" in cola)

    comprobar("Y se dice cuantos quedan",
              "quedan ${quedan}" in cola)

    # El nombre del equipo, como TEXTO: lo escribe una persona y el
    # hostname lo manda el equipo. Ninguno es marcado de confianza.
    agrupadas = JS.split("function pintarAgrupadas(", 1)[1][:3000]

    comprobar("El nombre del equipo se pinta como texto",
              "celdaEquipo.textContent" in agrupadas)


def test_sin_grabaciones_no_hay_jornadas():

    limpiar()

    comprobar("Una base vacia devuelve una lista vacia",
              grabaciones.jornadas() == [])


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("UNA FILA POR EQUIPO Y DIA")
    print("=" * 66)
    print()

    database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
    database.init_db()

    pruebas = [
        test_una_jornada_es_una_fila,
        test_una_ficha_a_medias_se_completa_cuando_el_segmento_termina,
        test_completar_medidas_no_resucita_una_grabacion_archivada,
        test_una_medida_mas_vieja_no_pisa_a_una_mejor,
        test_los_segmentos_van_en_orden,
        test_cada_dia_por_su_lado,
        test_cada_equipo_por_su_lado,
        test_el_dia_se_corta_en_la_hora_del_equipo,
        test_el_dia_se_corta_con_el_desfase_que_informa_el_equipo,
        test_un_equipo_que_no_ha_dicho_su_hora_no_rompe_nada,
        test_una_zona_que_no_existe_no_revienta,
        test_no_se_toca_ni_un_segmento,
        test_una_jornada_a_medias_se_ve_a_medias,
        test_un_dia_con_huecos_no_se_disimula,
        test_el_panel_abre_por_dia_y_reproduce_seguido,
        test_sin_grabaciones_no_hay_jornadas,
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

    # La base se aparta a un temporal vacio: dejarla apuntando a la
    # que se acaba de borrar mataria a la suite siguiente en un
    # sitio que no tiene nada que ver con la causa.
    database.DATABASE_PATH = os.path.join(
        tempfile.mkdtemp(prefix="apartado-"), "vacia.db"
    )

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
