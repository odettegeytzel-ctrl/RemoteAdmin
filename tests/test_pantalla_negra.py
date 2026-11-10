r"""
Un contexto de captura roto se rehace, no se queda en negro.

    .venv\Scripts\python tests/test_pantalla_negra.py

Un equipo grababa bien todo el dia -ocho segmentos, de 2 a 8 MB cada
uno, o sea escritorio con contenido de verdad- y su pantalla en vivo
se veia completamente negra. Nada en el registro.

La diferencia entre los dos caminos era COMO crean el contexto de
mss:

    grabador          un contexto propio, abierto al empezar la
    (funciona)        grabacion y cerrado al acabar

    pantalla en vivo  uno por hilo, cacheado en un threading.local
    (negra)           y conservado PARA SIEMPRE

El cacheo se hizo por velocidad, y estaba bien medido: abrir el
contexto costaba 58 de los 117 ms del fotograma. Lo que faltaba era
tirarlo cuando deja de servir.

Porque el contexto de mss no es solo un objeto de Python: guarda un
descriptor de dibujo de la pantalla y un mapa de bits del tamano del
monitor, pedidos a Windows una vez. Si la pantalla cambia debajo
-resolucion, monitor dormido, escritorio remoto, driver reiniciado-
esos recursos quedan invalidos. Y Windows entonces NO suele avisar:
BitBlt copia negro y dice que todo fue bien.

Asi que una vez negro, negro para siempre. La unica salida era
reiniciar el ayudante.

Lo que se comprueba aqui es que los dos modos de fallo tiran el
contexto y reintentan: el que lanza excepcion -el BitBlt failed que
ya aparecio en el registro del 6 de noviembre- y el silencioso, el
fotograma todo negro.

Y, lo mas importante de todo, que una pantalla negra DE VERDAD -el
monitor dormido- se sigue mandando como negra. Si el reintento
inventara algo, la pantalla en vivo mentiria.

No se importa agent: arrastra wmi, pyautogui y mss, y mss no se
puede instanciar sin escritorio. Se extrae del fuente el trozo de la
captura y se ejecuta con las piezas de Windows sustituidas, que es
lo que permite forzar los fallos. Se prueba el codigo que de verdad
se publica, no una copia.
"""

import base64
import io
import sys
import threading
import types

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

FUENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()


TROZO = FUENTE[
    FUENTE.index("_captura_local = threading.local()"):
    FUENTE.index("# Periodo objetivo entre fotogramas.")
]


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


class ImagenFalsa:
    """Lo justo que usa el codigo: tamano, getbbox, resize y save."""

    def __init__(self, negra, ancho=1920, alto=1080):
        self.negra = negra
        self.width = ancho
        self.height = alto

    def getbbox(self):
        return None if self.negra else (0, 0, self.width, self.height)

    def resize(self, medida, filtro=None):
        return ImagenFalsa(self.negra, medida[0], medida[1])

    def save(self, destino, format=None, quality=None):
        destino.write(b"NEGRO" if self.negra else b"CONTENIDO")


class CapturaFalsa:
    """Un contexto de mss. Lleva la cuenta de si se cerro."""

    creados = []

    def __init__(self, guion):
        # guion: lista de "bien", "negro" o "estalla", uno por grab
        self.guion = guion
        self.cerrado = False
        self.grabs = 0
        self.negra = False
        CapturaFalsa.creados.append(self)

    @property
    def monitors(self):
        return [None, {"left": 0, "top": 0,
                       "width": 1920, "height": 1080}]

    def grab(self, monitor):

        self.grabs += 1

        que = self.guion.pop(0) if self.guion else "bien"

        if que == "estalla":
            raise OSError(
                "gdi32.BitBlt() failed. Windows graphics function "
                "failed (no error provided)"
            )

        self.negra = (que == "negro")

        return types.SimpleNamespace(size=(1920, 1080), rgb=b"")

    def close(self):
        self.cerrado = True


def modulo_con(guion):
    """
    Carga las funciones de captura con un mss que sigue ese guion.

    El guion se comparte entre contextos: el siguiente sigue donde
    lo dejo el anterior, que es lo que permite decir "el primero
    falla, el segundo no".
    """

    pendiente = list(guion)

    CapturaFalsa.creados = []

    def fabricar():
        return CapturaFalsa(pendiente)

    def frombytes(modo, medida, datos):
        ultimo = CapturaFalsa.creados[-1]
        return ImagenFalsa(ultimo.negra, medida[0], medida[1])

    espacio = {
        "threading": threading,
        "io": io,
        "base64": base64,
        "mss": types.SimpleNamespace(mss=fabricar),
        "Image": types.SimpleNamespace(
            frombytes=frombytes,
            Resampling=types.SimpleNamespace(BILINEAR=2)
        ),
        "screen_geometry": {},
        "print": lambda *a, **k: None,
    }

    exec(compile(TROZO, "captura", "exec"), espacio)

    return espacio


def prueba_el_camino_normal_no_cambia():
    """
    Lo primero: que la correccion no rompa el caso bueno ni haga
    trabajo de mas. El cacheo existe por una razon medida.
    """

    espacio = modulo_con(["bien", "bien", "bien"])

    for _ in range(3):
        espacio["capture_screen"]()

    comprobar("tres fotogramas buenos usan UN solo contexto",
              len(CapturaFalsa.creados) == 1,
              len(CapturaFalsa.creados))

    comprobar("y se hacen tres capturas, no seis",
              CapturaFalsa.creados[0].grabs == 3,
              CapturaFalsa.creados[0].grabs)

    comprobar("el contexto no se cierra entre fotogramas",
              not CapturaFalsa.creados[0].cerrado)


