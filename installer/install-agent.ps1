# ==============================================================
# RemoteAdmin - Instalador del Agent para Windows
# ==============================================================
#
# Instala el Agent en el equipo que se quiere administrar:
#
#   1. copia el Agent a C:\ProgramData\RemoteAdmin
#   2. escribe la configuracion (servidor y credencial de alta)
#   3. instala las dependencias de Python
#   4. registra la tarea programada que lo arranca
#   5. lo inicia
#
# Uso, en PowerShell COMO ADMINISTRADOR, desde la carpeta donde se
# descomprimio el paquete:
#
#   .\installer\install-agent.ps1
#
# Pide la credencial de alta por pantalla. Tambien se puede pasar:
#
#   .\installer\install-agent.ps1 -EnrollmentToken "rae_..."
#
# pero NO es lo recomendable: asi queda escrita en el historial de
# PowerShell y en la lista de procesos. Sin el parametro, se teclea
# oculta y no se guarda en ningun sitio.
#
# El instalador grafico la pasa con -EnrollmentTokenFile, que evita
# los dos problemas: no aparece en el historial ni en la lista de
# procesos, y el archivo se borra al leerlo.
#
# El servidor viene ya configurado en el .env del paquete descargado;
# -Server solo hace falta para cambiarlo.

[CmdletBinding()]
param(
    [string]$Server = "",
    [string]$EnrollmentToken = "",

    # Archivo del que leer la credencial, en vez de pasarla como
    # argumento. Lo usa el instalador grafico: un argumento queda a la
    # vista en la lista de procesos de todo el equipo mientras el
    # script corre, y una credencial de alta sirve para meter equipos
    # en la organizacion. El archivo se borra en cuanto se lee.
    [string]$EnrollmentTokenFile = "",

    # Tarea del servicio de fondo. Conserva el nombre de siempre para
    # que una reinstalacion sobre un equipo ya instalado reemplace la
    # tarea anterior en vez de dejar dos.
    [string]$TaskName = "RemoteAdminAgent",

    # Tarea del ayudante interactivo
    [string]$HelperTaskName = "RemoteAdminAgentHelper",

    # Carpeta con el Python embebido que trae el instalador grafico,
    # ya con todas las dependencias dentro. Si se indica, el equipo
    # destino NO necesita Python instalado y no se ejecuta ningun pip.
    #
    # Vacio = comportamiento de siempre: se busca el Python del
    # sistema y se instalan las dependencias con pip. Se conserva
    # para no romper las instalaciones que ya se hacen asi.
    [string]$RuntimeDir = "",

    # Salta las comprobaciones de identidad de la seccion 2b.
    #
    # Solo para quien sepa lo que hace. Instalar un equipo que ya
    # tiene identidad de OTRO servidor lo deja sin poder
    # autenticarse, y no se le vuelve a pedir credencial: el Agent se
    # queda en 401 indefinidamente.
    #
    # Y aunque despues se aparte la identidad a mano, el equipo se da
    # de alta con un device_id NUEVO: el servidor anterior lo da por
    # desaparecido y sus grabaciones historicas se quedan atadas al
    # identificador viejo.
    [switch]$Forzar
)

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------
# 0. Registro de la instalacion
# --------------------------------------------------------------
#
# Se abre ANTES que ninguna comprobacion, porque los fallos que mas
# cuesta diagnosticar son justo los que ocurren antes de crear nada:
# el script aborta, la ventana muestra un "codigo 1" sin mas, y en el
# equipo no queda ni rastro de por que.
#
# Va a ProgramData y no a logs\ del destino por la misma razon: esa
# carpeta puede no existir todavia. La ruta es fija para que el
# instalador grafico pueda nombrarla sin adivinarla.
#
# Se anade al archivo en vez de reemplazarlo: cuando alguien reintenta
# una instalacion que fallo, lo util es ver los dos intentos.
$TranscriptFile = Join-Path $env:ProgramData "VEYRA-instalacion.log"
$Transcribiendo = $false

try {
    Start-Transcript -Path $TranscriptFile -Append | Out-Null
    $Transcribiendo = $true
} catch {
    # La transcripcion puede estar desactivada por directiva de grupo.
    # No es motivo para no instalar: solo se pierde el registro.
    Write-Host "Aviso: no se pudo abrir el registro en $TranscriptFile"
}

# Carpeta del paquete descomprimido = la superior a installer\
$SourceDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Carpeta de datos: la misma que usa agent\paths.py. Sobrevive a las
# actualizaciones del Agent, asi que la identidad y las grabaciones no
# se pierden al reinstalar.
$InstallDir = Join-Path $env:ProgramData "RemoteAdmin"

$EnvFile = Join-Path $InstallDir ".env"
$AgentScript = Join-Path $InstallDir "agent\agent.py"
$HelperScript = Join-Path $InstallDir "agent\helper.py"
$IdentityFile = Join-Path $InstallDir "config\identity.json"
$LogFile = Join-Path $InstallDir "logs\agent.log"

