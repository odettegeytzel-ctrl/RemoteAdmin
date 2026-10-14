"""
El desglose de alertas del panel imprimia "undefined".

    .venv\\Scripts\\python tests/test_alertas_panel.py

Lo que se veia:

    undefined Conexion 3 undefined Desconexion 3

La causa es de una letra. Las fichas de apariencia definen

    online: { dot: ..., icono: "actividad", label: "Conexion" }

con "icono" —un NOMBRE que resuelve el ayudante icono()— y cinco
sitios del panel leian "apariencia.icon", en ingles. Esa propiedad no
existe en ninguna ficha: vale undefined siempre.

Donde se interpolaba directa, salia la palabra. Donde estaba guardada
tras un ternario, el icono no se dibujaba NUNCA, en silencio. Mismo
origen, y el segundo caso no lo habria notado nadie.

Como se prueba
--------------

No se mira el codigo: se EJECUTA. Node corre las funciones reales de
app.js contra datos con la forma que devuelve el backend, y se
inspecciona el HTML que producen. Asi la prueba falla si vuelve a
colarse un undefined, venga de donde venga.

Se cubren tambien los casos que rompen una plantilla: un tipo que el
panel no conoce, una alerta sin mensaje, sin fecha, y con campos
ausentes del todo.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

APP = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


NODE = shutil.which("node")


# ==================================================================
# EJECUTAR LAS FUNCIONES DE VERDAD
# ==================================================================

def extraer(*nombres):
    """
    Saca funciones y constantes de app.js por su nombre.

    app.js entero no se puede cargar en Node: toca document, fetch y
    WebSocket desde el primer momento. Se extraen las piezas que se
    quieren probar, con un contador de llaves.
    """

    trozos = []

    for nombre in nombres:

        for patron in (f"\nfunction {nombre}(",
                       f"\nconst {nombre} = {{",
                       f"\nconst {nombre} = "):

            inicio = APP.find(patron)

            if inicio == -1:
                continue

            inicio += 1

            nivel = 0
            visto = False

            for indice in range(inicio, len(APP)):

                if APP[indice] in "{":
                    nivel += 1
                    visto = True

                elif APP[indice] == "}":
                    nivel -= 1

                    if visto and nivel == 0:
                        trozos.append(APP[inicio:indice + 1])
                        break

            break

        else:
            raise AssertionError(f"no se encontro {nombre} en app.js")

    return "\n\n".join(trozos)


GUION = """
%(piezas)s

// Lo minimo para que renderAlertsPanel pueda escribir en algun sitio
const _nodos = {};

function _nodo(id) {
    if (!_nodos[id]) {
        _nodos[id] = { id, textContent: "", innerHTML: "" };
    }
    return _nodos[id];
}

global.document = { getElementById: (id) => _nodo(id) };

function formatDate(v) {
    return v ? String(v) : "-";
}

const datos = JSON.parse(process.argv[2]);

renderAlertsPanel(datos);

