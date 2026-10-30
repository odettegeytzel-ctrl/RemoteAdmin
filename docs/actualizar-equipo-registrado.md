# Actualizar un equipo que ya está registrado

**Nada de esto se ha ejecutado todavía.** Es el procedimiento y la
auditoría que lo respalda.

Dos cosas distintas que se confunden con facilidad:

| | qué actualiza | quién lo dispara |
|---|---|---|
| **El `.exe` del panel** | reemplaza los archivos del Agent en el equipo | una persona, a mano |
| **El paquete Ed25519** | el Agent se actualiza solo | el manifiesto firmado |

Este documento trata **solo el primero**. El segundo está en
[publicar-version-agent.md](publicar-version-agent.md) y queda fuera
de este trabajo.

---

## 1. Qué hace hoy la reinstalación sobre un equipo registrado

Leído del código, no supuesto.

### El `.exe` reconoce el equipo antes de abrir la ventana

`installer/veyra_installer.py` mira
`C:\ProgramData\RemoteAdmin\config\identity.json` al arrancar
(`identidad_existente()`). Si está, la ventana muestra el
identificador y dice que no hace falta credencial, y **el campo de la
credencial ni siquiera se dibuja**. Después lanza
`install-agent.ps1` con `-Server <url>` y `-RuntimeDir`, sin
`-EnrollmentTokenFile`.

La dirección que propone sale, por este orden, del `.env` del paquete
o de `installer\servidor.txt`, que `build-installer.ps1` graba dentro
del `.exe` al construirlo.

### El guion conserva la identidad

`install-agent.ps1` calcula `$yaEnrolado = Test-Path $IdentityFile`.
Con identidad presente **no pide credencial** y no la exige: la
comprobación de que empiece por `rae_` está dentro de
`if (-not $yaEnrolado)`.

El `device_id` y el token individual **no se tocan**: el guion no
escribe ni borra `identity.json` en ningún punto. Quien lo escribe es
el Agent, y solo la primera vez (`save_identity()` en `agent.py`).

Ante el servidor, el Agent ya registrado manda su token individual y
el servidor deduce la identidad **del token**, no de lo que el Agent
diga ser. Reinstalar no cambia nada de eso.

### Qué se reemplaza y qué sobrevive

Se copia desde el paquete:

