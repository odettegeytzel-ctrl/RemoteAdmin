# RemoteAdmin Agent como servicio de Windows

El Agent se ejecuta como **Tarea Programada** que arranca al iniciar sesión, corre
oculta en la sesión del usuario y se reinicia sola si falla.

## ¿Por qué una Tarea Programada y no un servicio clásico?

El Agent captura la pantalla e inyecta mouse/teclado. Un servicio de Windows
tradicional (pywin32 o NSSM) se ejecuta en **Session 0**, aislado del escritorio
del usuario: ahí la captura de pantalla sale en negro y el mouse/teclado no llegan
a la sesión real. Una Tarea Programada en la sesión interactiva evita ese problema
y cumple lo mismo: inicio automático, ejecución en segundo plano sin consola y
reinicio ante fallos.

## Requisitos

- Python instalado (o el entorno `.venv` del proyecto).
- Un archivo `.env` en la raíz del proyecto con al menos:

  ```
  AGENT_TOKEN=<el mismo token del servidor>
  REMOTEADMIN_SERVER=http://IP-DEL-SERVIDOR:8000   # opcional; por defecto 127.0.0.1:8000
  ```

## Instalar

Abre PowerShell **como administrador** en la carpeta del proyecto:

```powershell
cd C:\Users\odett\RemoteAdmin
.\agent\service\install-agent.ps1
# o apuntando a un servidor concreto:
.\agent\service\install-agent.ps1 -Server "http://192.168.1.50:8000"
```

## Iniciar / detener / reiniciar

```powershell
Start-ScheduledTask   -TaskName RemoteAdminAgent   # iniciar
Stop-ScheduledTask    -TaskName RemoteAdminAgent   # detener
Stop-ScheduledTask -TaskName RemoteAdminAgent; Start-ScheduledTask -TaskName RemoteAdminAgent   # reiniciar
```

Estado:

```powershell
Get-ScheduledTask -TaskName RemoteAdminAgent | Get-ScheduledTaskInfo
```

## Desinstalar

```powershell
.\agent\service\uninstall-agent.ps1
```

## Configurar una PC nueva

1. Copiar la carpeta del proyecto (o, en el futuro, el `agent.exe` empaquetado).
2. Crear el `.env` con `AGENT_TOKEN` (el mismo del servidor) y `REMOTEADMIN_SERVER`.
3. Ejecutar `install-agent.ps1` como administrador.
4. `Start-ScheduledTask -TaskName RemoteAdminAgent` (o cerrar/abrir sesión).

## Futuro: instalador sin Python

Empaquetar `agent.py` con PyInstaller (`agent.exe`) y hacer que
`install-agent.ps1` apunte al `.exe` en vez de `pythonw.exe`. Así una persona de
TI instala en una PC sin Python.
