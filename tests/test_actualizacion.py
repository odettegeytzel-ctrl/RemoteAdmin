"""
Actualizacion automatica del Agent: firma, instalacion y vuelta atras.

    .venv\\Scripts\\python tests/test_actualizacion.py

Una actualizacion es, por definicion, ejecutar codigo nuevo en todos
los equipos administrados a la vez. Por eso la mayor parte de esta
suite no comprueba que la actualizacion funcione, sino que NO ocurra
cuando no debe: sin firma valida, con una version anterior, con un
paquete cambiado o incompleto.

El par de claves de las pruebas se genera aqui mismo y muere con el
proceso. La clave privada de verdad no esta en el repositorio ni se
usa nunca en una prueba.

Base temporal y datos inventados. No se toca produccion.
"""

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))
sys.path.insert(0, str(RAIZ / "agent"))

import users_harness as h

import backend.agent_releases as publicacion
import backend.database as database
import backend.enrollment as enrollment
import backend.main as servidor
import backend.organizations as orgs
import backend.packaging as packaging
import backend.users as users

import release
import updater
import version as versionado

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey
)


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
ACTUALIZADOR = io.open(RAIZ / "agent" / "updater.py",
                       encoding="utf-8").read()
FIRMA = io.open(RAIZ / "agent" / "release.py", encoding="utf-8").read()
HERRAMIENTA = io.open(RAIZ / "tools" / "sign_agent_release.py",
                      encoding="utf-8").read()

IDENTIDAD = {
    "device_id": "774f538e82d2537d",
    "agent_token": "token-individual-que-no-debe-perderse"
}

escenario = {}


# ==============================
# Claves de prueba
# ==============================

_privada = Ed25519PrivateKey.generate()

_pem = _privada.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

_publica = base64.b64encode(
    _privada.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
).decode("ascii")

release.AGENT_UPDATE_PUBLIC_KEY = _publica


def paquete(version, completo=True, extra=None):
    """Un ZIP como el que produce la herramienta de publicacion."""

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:

        if completo:
            z.writestr("agent/agent.py", f"# Agent {version}\n")
            z.writestr("agent/version.py",
                       f'AGENT_VERSION = "{version}"\n')
            z.writestr("installer/install-agent.ps1", "# instalador\n")

        if extra:
            z.writestr(extra, "contenido")

    return buffer.getvalue()


def firmar(version, contenido=None, clave=None):
    datos = contenido if contenido is not None else paquete(version)
    manifiesto = release.build_manifest(version, datos)
    firma = release.sign_manifest(manifiesto, clave or _pem)
    return manifiesto, firma, datos


def falla(funcion, tipo=Exception):
    try:
        funcion()
        return False
    except tipo:
        return True


def instalacion_de_prueba(version="1.1.0"):
    """Una instalacion completa en una carpeta temporal."""

    base = tempfile.mkdtemp(prefix="remoteadmin-prueba-")

    for carpeta in ("agent", "installer", "config", "logs",
                    os.path.join("recordings", "equipo1")):
        os.makedirs(os.path.join(base, carpeta), exist_ok=True)

    with open(os.path.join(base, "agent", "agent.py"), "w") as a:
        a.write(f"# Agent {version}\n")

    with open(os.path.join(base, "agent", "version.py"), "w") as a:
        a.write(f'AGENT_VERSION = "{version}"\n')

    with open(os.path.join(base, "config", "identity.json"), "w") as a:
        json.dump(IDENTIDAD, a)

    with open(os.path.join(base, "recordings", "equipo1",
                           "rec_1.mp4"), "w") as a:
        a.write("video")

    with open(os.path.join(base, ".env"), "w") as a:
        a.write(
            "REMOTEADMIN_SERVER=https://panel.ejemplo.com\n"
            "AGENT_TOKEN=\n"
        )

    with open(os.path.join(base, "logs", "agent-service.log"), "w") as a:
        a.write("historial que no debe perderse\n")

    return base


def estado(base):
    with open(os.path.join(base, "config", "identity.json")) as a:
        identidad = json.load(a)

    with open(os.path.join(base, "agent", "version.py")) as a:
        version = a.read().strip()

    with open(os.path.join(base, ".env")) as a:
        env = a.read()

    return {
        "identidad": identidad,
        "version": version,
        "env": env,
        "grabacion": os.path.isfile(
            os.path.join(base, "recordings", "equipo1", "rec_1.mp4")
        ),
        "log": os.path.isfile(
            os.path.join(base, "logs", "agent-service.log")
        )
    }


# ==============================
# 1-4. Version
# ==============================

def test_el_agent_sabe_su_version():

    comprobar("Hay una version declarada",
              versionado.is_valid(versionado.AGENT_VERSION),
              str(versionado.AGENT_VERSION))

    comprobar("El Agent la anuncia al arrancar",
              "versionado.AGENT_VERSION" in AGENTE
              and 'print(f"Version:' in AGENTE)

    comprobar("Y viaja en el paquete de instalacion",
              "agent/version.py" in packaging.PACKAGE_FILES)


def test_las_versiones_se_comparan_como_numeros():
    """'1.10.0' es posterior a '1.9.0', aunque alfabeticamente no."""

    comprobar("1.10.0 es posterior a 1.9.0",
              versionado.is_newer("1.10.0", "1.9.0"))

    comprobar("2.0.0 es posterior a 1.99.99",
              versionado.is_newer("2.0.0", "1.99.99"))

    comprobar("1.2.3 no es posterior a si misma",
              not versionado.is_newer("1.2.3", "1.2.3"))

    comprobar("1.2.3 no es posterior a 1.2.4",
              not versionado.is_newer("1.2.3", "1.2.4"))


