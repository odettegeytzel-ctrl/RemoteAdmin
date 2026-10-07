"""
Firma y verificacion de las actualizaciones del Agent.

Por que una firma y no un hash
------------------------------

Un SHA-256 que entrega el mismo servidor del que se descarga el
paquete no demuestra nada: quien pueda cambiar el paquete puede
cambiar tambien el hash que lo acompana. Solo sirve para detectar una
descarga corrupta, no para saber de quien viene el archivo.

Con una firma Ed25519 el Agent comprueba que el paquete lo publico
quien tiene la clave privada. Si alguien se hiciera con el servidor,
o con el camino hasta el, seguiria sin poder instalar codigo en los
equipos administrados: no podria firmar. Eso importa aqui mas que en
otros sitios, porque una actualizacion es, por definicion, ejecutar
codigo nuevo en todos los equipos a la vez.

Donde vive cada clave
---------------------

    publica     dentro del Agent (AGENT_UPDATE_PUBLIC_KEY). No es
                secreta: solo sirve para comprobar firmas.

    privada     FUERA del repositorio y fuera del servidor. La guarda
                quien publica versiones y se usa a mano al firmar.

Si la privada estuviera en el repositorio o en el servidor, la firma
no anadiria nada: quien llegara hasta ahi podria firmar lo que
quisiera. Por eso este modulo no la genera ni la guarda en ningun
sitio; solo la recibe como argumento cuando se firma.

Que se firma
------------

El manifiesto entero, en su forma canonica: version, tamano y hash del
paquete. Firmar el manifiesto y no solo el ZIP permite comprobar la
version ANTES de descargar nada, y deja el hash dentro de lo firmado,
asi que ya no depende de la buena fe del servidor.
"""

import base64
import hashlib
import json


# Clave publica con la que el Agent comprueba las actualizaciones.
#
# NO es secreta: solo sirve para verificar firmas, nunca para
# producirlas. Por eso va en el codigo y viaja con el Agent a cada
# equipo administrado.
#
# Su pareja privada vive FUERA del repositorio y fuera del servidor, y
# solo la usa quien publica versiones. Si alguna vez aparece una clave
# privada por aqui, la firma deja de significar nada.
#
# Se obtiene con:
#
#     python tools/sign_agent_release.py --generar-clave --clave <ruta>
#
# Vacia significa que todavia no se ha configurado la publicacion. En
# ese caso el Agent NO se actualiza: no se instala nada sin firma, y
# quedarse en la version actual es siempre mejor que instalar algo que
# no se puede comprobar.
AGENT_UPDATE_PUBLIC_KEY = "DWvOsshyNgNlDFh4QQLg6wTlNx8mOAVlntKJxNinS3k="


# Campos que componen el manifiesto firmado.
MANIFEST_FIELDS = ("version", "sha256", "size")


class ReleaseError(ValueError):
    """Paquete o manifiesto rechazado, con un motivo legible."""


class SignatureError(ReleaseError):
    """
    La firma no corresponde.

    Tiene su propio tipo porque no es un fallo cualquiera: significa
    que el paquete no viene de quien dice venir, y eso se registra y
    se trata distinto de una descarga a medias.
    """


def sha256_hex(datos):
    """Huella del paquete. Detecta corrupcion, no suplantacion."""

    return hashlib.sha256(datos).hexdigest()


def canonical_manifest(manifiesto):
    """
    Forma canonica del manifiesto, que es lo que se firma.

    Claves ordenadas y sin espacios: si el texto exacto dependiera del
    orden en que se escribieron los campos, una firma valida dejaria
    de verificarse al releer el mismo manifiesto.

    Se toman SOLO los campos conocidos. Asi el servidor puede anadir
    informacion util (notas, fecha) sin invalidar las firmas, y a la
    vez un campo extra no puede colarse dentro de lo que se da por
    verificado.
    """

    faltan = [c for c in MANIFEST_FIELDS if c not in manifiesto]

    if faltan:
        raise ReleaseError(f"Al manifiesto le faltan campos: {faltan}")

    reducido = {campo: manifiesto[campo] for campo in MANIFEST_FIELDS}

    return json.dumps(
        reducido, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_manifest(version, contenido):
    """Manifiesto de un paquete, todavia sin firmar."""

    return {
        "version": version,
        "sha256": sha256_hex(contenido),
        "size": len(contenido)
    }


def sign_manifest(manifiesto, clave_privada_pem):
    """
    Firma un manifiesto. Se usa SOLO al publicar, nunca en el Agent.

    La clave privada llega como argumento y no se guarda en ningun
    sitio: este modulo no la escribe ni la recuerda.
    """

    from cryptography.hazmat.primitives import serialization

    clave = serialization.load_pem_private_key(
        clave_privada_pem, password=None
    )

    firma = clave.sign(canonical_manifest(manifiesto))

    return base64.b64encode(firma).decode("ascii")


def verify_manifest(manifiesto, firma_b64, clave_publica_b64=None):
    """
    Comprueba que el manifiesto lo firmo quien tiene la clave privada.

    Falla —en vez de dejar pasar— ante cualquier duda: sin clave
    configurada, sin firma, con firma mal formada o con firma que no
    corresponde. Nunca devuelve False silenciosamente, porque un valor
    de retorno ignorado por error se convertiria en una actualizacion
    sin comprobar.
    """

    clave = (
        clave_publica_b64
        if clave_publica_b64 is not None
        else AGENT_UPDATE_PUBLIC_KEY
    )

    if not clave:
        raise SignatureError(
            "No hay clave publica configurada: no se puede comprobar "
            "la firma de la actualizacion"
        )

    if not firma_b64:
        raise SignatureError("La actualizacion no viene firmada")

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey
    )

    try:
        publica = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(clave)
        )

    except Exception as problema:
        raise SignatureError(f"Clave publica invalida: {problema}")

    try:
        firma = base64.b64decode(firma_b64, validate=True)

    except Exception as problema:
        raise SignatureError(f"Firma mal formada: {problema}")

    try:
        publica.verify(firma, canonical_manifest(manifiesto))

    except InvalidSignature:
        raise SignatureError(
            "La firma no corresponde a este paquete"
        )

    return True


def verify_package(contenido, manifiesto):
    """
    Comprueba que el paquete descargado es el del manifiesto.

    Se llama DESPUES de verificar la firma del manifiesto. En ese
    orden, el hash ya no lo pone el servidor: viene dentro de algo
    firmado, asi que comprobarlo si significa algo.

    Detecta tanto una descarga a medias como un paquete cambiado por
    otro.
    """

    esperado = manifiesto.get("sha256")
    tamano = manifiesto.get("size")

    if tamano is not None and len(contenido) != tamano:
        raise ReleaseError(
            f"El paquete mide {len(contenido)} bytes y deberia medir "
            f"{tamano}: la descarga quedo incompleta"
        )

    real = sha256_hex(contenido)

    if real != esperado:
        raise ReleaseError(
            "El contenido del paquete no coincide con el firmado"
        )

    return True
