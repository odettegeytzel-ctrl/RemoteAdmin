"""
Actualizacion automatica del Agent.

Lo hace el SERVICIO, nunca el ayudante: el ayudante corre en la sesion
del usuario, no conoce el token y no debe poder cambiar el programa
que corre como SYSTEM.

El orden de las comprobaciones no es casual. Cada paso solo se da si
el anterior salio bien, y el mas barato va primero:

    1. pedir el manifiesto          (unos cientos de bytes)
    2. comprobar su FIRMA           sin firma valida, no se sigue
    3. comprobar que es POSTERIOR   ni igual ni anterior
    4. descargar el paquete
    5. comprobar hash y tamano      ya vienen dentro de lo firmado
    6. mirar que lleva dentro       antes de copiar nada
    7. respaldar la version actual
    8. instalar
    9. si algo falla, volver atras

Comprobar la firma antes de descargar evita traerse un paquete que de
todas formas se iba a rechazar, y comprobar la version antes de
descargar evita bajar megas para no instalarlos.

Lo que esto NUNCA toca:

    config\\identity.json       la identidad del equipo
    recordings\\                las grabaciones locales
    logs\\                      el historial
    .env                       la configuracion

Se reemplaza el PROGRAMA y nada mas. Por eso una actualizacion no
vuelve a pedir la credencial de alta ni cambia el device_id: esos
datos no estan en lo que se sustituye.
"""

import json
import os
import shutil
import tempfile
import threading
import time
import zipfile


try:
    from agent import release
    from agent import version as versionado

except ImportError:
    import release
    import version as versionado


# Cada cuanto se pregunta si hay version nueva. Seis horas: una
# actualizacion no es urgente, y preguntar cada minuto multiplicado
# por todos los equipos administrados es trafico para nada.
CHECK_INTERVAL_SECONDS = 6 * 60 * 60

# Tope de lo que se admite descargar. El paquete real ronda los 60 KB;
# el limite esta para que un servidor que responda cualquier cosa no
# llene el disco del equipo.
MAX_PACKAGE_BYTES = 64 * 1024 * 1024

DOWNLOAD_TIMEOUT = 120

# Lo que un paquete tiene que traer para considerarse un Agent. Se
# comprueba ANTES de tocar la instalacion: un ZIP firmado pero vacio
# dejaria el equipo sin programa.
REQUIRED_MEMBERS = (
    "agent/agent.py",
    "agent/version.py"
)


class UpdateError(RuntimeError):
    """La actualizacion no se pudo completar, con un motivo legible."""


# Una actualizacion a la vez. Si el hilo periodico y una peticion del
# servidor coincidieran, dos procesos copiando sobre los mismos
# archivos dejarian una instalacion a medias.
_cerrojo = threading.Lock()


def hay_actualizacion_en_curso():
    return _cerrojo.locked()


# ==============================
# CONSULTA
# ==============================

def fetch_manifest(server_url, cabeceras, verify, requests_module=None):
    """
    Pide el manifiesto publicado y comprueba su firma.

    Devuelve (manifiesto, firma) o None si el servidor no publica
    ninguna version.
    """

    peticiones = requests_module

    if peticiones is None:
        import requests as peticiones

    respuesta = peticiones.get(
        f"{server_url}/api/agent/version",
        headers=cabeceras,
        timeout=30,
        verify=verify
    )

    respuesta.raise_for_status()

    datos = respuesta.json()

    manifiesto = datos.get("manifest")
    firma = datos.get("signature")

    if not manifiesto:
        return None

    # La firma se comprueba aqui, antes que nada. A partir de este
    # punto el manifiesto se puede creer; antes, no.
    release.verify_manifest(manifiesto, firma)

    return manifiesto, firma


def decide(manifiesto, instalada):
    """
    Dice si hay que actualizar y por que. No descarga nada.

    Devuelve (debe_actualizar, motivo).
    """

    disponible = manifiesto.get("version")

    if not versionado.is_valid(disponible):
        raise UpdateError(
            f"La version publicada no es valida: {disponible!r}"
        )

    if disponible == instalada:
        return False, f"ya esta en la version {instalada}"

    if not versionado.is_newer(disponible, instalada):
        # Volver atras reintroduce fallos ya corregidos, y seria la
        # via mas comoda para atacar un equipo si alguien lograse
        # publicar una version antigua firmada.
        return False, (
            f"la version publicada ({disponible}) es anterior a la "
            f"instalada ({instalada}): no se instala"
        )

    return True, f"hay version nueva: {instalada} -> {disponible}"


# ==============================
# DESCARGA
# ==============================