def prueba_una_excepcion_rehace_el_contexto():
    """
    El BitBlt failed que aparecio en el registro de un equipo real
    el 6 de noviembre.
    """

    espacio = modulo_con(["estalla", "bien"])

    salida = espacio["capture_screen"]()

    comprobar("se devuelve un fotograma pese al fallo", bool(salida))

    comprobar("se creo un contexto nuevo",
              len(CapturaFalsa.creados) == 2,
              len(CapturaFalsa.creados))

    comprobar("y el roto se cerro",
              CapturaFalsa.creados[0].cerrado)

    comprobar("lo que se manda tiene contenido",
              b"CONTENIDO" in base64.b64decode(salida))


def prueba_un_fotograma_negro_rehace_el_contexto():
    """
    El fallo silencioso, que es el que de verdad costaba: Windows
    copia negro y no se queja.
    """

    espacio = modulo_con(["negro", "bien"])

    salida = espacio["capture_screen"]()

    comprobar("un fotograma negro tira el contexto",
              len(CapturaFalsa.creados) == 2,
              len(CapturaFalsa.creados))

    comprobar("el contexto negro se cerro",
              CapturaFalsa.creados[0].cerrado)

    comprobar("el reintento se hizo con el contexto nuevo",
              CapturaFalsa.creados[1].grabs == 1,
              CapturaFalsa.creados[1].grabs)

    comprobar("y lo que llega al panel ya tiene contenido",
              b"CONTENIDO" in base64.b64decode(salida))


def prueba_una_pantalla_negra_de_verdad_se_manda_negra():
    """
    La promesa que no se puede romper.

    El monitor dormido o la sesion bloqueada dan negro de verdad. El
    reintento sale negro otra vez y eso es lo que se manda: la
    pantalla en vivo no puede inventar contenido que no existe.
    """

    espacio = modulo_con(["negro", "negro", "negro"])

    salida = espacio["capture_screen"]()

    comprobar("se devuelve algo, no un error", bool(salida))

    comprobar("y lo que se devuelve es el negro",
              b"NEGRO" in base64.b64decode(salida))

    comprobar("se reintento UNA vez, no en bucle",
              len(CapturaFalsa.creados) == 2,
              len(CapturaFalsa.creados))


def prueba_no_se_reintenta_sin_parar():
    """
    Un reintento por fotograma. Doce fotogramas por segundo
    reintentando sin limite serian veinticuatro capturas por segundo
    sobre un escritorio que ya va mal.
    """

    espacio = modulo_con(["negro"] * 20)

    for _ in range(5):
        espacio["capture_screen"]()

    comprobar("cinco fotogramas negros: un contexto nuevo cada uno",
              len(CapturaFalsa.creados) <= 6,
              len(CapturaFalsa.creados))

    comprobar("y dos capturas por fotograma, no mas",
              sum(c.grabs for c in CapturaFalsa.creados) <= 10,
              sum(c.grabs for c in CapturaFalsa.creados))


def prueba_si_el_reintento_tambien_estalla_el_error_sube():
    """
    Dos excepciones seguidas no son un contexto invalido: es que no
    se puede capturar. Inventar un fotograma ahi taparia el problema
    y el remoto se veria congelado sin que nadie supiera por que.
    """

    espacio = modulo_con(["estalla", "estalla"])

    try:
        espacio["capture_screen"]()
        subio = False

    except OSError:
        subio = True

    comprobar("dos excepciones seguidas: el error sube", subio)


def prueba_el_contexto_sigue_siendo_por_hilo():
    """
    Lo que no debe cambiar: mss no se puede compartir entre hilos, y
    la pantalla en vivo corre en el ejecutor, donde los hilos rotan.
    """

    comprobar("sigue en un threading.local",
              "_captura_local = threading.local()" in FUENTE)

    comprobar("al soltarlo se vacia el del hilo, no uno global",
              "_captura_local.mss = None" in FUENTE)

    comprobar("existe la funcion que lo suelta",
              "def soltar_el_contexto_de_captura():" in FUENTE)


def prueba_el_grabador_no_se_toca():
    """
    El grabador funciona: abre su contexto con with en su propio
    hilo y lo cierra al acabar. No usa el cacheado y no debe empezar
    a usarlo.
    """

    grabador = io.open(RAIZ / "agent" / "recorder.py",
                       encoding="utf-8").read()

    comprobar("el grabador sigue abriendo su propio contexto",
              "with mss.mss() as screen:" in grabador)

    comprobar("y no usa el contexto cacheado de la pantalla en vivo",
              "_contexto_de_captura" not in grabador)


def principal():

    prueba_el_camino_normal_no_cambia()
    prueba_una_excepcion_rehace_el_contexto()
    prueba_un_fotograma_negro_rehace_el_contexto()
    prueba_una_pantalla_negra_de_verdad_se_manda_negra()
    prueba_no_se_reintenta_sin_parar()
    prueba_si_el_reintento_tambien_estalla_el_error_sube()
    prueba_el_contexto_sigue_siendo_por_hilo()
    prueba_el_grabador_no_se_toca()

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
