"""
El portapapeles del equipo administrado.

Se habla con Windows directamente, por ctypes. La alternativa era
pyperclip, que ya esta instalado, pero solo porque PyAutoGUI lo
arrastra: depender de una dependencia indirecta significa que el dia
que PyAutoGUI deje de necesitarla, el portapapeles se cae sin que
nadie haya tocado nada.

SOLO TEXTO. Ni imagenes, ni archivos, ni formatos enriquecidos. Un
escritorio remoto que copia archivos por el portapapeles es otra
funcion, con otros riesgos, y no se cuela aqui de rebote.

Este modulo corre en el AYUDANTE, dentro de la sesion del usuario. El
servicio vive en la Sesion 0, donde el portapapeles es otro distinto
y vacio: desde alli no se leeria nada util.

El contenido no se escribe en ningun registro. Lo que se copia y se
pega es de lo mas delicado que pasa por un equipo -contraseñas,
datos personales-, y un portapapeles que acaba en un archivo de
texto es peor que no tener portapapeles.
"""

import ctypes

from ctypes import wintypes


# Mas de esto no viaja. No es una limitacion de Windows: es que un
# portapapeles de megas no es texto que alguien vaya a pegar, es otra
# cosa -un volcado, un archivo- y no deberia ir por aqui.
MAXIMO = 256 * 1024


CF_UNICODETEXT = 13

GMEM_MOVEABLE = 0x0002


def _usuario():
    return ctypes.windll.user32


def _nucleo():
    return ctypes.windll.kernel32


def _preparar():
    """Las firmas, para que ctypes no trunque punteros en 64 bits."""

    usuario = _usuario()
    nucleo = _nucleo()

    usuario.OpenClipboard.argtypes = [wintypes.HWND]
    usuario.OpenClipboard.restype = wintypes.BOOL

    usuario.GetClipboardData.argtypes = [wintypes.UINT]
    usuario.GetClipboardData.restype = wintypes.HANDLE

    usuario.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    usuario.SetClipboardData.restype = wintypes.HANDLE

    nucleo.GlobalLock.argtypes = [wintypes.HGLOBAL]
    nucleo.GlobalLock.restype = wintypes.LPVOID

    nucleo.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    nucleo.GlobalUnlock.restype = wintypes.BOOL

    nucleo.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    nucleo.GlobalAlloc.restype = wintypes.HGLOBAL


class _Abierto:
    """
    El portapapeles abierto, y cerrado pase lo que pase.

    Mientras un proceso lo tiene abierto, NINGUN otro puede usarlo:
    dejarlo abierto por una excepcion bloquea el copiar y pegar de
    todo el equipo hasta que el Agent se reinicie. De ahi que esto
    sea un gestor de contexto y no dos llamadas sueltas.

    Y de ahi los reintentos: es normal que otra aplicacion lo tenga
    un instante, y rendirse al primer intento fallaria la mitad de
    las veces sin motivo.
    """

    def __init__(self, intentos=10, espera=0.02):
        self.intentos = intentos
        self.espera = espera

    def __enter__(self):

        import time

        for _ in range(self.intentos):

            if _usuario().OpenClipboard(None):
                return self

            time.sleep(self.espera)

        raise OSError("El portapapeles esta ocupado por otro programa")

    def __exit__(self, *_):
        _usuario().CloseClipboard()
        return False


def leer():
    """
    El texto del portapapeles, o None si no hay texto.

    Que no haya texto es lo normal cuando lo ultimo copiado fue una
    imagen o unos archivos: no es un error y no se trata como tal.
    """

    _preparar()

    usuario = _usuario()
    nucleo = _nucleo()

    with _Abierto():

        puntero = usuario.GetClipboardData(CF_UNICODETEXT)

        if not puntero:
            return None

        bloque = nucleo.GlobalLock(puntero)

        if not bloque:
            return None

        try:
            texto = ctypes.c_wchar_p(bloque).value

        finally:
            nucleo.GlobalUnlock(puntero)

    if texto is None:
        return None

    # El tope se aplica tambien al leer: si el usuario del equipo
    # copio medio libro, no se arrastra por el WebSocket entero.
    return texto[:MAXIMO]


def escribir(texto):
    """
    Deja ese texto en el portapapeles del equipo.

    Devuelve cuantos caracteres se dejaron, para poder contarlo sin
    anotar jamas el contenido.
    """

    if not isinstance(texto, str):
        raise TypeError("El portapapeles solo lleva texto")

    texto = texto[:MAXIMO]

    _preparar()

    usuario = _usuario()
    nucleo = _nucleo()

    datos = ctypes.create_unicode_buffer(texto)
    tamano = ctypes.sizeof(datos)

    with _Abierto():

        usuario.EmptyClipboard()

        # GMEM_MOVEABLE y no un buffer propio: al entregarlo a
        # SetClipboardData, Windows se queda con la propiedad del
        # bloque y lo libera el. Pasarle memoria nuestra seria
        # entregarle algo que despues liberariamos dos veces.
        bloque = nucleo.GlobalAlloc(GMEM_MOVEABLE, tamano)

        if not bloque:
            raise MemoryError("No se pudo reservar el portapapeles")

        destino = nucleo.GlobalLock(bloque)

        if not destino:
            raise MemoryError("No se pudo escribir el portapapeles")

        try:
            ctypes.memmove(destino, datos, tamano)

        finally:
            nucleo.GlobalUnlock(bloque)

        if not usuario.SetClipboardData(CF_UNICODETEXT, bloque):
            raise OSError("Windows rechazo el portapapeles")

    return len(texto)
