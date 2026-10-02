"""
Ejecucion de las acciones de energia y sesion en Windows.

Se usan las funciones nativas de Windows a traves de ctypes, que ya es
parte de la biblioteca estandar y el Agent ya importa. No se construye
ningun comando de texto ni se abre ninguna consola: no hay cadena que
alguien pueda manipular, porque no hay cadena.

Las cuatro acciones son funciones separadas sin parametros. No existe una
funcion generica que reciba "que ejecutar".

Reiniciar y apagar necesitan el privilegio SeShutdownPrivilege, que el
proceso tiene pero llega desactivado; hay que habilitarlo antes de pedir
nada.
"""

import ctypes
import threading
import time


# Banderas de ExitWindowsEx (winuser.h)
EWX_LOGOFF = 0x00000000
EWX_SHUTDOWN = 0x00000001
EWX_REBOOT = 0x00000002
EWX_POWEROFF = 0x00000008

# FORCEIFHUNG cierra las aplicaciones que no respondan, pero deja que las
# que si responden guarden su trabajo. EWX_FORCE a secas mata todo sin
# preguntar y hace perder lo que no estuviera guardado: no se usa.
EWX_FORCEIFHUNG = 0x00000010

# Motivo que queda en el registro de eventos de Windows:
# aplicacion planificada, mantenimiento.
SHTDN_REASON_MAJOR_APPLICATION = 0x00040000
SHTDN_REASON_MINOR_MAINTENANCE = 0x00000001
SHTDN_REASON_FLAG_PLANNED = 0x80000000

REASON = (
    SHTDN_REASON_MAJOR_APPLICATION
    | SHTDN_REASON_MINOR_MAINTENANCE
    | SHTDN_REASON_FLAG_PLANNED
)

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002
SE_SHUTDOWN_NAME = "SeShutdownPrivilege"

# Margen entre aceptar la orden y ejecutarla. Da tiempo a que la
# confirmacion llegue al servidor por el WebSocket antes de que el equipo
# empiece a apagarse y la conexion se corte a media frase.
EXECUTION_DELAY_SECONDS = 2


class PowerActionError(Exception):
    """No se pudo realizar la accion en este equipo."""


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_ulong),
                ("HighPart", ctypes.c_long)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID),
                ("Attributes", ctypes.c_ulong)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", ctypes.c_ulong),
                ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


def _enable_shutdown_privilege():
    """
    Habilita SeShutdownPrivilege en el proceso actual.

    El Agent corre elevado, asi que tiene el privilegio, pero Windows lo
    entrega desactivado: sin habilitarlo, ExitWindowsEx falla con
    ERROR_ACCESS_DENIED y no se entiende por que.
    """

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    token = ctypes.c_void_p()

    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
        ctypes.byref(token)
    ):
        raise PowerActionError(
            "No se pudo abrir el token del proceso "
            f"(codigo {ctypes.get_last_error()})"
        )

    try:

        luid = _LUID()

        if not advapi32.LookupPrivilegeValueW(
            None, SE_SHUTDOWN_NAME, ctypes.byref(luid)
        ):
            raise PowerActionError(
                "No se encontro el privilegio de apagado "
                f"(codigo {ctypes.get_last_error()})"
            )

        privilegios = _TOKEN_PRIVILEGES()
        privilegios.PrivilegeCount = 1
        privilegios.Privileges[0].Luid = luid
        privilegios.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED

        advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(privilegios), 0, None, None
        )

        codigo = ctypes.get_last_error()

        # AdjustTokenPrivileges devuelve exito aunque no haya podido
        # asignar el privilegio; hay que mirar el codigo de error.
        if codigo != 0:
            raise PowerActionError(
                "No se pudo habilitar el privilegio de apagado "
                f"(codigo {codigo}). El Agent debe ejecutarse con permisos "
                "de administrador."
            )

    finally:
        kernel32.CloseHandle(token)


def _exit_windows(banderas, descripcion):
    """Llama a ExitWindowsEx con unas banderas fijas."""

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    if not user32.ExitWindowsEx(ctypes.c_uint(banderas),
                                ctypes.c_uint(REASON)):

        raise PowerActionError(
            f"Windows rechazo la orden de {descripcion} "
            f"(codigo {ctypes.get_last_error()})"
        )

    return True


# ==============================
# LAS CUATRO ACCIONES
# ==============================

def lock():
    """
    Bloquea la sesion interactiva.

    Es inmediata y reversible: no cierra nada, solo pide la contrasena.
    """

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    if not user32.LockWorkStation():
        raise PowerActionError(
            "No se pudo bloquear la sesion "
            f"(codigo {ctypes.get_last_error()})"
        )

    return True


def logoff():
    """
    Cierra la sesion interactiva de este equipo.

    Afecta solo a la sesion en la que corre el Agent; no toca otras
    sesiones ni otros equipos.
    """

    return _exit_windows(EWX_LOGOFF | EWX_FORCEIFHUNG, "cerrar sesion")


def restart():
    """Reinicia el equipo."""

    _enable_shutdown_privilege()

    return _exit_windows(EWX_REBOOT | EWX_FORCEIFHUNG, "reiniciar")


def shutdown():
    """Apaga el equipo."""

    _enable_shutdown_privilege()

    return _exit_windows(
        EWX_SHUTDOWN | EWX_POWEROFF | EWX_FORCEIFHUNG, "apagar"
    )


# Las cuatro, y ninguna mas. Se busca por nombre en este diccionario: no
# hay forma de llegar a otra funcion desde fuera.
HANDLERS = {
    "lock": lock,
    "logoff": logoff,
    "restart": restart,
    "shutdown": shutdown
}

# Las que hacen desaparecer al Agent. Se confirman ANTES de ejecutarse,
# porque despues ya no hay quien conteste.
DEFERRED = {"logoff", "restart", "shutdown"}


def execute(action, delay=None):
    """
    Realiza una accion y devuelve que ha pasado.

    Las acciones que desconectan al equipo se programan con un pequeno
    retardo y se informan como 'pendiente': asi la confirmacion llega al
    servidor antes de que el equipo se vaya. Decir que estan 'hechas'
    seria afirmar algo que nadie puede comprobar.

    Nunca lanza: devuelve el motivo del fallo para que el servidor lo
    registre y el panel lo ensene.
    """

    funcion = HANDLERS.get(str(action or "").strip().lower())

    if funcion is None:
        return {"accepted": False, "error": "Accion no permitida"}

    if action not in DEFERRED:

        try:
            funcion()

        except Exception as error:
            return {"accepted": False, "error": str(error)[:200]}

        return {
            "accepted": True,
            "executed": True,
            "pending": False,
            "detail": "Realizada"
        }

    espera = EXECUTION_DELAY_SECONDS if delay is None else delay

    def _mas_tarde():

        time.sleep(espera)

        try:
            funcion()

        except Exception as error:
            print(f"[energia] No se pudo completar '{action}': {error}")

    threading.Thread(
        target=_mas_tarde,
        name=f"PowerAction-{action}",
        daemon=True
    ).start()

    return {
        "accepted": True,
        "executed": False,
        "pending": True,
        "detail": f"Orden aceptada; se ejecutara en {espera} segundos"
    }
