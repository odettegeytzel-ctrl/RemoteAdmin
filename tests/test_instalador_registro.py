r"""
Lo que el instalador deja escrito y lo que comprueba antes de elevar.

    .venv\Scripts\python tests/test_instalador_registro.py

En una instalacion en campo, el instalador grafico mostro:

    La instalacion termino con errores (codigo 1)

y C:\ProgramData\RemoteAdmin NO existia: el script elevado habia
abortado en sus comprobaciones previas, antes de crear nada. No quedo
ni un archivo que dijera en cual de ellas.

Son dos agujeros distintos y aqui se cubren los dos:

  1. install-agent.ps1 no escribia NADA hasta bien entrada la
     instalacion, y la salida del proceso elevado no vuelve a la
     ventana que lo lanzo (ShellExecute 'runas' no da tuberias). El
     mensaje remitia ademas a agent-service.log, que lo escribe el
     Agent ya instalado y que en este caso no existia.

  2. La ventana pedia permisos de administrador sin haber mirado si
     su propia carga util estaba completa. Empaquetado con
     PyInstaller, esa carga vive en una carpeta temporal que un
     antivirus puede vaciar a medias.

Y de paso el campo "Servidor", que en el .exe autonomo salia vacio
porque ese .exe se descarga suelto, sin el .env del ZIP.

No se instala nada ni se ejecuta PowerShell: se lee el script y se
llama a las funciones del instalador sobre carpetas temporales.
"""

import importlib.util
import io
import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

GUION = RAIZ / "installer" / "install-agent.ps1"
CONSTRUCTOR = RAIZ / "installer" / "build-installer.ps1"

texto_guion = io.open(GUION, encoding="utf-8", newline="").read()
texto_constructor = io.open(CONSTRUCTOR, encoding="utf-8").read()


# El instalador grafico importa tkinter en el modulo. En un equipo sin
# interfaz eso no esta, y la prueba no necesita ninguna ventana.
try:
    import tkinter  # noqa: F401
    hay_tk = True

except Exception:
    hay_tk = False

instalador = None

if hay_tk:
    especificacion = importlib.util.spec_from_file_location(
        "veyra_installer", RAIZ / "installer" / "veyra_installer.py"
    )
    instalador = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(instalador)


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def linea_de(fragmento, texto=None):
    """Numero de linea de la primera aparicion, o None."""

    cuerpo = texto_guion if texto is None else texto

    posicion = cuerpo.find(fragmento)

    if posicion < 0:
        return None

    return cuerpo[:posicion].count("\n")


# ==================================================================
# 1. El script abre el registro ANTES de poder abortar
# ==================================================================

comprobar(
    "install-agent.ps1 abre una transcripcion",
    "Start-Transcript" in texto_guion
)

arranque = linea_de("Start-Transcript")

# Las tres comprobaciones que hacen abortar el script sin crear nada.
# Si el registro se abriera despues de cualquiera de ellas, el caso de
# campo seguiria sin dejar rastro, que es justo el fallo.
for descripcion, marca in [
    ("la de administrador", "Ejecuta PowerShell como administrador"),
    ("la de agent\\agent.py", "No se encontro agent\\agent.py"),
    ("la del runtime", "No se encontro python.exe en el runtime"),
]:

    donde = linea_de(marca)

    comprobar(
        "el registro se abre antes de %s" % descripcion,
        arranque is not None and donde is not None and arranque < donde,
        "transcripcion en %s, comprobacion en %s" % (arranque, donde)
    )


# Y antes de la PRIMERA linea ejecutable que no sea suya: no vale
# abrirlo "pronto" si algo se evalua antes y revienta.
comprobar(
    "el registro se abre antes de resolver la carpeta del paquete",
    arranque is not None
    and arranque < linea_de("$SourceDir = (Resolve-Path"),
    "SourceDir en %s" % linea_de("$SourceDir = (Resolve-Path")
)


# Si la transcripcion no se puede abrir -desactivada por directiva de
# grupo- la instalacion debe seguir. Perder el registro no es motivo
# para no instalar.
comprobar(
    "un fallo al abrir el registro no aborta la instalacion",
    "try {\r\n    Start-Transcript" in texto_guion
    and "} catch {" in texto_guion[texto_guion.find("Start-Transcript"):]
)