def test_una_version_mal_formada_se_rechaza():

    for mala in ("1.2", "v1.2.3", "1.2.3-beta", "", "abc", "1.2.3.4",
                 None, 123):

        comprobar(f"'{mala}' no es una version valida",
                  not versionado.is_valid(mala))


def test_no_se_actualiza_si_la_version_es_la_misma():

    manifiesto, _, _ = firmar("1.1.0")

    actualizar, motivo = updater.decide(manifiesto, "1.1.0")

    comprobar("Con la misma version no se actualiza", not actualizar)

    comprobar("Y se dice por que", "ya esta en la version" in motivo)


def test_se_actualiza_si_hay_version_nueva():

    manifiesto, _, _ = firmar("1.2.0")

    actualizar, motivo = updater.decide(manifiesto, "1.1.0")

    comprobar("Con version nueva si se actualiza", actualizar)

    comprobar("Y se dice de cual a cual",
              "1.1.0 -> 1.2.0" in motivo, motivo)


def test_se_rechaza_volver_a_una_version_anterior():
    """
    Un downgrade reintroduce fallos ya corregidos, y seria la via mas
    comoda de atacar un equipo si alguien lograse publicar una
    version antigua firmada.
    """

    for anterior in ("1.0.0", "1.0.9", "0.9.9"):

        manifiesto, _, _ = firmar(anterior)

        actualizar, motivo = updater.decide(manifiesto, "1.1.0")

        comprobar(f"No se baja a {anterior}", not actualizar)

        comprobar(f"Y se explica ({anterior})",
                  "anterior a la instalada" in motivo)


def test_una_version_publicada_invalida_se_rechaza():

    manifiesto = {"version": "no-es-una-version", "sha256": "x", "size": 1}

    comprobar("Una version publicada invalida no actualiza",
              falla(lambda: updater.decide(manifiesto, "1.1.0"),
                    updater.UpdateError))


# ==============================
# 5-7. Firma e integridad
# ==============================

def test_una_firma_valida_se_acepta():

    manifiesto, firma, datos = firmar("1.2.0")

    comprobar("La firma se verifica",
              release.verify_manifest(manifiesto, firma))

    comprobar("Y el paquete coincide con lo firmado",
              release.verify_package(datos, manifiesto))


def test_una_firma_invalida_se_rechaza():

    manifiesto, firma, datos = firmar("1.2.0")

    # Manifiesto cambiado despues de firmar
    alterado = dict(manifiesto)
    alterado["version"] = "9.9.9"

    comprobar("Un manifiesto alterado no pasa",
              falla(lambda: release.verify_manifest(alterado, firma),
                    release.SignatureError))

    # Firmado con otra clave: es el caso de un servidor comprometido
    otra = Ed25519PrivateKey.generate()

    otra_pem = otra.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )

    comprobar("Otra clave no sirve",
              falla(
                  lambda: release.verify_manifest(
                      manifiesto,
                      release.sign_manifest(manifiesto, otra_pem)
                  ),
                  release.SignatureError
              ))

    comprobar("Sin firma tampoco",
              falla(lambda: release.verify_manifest(manifiesto, ""),
                    release.SignatureError))

    comprobar("Una firma mal formada tampoco",
              falla(lambda: release.verify_manifest(manifiesto, "no!!"),
                    release.SignatureError))

    comprobar("Ni una firma de otro tamano",
              falla(lambda: release.verify_manifest(
                  manifiesto, base64.b64encode(b"corta").decode()
              ), release.SignatureError))


def test_sin_clave_publica_no_se_actualiza_nada():
    """Preferible quedarse quieto antes que instalar sin comprobar."""

    manifiesto, firma, _ = firmar("1.2.0")

    guardada = release.AGENT_UPDATE_PUBLIC_KEY

    release.AGENT_UPDATE_PUBLIC_KEY = ""

    try:
        comprobar("Sin clave configurada, se rechaza",
                  falla(lambda: release.verify_manifest(manifiesto, firma),
                        release.SignatureError))

    finally:
        release.AGENT_UPDATE_PUBLIC_KEY = guardada

    comprobar("Y el Agent no trae ninguna clave privada",
              "PRIVATE KEY" not in FIRMA
              and "private_bytes" not in FIRMA.split(
                  "def sign_manifest", 1
              )[0])


