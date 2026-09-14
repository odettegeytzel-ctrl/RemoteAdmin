# ==============================================================
# RemoteAdmin - Instala el Agent como Tarea Programada de Windows
# ==============================================================
#
# Registra una tarea que:
#   - arranca automáticamente al iniciar sesión el usuario,
#   - corre en la sesión interactiva (necesario para pantalla/mouse/teclado),
#   - se ejecuta oculta, sin ventana de consola (pythonw.exe),
#   - se reinicia sola si el Agent se cae.
#
# Uso (PowerShell como administrador):
#   .\install-agent.ps1
#   .\install-agent.ps1 -Server "http://192.168.1.50:8000"
#
# Debe existir un archivo .env en la raíz del proyecto con AGENT_TOKEN.

param(
    [string]$TaskName = "RemoteAdminAgent",
    [string]$Server = ""
)

$ErrorActionPreference = "Stop"

# Raíz del proyecto = carpeta superior a este script (agent\service\..\..)
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$AgentScript = Join-Path $ProjectDir "agent\agent.py"
$EnvFile = Join-Path $ProjectDir ".env"

Write-Host "Proyecto: $ProjectDir"

if (-not (Test-Path $AgentScript)) {
    throw "No se encontró agent\agent.py en $ProjectDir"
}

if (-not (Test-Path $EnvFile)) {
    Write-Warning "No existe .env en $ProjectDir. Crea uno con AGENT_TOKEN antes de arrancar el servicio."
}

# pythonw.exe (sin ventana). Prefiere el del entorno virtual .venv si existe.
$PythonwVenv = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"

if (Test-Path $PythonwVenv) {
    $Pythonw = $PythonwVenv
} else {
    $Pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
}

if (-not $Pythonw) {
    throw "No se encontró pythonw.exe. Instala Python o crea el entorno .venv."
}

Write-Host "Python: $Pythonw"

# Acción: pythonw agent\agent.py, arrancando en la raíz del proyecto
$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "`"$AgentScript`"" `
    -WorkingDirectory $ProjectDir

# Disparador: al iniciar sesión el usuario actual
$Trigger = New-ScheduledTaskTrigger -AtLogOn

# Se ejecuta como el usuario actual, en su sesión interactiva, con privilegios altos
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Highest

# Reinicio automático ante fallos; sin límite de tiempo de ejecución
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# Si ya existía, se reemplaza
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "La tarea '$TaskName' ya existe. Se reemplazará."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Si se indicó un servidor, se guarda en el .env como REMOTEADMIN_SERVER
if ($Server -ne "") {
    Write-Host "Configurando servidor: $Server"
    $line = "REMOTEADMIN_SERVER=$Server"
    if ((Test-Path $EnvFile) -and (Select-String -Path $EnvFile -Pattern "^REMOTEADMIN_SERVER=" -Quiet)) {
        (Get-Content $EnvFile) -replace "^REMOTEADMIN_SERVER=.*", $line | Set-Content -Encoding UTF8 $EnvFile
    } else {
        Add-Content -Encoding UTF8 -Path $EnvFile -Value $line
    }
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "RemoteAdmin Agent (control remoto)" | Out-Null

Write-Host ""
Write-Host "Servicio '$TaskName' instalado."
Write-Host "Iniciar ahora:   Start-ScheduledTask -TaskName $TaskName"
Write-Host "Detener:         Stop-ScheduledTask  -TaskName $TaskName"
Write-Host "Estado:          Get-ScheduledTask   -TaskName $TaskName | Get-ScheduledTaskInfo"