# Se anade, no se reemplaza: al reintentar una instalacion que fallo,
# lo util es tener los dos intentos.
comprobar(
    "el registro se anade en vez de reemplazarse",
    "-Append" in texto_guion[
        texto_guion.find("Start-Transcript"):
        texto_guion.find("Start-Transcript") + 120
    ]
)

comprobar(
    "la transcripcion se cierra al terminar",
    "Stop-Transcript" in texto_guion
)


# ==================================================================
# 2. Los dos programas nombran EL MISMO archivo
# ==================================================================
#
# Es la unica forma de que el mensaje de la ventana sirva de algo. Si
# cada uno eligiera su ruta, el error seguiria mandando a un archivo
# que no existe, que es exactamente lo que pasaba.

comprobar(
    "el script escribe el registro en ProgramData",
    'Join-Path $env:ProgramData "VEYRA-instalacion.log"' in texto_guion
)

if instalador:

    esperado = os.path.join(
        os.environ.get("ProgramData", r"C:\ProgramData"),
        "VEYRA-instalacion.log"
    )

    comprobar(
        "la ventana nombra ese mismo archivo",
        instalador.REGISTRO_INSTALACION == esperado,
        instalador.REGISTRO_INSTALACION
    )

    # La carpeta de destino puede no existir cuando falla: el registro
    # NO puede estar dentro de ella.
    comprobar(
        "el registro NO esta dentro de la carpeta de destino",
        not instalador.REGISTRO_INSTALACION.startswith(
            instalador.INSTALL_DIR + os.sep
        ),
        instalador.REGISTRO_INSTALACION
    )

    fuente = io.open(
        RAIZ / "installer" / "veyra_installer.py", encoding="utf-8"
    ).read()

    # El mensaje del codigo de salida apuntaba a agent-service.log,
    # que lo escribe el Agent YA instalado. Cuando la instalacion
    # aborta antes de crear nada, ese archivo no existe.
    trozo = fuente[
        fuente.find("termino con errores"):
        fuente.find("termino con errores") + 400
    ]

    comprobar(
        "el error del codigo de salida ya no manda a agent-service.log",
        "agent-service.log" not in trozo,
        trozo.strip()[:160]
    )

    comprobar(
        "manda al registro de la instalacion",
        "REGISTRO_INSTALACION" in trozo
    )


# ==================================================================
# 3. No se piden permisos de administrador para nada
# ==================================================================