def test_un_paquete_corrupto_se_rechaza():

    manifiesto, firma, datos = firmar("1.2.0")

    estropeado = bytearray(datos)
    estropeado[len(estropeado) // 2] ^= 0xFF

    comprobar("Un byte cambiado se detecta",
              falla(lambda: release.verify_package(bytes(estropeado),
                                                   manifiesto),
                    release.ReleaseError))

    comprobar("Un paquete distinto tambien",
              falla(lambda: release.verify_package(paquete("1.2.0", False),
                                                   manifiesto),
                    release.ReleaseError))


def test_una_descarga_interrumpida_se_rechaza():

    manifiesto, firma, datos = firmar("1.2.0")

    for recorte in (1, 20, len(datos) // 2):

        comprobar(f"Faltando {recorte} bytes se detecta",
                  falla(lambda: release.verify_package(
                      datos[:-recorte], manifiesto
                  ), release.ReleaseError))

    comprobar("Y se dice que quedo incompleta",
              "incompleta" in str(
                  _motivo(lambda: release.verify_package(
                      datos[:-10], manifiesto
                  ))
              ))


def _motivo(funcion):
    try:
        funcion()
        return ""
    except Exception as error:
        return error


def test_el_hash_no_se_cree_por_si_solo():
    """
    El hash viene DENTRO de lo firmado, no suelto.

    Un SHA-256 que entrega el mismo servidor del que se descarga no
    demuestra nada: quien cambie el paquete cambia tambien el hash.
    """

    comprobar("El hash forma parte del manifiesto firmado",
              "sha256" in release.MANIFEST_FIELDS)

    comprobar("Y el tamano tambien",
              "size" in release.MANIFEST_FIELDS)

    comprobar("La version igualmente",
              "version" in release.MANIFEST_FIELDS)

    # Cambiar el hash del manifiesto invalida la firma
    manifiesto, firma, datos = firmar("1.2.0")

    mentiroso = dict(manifiesto)
    mentiroso["sha256"] = release.sha256_hex(b"otra cosa")

    comprobar("Cambiar el hash invalida la firma",
              falla(lambda: release.verify_manifest(mentiroso, firma),
                    release.SignatureError))


def test_el_manifiesto_firmado_es_estable():
    """Si el texto firmado cambiara al releerlo, nada verificaria."""

    manifiesto = {"version": "1.2.0", "sha256": "abc", "size": 10}

    otro_orden = {"size": 10, "version": "1.2.0", "sha256": "abc"}

    comprobar("El orden de los campos no altera lo firmado",
              release.canonical_manifest(manifiesto)
              == release.canonical_manifest(otro_orden))

    # Un campo extra no entra en lo firmado: el servidor puede anadir
    # notas sin invalidar firmas, y a la vez no puede colar nada
    # dentro de lo que se da por verificado.
    con_extra = dict(manifiesto)
    con_extra["notas"] = "texto cualquiera"

    comprobar("Un campo extra no cambia lo firmado",
              release.canonical_manifest(con_extra)
              == release.canonical_manifest(manifiesto))

    comprobar("Un manifiesto incompleto se rechaza",
              falla(lambda: release.canonical_manifest({"version": "1.0.0"}),
                    release.ReleaseError))


# ==============================
# 8. Paquetes peligrosos
# ==============================

def test_un_paquete_no_puede_escribir_fuera_de_su_sitio():
    """
    Que venga firmado no exime de comprobarlo.

    La firma dice de QUIEN viene, no que el contenido sea correcto.
    """

    base = tempfile.mkdtemp(prefix="remoteadmin-rutas-")

    try:

        for nombre in ("../../Windows/System32/algo.dll",
                       "..\\..\\Windows\\evil.dll",
                       "/etc/passwd",
                       "\\Windows\\algo.dll",
                       "C:\\Windows\\algo.dll"):

            ruta = os.path.join(base, "p.zip")

            with open(ruta, "wb") as archivo:
                archivo.write(paquete("1.4.0", extra=nombre))

            comprobar(f"Se rechaza '{nombre}'",
                      falla(lambda: updater.inspect_package(ruta),
                            updater.UpdateError))

    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_un_paquete_incompleto_se_rechaza():
    """Un ZIP firmado pero vacio dejaria el equipo sin programa."""

    base = tempfile.mkdtemp(prefix="remoteadmin-vacio-")

    try:

        ruta = os.path.join(base, "vacio.zip")

        with open(ruta, "wb") as archivo:
            archivo.write(paquete("1.4.0", completo=False))

        comprobar("Un paquete sin el Agent se rechaza",
                  falla(lambda: updater.inspect_package(ruta),
                        updater.UpdateError))

        comprobar("Y se dice que falta",
                  "imprescindibles" in str(
                      _motivo(lambda: updater.inspect_package(ruta))
                  ))

    finally:
        shutil.rmtree(base, ignore_errors=True)


# ==============================
# 9-14. Instalacion, rollback y lo que se conserva
# ==============================

def test_una_actualizacion_conserva_todo_lo_del_equipo():

    base = instalacion_de_prueba("1.1.0")

    try:

        antes = estado(base)

        manifiesto, firma, datos = firmar("1.2.0")

        lineas = []

        nueva = updater.install_package(
            datos, base, manifiesto, registrar=lineas.append
        )

        comprobar("Se instala la version nueva", nueva == "1.2.0")

        despues = estado(base)

        comprobar("El programa cambia",
                  '"1.2.0"' in despues["version"])

        # Lo que NO debe cambiar
        comprobar("El device_id se conserva",
                  despues["identidad"]["device_id"]
                  == IDENTIDAD["device_id"])

        comprobar("El token individual se conserva",
                  despues["identidad"]["agent_token"]
                  == IDENTIDAD["agent_token"])

        comprobar("identity.json no se toca",
                  despues["identidad"] == antes["identidad"])

        comprobar("La configuracion se conserva",
                  despues["env"] == antes["env"])

        comprobar("Y sigue sin credencial de alta",
                  "AGENT_TOKEN=\n" in despues["env"])

        comprobar("Las grabaciones se conservan", despues["grabacion"])

        comprobar("Los registros se conservan", despues["log"])

    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_se_vuelve_atras_si_la_instalacion_queda_mal():
    """
    El caso que de verdad importa: una actualizacion a medias deja un
    Agent que no arranca, y un equipo remoto que no arranca es un
    equipo perdido hasta que alguien vaya fisicamente.
    """

    base = instalacion_de_prueba("1.1.0")

    try:

        respaldo = tempfile.mkdtemp(prefix="remoteadmin-respaldo-")

        # Se respalda la version buena
        shutil.copytree(
            os.path.join(base, "agent"),
            os.path.join(respaldo, "agent")
        )

        # Y se destroza la instalacion, como dejaria una copia a medias
        shutil.rmtree(os.path.join(base, "agent"))
        os.makedirs(os.path.join(base, "agent"))

        with open(os.path.join(base, "agent", "roto.txt"), "w") as a:
            a.write("instalacion a medias")

        comprobar("La instalacion queda inutilizable",
                  falla(lambda: updater.version_del_paquete(base),
                        updater.UpdateError))

        lineas = []

        recuperado = updater.rollback(
            respaldo, base, ["agent"], registrar=lineas.append
        )

        comprobar("La vuelta atras dice que fue bien", recuperado)

        comprobar("Y el Agent vuelve a su version",
                  updater.version_del_paquete(base) == "1.1.0")

        despues = estado(base)

        comprobar("Con su identidad intacta",
                  despues["identidad"] == IDENTIDAD)

        comprobar("Y sus grabaciones", despues["grabacion"])

        shutil.rmtree(respaldo, ignore_errors=True)

    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_un_paquete_que_miente_sobre_su_version_no_se_instala():

    base = instalacion_de_prueba("1.1.0")

    try:

        # Firmado de verdad, pero dentro declara otra version
        contenido = paquete("1.2.5")

        manifiesto = release.build_manifest("1.3.0", contenido)

        lineas = []

        comprobar("Se rechaza",
                  falla(lambda: updater.install_package(
                      contenido, base, manifiesto, registrar=lineas.append
                  ), updater.UpdateError))

        comprobar("Y el equipo se queda como estaba",
                  updater.version_del_paquete(base) == "1.1.0")

        comprobar("Con su identidad intacta",
                  estado(base)["identidad"] == IDENTIDAD)

    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_una_actualizacion_no_vuelve_a_pedir_enrollment():

    base = instalacion_de_prueba("1.1.0")

    try:

        manifiesto, firma, datos = firmar("1.2.0")

        updater.install_package(datos, base, manifiesto,
                                registrar=lambda m: None)

        despues = estado(base)

        comprobar("El token individual sigue ahi",
                  despues["identidad"]["agent_token"]
                  == IDENTIDAD["agent_token"])

        comprobar("No aparece ninguna credencial de alta",
                  "rae_" not in despues["env"])

        comprobar("Ni se pide",
                  "AGENT_TOKEN=\n" in despues["env"])

        # El actualizador no sabe nada de enrollment
        comprobar("El actualizador no menciona el alta",
                  "rae_" not in ACTUALIZADOR
                  and "enroll" not in ACTUALIZADOR.lower())

    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_lo_que_se_reemplaza_y_lo_que_no_esta_declarado():

    comprobar("Se reemplaza el programa",
              set(updater.REPLACEABLE) == {"agent", "installer"})

    comprobar("Y se conservan identidad, grabaciones, logs y config",
              set(updater.PRESERVED)
              == {"config", "recordings", "logs", ".env"})

    comprobar("Nada de lo conservado aparece en lo reemplazable",
              not set(updater.REPLACEABLE) & set(updater.PRESERVED))


# ==============================
# 15-17. Seguridad del proceso
# ==============================

def test_no_hay_descarga_y_ejecucion_arbitraria():

    comprobar("No se ejecuta nada de lo descargado",
              "exec(" not in ACTUALIZADOR
              and "eval(" not in ACTUALIZADOR)

    comprobar("Ni se lanza un script del paquete",
              "shell=True" not in ACTUALIZADOR)

    comprobar("No se desactiva la validacion TLS",
              "verify=False" not in ACTUALIZADOR
              and "CERT_NONE" not in ACTUALIZADOR
              and "_create_unverified_context" not in ACTUALIZADOR)

    comprobar("El verify se recibe de quien llama",
              "verify=verify" in ACTUALIZADOR)

    comprobar("Hay un limite de tamano",
              "MAX_PACKAGE_BYTES" in ACTUALIZADOR)

    comprobar("Y un limite de tiempo",
              "DOWNLOAD_TIMEOUT" in ACTUALIZADOR)


def test_el_token_no_aparece_en_los_registros():

    sospechosas = []

    for archivo, texto in (("updater.py", ACTUALIZADOR),
                           ("release.py", FIRMA)):

        for numero, linea in enumerate(texto.split(chr(10)), 1):

            if "print(" not in linea and "registrar(" not in linea:
                continue

            for peligro in ("token", "cabeceras", "headers"):

                if peligro in linea.lower():
                    sospechosas.append(f"{archivo}:{numero}")

    comprobar("Nada de lo que se registra lleva el token",
              not sospechosas, str(sospechosas))

    # Lo que SI debe registrarse
    for huella in ("Version instalada", "Version disponible",
                   "Descargando", "Comprobando el contenido",
                   "Instalada la version", "Volviendo a la version",
                   "Actualizacion completada"):

        comprobar(f"Se registra: '{huella}'",
                  huella in ACTUALIZADOR)

    comprobar("Una firma rechazada se registra aparte",
              "FIRMA RECHAZADA" in ACTUALIZADOR)


def test_no_hay_dos_actualizaciones_a_la_vez():

    comprobar("Hay un cerrojo", "_cerrojo" in ACTUALIZADOR)

    comprobar("Que no espera si ya hay una en curso",
              "acquire(blocking=False)" in ACTUALIZADOR)

    base = tempfile.mkdtemp(prefix="remoteadmin-cerrojo-")

    try:

        updater._cerrojo.acquire()

        comprobar("Se sabe que hay una en curso",
                  updater.hay_actualizacion_en_curso())

        resultado = updater.check_and_update(
            "https://no-se-usa", {}, True, base,
            instalada="1.1.0", registrar=lambda m: None
        )

        comprobar("La segunda no hace nada", resultado is None)

        updater._cerrojo.release()

        comprobar("Y el cerrojo queda libre",
                  not updater.hay_actualizacion_en_curso())

    finally:
        if updater._cerrojo.locked():
            updater._cerrojo.release()

        shutil.rmtree(base, ignore_errors=True)


def test_la_clave_privada_no_esta_en_el_repositorio():

    # No se busca "ningun .pem": los del TLS de desarrollo existen en
    # disco y estan en .gitignore, que es correcto. Lo que no puede
    # existir es una clave de FIRMA de actualizaciones.
    firmas = [
        ruta for ruta in RAIZ.glob("**/*.pem")
        if ".venv" not in str(ruta) and "signing" in ruta.name.lower()
    ]

    comprobar("No hay ninguna clave de firma en el proyecto",
              not firmas, str(firmas))

    # Y nada que contenga una clave privada puede estar versionado
    import subprocess

    versionados = subprocess.run(
        ["git", "ls-files"], cwd=str(RAIZ),
        capture_output=True, text=True
    ).stdout.split(chr(10))

    con_clave = []

    for relativo in versionados:

        if not relativo.strip().endswith((".pem", ".key")):
            continue

        ruta = RAIZ / relativo.strip()

        if not ruta.is_file():
            continue

        if "PRIVATE KEY" in io.open(
            ruta, encoding="utf-8", errors="ignore"
        ).read():
            con_clave.append(relativo)

    comprobar("Ninguna clave privada esta versionada",
              not con_clave, str(con_clave))

    comprobar("Y .gitignore cubre los .pem",
              "*.pem" in io.open(
                  RAIZ / ".gitignore", encoding="utf-8"
              ).read())

    comprobar("La herramienta la pide como argumento",
              "--clave" in HERRAMIENTA)

    comprobar("Y avisa de que no debe subirse",
              "No la subas al servidor" in HERRAMIENTA)

    comprobar("La clave privada no se imprime nunca",
              "print(pem" not in HERRAMIENTA
              and "print(privada" not in HERRAMIENTA)

    comprobar("El servidor no firma nada",
              "sign_manifest" not in io.open(
                  RAIZ / "backend" / "agent_releases.py", encoding="utf-8"
              ).read())


# ==============================
# La clave privada no llega a ninguna parte
# ==============================

def _contiene_una_clave_de_verdad(texto):
    """
    True solo si el texto lleva una clave privada que se puede cargar.

    Buscar las cadenas "BEGIN" y "PRIVATE KEY" marcaba cualquier
    archivo que las nombrara, incluida esta prueba. Lo que importa no
    es que se mencionen, sino que haya una clave utilizable.
    """

    import re

    from cryptography.hazmat.primitives import serialization

    bloques = re.findall(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
        r"-----END [A-Z ]*PRIVATE KEY-----",
        texto, re.DOTALL
    )

    for bloque in bloques:

        try:
            serialization.load_pem_private_key(
                bloque.encode("utf-8"), password=None
            )
            return True

        except Exception:
            # No se puede cargar: es un ejemplo o un texto suelto
            continue

    return False


def test_el_repositorio_no_contiene_ninguna_clave_de_firma():
    """
    Una clave de firma dentro del repositorio anula el sentido de
    firmar: acabaria en un commit, en el ZIP del Agent o en una copia
    al servidor, y cualquiera que llegara a ella podria publicar lo
    que quisiera en todos los equipos administrados.
    """

    import subprocess

    versionados = subprocess.run(
        ["git", "ls-files"], cwd=str(RAIZ),
        capture_output=True, text=True
    ).stdout.split(chr(10))

    con_clave = []

    for relativo in versionados:

        nombre = relativo.strip()

        if not nombre:
            continue

        ruta = RAIZ / nombre

        if not ruta.is_file():
            continue

        try:
            texto = io.open(ruta, encoding="utf-8", errors="ignore").read()

        except OSError:
            continue

        # Se comprueba si el contenido ES una clave, no si MENCIONA
        # una. Buscar el texto marcaba a esta misma prueba, que lleva
        # esas cadenas como patrones de busqueda.
        if _contiene_una_clave_de_verdad(texto):
            con_clave.append(nombre)

    comprobar("Ningun archivo versionado contiene una clave privada",
              not con_clave, str(con_clave))

    # Tampoco suelta en el arbol de trabajo
    sueltas = [
        str(ruta.relative_to(RAIZ))
        for ruta in RAIZ.glob("**/*")
        if ruta.is_file()
        and ".venv" not in str(ruta)
        and "signing" in ruta.name.lower()
    ]

    comprobar("No hay ninguna clave de firma en el arbol de trabajo",
              not sueltas, str(sueltas))

    gitignore = io.open(RAIZ / ".gitignore", encoding="utf-8").read()

    comprobar("Y .gitignore la excluye por nombre",
              "agent-signing.pem" in gitignore)

    comprobar("Igual que cualquier .pem",
              "*.pem" in gitignore)

    comprobar("Y los paquetes publicados",
              "releases/" in gitignore)


def test_el_paquete_del_agent_no_lleva_la_clave_privada():
    """Lo que se descarga a un equipo administrado no puede firmar."""

    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    contenido = packaging.build_agent_package()

    paquete_zip = zipfile.ZipFile(io.BytesIO(contenido))

    todo = b""

    for nombre in paquete_zip.namelist():
        todo += paquete_zip.read(nombre)

    texto = todo.decode("utf-8", errors="ignore")

    comprobar("No viaja ninguna clave privada",
              "PRIVATE KEY" not in texto)

    comprobar("Ni la herramienta de firma",
              not [n for n in paquete_zip.namelist() if "sign" in n.lower()])

    comprobar("Ni nada de tools/",
              not [n for n in paquete_zip.namelist()
                   if n.startswith("tools/")])

    # Lo que SI debe llevar: el modulo que comprueba firmas
    comprobar("Si lleva el verificador de firmas",
              "agent/release.py" in paquete_zip.namelist())

    # Y ese modulo no sabe firmar con nada que traiga dentro
    firma_en_el_paquete = paquete_zip.read("agent/release.py").decode("utf-8")

    comprobar("Que no trae ninguna clave privada dentro",
              "PRIVATE KEY" not in firma_en_el_paquete)

    comprobar("La clave publica es lo unico que se incorpora",
              "AGENT_UPDATE_PUBLIC_KEY" in firma_en_el_paquete)

    comprobar("Y firmar exige recibir la clave desde fuera",
              "def sign_manifest(manifiesto, clave_privada_pem)"
              in firma_en_el_paquete)


def test_el_servidor_solo_necesita_la_release_firmada():
    """
    El servidor guarda y sirve. No firma, no verifica y no necesita
    ninguna clave.

    Es lo que hace que un servidor comprometido no pueda instalar
    codigo en los equipos: lo peor que consigue es dejar de servir
    actualizaciones.
    """

    servidor_releases = io.open(
        RAIZ / "backend" / "agent_releases.py", encoding="utf-8"
    ).read()

    comprobar("El servidor no firma",
              "sign_manifest" not in servidor_releases
              and "PrivateKey" not in servidor_releases)

    # Se mira lo que el modulo HACE, no lo que dice: 'clave' aparece
    # en los comentarios que explican justamente que no tiene
    # ninguna. Para eso se analiza el arbol sintactico y se dejan
    # fuera comentarios y textos.
    import ast

    arbol = ast.parse(servidor_releases)

    importados = set()
    nombres = set()

    for nodo in ast.walk(arbol):

        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                importados.add(alias.name.split(".")[0])

        elif isinstance(nodo, ast.ImportFrom):
            if nodo.module:
                importados.add(nodo.module.split(".")[0])

        elif isinstance(nodo, ast.Name):
            nombres.add(nodo.id)

        elif isinstance(nodo, ast.Attribute):
            nombres.add(nodo.attr)

    comprobar("Solo importa lo justo para leer archivos",
              importados == {"json", "os"}, str(sorted(importados)))

    comprobar("No importa nada de criptografia",
              "cryptography" not in importados)

    # 'firma' como nombre de variable es legitimo: es el valor que se
    # LEE del disco. Lo que no puede haber es ninguna operacion de
    # firma ni ninguna clave privada.
    prohibidas = {
        "sign_manifest", "sign", "verify", "verify_manifest",
        "load_pem_private_key", "Ed25519PrivateKey",
        "private_bytes", "generate"
    }

    comprobar("No firma ni verifica nada, y no maneja claves",
              not (nombres & prohibidas),
              str(sorted(nombres & prohibidas)))

    comprobar("Ni siquiera importa la biblioteca de criptografia",
              "cryptography" not in servidor_releases)

    # Lo unico que lee de disco: el manifiesto y el paquete
    comprobar("Solo necesita el manifiesto",
              'MANIFEST_NAME = "manifest.json"' in servidor_releases)

    comprobar("Y el paquete",
              'f"agent-{version}.zip"' in servidor_releases)

    comprobar("Lo entrega tal cual, sin tocarlo",
              "return manifiesto, firma" in servidor_releases)

    # La verificacion es del Agent, y se dice por que
    comprobar("Se documenta que verifica el Agent, no el servidor",
              "La comprueba el AGENT" in servidor_releases)

    # El nombre del paquete no se construye con lo que pida el cliente
    comprobar("El nombre sale del manifiesto, no de la peticion",
              "nunca con la que pida el cliente" in servidor_releases)


def test_la_herramienta_no_deja_la_clave_en_el_proyecto():

    comprobar("Comprueba que la ruta este fuera del proyecto",
              "def _dentro_del_proyecto" in HERRAMIENTA
              and "_exigir_fuera_del_proyecto" in HERRAMIENTA)

    comprobar("Comparando rutas reales, para que no se cuele un '..'",
              "os.path.realpath" in HERRAMIENTA)

    comprobar("Lo aplica al generar",
              HERRAMIENTA.count("_exigir_fuera_del_proyecto(") >= 3)

    comprobar("La ruta es obligatoria",
              "indica donde guardar la clave privada" in HERRAMIENTA)

    comprobar("Se puede dar por variable de entorno, solo la RUTA",
              "REMOTEADMIN_SIGNING_KEY" in HERRAMIENTA)

    comprobar("La clave se lee donde este y no se copia",
              "No se copia al" in HERRAMIENTA
              and "se usa en memoria" in HERRAMIENTA)

    # Nada de escribir la clave fuera de --generar-clave
    escrituras = [
        linea.strip() for linea in HERRAMIENTA.split(chr(10))
        if 'open(' in linea and '"wb"' in linea
    ]

    comprobar("Solo se escribe un archivo binario: el que se genera",
              len(escrituras) == 2, str(escrituras))


# ==============================
# 18. Servicio y ayudante
# ==============================

def test_solo_el_servicio_actualiza():

    AYUDANTE = io.open(RAIZ / "agent" / "helper.py",
                       encoding="utf-8").read()

    comprobar("El ayudante no actualiza",
              "updater" not in AYUDANTE
              and "update" not in AYUDANTE.lower().replace(
                  "actualiza", ""
              ))

    comprobar("El hilo de actualizacion es del servicio",
              "if AGENT_ROLE == ROLE_SERVICE:" in AGENTE
              and "_bucle_de_actualizacion" in AGENTE)

    bloque = AGENTE.split("def _bucle_de_actualizacion", 1)[1].split(
        chr(10) + "def ", 1
    )[0]

    comprobar("Usa el token individual del equipo",
              "get_agent_device_token()" in bloque)

    comprobar("Y no hace nada si todavia no hay identidad",
              "if not token:" in bloque)

    comprobar("El reinicio se pide sin interprete de ordenes",
              "shell=True" not in AGENTE
              and '["schtasks", "/Run", "/TN", "RemoteAdminAgent"]'
              in AGENTE)


def test_el_paquete_de_actualizacion_es_el_mismo_que_el_de_instalacion():
    """
    Si se separaran, un equipo ya instalado se quedaria sin los
    modulos nuevos que si recibe una instalacion desde cero.
    """

    comprobar("La herramienta reutiliza la lista del instalador",
              "packaging.PACKAGE_FILES" in HERRAMIENTA)

    for pieza in ("agent/updater.py", "agent/release.py",
                  "agent/version.py", "agent/ipc.py",
                  "agent/helper.py"):

        comprobar(f"El paquete incluye {pieza}",
                  pieza in packaging.PACKAGE_FILES)


# ==============================
# 19-21. Regresiones
# ==============================

def preparar():

    h.reiniciar()

    servidor.connected_agents.clear()

    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organization_settings",
                  "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    organizacion = orgs.create_organization(
        "Empresa", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, organizacion["id"])

    escenario["organizacion"] = organizacion["id"]

    ficha, credencial = enrollment.create_token(organizacion["id"])

    alta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-UPDATE", "operating_system": "Windows",
              "ip_address": "10.0.0.5"},
        headers={"X-Agent-Token": credencial}
    ).json()

    escenario["device_id"] = alta["device_id"]
    escenario["token"] = alta["agent_token"]

    return alta


