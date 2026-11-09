"""
Paquete de instalacion del Agent para Windows.

Lo que se descarga desde el panel es un ZIP armado aqui, en memoria, a
partir de una LISTA FIJA de archivos del proyecto. No se sirve ninguna
ruta que venga de fuera: el usuario no elige que se empaqueta, asi que
no hay recorrido de directorios que impedir —sencillamente no hay por
donde pedir otra cosa.

Dentro del ZIP va:

    agent\\*.py                      el Agent
    requirements-*.txt               sus dependencias
    installer\\install-agent.ps1      el instalador
    installer\\README.md
    .env                             SOLO la direccion del servidor

Lo que NO va, y por eso la lista es blanca y no negra: la base de
datos, los certificados, las claves, el .env del servidor ni nada de
backend/. Una lista negra se olvida de un archivo nuevo; una lista
blanca no puede.

La CREDENCIAL DE ALTA no se mete en el paquete. Un ZIP se reenvia por
correo, se deja en una carpeta compartida y se olvida en Descargas; la
credencial se escribe en el equipo en el momento de instalar y se
revoca despues. Esa separacion es deliberada.
"""

import io
import os
import zipfile

from backend import organizations


# Raiz del proyecto (carpeta superior a backend\)
PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)


# Archivos que viajan en el paquete, con su ruta DENTRO del ZIP.
# Cualquier cosa que no este aqui no se empaqueta.
PACKAGE_FILES = (
    "agent/__init__.py",
    "agent/agent.py",

    # El portapapeles compartido. Por la misma razon que
    # maintenance.py: helper.py lo importa al arrancar, y el
    # actualizador reemplaza la carpeta agent ENTERA. Si faltara,
    # el ayudante no arrancaria en ningun equipo actualizado.
    "agent/clipboard.py",

    "agent/commands.py",
    "agent/helper.py",
    "agent/inventory.py",
    "agent/ipc.py",

    # El bloqueo del teclado y el raton fisicos. Tambien lo importa
    # helper.py al arrancar.
    "agent/lockinput.py",

    # El aviso de mantenimiento. Tiene que estar aqui aunque sea una
    # ventana y no parezca parte del "programa": helper.py lo importa
    # al arrancar.
    #
    # Sin el, una actualizacion dejaria la carpeta agent con un
    # helper.py que importa un modulo que no existe —el actualizador
    # reemplaza la carpeta entera— y el ayudante no arrancaria en
    # ningun equipo. Se perderian la pantalla en vivo, la grabacion y
    # el raton, que es todo lo que necesita escritorio.
    "agent/maintenance.py",

    # Quien sostiene la captura: la grabacion manual y la continua
    # comparten un solo grabador y este modulo lleva la cuenta.
    # agent.py lo importa al arrancar.
    #
    # Igual que maintenance.py, tiene que estar aqui aunque no
    # parezca "el programa": el actualizador reemplaza la carpeta
    # agent ENTERA, asi que un modulo que falte deja un agent.py que
    # no se puede importar y un equipo que no arranca.
    "agent/owners.py",

    # Unir los trozos de un dia en un solo video. agent.py lo importa
    # al arrancar, asi que es de la misma familia que owners.py y
    # maintenance.py: si falta, el Agent no se puede importar y el
    # equipo se queda sin arrancar despues de actualizar.
    "agent/jornada.py",

    "agent/paths.py",
    "agent/release.py",
    "agent/power.py",
    "agent/recorder.py",
    "agent/scheduler.py",
    "agent/storage.py",
    "agent/updater.py",
    "agent/version.py",
    "requirements-base.txt",
    "requirements-agent-windows.txt",
    "installer/install-agent.ps1",
    "installer/uninstall-agent.ps1",
    "installer/README.md"
)


# Nombre del archivo que descarga el navegador. Fijo: nunca se
# construye con texto que venga del usuario ni del nombre de la
# organizacion, que podria traer barras o acentos.
PACKAGE_FILENAME = "RemoteAdmin-Agent-Windows.zip"