if instalador:

    original = instalador.carpeta_del_paquete
    era_frozen = getattr(sys, "frozen", False)

    def con_paquete(armar, frozen=False):
        """Monta una carpeta, la declara paquete y devuelve que falta."""

        with tempfile.TemporaryDirectory() as carpeta:

            armar(Path(carpeta))

            instalador.carpeta_del_paquete = lambda: carpeta
            sys.frozen = frozen

            try:
                return instalador._lo_que_falta_del_paquete()

            finally:
                instalador.carpeta_del_paquete = original

                if era_frozen:
                    sys.frozen = True
                else:
                    try:
                        del sys.frozen
                    except AttributeError:
                        pass

    def completo(base, con_runtime=True, con_python=True):

        (base / "installer").mkdir()
        (base / "installer" / "install-agent.ps1").write_text("x")

        (base / "agent").mkdir()
        (base / "agent" / "agent.py").write_text("x")

        if con_runtime:
            (base / "runtime").mkdir()

            if con_python:
                (base / "runtime" / "python.exe").write_text("x")

    comprobar(
        "un paquete completo no reporta nada",
        con_paquete(lambda b: completo(b), frozen=True) == [],
        str(con_paquete(lambda b: completo(b), frozen=True))
    )

    def sin_agente(base):
        completo(base)
        (base / "agent" / "agent.py").unlink()

    falta = con_paquete(sin_agente, frozen=True)

    comprobar(
        "sin agent.py se dice que falta el programa",
        len(falta) == 1 and "agent.py" in falta[0],
        str(falta)
    )

    def sin_guion(base):
        completo(base)
        (base / "installer" / "install-agent.ps1").unlink()

    falta = con_paquete(sin_guion, frozen=True)

    comprobar(
        "sin install-agent.ps1 se dice que falta el script",
        len(falta) == 1 and "install-agent.ps1" in falta[0],
        str(falta)
    )

    # El caso de campo: la carpeta del runtime esta, pero vaciada.
    def runtime_vaciado(base):
        completo(base, con_python=False)

    falta = con_paquete(runtime_vaciado, frozen=True)

    comprobar(
        "un runtime vaciado se detecta",
        len(falta) == 1 and "python.exe" in falta[0],
        str(falta)
    )

    # Sin empaquetar no hay runtime y eso es normal: se instala con el
    # Python del sistema. No debe reportarse como que falta.
    comprobar(
        "sin empaquetar, la ausencia de runtime no es un fallo",
        con_paquete(
            lambda b: completo(b, con_runtime=False), frozen=False
        ) == [],
        str(con_paquete(
            lambda b: completo(b, con_runtime=False), frozen=False
        ))
    )

    # Todo a la vez: se nombran los tres, no solo el primero. Quien
    # esta delante del equipo arregla una vez, no tres.
    falta = con_paquete(lambda b: b.mkdir(exist_ok=True), frozen=True)

    comprobar(
        "con todo ausente se nombran el guion y el programa",
        len(falta) == 2,
        str(falta)
    )

    # Y que instalar() se pare AHI, sin llegar a ShellExecute.
    elevaciones = []

    original_lanzar = instalador._lanzar_elevado
    instalador._lanzar_elevado = lambda *a: elevaciones.append(a) or 0

    with tempfile.TemporaryDirectory() as vacio:

        instalador.carpeta_del_paquete = lambda: vacio

        try:
            correcto, mensaje = instalador.instalar(
                "https://ejemplo", "rae_x", lambda t: None
            )

        finally:
            instalador.carpeta_del_paquete = original
            instalador._lanzar_elevado = original_lanzar

    comprobar(
        "con el paquete incompleto, instalar() falla",
        not correcto,
        mensaje
    )

    comprobar(
        "y NO se pidieron permisos de administrador",
        elevaciones == [],
        str(elevaciones)
    )

    comprobar(
        "el mensaje dice que no se cambio nada en el equipo",
        "No se ha cambiado nada" in mensaje,
        mensaje
    )

    comprobar(
        "y nombra lo que falta, no un codigo",
        "agent" in mensaje and "install-agent.ps1" in mensaje,
        mensaje
    )


# ==================================================================
# 4. El campo "Servidor" del .exe autonomo
# ==================================================================

if instalador:

    def servidor_con(armar):

        with tempfile.TemporaryDirectory() as carpeta:

            armar(Path(carpeta))

            instalador.carpeta_del_paquete = lambda: carpeta

            try:
                return instalador.servidor_del_paquete()

            finally:
                instalador.carpeta_del_paquete = original

    def escribir_env(base, valor):
        (base / ".env").write_text(
            "# comentario\nREMOTEADMIN_SERVER=%s\nAGENT_TOKEN=rae_x\n" % valor,
            encoding="utf-8"
        )

    def escribir_txt(base, valor):
        (base / "installer").mkdir(exist_ok=True)
        (base / "installer" / "servidor.txt").write_text(
            valor, encoding="utf-8"
        )

    comprobar(
        "el .env del paquete sigue mandando",
        servidor_con(
            lambda b: escribir_env(b, "https://panel.ejemplo")
        ) == "https://panel.ejemplo"
    )

    comprobar(
        "sin .env se usa installer/servidor.txt",
        servidor_con(
            lambda b: escribir_txt(b, "https://empotrado.ejemplo")
        ) == "https://empotrado.ejemplo",
        servidor_con(lambda b: escribir_txt(b, "https://empotrado.ejemplo"))
    )

    # El orden importa: el .env lo escribe el servidor que de verdad
    # va a atender al equipo; el servidor.txt se congelo al construir.
    def ambos(base):
        escribir_env(base, "https://del-env")
        escribir_txt(base, "https://del-exe")

    comprobar(
        "con los dos, gana el .env",
        servidor_con(ambos) == "https://del-env",
        servidor_con(ambos)
    )

    # Un .env con la clave vacia no debe tapar al servidor.txt
    def env_vacio(base):
        escribir_env(base, "")
        escribir_txt(base, "https://del-exe")

    comprobar(
        "un REMOTEADMIN_SERVER vacio no tapa al servidor.txt",
        servidor_con(env_vacio) == "https://del-exe",
        servidor_con(env_vacio)
    )

    comprobar(
        "sin ninguno de los dos, el campo queda vacio",
        servidor_con(lambda b: None) == ""
    )

    # Un archivo con cualquier cosa dentro no debe acabar rellenando
    # el campo como si fuera una direccion buena.
    for basura in ["panel.ejemplo", "ftp://x", "", "   ", "no soy una url"]:

        comprobar(
            "se rechaza como servidor: %r" % basura,
            servidor_con(lambda b, v=basura: escribir_txt(b, v)) == "",
            servidor_con(lambda b, v=basura: escribir_txt(b, v))
        )

    # Con saltos de linea alrededor, que es lo que deja un editor
    comprobar(
        "se admite con espacios y salto de linea alrededor",
        servidor_con(
            lambda b: escribir_txt(b, "  https://panel.ejemplo\n")
        ) == "https://panel.ejemplo"
    )