def test_los_endpoints_de_actualizacion_exigen_token_de_agent():

    preparar()

    comprobar("Sin token no se consulta la version",
              h.cliente().get("/api/agent/version").status_code == 401)

    comprobar("Ni se descarga el paquete",
              h.cliente().get("/api/agent/package").status_code == 401)

    comprobar("Un token falso tampoco",
              h.cliente().get(
                  "/api/agent/version",
                  headers={"X-Agent-Token": "inventado"}
              ).status_code == 401)

    respuesta = h.cliente().get(
        "/api/agent/version",
        headers={"X-Agent-Token": escenario["token"]}
    )

    comprobar("Con el token individual si",
              respuesta.status_code == 200, str(respuesta.status_code))

    datos = respuesta.json()

    # Haya release publicada o no, la respuesta es valida: lo que no
    # puede es fallar ni filtrar nada. Se admiten los dos estados
    # porque el proyecto puede tener una release construida.
    if datos.get("manifest") is None:

        comprobar("Sin nada publicado, se responde sin version",
                  datos.get("signature") is None)

    else:

        comprobar("Con algo publicado, viene firmado",
                  bool(datos.get("signature")))

        comprobar("Y el manifiesto trae sus tres campos",
                  set(release.MANIFEST_FIELDS)
                  <= set(datos["manifest"]),
                  str(sorted(datos["manifest"])))

    comprobar("Y no se filtra ningun secreto",
              "rae_" not in respuesta.text
              and escenario["token"] not in respuesta.text)