Write-Host ""
Write-Host "RemoteAdmin - Instalacion del Agent"
Write-Host "-----------------------------------"

# --------------------------------------------------------------
# 1. Comprobaciones previas
# --------------------------------------------------------------

$esAdministrador = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $esAdministrador) {
    throw "Ejecuta PowerShell como administrador y vuelve a intentarlo."
}

if (-not (Test-Path (Join-Path $SourceDir "agent\agent.py"))) {
    throw "No se encontro agent\agent.py junto a este script. Ejecutalo desde la carpeta del paquete descomprimido."
}

# El interprete: el que trae el instalador, o el del sistema.
#
# Con $RuntimeDir el equipo destino no necesita Python de ninguna
# clase. Es el camino del instalador grafico. Sin el, se busca el del
# sistema como siempre.
$RuntimeDestino = Join-Path $InstallDir "runtime"
$UsaRuntimeEmbebido = $false

if ($RuntimeDir) {

    if (-not (Test-Path (Join-Path $RuntimeDir "python.exe"))) {
        throw "No se encontro python.exe en el runtime indicado: $RuntimeDir"
    }

    $UsaRuntimeEmbebido = $true
    $Python = Join-Path $RuntimeDestino "python.exe"

    Write-Host "Python:  embebido ($RuntimeDir)"

} else {

    $Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source

    if (-not $Python) {
        throw "No se encontro Python. Instalalo desde https://www.python.org/downloads/windows/ marcando 'Add python.exe to PATH' y vuelve a ejecutar este script."
    }

    Write-Host "Python:  $Python"
}
Write-Host "Destino: $InstallDir"

# --------------------------------------------------------------
# 1b. A que servidor
# --------------------------------------------------------------
#
# Se resuelve AQUI, y no al escribir el .env, porque las guardas de
# identidad de la seccion 2b necesitan saber el destino para poder
# detenerse antes de copiar nada.

if ($Server -eq "") {

    $origenEnv = Join-Path $SourceDir ".env"

    if (Test-Path $origenEnv) {
        $linea = Select-String -Path $origenEnv -Pattern "^REMOTEADMIN_SERVER=(.+)$"
        if ($linea) {
            $Server = $linea.Matches[0].Groups[1].Value.Trim()
        }
    }
}

if ($Server -eq "") {
    throw "No se sabe a que servidor conectar. Vuelve a descargar el paquete desde el panel, o pasa -Server 'https://...'."
}

if (-not ($Server.StartsWith("http://") -or $Server.StartsWith("https://"))) {
    throw "La direccion del servidor debe empezar por http:// o https://"
}

# Sin barra final: el Agent compone /api/... y /ws/agent detras, y
# "https://host//api" no es la misma cadena que "https://host/api"
# cuando se comparan direcciones.
$Server = $Server.TrimEnd("/")

# --------------------------------------------------------------
# 2. Credencial de alta
# --------------------------------------------------------------
#
# Si el equipo YA tiene identidad, no hace falta ninguna credencial:
# el Agent se autentica con su token individual. Pedirla de nuevo
# seria pedir un secreto para nada.

# La credencial puede llegar en un archivo, que se lee y se borra
# antes de seguir.
if ($EnrollmentTokenFile -ne "" -and (Test-Path $EnrollmentTokenFile)) {

    $archivo = Get-Item -LiteralPath $EnrollmentTokenFile -Force

    # Este script corre con permisos de administrador y la ruta la
    # da un proceso que no los tiene. Si en esa ruta hubiera un
    # enlace o una union de directorios, se leeria lo que apunte el
    # enlace en vez del archivo que se escribio, y con mas permisos
    # de los que tenia quien lo puso ahi.
    if ($archivo.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "El archivo de la credencial es un enlace. No se continua."
    }

    if ($archivo.PSIsContainer) {
        throw "La ruta de la credencial es una carpeta. No se continua."
    }

    # Una credencial son unas decenas de bytes. El limite evita leer
    # un archivo enorme si la ruta apuntara a otra cosa.
    if ($archivo.Length -gt 4096) {
        throw "El archivo de la credencial es demasiado grande. No se continua."
    }

    $EnrollmentToken = (Get-Content -LiteralPath $EnrollmentTokenFile -Raw).Trim()

    # Se sobrescribe antes de borrar para reducir la ventana en que
    # el contenido sigue legible. NO garantiza un borrado fisico: en
    # SSD y en sistemas de archivos con journaling los bytes
    # originales pueden sobrevivir en bloques ya desasociados. La
    # garantia real es que la credencial caduca y se puede revocar.
    try {
        Set-Content -Path $EnrollmentTokenFile -Value ("0" * 256) -Force
    } catch {}

    Remove-Item -Path $EnrollmentTokenFile -Force -ErrorAction SilentlyContinue
}

$yaEnrolado = Test-Path $IdentityFile

