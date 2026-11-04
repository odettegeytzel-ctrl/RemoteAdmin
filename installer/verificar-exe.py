"""
Que lleva dentro el instalador, extraido del .exe de verdad.

    .venv\\Scripts\\python installer/verificar-exe.py [ruta al exe]

No se fia de lo que diga la constancia ni de lo que haya en el arbol
de trabajo: abre el archivo CArchive que PyInstaller pega al final
del ejecutable y compara lo que hay DENTRO con lo que hay en el
repositorio. Un instalador construido desde una carpeta a medias, o
desde una compilacion anterior que se quedo en cache, se delata aqui
y no en el equipo del cliente.

Solo lee. No instala, no publica y no toca ningun equipo.
"""

import hashlib
import os
import re
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))

from PyInstaller.archive.readers import CArchiveReader


POR_DEFECTO = RAIZ / "installer" / "dist" / "VEYRA-Instalador.exe"


# Lo que tiene que viajar dentro, y con que nombre lo guarda
# PyInstaller. La izquierda es el nombre dentro del .exe; la derecha,
# el archivo del repositorio con el que se compara.
ESPERADOS = {
    "agent/agent.py": "agent/agent.py",
    "agent/commands.py": "agent/commands.py",
    "agent/version.py": "agent/version.py",
    "agent/release.py": "agent/release.py",
    "agent/helper.py": "agent/helper.py",
    "agent/maintenance.py": "agent/maintenance.py",
    "agent/clipboard.py": "agent/clipboard.py",
    "agent/lockinput.py": "agent/lockinput.py",
    "agent/scheduler.py": "agent/scheduler.py",
    "agent/owners.py": "agent/owners.py",
    "agent/recorder.py": "agent/recorder.py",
    "agent/storage.py": "agent/storage.py",
    "agent/updater.py": "agent/updater.py",
    "installer/install-agent.ps1": "installer/install-agent.ps1",
    "installer/uninstall-agent.ps1": "installer/uninstall-agent.ps1",
}


# Senales de que la transmision de grabaciones viaja dentro.
MARCAS_DE_TRANSMISION = (
    "handle_recording_stream_message",
    "_enviar_grabacion",
    "recording_stream_start",
    "recording_stream_abort",
    "agent_capabilities",
    "find_managed_recording",
)


# Senales de que el portapapeles, el bloqueo de entrada y la
# correccion de mayusculas viajan dentro.
MARCAS_DE_CONTROL = (
    ("agent/agent.py", "sincronizar_bloqueos"),
    ("agent/agent.py", "VK_CAPITAL"),
    ("agent/agent.py", "clipboard_set:"),
    ("agent/agent.py", "input_lock:"),
    ("agent/helper.py", "poner_portapapeles"),
    ("agent/helper.py", "bloquear_entrada"),
    ("agent/clipboard.py", "CF_UNICODETEXT"),
    ("agent/lockinput.py", "PLAZO_MAXIMO"),
    ("agent/maintenance.py", "SetWindowPos"),
)


# Senales de que las guardas de identidad y la recuperacion viajan.
MARCAS_DEL_INSTALADOR = (
    "yaEnrolado",
    "RespaldoAgent",
    "agent.anterior",
    "Forzar",
)


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def contenido_del_exe(ruta):
    """Nombre -> bytes, de todo lo que PyInstaller pego al final."""

    lector = CArchiveReader(str(ruta))

    dentro = {}

    for nombre in lector.toc:
        try:
            dentro[nombre.replace("\\", "/")] = lector.extract(nombre)
        except Exception:
            continue

    return dentro


def corto(datos):
    return hashlib.sha256(datos).hexdigest()