def test_el_heartbeat_y_el_websocket_siguen_funcionando():

    preparar()

    comprobar("El latido sigue funcionando",
              h.cliente().post(
                  "/api/devices/heartbeat",
                  json={"device_id": escenario["device_id"],
                        "ip_address": "10.0.0.5"},
                  headers={"X-Agent-Token": escenario["token"]}
              ).status_code == 200)

    servidor_py = io.open(RAIZ / "backend" / "main.py",
                          encoding="utf-8").read()

    bloque = servidor_py.split('@app.websocket("/ws/agent")', 1)[1]
    bloque = bloque.split(chr(10) + "@app.", 1)[0]

    comprobar("El WebSocket sigue pidiendo el token en cabecera",
              'websocket.headers.get("x-agent-token")' in bloque)

    comprobar("Y rechazando antes de aceptar",
              "await websocket.close(code=1008)" in bloque)

    comprobar("El device_id no cambio",
              servidor.get_device_id_for_token(escenario["token"])
              == escenario["device_id"])


def test_el_tls_del_agent_no_cambio():

    cuerpo = AGENTE.split("def build_ssl_context", 1)[1].split(
        chr(10) + chr(10) + "# =====", 1
    )[0]

    comprobar("ws:// sigue sin contexto",
              "if not usa_tls():" in cuerpo and "return None" in cuerpo)

    comprobar("wss:// con CA sigue usando esa CA",
              "ssl.create_default_context(cafile=CA_CERT)" in AGENTE)

    comprobar("wss:// sin CA sigue usando la del sistema",
              "return ssl.create_default_context()" in AGENTE)

    comprobar("Y el contexto solo se pasa si existe",
              "if contexto is not None:" in AGENTE)


