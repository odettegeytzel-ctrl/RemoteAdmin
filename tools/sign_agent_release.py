"""
Publicacion de una version firmada del Agent.

Se ejecuta en el equipo de quien publica, NO en el servidor: ahi es
donde esta la clave privada y donde debe quedarse.

    # Una sola vez, para crear el par de claves. La ruta es
    # obligatoria y tiene que estar FUERA del proyecto.
    python tools/sign_agent_release.py --generar-clave --clave D:\\llaves\\agent-signing.pem

    # Cada vez que se publica una version
    python tools/sign_agent_release.py --firmar --clave D:\\llaves\\agent-signing.pem

Para no repetir la ruta, se puede dejar en una variable de entorno:

    set REMOTEADMIN_SIGNING_KEY=D:\\llaves\\agent-signing.pem
    python tools/sign_agent_release.py --firmar

El segundo comando deja en releases/ el paquete y el manifiesto
firmado, listos para copiar al servidor. La clave privada NO se copia
a ningun sitio: se lee donde este y se usa en memoria.

Sobre la clave privada
----------------------

No se guarda en el repositorio ni se sube al servidor. Si estuviera en
cualquiera de los dos, la firma no serviria para nada: quien llegara
ahi podria firmar lo que quisiera e instalarlo en todos los equipos
administrados.

Guardala donde guardas lo demas que no puede perderse, y con copia: si
se pierde, hay que generar un par nuevo y actualizar a mano la clave
publica de todos los Agents ya instalados, porque dejarian de aceptar
cualquier version nueva.
"""

import argparse
import base64
import io
import json
import os
import sys
import zipfile


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, RAIZ)

from agent import release
from agent import version as versionado

from backend import packaging


RELEASES_DIR = os.path.join(RAIZ, "releases")


# Variable de entorno con la ruta de la clave, para no teclearla cada
# vez. Solo lleva la RUTA, nunca la clave.
SIGNING_KEY_ENV = "REMOTEADMIN_SIGNING_KEY"


def _dentro_del_proyecto(ruta):
    """
    True si esa ruta cae dentro del arbol del proyecto.

    Se compara por ruta real para que no se cuele por un enlace
    simbolico ni por un '..' en medio.
    """

    try:
        real = os.path.realpath(os.path.abspath(ruta))
        raiz = os.path.realpath(RAIZ)

    except OSError:
        return False

    return real == raiz or real.startswith(raiz + os.sep)


def _exigir_fuera_del_proyecto(ruta, accion):
    """
    Impide trabajar con una clave privada dentro del proyecto.

    No es una formalidad. Una clave ahi dentro acaba, antes o despues,
    en un commit, en el ZIP del Agent o en una copia del directorio
    al servidor. Y una clave de firma en el servidor anula el sentido
    de firmar: quien llegara a el podria publicar lo que quisiera e
    instalarlo en todos los equipos administrados.
    """

    if not _dentro_del_proyecto(ruta):
        return True

    print()
    print(f"ERROR: no se puede {accion} dentro del proyecto.")
    print()
    print(f"  Ruta indicada: {os.path.abspath(ruta)}")
    print(f"  Proyecto:      {RAIZ}")
    print()
    print("La clave privada de firma tiene que vivir fuera del")
    print("repositorio. Ahi dentro acabaria en un commit, en el ZIP")
    print("del Agent o en una copia al servidor, y una clave de firma")
    print("en el servidor deja la firma sin sentido.")
    print()
    print("Indica una ruta fuera, por ejemplo:")
    print("  --clave D:\\llaves\\agent-signing.pem")
    print()

    return False


def _ruta_de_la_clave(indicada):
    """La del parametro, o la de la variable de entorno."""

    if indicada:
        return indicada

    return os.environ.get(SIGNING_KEY_ENV, "").strip()


def generar_clave(destino):
    """
    Crea un par Ed25519 e imprime la publica para pegarla en el Agent.

    La privada se escribe en la ruta indicada, que tiene que estar
    fuera del proyecto, y NO se muestra por pantalla: lo que se
    imprime acaba en el historial de la terminal y en los registros
    de sesion.
    """

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey
    )

    if not destino:
        print()
        print("ERROR: indica donde guardar la clave privada.")
        print()
        print("  python tools/sign_agent_release.py --generar-clave \\")
        print("      --clave D:\\llaves\\agent-signing.pem")
        print()
        print("Tiene que ser una ruta FUERA del proyecto.")
        print()
        return 1

    if not _exigir_fuera_del_proyecto(destino, "crear la clave"):
        return 1

    destino = os.path.abspath(destino)

    carpeta = os.path.dirname(destino)

    if carpeta and not os.path.isdir(carpeta):
        print(f"ERROR: no existe la carpeta {carpeta}")
        return 1

    if os.path.exists(destino):
        print(f"ERROR: ya existe {destino}.")
        print("No se sobrescribe: si esa clave esta en uso, perderla")
        print("dejaria sin actualizaciones a los Agents instalados.")
        return 1

    privada = Ed25519PrivateKey.generate()

    pem = privada.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )

    with open(destino, "wb") as archivo:
        archivo.write(pem)

    try:
        os.chmod(destino, 0o600)
    except OSError:
        pass

    publica = base64.b64encode(
        privada.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
    ).decode("ascii")

    print()
    print("Par de claves creado.")
    print()
    print(f"  Clave privada: {destino}")
    print("  Guardala fuera del repositorio y haz copia de seguridad.")
    print("  No la subas al servidor.")
    print()
    print("  Clave publica (esta SI va en el codigo):")
    print()
    print(f"    {publica}")
    print()
    print("  Pegala en agent/release.py, en AGENT_UPDATE_PUBLIC_KEY.")
    print()

    return 0


