r"""
Borrar una grabacion de verdad.

    .venv\Scripts\python tests/test_borrar_grabaciones.py

El error que lo hizo necesario
------------------------------

No habia forma de borrar una grabacion. Se hizo a mano, en la base:

    20:33   DELETE ... 58 filas        ->  565 grabaciones
    20:44   [grabaciones] 58 fichas nuevas de c6c837c1e5cd5041
            ->  624 grabaciones

Once minutos. Las 58 volvieron enteras.

La causa: el Agent manda su catalogo de grabaciones locales cada vez
que conecta, y el servidor crea una ficha por cada archivo que
encuentra en el equipo. El catalogo no es un informe, es la FUENTE
DE VERDAD de lo que hay en esa maquina. Mientras el archivo exista,
la ficha vuelve.

Asi que borrar empieza en el equipo y termina en la base, nunca al
reves. Eso es lo que vigila sobre todo este archivo.

Lo que tambien se protege
-------------------------

  - una grabacion marcada como Conservar no se borra, este vacia o
    no: la marca es una decision de una persona;

  - si el equipo esta desconectado NO se borra la ficha. Dejarla es
    lo correcto: el archivo sigue ahi y la ficha dice la verdad;

  - el Agent solo borra dentro de su carpeta de grabaciones, y solo
    por nombre: el servidor nunca le manda una ruta.
"""

import io
import os
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-borrado")

SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()
AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
GRABACIONES = io.open(
    RAIZ / "backend" / "recordings.py", encoding="utf-8"
).read()
JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


# ==================================================================
# 1. EL ORDEN
# ==================================================================

def test_primero_el_archivo_y_despues_la_ficha():
    """
    Lo unico que de verdad importa de todo el archivo.
    """

    cuerpo = SERVIDOR.split("async def _borrar_grabacion(", 1)[1][:6000]

    comprobar("Se pide al equipo que borre el archivo",
              '"delete_recording:" + json.dumps(' in cuerpo)

    comprobar("Y se ESPERA su respuesta",
              "await asyncio.wait_for(future, DELETE_TIMEOUT_SECONDS)"
              in cuerpo)

    comprobar("La ficha se borra DESPUES del archivo",
              cuerpo.index("delete_recording:")
              < cuerpo.index("recordings_mod.borrar_ficha"),
              "se borra la ficha antes de que el archivo desaparezca")

    # Y si el equipo dice que no pudo, la ficha se queda: una ficha
    # sin archivo seria mentira, pero un archivo sin ficha es peor,
    # porque nadie sabe que existe.
    comprobar("Si el equipo no pudo, la ficha NO se toca",
              cuerpo.index('respuesta.get("ok")')
              < cuerpo.index("recordings_mod.borrar_ficha"))


def test_un_equipo_desconectado_no_pierde_su_ficha():
    """
    Tentador borrarla igual y "ya se arreglara". No: el archivo
    sigue en el disco del equipo, asi que la ficha dice la verdad.
    Borrarla solo serviria para que volviera al reconectarse, que es
    exactamente el error que se esta corrigiendo.
    """

    cuerpo = SERVIDOR.split("async def _borrar_grabacion(", 1)[1][:6000]

    comprobar("Se comprueba que el equipo este conectado",
              "if not websocket:" in cuerpo)

    comprobar("Y se explica por que no se borra la ficha",
              "volvería a aparecer al reconectarse" in cuerpo)

    comprobar("Con un codigo que el panel puede mirar",
              '"code": "equipo_desconectado"' in cuerpo)


def test_la_ficha_se_borra_en_un_solo_sitio():
    """
    Con el comentario que explica el orden, para que nadie la llame
    desde otro lado sin saber lo que pasa.
    """

    comprobar("Hay una funcion para quitar la ficha",
              "def borrar_ficha(" in GRABACIONES)

    cuerpo = GRABACIONES.split("def borrar_ficha(", 1)[1][:1400]

    # El aviso va partido en dos lineas en el fuente, asi que se
    # busca el trozo que cabe entero en una.
    comprobar("Y avisa de que el archivo va primero",
              "solo debe llamarse DESPUES" in cuerpo
              and "archivo haya desaparecido del equipo" in cuerpo)

    comprobar("Contando lo que paso, para que no se repita",
              "58 fichas" in cuerpo)


# ==================================================================
# 2. LO QUE NO SE BORRA
# ==================================================================

def test_una_marcada_para_conservar_no_se_borra():

    cuerpo = SERVIDOR.split("async def _borrar_grabacion(", 1)[1][:6000]

    comprobar("Se mira la marca",
              'grabacion.get("keep")' in cuerpo)

    comprobar("Y se niega el borrado",
              '"code": "marcada_para_conservar"' in cuerpo)

    comprobar("Diciendo como desbloquearlo",
              "Quita la marca antes de borrarla" in cuerpo)

    # La limpieza masiva tampoco las toca, y las cuenta aparte para
    # que quien la lanza sepa que ha quedado algo.
    limpieza = SERVIDOR.split(
        "async def limpiar_grabaciones_vacias(", 1
    )[1][:3000]

    comprobar("La limpieza masiva tambien las respeta",
              'grabacion.get("keep")' in limpieza
              and "conservadas += 1" in limpieza)