def test_lo_de_siempre_sigue_en_pie():

    preparar()

    cliente = h.cliente(h.OWNER)

    for ruta in ("/api/devices", "/api/alerts", "/api/settings",
                 "/api/users/me", "/api/recordings", "/api/audit",
                 "/api/agent-package"):

        comprobar(f"{ruta} responde",
                  cliente.get(ruta).status_code == 200,
                  str(cliente.get(ruta).status_code))

    comprobar("La organizacion del equipo no cambio",
              servidor.organization_of_device(escenario["device_id"])
              == escenario["organizacion"])

    conexion = database.get_connection()
    total = conexion.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    conexion.close()

    comprobar("Y no hay equipos duplicados", total == 1, str(total))


def test_sin_nada_publicado_el_servidor_lo_dice():
    """
    Se apunta a una carpeta VACIA en vez de mirar la del proyecto.

    Antes esta prueba daba por hecho que no habia ninguna release
    construida, asi que fallaba en cuanto se publicaba una. Lo que
    debe comprobarse es el comportamiento, no el estado en que
    casualmente este el disco.
    """

    vacia = tempfile.mkdtemp(prefix="sin-publicar-")

    original = publicacion.RELEASES_DIR

    try:

        publicacion.RELEASES_DIR = vacia

        comprobar("Sin nada publicado, no hay version",
                  publicacion.published_version() is None)

        comprobar("Y se avisa con una excepcion clara",
                  falla(publicacion.load_published,
                        publicacion.ReleaseNotPublished))

        comprobar("Tampoco hay paquete que servir",
                  falla(lambda: publicacion.load_package("1.0.0"),
                        publicacion.ReleaseNotPublished))

        # Un manifiesto incompleto se trata igual que no tener nada
        with io.open(os.path.join(vacia, "manifest.json"), "w",
                     encoding="utf-8") as archivo:
            json.dump({"manifest": {"version": "1.0.0"}}, archivo)

        comprobar("Un manifiesto sin firma no se considera publicado",
                  falla(publicacion.load_published,
                        publicacion.ReleaseNotPublished))

        # Y uno ilegible, tambien
        with io.open(os.path.join(vacia, "manifest.json"), "w",
                     encoding="utf-8") as archivo:
            archivo.write("esto no es json")

        comprobar("Un manifiesto ilegible tampoco",
                  falla(publicacion.load_published,
                        publicacion.ReleaseNotPublished))

    finally:
        publicacion.RELEASES_DIR = original
        shutil.rmtree(vacia, ignore_errors=True)


