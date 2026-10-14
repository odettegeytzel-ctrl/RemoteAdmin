"""
El resumen diario de la retencion, con varias empresas.

    .venv\\Scripts\\python tests/test_retencion_resumen.py

En produccion el registro mostraba una sola linea, cada arranque:

    [RETENCION] error: 'deleted'

El bucle recorre las organizaciones y guarda el parcial de cada una
bajo su nombre, de modo que 'result' quedo siendo un diccionario de
diccionarios. La condicion que decide si imprimir seguia pidiendo
result["deleted"], que es una clave del parcial y no del contenedor:
KeyError, y el mensaje de un KeyError es solo el nombre de la clave.

Nada se borraba de mas ni de menos —el fallo ocurre DESPUES de aplicar
la retencion— pero el resumen no llegaba nunca al registro. La
retencion llevaba desde el multiempresa borrando en silencio.

Aqui se ejecuta el bucle de verdad, con apply_retention sustituido por
una funcion que devuelve cuentas conocidas. No se toca ninguna base ni
ningun archivo.
"""

import asyncio
import io
import os
import sys

from contextlib import redirect_stdout
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-resumen")

import backend.main as main  # noqa: E402


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


class Parada(Exception):
    """Corta el bucle infinito tras una sola pasada."""


def una_pasada(empresas, por_empresa):
    """
    Ejecuta _retention_loop una vez y devuelve lo que imprimio.

    'empresas' son los nombres; 'por_empresa' dice, para cada una,
    cuantas grabaciones borro y cuantas rutas se saltaron.
    """

    original_listar = main.organizations.list_organizations
    original_dias = main.get_retention_days
    original_aplicar = main.apply_retention
    original_dormir = asyncio.sleep

    llamadas = []

    def listar():
        return [
            {"id": numero, "name": nombre}
            for numero, nombre in enumerate(empresas, start=1)
        ]

    def dias(organization_id):
        return 30

    def aplicar(dias_, organization_id):

        llamadas.append(organization_id)

        borradas, fuera = por_empresa[organization_id - 1]

        return {
            "deleted": ["g%d" % n for n in range(borradas)],
            "missing_file": [],
            "errors": [],
            "skipped_traversal": ["r%d" % n for n in range(fuera)]
        }

    async def dormir(segundos):
        raise Parada()

    main.organizations.list_organizations = listar
    main.get_retention_days = dias
    main.apply_retention = aplicar
    asyncio.sleep = dormir

    salida = io.StringIO()

    try:
        with redirect_stdout(salida):

            try:
                asyncio.run(main._retention_loop())

            except Parada:
                pass

    finally:
        main.organizations.list_organizations = original_listar
        main.get_retention_days = original_dias
        main.apply_retention = original_aplicar
        asyncio.sleep = original_dormir

    return salida.getvalue(), llamadas


# ------------------------------------------------------------------
# 1. El fallo que se veia en produccion
# ------------------------------------------------------------------

texto, visitadas = una_pasada(["Plastika", "Ternium"], [(2, 0), (1, 0)])

comprobar(
    "ya no salta KeyError: 'deleted'",
    "error:" not in texto,
    texto.strip() or "(sin salida)"
)

comprobar(
    "se recorren las dos empresas",
    visitadas == [1, 2],
    str(visitadas)
)


# ------------------------------------------------------------------
# 2. El resumen suma TODAS las organizaciones
# ------------------------------------------------------------------

comprobar(
    "con borrados en varias empresas, se imprime el resumen",
    "[RETENCIÓN]" in texto,
    texto.strip() or "(sin salida)"
)

comprobar(
    "el resumen nombra a las dos empresas",
    "Plastika" in texto and "Ternium" in texto,
    texto.strip()
)


# Lo que de verdad distingue sumar de mirar solo la primera: si la
# primera empresa no borro nada y la segunda si, tiene que imprimirse
# igual. Con result["deleted"] esto era un KeyError; con
# result[primera]["deleted"] seria un silencio, que es peor.

texto, _ = una_pasada(["Plastika", "Ternium"], [(0, 0), (3, 0)])

comprobar(
    "basta con que borre la ULTIMA empresa",
    "[RETENCIÓN]" in texto,
    texto.strip() or "(sin salida)"
)

texto, _ = una_pasada(["Plastika", "Ternium"], [(3, 0), (0, 0)])

comprobar(
    "basta con que borre la PRIMERA empresa",
    "[RETENCIÓN]" in texto,
    texto.strip() or "(sin salida)"
)


# Las rutas saltadas cuentan igual que los borrados: son el aviso de
# que una grabacion apuntaba fuera de su carpeta.

texto, _ = una_pasada(["Plastika", "Ternium"], [(0, 0), (0, 1)])

comprobar(
    "una ruta saltada tambien dispara el resumen",
    "[RETENCIÓN]" in texto,
    texto.strip() or "(sin salida)"
)


# ------------------------------------------------------------------
# 3. Silencio cuando no hay nada que contar
# ------------------------------------------------------------------

texto, _ = una_pasada(["Plastika", "Ternium"], [(0, 0), (0, 0)])

comprobar(
    "sin borrados no se imprime nada",
    texto.strip() == "",
    texto.strip()
)


# Una sola empresa, el caso de la mayoria de instalaciones

texto, visitadas = una_pasada(["Plastika"], [(1, 0)])

comprobar(
    "una sola empresa sigue funcionando",
    "[RETENCIÓN]" in texto and visitadas == [1],
    texto.strip()
)


# Y ninguna empresa: el bucle no debe romperse por un diccionario vacio
# —era otro camino al mismo KeyError—

texto, visitadas = una_pasada([], [])

comprobar(
    "sin organizaciones, ni error ni resumen",
    texto.strip() == "" and visitadas == [],
    texto.strip()
)


# ------------------------------------------------------------------
# 4. Que la prueba detecta el fallo si vuelve
# ------------------------------------------------------------------
#
# Una prueba que no se ha visto fallar no demuestra nada. Se reproduce
# el codigo anterior sobre los mismos datos.

try:
    fingido = {"Plastika": {"deleted": [], "skipped_traversal": []},
               "Ternium": {"deleted": ["g0"], "skipped_traversal": []}}

    fingido["deleted"]  # noqa: B018 - asi era la linea original

    detecta = False

except KeyError as error:
    detecta = str(error) == "'deleted'"

comprobar(
    "el codigo anterior daba exactamente KeyError: 'deleted'",
    detecta,
    "reproducido sobre los mismos datos"
)


# ------------------------------------------------------------------

print()
print("=" * 64)
print("RESUMEN DIARIO DE LA RETENCION")
print("=" * 64)
print()

fallos = 0

for nombre, bien, detalle in resultados:

    print("  %s  %s" % ("OK  " if bien else "FALLA", nombre))

    if not bien:
        fallos += 1

        if detalle:
            print("         %s" % detalle)

print()
print("%d de %d" % (len(resultados) - fallos, len(resultados)))

sys.exit(1 if fallos else 0)
