"""
Que una instalacion fallida no deje el equipo peor que antes.

    .venv\\Scripts\\python tests/test_instalador_no_deja_el_equipo_peor.py

El fallo que la motiva
----------------------

En un equipo de produccion, al actualizar:

    La instalacion termino con errores (codigo 1).

y el equipo desaparecio del panel. El instalador habia parado las
tareas programadas para poder reemplazar los archivos, abortó
despues, y NO volvio a arrancarlas. El Agent se quedo muerto hasta
que alguien fue a la maquina a levantarlo a mano.

Y el mensaje decia "No se ha modificado nada", que era falso.

Habia un segundo agujero en el mismo tramo: el Move-Item que aparta
la carpeta 'agent' estaba FUERA del try que la reponia. Si fallaba a
medias -un archivo bloqueado, un .pyd todavia cargado- el equipo se
quedaba sin programa y sin nada que lo repusiera.

Por que no lo cazo test_guardas_instalador
------------------------------------------

Esa prueba ejecuta el script de verdad, pero solo el bloque 2b: las
comprobaciones de identidad. La fase que para las tareas y reemplaza
los archivos nunca se ejecutaba en ninguna prueba. Pasaba 32/32 y el
instalador tumbaba un equipo.

Lo que hace esta
----------------

Extrae la fase de reemplazo del script REAL -de donde se paran las
tareas hasta que termina la copia- y la ejecuta en PowerShell contra
una carpeta temporal, sustituyendo las ordenes que tocarian el
equipo:

    Get-ScheduledTask / Stop-ScheduledTask / Start-ScheduledTask
    Get-CimInstance / Stop-Process

Asi se puede provocar cada fallo a voluntad -que la copia reviente,
que los procesos no mueran- y comprobar como queda el equipo
despues. No se registra ninguna tarea ni se toca ninguna
instalacion.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GUION = os.path.join(RAIZ, "installer", "install-agent.ps1")


resultados = []


def comprobar(nombre, bien, detalle=""):
    resultados.append((nombre, bool(bien), detalle))


def fuente_de(ruta_relativa):
    with io.open(os.path.join(RAIZ, ruta_relativa), encoding="utf-8") as f:
        return f.read()


def fase_de_reemplazo():
    """
    El tramo del script real que esta prueba ejecuta.

    Se recorta por dos anclas que existen en el propio script, no por
    numeros de linea: moverlo de sitio no rompe la prueba, y quitar
    la proteccion si la rompe, que es lo que se quiere.
    """

    with io.open(GUION, encoding="utf-8") as f:
        texto = f.read()

    inicio = texto.index("$tareasDetenidas = $false")
    fin = texto.index("# fin del respaldo")

    return texto[inicio:fin]


FASE = fase_de_reemplazo()


def correr(copia_falla=False, procesos_inmortales=False,
           hay_programa_anterior=True):
    """
    Ejecuta la fase y devuelve (codigo, salida, carpeta de datos).

    copia_falla          el paquete de origen no tiene 'agent'
    procesos_inmortales  el Agent nunca termina, ni matandolo
    hay_programa_anterior  ya hay una instalacion previa
    """

    carpeta = tempfile.mkdtemp(prefix="instalador-")

    datos = os.path.join(carpeta, "RemoteAdmin")
    origen = os.path.join(carpeta, "paquete")

    os.makedirs(datos)
    os.makedirs(origen)

    # Lo que el equipo ya tenia: el programa y sus datos.
    if hay_programa_anterior:
        os.makedirs(os.path.join(datos, "agent"))

        with io.open(os.path.join(datos, "agent", "agent.py"),
                     "w", encoding="utf-8") as f:
            f.write("# version ANTERIOR\n")

    # Los datos del equipo. No deben moverse pase lo que pase.
    os.makedirs(os.path.join(datos, "config"))
    os.makedirs(os.path.join(datos, "recordings"))

    with io.open(os.path.join(datos, "config", "identity.json"),
                 "w", encoding="utf-8") as f:
        json.dump({"device_id": "equipo-de-prueba",
                   "agent_token": "t"}, f)

    with io.open(os.path.join(datos, "recordings", "rec_1.mp4"),
                 "wb") as f:
        f.write(b"video de prueba")

    # El paquete nuevo. Si la copia debe fallar, no se crea 'agent'.
    if not copia_falla:
        os.makedirs(os.path.join(origen, "agent"))

        with io.open(os.path.join(origen, "agent", "agent.py"),
                     "w", encoding="utf-8") as f:
            f.write("# version NUEVA\n")

    arrancadas = os.path.join(carpeta, "arrancadas.txt")
    paradas = os.path.join(carpeta, "paradas.txt")

    # Las ordenes que tocarian el equipo, sustituidas. Se anotan en
    # archivos para poder comprobar DESPUES que se llamaron.
    preambulo = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        # Entre comillas simples PowerShell no interpreta nada, asi
        # que las rutas van tal cual. Escapar las barras invertidas
        # las dejaba DOBLADAS dentro del script.
        "$InstallDir = '%s'" % datos,
        "$SourceDir  = '%s'" % origen,
        "$TaskName = 'TareaDePrueba'",
        "$HelperTaskName = 'AyudanteDePrueba'",
        "",
        "function Get-ScheduledTask { param($TaskName, $ErrorAction)",
        "    [pscustomobject]@{ TaskName = $TaskName } }",
        "",
        "function Stop-ScheduledTask { param($TaskName, $ErrorAction)",
        "    Add-Content -Path '%s' -Value $TaskName }"
        % paradas,
        "",
        "function Start-ScheduledTask { param($TaskName, $ErrorAction)",
        "    Add-Content -Path '%s' -Value $TaskName }"
        % arrancadas,
        "",
        # Procesos del Agent: vivos o no, segun el caso.
        ("function Get-CimInstance { param($ClassName, $Filter, $ErrorAction)"
         "    [pscustomobject]@{ ProcessId = 4242; "
         "CommandLine = (Join-Path $InstallDir 'agent') } }"
         if procesos_inmortales else
         "function Get-CimInstance { param($ClassName, $Filter, $ErrorAction)"
         "    @() }"),
        "",
        # Matarlos no sirve: siguen ahi.
        "function Stop-Process { param($Id, $Force, $ErrorAction) }",
        "",
    ])

    guion = os.path.join(carpeta, "fase.ps1")

    with io.open(guion, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(preambulo + FASE)

    hecho = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", guion],
        capture_output=True, text=True, timeout=180
    )

    salida = (hecho.stdout or "") + (hecho.stderr or "")

    def leer(ruta):
        if not os.path.isfile(ruta):
            return []
        with io.open(ruta, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]

    return {
        "codigo": hecho.returncode,
        "salida": salida,
        "datos": datos,
        "paradas": leer(paradas),
        "arrancadas": leer(arrancadas),
        "carpeta": carpeta
    }


def hay_agent(datos):
    return os.path.isfile(os.path.join(datos, "agent", "agent.py"))


def version_instalada(datos):
    ruta = os.path.join(datos, "agent", "agent.py")

    if not os.path.isfile(ruta):
        return None

    with io.open(ruta, encoding="utf-8") as f:
        return f.read().strip()


def datos_intactos(datos):
    """La identidad y las grabaciones, que nunca deben tocarse."""

    identidad = os.path.join(datos, "config", "identity.json")
    video = os.path.join(datos, "recordings", "rec_1.mp4")

    return os.path.isfile(identidad) and os.path.isfile(video)


# ==================================================================
# 1. EL CAMINO QUE FUNCIONA
# ==================================================================

print("\n1. Una instalacion que va bien")

r = correr()

comprobar("Termina sin error", r["codigo"] == 0, r["salida"][-300:])

comprobar("Para las dos tareas antes de tocar archivos",
          sorted(r["paradas"]) == ["AyudanteDePrueba", "TareaDePrueba"],
          str(r["paradas"]))

comprobar("Queda instalada la version nueva",
          version_instalada(r["datos"]) == "# version NUEVA",
          str(version_instalada(r["datos"])))

comprobar("Guarda el programa anterior",
          os.path.isdir(os.path.join(r["datos"], "agent.anterior")))

comprobar("No restaura nada: no hizo falta",
          r["arrancadas"] == [], str(r["arrancadas"]))

comprobar("Los datos del equipo siguen ahi",
          datos_intactos(r["datos"]))

shutil.rmtree(r["carpeta"], ignore_errors=True)


# ==================================================================
# 2. LA COPIA FALLA
# ==================================================================
#
# Es el caso que dejaba el equipo sin programa: la carpeta 'agent' ya
# estaba apartada y la nueva no llego a escribirse.

print("2. Si la copia falla, el equipo vuelve a como estaba")

r = correr(copia_falla=True)

comprobar("Termina con error", r["codigo"] != 0, str(r["codigo"]))

comprobar("EL EQUIPO NO SE QUEDA SIN PROGRAMA",
          hay_agent(r["datos"]),
          "no hay agent\\agent.py")

comprobar("Y el que queda es el ANTERIOR, entero",
          version_instalada(r["datos"]) == "# version ANTERIOR",
          str(version_instalada(r["datos"])))

# Esto es lo que tumbo el equipo de produccion.
comprobar("VUELVE A ARRANCAR LAS TAREAS QUE PARO",
          sorted(r["arrancadas"]) == ["AyudanteDePrueba", "TareaDePrueba"],
          str(r["arrancadas"]))

comprobar("Lo cuenta en el registro",
          "Dejando el equipo como estaba" in r["salida"])

comprobar("Los datos del equipo siguen ahi",
          datos_intactos(r["datos"]))

shutil.rmtree(r["carpeta"], ignore_errors=True)


# ==================================================================
# 3. EL AGENT NO SE MUERE
# ==================================================================

print("3. Si el Agent no termina, se intenta cerrar antes de rendirse")

r = correr(procesos_inmortales=True)

comprobar("Intenta cerrarlos en vez de rendirse de inmediato",
          "Cerrando sus procesos" in r["salida"],
          r["salida"][-200:])

comprobar("Si aun asi siguen, se detiene", r["codigo"] != 0)

comprobar("Pero deja el programa anterior en su sitio",
          hay_agent(r["datos"])
          and version_instalada(r["datos"]) == "# version ANTERIOR",
          str(version_instalada(r["datos"])))

comprobar("Y LO DEJA FUNCIONANDO",
          sorted(r["arrancadas"]) == ["AyudanteDePrueba", "TareaDePrueba"],
          str(r["arrancadas"]))

# El mensaje anterior decia "No se ha modificado nada" cuando SI
# habia parado las tareas. Mentir en un mensaje de error manda a
# quien lo lee a buscar en el sitio equivocado.
comprobar("El mensaje ya no dice que no se modifico nada",
          "No se ha modificado nada" not in r["salida"],
          "sigue mintiendo")

comprobar("Dice lo que de verdad paso",
          "se ha dejado funcionando" in r["salida"],
          r["salida"][-200:])

comprobar("Los datos del equipo siguen ahi",
          datos_intactos(r["datos"]))

shutil.rmtree(r["carpeta"], ignore_errors=True)


# ==================================================================
# 4. INSTALACION LIMPIA
# ==================================================================
#
# Sin programa anterior no hay nada que reponer, y el fallo no debe
# inventarse una restauracion de algo que no existia.

print("4. En un equipo sin instalacion previa")

r = correr(copia_falla=True, hay_programa_anterior=False)

comprobar("Termina con error", r["codigo"] != 0)

comprobar("No inventa un programa que no habia",
          not os.path.isdir(os.path.join(r["datos"], "agent")))

comprobar("Y los datos siguen intactos", datos_intactos(r["datos"]))

shutil.rmtree(r["carpeta"], ignore_errors=True)


# ==================================================================
# 5. EL TRAMO SIGUE PROTEGIDO
# ==================================================================
#
# Lo de arriba comprueba el comportamiento. Esto comprueba que la
# proteccion no se quite sin querer al tocar el script mas adelante.

print("5. Que la proteccion no desaparezca en el futuro")

with io.open(GUION, encoding="utf-8") as f:
    texto = f.read()

comprobar("Hay una red general para los pasos posteriores",
          "trap {" in texto and "Restaurar-Lo-Que-Habia" in texto)

comprobar("Guardada por si todavia no se paro nada",
          "if ($tareasDetenidas)" in texto)

comprobar("El Move-Item esta dentro del try que lo repone",
          texto.index("try {", texto.index("# inicio del respaldo"))
          < texto.index("Move-Item -Path $AgentDir"))

comprobar("Restaurar no se ejecuta dos veces",
          "$script:yaRestaurado" in texto)

# PowerShell deshace un array de un elemento, y .Count sobre el
# objeto suelto vale $null. Sin el @() de fuera, la comprobacion de
# "sigue vivo el Agent" fallaba justo con UN proceso -el caso de una
# actualizacion- y el instalador reemplazaba archivos en uso.
comprobar("La cuenta de procesos resiste que haya uno solo",
          "(Procesos-Del-Agent).Count" not in texto.replace(
              "@(Procesos-Del-Agent).Count", ""),
          "falta el @() y con un proceso dara $null")

comprobar("Y en los tres sitios donde se cuenta",
          texto.count("@(Procesos-Del-Agent)") == 3,
          str(texto.count("@(Procesos-Del-Agent)")))


# ==================================================================
# 6. EL AGENT TIENE QUE PODER ARRANCAR
# ==================================================================
#
# En Windows Sandbox -un Windows limpio, el caso "equipo nuevo"- la
# instalacion termino con codigo 0, registro las dos tareas... y el
# Agent nunca arranco. Ni una linea de registro. Sin alta y sin
# identity.json, pero con un mensaje diciendo "instalado y
# corriendo".
#
# La causa: el Python EMBEBIDO que viaja en el instalador trae un
# archivo python313._pth, y su sola presencia hace que Python NO
# añada la carpeta del script a sys.path. El Agent moria en su
# primer import de un modulo vecino.
#
# helper.py ya se protegia de esto. agent.py no, y por eso el
# ayudante arrancaba y el Agent no.

print("6. Que el Agent pueda importar sus propios modulos")

agente = fuente_de("agent/agent.py")

comprobar(
    "agent.py se pone su carpeta en sys.path",
    "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))"
    in agente
)

# Antes de los imports de modulos vecinos, no despues.
# Por numero de LINEA y solo donde la linea empieza por el import.
# Buscar el texto suelto encontraba la mencion dentro del propio
# comentario que explica el fallo, que va antes.
lineas_agente = agente.splitlines()

posicion_sys_path = next(
    i for i, l in enumerate(lineas_agente)
    if l.startswith("sys.path.insert(0, os.path.dirname")
)

primer_vecino = min(
    i for i, l in enumerate(lineas_agente)
    if any(l.startswith("from %s import" % m)
           for m in ("recorder", "commands", "storage"))
)

comprobar(
    "y lo hace ANTES de importarlos",
    posicion_sys_path < primer_vecino,
    "la linea llega tarde: el import ya habra fallado"
)

# El runtime embebido: si algun dia desaparece el ._pth, esta
# proteccion sobra pero no estorba. Si sigue ahi, es imprescindible.
ruta_pth = os.path.join(RAIZ, "installer", "runtime", "python313._pth")

if os.path.isfile(ruta_pth):
    with io.open(ruta_pth, encoding="utf-8") as f:
        pth = f.read()

    comprobar(
        "El runtime embebido sigue en modo aislado (por eso hace falta)",
        "import site" in pth,
        pth.replace("\n", " | ")
    )


print("7. Y que no se declare exito si el Agent no arranco")

comprobar(
    "Se comprueba que el Agent haya escrito registro",
    "$escribioRegistro = Test-Path $LogFile" in texto
)

comprobar(
    "Sin registro, la instalacion falla en vez de decir que fue bien",
    "EL AGENT NO ARRANCO." in texto
    and 'throw "El Agent no arranco.' in texto
)

comprobar(
    "Y se dice como ver el error",
    "Para ver por que muere" in texto
)


# ==================================================================
# 8. LOS PERMISOS DE config\ DEBEN HEREDARSE
# ==================================================================
#
# El instalador quita la herencia de config\ y da acceso solo a
# SYSTEM y a los administradores. Pero lo hacia SIN (OI)(CI), y esos
# permisos valen entonces para la CARPETA y no para lo que se cree
# dentro.
#
# El identity.json lo escribe el Agent DESPUES, corriendo como
# SYSTEM. Sin herencia y sin (OI)(CI), ese archivo quedaba accesible
# solo para SYSTEM: el propio instalador, ejecutandose como
# administrador, no podia leerlo en la siguiente actualizacion y se
# detenia con "No se pudo leer identity.json".
#
# Resultado: el equipo quedaba instalado y funcionando, pero ya no se
# podia volver a actualizar NUNCA. Se vio en Windows Sandbox
# instalando dos veces seguidas, y es el mismo error que aparecio en
# un equipo de produccion.

print("8b. Que un aviso inofensivo no tumbe la instalacion")

# En un equipo de verdad, la instalacion murio aqui:
#
#   & $Python -c "import mss, wmi, cryptography, websockets" 2>$null
#   wmi.py:32: SyntaxWarning: invalid escape sequence '\_'
#   TerminatingError: ... "ErrorActionPreference" ...
#
# Los imports funcionaban. Lo que paso es que el modulo wmi escribe
# ese aviso por la salida de ERROR, y en PowerShell 5.1 redirigir la
# salida de error de un programa EXTERNO envuelve cada linea en un
# error; con la preferencia en Stop, eso aborta el script entero.
#
# El 2>$null que pretendia silenciarlo era justo lo que lo
# provocaba.
#
# No salio en Windows Sandbox porque el aviso solo aparece la
# primera vez que se compila el .pyc, y alli el runtime ya lo traia
# hecho.

bloque_imports = texto.split("import mss, wmi, cryptography, websockets")[0]

comprobar(
    "Los avisos de Python se silencian en el origen (-W ignore)",
    "-W ignore -c \"import mss, wmi" in texto
)

comprobar(
    "Y un aviso por la salida de error no aborta el script",
    '$ErrorActionPreference = "Continue"' in bloque_imports[-900:],
    "falta bajar la preferencia alrededor de la llamada"
)

comprobar(
    "Lo que decide es el codigo de salida, no lo que se escribio",
    "$codigoImports = $LASTEXITCODE" in texto
    and "if ($codigoImports -ne 0)" in texto
)

comprobar(
    "Y la preferencia se restaura despues",
    "$ErrorActionPreference = $preferenciaAnterior" in texto
)

# pip hace lo mismo: avisa de versiones por la salida de error.
comprobar(
    "pip tambien queda protegido",
    "$codigoPip = $LASTEXITCODE" in texto
)


print("8. Que identity.json siga siendo legible al actualizar")

linea_config = next(
    (l for l in texto.splitlines() if "icacls $ConfigDir" in l),
    ""
)

comprobar(
    "El permiso de config\\ se hereda a los archivos de dentro",
    "(OI)(CI)" in texto.split("icacls $ConfigDir")[1][:200],
    linea_config.strip()[:90]
)

comprobar(
    "Para SYSTEM y para los administradores",
    'SYSTEM:(OI)(CI)(F)' in texto
    and 'Administrators:(OI)(CI)(F)' in texto
)

# Una carpeta que ya existia lleva los permisos viejos: la herencia
# solo alcanza a lo que se cree despues.
comprobar(
    "Y se corrigen los archivos de una instalacion anterior",
    "icacls $ConfigDir /reset /t" in texto
)

# Lo que NO debe pasar: abrirlo a todo el mundo. El archivo lleva el
# token individual del equipo.
comprobar(
    "Sin abrirlo a cualquier usuario del equipo",
    "Users:(OI)(CI)" not in texto and "Everyone" not in texto
)


# ==================================================================

print()
print("-" * 52)

fallos = 0

for nombre, bien, detalle in resultados:
    if not bien:
        print(f"[FALLA] {nombre}  -> {detalle}")
        fallos += 1

print(f"{len(resultados) - fallos}/{len(resultados)} comprobaciones")

sys.exit(1 if fallos else 0)