def test_con_una_release_publicada_el_servidor_la_sirve():
    """
    El caso contrario, tambien aislado: se publica una release de
    prueba en una carpeta temporal y se comprueba que se entrega tal
    cual, sin tocarla.
    """

    carpeta = tempfile.mkdtemp(prefix="publicada-")

    original = publicacion.RELEASES_DIR

    try:

        publicacion.RELEASES_DIR = carpeta

        manifiesto, firma, datos = firmar("1.5.0")

        with io.open(os.path.join(carpeta, "manifest.json"), "w",
                     encoding="utf-8") as archivo:
            json.dump({"manifest": manifiesto, "signature": firma},
                      archivo)

        with open(os.path.join(carpeta, "agent-1.5.0.zip"), "wb") as archivo:
            archivo.write(datos)

        comprobar("Se ve la version publicada",
                  publicacion.published_version() == "1.5.0")

        leido, firma_leida = publicacion.load_published()

        comprobar("El manifiesto se entrega tal cual",
                  leido == manifiesto)

        comprobar("Y la firma tambien",
                  firma_leida == firma)

        comprobar("El paquete se entrega sin tocarlo",
                  publicacion.load_package("1.5.0") == datos)

        comprobar("Y sigue verificando despues de pasar por el servidor",
                  release.verify_manifest(leido, firma_leida)
                  and release.verify_package(
                      publicacion.load_package("1.5.0"), leido
                  ))

        comprobar("De una version que no existe no hay paquete",
                  falla(lambda: publicacion.load_package("9.9.9"),
                        publicacion.ReleaseNotPublished))

    finally:
        publicacion.RELEASES_DIR = original
        shutil.rmtree(carpeta, ignore_errors=True)


