r"""
Las guardas que impiden instalar sobre la identidad de otro servidor.

    .venv\Scripts\python tests/test_guardas_instalador.py

El fallo
--------

install-agent.ps1 no pide credencial cuando ya existe
config\identity.json -da por hecho que el equipo esta de alta- pero
SI reescribe el .env con la direccion nueva. Si esa identidad era de
otro servidor, el Agent se presenta con un token que el destino no
conoce: 401, y como nadie le vuelve a pedir credencial, se queda asi
indefinidamente.

Y no se nota al instalar: la instalacion dice que todo fue bien.

Como se prueba
--------------

No se reimplementa la logica: se EXTRAE el bloque 2b del propio
install-agent.ps1 y se ejecuta tal cual, con las variables que el
script tendria en ese punto. Si alguien cambia el bloque, esta prueba
corre el texto nuevo; si borra los marcadores, falla en vez de pasar
en verde sobre nada.

Hace falta porque el script entero no se puede ejecutar aqui: aborta
antes, en la comprobacion de administrador.

El servidor se simula con un http.server local en un puerto que
asigna el sistema. No se toca la base de datos real, no se registra
ningun equipo y no se instala nada.
"""

import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
INSTALADOR = RAIZ / "installer" / "install-agent.ps1"
COMPROBADOR = RAIZ / "installer" / "comprobar-equipo.ps1"

TOKEN_BUENO = "token-que-el-servidor-conoce"

resultados = []


def comprobar(nombre, bien, detalle=""):
    resultados.append((nombre, bool(bien), detalle))


# ==================================================================
# Un servidor que hace de RemoteAdmin
# ==================================================================

class Manejador(http.server.BaseHTTPRequestHandler):
    """Solo /api/agent/version, que es lo unico que consultan."""

    def do_GET(self):

        if self.path != "/api/agent/version":
            self.send_error(404)
            return

        if self.headers.get("X-Agent-Token") == TOKEN_BUENO:
            cuerpo = json.dumps({"manifest": {"version": "1.2.0"}}).encode()
            self.send_response(200)
        else:
            cuerpo = json.dumps({"detail": "no autorizado"}).encode()
            self.send_response(401)

        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *args):
        pass


def levantar():
    """Devuelve (servidor, url). Puerto 0: lo elige el sistema.

    Pedir un puerto concreto es como una suite le roba el puerto a
    otra cuando corren seguidas.
    """

    servidor = http.server.HTTPServer(("127.0.0.1", 0), Manejador)

    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()

    return servidor, "http://127.0.0.1:%d" % servidor.server_address[1]


# ==================================================================
# Extraer el bloque real del instalador
# ==================================================================

FUENTE = io.open(INSTALADOR, encoding="utf-8").read()

INICIO = "# 2b. Que la identidad sea de ESTE servidor"
FIN = "# fin de 2b"
COPIA = "# 3. Copiar el Agent"

if INICIO not in FUENTE or FIN not in FUENTE or COPIA not in FUENTE:
    print("No se encontraron los marcadores del bloque 2b en")
    print(INSTALADOR)
    print("La prueba no puede comprobar nada. Se detiene.")
    sys.exit(1)

BLOQUE = FUENTE[FUENTE.index(INICIO):FUENTE.index(FIN)]


# La posicion importa tanto como el contenido: una guarda que corra
# DESPUES de copiar archivos ya no puede prometer que no toco nada.
comprobar(
    "las guardas van antes de copiar el Agent",
    FUENTE.index(FIN) < FUENTE.index(COPIA)
)

comprobar(
    "y antes de escribir el .env",
    FUENTE.index(INICIO) < FUENTE.index("REMOTEADMIN_SERVER=$Server")
)

comprobar(
    "la guarda A lee el .env INSTALADO, no el del paquete",
    "Select-String -Path $EnvFile" in BLOQUE
)

comprobar(
    "la guarda B consulta /api/agent/version",
    "/api/agent/version" in BLOQUE
)

comprobar(
    "y manda el token individual en la cabecera",
    'X-Agent-Token" = $identidad.agent_token' in BLOQUE
)

comprobar(
    "no se desactiva la validacion TLS en ningun sitio",
    "SkipCertificateCheck" not in FUENTE
    and "ServerCertificateValidationCallback" not in FUENTE
)

comprobar(
    "el bloque NO borra ni renombra la identidad",
    "Remove-Item" not in BLOQUE and "Rename-Item -Path" not in BLOQUE
)

comprobar(
    "pero explica como apartarla a mano",
    "identity.ANTES.json" in BLOQUE
)

comprobar(
    "y avisa de que el device_id cambiaria",
    "device_id NUEVO" in BLOQUE
)


# ==================================================================
# Ejecutar el bloque en un entorno de mentira
# ==================================================================

