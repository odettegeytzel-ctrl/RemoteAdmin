"""
El teclado fisico del escritorio remoto.

    .venv\\Scripts\\python tests/test_teclado_remoto.py

El fallo
--------

Los oyentes de teclado estaban en la IMAGEN, con tabindex en ella:

    <img id="remote-screen-image" tabindex="0">
    image.addEventListener("keydown", onRemoteKeyDown);

Escribir funcionaba solo mientras esa imagen tenia el foco. En cuanto
se pulsaba un boton de la barra de teclas, "Avisar mantenimiento" o un
tirador de redimensionado, el foco se iba y el teclado dejaba de
llegar al equipo: en silencio, sin ninguna pista de por que.

Ahora escucha el MARCO, que contiene a los dos, cualquier pulsacion
dentro de la ventana le devuelve el foco, y un aviso dice a donde va
lo que se teclea.

Lo que esta prueba hace de verdad
---------------------------------

Abre Chrome, teclea con eventos de teclado reales y mira que se
habria enviado al Agent. No hay Agent: se intercepta el envio.

Lo que NO se puede hacer desde una pagina web
---------------------------------------------

Alt+Tab, Ctrl+W, Ctrl+Esc y la tecla Windows las atiende el shell del
sistema por debajo del navegador. preventDefault() no llega ahi.
Para esas estan los botones de la barra, y eso no es una carencia del
codigo: es un limite del navegador.
"""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()

SONDA = RAIZ / "tests" / "navegador" / "teclado-remoto.mjs"

PUERTO_WEB = 8792
PUERTO_CHROME = 9348

CHROMES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==================================================================
# EL CODIGO
# ==================================================================

def test_el_foco_ya_no_depende_de_la_imagen():

    comprobar("El marco es lo enfocable",
              'id="remote-screen-container"' in HTML
              and 'tabindex="0"' in HTML.split(
                  'id="remote-screen-container"', 1)[1][:260])

    imagen = HTML.split('id="remote-screen-image"', 1)[1][:300]

    comprobar("La imagen ya no lo es",
              'tabindex="0"' not in imagen, imagen[:140])

    comprobar("Los oyentes van al marco",
              'marco.addEventListener("keydown", onRemoteKeyDown)' in JS)

    comprobar("Y se sueltan del marco",
              'marco.removeEventListener("keydown", onRemoteKeyDown)' in JS)


def test_se_recupera_el_foco_tras_pulsar_un_boton():

    comprobar("Hay una funcion que lo devuelve",
              "function recuperarFocoRemoto(" in JS)

    comprobar("La barra de teclas lo devuelve",
              JS.split("function enlazarTeclasRemotas(", 1)[1][:1400]
              .count("recuperarFocoRemoto()") >= 1)

    comprobar("Y el boton de mantenimiento tambien",
              "recuperarFocoRemoto();" in
              JS.split("async function alternarMantenimiento(", 1)[1][:2000])

    comprobar("Pulsar la pantalla tambien lo devuelve",
              'marco.addEventListener("pointerdown", recuperarFocoRemoto)'
              in JS)


def test_se_avisa_de_a_donde_va_el_teclado():

    comprobar("Hay un aviso en la barra",
              'id="remote-foco"' in HTML)

    comprobar("Se pinta al enfocar",
              'marco.addEventListener("focus", pintarFocoRemoto)' in JS)

    comprobar("Y al perder el foco",
              "pintarFocoRemoto();" in
              JS.split("function onRemoteKeyboardBlur(", 1)[1][:400])


def test_las_teclas_se_sueltan_al_perder_el_foco():
    """Un Ctrl atascado en el equipo remoto es peor que no enviarlo."""

    cuerpo = JS.split("function onRemoteKeyboardBlur(", 1)[1][:300]

    comprobar("Al perder el foco se sueltan las teclas",
              "releaseAllRemoteKeys()" in cuerpo, cuerpo[:150])

    comprobar("Y al cerrar la sesion tambien",
              "releaseAllRemoteKeys()" in
              JS.split("function disableRemoteKeyboard(", 1)[1][:700])


def test_el_permiso_se_exige_en_el_envio():
    """remote.control, en el endpoint que recibe las teclas."""

    principal = io.open(RAIZ / "backend" / "main.py",
                        encoding="utf-8").read()

    teclado = principal.split(
        '@app.post("/api/devices/{device_id}/keyboard")', 1)[1][:400]

    comprobar("El endpoint de teclado exige remote.control",
              'require_permission(request, "remote.control")' in teclado,
              teclado[:160])


# ==================================================================
# EN UN NAVEGADOR DE VERDAD
# ==================================================================

def buscar_chrome():

    for ruta in CHROMES:
        if os.path.isfile(ruta):
            return ruta

    return shutil.which("chrome") or shutil.which("msedge")


def puerto_libre(puerto):

    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", puerto)) != 0