# --------------------------------------------------------------
# 2b. Que la identidad sea de ESTE servidor
# --------------------------------------------------------------
#
# Con identidad presente no se pide credencial -se da por hecho que
# el equipo ya esta de alta- pero mas abajo SI se reescribe el .env
# con la direccion nueva. Si esa identidad es de otro servidor, el
# Agent acaba presentandose con un token que el destino no conoce:
# responde 401, y como nadie le volvera a pedir credencial, se queda
# asi indefinidamente.
#
# Lo peor de ese fallo es cuando se nota. La instalacion dice que
# todo fue bien, y el problema aparece horas despues, mirando por que
# el equipo no sale en el panel.
#
# Dos comprobaciones, y van ANTES de copiar nada: si alguna falla, el
# equipo queda exactamente como estaba.

if ($yaEnrolado -and -not $Forzar) {

    $identidad = $null

    try {
        $identidad = Get-Content $IdentityFile -Raw | ConvertFrom-Json
    } catch {
        throw ("No se pudo leer $IdentityFile. Si esta corrupto, " +
               "apartalo a mano y vuelve a ejecutar: " +
               "Rename-Item '$IdentityFile' 'identity.ANTES.json'")
    }

    $comoSeguir = @(
        "",
        "La instalacion NO ha modificado nada. Tienes dos salidas:",
        "",
        "  1. Instalar en otro equipo. Es lo recomendable.",
        "",
        "  2. Dar de alta ESTE equipo como uno nuevo, apartando su",
        "     identidad a mano:",
        "",
        "       Rename-Item '$IdentityFile' 'identity.ANTES.json'",
        "",
        "     Renombrar, no borrar: asi se puede deshacer. Pero el",
        "     equipo recibira un device_id NUEVO, el servidor anterior",
        "     lo dara por desaparecido, y sus grabaciones historicas",
        "     se quedaran atadas al identificador viejo.",
        "",
        "Este script no aparta ninguna identidad por su cuenta.",
        "Con -Forzar se salta esta comprobacion, con esas consecuencias."
    )

    # --- Guarda A: la direccion que quedo configurada -------------
    #
    # Gratis y sin red. No es concluyente -el .env se edita a mano y
    # puede faltar- pero atrapa el caso corriente al instante.
    #
    # Se lee el .env INSTALADO, no el del paquete: son cosas
    # distintas, y mirar el del paquete fue justo lo que dejaba pasar
    # este fallo.

    $servidorAnterior = ""

    if (Test-Path $EnvFile) {
        $previa = Select-String -Path $EnvFile -Pattern "^REMOTEADMIN_SERVER=(.+)$"
        if ($previa) {
            $servidorAnterior = $previa.Matches[0].Groups[1].Value.Trim().TrimEnd("/")
        }
    }

    if ($servidorAnterior -ne "" -and $servidorAnterior -ne $Server) {

        Write-Host ""
        Write-Host "Este equipo ya esta dado de alta en OTRO servidor."
        Write-Host "  configurado para : $servidorAnterior"
        Write-Host "  se pedia instalar: $Server"
        $comoSeguir | ForEach-Object { Write-Host $_ }

        throw "Identidad de otro servidor. No se instala."
    }

    # --- Guarda B: preguntarselo al servidor ----------------------
    #
    # La concluyente. GET /api/agent/version se autentica con el
    # token INDIVIDUAL y no modifica nada en el servidor.
    #
    # Se usa ese y no el latido a proposito: el latido actualiza la
    # ultima conexion del equipo. Y tampoco se prueba contra
    # register, porque un intento fallido ahi cuenta para el limite
    # por IP y estorbaria una instalacion de verdad.
    #
    # La validacion TLS se deja ACTIVADA. Un servidor cuyo
    # certificado no valida no es el servidor al que se queria
    # conectar, y seguir seria peor que detenerse.

    Write-Host ""
    Write-Host "Comprobando que la identidad sea de este servidor..."

    $reconocido = $null
    $motivo = ""

    try {

        $respuesta = Invoke-WebRequest `
            -Uri "$Server/api/agent/version" `
            -Headers @{ "X-Agent-Token" = $identidad.agent_token } `
            -TimeoutSec 20 `
            -UseBasicParsing

        $reconocido = ($respuesta.StatusCode -eq 200)

    } catch {

        $codigo = $null

        if ($_.Exception.Response) {
            $codigo = [int]$_.Exception.Response.StatusCode
        }

        if ($codigo -eq 401 -or $codigo -eq 403) {
            $reconocido = $false
        } else {
            $motivo = $_.Exception.Message
        }
    }

    if ($reconocido -eq $false) {

        Write-Host ""
        Write-Host "El servidor NO reconoce la identidad de este equipo."
        Write-Host "  servidor : $Server"
        Write-Host "  device_id: $($identidad.device_id)"
        $comoSeguir | ForEach-Object { Write-Host $_ }

        throw "El servidor no reconoce la identidad. No se instala."
    }

    if ($reconocido -eq $null) {

        # Indeterminado. Se detiene igual: no saber de quien es la
        # identidad no es mejor que saber que es de otro, y
        # continuar a ciegas es como se llega al 401 permanente.

        Write-Host ""
        Write-Host "No se pudo comprobar la identidad con el servidor."
        Write-Host "  servidor : $Server"
        Write-Host "  motivo   : $motivo"
        Write-Host ""
        Write-Host "Puede ser falta de red, un cortafuegos, un certificado"
        Write-Host "que no valida o el servidor caido. No se continua sin"
        Write-Host "saber si la identidad es de este servidor."
        $comoSeguir | ForEach-Object { Write-Host $_ }

        throw "No se pudo comprobar la identidad. No se instala."
    }

    Write-Host "La identidad es de este servidor. Se conserva."
}

