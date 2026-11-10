r"""
En Grabaciones se puede filtrar por equipo, y por su nombre.

    .venv\Scripts\python tests/test_filtro_de_grabaciones.py

El desplegable de equipos salia con una sola opcion, "Todos", asi que
no habia por quien filtrar.

La causa: el desplegable lo llenaba populateRecordingsDeviceFilter(),
que solo se llama dentro de loadRecordings() -el cargador de
SEGMENTOS-. Y Grabaciones no abre por segmentos: abre por DIA, y ese
camino sale antes:

    if (cual === "segmentos") {
        loadRecordings();
        return;
    }
    ...
    const respuesta = await fetch(destino);   // sin pasar por ahi

Asi que el desplegable se quedaba con el unico <option> que trae
escrito el index.html.

Eran tres sintomas de la misma causa:

  el desplegable vacio   nadie lo llenaba

  los filtros ignorados  la vista por dia pedia /api/recordings/days
                         SIN parametros. Aunque el desplegable
                         funcionara, elegir un equipo no cambiaba
                         nada

  Buscar cambiaba de     el boton colgaba de loadRecordings, el
  vista sin avisar       cargador de segmentos: pulsarlo en la vista
                         por dia te sacaba de ella

El servidor no tenia nada que ver: /api/recordings/days y
/api/recordings/clips aceptan device_id, start y end desde que se
escribieron. Solo no se los mandaba nadie.

Y una cuarta cosa, que es la que pidio quien usa el panel: el
desplegable iba a construirse con el hostname. Tres hostnames estan
repetidos entre seis equipos -las maquinas se clonaron de la misma
imagen-, asi que habria salido "DESKTOP-8J0TJK8" tres veces. Un
filtro que existe y no se puede usar. Va con nombreDeEquipo(), que
es el alias si lo tiene.
"""

import io
import json
import os
import re
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))

os.environ.setdefault(
    "AUTH_SECRET_KEY", "clave-solo-para-filtro-" + "x" * 20
)

PANEL = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()

SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def cuerpo(nombre_funcion):
    """El cuerpo de una funcion del panel, hasta la siguiente."""

    inicio = PANEL.index(nombre_funcion)

    resto = PANEL[inicio + len(nombre_funcion):]

    corte = re.search(r"\nfunction |\nasync function ", resto)

    return resto[:corte.start()] if corte else resto


def prueba_el_html_solo_trae_todos():
    """
    El punto de partida, que es lo que hacia el sintoma tan
    desconcertante: el desplegable NO esta vacio en el HTML, trae una
    opcion. Por eso parecia que funcionaba y simplemente no habia
    equipos.
    """

    comprobar(
        "el HTML trae 'Todos' y nada mas",
        HTML.count('id="recordings-device"') == 1
        and '<option value="">Todos</option>' in HTML
    )


def prueba_la_vista_por_dia_llena_el_desplegable():
    """
    La correccion del sintoma visible.
    """

    trozo = cuerpo("async function cambiarVistaDeGrabaciones(cual) {")

    comprobar(
        "la vista por dia llena el filtro de equipos",
        "populateRecordingsDeviceFilter()" in trozo
    )

    comprobar(
        "y lo espera, para que las opciones esten antes de leerlas",
        "await populateRecordingsDeviceFilter()" in trozo
    )


def prueba_la_vista_por_dia_manda_los_filtros():
    """
    Lo que de verdad pedia quien usa el panel: que filtrar filtre.
    """

    trozo = cuerpo("async function cambiarVistaDeGrabaciones(cual) {")

    comprobar(
        "la vista por dia construye la consulta",
        "buildRecordingsQuery()" in trozo
    )

    comprobar(
        "y la pega a la direccion que pide",
        "?${consulta}" in trozo or "? `${base}?${consulta}`" in trozo,
        trozo[trozo.find("const destino"):][:120]
    )

    comprobar(
        "ya no se pide la direccion pelada",
        "await fetch(destino)" in trozo
        and "const destino = cual === " not in trozo
    )


def prueba_el_servidor_ya_aceptaba_los_filtros():
    """
    Se comprueba para dejar constancia de que el fallo estaba
    entero en el panel: si el servidor no los aceptara, mandarlos no
    arreglaria nada y la correccion seria falsa.
    """

    for ruta in ("/api/recordings/days", "/api/recordings/clips"):

        trozo = SERVIDOR[SERVIDOR.index('"%s"' % ruta):][:400]

        comprobar(
            "%s acepta device_id" % ruta,
            "device_id" in trozo
        )

        comprobar(
            "%s acepta el rango start/end" % ruta,
            "start" in trozo and "end" in trozo
        )


def prueba_buscar_no_cambia_de_vista():
    """
    El tercer sintoma, y el mas confuso de los tres: el boton
    funcionaba, pero te sacaba de donde estabas.
    """

    comprobar(
        "existe una busqueda que respeta la vista",
        "function buscarEnGrabaciones()" in PANEL
    )

    comprobar(
        "Buscar ya no cuelga del cargador de segmentos",
        '"recordings-search-button"\n)\n.addEventListener(\n"click",\n'
        'buscarEnGrabaciones\n);' in PANEL
    )

    trozo = cuerpo("function buscarEnGrabaciones() {")

    comprobar(
        "en segmentos sigue usando su cargador",
        'vistaDeGrabaciones === "segmentos"' in trozo
        and "loadRecordings()" in trozo
    )

    comprobar(
        "y en las demas recarga la vista en la que se esta",
        "cambiarVistaDeGrabaciones(vistaDeGrabaciones)" in trozo
    )

    comprobar(
        "Limpiar tambien respeta la vista",
        "buscarEnGrabaciones();" in PANEL
        and PANEL.count("buscarEnGrabaciones") >= 3
    )