console.log(JSON.stringify({
    resumen: _nodos["dash-alerts-summary"].textContent,
    tipos: _nodos["dash-alerts-types"].innerHTML,
    recientes: _nodos["dash-alerts-recent"].innerHTML
}));
"""


def pintar(alertas):
    """Devuelve el HTML que produce renderAlertsPanel con esas alertas."""

    piezas = extraer("icono", "ALERT_APPEARANCE",
                     "UNKNOWN_ALERT_APPEARANCE", "alertAppearance",
                     "renderAlertsPanel")

    carpeta = tempfile.mkdtemp(prefix="alertas-")

    try:
        guion = os.path.join(carpeta, "prueba.js")

        with io.open(guion, "w", encoding="utf-8") as archivo:
            archivo.write(GUION % {"piezas": piezas})

        salida = subprocess.run(
            [NODE, guion, json.dumps(alertas)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=40
        )

        if salida.returncode != 0:
            raise AssertionError(salida.stderr[-600:])

        return json.loads(salida.stdout)

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


# ==================================================================
# DATOS CON LA FORMA QUE DEVUELVE EL BACKEND
# ==================================================================
#
# backend/alerts.py inserta device_id, hostname, type, message,
# created_at y organization_id. is_read lo añade la consulta.

def alerta(tipo, leida=False, **extra):

    base = {
        "id": 1,
        "device_id": "eq-1",
        "hostname": "PC-PRUEBA",
        "type": tipo,
        "message": "Mensaje de prueba",
        "created_at": "2026-10-13T10:00:00+00:00",
        "is_read": 1 if leida else 0
    }

    base.update(extra)

    return base


LAS_SEIS = (
    [alerta("online")] * 3 + [alerta("offline")] * 3
)


# ==================================================================
# LAS COMPROBACIONES
# ==================================================================

def test_el_desglose_no_dice_undefined():
    """Exactamente lo que se veia en el panel."""

    salida = pintar(LAS_SEIS)

    comprobar("El desglose por tipo no contiene 'undefined'",
              "undefined" not in salida["tipos"],
              salida["tipos"][:220])

    comprobar("Ni las alertas recientes",
              "undefined" not in salida["recientes"],
              salida["recientes"][:220])

    comprobar("Ni el resumen",
              "undefined" not in salida["resumen"],
              salida["resumen"])


def test_siguen_saliendo_los_tipos_y_sus_cantidades():
    """La correccion no puede llevarse por delante la informacion."""

    salida = pintar(LAS_SEIS)

    comprobar("Sale 'Conexion'", "Conexión" in salida["tipos"],
              salida["tipos"][:200])

    comprobar("Sale 'Desconexion'", "Desconexión" in salida["tipos"],
              salida["tipos"][:200])

    # Tres de cada
    cuentas = re.findall(r"<strong[^>]*>(\d+)</strong>", salida["tipos"])

    comprobar("Con sus cantidades: 3 y 3", cuentas == ["3", "3"],
              str(cuentas))

    comprobar("El resumen cuenta las seis como pendientes",
              "6 pendientes" in salida["resumen"], salida["resumen"])


def test_el_icono_se_dibuja_de_verdad():
    """
    El fallo silencioso: donde habia un ternario, el icono no salia
    nunca y nadie lo noto. Ahora tiene que estar el SVG.
    """

    salida = pintar(LAS_SEIS)

    comprobar("El desglose dibuja simbolos SVG",
              salida["tipos"].count("<svg") >= 2,
              salida["tipos"][:200])

    comprobar("Con el simbolo de 'actividad' para Conexion",
              "#i-actividad" in salida["tipos"],
              salida["tipos"][:300])

    comprobar("Y el de 'servidor' para Desconexion",
              "#i-servidor" in salida["tipos"],
              salida["tipos"][:300])

    comprobar("Las alertas recientes tambien dibujan su simbolo",
              "<svg" in salida["recientes"],
              salida["recientes"][:220])


def test_un_tipo_desconocido_no_rompe_nada():
    """Un tipo futuro no puede dejar un hueco ni imprimir undefined."""

    salida = pintar([alerta("temperatura_critica")] * 2)

    comprobar("Sin 'undefined' con un tipo no previsto",
              "undefined" not in salida["tipos"],
              salida["tipos"][:220])

    comprobar("Se enseña el tipo crudo en vez de un hueco",
              "temperatura_critica" in salida["tipos"],
              salida["tipos"][:220])

    comprobar("Y lleva el simbolo generico",
              "#i-info" in salida["tipos"],
              salida["tipos"][:220])

    comprobar("Con su cantidad",
              ">2</strong>" in salida["tipos"].replace(" ", ""),
              salida["tipos"][:220])


def test_campos_nulos_o_ausentes():
    """
    Lo que de verdad rompe una plantilla: que el dato no este.

    Son casos posibles: 'message' es NOT NULL en la tabla pero
    'created_at' puede quedarse en NULL, y una fila antigua puede no
    traer todas las claves.
    """

    casos = [
        ("mensaje nulo", [alerta("online", message=None)]),
        ("fecha nula", [alerta("online", created_at=None)]),
        ("tipo nulo", [alerta(None)]),
        ("tipo vacio", [alerta("")]),
        ("sin la clave 'type'", [{"id": 1, "message": "x", "is_read": 0}]),
        ("sin la clave 'is_read'", [{"id": 1, "type": "online",
                                     "message": "x"}]),
        ("alerta vacia", [{}])
    ]

    for nombre, datos in casos:

        try:
            salida = pintar(datos)

        except AssertionError as error:
            comprobar(f"No revienta con {nombre}", False, str(error)[:150])
            continue

        comprobar(f"No revienta con {nombre}", True)

        comprobar(f"Y no dice 'undefined' con {nombre}",
                  "undefined" not in salida["tipos"]
                  and "undefined" not in salida["recientes"],
                  (salida["tipos"] + " || " + salida["recientes"])[:200])


def test_sin_alertas():
    """El estado vacio dice que no hay nada, no un cero inventado."""

    salida = pintar([])

    comprobar("Sin alertas, lo dice", "Sin alertas" in salida["resumen"],
              salida["resumen"])

    comprobar("Y el desglose tambien",
              "Sin alertas registradas" in salida["tipos"],
              salida["tipos"][:160])

    comprobar("Y la lista",
              "No hay alertas" in salida["recientes"],
              salida["recientes"][:160])


def test_leidas_y_pendientes_se_distinguen():
    """Que la correccion no haya tocado el estado de cada alerta."""

    salida = pintar([alerta("online"), alerta("offline", leida=True)])

    comprobar("Cuenta una pendiente y una leida",
              "1 pendientes" in salida["resumen"]
              and "1 leídas" in salida["resumen"],
              salida["resumen"])

    comprobar("La pendiente se marca",
              "Pendiente" in salida["recientes"],
              salida["recientes"][:300])


def test_ninguna_ficha_usa_ya_la_propiedad_en_ingles():
    """
    La causa raiz, buscada en el codigo.

    Las dos comprobaciones anteriores pasarian aunque quedara un
    'apariencia.icon' en una rama que estos datos no recorren.
    """

    comprobar("No queda ningun 'apariencia.icon'",
              not re.search(r"apariencia\.icon\b", APP),
              "quedan referencias a la propiedad inexistente")

    # Y las fichas siguen declarando 'icono'
    fichas = APP.split("const ALERT_APPEARANCE = {", 1)[1].split("};", 1)[0]

    comprobar("Las fichas declaran 'icono'",
              fichas.count("icono:") == 8, str(fichas.count("icono:")))

    comprobar("Y ninguna declara 'icon:'",
              "icon:" not in fichas.replace("icono:", ""))


# ==================================================================

def principal():

    if not NODE:
        print("\nNo hay 'node' en el PATH: estas pruebas ejecutan el")
        print("codigo real de app.js y lo necesitan.\n")
        return 1

    pruebas = [
        test_el_desglose_no_dice_undefined,
        test_siguen_saliendo_los_tipos_y_sus_cantidades,
        test_el_icono_se_dibuja_de_verdad,
        test_un_tipo_desconocido_no_rompe_nada,
        test_campos_nulos_o_ausentes,
        test_sin_alertas,
        test_leidas_y_pendientes_se_distinguen,
        test_ninguna_ficha_usa_ya_la_propiedad_en_ingles
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 64)
    print("ALERTAS DEL PANEL")
    print("=" * 64)
    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        print("  %s  %s" % ("OK  " if bien else "FALLA", nombre))

        if not bien and detalle:
            print("         %s" % detalle)

        if not bien:
            fallos += 1

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos, len(resultados)))

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