if ($yaEnrolado -and $Forzar) {

    Write-Host ""
    Write-Host "AVISO: -Forzar. No se comprueba de que servidor es la"
    Write-Host "identidad. Si es de otro, el Agent quedara en 401."
}

# fin de 2b

if ($yaEnrolado) {

    Write-Host ""
    Write-Host "Este equipo ya esta dado de alta. Se conserva su identidad."

} elseif ($EnrollmentToken -eq "") {

    Write-Host ""
    Write-Host "Credencial de instalacion (empieza por rae_)."
    Write-Host "La generas en el panel, en 'Instalar equipo'."

    $segura = Read-Host "Credencial" -AsSecureString

    $EnrollmentToken = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura)
    )
}

if (-not $yaEnrolado) {

    if ($EnrollmentToken -eq "") {
        throw "Sin credencial de instalacion no se puede dar de alta el equipo."
    }

    if (-not $EnrollmentToken.StartsWith("rae_")) {
        throw "Esa no parece una credencial de instalacion: deben empezar por 'rae_'."
    }
}

# --------------------------------------------------------------
# 3. Copiar el Agent
# --------------------------------------------------------------

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Host ""
Write-Host "Copiando el Agent..."

# Solo el programa y sus dependencias. NO se copia config\, recordings\
# ni logs\: son los datos del equipo y deben sobrevivir a una
# reinstalacion.
#
# A partir de AQUI el equipo queda tocado: se paran las tareas. Si el
# script aborta mas adelante sin volver a arrancarlas, el Agent se
# queda muerto y el equipo desaparece del panel.
#
# Paso exactamente eso en un equipo de produccion: la instalacion
# fallo, dijo "no se ha modificado nada" -que era falso- y el equipo
# se quedo desconectado hasta que alguien fue a arrancarlo a mano.
#
# Por eso todo lo que sigue va dentro de un try cuyo catch deja el
# equipo como estaba antes de empezar.
$tareasDetenidas = $false

$yaRestaurado = $false

function Restaurar-Lo-Que-Habia {
    <#
        Deshace lo que se hizo, en orden inverso.

        Se llama desde el catch de cada paso que ya haya tocado algo.
        Es deliberadamente tolerante a fallos: si no puede reponer
        una cosa, lo dice y sigue con las demas. Un equipo con el
        Agent viejo corriendo es mejor que uno sin nada.
    #>

    # Puede llamarse desde un catch concreto y despues desde la red
    # general. Restaurar dos veces no rompe nada, pero contarlo dos
    # veces confunde a quien lee el registro.
    if ($script:yaRestaurado) {
        return
    }

    $script:yaRestaurado = $true

    Write-Host ""
    Write-Host "La instalacion fallo. Dejando el equipo como estaba..."

    # 1. Reponer el programa anterior, si se habia apartado y no hay
    #    uno nuevo en su sitio.
    if ((Test-Path $RespaldoAgent) -and -not (Test-Path $AgentDir)) {
        try {
            Move-Item -Path $RespaldoAgent -Destination $AgentDir -Force
            Write-Host "  programa anterior repuesto"
        } catch {
            Write-Host "  NO se pudo reponer el programa anterior:"
            Write-Host "    esta en $RespaldoAgent"
        }
    }

    # 2. Volver a arrancar lo que se paro.
    if ($tareasDetenidas) {
        foreach ($tarea in @($TaskName, $HelperTaskName)) {
            if (Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue) {
                try {
                    Start-ScheduledTask -TaskName $tarea
                    Write-Host "  tarea '$tarea' arrancada de nuevo"
                } catch {
                    Write-Host "  NO se pudo arrancar '$tarea'. Reinicia el equipo."
                }
            }
        }
    }

    Write-Host ""
}

# Se definen antes de pararlas: Restaurar-Lo-Que-Habia los necesita.
$AgentDir = Join-Path $InstallDir "agent"
$RespaldoAgent = Join-Path $InstallDir "agent.anterior"

# La red general. Los try/catch concretos cubren los dos pasos mas
# delicados; esto cubre TODO lo demas que venga despues -copiar el
# runtime, escribir el .env, registrar las tareas- sin tener que
# envolver cada paso a mano y sin que el proximo paso que alguien
# añada se quede fuera por olvido.
#
# El guardia de $tareasDetenidas es lo que lo hace seguro: antes de
# parar nada no hay nada que restaurar, y un fallo temprano -no eres
# administrador, falta el paquete- debe salir tal cual.
trap {

    if ($tareasDetenidas) {
        Restaurar-Lo-Que-Habia
    }

    break
}