def test_la_limpieza_solo_toca_las_vacias():

    limpieza = SERVIDOR.split(
        "async def limpiar_grabaciones_vacias(", 1
    )[1][:3000]

    comprobar("Se filtra por tamano",
              "TAMANO_MINIMO_DE_UN_VIDEO" in limpieza)

    comprobar("Y una grabacion con contenido se salta",
              ">= TAMANO_MINIMO_DE_UN_VIDEO" in limpieza
              and "continue" in limpieza)

    comprobar("El umbral es el mismo que usa el panel",
              "TAMANO_MINIMO_DE_UN_VIDEO = 10 * 1024" in SERVIDOR
              and "TAMANO_MINIMO_DE_UN_VIDEO = 10 * 1024" in JS)

    comprobar("No se cruza de empresa",
              "get_device_organization(grabacion" in limpieza)

    comprobar("Hace falta poder administrar grabaciones",
              'require_permission(request, "recordings.manage")' in limpieza)


def test_se_cuenta_lo_que_NO_se_pudo_borrar():
    """
    Una limpieza que dice "listo" y deja la mitad sin tocar se
    descubre tarde y mal.
    """

    limpieza = SERVIDOR.split(
        "async def limpiar_grabaciones_vacias(", 1
    )[1][:3000]

    for campo in ("borradas", "conservadas", "sin_conexion", "fallidas"):
        comprobar("Se informa '%s'" % campo,
                  '"%s":' % campo in limpieza)

    comprobar("Y el panel lo ensena, no solo lo borrado",
              "datos.sin_conexion" in JS and "datos.conservadas" in JS)


# ==================================================================
# 3. EL EQUIPO
# ==================================================================

def test_el_agent_solo_borra_lo_suyo():

    comprobar("Existe el borrado en el Agent",
              "def borrar_grabacion_local(" in AGENTE)

    cuerpo = AGENTE.split("def borrar_grabacion_local(", 1)[1][:1800]

    comprobar("Busca dentro de su carpeta administrada",
              "find_managed_recording(filename)" in cuerpo)

    # find_managed_recording compara por NOMBRE dentro de la carpeta
    # y pasa por is_managed_recording: no hay '..' ni ruta absoluta
    # que lleve a otro sitio.
    comprobar("Nunca recibe una ruta, solo un nombre",
              '"filename": nombre' in SERVIDOR)

    comprobar("Un archivo que ya no esta no es un error",
              '"ya_no_estaba": True' in cuerpo)

    comprobar("Y un fallo del disco se informa, no revienta",
              "except OSError as problema:" in cuerpo
              and '"ok": False' in cuerpo)


def test_un_agent_antiguo_no_da_un_error_confuso():
    """
    Un Agent anterior ignora la orden. Sin comprobarlo habria que
    esperar a que venza el plazo para decir "no respondio", que
    suena a averia y no lo es.
    """

    cuerpo = SERVIDOR.split("async def _borrar_grabacion(", 1)[1][:6000]

    comprobar("Se comprueba la capacidad antes de pedirlo",
              'agent_supports(device_id, "delete_recording")' in cuerpo)

    comprobar("Con su propio codigo",
              '"code": "agent_sin_borrado"' in cuerpo)

    comprobar("Y el Agent la declara",
              '"delete_recording"' in AGENTE)


def test_la_orden_funciona_sin_nadie_con_la_sesion_iniciada():
    """
    Es un archivo, no una ventana. Y el caso en el que mas falta
    hace -limpiar un kiosco- es justo donde no hay nadie dentro.
    """

    import commands

    comprobar("Borrar no necesita escritorio",
              not commands.requires_desktop(
                  'delete_recording:{"filename": "rec_1.mp4"}'
              ))

    ORDENES = io.open(
        RAIZ / "agent" / "commands.py", encoding="utf-8"
    ).read()

    comprobar("Y esta escrito entre las de fondo",
              '"delete_recording:"' in ORDENES)


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("BORRAR UNA GRABACION DE VERDAD")
    print("=" * 66)
    print()

    pruebas = [
        test_primero_el_archivo_y_despues_la_ficha,
        test_un_equipo_desconectado_no_pierde_su_ficha,
        test_la_ficha_se_borra_en_un_solo_sitio,
        test_una_marcada_para_conservar_no_se_borra,
        test_la_limpieza_solo_toca_las_vacias,
        test_se_cuenta_lo_que_NO_se_pudo_borrar,
        test_el_agent_solo_borra_lo_suyo,
        test_un_agent_antiguo_no_da_un_error_confuso,
        test_la_orden_funciona_sin_nadie_con_la_sesion_iniciada,
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
