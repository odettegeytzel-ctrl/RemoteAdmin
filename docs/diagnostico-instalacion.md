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

## Orden recomendado

Hay dos equipos en juego y **no se prueba lo mismo en cada uno**.

| | Equipo | Qué se hace ahí |
|---|---|---|
| **A** | El de Oaxaca, que falló | Recoger la evidencia y **dejarlo instalado** |
| **B** | Uno de prueba | Los fallos provocados a propósito |

Se empieza por **A**, porque ahí hay alguien esperando que el equipo
quede funcionando y porque la evidencia del fallo se pierde en cuanto
se reinstala.

**En el equipo de Oaxaca no se provocan fallos.** Las pruebas de la
parte 3 —rechazar el aviso de Windows, vaciar la carga útil— se hacen
en un equipo de prueba, no en uno de cliente.

### Antes de ir: dos cosas que decidir

**1. El ejecutable hay que llevarlo a mano.** El que se descarga del
panel es el publicado (1.2.0) y **no** lleva estos cambios. El de
prueba está en `installer\dist\VEYRA-Instalador.exe` y hay que
pasarlo por USB, carpeta compartida o como se prefiera.

Para comprobar que llegó entero:

```powershell
Get-FileHash .\VEYRA-Instalador.exe -Algorithm SHA256
```

Debe dar la huella que anota
`installer\dist\VEYRA-Instalador.sha256.txt`. Si no coincide, el
archivo se dañó al copiarlo y no hay que ejecutarlo.

**2. Ese equipo quedaría con el Agent 1.3.0, que todavía no está
publicado.** No se va a actualizar solo hacia atrás —el actualizador
se niega a instalar una versión anterior a la que ya tiene— así que
se quedará en 1.3.0 hasta que publiques. Es una decisión tuya:
funciona, pero es un equipo de cliente corriendo algo que aún no has
publicado.

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

## Parte 2. El equipo de Oaxaca, paso a paso

El orden importa: **la evidencia primero**, porque reinstalar la
borra.

### Etapa 1 · Recoger antes de tocar

Todo de solo lectura. Guarda la salida de cada uno.

```powershell
# 1. Queda algo de la instalación anterior?
Test-Path C:\ProgramData\RemoteAdmin
Test-Path C:\ProgramData\VEYRA-instalacion.log

# 2. Hay tareas registradas?
Get-ScheduledTask -TaskName RemoteAdminAgent* -ErrorAction SilentlyContinue |
    Select-Object TaskName, State
```

Lo esperable, por lo que ya se vio: **las dos primeras en `False`** y
ninguna tarea. El archivo de registro no va a estar porque la 1.2.0
no lo escribía.

Después, el inventario de las `_MEI*` (sección 1.3) y el historial de
Defender (sección 1.4), y la copia de evidencia de la sección 1.5.

> **No borres ninguna `_MEI*` todavía.** Ni siquiera después de
> instalar: son la única pista de qué pasó aquel día.

### Etapa 2 · Instalar con el ejecutable de prueba

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Comprobar la huella del `.exe` copiado | Coincide con la constancia |
| 2 | Doble clic | Se abre la ventana. **El campo Servidor ya trae la dirección** |
| 3 | Mirar que no diga `localhost` ni `127.0.0.1` | — |
| 4 | **Comprobar conexión** | *«El servidor responde correctamente»* |
| 5 | Pegar la credencial `rae_…` del panel | — |
| 6 | **Instalar** y aceptar el aviso de Windows | Arranca |

**Pase lo que pase en el paso 6**, lo siguiente ya existe:

```powershell
Get-Content C:\ProgramData\VEYRA-instalacion.log -Tail 60
```

Ese archivo es el objetivo de todo este trabajo. Si la instalación
falla otra vez, **el motivo está ahí** y ya no hay que adivinarlo.

### Etapa 3 · Comprobar que quedó bien

| # | Qué mirar | Dónde |
|---|---|---|
| 1 | Aparece en el panel | Dispositivos |
| 2 | Tiene `device_id` | `C:\ProgramData\RemoteAdmin\config\identity.json` |
| 3 | Las dos tareas, en `Ready` o `Running` | `Get-ScheduledTask RemoteAdminAgent*` |
| 4 | La transcripción termina en alta correcta | `VEYRA-instalacion.log` |
| 5 | Informa versión **1.3.0** | Ficha del equipo en el panel |

### Etapa 4 · Reinstalar sin duplicar

Solo si la etapa 3 salió bien. Es la prueba más cara de equivocar.

| # | Qué hacer | Qué debe pasar |
|---|---|---|
| 1 | Anotar el `device_id` del paso 2 anterior | — |
| 2 | Volver a ejecutar el `.exe` | Dice **«ya está dado de alta»** y **no pide credencial**. El botón dice *Actualizar* |
| 3 | Pulsarlo y esperar | Termina sin error |
| 4 | Releer `identity.json` | **El `device_id` no cambió** |
| 5 | Mirar el inventario | **No hay un equipo duplicado** |
| 6 | Contar los inicios de transcripción | **2 o más**: el intento anterior se conservó |

```powershell
(Select-String C:\ProgramData\VEYRA-instalacion.log `
    -Pattern "Inicio de la transcripción").Count
```

El paso 4 es el que miraría primero. Si el `device_id` cambiara, la
reinstalación habría tocado `identity.json`, y eso no debería poder
pasar.

---

## Parte 3. Pruebas en un equipo de prueba

Los fallos que hay que **provocar**, y que por eso no se hacen en un
equipo de cliente. Ninguna de estas se ha ejecutado todavía.

Hace falta el `VEYRA-Instalador.exe` construido desde este código,
no el publicado.

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

### 6. Instalación completa, alta y reinstalación

Son las etapas 2 a 4 de la parte 2, que ya están descritas ahí. En un
equipo de prueba se pueden hacer igual, con una comprobación de más
que en Oaxaca no conviene provocar:

| # | Qué mirar | Dónde |
|---|---|---|
| 1 | No quedan `_MEI*` nuevas tras una instalación correcta | `Get-ChildItem $env:TEMP -Filter "_MEI*"` |
| 2 | `recordings\` y `config\` sobreviven a reinstalar | `C:\ProgramData\RemoteAdmin\` |

Si quedara una `_MEI*` después de una instalación que terminó bien,
el `.exe` no está limpiando al salir y conviene saberlo antes de que
se acumulen 62 como en el caso de campo.

---

## Lo que sigue sin estar comprobado

- Que el aviso de SmartScreen desaparece. Hace falta firmar; ver
  [firma-de-codigo.md](firma-de-codigo.md).
- Qué provocó exactamente el fallo de campo. Sin la transcripción
  —que esa versión no escribía— sigue sin saberse.
