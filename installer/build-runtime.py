# -*- coding: utf-8 -*-
"""
Arma el Python embebido que acompana al instalador de VEYRA.

Por que existe
--------------

Hasta ahora install-agent.ps1 exigia Python instalado en el equipo y
hacia pip install de las dependencias. Eso obligaba al cliente a
instalar Python a mano y a tener salida a internet en el momento justo
de la instalacion.

Aqui se prepara, de una vez y en el equipo de quien publica, una
carpeta con el interprete y TODAS las dependencias ya resueltas. El
instalador la copia tal cual. En el equipo destino no se ejecuta
ningun pip ni hace falta Python.

Lo que NO cambia, y es deliberado
---------------------------------

El paquete de ACTUALIZACION (backend/packaging.py, PACKAGE_FILES)
sigue llevando solo los .py del Agent: 73 KB. El interprete no entra
ahi.

Dos razones. La primera, que una actualizacion solo sustituye el
programa; el interprete ya esta en el equipo desde la instalacion y no
hay motivo para volver a mandarlo. La segunda, mas importante: meterlo
cambiaria el contenido del paquete y con el su hash, invalidando la
firma de la 1.2.0 que ya esta publicada.

    instalacion   ->  interprete + Agent   (este archivo)
    actualizacion ->  solo el Agent        (firmado, sin tocar)

Uso:

    .venv\\Scripts\\python installer\\build-runtime.py
    .venv\\Scripts\\python installer\\build-runtime.py --limpiar
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

from datetime import datetime, timezone


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DESTINO = os.path.join(RAIZ, "installer", "runtime")

# La misma version con la que se desarrolla y se prueba. Clavarla
# evita que el equipo del cliente acabe con un interprete distinto del
# que se uso para comprobar que todo importa.
VERSION_PYTHON = "3.13.15"

URL_EMBED = (
    "https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip"
    % (VERSION_PYTHON, VERSION_PYTHON)
)

REQUISITOS = os.path.join(RAIZ, "requirements-agent-windows.txt")


# El archivo de rutas del interprete embebido.
#
# 'import site' no es opcional: pywin32 se instala con un pywin32.pth
# que añade sus carpetas y carga sus DLL. Sin site, los .pth no se
# procesan y win32com no importa, que es justo lo que necesita el
# inventario de Windows.
CONTENIDO_PTH = """python313.zip
.
Lib\\site-packages

import site
"""


# Lo que tiene que poder importar el Agent para funcionar. Se
# comprueba de verdad al final, ejecutando el interprete recien
# armado: una carpeta con archivos dentro no demuestra nada.
IMPORTES = (
    "dotenv", "websockets", "imageio_ffmpeg", "requests", "psutil",
    "mss", "PIL", "pyautogui", "wmi", "win32api", "win32com.client",
    "win32service", "cryptography", "ssl", "sqlite3", "zoneinfo",
    "asyncio", "ctypes"
)


def descargar(url):
    print("Descargando %s" % url)

    with urllib.request.urlopen(url, timeout=180) as respuesta:
        datos = respuesta.read()

    print("  %.1f MB" % (len(datos) / 1048576))

    return datos


def armar(limpiar=False):

    if limpiar and os.path.isdir(DESTINO):
        print("Limpiando %s" % DESTINO)
        shutil.rmtree(DESTINO)

    if os.path.isdir(DESTINO):
        print("Ya existe %s" % DESTINO)
        print("Usa --limpiar para rehacerlo desde cero.")
        return 1

    os.makedirs(DESTINO)

    # --- 1. El interprete ---

    datos = descargar(URL_EMBED)

    with zipfile.ZipFile(io.BytesIO(datos)) as paquete:
        paquete.extractall(DESTINO)

    # --- 2. Las rutas ---

    nombre_pth = "python%s._pth" % "".join(VERSION_PYTHON.split(".")[:2])

    with io.open(os.path.join(DESTINO, nombre_pth), "w",
                 encoding="utf-8") as archivo:
        archivo.write(CONTENIDO_PTH)

    print("Rutas escritas en %s" % nombre_pth)

    # --- 3. Las dependencias ---

    print("Instalando dependencias (esto tarda)")

    sitio = os.path.join(DESTINO, "Lib", "site-packages")

    resultado = subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--quiet", "--disable-pip-version-check",
         "--target", sitio, "-r", REQUISITOS],
        capture_output=True, text=True, errors="replace"
    )

    if resultado.returncode != 0:
        print("FALLO pip:")
        print(resultado.stdout[-2000:])
        print(resultado.stderr[-2000:])
        return 1

    # --- 4. La comprobacion que importa ---

    print()
    print("Comprobando que el Agent puede importar lo que necesita")

    interprete = os.path.join(DESTINO, "python.exe")

    guion = (
        "import sys\n"
        "fallos = []\n"
        "for m in %r:\n"
        "    try:\n"
        "        __import__(m)\n"
        "    except Exception as e:\n"
        "        fallos.append((m, type(e).__name__, str(e)[:90]))\n"
        "print(repr(fallos))\n"
    ) % (IMPORTES,)

    salida = subprocess.run(
        [interprete, "-c", guion],
        capture_output=True, text=True, errors="replace", timeout=180
    )

    if salida.returncode != 0:
        print("El interprete embebido no arranca:")
        print(salida.stderr[-1500:])
        return 1

    fallos = eval(salida.stdout.strip() or "[]")

    for modulo, clase, mensaje in fallos:
        print("  FALLA %-18s %s: %s" % (modulo, clase, mensaje))

    if fallos:
        print()
        print("%d dependencias no importan. El instalador NO sirve asi."
              % len(fallos))
        return 1

    print("  los %d modulos importan" % len(IMPORTES))

    # --- 5. La ficha ---

    total = 0
    cuenta = 0

    for carpeta, _, archivos in os.walk(DESTINO):
        for nombre in archivos:
            ruta = os.path.join(carpeta, nombre)
            total += os.path.getsize(ruta)
            cuenta += 1

    ficha = {
        "python": VERSION_PYTHON,
        "construido": datetime.now(timezone.utc).isoformat(),
        "archivos": cuenta,
        "bytes": total,
        "requisitos_sha256": hashlib.sha256(
            io.open(REQUISITOS, "rb").read()
        ).hexdigest(),
        "importes_comprobados": list(IMPORTES)
    }

    with io.open(os.path.join(DESTINO, "runtime.json"), "w",
                 encoding="utf-8") as archivo:
        json.dump(ficha, archivo, indent=2, ensure_ascii=False)

    print()
    print("Listo: %s" % DESTINO)
    print("  Python %s · %d archivos · %.1f MB"
          % (VERSION_PYTHON, cuenta, total / 1048576))

    return 0


def main():

    analizador = argparse.ArgumentParser(
        description="Arma el Python embebido del instalador de VEYRA"
    )

    analizador.add_argument(
        "--limpiar", action="store_true",
        help="Borra lo que haya y lo rehace desde cero"
    )

    argumentos = analizador.parse_args()

    if sys.platform != "win32":
        print("Esto se arma en Windows: las dependencias son de Windows.")
        return 1

    return armar(limpiar=argumentos.limpiar)


if __name__ == "__main__":
    sys.exit(main())
