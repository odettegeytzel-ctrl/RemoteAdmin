r"""
El canal con el ayudante no se queda colgado.

    .venv\Scripts\python tests/test_canal_no_se_cuelga.py

El fallo
--------

El hilo que escucha al ayudante esperaba el siguiente mensaje con

    WaitForSingleObject(evento, INFINITE)

Cuando un envio fallaba desde OTRO hilo, el canal se marcaba como
caido y se llamaba a DisconnectNamedPipe. Eso NO despierta una
lectura pendiente, asi que el hilo lector se quedaba ahi para
siempre. A partir de ese momento:

  - el servicio creia que no habia ayudante, aunque estuviera vivo
    al otro lado del tubo;
  - nadie volvia a esperar_ayudante(), de modo que ningun ayudante
    nuevo podia conectarse NUNCA;
  - y pedir el arranque de la tarea no servia: el proceso ya
    estaba corriendo.

Lo unico que lo desatascaba era reiniciar el servicio, cosa que
ocurre una vez al dia con la comprobacion de version. En los
registros de un equipo real se veia clavado:

    Nov 03 12:55  Esperando al ayudante -> Ayudante conectado
    Nov 04 12:56  Esperando al ayudante -> Ayudante conectado
    Nov 05 12:58  Esperando al ayudante -> Ayudante conectado

Mismo pid de ayudante los tres dias. Se recuperaba a la misma hora
y se rompia otra vez despues.

Por que no se cancela la lectura
--------------------------------

Seria lo natural, y no se puede: CancelIoEx no esta en esta version
de pywin32, y CancelIo solo alcanza a lo que pidio el MISMO hilo
-aqui quien envia y quien lee son distintos-. Se comprobo antes de
escribir el arreglo, porque la primera version lo usaba y no habria
hecho nada.

Asi que la espera deja de ser infinita: despierta cada segundo y
mira si el canal sigue en pie.
"""

import io
import os
import sys
import threading
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

IPC = io.open(RAIZ / "agent" / "ipc.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


# ==================================================================
# 1. LA ESPERA SE ENTERA DE QUE EL CANAL SE SOLTO
# ==================================================================

def test_una_lectura_que_espera_se_suelta_al_desconectar():
    """
    El corazon del arreglo, probado de verdad: un hilo esperando un
    mensaje y otro soltando el canal, como pasa en produccion.

    Se usa un evento que NUNCA se señala, que es exactamente la
    situacion real: un ayudante vivo que no manda nada.
    """

    try:
        import win32event
        import ipc

    except ImportError as falta:
        comprobar("(sin pywin32: no se puede probar aqui) %s" % falta, True)
        return

    # Un evento que no se señala nunca: nadie escribe en el tubo.
    evento = win32event.CreateEvent(None, True, False, None)

    class OverlappedFalso:
        hEvent = evento

    conectado = {"si": True}

    salio = {"con": None, "cuando": None}

    def esperar():

        comenzado = time.monotonic()

        try:
            # Se llama al bucle de espera tal cual lo hace el
            # producto, con la bandera del canal.
            while True:

                estado = win32event.WaitForSingleObject(
                    OverlappedFalso.hEvent, ipc.LATIDO_DE_ESPERA_MS
                )

                if estado != win32event.WAIT_TIMEOUT:
                    break

                if not conectado["si"]:
                    raise ipc.IPCError("El canal se solto")

        except ipc.IPCError as problema:
            salio["con"] = str(problema)

        salio["cuando"] = time.monotonic() - comenzado

    hilo = threading.Thread(target=esperar, daemon=True)
    hilo.start()

    time.sleep(0.3)

    comprobar("Mientras el canal esta en pie, sigue esperando",
              hilo.is_alive())

    # Otro hilo suelta el canal, como hace enviar() al fallar
    conectado["si"] = False

    hilo.join(timeout=5)

    comprobar("Al soltarse el canal, la espera termina",
              not hilo.is_alive(),
              "sigue colgada")

    comprobar("Y dice por que",
              salio["con"] is not None, str(salio["con"]))

    # Tiene que notarlo en el siguiente latido, no al cabo de un
    # rato largo: un escritorio que tarda un minuto en volver es un
    # escritorio que el tecnico ya dio por perdido.
    comprobar("En poco mas de un latido",
              salio["cuando"] is not None and salio["cuando"] < 3,
              "tardo %.1fs" % (salio["cuando"] or -1))

    try:
        import win32api
        win32api.CloseHandle(evento)
    except Exception:
        pass


# ==================================================================
# 2. QUE NO VUELVA LA ESPERA INFINITA
# ==================================================================

def test_ya_no_se_espera_para_siempre():

    cuerpo = IPC.split("def _leer_sin_prisa(", 1)[1][:3000]

    # En la LECTURA, que es donde colgaba. Queda otra espera
    # infinita en esperar_ayudante y esa es correcta: aguardar a que
    # se conecte alguien no tiene nada que agotar, y estar parado
    # ahi es justo lo que el servicio debe hacer cuando no hay nadie.
    comprobar("La lectura ya no espera para siempre",
              "win32event.INFINITE" not in cuerpo,
              "vuelve a haber una espera sin limite al leer")

    comprobar("Se espera a ratos",
              "LATIDO_DE_ESPERA_MS" in IPC)

    comprobar("Y entre rato y rato se mira si el canal sigue",
              "if sigue_conectado is not None and not sigue_conectado():"
              in cuerpo)

    comprobar("El latido es corto",
              "LATIDO_DE_ESPERA_MS = 1000" in IPC)


def test_el_servicio_pasa_su_bandera():
    """
    Sin esto el arreglo no sirve de nada: la espera sabria mirar,
    pero no tendria que mirar.
    """

    comprobar("recibir() pasa la bandera del canal",
              "lambda: self._conectado" in IPC)

    recibir = IPC.split("def recibir(self):", 1)[1][:900]

    comprobar("Y es el lado del SERVICIO, que es donde colgaba",
              "_recibir_mensaje(" in recibir)


def test_desconectar_explica_lo_que_hace():
    """
    La bandera no es cosmetica: es lo que despierta al lector. Y eso
    tiene que quedar escrito donde se pone, o alguien la quitara por
    parecer redundante.
    """

    cuerpo = IPC.split("def desconectar(self):", 1)[1][:2600]

    comprobar("Se explica por que no se cancela la lectura",
              "CancelIoEx" in cuerpo and "CancelIo" in cuerpo)

    comprobar("Y que la bandera es lo que despierta al lector",
              "_conectado" in cuerpo and "_leer_sin_prisa" in cuerpo)


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("EL CANAL CON EL AYUDANTE NO SE CUELGA")
    print("=" * 66)
    print()

    pruebas = [
        test_una_lectura_que_espera_se_suelta_al_desconectar,
        test_ya_no_se_espera_para_siempre,
        test_el_servicio_pasa_su_bandera,
        test_desconectar_explica_lo_que_hace,
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
