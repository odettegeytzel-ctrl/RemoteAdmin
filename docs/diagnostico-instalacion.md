# Diagnóstico de una instalación fallida

Qué mirar cuando `VEYRA-Instalador.exe` termina con error, y las
pruebas que hay que hacer en un Windows de verdad para dar por buenos
los cambios de diagnóstico.

**Nada de esto borra ni modifica nada.** Todos los comandos de la
primera parte son de solo lectura.

---

## El caso que motivó esto

En una instalación en campo, la ventana mostró:

> La instalación terminó con errores (código 1)

y `C:\ProgramData\RemoteAdmin` **no existía**. El script había
abortado en sus comprobaciones previas, antes de crear nada, y no
quedó ni un archivo diciendo en cuál.

Eran dos agujeros:

1. `install-agent.ps1` no escribía nada hasta bien entrada la
   instalación, y la salida de un proceso elevado **no vuelve** a la
   ventana que lo lanzó: `ShellExecute` con `runas` no da tuberías.
   Lo único que cruza es el código de salida.

2. El mensaje remitía a `agent-service.log`, que lo escribe el Agent
   **ya instalado**. Si la instalación aborta antes, ese archivo no
   existe.

Desde estos cambios, el script abre una transcripción en
`C:\ProgramData\VEYRA-instalacion.log` **antes de la primera
comprobación**, y la ventana remite ahí.

> La versión publicada como 1.2.0 **no** lleva esto. En un equipo que
> falló con la 1.2.0, el archivo no va a estar: hay que reinstalar con
> un ejecutable construido desde este código.

---

## Parte 1. Qué mirar en el equipo que falló

### 1.1 La transcripción, si el instalador ya la escribe

```powershell
Get-Content C:\ProgramData\VEYRA-instalacion.log -Tail 60
```

El motivo está al final. Además, la cabecera que escribe PowerShell
dice **con qué usuario corrió realmente**:

```
Nombre de usuario: EQUIPO\quien-sea
Usuario RunAs:     EQUIPO\quien-sea
```

Eso contesta de una vez la pregunta de si la elevación cambió de
usuario, que de otro modo cuesta un rato largo de averiguar.

### 1.2 Si no existe: ¿llegó a crearse algo?

```powershell
Test-Path C:\ProgramData\RemoteAdmin
Get-ScheduledTask -TaskName RemoteAdminAgent* -ErrorAction SilentlyContinue |
    Select-Object TaskName, State
```

- **No existe la carpeta y no hay tareas** → abortó en las
  comprobaciones previas, antes de tocar nada. Sigue en 1.3.
- **Existe la carpeta** → llegó más lejos; mira
  `C:\ProgramData\RemoteAdmin\logs\`.

### 1.3 Las carpetas `_MEI*`

PyInstaller descomprime el `.exe` en una carpeta temporal llamada
`_MEI` y un número, y la borra al salir. Las que quedan son de
ejecuciones que **no terminaron limpiamente**.

**No las borres todavía.** Una puede contener la prueba de qué se
perdió, y además alguna puede estar en uso por un proceso vivo.

Primero, cuántas y de cuándo:

```powershell
Get-ChildItem $env:TEMP -Directory -Filter "_MEI*" |
    Sort-Object LastWriteTime |
    Select-Object Name, LastWriteTime,
        @{N="entradas"; E={ @(Get-ChildItem $_.FullName -Force).Count }}
```

Luego, cuáles son de VEYRA y cuáles están completas:

```powershell
Get-ChildItem $env:TEMP -Directory -Filter "_MEI*" | ForEach-Object {
    [pscustomobject]@{
        Nombre   = $_.Name
        Fecha    = $_.LastWriteTime
        Total    = @(Get-ChildItem $_.FullName -Recurse -Force -ErrorAction SilentlyContinue).Count
        Agent    = Test-Path (Join-Path $_.FullName "agent\agent.py")
        Runtime  = Test-Path (Join-Path $_.FullName "runtime\python.exe")
        Script   = Test-Path (Join-Path $_.FullName "installer\install-agent.ps1")
    }
} | Format-Table -AutoSize
```

Cómo leer la tabla:

| Lo que se ve | Qué significa |
|---|---|
| `Total` ~3169, las tres en `True` | Extracción **completa** de VEYRA. El fallo fue más adelante |
| `Agent`/`Runtime` en `False` y `Total` bajo | Extracción **vaciada**. Es el patrón de un antivirus |
| Sin `agent\` y pocas entradas | De **otro programa**, no de VEYRA |

Una extracción completa de este instalador tiene unas **3169
entradas**. Muy por debajo de eso, con `agent` o `runtime` en `False`,
es el caso que los cambios nuevos ya detectan y nombran antes de pedir
permisos de administrador.

### 1.4 ¿Se lo llevó el antivirus?

```powershell
Get-MpThreatDetection |
    Where-Object { $_.Resources -match "VEYRA|_MEI" } |
    Select-Object InitialDetectionTime, ThreatID, Resources
```

Y a mano: **Seguridad de Windows → Protección antivirus y contra
amenazas → Historial de protección**.

Es el sospechoso principal con un ejecutable de PyInstaller sin firmar.
Ver [firma-de-codigo.md](firma-de-codigo.md).

### 1.5 Conservar la evidencia antes de limpiar

Si hay que liberar espacio, primero una copia de lo que diga algo:

```powershell
$destino = "$env:USERPROFILE\Desktop\veyra-evidencia"
New-Item -ItemType Directory -Force -Path $destino | Out-Null

Get-ChildItem $env:TEMP -Directory -Filter "_MEI*" | ForEach-Object {
    Get-ChildItem $_.FullName -Force -ErrorAction SilentlyContinue |
        Select-Object @{N="Carpeta";E={$_.Directory.Name}}, Name, Length
} | Export-Csv "$destino\contenido-MEI.csv" -NoTypeInformation -Encoding UTF8

