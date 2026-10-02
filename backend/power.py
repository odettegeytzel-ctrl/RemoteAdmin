"""
Acciones de energia y sesion sobre un equipo remoto (G5).

Cuatro, y solo cuatro: bloquear, cerrar sesion, reiniciar y apagar.

La lista es cerrada a proposito. No hay ningun parametro por el que pueda
viajar un comando, un script ni un argumento: lo unico que llega del
navegador es cual de las cuatro, y cualquier otra cosa se rechaza antes de
tocar el WebSocket. El Agent recibe un nombre de accion conocido y ejecuta
una funcion fija de Windows, nunca una cadena que alguien haya escrito.

Este modulo decide QUE se puede pedir y protege de las repeticiones. Quien
puede pedirlo lo decide el sistema de permisos, y el transporte es el
WebSocket de siempre.
"""

import time


ACTION_LOCK = "lock"
ACTION_LOGOFF = "logoff"
ACTION_RESTART = "restart"
ACTION_SHUTDOWN = "shutdown"


# Ventana durante la cual no se repite una accion destructiva sobre el
# mismo equipo. Treinta segundos cubren de sobra un doble clic, un
# navegador que reenvia y dos operadores actuando a la vez, sin estorbar a
# quien de verdad quiera reiniciar dos veces seguidas.
REPEAT_WINDOW_SECONDS = 30

# Espera maxima por la confirmacion del Agent. Mas corta que la de las
# consultas: aqui el equipo puede desaparecer justo despues de aceptar la
# orden, y dejar la peticion colgada no aporta nada.
ACK_TIMEOUT_SECONDS = 10


ACTIONS = {

    ACTION_LOCK: {
        "permission": "device.lock",
        "audit": "device.lock",
        "label": "Bloquear equipo",
        # Bloquear es reversible: quien este delante desbloquea y sigue.
        "destructive": False
    },

    ACTION_LOGOFF: {
        "permission": "device.logoff",
        "audit": "device.logoff",
        "label": "Cerrar sesion",
        # Cierra programas abiertos: puede perderse trabajo sin guardar.
        "destructive": True
    },

    ACTION_RESTART: {
        "permission": "device.restart",
        "audit": "device.restart",
        "label": "Reiniciar equipo",
        "destructive": True
    },

    ACTION_SHUTDOWN: {
        "permission": "device.shutdown",
        "audit": "device.shutdown",
        "label": "Apagar equipo",
        "destructive": True
    }
}


class PowerError(ValueError):
    """Peticion de accion rechazada, con un motivo que se puede ensenar."""


def get_action(nombre):
    """
    Devuelve la definicion de una accion conocida.

    La coincidencia es EXACTA: ni mayusculas distintas ni espacios
    sobrantes. Normalizar aqui seria aceptar "LOCK " o " Shutdown" como
    si fueran lo mismo, y con cuatro nombres fijos que el panel escribe
    tal cual no hay ninguna razon para ser flexible. Cuanto mas estrecha
    es la puerta, menos hay que vigilarla.

    Lanza si no esta en la lista: no existe ningun camino por el que un
    nombre inventado llegue al Agent.
    """

    if not isinstance(nombre, str) or nombre not in ACTIONS:
        raise PowerError("Accion no permitida")

    return ACTIONS[nombre]


def is_destructive(nombre):

    try:
        return get_action(nombre)["destructive"]

    except PowerError:
        return False


# ==============================
# PROTECCION CONTRA REPETICION
# ==============================
#
# (device_id, accion) -> momento en que se acepto la ultima.
#
# Vive en memoria a proposito: protege de un doble clic y de reenvios,
# que ocurren en segundos. Tras un reinicio del servidor la ventana se
# pierde, y es lo correcto: si el servidor se ha reiniciado, la orden
# anterior ya no esta en vuelo.
_recent_actions = {}


def seconds_until_repeat_allowed(device_id, action, ahora=None):
    """
    Segundos que faltan para poder repetir esta accion. 0 si se puede ya.

    Solo se protegen las destructivas: bloquear dos veces no hace dano.
    """

    if not is_destructive(action):
        return 0

    momento = ahora if ahora is not None else time.time()

    anterior = _recent_actions.get((device_id, action))

    if anterior is None:
        return 0

    transcurrido = momento - anterior

    if transcurrido >= REPEAT_WINDOW_SECONDS:
        return 0

    return max(1, int(REPEAT_WINDOW_SECONDS - transcurrido))


def register_action(device_id, action, ahora=None):
    """Anota que la accion se acepto, para no repetirla por accidente."""

    if not is_destructive(action):
        return

    momento = ahora if ahora is not None else time.time()

    _recent_actions[(device_id, action)] = momento

    # Limpieza oportunista de las que ya salieron de la ventana
    for clave, cuando in list(_recent_actions.items()):
        if momento - cuando >= REPEAT_WINDOW_SECONDS:
            _recent_actions.pop(clave, None)


def forget_action(device_id, action):
    """
    Olvida la anotacion de una accion que al final no salio.

    Si la orden no llego a enviarse —el equipo estaba desconectado, el
    envio fallo— no tiene sentido bloquear el siguiente intento.
    """

    _recent_actions.pop((device_id, action), None)


def reset_repeat_guard():
    """Vacia la proteccion. Solo para pruebas."""

    _recent_actions.clear()


# ==============================
# NORMALIZACION DE LA RESPUESTA
# ==============================

def normalize_result(payload, action=None):
    """
    Deja la respuesta del Agent en una forma conocida.

    Para reiniciar y apagar, lo unico que el Agent puede confirmar es que
    ACEPTO la orden: cuando la ejecuta, se va. Decir 'confirmado' seria
    afirmar algo que nadie ha comprobado, asi que se distingue:

      executed -> hecho y comprobado (bloquear, cerrar sesion)
      accepted -> aceptado, pendiente de ejecutarse (reiniciar, apagar)
    """

    if not isinstance(payload, dict):
        return {"error": "Respuesta no valida del equipo"}

    if payload.get("error"):
        return {"error": str(payload["error"])[:200]}

    aceptada = bool(payload.get("accepted"))

    if not aceptada:
        return {"error": "El equipo no acepto la accion"}

    return {
        "accepted": True,
        "executed": bool(payload.get("executed")),
        "pending": bool(payload.get("pending")),
        "detail": str(payload.get("detail") or "")[:200]
    }


def describe_outcome(resultado, action):
    """Frase para el panel y para la auditoria. Nunca promete de mas."""

    definicion = ACTIONS.get(action, {})
    etiqueta = definicion.get("label", action)

    if resultado.get("executed"):
        return f"{etiqueta}: realizada en el equipo"

    if resultado.get("pending"):
        return (
            f"{etiqueta}: orden aceptada por el equipo. "
            "No puede confirmarse su final, porque el equipo se desconecta "
            "al ejecutarla."
        )

    return f"{etiqueta}: orden aceptada"
