r"""
Cuanto cuesta y cuanto tarda la pantalla en vivo.

    .venv\Scripts\python tests/test_medida_del_remoto.py

Por que existe
--------------

Hay una decision que tomar: si merece la pena la conexion directa.
Son unos 90 MB en cada equipo -WebRTC en Python obliga a instalar av,
que trae su propio ffmpeg- y eso no se decide con "va lento".

Las cuatro cifras señalan a culpables distintos:

    panel -> servidor    el viaje a la nube desde el navegador
    servidor -> equipo   el otro tramo del mismo viaje
    captura              lo que tarda el EQUIPO en hacer la foto
    fps y KB/s           lo que cuesta mantener la sesion abierta

Los dos primeros son lo unico que una conexion directa se ahorraria.
Si suman poco, los 90 MB no valen la pena todavia y lo que hay que
cambiar es la captura o el formato.

Lo que se vigila aqui
---------------------

Sobre todo, que la medida NO MIENTA y que no cueste mas que lo que
mide. Un instrumento que inventa un cero cuando no sabe es peor que
no tener instrumento: con el cero se toman decisiones.
"""

import io
import os
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-medida")

import backend.main as servidor  # noqa: E402

JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
AYUDANTE = io.open(RAIZ / "agent" / "helper.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def limpiar(equipo="eq-medida"):
    servidor.mediciones_de_pantalla.pop(equipo, None)


# ==================================================================
# 1. LA MEDIDA NO MIENTE
# ==================================================================

def test_sin_datos_no_se_inventa_nada():
    """
    Lo mas importante del archivo.

    Un cero inventado parece un dato y se usa para decidir. Es peor
    que no tener instrumento, porque nadie desconfia de un numero.
    """

    limpiar()

    comprobar("Un equipo del que no se sabe nada devuelve None",
              servidor.medida_de_pantalla("eq-medida") is None)

    # Con UN solo fotograma tampoco: no hay intervalo que medir.
    servidor.anotar_fotograma("eq-medida", 1000)

    comprobar("Con un solo fotograma tampoco se calcula nada",
              servidor.medida_de_pantalla("eq-medida") is None,
              str(servidor.medida_de_pantalla("eq-medida")))

    limpiar()


def test_se_cuentan_los_fotogramas_y_los_bytes():

    limpiar()

    import time

    for _ in range(10):
        servidor.anotar_fotograma("eq-medida", 50_000, captura_ms=42.0)
        time.sleep(0.01)

    medida = servidor.medida_de_pantalla("eq-medida")

    comprobar("Hay medida", medida is not None)

    if medida is None:
        return

    comprobar("Se cuentan los fotogramas",
              medida["muestras"] == 9, str(medida["muestras"]))

    comprobar("Y sale un numero de fps creible",
              0 < medida["fps"] < 1000, str(medida["fps"]))

    comprobar("Se mide lo que ocupa",
              medida["kb_por_segundo"] > 0,
              str(medida["kb_por_segundo"]))

    comprobar("Y lo que tarda la captura, que lo dice el equipo",
              medida["captura_ms"] == 42.0, str(medida["captura_ms"]))

    limpiar()


def test_sin_dato_de_captura_no_se_finge():
    """
    Un Agent anterior no manda 'captura_ms'. Ahi la casilla queda
    vacia, no en cero: cero significaria "captura instantanea", que
    es una afirmacion distinta y falsa.
    """

    limpiar()

    import time

    for _ in range(5):
        servidor.anotar_fotograma("eq-medida", 10_000)
        time.sleep(0.01)

    medida = servidor.medida_de_pantalla("eq-medida")

    comprobar("Sin dato de captura, no se dice cero",
              medida is not None and medida["captura_ms"] is None,
              str(medida))

    limpiar()


def test_solo_se_recuerdan_los_ultimos():
    """
    Es una MEDIDA, no un historial. Guardar todos los fotogramas de
    una sesion de dos horas seria llenar la memoria del servidor
    para contestar una pregunta sobre el ultimo minuto.
    """

    limpiar()

    for _ in range(servidor.VENTANA_DE_MEDIDA * 3):
        servidor.anotar_fotograma("eq-medida", 1000)

    guardados = len(
        servidor.mediciones_de_pantalla["eq-medida"]["momentos"]
    )

    comprobar("No crece sin limite",
              guardados == servidor.VENTANA_DE_MEDIDA,
              str(guardados))

    limpiar()


# ==================================================================
# 2. EL TRAMO QUE DECIDE
# ==================================================================

def test_la_ida_y_vuelta_al_equipo_se_mide_de_verdad():
    """
    No se estima: se manda un ping por el WebSocket y se cronometra
    el pong. Es el numero que decide si los 90 MB valen la pena.
    """

    SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()

    cuerpo = SERVIDOR.split("async def diagnostico_remoto(", 1)[1][:2600]

    comprobar("Se manda un ping de verdad",
              'await websocket.send_text("ping")' in cuerpo)

    comprobar("Y se cronometra la respuesta",
              "pings_pendientes[device_id]" in cuerpo
              and "time.monotonic()" in cuerpo)

    comprobar("El pong cierra el cronometro",
              'pings_pendientes.pop(device_id, None)' in SERVIDOR)

    comprobar("Si no contesta a tiempo, no se inventa un numero",
              "except asyncio.TimeoutError:" in cuerpo
              and "ida_y_vuelta = None" in cuerpo)

    comprobar("Hay plazo, para no esperar indefinidamente",
              "PLAZO_DEL_PING" in SERVIDOR)

    comprobar("Hace falta poder controlar el equipo para medirlo",
              'require_permission(request, "remote.control")' in cuerpo)


def test_el_equipo_dice_lo_que_tarda_en_capturar():
    """
    Es la parte del retardo que NO depende de la red. Sin separarla,
    un remoto lento se achaca siempre a la conexion.
    """

    comprobar("El ayudante cronometra la captura",
              "comenzado = time.monotonic()" in AYUDANTE
              and "captura_ms" in AYUDANTE)

    bucle = AYUDANTE.split("def _bucle_de_pantalla(", 1)[1][:1600]

    comprobar("Y mide SOLO la captura, no el envio",
              bucle.index("captura_ms = round(")
              < bucle.index("mandar_al_servidor"),
              "se mide tambien lo que tarda la red")

    comprobar("Viaja con el fotograma",
              '"captura_ms": captura_ms' in bucle)


# ==================================================================
# 3. MEDIR NO PUEDE ESTORBAR
# ==================================================================

def test_la_medida_no_rompe_la_sesion_que_mide():

    comprobar("El panel se calla si falla la medida",
              "// Medir no puede romper la sesion que se esta midiendo."
              in JS)

    cuerpo = JS.split("async function refrescarMedida(", 1)[1][:2600]

    comprobar("Un fallo esconde el indicador, no revienta",
              "catch (error)" in cuerpo
              and 'caja.classList.add("hidden")' in cuerpo)

    comprobar("Se deja de medir al cerrar el remoto",
              "detenerLaMedida();" in JS)

    cierre = JS.split("async function stopRemoteDesktop() {", 1)[1][:600]

    comprobar("Y se para en el cierre, no solo se define",
              "detenerLaMedida()" in cierre)


def test_se_mide_cada_poco_pero_no_todo_el_rato():
    """
    Medir cada 100 ms costaria mas que lo que mide. Tres segundos
    bastan para ver si algo va mal mientras va mal.
    """

    comprobar("Hay un intervalo acotado",
              "const MEDIDA_CADA_MS = 3000;" in JS)

    cuerpo = JS.split("async function medirIdaYVuelta(", 1)[1][:1400]

    comprobar("Se toma la MEJOR de varias, no una suelta",
              "mejor" in cuerpo and "i < 3" in cuerpo)

    # Importa: la peor medida incluye cualquier hipo del navegador, y
    # lo que se busca es el coste del CAMINO, no el del momento.
    comprobar("Y si falla la red, se devuelve nada",
              "return null;" in cuerpo)


def test_se_ve_donde_se_necesita():

    comprobar("El indicador esta en la ventana remota",
              'id="remote-medida"' in HTML)

    comprobar("Y empieza escondido",
              '"hidden text-xs' in HTML
              or 'class="hidden' in HTML.split('id="remote-medida"', 1)[1][:200])


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("CUANTO CUESTA Y CUANTO TARDA LA PANTALLA EN VIVO")
    print("=" * 66)
    print()

    pruebas = [
        test_sin_datos_no_se_inventa_nada,
        test_se_cuentan_los_fotogramas_y_los_bytes,
        test_sin_dato_de_captura_no_se_finge,
        test_solo_se_recuerdan_los_ultimos,
        test_la_ida_y_vuelta_al_equipo_se_mide_de_verdad,
        test_el_equipo_dice_lo_que_tarda_en_capturar,
        test_la_medida_no_rompe_la_sesion_que_mide,
        test_se_mide_cada_poco_pero_no_todo_el_rato,
        test_se_ve_donde_se_necesita,
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

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
