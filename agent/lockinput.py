"""
Bloqueo del teclado y el raton FISICOS del equipo administrado.

Lo pide el tecnico desde el panel, con un boton, y solo el puede
quitarlo. Quien esta delante del equipo no: ese es justamente el
punto cuando hay que trabajar sin que nadie mueva nada.

LO QUE NO BLOQUEA

Ctrl+Alt+Supr. Windows se la reserva y ningun programa puede
quitarsela, ni este ni TeamViewer. Es deliberado del sistema: tiene
que existir una combinacion que siempre llegue al sistema y nunca a
una aplicacion, porque si no, cualquier programa podria imitar la
pantalla de inicio de sesion y robar la contraseña.

EL PLAZO, QUE ES LO IMPORTANTE

El bloqueo se suelta SOLO al cumplirse un plazo. No es una comodidad:
si se cae la red, el Agent, el servidor o el navegador del tecnico
con el bloqueo puesto, la persona que esta delante se queda sin
teclado y sin raton, sin nadie que pueda devolverselos y sin poder
ni apagar el equipo con el teclado.

Mientras el tecnico sigue ahi, el panel renueva el plazo y el bloqueo
no se cae. En cuanto deja de renovarlo -porque cerro, porque se
desconecto, porque se fue la luz en su casa-, el equipo se libera
solo.

El vigilante corre en su propio hilo, en vez de confiar en que
alguien mande 'desbloquear': un hilo que ya esta corriendo no
depende de que llegue ningun mensaje.
"""

import ctypes
import threading
import time


# Cuanto dura el bloqueo sin que nadie lo renueve. El panel renueva
# cada pocos segundos, asi que este plazo solo se agota cuando el
# tecnico ya no esta.
PLAZO_POR_DEFECTO = 60

# Tope duro. Aunque el panel pida mas, no se concede: un bloqueo de
# horas sin vigilancia es la situacion que este modulo existe para
# evitar.
PLAZO_MAXIMO = 300

LATIDO = 0.5


_candado = threading.Lock()
_hasta = 0.0
_vigilante = None


def activo():
    """True si el equipo esta bloqueado ahora mismo."""

    with _candado:
        return _hasta > time.monotonic()


def _bloquear(valor):
    """BlockInput de Windows. True si Windows lo acepto."""

    try:
        return bool(ctypes.windll.user32.BlockInput(bool(valor)))

    except Exception:
        return False


def _vigilar():
    """
    Suelta el bloqueo en cuanto vence el plazo.

    Comprueba cada medio segundo en vez de dormir hasta la hora
    exacta: asi una renovacion no tiene que despertar a nadie, solo
    mover la hora.
    """

    global _vigilante

    while True:

        time.sleep(LATIDO)

        with _candado:

            if time.monotonic() < _hasta:
                continue

            # Vencido. Se suelta DENTRO del candado para que no se
            # cruce con una renovacion que llegue en este instante.
            _bloquear(False)
            _vigilante = None

            return


def activar(segundos=None):
    """
    Bloquea, o renueva el plazo si ya estaba bloqueado.

    Renovar no vuelve a llamar a BlockInput: ya esta puesto, y
    repetirlo no aporta nada. Solo mueve la hora de vencimiento.

    Devuelve los segundos concedidos, que pueden ser menos de los
    pedidos si se paso del tope.
    """

    global _vigilante

    try:
        plazo = int(segundos) if segundos else PLAZO_POR_DEFECTO

    except (TypeError, ValueError):
        plazo = PLAZO_POR_DEFECTO

    plazo = max(1, min(plazo, PLAZO_MAXIMO))

    with _candado:

        ya_estaba = _hasta > time.monotonic()

        if not ya_estaba and not _bloquear(True):
            # Windows lo rechaza si el proceso no corre en el
            # escritorio interactivo. Decirlo es mejor que dejar al
            # panel creyendo que el equipo esta bloqueado.
            return 0

        globals()["_hasta"] = time.monotonic() + plazo

        if _vigilante is None or not _vigilante.is_alive():
            _vigilante = threading.Thread(
                target=_vigilar, name="vigilante-bloqueo", daemon=True
            )
            _vigilante.start()

    return plazo


def desactivar():
    """Suelta el bloqueo ya, sin esperar al plazo."""

    with _candado:
        globals()["_hasta"] = 0.0
        return _bloquear(False)
