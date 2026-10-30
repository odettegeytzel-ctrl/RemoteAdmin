# ==============================================================
# VEYRA - Comprobar si un equipo es apto para instalar
# ==============================================================
#
# SOLO LEE. No escribe, no borra, no instala y no necesita
# administrador. Se puede ejecutar en un equipo en uso sin
# consecuencias.
#
# Responde a una pregunta concreta: este equipo, ¿esta limpio, ya es
# de este servidor, o arrastra una identidad de OTRO servidor?
#
# El tercer caso es el que importa. install-agent.ps1 no pide
# credencial cuando ya existe config\identity.json -da por hecho que
# el equipo esta de alta- pero si reescribe el .env con la direccion
# nueva. El Agent acaba presentandose al servidor nuevo con un token
# que ese servidor no conoce, y responde 401. Para siempre, porque
# nadie volvera a pedirle una credencial.
#
# Uso, en el equipo candidato:
#
#   powershell -ExecutionPolicy Bypass -File .\comprobar-equipo.ps1 `
#              -Servidor "https://remoteadmin.tail37c528.ts.net"
#
# Codigos de salida:
#   0  apto: limpio, o ya pertenece a ese mismo servidor
#   1  NO apto: identidad de otro servidor, o indeterminado
#   2  error de uso

[CmdletBinding()]
param(
    # Servidor al que se quiere conectar el equipo. La URL BASE, sin
    # /panel: el Agent le anade /api/... y /ws/agent por su cuenta.
    [Parameter(Mandatory = $true)]
    [string]$Servidor
)

$ErrorActionPreference = "Stop"

if (-not ($Servidor.StartsWith("http://") -or $Servidor.StartsWith("https://"))) {
    Write-Host "La direccion debe empezar por http:// o https://" -ForegroundColor Red
    exit 2
}

if ($Servidor -match "/panel/?$") {
    Write-Host ""
    Write-Host "Esa es la direccion del PANEL, no la base." -ForegroundColor Red
    Write-Host "Quita el /panel: el Agent anade /api/... y /ws/agent solo."
    exit 2
}

$Servidor = $Servidor.TrimEnd("/")

$InstallDir   = Join-Path $env:ProgramData "RemoteAdmin"
$IdentityFile = Join-Path $InstallDir "config\identity.json"
$EnvFile      = Join-Path $InstallDir ".env"

Write-Host ""
Write-Host "VEYRA - aptitud del equipo"
Write-Host "--------------------------"
Write-Host "Equipo   : $env:COMPUTERNAME"
Write-Host "Servidor : $Servidor"
Write-Host ""

# --------------------------------------------------------------
# 1. ¿Hay rastro de una instalacion anterior?
# --------------------------------------------------------------

$tarea = Get-ScheduledTask -TaskName RemoteAdminAgent -ErrorAction SilentlyContinue

Write-Host "Carpeta de datos    : $(if (Test-Path $InstallDir) { 'existe' } else { 'no existe' })"
Write-Host "Tarea RemoteAdminAgent: $(if ($tarea) { $tarea.State } else { 'no registrada' })"
Write-Host "identity.json       : $(if (Test-Path $IdentityFile) { 'existe' } else { 'no existe' })"
Write-Host ""

if (-not (Test-Path $IdentityFile)) {

    Write-Host "EQUIPO LIMPIO." -ForegroundColor Green
    Write-Host ""
    Write-Host "No hay identidad previa. La instalacion pedira la credencial"
    Write-Host "rae_... y el servidor asignara un device_id nuevo."

    if ($tarea -or (Test-Path $InstallDir)) {
        Write-Host ""
        Write-Host "Aviso: hay restos de una instalacion (carpeta o tarea) pero"
        Write-Host "sin identidad. Se dara de alta como equipo nuevo."
    }

    exit 0
}

# --------------------------------------------------------------
# 2. Hay identidad. ¿De quien?
# --------------------------------------------------------------
#
# identity.json guarda device_id y agent_token, y NADA MAS: no deja
# constancia del servidor que la emitio. Asi que de quien es solo se
# puede averiguar por dos vias, y ninguna sola es concluyente.

$identidad = Get-Content $IdentityFile -Raw | ConvertFrom-Json

Write-Host "Este equipo YA tiene identidad."
Write-Host "  device_id : $($identidad.device_id)"
Write-Host "  token     : (no se muestra)"
Write-Host ""

# --- Pista 1: la direccion que quedo configurada ------------------

$servidorAnterior = $null

if (Test-Path $EnvFile) {
    $linea = Select-String -Path $EnvFile -Pattern "^REMOTEADMIN_SERVER=(.+)$"
    if ($linea) {
        $servidorAnterior = $linea.Matches[0].Groups[1].Value.Trim().TrimEnd("/")
    }
}

if ($servidorAnterior) {
    Write-Host "Configurado para : $servidorAnterior"
} else {
    Write-Host "Configurado para : (no se pudo leer el .env)"
}

# --- Pista 2, la que manda: preguntarselo al servidor -------------
#
# GET /api/agent/version se autentica con el token INDIVIDUAL y no
# modifica nada. Si el servidor responde 200, conoce este token y la
# identidad le pertenece. Si responde 401, no es suya.
#
# Es la unica respuesta concluyente: el .env puede estar editado a
# mano o haberse perdido.

$reconocido = $null
$detalle = ""

try {
    $respuesta = Invoke-WebRequest `
        -Uri "$Servidor/api/agent/version" `
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
        $detalle = $_.Exception.Message
    }
}

Write-Host ""

if ($reconocido -eq $true) {

    Write-Host "APTO: la identidad es de ESTE servidor." -ForegroundColor Green
    Write-Host ""
    Write-Host "Reinstalar aqui conserva el device_id $($identidad.device_id)"
    Write-Host "y sus grabaciones. No hace falta credencial."
    exit 0
}

if ($reconocido -eq $false) {

    Write-Host "NO APTO: identidad de OTRO servidor." -ForegroundColor Red
    Write-Host ""
    Write-Host "El servidor $Servidor no reconoce el token de este equipo."
    Write-Host "Si se instala tal cual, el instalador NO pedira credencial"
    Write-Host "-ve que hay identidad- pero cambiara la direccion, y el"
    Write-Host "Agent quedara en 401 permanente."
    Write-Host ""
    Write-Host "Elige otro equipo, o aparta la identidad A PROPOSITO:"
    Write-Host ""
    Write-Host "  Rename-Item '$IdentityFile' 'identity.ANTES.json'"
    Write-Host ""
    Write-Host "Renombrar, no borrar: asi se puede volver atras. El equipo"
    Write-Host "se dara de alta como nuevo, con otro device_id, y perdera"
    Write-Host "el vinculo con sus grabaciones en el servidor anterior."
    exit 1
}

Write-Host "INDETERMINADO: no se pudo preguntar al servidor." -ForegroundColor Yellow
Write-Host ""
Write-Host "  $detalle"
Write-Host ""
Write-Host "Sin esa respuesta no se sabe de quien es la identidad."

if ($servidorAnterior -and $servidorAnterior -ne $Servidor) {
    Write-Host ""
    Write-Host "Y el .env apunta a OTRA direccion ($servidorAnterior)," -ForegroundColor Yellow
    Write-Host "asi que lo mas probable es que no sea apto."
}

Write-Host ""
Write-Host "No instales hasta resolverlo."
exit 1
