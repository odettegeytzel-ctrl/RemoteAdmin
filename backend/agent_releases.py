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

import hashlib
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


# ==============================
# EL INSTALADOR DE WINDOWS
# ==============================
#
# El .exe que se descarga desde el panel. Vive en la misma carpeta que
# los paquetes firmados, por la misma razon: lo produce quien publica,
# en su equipo, y se copia aqui. El servidor no lo construye.
#
# No va en el repositorio —pesa decenas de MB y installer/dist/ esta
# ignorado— asi que se sube igual que agent-<version>.zip.

INSTALLER_NAME = "VEYRA-Instalador.exe"


class InstallerNotPublished(LookupError):
    """No hay instalador publicado."""


def installer_path():
    return os.path.join(RELEASES_DIR, INSTALLER_NAME)


def installer_info():
    """
    Lo que el panel enseña ANTES de descargar: version, tamano y huella.

    La version NO sale del nombre del archivo ni se escribe a mano:
    sale del manifiesto firmado, que es lo unico que no se puede
    falsear sin la clave privada. Si no hay version publicada, el
    instalador no se ofrece: descargarlo sin saber que instala seria
    peor que no tenerlo.
    """

    ruta = installer_path()

    if not os.path.isfile(ruta):
        raise InstallerNotPublished(
            "No hay instalador publicado en el servidor"
        )

    manifiesto, _ = load_published()

    resumen = hashlib.sha256()

    with open(ruta, "rb") as archivo:

        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            resumen.update(bloque)

    return {
        "filename": INSTALLER_NAME,
        "version": manifiesto["version"],
        "size": os.path.getsize(ruta),
        "sha256": resumen.hexdigest()
    }
