r"""
Actualizar un equipo que YA estaba dado de alta.

    .venv\Scripts\python tests/test_actualizacion_instalador.py

Que se prueba
-------------

Reinstalar sobre un equipo registrado tiene que hacer tres cosas a la
vez: reemplazar el programa de verdad, conservar los datos, y poder
volver atras si algo sale mal. Las tres fallaban de una forma u otra:

  1. Copy-Item -Recurse -Force sobrescribe y anade, pero nunca quita.
     Un modulo de la version anterior que ya no existe en la nueva se
     quedaba en disco y se seguia importando.

  2. No habia copia del programa anterior. Un fallo a mitad de la
     copia dejaba el equipo sin Agent: ni el viejo ni el nuevo.

  3. La confirmacion final solo miraba si existia config\identity.json.
     En una actualizacion ese archivo YA estaba antes de empezar, asi
     que la instalacion decia "dado de alta" pasara lo que pasara.

Como se prueba
--------------

Igual que test_guardas_instalador.py: no se reimplementa nada, se
EXTRAE el bloque real de install-agent.ps1 entre sus marcadores y se
ejecuta tal cual sobre carpetas de mentira. Si alguien cambia el
bloque, esta prueba corre el texto nuevo; si borra los marcadores,
falla en vez de pasar en verde sobre nada.

No se toca ProgramData, ni la base de datos, ni ninguna tarea
programada, ni el equipo registrado en produccion.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
INSTALADOR = RAIZ / "installer" / "install-agent.ps1"

resultados = []


def comprobar(nombre, bien, detalle=""):
    resultados.append((nombre, bool(bien), detalle))


FUENTE = io.open(INSTALADOR, encoding="utf-8").read()

INICIO = "# inicio del respaldo"
FIN = "# fin del respaldo"

if INICIO not in FUENTE or FIN not in FUENTE:
    print("No se encontraron los marcadores del bloque de respaldo en")
    print(INSTALADOR)
    print("La prueba no puede comprobar nada. Se detiene.")
    sys.exit(1)

BLOQUE = FUENTE[FUENTE.index(INICIO):FUENTE.index(FIN)]


# ==================================================================
# Lo que el bloque NO debe hacer, leido del texto
# ==================================================================

comprobar(
    "el respaldo va antes de escribir el .env",
    FUENTE.index(FIN) < FUENTE.index("REMOTEADMIN_SERVER=$Server")
)

for datos in ("config", "recordings", "logs", "identity.json"):
    comprobar(
        "el bloque de respaldo no nombra %s" % datos,
        datos not in BLOQUE
    )

comprobar(
    "lo unico que borra es el respaldo anterior",
    BLOQUE.count("Remove-Item") == 1
    and "Remove-Item -Path $RespaldoAgent" in BLOQUE
)

comprobar(
    "el programa anterior se MUEVE, no se copia",
    "Move-Item -Path $AgentDir -Destination $RespaldoAgent" in BLOQUE
)

# La reposicion ya no esta en linea dentro del bloque: vive en
# Restaurar-Lo-Que-Habia, que ademas vuelve a arrancar las tareas
# programadas. El bloque la LLAMA.
comprobar(
    "y se repone si la copia falla",
    "Restaurar-Lo-Que-Habia" in BLOQUE
    and "Move-Item -Path $RespaldoAgent -Destination $AgentDir" in FUENTE
)


# ==================================================================
# La confirmacion final, en una actualizacion
# ==================================================================

comprobar(
    "la actualizacion no se da por buena con Test-Path del identity",
    '$tareaActual.State -eq "Running"' in FUENTE
    and "(Get-Item $LogFile).Length -gt $LogAntes" in FUENTE
)

comprobar(
    "se mide el registro ANTES de arrancar",
    FUENTE.index("$LogAntes = (Get-Item $LogFile).Length")
    < FUENTE.index("Start-ScheduledTask -TaskName $TaskName")
)

comprobar(
    "se informa la version que quedo instalada, leida del disco",
    "$InstallDir \"agent\\version.py\"" in FUENTE
       or 'Join-Path $InstallDir "agent\\version.py"' in FUENTE
)

comprobar(
    "y se explica como volver a la version anterior",
    "Rename-Item '$RespaldoAgent' 'agent'" in FUENTE
)

comprobar(
    "no se espera un numero fijo de segundos a que salga el Agent",
    "$limiteParada = (Get-Date).AddSeconds(30)" in FUENTE
    # El @() es obligatorio: sin el, con UN solo proceso vivo
    # .Count vale $null y la espera no detecta nada.
    and "while (@(Procesos-Del-Agent).Count -gt 0" in FUENTE
)

# Antes el mensaje decia "No se ha modificado nada", y era falso:
# las tareas ya estaban paradas. Ahora se intenta cerrar los
# procesos y, si aun asi siguen, se repone el estado anterior.
comprobar(
    "y si no sale, no se copia nada encima",
    "El Agent anterior se ha dejado funcionando." in FUENTE
    and "No se ha modificado nada." not in FUENTE
)


# ==================================================================
# Ejecutar el bloque de verdad
# ==================================================================

def correr(instalacion_previa=True, origen_valido=True):
    """
    Ejecuta el bloque de respaldo con las variables que tendria.

    Devuelve (codigo, salida, carpeta). La carpeta se deja al que
    llama para que mire lo que quedo dentro.
    """

    base = Path(tempfile.mkdtemp(prefix="veyra-act-"))

    destino = base / "destino"
    origen = base / "origen"

    # Los datos del equipo, que no se deben tocar
    (destino / "config").mkdir(parents=True)
    (destino / "config" / "identity.json").write_text(
        '{"device_id": "abc123", "agent_token": "no-mirar"}'
    )
    (destino / "recordings").mkdir()
    (destino / "recordings" / "grabacion.mp4").write_text("video")
    (destino / "logs").mkdir()
    (destino / "logs" / "agent.log").write_text("historia")

    if instalacion_previa:
        (destino / "agent").mkdir()
        (destino / "agent" / "agent.py").write_text("VIEJO")
        (destino / "agent" / "modulo_que_ya_no_existe.py").write_text("resto")

    if origen_valido:
        (origen / "agent").mkdir(parents=True)
        (origen / "agent" / "agent.py").write_text("NUEVO")
        (origen / "agent" / "scheduler.py").write_text("NUEVO")

    guion = base / "bloque.ps1"

    preambulo = "\n".join([
        '$ErrorActionPreference = "Stop"',
        '$InstallDir = "%s"' % destino,
        '$SourceDir  = "%s"' % origen,
        '$AgentDir   = Join-Path $InstallDir "agent"',
        '$RespaldoAgent = Join-Path $InstallDir "agent.anterior"',
        # El bloque llama a esta funcion, que en el script real vive
        # mas arriba y ademas rearranca las tareas programadas. Aqui
        # se sustituye por su parte de archivos: las tareas las
        # comprueba test_instalador_no_deja_el_equipo_peor, que
        # ejecuta la fase entera.
        'function Restaurar-Lo-Que-Habia {',
        '    if ((Test-Path $RespaldoAgent) -and -not (Test-Path $AgentDir)) {',
        '        Move-Item -Path $RespaldoAgent -Destination $AgentDir -Force',
        '        Write-Host "  programa anterior repuesto"',
        '    }',
        '}',
        ""
    ])

    guion.write_text(preambulo + BLOQUE, encoding="utf-8")

    proceso = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(guion)],
        capture_output=True, text=True, timeout=120
    )

    return proceso.returncode, (proceso.stdout + proceso.stderr), destino


def datos_intactos(destino):
    return (
        (destino / "config" / "identity.json").exists()
        and (destino / "recordings" / "grabacion.mp4").read_text() == "video"
        and (destino / "logs" / "agent.log").read_text() == "historia"
    )


usadas = []

# --- 1. Actualizacion sobre una instalacion existente -------------

codigo, salida, destino = correr()
usadas.append(destino)

comprobar(
    "la actualizacion termina bien",
    codigo == 0, salida.strip()[-200:]
)

comprobar(
    "el agent.py que queda es el NUEVO",
    (destino / "agent" / "agent.py").read_text() == "NUEVO"
)

comprobar(
    "el modulo obsoleto ya no esta en agent\\",
    not (destino / "agent" / "modulo_que_ya_no_existe.py").exists()
)

comprobar(
    "pero si esta guardado en agent.anterior\\",
    (destino / "agent.anterior" / "modulo_que_ya_no_existe.py").exists()
    and (destino / "agent.anterior" / "agent.py").read_text() == "VIEJO"
)

comprobar(
    "la identidad, las grabaciones y los registros siguen intactos",
    datos_intactos(destino)
)

# --- 2. Dos actualizaciones seguidas no acumulan respaldos --------

(destino / "agent" / "marca.txt").write_text("segunda vuelta")

guion = destino.parent / "bloque.ps1"

repetido = subprocess.run(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
     "-File", str(guion)],
    capture_output=True, text=True, timeout=120
)

comprobar(
    "una segunda actualizacion vuelve a terminar bien",
    repetido.returncode == 0,
    (repetido.stdout + repetido.stderr).strip()[-200:]
)

comprobar(
    "solo se conserva el respaldo mas reciente",
    (destino / "agent.anterior" / "marca.txt").exists()
    and not (destino / "agent.anterior2").exists()
)

comprobar(
    "y los datos del equipo siguen intactos tras la segunda",
    datos_intactos(destino)
)

# --- 3. Instalacion limpia: no hay nada que respaldar -------------

codigo, salida, limpio = correr(instalacion_previa=False)
usadas.append(limpio)

comprobar(
    "en un equipo sin Agent previo tambien termina bien",
    codigo == 0, salida.strip()[-200:]
)

comprobar(
    "se copia el Agent",
    (limpio / "agent" / "agent.py").read_text() == "NUEVO"
)

comprobar(
    "y no se inventa una carpeta de respaldo",
    not (limpio / "agent.anterior").exists()
)

# --- 4. Si la copia falla, se repone lo anterior ------------------

codigo, salida, roto = correr(origen_valido=False)
usadas.append(roto)

comprobar(
    "una copia que falla se informa como fallo",
    codigo != 0, salida.strip()[-200:]
)

comprobar(
    "el equipo se queda con el Agent ANTERIOR, no sin ninguno",
    (roto / "agent" / "agent.py").exists()
    and (roto / "agent" / "agent.py").read_text() == "VIEJO"
)

comprobar(
    "se dice en pantalla que se repuso",
    "programa anterior repuesto" in salida
)

comprobar(
    "y los datos del equipo siguen intactos tras el fallo",
    datos_intactos(roto)
)


# ==================================================================

for carpeta in usadas:
    shutil.rmtree(carpeta.parent, ignore_errors=True)

print()
print("Actualizacion sobre una instalacion existente")
print("=" * 62)

fallos = 0

for nombre, bien, detalle in resultados:

    print("  %s  %s" % ("OK  " if bien else "FALLA", nombre))

    if not bien:
        fallos += 1
        if detalle:
            print("         %s" % detalle)

print()
print("%d de %d" % (len(resultados) - fallos, len(resultados)))
print()

sys.exit(1 if fallos else 0)
