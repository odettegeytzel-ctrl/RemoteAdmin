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

## Por que una tarea programada y no un servicio

El Agent captura la pantalla e inyecta mouse y teclado. Un servicio clasico de
Windows corre en la Sesion 0, aislada del escritorio del usuario: ahi la
captura sale en negro y el mouse y el teclado no llegan a la sesion real. Una
tarea programada en la sesion interactiva cumple lo mismo —arranque
automatico, sin ventana, reinicio ante fallos— sin ese problema.

La consecuencia a tener presente: tras reiniciar Windows, el Agent arranca
cuando alguien **inicia sesion**, no antes.

## Reinstalar o actualizar

Vuelve a ejecutar el script. Conserva `config\identity.json`, las grabaciones
y los logs, asi que el equipo mantiene su identidad y no aparece duplicado. Si
ya esta dado de alta, no vuelve a pedir credencial.

## Desinstalar

```powershell
.\installer\uninstall-agent.ps1
```

Quita la tarea programada. No borra las grabaciones ni la identidad: si
quieres eliminarlas, borra a mano `C:\ProgramData\RemoteAdmin`.

## Comandos utiles

```powershell
Get-ScheduledTask -TaskName RemoteAdminAgent | Get-ScheduledTaskInfo   # estado
Stop-ScheduledTask  -TaskName RemoteAdminAgent                          # detener
Start-ScheduledTask -TaskName RemoteAdminAgent                          # iniciar
```

Para ver por que algo no funciona, arranca el Agent a mano y deja la ventana
abierta:

```powershell
python C:\ProgramData\RemoteAdmin\agent\agent.py
```
