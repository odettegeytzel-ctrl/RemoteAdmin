# ==============================================================
# RemoteAdmin - Desinstala el Agent
# ==============================================================
#
# Por defecto quita el PROGRAMA y deja los DATOS: identidad,
# grabaciones y registros. Esa es la opcion segura, porque las
# grabaciones pueden ser lo unico que quede de algo que paso en ese
# equipo, y porque conservar la identidad permite volver a instalar
# sin que el equipo aparezca duplicado en el panel.
#
# Uso, en PowerShell como administrador:
#
#   .\uninstall-agent.ps1              quita el programa, conserva datos
#   .\uninstall-agent.ps1 -PurgeData   borra ademas TODOS los datos
#
# -PurgeData es irreversible: borra las grabaciones locales que no se
# hayan archivado en el servidor y la identidad del equipo. Si se
# vuelve a instalar despues, entrara como un equipo NUEVO, con otro
# identificador, y hara falta una credencial de instalacion.

[CmdletBinding()]
param(
    [string]$TaskName = "RemoteAdminAgent",
    [string]$HelperTaskName = "RemoteAdminAgentHelper",
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"

$InstallDir = Join-Path $env:ProgramData "RemoteAdmin"

Write-Host ""
Write-Host "RemoteAdmin - Desinstalacion"
Write-Host "----------------------------"

# --------------------------------------------------------------
# 1. Las dos tareas
# --------------------------------------------------------------

foreach ($tarea in @($TaskName, $HelperTaskName)) {

    if (Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue) {

        Stop-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $tarea -Confirm:$false

        Write-Host "Tarea '$tarea' eliminada."

    } else {
        Write-Host "La tarea '$tarea' no existe."
    }
}

# Se le da un momento a los procesos para que terminen antes de
# borrar sus archivos.
Start-Sleep -Seconds 2

# --------------------------------------------------------------
# 2. El programa
# --------------------------------------------------------------
#
# Solo el codigo y la configuracion. config\, recordings\ y logs\ se
# quedan salvo que se pida lo contrario.

if (Test-Path $InstallDir) {

    foreach ($resto in @("agent", "installer")) {

        $ruta = Join-Path $InstallDir $resto

        if (Test-Path $ruta) {
            Remove-Item -Path $ruta -Recurse -Force
            Write-Host "Eliminado: $resto"
        }
    }

    foreach ($archivo in @(".env", "requirements-base.txt",
                           "requirements-agent-windows.txt")) {

        $ruta = Join-Path $InstallDir $archivo

        if (Test-Path $ruta) {
            Remove-Item -Path $ruta -Force
            Write-Host "Eliminado: $archivo"
        }
    }
}

# --------------------------------------------------------------
# 3. Los datos, solo si se piden
# --------------------------------------------------------------

if ($PurgeData) {

    if (Test-Path $InstallDir) {

        Remove-Item -Path $InstallDir -Recurse -Force

        Write-Host ""
        Write-Host "Datos eliminados: identidad, grabaciones y registros."
        Write-Host "Si se reinstala, el equipo entrara como uno nuevo."
    }

} else {

    Write-Host ""
    Write-Host "Se conservan los datos del equipo en:"
    Write-Host "  $InstallDir"
    Write-Host ""
    Write-Host "Incluyen la identidad, las grabaciones locales y los"
    Write-Host "registros. Si se reinstala, el equipo conserva su"
    Write-Host "identificador y no hara falta credencial."
    Write-Host ""
    Write-Host "Para borrarlo todo:  .\uninstall-agent.ps1 -PurgeData"
}

Write-Host ""
Write-Host "Desinstalacion terminada."
Write-Host ""