- `agent\` entero — incluidos `agent.py`, `scheduler.py`, `owners.py`
  y `version.py`
- `requirements-base.txt` y `requirements-agent-windows.txt`
- `installer\`
- `runtime\`, si el instalador trae intérprete embebido

**No se copian, y por tanto sobreviven:** `config\` (la identidad),
`recordings\` (las grabaciones), `logs\` y el `.env` anterior, que se
reescribe con la misma dirección.

### Qué hace que el servicio ejecute el código nuevo

No basta con copiar archivos. La secuencia real es:

1. `Stop-ScheduledTask` sobre `RemoteAdminAgent` y
   `RemoteAdminAgentHelper`.
2. Se espera a que los procesos salgan **de verdad**.
3. Se reemplaza `agent\`.
4. `Unregister-ScheduledTask` y `Register-ScheduledTask`: las dos
   tareas se vuelven a crear apuntando a
   `%ProgramData%\RemoteAdmin\agent\agent.py`.
5. `Start-ScheduledTask`.

Como la tarea arranca un proceso nuevo desde la ruta que acaba de
reescribirse, el servicio queda ejecutando el código actualizado. No
hay proceso viejo sobreviviente: el paso 2 lo garantiza.

### Las guardas de identidad no estorban

La sección 2b compara el `.env` **instalado** con el servidor de
destino y, si coinciden, pregunta al servidor con el token individual
(`GET /api/agent/version`). Una actualización legítima contra el mismo
servidor pasa las dos y sigue. Solo se detiene cuando la identidad es
de otro servidor o cuando no se puede averiguar.

---

## 2. Defectos encontrados y corregidos

Los tres se corrigieron en `installer/install-agent.ps1`.

### a) Los archivos huérfanos de la versión anterior

`Copy-Item -Recurse -Force` sobrescribe y añade, pero **nunca quita**.
Un módulo que existía en 1.2.0 y ya no existe en 1.3.0 se quedaba en
disco y se seguía importando. Es un fallo caro de encontrar: el
código que corre no es ni el viejo ni el nuevo.

**Corregido:** el `agent\` anterior se **mueve** a `agent.anterior\`
antes de copiar, así que la carpeta nueva nace limpia.

### b) No había vuelta atrás

Si la copia fallaba a mitad, el equipo se quedaba **sin Agent**: ni el
viejo ni el nuevo, y sin nada que reponer.

**Corregido:** `agent.anterior\` es también el respaldo. Si
`Copy-Item` falla, el guion lo repone solo y lo dice. Se conserva una
única copia; la desinstalación la retira con el resto del programa.

### c) La confirmación final no confirmaba nada

La sección 8 daba la instalación por buena si existía
`config\identity.json`. En una actualización **ese archivo ya estaba
antes de empezar**: una actualización que dejara el Agent roto
terminaba diciendo «instalado y dado de alta», y el fallo no se veía
hasta mirar el panel horas después.

**Corregido:** en una actualización se comprueba otra cosa — que la
tarea quedó en ejecución y que `logs\agent.log` **creció** desde el
arranque. Se mide el tamaño antes de `Start-ScheduledTask`. Si no se
cumple, el guion lo dice y escribe las órdenes para volver a la
versión anterior.

También se informa ahora la versión que quedó instalada, **leída de
`agent\version.py` en el disco del equipo**, no de la que el
instalador creía traer.

### d) La espera fija de dos segundos (corregido de paso)

`Stop-ScheduledTask` no es inmediato. Con `Start-Sleep -Seconds 2` y
un proceso que tardara más, `Copy-Item` fallaba sobre archivos en uso.

**Corregido:** se espera hasta 30 s a que los procesos del Agent
salgan de verdad, y si no salen **no se copia nada** y se dice que el
equipo quedó sin modificar.

### e) Lo que NO se corrigió, y por qué

**La versión que anuncia el panel sale del manifiesto firmado.**
`agent_releases.installer_info()` toma `version` de
`releases/manifest.json`, que hoy dice `1.2.0`. Si se sustituye solo
el `.exe`, el panel ofrecerá un instalador 1.3.0 **etiquetado como
1.2.0** (el tamaño y el SHA-256 sí serían los nuevos, porque se
calculan del archivo).

No se toca: corregirlo pasa por publicar el manifiesto 1.3.0 firmado,
que está excluido de este trabajo. Es una decisión, no un descuido.

El apartado 2bis explica por qué no es un cambio pequeño.

---

## 2bis. La diferencia entre 1.2.0 y 1.3.0

**Publicar el manifiesto 1.3.0 no es cambiar una etiqueta.** Es lo
que dispara la actualización automática de toda la flota, y conviene
verlo antes de decidir.

### Qué pasa si se publica el manifiesto

`agent/updater.py` consulta `GET /api/agent/version` **cada 6 horas**
(`CHECK_INTERVAL_SECONDS = 6 * 60 * 60`) desde cada equipo instalado.
Ese endpoint devuelve el manifiesto firmado. Si la versión publicada
es posterior a la instalada, el Agent **descarga el paquete y se
actualiza solo**.

Es el mismo endpoint que usa la guarda B del instalador, pero para
otra cosa.

Consecuencias, en orden:

1. Todos los equipos de la flota pasarían a 1.3.0 en las 6 horas
   siguientes, sin que nadie ejecute nada.
2. Hoy **no existe `releases/agent-1.3.0.zip`** — solo están 1.1.0 y
   1.2.0. Un manifiesto 1.3.0 sin su paquete dejaría a cada Agent
   pidiendo cada 6 horas algo que no está, con un error en el
   registro. Por eso el paquete va **antes** que el manifiesto.
3. Solo entonces el panel etiquetaría el instalador como 1.3.0.

### Qué hace falta para firmar

| | |
|---|---|
| la clave privada Ed25519 original | **no está en el proyecto, y no debe estar** |
| `tools/sign_agent_release.py` | está, y **rechaza** una clave guardada dentro del proyecto |
| `releases/agent-1.3.0.zip` | **no existe**: hay que construirlo |

La clave pública está **fijada en el código del Agent**
(`AGENT_UPDATE_PUBLIC_KEY` en `agent/release.py`). Generar un par
nuevo **no sirve**: todos los Agents instalados rechazarían la firma,
y para que aceptaran la clave nueva habría que actualizarlos antes…
con un paquete que no podrían verificar. **La clave original es
insustituible.** Si se perdió, el único camino es reinstalar a mano
cada equipo.

### ¿Se puede corregir la etiqueta sin recompilar ni firmar?

Técnicamente sí, cambiando `installer_info()` para que tome la
versión de otro sitio: un archivo suelto junto al `.exe`, o la
versión que declara el `agent/version.py` que lleva dentro.

**Pero el acoplamiento es deliberado.** El comentario del código lo
dice: la versión sale del manifiesto firmado porque *«es lo único que
no se puede falsear sin la clave privada»*. Leerla de un archivo
escrito a mano convierte un dato verificable en uno que cualquiera
con acceso al servidor puede poner a su gusto.

No lo he cambiado. Es una decisión de diseño, y revertirla a cambio
de una etiqueta no me parece buen trato.

### Las tres salidas

| | qué implica |
|---|---|
| **A. Publicar solo el `.exe`** | el panel dice 1.2.0 y el instalador instala 1.3.0. La flota no se mueve. Es lo menos invasivo |
| **B. Publicar paquete + manifiesto firmado** | la etiqueta queda bien **y toda la flota se actualiza sola**. Requiere la clave privada |
| **C. Desacoplar la versión en el código** | etiqueta correcta sin firmar, a cambio de perder la garantía de la firma |

**A** es lo coherente con probar primero en un equipo. **B** solo
después de que esa prueba salga bien.

---

## 3. Las pruebas

```powershell
.\.venv\Scripts\python tests\test_actualizacion_instalador.py   # 29/29
.\.venv\Scripts\python tests\test_guardas_instalador.py         # 32/32
.\.venv\Scripts\python tests\test_produccion.py                 # 112/112
```

`test_actualizacion_instalador.py` **no reimplementa la lógica**:
extrae el bloque real de `install-agent.ps1` entre sus marcadores y lo
ejecuta sobre carpetas de mentira. Si alguien cambia el bloque, la
prueba corre el texto nuevo; si borra los marcadores, falla en vez de
pasar en verde sobre nada.

Lo que demuestra, ejecutándolo:

| escenario | se comprueba |
|---|---|
| actualización sobre instalación existente | el `agent.py` que queda es el nuevo |
| | el módulo obsoleto ya no está |
| | `identity.json`, `recordings\` y `logs\` intactos |
| dos actualizaciones seguidas | solo se conserva un respaldo |
| | datos intactos otra vez |
| equipo limpio | se copia el Agent, sin carpeta de respaldo |
| **la copia falla** | el equipo se queda con el Agent **anterior** |
| | se dice en pantalla que se repuso |
| | datos intactos también tras el fallo |

Y, sobre el texto del guion: que el bloque no nombra `config`,
`recordings`, `logs` ni `identity.json`; que el único borrado es el
respaldo previo; que la confirmación no se apoya en `Test-Path` del
`identity.json`; y que el respaldo ocurre antes de escribir el `.env`.

`test_guardas_instalador.py` cubre lo demás ejecutándolo contra un
`http.server` local: identidad del mismo servidor, de otro servidor,
token válido, token rechazado, error de conexión y ausencia de
identidad.

**Ninguna prueba toca** la base de datos real, el equipo registrado,
`ProgramData` ni ninguna tarea programada.

### Lo que no se ha probado

- **Una actualización real sobre el equipo registrado.** Requiere
  autorización y es lo que se pide a continuación.
- Que el Agent 1.3.0 arranque bajo el intérprete embebido en un
  equipo que venía de la versión antigua.
- `test_f4_validation` sigue en 45/46 por la contaminación de la
  prueba real del 27-oct. No se ha manipulado la base para que pase.

---

## 4. Qué entrega hoy la descarga del panel

### El endpoint

`GET /api/agent-installer` (`backend/main.py`). Misma autenticación
que el paquete ZIP: no hay ruta pública. Responde con
`FileResponse(agent_releases.installer_path())`.

`GET /api/agent-installer/info` es lo que el panel consulta antes, y
devuelve nombre, **versión del manifiesto**, tamaño y SHA-256
recalculado del archivo.

### El archivo, en producción

```
/opt/remoteadmin/RemoteAdmin/releases/VEYRA-Instalador.exe
```

En `159.54.156.221`, propiedad de `remoteadmin:remoteadmin`. **No es
la carpeta `releases\` de este equipo**, que es solo la copia de
trabajo de quien publica.

### El que hay ahora frente al nuevo

| | publicado (copia local) | nuevo |
|---|---|---|
| fecha | 14-oct-2026 16:20 | 28-oct-2026 09:39 |
| tamaño | 79 933 274 B | 79 959 782 B |
| SHA-256 | `fa83df9a71b8f977…` | `4703784d17826afb…` |

El SHA del publicado está medido sobre la copia local; **el del
servidor hay que comprobarlo allí** antes de sustituir nada.

### Cómo se sustituiría

No hay que reiniciar `remoteadmin`: el archivo se lee de disco en cada
petición.

```powershell
# Desde este equipo
scp -i <tu_clave> installer\dist\VEYRA-Instalador.exe `
    ubuntu@159.54.156.221:/tmp/
```

