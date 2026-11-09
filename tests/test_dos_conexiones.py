r"""
Una conexion vieja que se muere no borra a la nueva que esta viva.

    .venv\Scripts\python tests/test_dos_conexiones.py

El registro de agentes va por device_id:

    connected_agents[device_id] = websocket

Asi que una reconexion pisa la entrada anterior. Al limpiar, el
servidor hacia:

    connected_agents.pop(device_id, None)

sin mirar QUE conexion se estaba muriendo. Con dos conexiones del
mismo equipo solapadas, la vieja se lleva por delante a la nueva:

    22:29:57  conexion A    connected_agents[D] = A
    22:30:37  conexion B    connected_agents[D] = B   (pisa a A)
    22:30:57  muere A       pop(D)  ->  se lleva a B

El equipo queda con su WebSocket abierto y el servidor sin saberlo.

Y lo peor es que no se nota. El latido va por HTTP y sigue llegando,
asi que el panel enseña el equipo EN LINEA mientras no funciona nada
de lo que necesita el canal: pantalla, control remoto, portapapeles,
horario, actualizacion. Un equipo estuvo media hora asi antes de que
alguien se diera cuenta de que no se veia su pantalla.

Pasa justo cuando la red va mal -que es cuando hay reconexiones- y
sera mas frecuente cuantos mas equipos haya.

La prueba no simula el WebSocket: usa el registro de verdad y
objetos distintos, que es lo unico que importa aqui. Lo que se
comprueba es la IDENTIDAD del socket, no su comportamiento.
"""

import io
import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))

os.environ.setdefault(
    "AUTH_SECRET_KEY", "clave-solo-para-conexiones-" + "x" * 20
)

from backend import database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="conexiones-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

from backend import main as servidor  # noqa: E402


SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()


EQUIPO = "6e6e4f515a4ccb69"


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


class Conexion:
    """Un socket cualquiera. Solo importa que sea otro objeto."""

    def __init__(self, nombre):
        self.nombre = nombre

    def __repr__(self):
        return "<conexion %s>" % self.nombre


def limpiar():
    servidor.connected_agents.pop(EQUIPO, None)


def prueba_la_conexion_registrada_es_vigente():

    limpiar()

    a = Conexion("A")

    servidor.connected_agents[EQUIPO] = a

    comprobar("la conexion registrada es vigente",
              servidor.conexion_vigente(EQUIPO, a))


def prueba_la_vieja_deja_de_ser_vigente():
    """
    El caso exacto del fallo: A se registra, B la pisa, y entonces A
    se muere. A ya no es la buena y no debe limpiar nada.
    """

    limpiar()

    a = Conexion("A")
    b = Conexion("B")

    servidor.connected_agents[EQUIPO] = a
    servidor.connected_agents[EQUIPO] = b

    comprobar("tras reconectar, la nueva es la vigente",
              servidor.conexion_vigente(EQUIPO, b))

    comprobar("y la vieja ya NO lo es",
              not servidor.conexion_vigente(EQUIPO, a))

    comprobar("el registro sigue apuntando a la nueva",
              servidor.connected_agents.get(EQUIPO) is b,
              servidor.connected_agents.get(EQUIPO))


def prueba_sin_device_id_nunca_es_vigente():
    """
    Una conexion que nunca llego a identificarse no limpia nada de
    nadie.
    """

    limpiar()

    comprobar("sin device_id no es vigente",
              not servidor.conexion_vigente(None, Conexion("X")))

    comprobar("con device_id vacio tampoco",
              not servidor.conexion_vigente("", Conexion("X")))


def prueba_un_equipo_que_no_esta_no_es_vigente():

    limpiar()

    comprobar("un equipo sin conexion registrada no es vigente",
              not servidor.conexion_vigente(EQUIPO, Conexion("A")))


def prueba_no_confunde_equipos_distintos():

    limpiar()

    a = Conexion("A")

    servidor.connected_agents[EQUIPO] = a

    try:
        comprobar("la conexion de un equipo no vale para otro",
                  not servidor.conexion_vigente("otroequipo", a))

    finally:
        limpiar()
        servidor.connected_agents.pop("otroequipo", None)


def prueba_el_servidor_usa_la_comprobacion_antes_de_limpiar():
    """
    Que la funcion exista no sirve de nada si la limpieza no la mira.
    """

    comprobar(
        "la desconexion comprueba que la conexion sea la vigente",
        "if device_id and not conexion_vigente(device_id, websocket):"
        in SERVIDOR
    )

    # Y que al descartarla se corte de verdad: poner device_id a None
    # es lo que impide que corra toda la limpieza de abajo.
    trozo = SERVIDOR.split("except WebSocketDisconnect:", 1)[1]
    trozo = trozo.split("@app.post", 1)[0]

    comprobar(
        "una conexion vieja no sigue adelante con la limpieza",
        "device_id = None" in trozo
    )

    comprobar(
        "y se dice en el registro que era una conexion vieja",
        "conexion vieja" in trozo
    )

    # El pop por device_id sigue existiendo, pero ya solo se alcanza
    # cuando la conexion es la vigente.
    comprobar(
        "la limpieza sigue siendo por device_id, ya protegida",
        "connected_agents.pop(" in trozo
    )


def principal():

    prueba_la_conexion_registrada_es_vigente()
    prueba_la_vieja_deja_de_ser_vigente()
    prueba_sin_device_id_nunca_es_vigente()
    prueba_un_equipo_que_no_esta_no_es_vigente()
    prueba_no_confunde_equipos_distintos()
    prueba_el_servidor_usa_la_comprobacion_antes_de_limpiar()

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