def test_tecleando_de_verdad():

    navegador = buscar_chrome()
    node = shutil.which("node")

    if not navegador or not node or not SONDA.is_file():
        comprobar("(sin Chrome o sin Node: no se teclea nada)", True)
        return

    if not (puerto_libre(PUERTO_WEB) and puerto_libre(PUERTO_CHROME)):
        comprobar("(los puertos de prueba estan ocupados)", True)
        return

    perfil = RAIZ / "tests" / "navegador" / ".perfil-teclado"

    web = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(PUERTO_WEB),
         "--bind", "127.0.0.1"],
        cwd=str(RAIZ),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    chrome = subprocess.Popen(
        [navegador, "--headless", "--disable-gpu", "--no-sandbox",
         "--remote-debugging-port=%d" % PUERTO_CHROME,
         "--user-data-dir=%s" % perfil, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    try:
        time.sleep(4)

        salida = subprocess.run(
            [node, str(SONDA), str(PUERTO_WEB), str(PUERTO_CHROME)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=240
        )

        if salida.returncode != 0 or not salida.stdout.strip():
            comprobar("La sonda del teclado corre", False,
                      (salida.stderr or "(sin salida)")[-220:])
            return

        datos = json.loads(salida.stdout)

    except Exception as error:
        comprobar("La sonda del teclado corre", False,
                  type(error).__name__ + ": " + str(error)[:160])
        return

    finally:
        for proceso in (chrome, web):
            try:
                proceso.terminate()
                proceso.wait(timeout=8)
            except Exception:
                try:
                    proceso.kill()
                except Exception:
                    pass

        shutil.rmtree(perfil, ignore_errors=True)

    # --- al abrir ---
    comprobar("Al abrir, el teclado va al equipo",
              datos["alAbrir"]["enfocado"]
              and "equipo remoto" in (datos["alAbrir"]["aviso"] or ""),
              str(datos["alAbrir"]))

    # --- escribir ---
    texto = datos["texto"]

    comprobar("Escribir 'hola' envia 8 eventos",
              texto["enviadas"] == 8, "envio %s" % texto["enviadas"])

    teclas = [e.get("key") for e in texto["ultimas"]]

    comprobar("Con las teclas correctas",
              "a" in teclas and "l" in teclas, str(teclas))

    # --- combinacion ---
    combo = datos["combinacion"]

    enviadas = [(e.get("action"), e.get("key"))
                for e in combo["ultimas"]]

    comprobar("Ctrl+C manda el modificador y la letra",
              ("down", "ctrlleft") in enviadas
              and ("down", "c") in enviadas,
              str(enviadas))

    comprobar("Y los sueltas en orden",
              ("up", "c") in enviadas and ("up", "ctrlleft") in enviadas,
              str(enviadas))

    # --- EL FALLO QUE SE CORRIGE ---
    comprobar("Tras pulsar un boton, el foco VUELVE al marco",
              datos["trasBoton"]["enfocado"],
              "activo: %s" % datos["trasBoton"]["activo"])

    despues = datos["escribirTrasBoton"]

    comprobar("Y se puede seguir escribiendo",
              despues["enviadas"] == 2,
              "envio %s eventos" % despues["enviadas"])

    # --- teclas atascadas ---
    comprobar("Con dos teclas pulsadas, constan como pulsadas",
              datos["conTeclasPulsadas"]["pulsadas"] == 2,
              str(datos["conTeclasPulsadas"]["pulsadas"]))

    perdido = datos["trasPerderFoco"]

    comprobar("Al perder el foco NO queda ninguna pulsada",
              perdido["pulsadas"] == 0,
              "quedaban %s" % perdido["pulsadas"])

    acciones = [e.get("action") for e in perdido["ultimas"]]

    comprobar("Se manda soltar las teclas",
              "up" in acciones or "release_all" in acciones,
              str(perdido["ultimas"]))

    comprobar("Y el aviso deja de decir que va al equipo",
              "Pulsa la pantalla" in (perdido["aviso"] or ""),
              repr(perdido["aviso"]))


# ==================================================================

def principal():

    pruebas = [
        test_el_foco_ya_no_depende_de_la_imagen,
        test_se_recupera_el_foco_tras_pulsar_un_boton,
        test_se_avisa_de_a_donde_va_el_teclado,
        test_las_teclas_se_sueltan_al_perder_el_foco,
        test_el_permiso_se_exige_en_el_envio,
        test_tecleando_de_verdad
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("TECLADO DEL ESCRITORIO REMOTO")
    print("=" * 66)
    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if not bien:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    if not fallos:
        print("  todo correcto")

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    print()
    print("-" * 66)
    print("LIMITE DEL NAVEGADOR, NO DEL CODIGO")
    print("-" * 66)
    print("""
Alt+Tab, Ctrl+W, Ctrl+Esc y la tecla Windows las atiende el shell del
sistema antes que el navegador: ninguna pagina web puede evitarlo.
Para esas estan los botones de la barra, que si llegan al equipo.

Pendiente con dos computadoras: que lo tecleado aparezca de verdad en
el equipo remoto, y que no quede ninguna tecla atascada alli despues
de cerrar la sesion.
""")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