# Si el Agent estaba corriendo, se para antes de sobrescribir sus
# archivos: en Windows no se puede reemplazar lo que esta en uso.
foreach ($tarea in @($TaskName, $HelperTaskName)) {
    if (Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
        $tareasDetenidas = $true
    }
}

# Esperar a que SALGAN de verdad, en vez de dar por hecho que dos
# segundos bastan. Detener una tarea no es inmediato, y si el proceso
# sigue vivo Copy-Item falla a mitad: el Agent queda parado y con los
# archivos a medio reemplazar, que es el peor sitio donde dejarlo.

function Procesos-Del-Agent {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$AgentDir*" })
}

$limiteParada = (Get-Date).AddSeconds(30)

# El @() de fuera NO sobra, aunque la funcion ya devuelva un array.
#
# PowerShell deshace un array de un solo elemento al devolverlo. Lo
# que llega aqui con UN proceso vivo es el objeto suelto, y .Count
# sobre un PSCustomObject vale $null. Como $null -gt 0 es falso, la
# comprobacion decia que no quedaba ninguno... justo en el caso
# corriente de una actualizacion, donde queda exactamente uno.
#
# El resultado era que el instalador seguia adelante y reemplazaba
# archivos en uso. Con dos o mas procesos funcionaba, que es lo que
# hacia el fallo tan dificil de ver.
while (@(Procesos-Del-Agent).Count -gt 0 -and (Get-Date) -lt $limiteParada) {
    Start-Sleep -Seconds 1
}

$siguenVivos = @(Procesos-Del-Agent)

if ($siguenVivos.Count -gt 0) {

    # Rendirse aqui era lo que hacia antes, y estaba mal por dos
    # razones. La primera, que dejaba las tareas paradas y el equipo
    # sin Agent. La segunda, que es evitable: detener la tarea no
    # siempre mata a sus hijos, y un proceso que ya se le pidio
    # terminar y no termino en treinta segundos no va a terminar.
    #
    # Se cierran. Son procesos del propio Agent, identificados por su
    # linea de comandos dentro de la carpeta de instalacion: no se
    # toca ningun Python ajeno que el usuario tenga abierto.
    Write-Host ""
    Write-Host "El Agent no termino solo. Cerrando sus procesos..."

    foreach ($proceso in $siguenVivos) {
        try {
            Stop-Process -Id $proceso.ProcessId -Force -ErrorAction Stop
            Write-Host "  cerrado el proceso $($proceso.ProcessId)"
        } catch {
            Write-Host "  no se pudo cerrar el proceso $($proceso.ProcessId)"
        }
    }

    Start-Sleep -Seconds 2

    $siguenVivos = @(Procesos-Del-Agent)
}

if ($siguenVivos.Count -gt 0) {

    # Ya ni cerrandolos. Aqui si hay que parar, pero dejando el
    # equipo como estaba: las tareas vuelven a arrancar.
    Restaurar-Lo-Que-Habia

    throw ("El Agent sigue en ejecucion y no se pueden reemplazar sus " +
           "archivos. Reinicia el equipo y vuelve a ejecutar el " +
           "instalador. El Agent anterior se ha dejado funcionando.")
}

# Apartar el programa anterior ANTES de escribir el nuevo.
#
# Se MUEVE, no se copia: asi la carpeta nueva nace limpia y no
# sobreviven modulos de una version anterior que ya no existen en
# esta. Copy-Item -Force sobrescribe y anade, pero nunca quita, y un
# archivo .py huerfano que todavia se importa es un fallo muy caro de
# encontrar.
#
# Solo se aparta el PROGRAMA. config\, recordings\ y logs\ no se
# tocan: son los datos del equipo.
# inicio del respaldo
#
# El Move-Item estaba FUERA del try. Si fallaba a medias -un archivo
# bloqueado, un .pyd todavia cargado- el equipo se quedaba sin
# carpeta agent y sin nada que la repusiera. Ahora los dos pasos,
# apartar y copiar, estan dentro de la misma proteccion.
try {

    if (Test-Path $AgentDir) {

        # Solo se guarda el respaldo mas reciente. Es codigo del
        # producto, reponible desde el instalador; no son datos del
        # equipo.
        if (Test-Path $RespaldoAgent) {
            Remove-Item -Path $RespaldoAgent -Recurse -Force
        }

        Move-Item -Path $AgentDir -Destination $RespaldoAgent -Force

        Write-Host "Programa anterior guardado en: $RespaldoAgent"
    }

    Copy-Item -Path (Join-Path $SourceDir "agent") -Destination $InstallDir -Recurse -Force

} catch {

    # Reponer lo que habia y volver a arrancarlo. Sin esto, un fallo
    # aqui deja el equipo sin Agent: ni el viejo ni el nuevo.
    Restaurar-Lo-Que-Habia

    throw
}
# fin del respaldo