```bash
cd /opt/remoteadmin/RemoteAdmin

# 1. Qué hay ahora, para poder compararlo después
sha256sum releases/VEYRA-Instalador.exe

# 2. Guardarlo. Con fecha: así no se pisa un respaldo anterior
sudo cp releases/VEYRA-Instalador.exe \
        releases/VEYRA-Instalador.exe.20261014

# 3. Comprobar lo que llegó ANTES de ponerlo en su sitio
sha256sum /tmp/VEYRA-Instalador.exe
# debe dar 4703784d17826afbf18ec3132164e13615fde329c8b9141fe2ce745a0aac2335

# 4. Solo si coincide
sudo mv /tmp/VEYRA-Instalador.exe releases/
sudo chown remoteadmin:remoteadmin releases/VEYRA-Instalador.exe

# 5. Y comprobarlo ya puesto
sha256sum releases/VEYRA-Instalador.exe
```

Desde el panel, `GET /api/agent-installer/info` debe devolver el mismo
SHA-256 y 79 959 782 bytes. **Dirá `1.3.0` solo si antes se publicó el
manifiesto firmado; si no, dirá `1.2.0`** (defecto `e` del apartado 2).

**Para volver atrás:**

```bash
sudo mv releases/VEYRA-Instalador.exe.20261014 \
        releases/VEYRA-Instalador.exe
sudo chown remoteadmin:remoteadmin releases/VEYRA-Instalador.exe
```