def correr(con_identidad=True, servidor="", servidor_anterior=None,
           token=TOKEN_BUENO, forzar=False):
    """
    Ejecuta el bloque 2b tal cual, con las variables que tendria.

    Devuelve (codigo, salida, carpeta). La carpeta se deja para poder
    comprobar que no se escribio nada dentro.
    """

    carpeta = tempfile.mkdtemp(prefix="guardas-")

    datos = os.path.join(carpeta, "RemoteAdmin")
    config = os.path.join(datos, "config")
    os.makedirs(config)

    identidad = os.path.join(config, "identity.json")
    env = os.path.join(datos, ".env")

    if con_identidad:
        with io.open(identidad, "w", encoding="utf-8") as f:
            json.dump({"device_id": "equipo-de-prueba",
                       "agent_token": token}, f)

    if servidor_anterior:
        with io.open(env, "w", encoding="utf-8") as f:
            f.write("REMOTEADMIN_SERVER=%s\n" % servidor_anterior)

    # Las variables que install-agent.ps1 ya tiene resueltas al
    # llegar al bloque. Se definen igual que alli.
    preambulo = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$InstallDir = '%s'" % datos,
        "$EnvFile = '%s'" % env,
        "$IdentityFile = '%s'" % identidad,
        "$Server = '%s'" % servidor,
        "$yaEnrolado = $%s" % ("true" if con_identidad else "false"),
        "$Forzar = $%s" % ("true" if forzar else "false"),
        "",
    ])

    guion = os.path.join(carpeta, "bloque.ps1")

    with io.open(guion, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(preambulo + BLOQUE)

    hecho = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", guion],
        capture_output=True, text=True, timeout=120
    )

    return hecho.returncode, (hecho.stdout or "") + (hecho.stderr or ""), datos


def nada_escrito(datos, con_identidad=True, con_env=False):
    """Que el bloque no haya dejado ni quitado nada."""

    esperado = {"config"}

    if con_env:
        esperado.add(".env")

    return set(os.listdir(datos)) == esperado


servidor, URL = levantar()

try:

    # --- 1. Sin identidad: el bloque no hace nada ------------------
    #
    # Es el caso de un equipo limpio, que es justo el que se busca.

    codigo, salida, datos = correr(con_identidad=False, servidor=URL)

    comprobar(
        "sin identidad el bloque no se interpone",
        codigo == 0,
        salida.strip()[-200:]
    )

    comprobar(
        "y no consulta nada al servidor",
        "Comprobando que la identidad" not in salida
    )

    # --- 2. Guarda A: el .env apunta a otro servidor ---------------

    codigo, salida, datos = correr(
        servidor=URL,
        servidor_anterior="https://otro-servidor.example"
    )

    comprobar(
        "con el .env de otro servidor, se detiene",
        codigo != 0,
        "codigo %s" % codigo
    )

    comprobar(
        "y dice las dos direcciones",
        "otro-servidor.example" in salida and URL in salida
    )

    comprobar(
        "la guarda A corta ANTES de preguntar al servidor",
        "Comprobando que la identidad" not in salida
    )

    comprobar(
        "no toca nada del equipo (guarda A)",
        nada_escrito(datos, con_env=True)
    )

    # --- 3. Guarda A: el .env coincide, y el token vale ------------

    codigo, salida, datos = correr(
        servidor=URL,
        servidor_anterior=URL,
        token=TOKEN_BUENO
    )

    comprobar(
        "con el mismo servidor y token valido, continua",
        codigo == 0,
        salida.strip()[-300:]
    )

    comprobar(
        "y lo dice",
        "La identidad es de este servidor" in salida
    )

    # --- 4. Guarda B: el servidor rechaza el token -----------------
    #
    # Sin .env: la guarda A no puede opinar. Es el caso para el que
    # existe la guarda B.

    codigo, salida, datos = correr(
        servidor=URL,
        servidor_anterior=None,
        token="token-de-otro-servidor"
    )

    comprobar(
        "con el token rechazado, se detiene",
        codigo != 0,
        "codigo %s" % codigo
    )

    comprobar(
        "y dice que el servidor no la reconoce",
        "NO reconoce la identidad" in salida
    )

    comprobar(
        "no toca nada del equipo (guarda B)",
        nada_escrito(datos)
    )

    comprobar(
        "sin .env que comparar, la guarda B si llega a preguntar",
        "Comprobando que la identidad" in salida
    )

    # --- 5. Guarda B: token valido pero sin .env -------------------

    codigo, salida, datos = correr(
        servidor=URL,
        servidor_anterior=None,
        token=TOKEN_BUENO
    )

    comprobar(
        "sin .env pero con token valido, continua",
        codigo == 0,
        salida.strip()[-300:]
    )

    # --- 6. -Forzar se salta las dos --------------------------------

    codigo, salida, datos = correr(
        servidor=URL,
        servidor_anterior="https://otro-servidor.example",
        token="token-de-otro-servidor",
        forzar=True
    )

    comprobar(
        "-Forzar instala igual",
        codigo == 0,
        salida.strip()[-200:]
    )

    comprobar(
        "pero avisa de lo que puede pasar",
        "AVISO" in salida and "401" in salida
    )

