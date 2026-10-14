"""
El instalador .exe que se descarga del panel.

    .venv\\Scripts\\python tests/test_instalador_exe.py

Que cubre
---------

Lo que SE PUEDE comprobar sin una maquina Windows de pruebas:

  - el .exe lleva dentro el Agent, el instalador y el interprete;
  - el interprete embebido importa todo lo que el Agent necesita;
  - y verifica la firma Ed25519 y el hash del paquete 1.2.0, que es
    lo que decide si la actualizacion automatica sigue funcionando;
  - el actualizador NO borra el interprete al actualizar;
  - los endpoints de descarga exigen sesion;
  - no viaja ninguna clave privada ni ningun .env dentro del .exe;
  - el paquete FIRMADO sigue siendo el de 73 KB, sin interprete, para
    que la firma de la 1.2.0 ya publicada siga valiendo.

Lo que NO cubre, y hay que probar en Windows de verdad: instalar en
limpio, actualizar sobre una instalacion existente, que el device_id
no cambie, que las grabaciones se conserven y que el Agent vuelva a
conectarse. Nada de eso se puede afirmar desde aqui.
"""

import io
import json
import os
import subprocess
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-exe")

EXE = RAIZ / "installer" / "dist" / "VEYRA-Instalador.exe"
RUNTIME = RAIZ / "installer" / "runtime"

PS1 = io.open(RAIZ / "installer" / "install-agent.ps1",
              encoding="utf-8").read()

GUI = io.open(RAIZ / "installer" / "veyra_installer.py",
              encoding="utf-8").read()

