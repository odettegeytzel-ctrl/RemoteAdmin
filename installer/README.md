# Instalar el Agent de RemoteAdmin en Windows

Este paquete se descarga desde el panel, en **Instalar equipo**, y ya viene
configurado con la direccion del servidor de tu organizacion.

## Pasos

1. Descomprime el ZIP en el equipo que quieres administrar.
2. Abre **PowerShell como administrador** en esa carpeta.
3. Ejecuta:

   ```powershell
   .\installer\install-agent.ps1
   ```

4. Pega la **credencial de instalacion** cuando te la pida. La generas en el
   panel, en la misma pantalla desde la que descargaste este paquete.
5. Espera a que el equipo aparezca en **Dispositivos**. Tarda menos de un
   minuto.

Requiere Python instalado. Si no lo esta, el script te lo dira: descargalo de
python.org marcando *Add python.exe to PATH*.

## Que hace la instalacion

- Copia el Agent a `C:\ProgramData\RemoteAdmin`.
- Escribe la configuracion en `C:\ProgramData\RemoteAdmin\.env`, legible solo
  por administradores.
- Instala las dependencias de Python.
- Registra la tarea programada `RemoteAdminAgent`, que arranca el Agent al
  iniciar sesion y lo reinicia si se cae.

## Sobre las dos credenciales

No son lo mismo, y la diferencia importa:

- La **credencial de instalacion** (`rae_...`) es de la organizacion y solo
  sirve para dar de alta el equipo. Una vez que aparezca en el panel, puedes
  revocarla: el equipo seguira funcionando.
- El **token individual** lo asigna el servidor en el alta y es lo que el
  Agent usa a partir de entonces. Se guarda en
  `C:\ProgramData\RemoteAdmin\config\identity.json` y no se muestra en ningun
  sitio.

Por eso la credencial de instalacion **no viene dentro del ZIP**: un archivo
descargado se reenvia y se olvida en la carpeta de Descargas. Se teclea en el
momento de instalar y se revoca despues.

## Dos piezas, y por que

Windows aisla los servicios en la **Sesion 0**, donde no hay escritorio: la
captura de pantalla sale en negro y el raton y el teclado no llegan a ninguna
parte. Pero el latido, el inventario y el apagado no necesitan escritorio y
deberian funcionar desde que arranca el equipo.

Por eso la instalacion crea **dos tareas**:

| Tarea | Cuando arranca | Como | Que hace |
|---|---|---|---|
| `RemoteAdminAgent` | al **arrancar Windows** | SYSTEM | habla con el servidor |
| `RemoteAdminAgentHelper` | al **iniciar sesion** | el usuario | toca el escritorio |

Lo que funciona **sin que nadie inicie sesion**: aparecer en el panel, latido,
inventario, procesos, servicios, alertas, apagar, reiniciar, bloquear, cerrar
sesion, archivar grabaciones ya hechas y cambiar ajustes.

Lo que **necesita una sesion abierta**: ver la pantalla en vivo, grabar y el
control remoto de raton y teclado. Si se pide una de estas y no hay nadie con
la sesion iniciada, el panel lo dice en vez de quedarse esperando.

### Como se hablan

Por un canal local de Windows (*named pipe*), que no es alcanzable desde la
red. El ayudante **no conoce el token del equipo** y no habla con el servidor:
solo recibe ordenes de escritorio y devuelve lo que produce. Todo lo que
importa —la identidad, los comandos administrativos— se queda en el servicio.

Si el ayudante se cae o el usuario cierra sesion, el servicio **sigue**. Cuando
alguien vuelve a entrar, se conecta un ayudante nuevo. El equipo no cambia de
identidad ni aparece duplicado.

## Reinstalar o actualizar

Vuelve a ejecutar el script. Conserva `config\identity.json`, las grabaciones
y los logs, asi que el equipo mantiene su identidad y no aparece duplicado. Si
ya esta dado de alta, no vuelve a pedir credencial.

## Desinstalar

```powershell
.\installer\uninstall-agent.ps1              # quita el programa
.\installer\uninstall-agent.ps1 -PurgeData   # borra ademas los datos
```

Sin `-PurgeData` se quitan las dos tareas y el programa, pero se conservan la
identidad, las grabaciones locales y los registros: asi una reinstalacion
mantiene el mismo equipo en el panel.

`-PurgeData` es irreversible y borra tambien las grabaciones que no se hayan
archivado en el servidor. Despues de usarlo, reinstalar da de alta un equipo
**nuevo** y hace falta otra credencial.

## Comandos utiles

```powershell
# Estado de las dos
Get-ScheduledTask -TaskName RemoteAdminAgent* | Get-ScheduledTaskInfo

Stop-ScheduledTask  -TaskName RemoteAdminAgent          # detener el servicio
Start-ScheduledTask -TaskName RemoteAdminAgent          # iniciarlo
Start-ScheduledTask -TaskName RemoteAdminAgentHelper    # el ayudante
```

Cada pieza escribe su propio registro, en
`C:\ProgramData\RemoteAdmin\logs\`: `agent-service.log` y
`agent-helper.log`.

Para ver por que algo no funciona, arranca el Agent a mano y deja la ventana
abierta:

```powershell
python C:\ProgramData\RemoteAdmin\agent\agent.py
```
