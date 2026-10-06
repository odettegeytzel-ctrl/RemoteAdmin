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
    [string]$TaskName = "RemoteAdminAgent"
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
$IdentityFile = Join-Path $InstallDir "config\identity.json"

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
# 6. Tarea programada
# --------------------------------------------------------------
#
# Tarea programada en la sesion del usuario, y no un servicio clasico,
# porque el Agent captura la pantalla e inyecta mouse y teclado. Un
# servicio corre en la Sesion 0, aislado del escritorio: ahi la captura
# sale en negro y el mouse no llega a ninguna parte.

$Pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source

if (-not $Pythonw) {
    $Pythonw = $Python
}

$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "`"$AgentScript`"" `
    -WorkingDirectory $InstallDir

$Trigger = New-ScheduledTaskTrigger -AtLogOn

$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "RemoteAdmin Agent" | Out-Null

Write-Host "Tarea '$TaskName' registrada."

# --------------------------------------------------------------
# 7. Arranque
# --------------------------------------------------------------

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Agent instalado e iniciado."
Write-Host ""
Write-Host "El equipo deberia aparecer en el panel, en Dispositivos,"
Write-Host "en menos de un minuto."
Write-Host ""
Write-Host "Si no aparece, ejecuta esto para ver el motivo:"
Write-Host "  & '$Python' '$AgentScript'"
Write-Host ""
