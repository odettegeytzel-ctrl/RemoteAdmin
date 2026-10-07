"""
Publicacion de versiones del Agent.

El servidor solo SIRVE lo que ya viene firmado. No firma nada: la
clave privada no esta aqui, y ese es justamente el punto. Si el
servidor pudiera firmar, quien se hiciera con el podria instalar
cualquier cosa en todos los equipos administrados a la vez, que es el
escenario que la firma existe para evitar.

Lo que hay en disco, en releases/:

    manifest.json        version, hash, tamano y firma
    agent-<version>.zip  el paquete

Los produce tools/sign_agent_release.py en el equipo de quien publica,
con la clave privada, y se copian aqui. El servidor los lee y los
entrega tal cual.

Si no hay nada publicado, los endpoints lo dicen y los Agents siguen
con su version. No publicar no rompe nada.
"""

import json
import os


# Carpeta de publicacion, junto a la raiz del proyecto.
RELEASES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "releases"
)

MANIFEST_NAME = "manifest.json"


class ReleaseNotPublished(LookupError):
    """No hay ninguna version publicada."""


def manifest_path():
    return os.path.join(RELEASES_DIR, MANIFEST_NAME)


def load_published():
    """
    Lee lo publicado: (manifiesto, firma).

    No se comprueba la firma aqui. La comprueba el AGENT, con su clave
    publica, y asi tiene que ser: una comprobacion en el servidor no
    protegeria de un servidor comprometido, que es de lo que se trata.
    """

    ruta = manifest_path()

    if not os.path.isfile(ruta):
        raise ReleaseNotPublished("No hay ninguna version publicada")

    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)

    except (OSError, ValueError) as problema:
        raise ReleaseNotPublished(
            f"El manifiesto publicado no se puede leer: {problema}"
        )

    manifiesto = datos.get("manifest")
    firma = datos.get("signature")

    if not manifiesto or not firma:
        raise ReleaseNotPublished("El manifiesto publicado esta incompleto")

    return manifiesto, firma


def package_path(version):
    """
    Ruta del paquete de una version.

    El nombre se construye con la version que figura en el MANIFIESTO,
    nunca con la que pida el cliente: asi un 'version' de la peticion
    no puede convertirse en una ruta.
    """

    return os.path.join(RELEASES_DIR, f"agent-{version}.zip")


def load_package(version):
    """Bytes del paquete publicado para esa version."""

    ruta = package_path(version)

    if not os.path.isfile(ruta):
        raise ReleaseNotPublished(
            f"No hay paquete publicado para la version {version}"
        )

    with open(ruta, "rb") as archivo:
        return archivo.read()


def published_version():
    """Version publicada, o None si no hay ninguna."""

    try:
        manifiesto, _ = load_published()
        return manifiesto.get("version")

    except ReleaseNotPublished:
        return None