foreach ($req in @("requirements-base.txt", "requirements-agent-windows.txt")) {
    Copy-Item -Path (Join-Path $SourceDir $req) -Destination $InstallDir -Force
}

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "installer") | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "*") -Destination (Join-Path $InstallDir "installer") -Force

# --------------------------------------------------------------
# 4. Configuracion
# --------------------------------------------------------------
#
# El .env se arma de nuevo en cada instalacion en vez de editarse a
# parches: asi no quedan lineas duplicadas ni restos de una
# instalacion anterior.

Write-Host "Servidor: $Server"

# El token conserva el que ya hubiera si este equipo se reinstala sin
# credencial nueva (caso $yaEnrolado): no se pierde nada, aunque ya no
# se use para nada.
$tokenLinea = "AGENT_TOKEN=$EnrollmentToken"

@(
    "# Configuracion del Agent de RemoteAdmin.",
    "#",
    "# AGENT_TOKEN es la credencial de ALTA de la organizacion y solo",
    "# sirve para el primer registro. Despues el Agent usa su token",
    "# individual, guardado en config\identity.json. En cuanto el",
    "# equipo aparezca en el panel, esta credencial puede revocarse.",
    "",
    "REMOTEADMIN_SERVER=$Server",
    $tokenLinea,
    "REMOTEADMIN_CA_CERT="
) | Set-Content -Encoding UTF8 -Path $EnvFile

# El .env lleva una credencial: se restringe a administradores y al
# sistema. Sin esto, cualquier usuario del equipo podria leerla y dar
# de alta equipos en la organizacion.
icacls $EnvFile /inheritance:r /grant:r "SYSTEM:(R)" "Administrators:(F)" | Out-Null

# La carpeta config\ guarda identity.json, que lleva el token
# individual de este equipo. Se protege igual: ese token ES la
# identidad del dispositivo ante el servidor.
#
# Se crea aqui, antes de que arranque el Agent, para que el archivo
# nazca ya con los permisos puestos y no exista ni un instante legible
# por cualquiera.
$ConfigDir = Join-Path $InstallDir "config"

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

# (OI)(CI) no sobra: sin ellos los permisos valen para la CARPETA y
# no para lo que se cree dentro.
#
# Como ademas se quita la herencia, el identity.json que el Agent
# escribe despues -y lo escribe como SYSTEM, que es quien corre la
# tarea- quedaba accesible SOLO para SYSTEM. El propio instalador,
# ejecutandose como administrador, no podia leerlo en la siguiente
# actualizacion y se detenia con "No se pudo leer identity.json".
#
# Es decir: el equipo quedaba instalado y funcionando, pero ya no se
# podia volver a actualizar nunca. Se vio en Windows Sandbox, al
# instalar dos veces seguidas.
#
# (OI) lo hereda cada archivo, (CI) cada subcarpeta. Quien puede
# leerlo sigue siendo el mismo: SYSTEM y los administradores del
# equipo. El token individual no se expone a nadie mas.
icacls $ConfigDir /inheritance:r `
    /grant:r "SYSTEM:(OI)(CI)(F)" "Administrators:(OI)(CI)(F)" | Out-Null

# Y si la carpeta ya existia de una instalacion anterior, sus
# archivos llevan los permisos viejos: la herencia solo alcanza a lo
# que se cree DESPUES. Se corrigen los que ya estan.
if (Test-Path (Join-Path $ConfigDir "*")) {
    icacls $ConfigDir /reset /t /c /q | Out-Null
    icacls $ConfigDir /inheritance:r `
        /grant:r "SYSTEM:(OI)(CI)(F)" "Administrators:(OI)(CI)(F)" | Out-Null
}

# --------------------------------------------------------------
# 5. Dependencias
# --------------------------------------------------------------

if ($UsaRuntimeEmbebido) {

    # El interprete y sus dependencias ya vienen resueltos: solo hay
    # que ponerlos en su sitio. Ni pip, ni red, ni esperas.
    #
    # Va a InstallDir\runtime, que NO esta en la lista de lo que el
    # actualizador reemplaza (agent/updater.py, PRESERVED), asi que
    # una actualizacion del Agent no lo toca.
    Write-Host ""
    Write-Host "Copiando el entorno de ejecucion..."

    if (Test-Path $RuntimeDestino) {
        Remove-Item -Recurse -Force $RuntimeDestino
    }

    Copy-Item -Recurse -Force $RuntimeDir $RuntimeDestino

    if (-not (Test-Path $Python)) {
        throw "No se pudo copiar el entorno de ejecucion a $RuntimeDestino"
    }

    # Que funcione de verdad, no que los archivos esten ahi
    & $Python -c "import mss, wmi, cryptography, websockets" 2>$null

    if ($LASTEXITCODE -ne 0) {
        throw "El entorno de ejecucion copiado no puede importar las dependencias del Agent."
    }

    Write-Host "Entorno listo. No se ha instalado nada en el sistema."

} else {

    Write-Host ""
    Write-Host "Instalando dependencias (puede tardar un par de minutos)..."

    & $Python -m pip install --quiet --disable-pip-version-check --upgrade pip
    & $Python -m pip install --quiet --disable-pip-version-check -r (Join-Path $InstallDir "requirements-agent-windows.txt")

    if ($LASTEXITCODE -ne 0) {
        throw "Fallo la instalacion de dependencias. Revisa la conexion a internet y vuelve a ejecutar el script."
    }
}