# ==================================================================
# 5. La construccion del .exe
# ==================================================================

comprobar(
    "build-installer.ps1 acepta -Servidor",
    "[string]$Servidor = \"\"" in texto_constructor
)

comprobar(
    "valida el esquema antes de empotrarlo",
    "La direccion del servidor debe empezar por http" in texto_constructor
)

comprobar(
    "lo empaqueta dentro de installer\\",
    '"$ServidorFile;installer"' in texto_constructor
)

comprobar(
    "y lo pasa a PyInstaller",
    "@DatosServidor" in texto_constructor
)

# Sin BOM: lo lee Python y un BOM se colaria en la direccion.
comprobar(
    "el archivo se escribe sin BOM",
    "New-Object Text.UTF8Encoding $false" in texto_constructor
)

comprobar(
    "sin -Servidor no se empaqueta nada",
    "$DatosServidor = @()" in texto_constructor
)

# El .env de este repositorio apunta a localhost. Tomarlo de ahi por
# omision publicaria un instalador que no conecta con nada.
comprobar(
    "el constructor NO toma la direccion del .env del repositorio",
    'Join-Path $Raiz ".env"' not in texto_constructor
)

comprobar(
    "la constancia deja escrito que servidor se empotro",
    '"  servidor     :' in texto_constructor
)


# ==================================================================
# 6. El script sigue siendo un script valido
# ==================================================================
#
# Un CR suelto dentro de un comentario parte la linea en dos y lo que
# sigue deja de ser comentario: PowerShell no analiza el archivo y la
# instalacion falla con un error de sintaxis. Ya paso una vez con
# "InstallDir\runtime", donde el \r se interpreto como retorno de
# carro al reescribir el archivo.

sueltos = texto_guion.replace("\r\n", "").count("\r")

comprobar(
    "install-agent.ps1 no tiene retornos de carro sueltos",
    sueltos == 0,
    "%d encontrados" % sueltos
)

comprobar(
    "el comentario del runtime esta entero",
    "# Va a InstallDir\\runtime, que NO esta" in texto_guion
)


# ------------------------------------------------------------------

print()
print("=" * 66)
print("REGISTRO DE LA INSTALACION Y COMPROBACIONES PREVIAS")
print("=" * 66)
print()

if not hay_tk:
    print("  AVISO: sin tkinter, solo se comprobaron los scripts.")
    print()

fallos = 0

for nombre, bien, detalle in resultados:

    print("  %s  %s" % ("OK   " if bien else "FALLA", nombre))

    if not bien:
        fallos += 1

        if detalle:
            print("          %s" % detalle)

print()
print("%d/%d comprobaciones" % (len(resultados) - fallos, len(resultados)))

print()
print("-" * 66)
print("LO QUE ESTO NO DEMUESTRA")
print("-" * 66)
print()
print("No se ejecuto PowerShell ni se instalo nada. Queda por ver en")
print("un Windows de verdad:")
print()
print("  - que el archivo de registro aparece y contiene el motivo")
print("  - que lo escribe tambien cuando el script aborta por un throw")
print("  - que un .exe construido con -Servidor abre con el campo lleno")

sys.exit(1 if fallos else 0)