def main():

    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else POR_DEFECTO

    if not ruta.is_file():
        print(f"No existe: {ruta}")
        return 1

    tamano = ruta.stat().st_size
    huella = hashlib.sha256(ruta.read_bytes()).hexdigest()

    print()
    print("VEYRA - verificacion del instalador")
    print("-" * 52)
    print(f"  archivo : {ruta}")
    print(f"  tamano  : {tamano:,} bytes".replace(",", " "))
    print(f"  SHA-256 : {huella.upper()}")
    print()

    dentro = contenido_del_exe(ruta)

    comprobar("Se puede abrir el archivo empaquetado",
              len(dentro) > 0, f"{len(dentro)} entradas")

    # --- los archivos, uno por uno ---

    print("CONTENIDO EXTRAIDO")

    for nombre, origen in ESPERADOS.items():

        datos = dentro.get(nombre)

        if datos is None:
            comprobar(f"{nombre} viaja dentro", False, "no esta")
            print(f"  {nombre:<32} FALTA")
            continue

        archivo = RAIZ / origen

        if not archivo.is_file():
            comprobar(f"{nombre} tiene original en el arbol", False,
                      origen)
            continue

        esperado = archivo.read_bytes()

        # Los .ps1 van con CRLF en el repositorio; PyInstaller los
        # copia tal cual, asi que la comparacion es directa.
        igual = datos == esperado

        comprobar(f"{nombre} es identico al arbol", igual,
                  f"dentro {corto(datos)[:16]} / arbol "
                  f"{corto(esperado)[:16]}")

        print(f"  {nombre:<32} {corto(datos)[:16]}  "
              f"{'identico al arbol' if igual else 'DISTINTO'}")

    print()

    # --- la version ---

    version_dentro = ""

    datos = dentro.get("agent/version.py")

    if datos:
        hallado = re.search(
            rb'AGENT_VERSION\s*=\s*"([^"]+)"', datos
        )
        if hallado:
            version_dentro = hallado.group(1).decode()

    print(f"VERSION DEL AGENT DENTRO DEL EXE: {version_dentro}")

    comprobar("La version empaquetada es 1.8.0",
              version_dentro == "1.8.0", version_dentro)

    print()

    # --- la clave de firma ---
    #
    # Un .exe con la clave vieja dentro no aceptaria ninguna
    # actualizacion firmada con la nueva, y el fallo no se veria
    # hasta meses despues, cuando tocara actualizar.

    clave_dentro = ""

    datos = dentro.get("agent/release.py")

    if datos:
        hallado = re.search(
            rb'AGENT_UPDATE_PUBLIC_KEY\s*=\s*"([^"]+)"', datos
        )
        if hallado:
            clave_dentro = hallado.group(1).decode()

    from agent import release as _release

    print(f"CLAVE PUBLICA DENTRO DEL EXE: {clave_dentro[:16]}...")

    comprobar("La clave publica es la del arbol",
              clave_dentro == _release.AGENT_UPDATE_PUBLIC_KEY,
              clave_dentro[:16])

    comprobar("Y no es la anterior, cuya privada se perdio",
              clave_dentro != "DWvOsshyNgNlDFh4QQLg6wTlNx8mOAVlntKJxNinS3k=",
              "lleva la clave vieja")

    comprobar("No viaja ninguna clave PRIVADA",
              b"PRIVATE KEY" not in (datos or b""),
              "hay material privado en release.py")

    print()

    # --- el servidor ---

    servidor = (dentro.get("installer/servidor.txt") or b"").decode(
        "utf-8", "replace"
    ).strip()

    print(f"SERVIDOR PRECARGADO: {servidor}")

    comprobar("Apunta al servidor de produccion",
              servidor == "https://remoteadmin.tail37c528.ts.net",
              servidor)

    comprobar("Sin /panel en la direccion",
              "/panel" not in servidor, servidor)

    comprobar("Y no apunta a una direccion de desarrollo",
              not any(
                  malo in servidor.lower()
                  for malo in ("localhost", "127.0.0.1", ":8000")
              ),
              servidor)

    print()

    # --- la transmision ---

    agente = (dentro.get("agent/agent.py") or b"").decode(
        "utf-8", "replace"
    )

    print("TRANSMISION DE GRABACIONES, DENTRO DEL EXE")

    for marca in MARCAS_DE_TRANSMISION:
        presente = marca in agente
        comprobar(f"  {marca}", presente, "no aparece")
        print(f"  {marca:<34} {'si' if presente else 'NO'}")

    ordenes = (dentro.get("agent/commands.py") or b"").decode(
        "utf-8", "replace"
    )

    comprobar("Las ordenes de transmision son de fondo",
              "recording_stream_start:" in ordenes, "no estan")

    print()

    # --- portapapeles, bloqueo y mayusculas ---

    print("CONTROL REMOTO, DENTRO DEL EXE")

    for archivo, marca in MARCAS_DE_CONTROL:

        texto = (dentro.get(archivo) or b"").decode("utf-8", "replace")

        presente = marca in texto

        comprobar(f"  {archivo}: {marca}", presente, "no aparece")

        print(f"  {archivo:<26} {marca:<24} "
              f"{'si' if presente else 'NO'}")

    comprobar(
        "Las ordenes de portapapeles y bloqueo son de escritorio",
        "clipboard_set:" in ordenes and "input_lock:" in ordenes,
        "no estan clasificadas"
    )

    print()

    # --- las guardas de identidad ---

    instalador = (
        dentro.get("installer/install-agent.ps1") or b""
    ).decode("utf-8", "replace")

    print("PROTECCIONES DE IDENTIDAD, DENTRO DEL EXE")

    for marca in MARCAS_DEL_INSTALADOR:
        presente = marca in instalador
        comprobar(f"  {marca}", presente, "no aparece")
        print(f"  {marca:<34} {'si' if presente else 'NO'}")

    # Lo que NUNCA debe hacer: borrar o renombrar la identidad.
    lineas = [
        linea.strip() for linea in instalador.split("\n")
        if not linea.strip().startswith("Write-Host")
    ]

    toca_identidad = [
        linea for linea in lineas
        if "identity.json" in linea
        and ("Remove-Item" in linea or "Rename-Item" in linea)
    ]

    comprobar("No borra ni renombra identity.json",
              not toca_identidad, str(toca_identidad)[:100])

    print(f"  {'no borra ni renombra identity.json':<34} "
          f"{'si' if not toca_identidad else 'NO'}")

    # Lo que se conserva al actualizar.
    conserva = [
        c for c in ("config", "recordings", "logs")
        if f'"{c}"' in instalador or f"\\{c}" in instalador
    ]

    comprobar("Solo se reemplazan agent e installer",
              'Copy-Item -Path (Join-Path $SourceDir "agent")'
              in instalador,
              "no se encontro la copia selectiva")

    print()

    # --- secretos ---

    print("SIN SECRETOS")

    sospechas = []

    for nombre, datos in dentro.items():

        if not nombre.endswith((".py", ".ps1", ".txt")):
            continue

        texto = datos.decode("utf-8", "replace")

        for patron, aviso in (
            (r'rae_[A-Za-z0-9]{8,}', "credencial de alta"),

            # La cabecera seguida de material en base64. Buscar solo
            # la cabecera daba un falso positivo en
            # cryptography/.../ssh.py, que la lleva como constante
            # para LEER claves ajenas:
            #     _SK_START = b"-----BEGIN OPENSSH PRIVATE KEY-----"
            # Eso no es una clave, es un delimitador. Exigir que
            # detras venga el cuerpo distingue una de otra sin dejar
            # de detectar una clave de verdad.
            (r'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]+'
             r'[A-Za-z0-9+/=\r\n]{60,}',
             "clave privada"),

            (r'agent_token"\s*:\s*"[A-Za-z0-9]{16,}', "token"),
        ):
            if re.search(patron, texto):
                sospechas.append(f"{nombre}: {aviso}")

    comprobar("No viaja ninguna credencial", not sospechas,
              str(sospechas)[:140])

    pem = [n for n in dentro if n.endswith(".pem")]

    comprobar("El unico .pem es el paquete publico de CA",
              all("cacert" in n for n in pem), str(pem)[:100])

    print(f"  credenciales                       "
          f"{'ninguna' if not sospechas else 'HAY'}")
    print(f"  archivos .pem                      {pem}")

    print()
    print("-" * 52)

    fallos = 0

    for nombre, ok, detalle in resultados:
        if not ok:
            print(f"[FALLO] {nombre}  -> {detalle}")
            fallos += 1

    print(f"{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    if fallos:
        print()
        print("NO USAR ESTE INSTALADOR.")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
