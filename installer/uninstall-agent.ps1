# ==============================================================
# RemoteAdmin - Desinstala el servicio (Tarea Programada) del Agent
# ==============================================================
#
# Uso (PowerShell como administrador):
#   .\uninstall-agent.ps1

param(
    [string]$TaskName = "RemoteAdminAgent"
)

$ErrorActionPreference = "Stop"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {

    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

    Write-Host "Servicio '$TaskName' desinstalado."

} else {

    Write-Host "El servicio '$TaskName' no existe."
}