# --------------------------------------------------------------
# 6. Las dos tareas
# --------------------------------------------------------------
#
# El Agent se parte en dos porque Windows aisla los servicios en la
# Sesion 0, donde no hay escritorio: ahi la captura de pantalla sale
# en negro y el raton y el teclado no llegan a ninguna parte. Pero el
# latido, el inventario, los procesos y el apagado no necesitan
# escritorio y deben funcionar desde que arranca el equipo.
#
#   RemoteAdminAgent         al ARRANQUE, como SYSTEM. Habla con el
#                            servidor. No toca la pantalla.
#
#   RemoteAdminAgentHelper   al INICIAR SESION, como el usuario. Es
#                            el unico que toca el escritorio.

if ($UsaRuntimeEmbebido) {
    $Pythonw = Join-Path $RuntimeDestino "pythonw.exe"
} else {
    $Pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
}

if (-not $Pythonw) {
    $Pythonw = $Python
}

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# --- Servicio de fondo ---
#
# -AtStartup y SYSTEM: arranca con Windows, sin esperar a que nadie
# inicie sesion. Es el cambio que hace que un equipo recien reiniciado
# aparezca en el panel aunque nadie lo haya tocado.

$AccionServicio = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "`"$AgentScript`" --role=service" `
    -WorkingDirectory $InstallDir

$DisparadorServicio = New-ScheduledTaskTrigger -AtStartup

$PrincipalServicio = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $AccionServicio `
    -Trigger $DisparadorServicio `
    -Principal $PrincipalServicio `
    -Settings $Settings `
    -Description "RemoteAdmin Agent (servicio de fondo)" | Out-Null

Write-Host "Tarea '$TaskName' registrada (arranque del sistema)."

# --- Ayudante interactivo ---
#
# -AtLogOn y sesion interactiva: aqui SI hace falta el escritorio.
# -GroupId con los usuarios del equipo para que valga para cualquiera
# que inicie sesion, no solo para quien instalo.

$AccionAyudante = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "`"$HelperScript`"" `
    -WorkingDirectory $InstallDir

$DisparadorAyudante = New-ScheduledTaskTrigger -AtLogOn

$PrincipalAyudante = New-ScheduledTaskPrincipal `
    -GroupId "S-1-5-32-545" `
    -RunLevel Limited

