r"""
Arrancar y cerrar el Chrome de las pruebas, sin dejar restos.

Lo usan las suites que miden pixeles de verdad en un navegador
-test_ventana_remota y test_teclado_remoto-, que son las unicas que
detectan una deformacion o un foco perdido.

Dos problemas que se repetian, y los dos salieron a la luz el mismo
dia:

HUERFANOS

    proceso.terminate()

mata el chrome.exe que se lanzo, pero Chrome arranca HIJOS
-renderizador, GPU, utilidades- que sobreviven al padre. Cada
ejecucion dejaba un par sueltos, headless y sin ventana, invisibles
salvo mirando la lista de procesos.

Con unos cuantos acumulados, la maquina va mas cargada y la prueba
que mide pixeles empieza a medir mal. No falla: mide MAL, que es
peor, porque parece un defecto del producto. Se perdio una tarde
buscando en el codigo de la ventana remota un fallo que no estaba
ahi.

Aqui se cierra el ARBOL entero, y solo el del proceso que esta
prueba lanzo: nunca un Chrome que estuviera abierto antes.

LA ESPERA FIJA

    time.sleep(4)

Cuatro segundos bastan en una maquina descansada. Con la maquina
ocupada -por ejemplo, por los huerfanos de arriba- Chrome todavia no
esta listo, la sonda se conecta a medias y las medidas salen
equivocadas.

Ahora se espera a que Chrome CONTESTE, con un plazo. Si no contesta,
se dice; no se mide igualmente.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from pathlib import Path


# Cuanto se espera como mucho a que Chrome levante. Generoso porque
# en una maquina cargada tarda, y acotado porque una prueba colgada
# no avisa de nada.
PLAZO_DE_ARRANQUE = 40


def puerto_libre():
    """
    Uno que elige el sistema operativo.

    Con bind al puerto 0 y no escaneando un rango: un escaneo podria
    alcanzar el puerto que otra suite tiene reservado y robarselo.
    """

    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    except OSError:
        return None


def esperar_a_chrome(puerto, limite=PLAZO_DE_ARRANQUE):
    """
    Espera a que Chrome conteste en su puerto de depuracion.

    Devuelve True si contesto. Sustituye a un sleep fijo, que es una
    apuesta sobre lo ocupada que esta la maquina.
    """

    hasta = time.monotonic() + limite

    while time.monotonic() < hasta:

        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:%d/json/version" % puerto, timeout=2
            ) as respuesta:

                if respuesta.status == 200:
                    json.loads(respuesta.read().decode("utf-8"))
                    return True

        except (urllib.error.URLError, OSError, ValueError):
            pass

        time.sleep(0.3)

    return False


def esperar_al_servidor(puerto, limite=15):
    """Lo mismo para el servidor de archivos que sirve el panel."""

    hasta = time.monotonic() + limite

    while time.monotonic() < hasta:

        try:
            with socket.create_connection(("127.0.0.1", puerto), timeout=1):
                return True

        except OSError:
            time.sleep(0.2)

    return False


def cerrar_del_todo(proceso):
    """
    Cierra un proceso y TODOS sus hijos.

    Es lo que evita los huerfanos. En Windows se usa taskkill con /T
    porque terminate() solo alcanza al padre, y los procesos de
    Chrome que de verdad consumen son los hijos.

    Solo se cierra el arbol del proceso que la prueba lanzo. Nunca se
    busca "chrome.exe" por nombre: eso cerraria el navegador que la
    persona tuviera abierto, que no es nuestro y no se toca.
    """

    if proceso is None:
        return

    if proceso.poll() is not None:
        return

    if os.name == "nt":

        try:
            subprocess.run(
                ["taskkill", "/PID", str(proceso.pid), "/T", "/F"],
                capture_output=True, timeout=20
            )

        except Exception:
            pass

    try:
        proceso.terminate()
        proceso.wait(timeout=8)

    except Exception:

        try:
            proceso.kill()
        except Exception:
            pass


def restos_de_pruebas_anteriores(raiz):
    """
    Chrome headless de una corrida anterior que use NUESTROS perfiles.

    No cierra nada: solo informa, para que una prueba pueda decir
    "estoy bloqueada" en vez de medir mal y echarle la culpa al
    producto.

    Se reconocen por la carpeta de perfil, que esta dentro del
    repositorio. Un Chrome normal nunca apunta ahi.
    """

    if os.name != "nt":
        return []

    marca = str(Path(raiz) / "tests" / "navegador").lower()

    try:
        salida = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
             "| Where-Object { $_.CommandLine -like '*--headless*' } "
             "| ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
            capture_output=True, text=True, timeout=30
        )

    except Exception:
        return []

    encontrados = []

    for linea in (salida.stdout or "").splitlines():

        if "|" not in linea:
            continue

        pid, _, orden = linea.partition("|")

        if marca in orden.lower():

            try:
                encontrados.append(int(pid.strip()))
            except ValueError:
                pass

    return encontrados


def arrancar(navegador, puerto, perfil):
    """Un Chrome headless de pruebas, aislado en su propio perfil."""

    return subprocess.Popen(
        [navegador, "--headless", "--disable-gpu", "--no-sandbox",
         "--remote-debugging-port=%d" % puerto,
         "--user-data-dir=%s" % perfil, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def servidor_de_archivos(puerto, raiz):
    """El panel servido por HTTP, para que el navegador lo abra."""

    return subprocess.Popen(
        [sys.executable, "-m", "http.server", str(puerto),
         "--bind", "127.0.0.1"],
        cwd=str(raiz),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
