"""
El paquete de actualizacion lleva TODO lo que el Agent importa.

    .venv\\Scripts\\python tests/test_paquete_completo.py

Por que existe esta prueba
--------------------------

Ha pasado dos veces. Primero con maintenance.py, despues con
owners.py: un modulo nuevo del Agent que se queda fuera de
PACKAGE_FILES.

En desarrollo no se nota, porque ahi estan todos los archivos. Y en
una instalacion nueva tampoco, porque el instalador empaqueta la
carpeta agent entera. Solo se ve por el canal de actualizacion, que
es el peor sitio donde verlo:

    REPLACEABLE = ("agent", "installer")

El actualizador BORRA la carpeta agent y la repone con la del
paquete. Si falta un modulo, el equipo se queda con un agent.py que
no se puede importar. Y no es un fallo parcial: el Agent no arranca,
en todos los equipos a la vez, y sin panel desde el que arreglarlo.

Como se comprueba
-----------------

No se revisa la lista a ojo. Se calcula el cierre transitivo de los
imports desde agent.py y helper.py, se construye el paquete DE
VERDAD, y se importa el Agent desde el paquete descomprimido, en un
proceso aparte y con sys.path apuntando solo ahi.

Eso es lo unico que reproduce lo que ocurre en un equipo despues de
actualizar.
"""

import ast
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))

from backend import packaging  # noqa: E402


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# Por donde empieza a ejecutarse el Agent en un equipo
ENTRADAS = ("agent", "helper")


def modulos_locales():
    """Los .py que hay en agent/, por su nombre de modulo."""

    return {
        archivo[:-3]
        for archivo in os.listdir(RAIZ / "agent")
        if archivo.endswith(".py")
    }


def importados_por(modulo, locales):
    """Los modulos LOCALES que importa este archivo."""

    ruta = RAIZ / "agent" / ("%s.py" % modulo)

    arbol = ast.parse(io.open(ruta, encoding="utf-8").read())

    encontrados = set()

    for nodo in ast.walk(arbol):

        if isinstance(nodo, ast.Import):

            for alias in nodo.names:
                encontrados.add(alias.name.split(".")[0])

        elif isinstance(nodo, ast.ImportFrom):

            # level > 0 es un import relativo: no es un modulo suelto
            if nodo.level == 0 and nodo.module:
                encontrados.add(nodo.module.split(".")[0])

    return encontrados & locales


def necesarios():
    """
    Cierre transitivo desde las entradas.

    No basta con mirar agent.py: helper.py importa maintenance, y si
    algun dia maintenance importara otra cosa, tambien haria falta.
    """

    locales = modulos_locales()

    pendientes = set(ENTRADAS)
    vistos = set()

    while pendientes:

        modulo = pendientes.pop()

        if modulo in vistos:
            continue

        vistos.add(modulo)

        pendientes |= importados_por(modulo, locales)

    return vistos


def construir_paquete():
    """El mismo contenido que armaria el firmador."""

    memoria = io.BytesIO()

    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as paquete:

        for relativo in packaging.PACKAGE_FILES:

            origen = RAIZ.joinpath(*relativo.split("/"))

            if not origen.is_file():
                raise AssertionError("falta en disco: %s" % relativo)

            paquete.write(origen, arcname=relativo)

    return memoria.getvalue()


# ==================================================================
# 1. LA LISTA
# ==================================================================

def test_no_falta_ningun_modulo_que_el_agent_importe():

    hacen_falta = necesarios()

    comprobar("Se encuentran los modulos del Agent",
              len(hacen_falta) >= 10, str(sorted(hacen_falta)))

    faltan = [
        m for m in sorted(hacen_falta)
        if "agent/%s.py" % m not in packaging.PACKAGE_FILES
    ]

    comprobar("El paquete los lleva todos",
              not faltan,
              "faltan: %s" % faltan)

    # Los dos que ya se escaparon una vez, nombrados
    for modulo in ("owners", "maintenance", "scheduler", "recorder",
                   "updater", "release", "storage", "ipc", "commands",
                   "paths", "power", "inventory", "version"):

        comprobar("Esta agent/%s.py" % modulo,
                  "agent/%s.py" % modulo in packaging.PACKAGE_FILES)


def test_no_se_empaqueta_lo_que_nadie_usa():
    """
    Al reves tambien importa: un modulo que ya no se importa y sigue
    en la lista es codigo muerto viajando a todos los equipos.
    """

    hacen_falta = necesarios()

    sobran = [
        f for f in packaging.PACKAGE_FILES
        if f.startswith("agent/")
        and f != "agent/__init__.py"
        and f[len("agent/"):-3] not in hacen_falta
    ]

    comprobar("No sobra ningun modulo en el paquete",
              not sobran, "sobran: %s" % sobran)


