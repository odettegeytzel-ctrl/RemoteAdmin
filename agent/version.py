"""
Version del Agent de RemoteAdmin.

Un archivo propio y minimo, sin importar nada, para que lo puedan leer
tanto el Agent como el servidor y las herramientas de publicacion sin
arrastrar wmi, mss ni pyautogui.

El formato es MAYOR.MENOR.PARCHE, con numeros enteros. Se comparan como
numeros y no como texto, porque "1.10.0" es posterior a "1.9.0" aunque
alfabeticamente vaya antes: ese detalle es justo el que convierte una
actualizacion en un downgrade silencioso.
"""

import re


AGENT_VERSION = "1.1.0"


VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class VersionError(ValueError):
    """Version mal formada, con un motivo legible."""


def parse(version):
    """
    Convierte "1.2.3" en (1, 2, 3).

    Lo que no encaje exactamente se rechaza. No se intenta
    interpretar "1.2", "v1.2.3" ni "1.2.3-beta": una version que no se
    entiende del todo no sirve para decidir si hay que actualizar, y
    adivinar aqui es como se acaba instalando lo que no toca.
    """

    if not isinstance(version, str):
        raise VersionError("La version debe ser texto")

    encaje = VERSION_PATTERN.match(version.strip())

    if not encaje:
        raise VersionError(f"Version mal formada: {version!r}")

    return tuple(int(parte) for parte in encaje.groups())


def is_valid(version):
    """True si la version tiene el formato esperado."""

    try:
        parse(version)
        return True

    except VersionError:
        return False


def compare(una, otra):
    """-1 si una < otra, 0 si son iguales, 1 si una > otra."""

    izquierda = parse(una)
    derecha = parse(otra)

    if izquierda < derecha:
        return -1

    if izquierda > derecha:
        return 1

    return 0


def is_newer(candidata, instalada):
    """
    True solo si `candidata` es ESTRICTAMENTE posterior.

    Una version igual no se instala (no hay nada que ganar) y una
    anterior tampoco: volver atras sin querer reintroduce fallos ya
    corregidos, y seria la forma mas comoda de atacar un equipo si
    alguien lograse publicar una version antigua.
    """

    return compare(candidata, instalada) > 0