def modulos_del_agente_fuera_de_la_lista():
    """
    Modulos de agent/ que existen en disco y NO van en el paquete.

    Por que existe esto
    -------------------

    PACKAGE_FILES es una lista a mano, y el actualizador reemplaza la
    carpeta agent ENTERA. Un modulo nuevo que no se anada aqui no
    viaja: el equipo actualiza, agent.py lo importa, revienta, y se
    queda sin arrancar. En los siete a la vez, porque todos reciben
    el mismo paquete firmado.

    Y no es un peligro teorico. Al firmar la 1.15.0, jornada.py se
    quedo fuera: el paquete pesaba lo normal, la firma era correcta,
    el manifiesto decia la verdad, y habria dejado la flota entera
    sin Agent. Lo que lo detecto fue mirar dentro del zip a mano.

    Tres comentarios de esta misma lista -clipboard, maintenance,
    owners- avisan cada uno por su cuenta de este mismo riesgo. Tres
    avisos escritos y el cuarto caso ocurrio igual, porque un aviso
    no comprueba nada. Esto si lo comprueba.

    Devuelve la lista de rutas relativas que faltan, vacia si esta
    todo.
    """

    carpeta = os.path.join(PROJECT_DIR, "agent")

    try:
        nombres = os.listdir(carpeta)

    except OSError:
        return []

    en_la_lista = set(PACKAGE_FILES)

    faltan = []

    for nombre in sorted(nombres):

        if not nombre.endswith(".py"):
            continue

        relativo = "agent/" + nombre

        if relativo not in en_la_lista:
            faltan.append(relativo)

    return faltan


class PackagingError(RuntimeError):
    """No se pudo armar el paquete, con un motivo legible."""


def public_server_url(organizacion=None):
    """
    Direccion publica a la que debe apuntar el Agent instalado.

    El orden importa:

      1. el server_url de la organizacion, si lo tiene. Es lo que
         manda en self-hosted, donde cada cliente corre su servidor;
      2. REMOTEADMIN_PUBLIC_URL del entorno del servidor, que es la
         direccion publica del despliegue cloud;
      3. nada.

    Devuelve None si no hay ninguna configurada, y entonces el panel
    debe decirlo en vez de entregar un instalador que apunte al
    propio equipo: un Agent configurado contra localhost parece
    instalado y no conecta nunca, que es la peor de las averias.
    """

    if organizacion:

        ficha = organizations.get_organization(organizacion)

        if ficha and ficha.get("server_url"):
            return ficha["server_url"].rstrip("/")

    publica = os.getenv("REMOTEADMIN_PUBLIC_URL", "").strip().rstrip("/")

    if publica.startswith(("http://", "https://")):
        return publica

    return None


def _env_para_el_equipo(server_url):
    """
    .env que se instala en el equipo administrado.

    Lleva la direccion del servidor y NADA MAS. AGENT_TOKEN se deja
    vacio a proposito: lo escribe el instalador con la credencial de
    alta que se teclee en ese momento, para que el secreto no viaje
    dentro de un archivo descargable.
    """

    return (
        "# Configuracion del Agent de RemoteAdmin.\n"
        "#\n"
        "# REMOTEADMIN_SERVER lo rellena el panel al generar este\n"
        "# paquete. No lo cambies a mano salvo que el servidor cambie\n"
        "# de direccion.\n"
        "#\n"
        "# AGENT_TOKEN es la credencial de ALTA de la organizacion\n"
        "# (empieza por rae_). La escribe el instalador y solo sirve\n"
        "# para el primer registro: despues el Agent usa su token\n"
        "# individual, que el servidor le asigna y que se guarda en\n"
        "# config\\identity.json. La credencial de alta puede\n"
        "# revocarse en cuanto el equipo aparezca en el panel.\n"
        "\n"
        f"REMOTEADMIN_SERVER={server_url}\n"
        "AGENT_TOKEN=\n"
        "REMOTEADMIN_CA_CERT=\n"
    )


def build_agent_package(organizacion=None):
    """
    Arma el ZIP en memoria y devuelve sus bytes.

    En memoria y no en disco: no deja un archivo que alguien pueda
    encontrar despues, y evita tener que limpiar temporales.
    """

    server_url = public_server_url(organizacion)

    if not server_url:
        raise PackagingError(
            "No hay una direccion publica configurada para el "
            "servidor. Configure REMOTEADMIN_PUBLIC_URL en el "
            "servidor, o la direccion del servidor en la ficha de la "
            "organizacion, antes de descargar el instalador."
        )

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as paquete:

        for relativo in PACKAGE_FILES:

            origen = os.path.join(PROJECT_DIR, *relativo.split("/"))

            if not os.path.isfile(origen):
                raise PackagingError(
                    f"Falta un archivo del paquete: {relativo}"
                )

            paquete.write(origen, arcname=relativo)

        paquete.writestr(".env", _env_para_el_equipo(server_url))

    return buffer.getvalue()