def test_todos_los_archivos_existen():

    faltan = [
        f for f in packaging.PACKAGE_FILES
        if not RAIZ.joinpath(*f.split("/")).is_file()
    ]

    comprobar("Todo lo listado existe en disco",
              not faltan, "no existen: %s" % faltan)


# ==================================================================
# 2. EL PAQUETE DE VERDAD
# ==================================================================

def test_el_paquete_construido_lleva_los_modulos():

    try:
        contenido = construir_paquete()

    except AssertionError as error:
        comprobar("Se puede construir el paquete", False, str(error))
        return

    comprobar("Se puede construir el paquete", True)

    with zipfile.ZipFile(io.BytesIO(contenido)) as paquete:
        dentro = set(paquete.namelist())

    for modulo in sorted(necesarios()):

        comprobar("En el ZIP: agent/%s.py" % modulo,
                  "agent/%s.py" % modulo in dentro)

    comprobar("Y no viaja ningun .env ni clave",
              not any(n.endswith((".env", ".pem", ".key"))
                      for n in dentro),
              str([n for n in dentro
                   if n.endswith((".env", ".pem", ".key"))]))


def test_el_agent_se_importa_DESDE_EL_PAQUETE():
    """
    La prueba que de verdad reproduce una actualizacion.

    Se descomprime el paquete en una carpeta vacia y se importa el
    Agent con sys.path apuntando SOLO ahi. Si falta un modulo, aqui
    salta; en el repositorio de desarrollo no saltaria nunca, porque
    ahi estan todos los archivos.
    """

    try:
        contenido = construir_paquete()

    except AssertionError as error:
        comprobar("Se puede construir el paquete", False, str(error))
        return

    carpeta = tempfile.mkdtemp(prefix="paquete-")

    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as paquete:
            paquete.extractall(carpeta)

        destino = os.path.join(carpeta, "agent")

        guion = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import agent\n"
            "import helper\n"
            "print('IMPORTA_BIEN')\n"
        ) % destino

        salida = subprocess.run(
            [sys.executable, "-c", guion],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
            # Sin el directorio actual en la ruta: si no, Python
            # encontraria los modulos del repositorio y la prueba
            # pasaria aunque el paquete estuviera incompleto.
            cwd=carpeta
        )

        comprobar(
            "El Agent se importa desde el paquete descomprimido",
            "IMPORTA_BIEN" in salida.stdout,
            (salida.stderr or "").strip().splitlines()[-1:][0]
            if (salida.stderr or "").strip() else "(sin error)"
        )

        comprobar(
            "Y el ayudante tambien",
            salida.returncode == 0,
            "codigo %d" % salida.returncode
        )

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def test_la_prueba_detecta_un_modulo_que_falte():
    """
    Una prueba que no se ha visto fallar no demuestra nada.

    Se quita owners.py de la lista EN MEMORIA —no del archivo— y se
    comprueba que el paquete resultante ya no se puede importar.
    """

    original = packaging.PACKAGE_FILES

    packaging.PACKAGE_FILES = tuple(
        f for f in original if f != "agent/owners.py"
    )

    carpeta = tempfile.mkdtemp(prefix="paquete-roto-")

    try:
        contenido = construir_paquete()

        with zipfile.ZipFile(io.BytesIO(contenido)) as paquete:
            paquete.extractall(carpeta)

        salida = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import agent"
             % os.path.join(carpeta, "agent")],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
            cwd=carpeta
        )

        comprobar(
            "Sin owners.py el Agent NO arranca",
            salida.returncode != 0
            and "owners" in (salida.stderr or ""),
            (salida.stderr or "")[-120:]
        )

        faltan = [
            m for m in sorted(necesarios())
            if "agent/%s.py" % m not in packaging.PACKAGE_FILES
        ]

        comprobar("Y la comprobacion de la lista lo detecta",
                  faltan == ["owners"], str(faltan))

    finally:
        packaging.PACKAGE_FILES = original
        shutil.rmtree(carpeta, ignore_errors=True)

    comprobar("La lista queda como estaba",
              "agent/owners.py" in packaging.PACKAGE_FILES)


# ==================================================================

def principal():

    pruebas = [
        test_no_falta_ningun_modulo_que_el_agent_importe,
        test_no_se_empaqueta_lo_que_nadie_usa,
        test_todos_los_archivos_existen,
        test_el_paquete_construido_lleva_los_modulos,
        test_el_agent_se_importa_DESDE_EL_PAQUETE,
        test_la_prueba_detecta_un_modulo_que_falte
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("EL PAQUETE DE ACTUALIZACION ESTA COMPLETO")
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

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
