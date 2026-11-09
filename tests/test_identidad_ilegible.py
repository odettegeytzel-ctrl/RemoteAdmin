r"""
El instalador no confunde "no hay identidad" con "no puedo leerla".

    .venv\Scripts\python tests/test_identidad_ilegible.py

La ventana del instalador decidia si un equipo estaba dado de alta
leyendo config\identity.json, y trataba cualquier error de lectura
como "no esta dado de alta":

    except (OSError, ValueError):
        return None

Pero identity.json esta restringido a SYSTEM y a los administradores,
y la ventana corre SIN elevar: el permiso solo se pide al pulsar
Instalar. Un administrador sin elevar tiene el token filtrado, asi
que tambien se le deniega.

Resultado: en un equipo que llevaba meses funcionando, la ventana
anunciaba "este equipo todavia no esta dado de alta" y pedia una
credencial de instalacion. Quien la lee hace lo razonable -dar de
alta el equipo otra vez- y eso le cuesta su historial, porque el
equipo recibe un device_id nuevo y sus grabaciones se quedan atadas
al viejo.

Es el mismo fallo que tuvo el ayudante con el .env el mismo dia: no
distinguir "no esta" de "no me dejan verlo". El archivo es otro, el
error es identico.

Ahora son tres estados:

    {"device_id": ...}   dado de alta, y se sabe cual es
    None                 NO dado de alta -y esto es concluyente-
    ILEGIBLE             hay identidad y no se pudo leer: no se sabe

Y el tercero no pide credencial a ciegas ni afirma nada: dice que no
pudo comprobarlo y como arreglarlo.
"""

import io
import json
import os
import subprocess
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ / "installer"))

import veyra_installer as instalador  # noqa: E402


VENTANA = io.open(
    RAIZ / "installer" / "veyra_installer.py", encoding="utf-8"
).read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def con_identidad(contenido):
    """Apunta el instalador a un identity.json de prueba."""

    carpeta = tempfile.mkdtemp(prefix="identidad-")
    ruta = os.path.join(carpeta, "identity.json")

    if contenido is not None:
        with io.open(ruta, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)

    instalador.IDENTITY_FILE = ruta

    return ruta


def prueba_identidad_buena():

    con_identidad(json.dumps({
        "device_id": "695b8818171af876",
        "agent_token": "no se lee"
    }))

    identidad = instalador.identidad_existente()

    comprobar("un identity.json valido se lee",
              identidad == {"device_id": "695b8818171af876"},
              identidad)


def prueba_sin_archivo_es_concluyente():
    """
    Esto si prueba que el equipo es nuevo, y es el unico caso que lo
    prueba.
    """

    con_identidad(None)

    comprobar("sin archivo: None, y se puede concluir",
              instalador.identidad_existente() is None)


def prueba_sin_permiso_no_es_lo_mismo():
    """
    El caso real: el archivo esta y no se puede leer.

    Se deniega el permiso de verdad con icacls, no simulando la
    excepcion: lo que fallaba era el comportamiento con un archivo
    protegido de verdad, y una excepcion fingida no habria
    reproducido nada.
    """

    ruta = con_identidad(json.dumps({"device_id": "695b8818171af876"}))

    usuario = os.environ.get("USERNAME", "")

    subprocess.run(
        ["icacls", ruta, "/deny", "%s:(R)" % usuario],
        capture_output=True, text=True, timeout=30
    )

    try:
        identidad = instalador.identidad_existente()

    finally:
        subprocess.run(
            ["icacls", ruta, "/remove:d", usuario],
            capture_output=True, text=True, timeout=30
        )

    comprobar(
        "sin permiso NO se dice que el equipo sea nuevo",
        identidad is not None,
        identidad
    )

    comprobar(
        "se responde ILEGIBLE",
        identidad == instalador.ILEGIBLE,
        identidad
    )


def prueba_un_json_roto_tampoco_es_nuevo():
    """
    Un archivo que esta pero no se entiende tampoco prueba que no
    haya identidad. Puede ser una escritura a medias.
    """

    con_identidad("{esto no es json")

    comprobar("un identity.json corrupto da ILEGIBLE",
              instalador.identidad_existente() == instalador.ILEGIBLE)


def prueba_un_json_sin_device_id():
    """
    Se leyo bien y no hay identificador: ahi si no hay identidad.
    """

    con_identidad(json.dumps({"agent_token": "x"}))

    comprobar("un identity.json sin device_id da None",
              instalador.identidad_existente() is None)


def prueba_la_ventana_no_exige_credencial_a_ciegas():
    """
    La parte que de verdad costaba el historial.

    Con la identidad ilegible la ventana puede ENSEÑAR el campo de la
    credencial -podria ser un equipo nuevo de verdad- pero no puede
    EXIGIRLA, porque exigirla es afirmar que el equipo no esta dado
    de alta.
    """

    comprobar(
        "solo se exige credencial si consta que es nuevo",
        "if self.ya_instalado is None:" in VENTANA
    )

    comprobar(
        "ya no se exige con un 'not' que mete a ILEGIBLE dentro",
        "if not self.ya_instalado:\n\n            if not credencial:"
        not in VENTANA
    )

    comprobar(
        "el campo se enseña tambien cuando no se sabe",
        "if self.ya_instalado is None or self.ya_instalado == ILEGIBLE:"
        in VENTANA
    )


def prueba_la_ventana_lo_dice_con_esas_palabras():
    """
    Que el rotulo no afirme nada que no sepa, y que diga que hacer.
    """

    comprobar(
        "hay un rotulo propio para el caso ilegible",
        "if self.ya_instalado == ILEGIBLE:" in VENTANA
    )

    # El texto va partido en varias lineas del fuente, asi que se
    # busca el trozo tal y como esta escrito.
    comprobar(
        "dice que no se pudo comprobar, no que sea nuevo",
        "No se pudo comprobar si este equipo ya esta dado de " in VENTANA
    )

    comprobar(
        "y nombra el archivo de identidad como la causa",
        "hay un archivo de identidad y no se puede " in VENTANA
    )

    comprobar(
        "y explica como arreglarlo",
        "Ejecutar como administrador" in VENTANA
    )

    # El rotulo viejo sigue existiendo, pero solo para el caso en que
    # de verdad no hay identidad.
    comprobar(
        "el rotulo de 'todavia no esta dado de alta' sigue, para su caso",
        "Este equipo todavia no esta dado de alta" in VENTANA
    )


def principal():

    prueba_identidad_buena()
    prueba_sin_archivo_es_concluyente()
    prueba_sin_permiso_no_es_lo_mismo()
    prueba_un_json_roto_tampoco_es_nuevo()
    prueba_un_json_sin_device_id()
    prueba_la_ventana_no_exige_credencial_a_ciegas()
    prueba_la_ventana_lo_dice_con_esas_palabras()

    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  bien   %s" % nombre)
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