def firmar(ruta_clave):
    """
    Arma el paquete de la version actual, lo firma y lo publica.

    La clave se lee DONDE ESTE y se usa en memoria. No se copia al
    proyecto, no se deja en releases/ y no se escribe en ningun sitio.
    """

    if not ruta_clave:
        print()
        print("ERROR: indica la clave privada con la que firmar.")
        print()
        print("  python tools/sign_agent_release.py --firmar \\")
        print("      --clave D:\\llaves\\agent-signing.pem")
        print()
        print(f"O deja la ruta en {SIGNING_KEY_ENV}.")
        print()
        return 1

    if not _exigir_fuera_del_proyecto(ruta_clave, "usar una clave"):
        return 1

    if not os.path.isfile(ruta_clave):
        print(f"ERROR: no existe la clave privada: {ruta_clave}")
        return 1

    version = versionado.AGENT_VERSION

    if not versionado.is_valid(version):
        print(f"ERROR: la version del Agent no es valida: {version!r}")
        return 1

    print(f"Empaquetando la version {version}")

    contenido = _construir_paquete()

    manifiesto = release.build_manifest(version, contenido)

    with open(ruta_clave, "rb") as archivo:
        pem = archivo.read()

    firma = release.sign_manifest(manifiesto, pem)

    # Se comprueba la firma recien hecha contra la clave publica que
    # lleva el Agent. Si no coinciden, se publicaria algo que ningun
    # Agent instalado podria instalar.
    if release.AGENT_UPDATE_PUBLIC_KEY:

        try:
            release.verify_manifest(manifiesto, firma)
            print("Firma comprobada contra la clave publica del Agent")

        except release.SignatureError as problema:
            print(f"ERROR: {problema}")
            print()
            print("La clave privada usada no se corresponde con la")
            print("AGENT_UPDATE_PUBLIC_KEY que lleva el Agent. Publicar")
            print("esto dejaria la actualizacion sin efecto.")
            return 1

    else:
        print()
        print("AVISO: agent/release.py no tiene clave publica configurada.")
        print("Pega la que imprimio --generar-clave antes de publicar,")
        print("o ningun Agent aceptara esta actualizacion.")
        print()

    os.makedirs(RELEASES_DIR, exist_ok=True)

    ruta_zip = os.path.join(RELEASES_DIR, f"agent-{version}.zip")

    with open(ruta_zip, "wb") as archivo:
        archivo.write(contenido)

    ruta_manifiesto = os.path.join(RELEASES_DIR, "manifest.json")

    with open(ruta_manifiesto, "w", encoding="utf-8") as archivo:
        json.dump(
            {"manifest": manifiesto, "signature": firma},
            archivo, indent=2, ensure_ascii=False
        )

    print()
    print(f"  Paquete:    {ruta_zip}  ({len(contenido)} bytes)")
    print(f"  Manifiesto: {ruta_manifiesto}")
    print(f"  SHA-256:    {manifiesto['sha256']}")
    print()
    print("Copia los dos archivos a releases/ en el servidor.")
    print("Los Agents los recogeran en su siguiente comprobacion.")
    print()
    print("La clave privada NO se ha copiado a ningun sitio: sigue")
    print(f"solo en {os.path.dirname(os.path.abspath(ruta_clave))}.")
    print("El servidor no la necesita y no debe tenerla.")
    print()

    return 0


def _construir_paquete():
    """
    El mismo contenido que descarga un equipo nuevo, sin el .env.

    Se reutiliza la lista de archivos de backend/packaging.py para que
    no puedan separarse: si el paquete de instalacion incluye un
    modulo nuevo y el de actualizacion no, los equipos ya instalados
    se quedarian con una version incompleta.

    El .env no va: lleva la configuracion del equipo y actualizar no
    debe tocarla.
    """

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as paquete:

        for relativo in packaging.PACKAGE_FILES:

            origen = os.path.join(RAIZ, *relativo.split("/"))

            if not os.path.isfile(origen):
                raise SystemExit(f"Falta un archivo del paquete: {relativo}")

            paquete.write(origen, arcname=relativo)

    return buffer.getvalue()


def main():

    analizador = argparse.ArgumentParser(
        description="Publica una version firmada del Agent"
    )

    analizador.add_argument(
        "--generar-clave", action="store_true",
        help="crea el par de claves de firma (una sola vez)"
    )

    analizador.add_argument(
        "--firmar", action="store_true",
        help="empaqueta y firma la version actual del Agent"
    )

    analizador.add_argument(
        "--clave", default="",
        help=(
            "ruta de la clave privada de firma, FUERA del proyecto "
            f"(o la variable {SIGNING_KEY_ENV})"
        )
    )

    argumentos = analizador.parse_args()

    ruta = _ruta_de_la_clave(argumentos.clave)

    if argumentos.generar_clave:
        return generar_clave(ruta)

    if argumentos.firmar:
        return firmar(ruta)

    analizador.print_help()

    return 1


if __name__ == "__main__":
    sys.exit(main())