def prueba_las_opciones_van_por_el_nombre_puesto():
    """
    Lo que pidio quien usa el panel, con todas sus letras: "filtrar
    por nombre de dispositivo o el que yo le puse, como kiosko1,
    calidad".

    No es una preferencia: tres hostnames estan repetidos entre seis
    equipos. Por hostname el filtro no se podria usar.
    """

    trozo = cuerpo("async function populateRecordingsDeviceFilter() {")

    comprobar(
        "las opciones usan nombreDeEquipo()",
        "nombreDeEquipo(" in trozo
    )

    comprobar(
        "ya NO se arma el nombre a mano con el hostname",
        "d.hostname || d.device_id" not in trozo
    )

    comprobar(
        "el valor que se manda sigue siendo el device_id",
        "opcion.value = equipo.device_id" in trozo
    )

    comprobar(
        "la lista va ordenada por nombre",
        "localeCompare(" in trozo
    )


def prueba_el_alias_no_puede_romper_el_desplegable():
    """
    El alias lo escribe una persona. Pegarlo como HTML en texto es
    como se cuela medio problema de este tipo.
    """

    trozo = cuerpo("async function populateRecordingsDeviceFilter() {")

    comprobar(
        "las opciones se crean como elementos, no como texto",
        "document.createElement(\"option\")" in trozo
    )

    comprobar(
        "y el nombre se pone con textContent",
        "opcion.textContent = nombreDeEquipo(equipo)" in trozo
    )

    comprobar(
        "ya no se pega HTML con el nombre dentro",
        "`<option value=" not in trozo
    )


def prueba_el_fallo_no_vuelve_a_ser_silencioso():
    """
    Lo que costo encontrarlo: si la consulta fallaba, el catch estaba
    vacio y el desplegable se quedaba con 'Todos' sin decir nada.
    """

    trozo = cuerpo("async function populateRecordingsDeviceFilter() {")

    comprobar(
        "un fallo al llenar el filtro se dice en la consola",
        "console.warn(" in trozo
    )


def prueba_nombre_de_equipo_sigue_siendo_unica():
    """
    nombreDeEquipo() existe en un solo sitio a proposito, para que no
    haya veinte vistas con veinte reglas. Esta prueba existe para que
    la correccion no deje una regla nueva por ahi.
    """

    comprobar(
        "nombreDeEquipo se define una sola vez",
        PANEL.count("function nombreDeEquipo(") == 1,
        PANEL.count("function nombreDeEquipo(")
    )

    comprobar(
        "y sigue siendo alias, luego hostname, luego device_id",
        "return alias || equipo.hostname || equipo.device_id" in PANEL
    )


def prueba_el_filtro_de_verdad_recorta_en_el_servidor():
    """
    Lo unico que no es texto: que pedir un equipo concreto devuelva
    menos que pedirlos todos.

    Se monta una base de prueba con dos equipos y una grabacion de
    cada uno, y se llama a la funcion que consulta. Si el filtro no
    se aplicara, las dos llamadas darian lo mismo.
    """

    from backend import database

    carpeta = tempfile.mkdtemp(prefix="filtro-")
    database.DATABASE_PATH = os.path.join(carpeta, "prueba.db")
    database.init_db()

    from backend import recordings as grabaciones

    conexion = database.get_connection()

    try:
        for equipo, nombre in (("aaaa1111aaaa1111", "KIOSKO 1"),
                               ("bbbb2222bbbb2222", "CALIDAD")):

            conexion.execute(
                "INSERT OR IGNORE INTO devices "
                "(device_id, hostname, status) VALUES (?, ?, 'online')",
                (equipo, nombre)
            )

            conexion.execute(
                "INSERT INTO recordings "
                "(device_id, path, started_at, status) "
                "VALUES (?, ?, ?, 'local')",
                (equipo, "%s/2026/11/10/rec_1.mp4" % equipo,
                 "2026-11-10T15:00:00+00:00")
            )

        conexion.commit()

    finally:
        conexion.close()

    try:
        todos = grabaciones.jornadas(None, None, None)
        uno = grabaciones.jornadas("bbbb2222bbbb2222", None, None)

    except AttributeError:
        # La funcion que agrupa por dia puede llamarse de otro modo;
        # si no esta, no se finge que la prueba paso.
        comprobar("existe la funcion que agrupa por jornadas", False,
                  "no se encontro recordings.jornadas")
        return

    comprobar("sin filtro salen los dos equipos",
              len({j["device_id"] for j in todos}) == 2,
              sorted({j["device_id"] for j in todos}))

    comprobar("con filtro sale UNO solo",
              len({j["device_id"] for j in uno}) == 1,
              sorted({j["device_id"] for j in uno}))

    comprobar("y es el que se pidio",
              all(j["device_id"] == "bbbb2222bbbb2222" for j in uno))


def principal():

    prueba_el_html_solo_trae_todos()
    prueba_la_vista_por_dia_llena_el_desplegable()
    prueba_la_vista_por_dia_manda_los_filtros()
    prueba_el_servidor_ya_aceptaba_los_filtros()
    prueba_buscar_no_cambia_de_vista()
    prueba_las_opciones_van_por_el_nombre_puesto()
    prueba_el_alias_no_puede_romper_el_desplegable()
    prueba_el_fallo_no_vuelve_a_ser_silencioso()
    prueba_nombre_de_equipo_sigue_siendo_unica()
    prueba_el_filtro_de_verdad_recorta_en_el_servidor()

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