Get-MpThreatDetection -ErrorAction SilentlyContinue |
    Export-Csv "$destino\defender.csv" -NoTypeInformation -Encoding UTF8
```

**La limpieza va después, y es decisión tuya.** Una carpeta en uso por
un proceso vivo no se puede borrar y no hay que forzarla.

---

## Parte 2. Pruebas en un Windows de verdad

Lo que las pruebas automáticas **no** pueden demostrar. Ninguna de
estas se ha ejecutado todavía.

Hace falta un equipo de prueba —no uno de cliente— y el
`VEYRA-Instalador.exe` construido desde este código, no el publicado.

### Antes de empezar

```powershell
# Punto de partida limpio, en el EQUIPO DE PRUEBA
Test-Path C:\ProgramData\VEYRA-instalacion.log   # debe dar False
Test-Path C:\ProgramData\RemoteAdmin             # debe dar False
```

Si alguno da `True`, el equipo ya tiene una instalación y las pruebas
1 y 2 no medirán lo que se cree.

### 1. El registro nace antes de la primera comprobación

Es la corrección principal: que quede rastro **aunque se aborte antes
de crear nada**.

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Abrir el `.exe`, poner un servidor válido y la credencial | — |
| 2 | Pulsar **Instalar** y **rechazar** el aviso de Windows | La ventana dice que no se concedieron los permisos |
| 3 | `Test-Path C:\ProgramData\VEYRA-instalacion.log` | **`False`**: sin elevación el script no llegó a correr |
| 4 | Repetir y **aceptar** el aviso | La instalación arranca |
| 5 | `Get-Content C:\ProgramData\VEYRA-instalacion.log -Tail 40` | Existe, y empieza por la cabecera de PowerShell |

### 2. Un fallo antes del alta queda registrado

Es el escenario de campo. Para provocarlo sin romper nada, basta con
dar un servidor que resuelva pero no sea el panel.

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Poner `https://example.com` como servidor | — |
| 2 | **Comprobar conexión** | Falla antes de instalar: *«No se instaló nada»* |
| 3 | `Test-Path C:\ProgramData\RemoteAdmin` | `False` |

Y para un fallo **dentro** del script, ya elevado, se ejecuta a mano
sin permisos de administrador:

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 4 | En PowerShell **normal**, `.\installer\install-agent.ps1` | Aborta: *«Ejecuta PowerShell como administrador»* |
| 5 | Mirar el final de la transcripción | **Contiene ese mismo texto** |
| 6 | Mirar la cabecera | Dice el usuario con el que corrió |

> El paso 5 es el que de verdad importa: el `throw` ocurre **antes**
> de `Stop-Transcript`. Que el texto llegue igualmente al archivo está
> comprobado en este equipo de desarrollo, pero no en el de destino.

### 3. Un reintento conserva el intento anterior

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Repetir la prueba 2 (pasos 4-6) una segunda vez | Vuelve a abortar |
| 2 | `(Select-String C:\ProgramData\VEYRA-instalacion.log -Pattern "Inicio de la transcripción").Count` | **2 o más**. Si da 1, se está reemplazando |

### 4. Falta parte de la carga útil

Prueba el camino que evita pedir permisos de administrador para nada.
Se hace sin empaquetar, que es como se puede provocar a voluntad:

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Copiar el paquete descomprimido a una carpeta aparte | — |
| 2 | Renombrar su `agent\agent.py` | — |
| 3 | `python installer\veyra_installer.py` desde ahí, e **Instalar** | Dice que **falta `agent\agent.py`** y que no se cambió nada |
| 4 | Observar la pantalla | **No aparece ningún aviso de Windows**. Si apareciera, se está elevando antes de comprobar |

El paso 4 es el que distingue la corrección de un mensaje más bonito.

### 5. El campo Servidor viene relleno

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Abrir el `.exe` **suelto**, sin ZIP alrededor | El campo **Servidor** ya trae la dirección |
| 2 | Comprobar que no dice `localhost` ni `127.0.0.1` | — |
| 3 | Dejarlo junto a un paquete con `.env` y volver a abrirlo | Manda el `.env`, no el valor empotrado |
| 4 | **Comprobar conexión** | Responde correctamente |

### 6. Instalación completa y alta

La que cierra todo. En un equipo de prueba, no de cliente.

| # | Qué mirar | Dónde |
|---|---|---|
| 1 | La instalación termina sin error | La ventana |
| 2 | El equipo aparece en el panel | Dispositivos |
| 3 | `device_id` escrito | `C:\ProgramData\RemoteAdmin\config\identity.json` |
| 4 | Las dos tareas registradas | `Get-ScheduledTask RemoteAdminAgent*` |
| 5 | La transcripción termina en éxito | `VEYRA-instalacion.log` |
| 6 | No quedan `_MEI*` nuevas | `Get-ChildItem $env:TEMP -Filter "_MEI*"` |

### 7. Reinstalar no pierde la identidad

| # | Qué mirar | Qué debe pasar |
|---|---|---|
| 1 | Anotar el `device_id` | — |
| 2 | Volver a ejecutar el instalador | Dice *«ya está dado de alta»* y no pide credencial |
| 3 | El `device_id` | **No cambió** |
| 4 | El inventario del panel | **No aparece un equipo duplicado** |
| 5 | `recordings\` y `config\` | Siguen ahí |

---

## Lo que sigue sin estar comprobado

- Que el aviso de SmartScreen desaparece. Hace falta firmar; ver
  [firma-de-codigo.md](firma-de-codigo.md).
- Qué provocó exactamente el fallo de campo. Sin la transcripción
  —que esa versión no escribía— sigue sin saberse.