finally:
    servidor.shutdown()
    servidor.server_close()


# --- 7. No se puede preguntar: indeterminado -----------------------
#
# El servidor ya esta apagado, asi que el puerto rechaza la conexion.
# Es lo que pasaria sin red, con un cortafuegos por medio o con un
# certificado que no valida.

codigo, salida, datos = correr(
    servidor=URL,
    servidor_anterior=None,
    token=TOKEN_BUENO
)

comprobar(
    "si no se puede comprobar, se detiene igual",
    codigo != 0,
    "codigo %s" % codigo
)

comprobar(
    "y lo llama por su nombre, no lo da por bueno",
    "No se pudo comprobar la identidad" in salida
)

comprobar(
    "no toca nada del equipo (indeterminado)",
    nada_escrito(datos)
)


# ==================================================================
# El comprobador: sus cuatro salidas
# ==================================================================
#
# comprobar-equipo.ps1 responde lo mismo pero sin instalar, para
# poder mirar un equipo antes de decidir. Se ejecuta entero, que para
# eso no necesita administrador.

def comprobador(con_identidad, servidor, token=TOKEN_BUENO,
                servidor_anterior=None):
    """Devuelve (codigo, salida) con un ProgramData de mentira."""

    carpeta = tempfile.mkdtemp(prefix="comprobador-")

    datos = os.path.join(carpeta, "RemoteAdmin")
    config = os.path.join(datos, "config")
    os.makedirs(config)

    if con_identidad:
        with io.open(os.path.join(config, "identity.json"),
                     "w", encoding="utf-8") as f:
            json.dump({"device_id": "equipo-de-prueba",
                       "agent_token": token}, f)

    if servidor_anterior:
        with io.open(os.path.join(datos, ".env"), "w", encoding="utf-8") as f:
            f.write("REMOTEADMIN_SERVER=%s\n" % servidor_anterior)

    entorno = dict(os.environ)
    entorno["ProgramData"] = carpeta

    hecho = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass",
         "-File", str(COMPROBADOR), "-Servidor", servidor],
        capture_output=True, text=True, timeout=120, env=entorno
    )

    shutil.rmtree(carpeta, ignore_errors=True)

    return hecho.returncode, (hecho.stdout or "") + (hecho.stderr or "")


servidor, URL = levantar()

try:

    codigo, salida = comprobador(False, URL)

    comprobar(
        "comprobador: sin identidad dice EQUIPO LIMPIO",
        codigo == 0 and "EQUIPO LIMPIO" in salida,
        "codigo %s" % codigo
    )

    codigo, salida = comprobador(True, URL, token=TOKEN_BUENO)

    comprobar(
        "comprobador: identidad de este servidor dice APTO",
        codigo == 0 and "APTO: la identidad es de ESTE servidor" in salida,
        "codigo %s" % codigo
    )

    codigo, salida = comprobador(True, URL, token="token-de-otro")

    comprobar(
        "comprobador: identidad ajena dice NO APTO",
        codigo == 1 and "NO APTO" in salida,
        "codigo %s" % codigo
    )

    codigo, salida = comprobador(
        True, URL + "/panel", token=TOKEN_BUENO
    )

    comprobar(
        "comprobador: rechaza una direccion con /panel",
        codigo == 2 and "direccion del PANEL" in salida,
        "codigo %s" % codigo
    )

finally:
    servidor.shutdown()
    servidor.server_close()


codigo, salida = comprobador(True, URL, token=TOKEN_BUENO)

comprobar(
    "comprobador: sin servidor dice INDETERMINADO",
    codigo == 1 and "INDETERMINADO" in salida,
    "codigo %s" % codigo
)


# ------------------------------------------------------------------

print()
print("=" * 66)
print("GUARDAS CONTRA LA IDENTIDAD DE OTRO SERVIDOR")
print("=" * 66)
print()

fallos = 0

for nombre, bien, detalle in resultados:

    print("  %s  %s" % ("OK   " if bien else "FALLA", nombre))

    if not bien:
        fallos += 1
        if detalle:
            print("          %s" % detalle)

print()
print("%d/%d comprobaciones" % (len(resultados) - fallos, len(resultados)))

sys.exit(1 if fallos else 0)
