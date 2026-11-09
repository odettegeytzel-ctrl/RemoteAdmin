"""
Que ordenes necesitan un escritorio y cuales no.

El servidor manda al Agent ordenes de texto ('get_processes',
'mouse_move:{...}'). Con el Agent partido en dos procesos hay que
saber a cual le toca cada una:

    BACKGROUND   las atiende el servicio, funcionan desde que
                 arranca Windows aunque no haya nadie con la
                 sesion iniciada

    INTERACTIVE  necesitan el escritorio del usuario, asi que las
                 reenvia el servicio al ayudante

La lista es BLANCA para lo interactivo: todo lo que no este
explicitamente marcado como de escritorio se queda en el fondo. Al
reves —una lista negra— una orden nueva se iria por defecto al
ayudante y dejaria de funcionar en un equipo sin sesion abierta, que
es justo el fallo que cuesta semanas en descubrirse.
"""


# Ordenes exactas que tocan el escritorio.
INTERACTIVE_EXACT = frozenset({
    # Transmision de pantalla en vivo
    "start_screen_stream",
    "stop_screen_stream",

    # Grabacion: el grabador captura la pantalla
    "start_recording",
    "stop_recording",

    # El aviso de mantenimiento es una ventana en el escritorio del
    # usuario. El servicio corre en la Sesion 0, donde no hay
    # escritorio: alli no se veria nada.
    "maintenance_off",

    # El portapapeles de la Sesion 0 es otro, y esta vacio: leer el
    # del usuario solo se puede desde su sesion.
    "clipboard_get",

    # BlockInput solo surte efecto sobre el escritorio interactivo.
    "input_unlock",
})


# Ordenes con argumento, por su prefijo ('mouse_move:{...}').
#
# Los nombres son los del protocolo que ya existe entre el servidor y
# el Agent; no se inventa ninguno. El teclado llega entero en
# 'keyboard:' (pulsar, soltar y combinaciones), no como una orden por
# tecla.
INTERACTIVE_PREFIXES = (
    "mouse_move:",
    "mouse_click:",
    "mouse_down:",
    "mouse_up:",
    "keyboard:",

    # maintenance_on:{"seconds": 600}
    "maintenance_on:",

    # clipboard_set:{"text": "..."}
    "clipboard_set:",

    # input_lock:{"seconds": 60}
    "input_lock:",
)


# Ordenes de fondo que conviene dejar escritas, aunque la regla por
# defecto ya las cubra: sirven de documentacion de lo que TIENE que
# seguir funcionando sin sesion iniciada, y las pruebas lo comprueban
# una por una.
BACKGROUND_EXPECTED = frozenset({
    "ping",
    "get_system_info",
    "get_installed_software",
    "get_recording_catalog",
})


BACKGROUND_PREFIXES_EXPECTED = (
    # Consultas de solo lectura: llevan un identificador de peticion
    "get_processes:",
    "get_services:",


    # Ver una grabacion que sigue en el equipo. Lee un archivo del
    # disco: no necesita escritorio, y debe funcionar aunque nadie
    # tenga la sesion iniciada.
    "recording_stream_start:",
    "recording_stream_abort:",

    # Apagar, reiniciar, bloquear, cerrar sesion. Ninguna necesita
    # escritorio: se piden por API de Windows, no tocando la pantalla.
    "power_action:",

    # Comprobar si hay version nueva, ahora. La cumple el SERVICIO,
    # que es quien se actualiza; el ayudante no tiene nada que ver.
    "check_update",

    # Archivado de grabaciones y ajustes. El grabador vive en el
    # ayudante, pero decidir el horario o la retencion no necesita
    # escritorio: son ajustes que el servicio guarda y transmite.
    "store_recording:",

    # Borrar una grabacion del disco del equipo. De fondo: es un
    # archivo, no una ventana, y tiene que funcionar aunque no haya
    # nadie con la sesion iniciada.
    "delete_recording:",
    "set_retention:",
    "set_schedule:",
    "set_continuous:",

    # Transferencia de archivos: escribe en disco, no en la pantalla
    "file_transfer_ready:",
    "file_download_ready:",
)


KIND_BACKGROUND = "background"
KIND_INTERACTIVE = "interactive"


def requires_desktop(mensaje):
    """
    True si la orden necesita el escritorio del usuario.

    Se mira la orden, no sus argumentos: 'mouse_move:{...}' se decide
    por el prefijo.
    """

    if not isinstance(mensaje, str):
        return False

    orden = mensaje.strip()

    if orden in INTERACTIVE_EXACT:
        return True

    return orden.startswith(INTERACTIVE_PREFIXES)


# Ajustes que el servicio recibe y el ayudante CUMPLE.
#
# No son de escritorio -guardar un horario no necesita pantalla- y
# por eso los atiende el servicio, que sobrevive a que el usuario
# cierre sesion. Pero quien tiene que cumplirlos es el ayudante,
# porque el grabador vive ahi: en la Sesion 0 no hay pantalla que
# capturar.
#
# Asi que el servicio se los queda Y se los pasa. Hacia falta
# decirlo aqui porque el ayudante vuelve a comprobar cada orden que
# le llega, y al no ser de escritorio las descartaba:
#
#     [ayudante] Orden descartada: no es de escritorio (set_schedule)
#
# Esa linea salio en los siete equipos durante tres dias. El
# servicio habia dejado de grabar -que era lo correcto- y el
# ayudante nunca recibia el horario, asi que no grababa NADIE.
AJUSTES_QUE_CUMPLE_EL_AYUDANTE = (
    "set_schedule:",
    "set_continuous:",
    "schedule_suppression:",
)


def el_ayudante_debe_atender(mensaje):
    """
    Si el ayudante tiene que ejecutar esta orden.

    Es mas amplio que requires_desktop: ademas de lo que necesita
    pantalla, incluye los ajustes de grabacion que el servicio le
    reenvia para que los cumpla.

    Se mantiene como una lista BLANCA, igual que la otra: lo que no
    este escrito aqui, el ayudante no lo ejecuta aunque se lo pidan
    por el canal.
    """

    if not isinstance(mensaje, str):
        return False

    orden = mensaje.strip()

    return (
        requires_desktop(orden)
        or orden.startswith(AJUSTES_QUE_CUMPLE_EL_AYUDANTE)
    )


def classify(mensaje):
    """Devuelve KIND_INTERACTIVE o KIND_BACKGROUND."""

    return KIND_INTERACTIVE if requires_desktop(mensaje) else KIND_BACKGROUND


def nombre_de_la_orden(mensaje):
    """
    Parte de la orden sin sus argumentos, para registrarla.

    Lo que va despues de ':' NO se devuelve: ahi hay coordenadas y
    texto tecleado, que no tienen por que acabar en un log.
    """

    if not isinstance(mensaje, str):
        return "desconocida"

    return mensaje.split(":", 1)[0].strip() or "desconocida"


# Respuesta cuando llega una orden de escritorio y no hay nadie con la
# sesion iniciada. Se contesta esto en vez de callar o de reintentar:
# el panel puede decirle a quien lo usa por que no pasa nada.
NO_INTERACTIVE_SESSION = "no_interactive_session"


def respuesta_sin_sesion(mensaje):
    """Lo que el servicio responde al servidor cuando no hay ayudante."""

    return {
        "type": NO_INTERACTIVE_SESSION,
        "command": nombre_de_la_orden(mensaje),
        "message": (
            "Esta accion necesita que haya un usuario con la sesion "
            "iniciada en el equipo."
        )
    }
