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
# El servidor viene ya configurado en el .env del paquete descargado;
# -Server solo hace falta para cambiarlo.

[CmdletBinding()]
param(
    [string]$Server = "",
    [string]$EnrollmentToken = "",

    # Tarea del servicio de fondo. Conserva el nombre de siempre para
    # que una reinstalacion sobre un equipo ya instalado reemplace la
    # tarea anterior en vez de dejar dos.
    [string]$TaskName = "RemoteAdminAgent",

    # Tarea del ayudante interactivo
    [string]$HelperTaskName = "RemoteAdminAgentHelper"
)

$ErrorActionPreference = "Stop"

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

$Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source

if (-not $Python) {
    throw "No se encontro Python. Instalalo desde https://www.python.org/downloads/windows/ marcando 'Add python.exe to PATH' y vuelve a ejecutar este script."
}

Write-Host "Python:  $Python"
Write-Host "Destino: $InstallDir"

# --------------------------------------------------------------
# 2. Credencial de alta
# --------------------------------------------------------------
#
# Si el equipo YA tiene identidad, no hace falta ninguna credencial:
# el Agent se autentica con su token individual. Pedirla de nuevo
# seria pedir un secreto para nada.

$yaEnrolado = Test-Path $IdentityFile

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
# Si el Agent estaba corriendo, se para antes de sobrescribir sus
# archivos: en Windows no se puede reemplazar lo que esta en uso.
foreach ($tarea in @($TaskName, $HelperTaskName)) {
    if (Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 2

Copy-Item -Path (Join-Path $SourceDir "agent") -Destination $InstallDir -Recurse -Force

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

icacls $ConfigDir /inheritance:r /grant:r "SYSTEM:(F)" "Administrators:(F)" | Out-Null

# --------------------------------------------------------------
# 5. Dependencias
# --------------------------------------------------------------

Write-Host ""
Write-Host "Instalando dependencias (puede tardar un par de minutos)..."

& $Python -m pip install --quiet --disable-pip-version-check --upgrade pip
& $Python -m pip install --quiet --disable-pip-version-check -r (Join-Path $InstallDir "requirements-agent-windows.txt")

if ($LASTEXITCODE -ne 0) {
    throw "Fallo la instalacion de dependencias. Revisa la conexion a internet y vuelve a ejecutar el script."
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

$Pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source

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

Write-Host ""
Write-Host "Esperando el alta en el servidor..."

$limite = (Get-Date).AddSeconds(90)
$alta = $false

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
    Write-Host ""
    Write-Host "Ya aparece en el panel, en Dispositivos."
    Write-Host "No hace falta ningun otro comando."
    Write-Host ""
    Write-Host "El servicio de fondo arranca con Windows, sin que nadie"
    Write-Host "tenga que iniciar sesion. La grabacion y el control"
    Write-Host "remoto necesitan una sesion abierta."

} else {

    Write-Host "El Agent esta instalado y corriendo, pero todavia no se"
    Write-Host "ha confirmado el alta."
    Write-Host ""
    Write-Host "Sigue reintentando solo; si en unos minutos no aparece en"
    Write-Host "el panel, el motivo estara aqui:"
    Write-Host "  $LogFile"
}

Write-Host ""