# ==============================

def main():

    pruebas = [
        test_el_agent_sabe_su_version,
        test_las_versiones_se_comparan_como_numeros,
        test_una_version_mal_formada_se_rechaza,
        test_no_se_actualiza_si_la_version_es_la_misma,
        test_se_actualiza_si_hay_version_nueva,
        test_se_rechaza_volver_a_una_version_anterior,
        test_una_version_publicada_invalida_se_rechaza,
        test_una_firma_valida_se_acepta,
        test_una_firma_invalida_se_rechaza,
        test_sin_clave_publica_no_se_actualiza_nada,
        test_un_paquete_corrupto_se_rechaza,
        test_una_descarga_interrumpida_se_rechaza,
        test_el_hash_no_se_cree_por_si_solo,
        test_el_manifiesto_firmado_es_estable,
        test_un_paquete_no_puede_escribir_fuera_de_su_sitio,
        test_un_paquete_incompleto_se_rechaza,
        test_una_actualizacion_conserva_todo_lo_del_equipo,
        test_se_vuelve_atras_si_la_instalacion_queda_mal,
        test_un_paquete_que_miente_sobre_su_version_no_se_instala,
        test_una_actualizacion_no_vuelve_a_pedir_enrollment,
        test_lo_que_se_reemplaza_y_lo_que_no_esta_declarado,
        test_no_hay_descarga_y_ejecucion_arbitraria,
        test_el_token_no_aparece_en_los_registros,
        test_no_hay_dos_actualizaciones_a_la_vez,
        test_la_clave_privada_no_esta_en_el_repositorio,
        test_el_repositorio_no_contiene_ninguna_clave_de_firma,
        test_el_paquete_del_agent_no_lleva_la_clave_privada,
        test_el_servidor_solo_necesita_la_release_firmada,
        test_la_herramienta_no_deja_la_clave_en_el_proyecto,
        test_solo_el_servicio_actualiza,
        test_el_paquete_de_actualizacion_es_el_mismo_que_el_de_instalacion,
        test_los_endpoints_de_actualizacion_exigen_token_de_agent,
        test_el_heartbeat_y_el_websocket_siguen_funcionando,
        test_el_tls_del_agent_no_cambio,
        test_lo_de_siempre_sigue_en_pie,
        test_sin_nada_publicado_el_servidor_lo_dice,
        test_con_una_release_publicada_el_servidor_la_sirve
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:90])

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLO"
        print(f"[{marca}] {nombre}" + (f"  -> {detalle}" if not ok else ""))
        if not ok:
            fallos += 1

    print(f"\n{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