BUILD = io.open(RAIZ / "installer" / "build-installer.ps1",
                encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==================================================================
# 1. EL PAQUETE FIRMADO NO CAMBIA
# ==================================================================

def test_el_paquete_de_actualizacion_sigue_sin_interprete():
    """
    Lo mas importante de todo el cambio.

    Si el interprete entrara en PACKAGE_FILES, cambiaria el contenido
    del paquete y con el su hash, y la firma de la 1.2.0 que ya esta
    publicada dejaria de valer.

    El interprete va en el INSTALADOR; el paquete de actualizacion
    sigue llevando solo los .py del Agent.
    """

    from backend import packaging

    comprobar("PACKAGE_FILES no lleva el interprete",
              not any("runtime" in f for f in packaging.PACKAGE_FILES),
              str([f for f in packaging.PACKAGE_FILES if "runtime" in f]))

    comprobar("Tiene los 20 archivos del Agent",
              len(packaging.PACKAGE_FILES) == 20,
              str(len(packaging.PACKAGE_FILES)))

    # Lo que de verdad importa de esta lista: que no falte nada que
    # el Agent importe al arrancar. maintenance.py se quedo fuera al
    # añadirlo, y como el actualizador REEMPLAZA la carpeta agent
    # entera, una actualizacion habria dejado un helper.py importando
    # un modulo inexistente en todos los equipos.
    for modulo in ("agent/agent.py", "agent/helper.py",
                   "agent/maintenance.py", "agent/scheduler.py",
                   "agent/updater.py", "agent/release.py"):

        comprobar("El paquete lleva %s" % modulo,
                  modulo in packaging.PACKAGE_FILES)

    # Y si hay una version firmada, que siga cuadrando
    manifiesto = RAIZ / "releases" / "manifest.json"

    if not manifiesto.is_file():
        comprobar("(no hay version publicada que comprobar)", True)
        return

    datos = json.loads(io.open(manifiesto, encoding="utf-8").read())

    from agent import release

    zip_publicado = RAIZ / "releases" / (
        "agent-%s.zip" % datos["manifest"]["version"]
    )

    if not zip_publicado.is_file():
        comprobar("(falta el zip publicado)", True)
        return

    contenido = zip_publicado.read_bytes()

    try:
        release.verify_manifest(datos["manifest"], datos["signature"])
        firma_ok = True
    except Exception:
        firma_ok = False

    comprobar("La firma de la version publicada sigue siendo valida",
              firma_ok)

    try:
        release.verify_package(contenido, datos["manifest"])
        paquete_ok = True
    except Exception:
        paquete_ok = False

    comprobar("Y el paquete sigue coincidiendo con lo firmado",
              paquete_ok)


# ==================================================================
# 2. EL ACTUALIZADOR NO BORRA EL INTERPRETE
# ==================================================================

def test_actualizar_no_se_lleva_por_delante_el_interprete():
    """
    Si el actualizador borrara runtime\\, el Agent dejaria de arrancar
    tras la primera actualizacion. Seria el peor fallo posible: un
    equipo remoto que no arranca es un equipo perdido.
    """

    from agent import updater

    comprobar("Solo se reemplazan agent e installer",
              updater.REPLACEABLE == ("agent", "installer"),
              str(updater.REPLACEABLE))

    comprobar("El interprete NO esta entre lo reemplazable",
              "runtime" not in updater.REPLACEABLE)

    # Y lo de siempre sigue protegido
    for parte in ("config", "recordings", "logs", ".env"):
        comprobar("Sigue conservandose %s" % parte,
                  parte in updater.PRESERVED)


# ==================================================================
# 3. EL INTERPRETE EMBEBIDO
# ==================================================================

MODULOS = (
    "dotenv", "websockets", "imageio_ffmpeg", "requests", "psutil",
    "mss", "PIL", "pyautogui", "wmi", "win32api", "win32com.client",
    "win32service", "cryptography", "ssl", "sqlite3", "zoneinfo"
)


def test_el_interprete_embebido_importa_lo_que_hace_falta():
    """Una carpeta con archivos dentro no demuestra nada: se ejecuta."""

    interprete = RUNTIME / "python.exe"

    if not interprete.is_file():
        comprobar("Falta el interprete embebido", False,
                  "armalo con installer/build-runtime.py")
        return

    guion = (
        "import sys\n"
        "malos = []\n"
        "for m in %r:\n"
        "    try:\n"
        "        __import__(m)\n"
        "    except Exception as e:\n"
        "        malos.append(m)\n"
        "print(repr(malos))\n"
    ) % (MODULOS,)

    salida = subprocess.run(
        [str(interprete), "-c", guion],
        capture_output=True, text=True, errors="replace", timeout=180
    )

    comprobar("El interprete embebido arranca",
              salida.returncode == 0, salida.stderr[-200:])

    if salida.returncode != 0:
        return

    malos = eval(salida.stdout.strip() or "[]")

    comprobar("Los %d modulos del Agent importan" % len(MODULOS),
              not malos, "no importan: " + str(malos))


def test_el_interprete_embebido_verifica_la_firma():
    """
    La condicion que decidia si este camino era viable.

    Si el interprete embebido no pudiera comprobar una firma Ed25519,
    la actualizacion automatica quedaria rota y no se podria seguir.
    """

    interprete = RUNTIME / "python.exe"
    manifiesto = RAIZ / "releases" / "manifest.json"

    if not interprete.is_file() or not manifiesto.is_file():
        comprobar("(sin interprete o sin version publicada)", True)
        return

    guion = (
        "import sys, json\n"
        "sys.path.insert(0, %r)\n"
        "import release, updater\n"
        "d = json.load(open(%r, encoding='utf-8'))\n"
        "m, f = d['manifest'], d['signature']\n"
        "release.verify_manifest(m, f)\n"
        "z = open(%r, 'rb').read()\n"
        "release.verify_package(z, m)\n"
        "print('FIRMA_OK', updater.decide(m, '1.1.0')[0],"
        " updater.decide(m, m['version'])[0])\n"
    ) % (
        str(RAIZ / "agent"),
        str(RAIZ / "releases" / "manifest.json"),
        str(RAIZ / "releases" / ("agent-%s.zip" % json.loads(
            io.open(manifiesto, encoding="utf-8").read()
        )["manifest"]["version"]))
    )

    salida = subprocess.run(
        [str(interprete), "-c", guion],
        capture_output=True, text=True, errors="replace", timeout=120
    )

    comprobar("El interprete embebido verifica la firma Ed25519",
              "FIRMA_OK" in salida.stdout,
              (salida.stderr or salida.stdout)[-250:])

    comprobar("Y decide bien: actualiza desde 1.1.0, no desde la actual",
              "FIRMA_OK True False" in salida.stdout,
              salida.stdout.strip()[-120:])


# ==================================================================
# 4. EL .EXE
# ==================================================================

def contenido_del_exe():

    from PyInstaller.archive.readers import CArchiveReader

    lector = CArchiveReader(str(EXE))

    return [n.replace(chr(92), "/") for n in lector.toc]


def test_el_exe_lleva_todo_dentro():
    """Un solo archivo que funcione solo, sin nada alrededor."""

    if not EXE.is_file():
        comprobar("Falta el .exe", False,
                  "construyelo con installer/build-installer.ps1")
        return

    nombres = contenido_del_exe()

    for clave in ("runtime/python.exe", "runtime/pythonw.exe",
                  "agent/agent.py", "agent/scheduler.py",
                  "agent/updater.py", "agent/release.py",
                  "agent/recorder.py",
                  "installer/install-agent.ps1",
                  "installer/uninstall-agent.ps1"):

        comprobar("Dentro del .exe: %s" % clave, clave in nombres)

    ffmpeg = [n for n in nombres
              if "ffmpeg" in n.lower() and n.endswith(".exe")]

    comprobar("Y ffmpeg, que el grabador necesita", bool(ffmpeg),
              str(ffmpeg[:1]))


def test_el_exe_no_lleva_secretos():
    """
    Se distribuye a clientes. Lo que vaya dentro, va a todas partes.
    """

    if not EXE.is_file():
        comprobar("(sin .exe que comprobar)", True)
        return

    nombres = contenido_del_exe()

    claves = [n for n in nombres
              if n.endswith((".pem", ".key"))
              and not n.endswith("cacert.pem")]

    comprobar("Ninguna clave privada dentro", not claves, str(claves))

    entornos = [n for n in nombres if n.endswith(".env")]

    comprobar("Ningun .env dentro", not entornos, str(entornos))

    credenciales = [n for n in nombres if "signing" in n.lower()]

    comprobar("Ninguna clave de firma dentro", not credenciales,
              str(credenciales))

    # certifi es el paquete de certificados PUBLICOS: debe estar
    comprobar("El paquete de certificados publicos si esta",
              any(n.endswith("cacert.pem") for n in nombres))


# ==================================================================
# 5. EL SCRIPT DE INSTALACION
# ==================================================================

def test_el_script_usa_el_interprete_embebido():

    comprobar("Acepta -RuntimeDir", "$RuntimeDir" in PS1)

    comprobar("Y con el no ejecuta pip",
              "if ($UsaRuntimeEmbebido) {" in PS1)

    # El pip sigue existiendo para el camino antiguo, dentro del else
    comprobar("El camino sin interprete embebido se conserva",
              "pip install" in PS1 and "} else {" in PS1)

    comprobar("Comprueba que el interprete copiado funcione",
              "import mss, wmi, cryptography, websockets" in PS1)

    comprobar("El interprete va a una carpeta propia",
              'Join-Path $InstallDir "runtime"' in PS1)

    # Y la identidad no se toca
    comprobar("No se borra la carpeta de datos entera",
              "Remove-Item -Recurse -Force $InstallDir\n" not in PS1)


def test_el_instalador_grafico_encuentra_su_carga():

    comprobar("Busca la carga util dentro del .exe",
              "_MEIPASS" in GUI)

    comprobar("Y sabe si trae interprete",
              "def runtime_empaquetado(" in GUI)

    comprobar("Se lo pasa al script",
              '"-RuntimeDir", runtime' in GUI)

    # --uac-admin aparece en un COMENTARIO del script, explicando por
    # que no se usa. Lo que no puede haber es la bandera de verdad: se
    # miran solo las lineas que no son comentario.
    activas = [l for l in BUILD.splitlines()
               if not l.lstrip().startswith("#")]

    comprobar("Sigue sin eludir el aviso de Windows",
              not any("--uac-admin" in l for l in activas),
              str([l.strip() for l in activas if "--uac-admin" in l]))

    comprobar("Y el motivo sigue explicado",
              "--uac-admin" in BUILD)

    comprobar("El empaquetado incluye el interprete",
              '--add-data "$Runtime;runtime"' in BUILD)

    comprobar("Y se niega a empaquetar sin el",
              "Falta el Python embebido" in BUILD)


# ==================================================================
# 6. LOS ENDPOINTS
# ==================================================================

def test_la_descarga_exige_sesion():
    """
    Un .exe que cualquiera pueda bajar es un .exe que cualquiera
    puede estudiar para atacar a los equipos.
    """

    from fastapi.testclient import TestClient
    import backend.main as main

    cliente = TestClient(main.app)

    for ruta in ("/api/agent-installer", "/api/agent-installer/info"):

        respuesta = cliente.get(ruta)

        comprobar("Sin sesion, %s no entrega nada" % ruta,
                  respuesta.status_code in (401, 403),
                  "codigo %d" % respuesta.status_code)

    # Y el del ZIP sigue igual de protegido
    respuesta = cliente.get("/api/agent-package")

    comprobar("El paquete .zip sigue exigiendo sesion",
              respuesta.status_code in (401, 403),
              "codigo %d" % respuesta.status_code)


def test_la_version_sale_del_manifiesto_firmado():
    """
    No del nombre del archivo: ese se puede cambiar sin la clave.
    """

    fuente = io.open(RAIZ / "backend" / "agent_releases.py",
                     encoding="utf-8").read()

    bloque = fuente.split("def installer_info(", 1)[1]

    comprobar("installer_info lee el manifiesto firmado",
              "load_published()" in bloque)

    comprobar("Y devuelve su version",
              'manifiesto["version"]' in bloque)

    comprobar("Sin version publicada, no se ofrece el instalador",
              "ReleaseNotPublished" in fuente)


# ==================================================================

def principal():

    pruebas = [
        test_el_paquete_de_actualizacion_sigue_sin_interprete,
        test_actualizar_no_se_lleva_por_delante_el_interprete,
        test_el_interprete_embebido_importa_lo_que_hace_falta,
        test_el_interprete_embebido_verifica_la_firma,
        test_el_exe_lleva_todo_dentro,
        test_el_exe_no_lleva_secretos,
        test_el_script_usa_el_interprete_embebido,
        test_el_instalador_grafico_encuentra_su_carga,
        test_la_descarga_exige_sesion,
        test_la_version_sale_del_manifiesto_firmado
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:150])

    print()
    print("=" * 66)
    print("INSTALADOR .EXE CON PYTHON EMBEBIDO")
    print("=" * 66)
    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        print("  %s  %s" % ("OK  " if bien else "FALLA", nombre))

        if not bien and detalle:
            print("         %s" % detalle)

        if not bien:
            fallos += 1

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    print()
    print("-" * 66)
    print("LO QUE ESTO NO DEMUESTRA")
    print("-" * 66)
    print("""
No se ha instalado nada en ninguna maquina. Queda por probar en un
Windows de verdad:

  - instalacion limpia en un equipo sin Python
  - actualizacion sobre una instalacion existente
  - que el device_id NO cambie y no aparezca un equipo duplicado
  - que las grabaciones y la configuracion se conserven
  - que el Agent vuelva a conectarse despues
""")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