def download_package(server_url, manifiesto, cabeceras, verify,
                     requests_module=None):
    """
    Descarga el paquete y comprueba que es el del manifiesto firmado.

    Se descarga a trozos y se corta en cuanto se pasa del limite: asi
    un servidor que mande un archivo sin fin no llena el disco.
    """

    peticiones = requests_module

    if peticiones is None:
        import requests as peticiones

    version = manifiesto["version"]

    respuesta = peticiones.get(
        f"{server_url}/api/agent/package",
        params={"version": version},
        headers=cabeceras,
        timeout=DOWNLOAD_TIMEOUT,
        verify=verify,
        stream=True
    )

    respuesta.raise_for_status()

    trozos = []
    total = 0

    for trozo in respuesta.iter_content(chunk_size=64 * 1024):

        if not trozo:
            continue

        total += len(trozo)

        if total > MAX_PACKAGE_BYTES:
            raise UpdateError(
                "El paquete excede el tamano maximo admitido"
            )

        trozos.append(trozo)

    contenido = b"".join(trozos)

    # El hash y el tamano vienen DENTRO del manifiesto firmado, asi
    # que comprobarlos aqui si demuestra algo. Esto detecta tanto una
    # descarga cortada como un paquete cambiado por otro.
    release.verify_package(contenido, manifiesto)

    return contenido


# ==============================
# EL PAQUETE
# ==============================

def inspect_package(ruta_zip):
    """
    Mira que trae el paquete antes de tocar la instalacion.

    Dos cosas:

      que este completo, porque un ZIP firmado pero vacio dejaria el
      equipo sin programa;

      que no intente escribir fuera de su sitio. Un nombre como
      '..\\..\\Windows\\System32\\algo' convertiria la actualizacion en
      la sobrescritura de cualquier archivo del sistema. Que venga
      firmado no exime de comprobarlo: la firma dice de quien viene,
      no que sea correcto.
    """

    with zipfile.ZipFile(ruta_zip) as paquete:

        danado = paquete.testzip()

        if danado is not None:
            raise UpdateError(f"El paquete esta corrupto: {danado}")

        nombres = paquete.namelist()

        for nombre in nombres:

            if nombre.startswith("/") or nombre.startswith("\\"):
                raise UpdateError(f"Ruta absoluta en el paquete: {nombre}")

            if ".." in nombre.replace("\\", "/").split("/"):
                raise UpdateError(f"Ruta que sale de la carpeta: {nombre}")

            if len(nombre) > 2 and nombre[1] == ":":
                raise UpdateError(f"Ruta con unidad en el paquete: {nombre}")

        faltan = [m for m in REQUIRED_MEMBERS if m not in nombres]

        if faltan:
            raise UpdateError(
                f"Al paquete le faltan archivos imprescindibles: {faltan}"
            )

        return nombres


def version_del_paquete(carpeta):
    """
    Lee la version que declara el paquete ya extraido.

    Se compara con la del manifiesto: si no coinciden, alguien firmo
    un manifiesto que no describe lo que hay dentro.
    """

    ruta = os.path.join(carpeta, "agent", "version.py")

    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            texto = archivo.read()

    except OSError as problema:
        raise UpdateError(f"No se pudo leer la version del paquete: {problema}")

    for linea in texto.split("\n"):

        if linea.startswith("AGENT_VERSION"):

            valor = linea.split("=", 1)[1].strip().strip('"').strip("'")

            if not versionado.is_valid(valor):
                raise UpdateError(
                    f"El paquete declara una version invalida: {valor!r}"
                )

            return valor

    raise UpdateError("El paquete no declara ninguna version")


# ==============================
# INSTALACION
# ==============================

# Lo que se reemplaza al actualizar. Todo lo demas de la carpeta de
# datos se queda como esta: identidad, grabaciones, registros y
# configuracion no son parte del programa.
REPLACEABLE = ("agent", "installer")

PRESERVED = ("config", "recordings", "logs", ".env")


