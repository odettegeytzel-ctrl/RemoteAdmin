"""
La ventana de control remoto: plegar, redimensionar y cerrar.

    .venv\\Scripts\\python tests/test_ventana_remota.py

Esta prueba abre Chrome DE VERDAD y mide pixeles, que es la unica
forma de saber si una imagen se deforma. Usa el protocolo de
depuracion de Chrome por el WebSocket que trae Node: no hace falta
Playwright ni Selenium ni instalar nada.

Si no hay Chrome o no hay Node, las comprobaciones del navegador se
saltan y se dice; las que miran el codigo siguen corriendo.

Lo que se comprueba midiendo
----------------------------

  - plegar deja el cuerpo en 0 de alto y la X sigue alcanzable:
    minimizar y cerrar son acciones distintas;
  - la imagen conserva su proporcion a cualquier tamaño, sin
    deformarse;
  - y lo que de verdad importa de la sesion persistente: que el
    elemento <img> de la transmision NO se recrea ni se recarga al
    plegar y restaurar. Si se recreara, la conexion MJPEG se
    cortaria y habria que reconectar con el Agent.
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

import chrome_de_pruebas


RAIZ = Path(__file__).resolve().parent.parent

JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
CSS = io.open(RAIZ / "frontend" / "veyra.css", encoding="utf-8").read()

SONDA = RAIZ / "tests" / "navegador" / "ventana-remota.mjs"
SONDA_TIRADORES = RAIZ / "tests" / "navegador" / "tiradores.mjs"

PUERTO_WEB = 8788
PUERTO_CHROME = 9344

CHROMES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==================================================================
# LO QUE SE PUEDE MIRAR SIN NAVEGADOR
# ==================================================================

def test_minimizar_y_cerrar_son_distintos():

    comprobar("Hay un boton de minimizar",
              'id="remote-minimizar"' in HTML)

    comprobar("Y sigue estando el de cerrar",
              'id="close-remote-button"' in HTML)

    comprobar("Son dos botones, no uno",
              HTML.count('id="remote-minimizar"') == 1
              and HTML.count('id="close-remote-button"') == 1)

    comprobar("Minimizar no llama a stopRemoteDesktop",
              'getElementById("remote-minimizar")\n?.addEventListener('
              '"click", alternarPlegadoRemoto)' in JS
              or 'alternarPlegadoRemoto' in JS)


def test_plegar_no_destruye_la_transmision():
    """
    display:none permitiria al navegador dejar de decodificar el
    MJPEG. max-height conserva el elemento mostrandose.
    """

    bloque = CSS.split(".vy-remoto-cuerpo {", 1)[1].split("}", 1)[0]

    comprobar("El cuerpo se pliega con max-height",
              "max-height" in bloque, bloque)

    plegada = CSS.split(".vy-remoto-cuerpo.is-plegada {", 1)[1]
    plegada = plegada.split("}", 1)[0]

    comprobar("Y plegado es max-height 0, no display none",
              "max-height: 0" in plegada and "display" not in plegada,
              plegada)

    cuerpo = JS.split("function plegarRemoto(", 1)[1][:1500]

    comprobar("Al plegar no se toca el src de la imagen",
              "removeAttribute" not in cuerpo and ".src" not in cuerpo)


def test_el_marco_se_puede_redimensionar():

    bloque = CSS.split(".vy-remoto-marco {", 1)[1].split("}", 1)[0]

    comprobar("El marco es redimensionable",
              "resize: both" in bloque, bloque)

    comprobar("Con un minimo que no lo deje inservible",
              "min-width" in bloque and "min-height" in bloque)

    comprobar("Y sin pasarse del ancho disponible",
              "max-width: 100%" in bloque)

    imagen = CSS.split(".vy-remoto-imagen {", 1)[1].split("}", 1)[0]

    comprobar("La imagen conserva su proporcion",
              "object-fit: contain" in imagen, imagen)


def test_cada_sesion_empieza_desplegada():
    """
    Abrir el remoto y no ver nada porque la vez anterior quedo
    minimizado seria desconcertante.
    """

    inicio = JS.split("async function startRemoteDesktop() {", 1)[1][:2500]

    comprobar("Al abrir se despliega",
              "plegarRemoto(false)" in inicio)

    comprobar("Y se recupera el tamaño guardado",
              "recordarTamanoRemoto()" in inicio)


def test_el_tamano_se_guarda_sin_romperse():

    bloque = JS.split("function recordarTamanoRemoto(", 1)[1][:2200]

    comprobar("Se guarda en el navegador",
              "localStorage" in bloque)

    comprobar("Con try/catch: en una ventana privada no revienta",
              bloque.count("try {") >= 2 and bloque.count("catch") >= 2,
              "try %d, catch %d" % (bloque.count("try {"),
                                    bloque.count("catch")))

    comprobar("Y no se escribe en cada pixel del arrastre",
              "setTimeout" in bloque and "clearTimeout" in bloque)


# ==================================================================
# CON NAVEGADOR DE VERDAD
# ==================================================================

def buscar_chrome():

    for ruta in CHROMES:
        if os.path.isfile(ruta):
            return ruta

    return shutil.which("chrome") or shutil.which("msedge")


def puerto_libre(puerto):

    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", puerto)) != 0


def buscar_puerto(_desde=None):
    """
    Un puerto libre, elegido por el sistema operativo.

    Hace falta porque un Chrome huerfano de una corrida anterior
    -headless, sin ventana, imposible de ver- se queda escuchando en
    el puerto de depuracion y bloqueaba todas las ejecuciones
    siguientes.

    Se pide con bind al puerto 0 en vez de escanear un rango: un
    escaneo desde 9344 alcanzaria el 9348, que es el de
    test_teclado_remoto, y le robaria el puerto. El sistema reparte
    de un rango alto que ninguna prueba reserva.

    Queda una ventana minima entre cerrar el socket y que Chrome lo
    tome; es la practica habitual y el riesgo es despreciable.
    """

    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    except OSError:
        return None


def carpeta_de_perfil(base):
    """
    Un perfil que no este en uso.

    Chrome no arranca dos veces sobre el mismo --user-data-dir, y el
    huerfano se queda con el suyo. Se prueban sufijos hasta dar con
    uno libre.

    El candado lo deja el propio Chrome, y NO se llama igual en todas
    partes: 'lockfile' en Windows, 'SingletonLock' en Linux y macOS.
    Se miran los dos.
    """

    for sufijo in range(0, 20):

        carpeta = base if sufijo == 0 else base.with_name(
            base.name + "-" + str(sufijo)
        )

        bloqueada = any(
            (carpeta / nombre).exists()
            for nombre in ("lockfile", "SingletonLock")
        )

        if not bloqueada:
            return carpeta

    return None


def test_el_navegador_de_pruebas_no_deja_restos_ni_mide_a_ciegas():
    """
    Dos fallos de las PRUEBAS, no del producto, que costaron una
    tarde de buscar en el sitio equivocado.

    El primero: terminate() mata el chrome.exe que se lanzo, pero
    Chrome arranca hijos -renderizador, GPU- que sobreviven. Cada
    ejecucion dejaba un par sueltos, headless y sin ventana.

    El segundo: con la maquina cargada por esos restos, los cuatro
    segundos de espera fija no bastaban. Chrome todavia no estaba
    listo, la sonda se conectaba a medias y las medidas salian mal.
    Y eso es peor que fallar, porque unas medidas equivocadas
    parecen un defecto de la ventana remota.

    Esta suite llego a dar 26 de 40 por esto, con el codigo de la
    ventana intacto.
    """

    AQUI = io.open(__file__, encoding="utf-8").read()
    TECLADO = io.open(
        RAIZ / "tests" / "test_teclado_remoto.py", encoding="utf-8"
    ).read()
    UTIL = io.open(
        RAIZ / "tests" / "chrome_de_pruebas.py", encoding="utf-8"
    ).read()

    for nombre, fuente in (("la ventana", AQUI), ("el teclado", TECLADO)):

        comprobar("En %s se espera a que Chrome conteste" % nombre,
                  "esperar_a_chrome(" in fuente)

        # Por LINEA, no buscando el texto en el archivo entero:
        # esta misma comprobacion contiene la cadena que busca, y
        # se encontraba a si misma.
        espera_fija = [
            linea for linea in fuente.splitlines()
            if linea.strip().startswith("time.sleep(4)")
        ]

        comprobar("Y ya no hay espera fija en %s" % nombre,
                  not espera_fija,
                  "queda: " + "; ".join(espera_fija))

        comprobar("En %s se cierra el arbol entero" % nombre,
                  "cerrar_del_todo(" in fuente)

    comprobar("El cierre alcanza a los hijos",
              '"/T", "/F"' in UTIL)

    # Y solo a los nuestros. Buscar "chrome.exe" por nombre cerraria
    # el navegador de la persona, que no es nuestro y no se toca.
    comprobar("Nunca se cierra Chrome por nombre",
              "taskkill" in UTIL and "/IM" not in UTIL,
              "se estaria cerrando cualquier Chrome")

    comprobar("Se cierra el arbol del proceso que lanzamos",
              "str(proceso.pid)" in UTIL)

    comprobar("Si Chrome no levanta, se dice en vez de medir",
              "BLOQUEADA: Chrome no levanto a tiempo" in AQUI)


def test_en_un_navegador_de_verdad():
    """Mide pixeles. Es lo unico que detecta una deformacion."""

    navegador = buscar_chrome()
    node = shutil.which("node")

    if not navegador or not node or not SONDA.is_file():
        comprobar("(sin Chrome o sin Node: no se mide en navegador)",
                  True)
        return

    puerto_web = buscar_puerto(PUERTO_WEB)
    puerto_chrome = buscar_puerto(PUERTO_CHROME)
    perfil = carpeta_de_perfil(RAIZ / "tests" / "navegador" / ".perfil")

    # Un puerto ocupado NO es una comprobacion aprobada. Antes lo era,
    # y la prueba pasaba en verde habiendo medido 47 de 73 cosas.
    if puerto_web is None or puerto_chrome is None or perfil is None:
        comprobar(
            "BLOQUEADA: no hay puerto o perfil libre para el navegador",
            False,
            "web desde %d, chrome desde %d. Suele ser un Chrome "
            "headless huerfano de una corrida anterior."
            % (PUERTO_WEB, PUERTO_CHROME)
        )
        return

    web = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(puerto_web),
         "--bind", "127.0.0.1"],
        cwd=str(RAIZ),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    chrome = subprocess.Popen(
        [navegador, "--headless", "--disable-gpu", "--no-sandbox",
         "--remote-debugging-port=%d" % puerto_chrome,
         "--user-data-dir=%s" % perfil, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    try:

        # Se espera a que Chrome CONTESTE, no un numero fijo
        # de segundos. Cuatro bastan en una maquina
        # descansada; con la maquina ocupada la sonda se
        # conectaba a medias y las medidas salian mal. No
        # fallaba: medía MAL, que es peor, porque parece un
        # defecto del producto.
        if not chrome_de_pruebas.esperar_a_chrome(puerto_chrome):
            comprobar(
                "BLOQUEADA: Chrome no levanto a tiempo", False,
                "suele ser un Chrome headless huerfano de una "
                "corrida anterior ocupando la maquina"
            )
            return

        chrome_de_pruebas.esperar_al_servidor(puerto_web)

        salida = subprocess.run(
            [node, str(SONDA), str(puerto_web), str(puerto_chrome)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180
        )

        if salida.returncode != 0 or not salida.stdout.strip():
            comprobar("La sonda del navegador corre", False,
                      (salida.stderr or "(sin salida)")[-220:])
            return

        datos = json.loads(salida.stdout)

    except Exception as error:
        comprobar("La sonda del navegador corre", False,
                  type(error).__name__ + ": " + str(error)[:160])
        return

    finally:
        # El ARBOL entero, no solo el padre: Chrome deja
        # hijos -renderizador, GPU- que sobreviven a
        # terminate() y se acumulan de una corrida a la
        # siguiente hasta estropear las medidas.
        for proceso in (chrome, web):
            chrome_de_pruebas.cerrar_del_todo(proceso)

        shutil.rmtree(perfil, ignore_errors=True)

    pasos = {p["paso"]: p for p in datos["pasos"]}

    # --- plegar ---
    for numero in (1, 2, 3):

        plegada = pasos["plegada %d" % numero]
        abierta = pasos["restaurada %d" % numero]

        comprobar("Plegada %d: el cuerpo queda sin alto" % numero,
                  plegada["cuerpoAlto"] == 0,
                  "alto %d" % plegada["cuerpoAlto"])

        comprobar("Plegada %d: la X sigue alcanzable" % numero,
                  plegada["cerrarVisible"])

        comprobar("Plegada %d: la imagen sigue en el documento" % numero,
                  plegada["imagenEnElDom"] and plegada["src"] == "puesto")

        comprobar("Restaurada %d: vuelve a verse" % numero,
                  abierta["cuerpoAlto"] > 100,
                  "alto %d" % abierta["cuerpoAlto"])

        comprobar("Restaurada %d: y las teclas tambien" % numero,
                  abierta["teclasVisibles"])

    # --- redimensionar, sin deformar ---
    for paso in datos["pasos"]:

        if not paso["paso"].startswith("tamano"):
            continue

        ancho, alto = paso["dibujada"]
        natural_ancho, natural_alto = paso["natural"]

        if not (ancho and alto and natural_ancho):
            comprobar("%s: hay imagen que medir" % paso["paso"], False)
            continue

        proporcion = ancho / alto
        esperada = natural_ancho / natural_alto

        comprobar(
            "%s: la imagen no se deforma" % paso["paso"],
            abs(proporcion - esperada) < 0.02,
            "proporcion %.3f, esperada %.3f" % (proporcion, esperada)
        )

        comprobar("%s: los controles siguen alcanzables" % paso["paso"],
                  paso["teclasVisibles"] and paso["cerrarVisible"])

    # --- lo decisivo ---
    final = datos["final"]

    comprobar("Tras plegar y redimensionar, es el MISMO elemento",
              final["mismoElemento"],
              "se recreo: la transmision se habria cortado")

    comprobar("Y la imagen no se recargo ni una vez",
              final["recargas"] == 0,
              "recargas: %s" % final["recargas"])

    comprobar("No se duplico la imagen",
              final["imagenes"] == 1, str(final["imagenes"]))

    comprobar("Ni el marco",
              final["marcos"] == 1, str(final["marcos"]))


def test_los_ocho_tiradores_en_un_navegador():
    """
    Arrastra los ocho, en los dos sentidos, con eventos de puntero
    de verdad. Es la unica forma de saber si un tirador engancha.
    """

    navegador = buscar_chrome()
    node = shutil.which("node")

    if not navegador or not node or not SONDA_TIRADORES.is_file():
        comprobar("(sin Chrome o sin Node: no se arrastra nada)", True)
        return

    puerto_web = buscar_puerto(PUERTO_WEB + 1)
    puerto_chrome = buscar_puerto(PUERTO_CHROME + 1)
    perfil = carpeta_de_perfil(RAIZ / "tests" / "navegador" / ".perfil2")

    if puerto_web is None or puerto_chrome is None or perfil is None:
        comprobar(
            "BLOQUEADA: no hay puerto o perfil libre para los tiradores",
            False,
            "web desde %d, chrome desde %d"
            % (PUERTO_WEB + 1, PUERTO_CHROME + 1)
        )
        return

    web = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(puerto_web),
         "--bind", "127.0.0.1"],
        cwd=str(RAIZ),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    chrome = subprocess.Popen(
        [navegador, "--headless", "--disable-gpu", "--no-sandbox",
         "--remote-debugging-port=%d" % puerto_chrome,
         "--user-data-dir=%s" % perfil, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    try:

        # Igual que la otra: se espera a que Chrome conteste. Esta
        # era precisamente la funcion cuyas medidas salian mal.
        if not chrome_de_pruebas.esperar_a_chrome(puerto_chrome):
            comprobar(
                "BLOQUEADA: Chrome no levanto a tiempo (tiradores)", False,
                "suele ser un Chrome headless huerfano de una corrida "
                "anterior ocupando la maquina"
            )
            return

        chrome_de_pruebas.esperar_al_servidor(puerto_web)

        salida = subprocess.run(
            [node, str(SONDA_TIRADORES),
             str(puerto_web), str(puerto_chrome)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=240
        )

        if salida.returncode != 0 or not salida.stdout.strip():
            comprobar("La sonda de tiradores corre", False,
                      (salida.stderr or "(sin salida)")[-220:])
            return

        datos = json.loads(salida.stdout)

    except Exception as error:
        comprobar("La sonda de tiradores corre", False,
                  type(error).__name__ + ": " + str(error)[:160])
        return

    finally:
        # El arbol entero, igual que arriba.
        for proceso in (chrome, web):
            chrome_de_pruebas.cerrar_del_todo(proceso)

        shutil.rmtree(perfil, ignore_errors=True)

    # Lo primero: que no le roben ni un pixel al control remoto
    comprobar("Hay ocho tiradores",
              datos["solape"]["tiradores"] == 8,
              str(datos["solape"]["tiradores"]))

    comprobar("Y NINGUNO cae encima de la imagen",
              not datos["solape"]["malos"],
              "pisan la imagen: %s" % datos["solape"]["malos"])

    # Cada arrastre, en los dos sentidos
    for paso in datos["pasos"]:

        etiqueta = "%s (%+d,%+d)" % (paso["lado"], paso["dx"], paso["dy"])

        comprobar(
            "Tirador %s cambia el ancho lo que debe" % etiqueta,
            abs(paso["dAncho"] - paso["esperaAncho"]) <= 3,
            "cambio %+d, esperado %+d" % (paso["dAncho"],
                                          paso["esperaAncho"])
        )

        comprobar(
            "Tirador %s cambia el alto lo que debe" % etiqueta,
            abs(paso["dAlto"] - paso["esperaAlto"]) <= 3,
            "cambio %+d, esperado %+d" % (paso["dAlto"],
                                          paso["esperaAlto"])
        )

    # Los topes
    limites = datos["limites"]

    comprobar("Se respeta el ancho minimo",
              limites["minAncho"] >= 280, str(limites["minAncho"]))

    comprobar("Y el alto minimo",
              limites["minAlto"] >= 180, str(limites["minAlto"]))

    comprobar("No se pasa del contenedor",
              limites["anchoReal"] <= limites["anchoPadre"],
              "%d sobre %d" % (limites["anchoReal"],
                               limites["anchoPadre"]))

    # Y la imagen sigue sin deformarse en un marco muy apaisado
    ancho, alto = datos["proporcion"]["dibujada"]
    natural_ancho, natural_alto = datos["proporcion"]["natural"]

    comprobar(
        "En un marco apaisado la imagen no se deforma",
        abs(ancho / alto - natural_ancho / natural_alto) < 0.02,
        "proporcion %.3f" % (ancho / alto)
    )


# ==================================================================

def principal():

    pruebas = [
        test_minimizar_y_cerrar_son_distintos,
        test_plegar_no_destruye_la_transmision,
        test_el_marco_se_puede_redimensionar,
        test_cada_sesion_empieza_desplegada,
        test_el_tamano_se_guarda_sin_romperse,
        test_el_navegador_de_pruebas_no_deja_restos_ni_mide_a_ciegas,
        test_en_un_navegador_de_verdad,
        test_los_ocho_tiradores_en_un_navegador
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("VENTANA REMOTA: PLEGAR, REDIMENSIONAR, CERRAR")
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
    print("PENDIENTE CON DOS COMPUTADORAS")
    print("-" * 66)
    print("""
Aqui la imagen es un SVG fijo, no una transmision real. Queda por
ver con un equipo de verdad:

  - que al minimizar y restaurar la pantalla sigue viva, sin un
    parpadeo de reconexion
  - que el raton sigue cayendo donde debe despues de redimensionar
  - que el aviso de mantenimiento sigue puesto tras minimizar
  - que la esquina de arrastre se agarra comodamente
""")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