if (Get-ScheduledTask -TaskName $HelperTaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $HelperTaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $HelperTaskName `
    -Action $AccionAyudante `
    -Trigger $DisparadorAyudante `
    -Principal $PrincipalAyudante `
    -Settings $Settings `
    -Description "RemoteAdmin Agent (ayudante interactivo)" | Out-Null

Write-Host "Tarea '$HelperTaskName' registrada (inicio de sesion)."

# --------------------------------------------------------------
# 7. Arranque
# --------------------------------------------------------------

# Tamano del registro ANTES de arrancar. En una actualizacion es la
# unica forma de distinguir "el Agent nuevo esta funcionando" de "el
# archivo de identidad ya estaba ahi desde antes".
$LogAntes = -1

if (Test-Path $LogFile) {
    $LogAntes = (Get-Item $LogFile).Length
}

Start-ScheduledTask -TaskName $TaskName

# El ayudante solo arranca si quien instala tiene sesion abierta, que
# es lo normal. Si no, entrara solo en el proximo inicio de sesion.
Start-ScheduledTask -TaskName $HelperTaskName -ErrorAction SilentlyContinue

# --------------------------------------------------------------
# 8. Confirmacion
# --------------------------------------------------------------
#
# La instalacion no termina cuando arranca el proceso, sino cuando el
# equipo esta REALMENTE dado de alta. Esperar aqui evita que alguien se
# marche creyendo que quedo instalado y descubra manana que no.

# Que version quedo instalada. Se lee del archivo copiado, no de la
# que creia traer el instalador: lo que importa es lo que hay en el
# disco del equipo.
$VersionInstalada = ""

$VersionFile = Join-Path $InstallDir "agent\version.py"

if (Test-Path $VersionFile) {
    $enc = Select-String -Path $VersionFile -Pattern '^AGENT_VERSION\s*=\s*"([^"]+)"'
    if ($enc) {
        $VersionInstalada = $enc.Matches[0].Groups[1].Value
    }
}

$limite = (Get-Date).AddSeconds(90)
$alta = $false

if ($yaEnrolado) {

    # ACTUALIZACION. El identity.json ya estaba antes de empezar, asi
    # que su presencia no demuestra nada. Lo que hay que comprobar es
    # que el programa NUEVO esta corriendo: que la tarea sigue en
    # ejecucion y que el registro ha crecido desde el arranque.
    #
    # Sin esto, una actualizacion que dejara el Agent roto terminaba
    # diciendo "instalado y dado de alta", y el fallo no se veia
    # hasta mirar el panel horas despues.

    Write-Host ""
    Write-Host "Comprobando que el Agent actualizado arranca..."

    while ((Get-Date) -lt $limite) {

        $tareaActual = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

        $creciendo = (Test-Path $LogFile) -and
                     ((Get-Item $LogFile).Length -gt $LogAntes)

        if ($tareaActual -and $tareaActual.State -eq "Running" -and $creciendo) {
            $alta = $true
            break
        }

        Start-Sleep -Seconds 3
    }

    Write-Host ""

    $identidad = Get-Content $IdentityFile -Raw | ConvertFrom-Json

    if ($alta) {

        Write-Host "Agent actualizado y en ejecucion."
        Write-Host "Identificador del equipo: $($identidad.device_id)"
        Write-Host "Version instalada: $VersionInstalada"
        Write-Host ""
        Write-Host "Es el MISMO equipo en el panel: conserva su identidad,"
        Write-Host "sus grabaciones y su configuracion."

    } else {

        Write-Host "El Agent se actualizo, pero no se ha confirmado que"
        Write-Host "este funcionando: la tarea no quedo en ejecucion o el"
        Write-Host "registro no ha crecido."
        Write-Host ""
        Write-Host "El motivo estara aqui:"
        Write-Host "  $LogFile"
        Write-Host ""
        Write-Host "Para volver a la version anterior, con el Agent parado:"
        Write-Host "  Stop-ScheduledTask -TaskName $TaskName"
        Write-Host "  Remove-Item '$AgentDir' -Recurse -Force"
        Write-Host "  Rename-Item '$RespaldoAgent' 'agent'"
        Write-Host "  Start-ScheduledTask -TaskName $TaskName"
    }

    Write-Host ""

    if ($Transcribiendo) {
        Write-Host "Registro de esta instalacion: $TranscriptFile"
        try { Stop-Transcript | Out-Null } catch {}
    }

    return
}

Write-Host ""
Write-Host "Esperando el alta en el servidor..."

while ((Get-Date) -lt $limite) {

    if (Test-Path $IdentityFile) {
        $alta = $true
        break
    }

    Start-Sleep -Seconds 3
}

Write-Host ""

if ($alta) {

    $identidad = Get-Content $IdentityFile -Raw | ConvertFrom-Json

    # Se muestra el device_id, que es un identificador. El token
    # individual que hay en el mismo archivo NO se imprime nunca.
    Write-Host "Agent instalado y dado de alta."
    Write-Host "Identificador del equipo: $($identidad.device_id)"
    Write-Host "Version instalada: $VersionInstalada"
    Write-Host ""
    Write-Host "Ya aparece en el panel, en Dispositivos."
    Write-Host "No hace falta ningun otro comando."
    Write-Host ""
    Write-Host "El servicio de fondo arranca con Windows, sin que nadie"
    Write-Host "tenga que iniciar sesion. La grabacion y el control"
    Write-Host "remoto necesitan una sesion abierta."

} else {

    # Aqui el instalador terminaba con exito y decia "instalado y
    # corriendo". No siempre era verdad: si el Agent moria al
    # arrancar, el equipo quedaba con las tareas puestas, sin dar de
    # alta, sin registro y con un mensaje tranquilizador.
    #
    # Paso de verdad: el Python embebido no añadia la carpeta del
    # script a la ruta de modulos, el Agent moria en su primer
    # import y nadie se enteraba.
    #
    # Se distingue entre "tarda" y "no arranco" mirando si llego a
    # escribir registro. Sin una sola linea no es lentitud: es que
    # el proceso no vivio lo suficiente para escribirla.
    $escribioRegistro = Test-Path $LogFile

    Write-Host ""

    if ($escribioRegistro) {

        Write-Host "El Agent esta corriendo, pero todavia no se ha"
        Write-Host "confirmado el alta."
        Write-Host ""
        Write-Host "Sigue reintentando solo; si en unos minutos no aparece"
        Write-Host "en el panel, el motivo estara aqui:"
        Write-Host "  $LogFile"

    } else {

        Write-Host "EL AGENT NO ARRANCO."
        Write-Host ""
        Write-Host "Los archivos estan instalados y las tareas registradas,"
        Write-Host "pero el programa no llego a ejecutarse: no ha escrito"
        Write-Host "ni una linea en"
        Write-Host "  $LogFile"
        Write-Host ""
        Write-Host "Para ver por que muere, ejecuta esto y lee el error:"
        Write-Host "  & '$Python' '$AgentScript'"

        throw "El Agent no arranco. El equipo no ha quedado dado de alta."
    }
}

Write-Host ""

if ($Transcribiendo) {
    Write-Host "Registro de esta instalacion: $TranscriptFile"
    try { Stop-Transcript | Out-Null } catch {}
}