def install_package(contenido, install_dir, manifiesto, registrar=print):
    """
    Instala una version nueva, con vuelta atras si algo sale mal.

    La version anterior se guarda ENTERA antes de tocar nada, y se
    restaura ante cualquier fallo. Una actualizacion a medias deja un
    Agent que no arranca, y un equipo remoto que no arranca es un
    equipo perdido hasta que alguien vaya fisicamente.
    """

    temporal = tempfile.mkdtemp(prefix="remoteadmin-update-")

    respaldo = os.path.join(temporal, "respaldo")

    try:

        ruta_zip = os.path.join(temporal, "paquete.zip")

        with open(ruta_zip, "wb") as archivo:
            archivo.write(contenido)

        registrar("[update] Comprobando el contenido del paquete")

        inspect_package(ruta_zip)

        extraido = os.path.join(temporal, "nuevo")

        with zipfile.ZipFile(ruta_zip) as paquete:
            paquete.extractall(extraido)

        declarada = version_del_paquete(extraido)

        if declarada != manifiesto["version"]:
            raise UpdateError(
                f"El paquete declara la version {declarada} pero el "
                f"manifiesto dice {manifiesto['version']}"
            )

        # --- Respaldo ---
        os.makedirs(respaldo, exist_ok=True)

        guardados = []

        for parte in REPLACEABLE:

            origen = os.path.join(install_dir, parte)

            if os.path.isdir(origen):
                shutil.copytree(origen, os.path.join(respaldo, parte))
                guardados.append(parte)

        registrar(f"[update] Version anterior respaldada: {guardados}")

        # --- Instalacion ---
        try:

            for parte in REPLACEABLE:

                nuevo = os.path.join(extraido, parte)

                if not os.path.isdir(nuevo):
                    continue

                destino = os.path.join(install_dir, parte)

                if os.path.isdir(destino):
                    shutil.rmtree(destino)

                shutil.copytree(nuevo, destino)

            registrar(f"[update] Instalada la version {declarada}")

        except Exception as problema:

            registrar(f"[update] Fallo la instalacion: {problema}")

            rollback(respaldo, install_dir, guardados, registrar)

            raise UpdateError(f"Instalacion fallida: {problema}")

        # Comprobacion final: lo instalado tiene que ser legible y
        # declarar la version esperada. Si no, se vuelve atras.
        try:
            instalada = version_del_paquete(install_dir)

        except UpdateError as problema:

            registrar(f"[update] La instalacion no es utilizable: {problema}")

            rollback(respaldo, install_dir, guardados, registrar)

            raise

        if instalada != declarada:

            registrar(
                f"[update] Tras instalar, la version es {instalada} y "
                f"deberia ser {declarada}"
            )

            rollback(respaldo, install_dir, guardados, registrar)

            raise UpdateError("La version instalada no es la esperada")

        return declarada

    finally:

        shutil.rmtree(temporal, ignore_errors=True)


def rollback(respaldo, install_dir, partes, registrar=print):
    """
    Devuelve la instalacion a como estaba.

    Se intentan TODAS las partes aunque alguna falle: recuperar la
    mitad es mejor que abandonar a la primera. Lo que no se pudo
    restaurar se anota.
    """

    registrar("[update] Volviendo a la version anterior")

    problemas = []

    for parte in partes:

        origen = os.path.join(respaldo, parte)

        if not os.path.isdir(origen):
            continue

        destino = os.path.join(install_dir, parte)

        try:

            if os.path.isdir(destino):
                shutil.rmtree(destino)

            shutil.copytree(origen, destino)

        except Exception as problema:
            problemas.append(f"{parte}: {problema}")

    if problemas:
        registrar(f"[update] La vuelta atras dejo problemas: {problemas}")
        return False

    registrar("[update] Version anterior restaurada")

    return True


# ==============================
# EL CICLO COMPLETO
# ==============================

def check_and_update(server_url, cabeceras, verify, install_dir,
                     instalada=None, registrar=print,
                     requests_module=None, reiniciar=None):
    """
    Un ciclo entero: consultar, decidir y, si toca, actualizar.

    Devuelve la version instalada despues de todo, o None si no se
    actualizo. No lanza excepciones por un fallo de red o una firma
    mala: lo registra y devuelve None, porque el Agent tiene que
    seguir funcionando pase lo que pase con la actualizacion.
    """

    actual = instalada or versionado.AGENT_VERSION

    if not _cerrojo.acquire(blocking=False):
        registrar("[update] Ya hay una actualizacion en curso")
        return None

    try:

        registrar(f"[update] Version instalada: {actual}")

        try:
            resultado = fetch_manifest(
                server_url, cabeceras, verify, requests_module
            )

        except release.SignatureError as problema:

            # Se separa a proposito: esto no es un fallo de red, es un
            # paquete que no viene de quien dice venir.
            registrar(f"[update] FIRMA RECHAZADA: {problema}")
            return None

        except Exception as problema:
            registrar(f"[update] No se pudo consultar: {problema}")
            return None

        if resultado is None:
            registrar("[update] El servidor no publica ninguna version")
            return None

        manifiesto, _ = resultado

        registrar(f"[update] Version disponible: {manifiesto.get('version')}")

        try:
            actualizar, motivo = decide(manifiesto, actual)

        except UpdateError as problema:
            registrar(f"[update] {problema}")
            return None

        registrar(f"[update] {motivo}")

        if not actualizar:
            return None

        registrar("[update] Descargando")

        try:
            contenido = download_package(
                server_url, manifiesto, cabeceras, verify, requests_module
            )

        except release.ReleaseError as problema:
            registrar(f"[update] Paquete rechazado: {problema}")
            return None

        except Exception as problema:
            registrar(f"[update] Fallo la descarga: {problema}")
            return None

        registrar(f"[update] Descargados {len(contenido)} bytes, validados")

        try:
            nueva = install_package(
                contenido, install_dir, manifiesto, registrar
            )

        except Exception as problema:
            registrar(f"[update] No se actualizo: {problema}")
            return None

        registrar(f"[update] Actualizacion completada: {actual} -> {nueva}")

        if reiniciar is not None:

            registrar("[update] Reiniciando el servicio")

            try:
                reiniciar()

            except Exception as problema:
                registrar(f"[update] No se pudo reiniciar: {problema}")

        return nueva

    finally:
        _cerrojo.release()