---

## 5. Actualizar el equipo registrado

Cuando se autorice, en este orden.

**Antes de nada, en el equipo registrado** (solo lee, no hace falta
administrador):

```powershell
powershell -ExecutionPolicy Bypass -File .\comprobar-equipo.ps1 `
           -Servidor "https://remoteadmin.tail37c528.ts.net"
```

Tiene que decir **`APTO: la identidad es de ESTE servidor`**. Si dice
`NO APTO` o `INDETERMINADO`, **no se instala**: ver el mensaje, que
explica el caso.

`comprobar-equipo.ps1` **no viaja dentro del `.exe`**. Hay que
copiarlo aparte.

Anotar antes, para poder comparar después:

- el `device_id` que muestra el comprobador
- cuántas grabaciones tiene el equipo en el panel
- su horario configurado

**Luego:** descargar el `.exe` del panel, ejecutarlo como
administrador, y confirmar que la ventana dice *«Este equipo ya esta
dado de alta»* con el `device_id` esperado y **sin pedir credencial**.

**Al terminar, el instalador debe decir** «Agent actualizado y en
ejecucion», el mismo `device_id`, y `Version instalada: 1.3.0`.

**Después, comprobar en el panel:**

- el equipo es el **mismo**, no uno nuevo: mismo `device_id`
- el número de grabaciones no ha cambiado
- el horario sigue puesto
- la última conexión es de hace un momento

**Si algo falla**, el guion escribe en pantalla cómo volver atrás, y
el registro de la instalación queda en
`C:\ProgramData\VEYRA-instalacion.log`.

---

## Riesgos que quedan

| riesgo | estado |
|---|---|
| El `.exe` no está firmado: SmartScreen avisará | sin resolver, [firma-de-codigo.md](firma-de-codigo.md) |
| El panel etiquetará el instalador como 1.2.0 | conocido, apartado 2e |
| La actualización no se ha probado en un equipo real | pendiente de autorización |
| `comprobar-equipo.ps1` hay que copiarlo aparte | conocido |
| Si el `.env` del equipo registrado apunta a otra dirección de la misma máquina (IP en vez de nombre), la guarda A lo detendrá | lo resuelve el comprobador antes de instalar |

---

## Lo que no se ha hecho

No se ha sustituido el instalador publicado, no se ha hecho `push`, no
se ha desplegado, no se ha reiniciado ningún servicio, no se ha
instalado nada en ningún equipo, y no se ha firmado ni publicado el
manifiesto Ed25519.
