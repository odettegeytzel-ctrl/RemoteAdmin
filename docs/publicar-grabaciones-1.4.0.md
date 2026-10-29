# Publicar por fases: paginación, filtros y Agent 1.4.0

Procedimiento preparado el 29-10-2026. **Nada de esto se ha
ejecutado**: no hay acceso SSH al servidor de producción.

El orden importa. El backend nuevo entiende a los Agents viejos; un
Agent nuevo contra un servidor viejo declararía una capacidad que ese
servidor no sabe leer. Por eso el servidor va primero, y el
instalador después.

---

## Lo que falta para poder empezar

| | |
|---|---|
| Acceso SSH a `159.54.156.221` | **no lo tengo.** Solo acepta clave pública y no hay ninguna para ese servidor en esta máquina |
| Acceso a un equipo Windows registrado | **no lo tengo** |
| Los cambios en `origin/main` | **no están.** El procedimiento del repositorio despliega con `git pull`, y eso exige confirmar y subir antes |

Ese tercer punto es una decisión tuya y conviene tomarla antes de
nada. Ver el apartado 0.

---

## 0. Antes de la fase 1: cómo llega el código al servidor

`deploy/README.md` despliega así:

```bash
sudo -u remoteadmin git pull --ff-only origin main
```

Eso necesita que los cambios estén confirmados **y subidos**. Hoy
están solo en el árbol de trabajo de esta máquina, sin confirmar.

Hay dos caminos y no son equivalentes:

| | Qué implica |
|---|---|
| **A. Confirmar y subir** (recomendado) | El servidor sigue el procedimiento de siempre. `git status` en el servidor queda limpio y la vuelta atrás es un `git checkout <commit>` |
| **B. Copiar los archivos con `scp`** | Deja el repositorio del servidor sucio. La **siguiente** actualización fallaría su propia comprobación previa (`git status --short` debe salir vacío) y un `git pull` pisaría los cambios |

**Recomiendo A.** El commit está preparado pero **no lo he creado**:
no tengo autorización para confirmar ni para subir.

Los archivos que irían en él:

```
backend/database.py          indice nuevo
backend/main.py              paginacion, local-video, capacidades
backend/recordings.py        consulta paginada en SQL
frontend/app.js              paginacion, filtros, reproductor
frontend/index.html          pie de tabla con "Ver mas"
agent/agent.py               transmision y capacidades
agent/commands.py            ordenes nuevas, de fondo
agent/version.py             1.4.0
installer/verificar-exe.py   verificador del paquete
tests/test_grabaciones_paginadas.py
tests/test_grabaciones_transmision_real.py
tests/test_panel_acciones.py
docs/grabaciones-paginacion-y-local.md
docs/publicar-grabaciones-1.4.0.md
```

`agent/` viaja en el commit pero **no se despliega en el servidor**:
llega a los equipos dentro del `.exe`.

---

## Fase 1 — Backend y panel, juntos

### Por qué juntos

No es una preferencia. El panel nuevo pide `limit`/`offset` y lee
`total` y `has_more`; el backend nuevo es quien los sirve.

- Panel nuevo contra backend viejo: el backend ignora `limit`,
  devuelve **las 173 filas** y el panel las pinta todas. Vuelve el
  problema que esto venía a resolver.
- Backend nuevo contra panel viejo: el panel no manda `limit`, y la
  respuesta es la de siempre. Funciona, pero sin paginación.

Ninguno de los dos rompe nada, pero solo desplegándolos juntos se
obtiene lo que se busca.

### Qué NO necesita esta fase

**Ningún equipo.** Verificado con cero Agents conectados: la
paginación y los filtros funcionan igual.

```
agentes conectados: 0      pagina 1: 10 filas    total: 173
                           pagina 2: 10 filas    solape: ninguno
                           filtro de un dia: 2 grabaciones
```

### El índice nuevo

Se crea solo al arrancar, dentro de `init_db()`:

```sql
CREATE INDEX IF NOT EXISTS idx_recordings_orden
ON recordings (started_at DESC, id DESC)
```

Comprobado sobre una **copia** de la base de desarrollo, con
`init_db()` ejecutado tres veces seguidas:

```
ANTES    173 filas   sha 6e0d5108da13f1ab
         indices: device_path, storage_state
DESPUES  173 filas   sha 6e0d5108da13f1ab
         indices: device_path, orden, storage_state

FILAS INTACTAS: si       IDEMPOTENTE: si (3 vueltas, un indice)
```

No añade columnas, no reescribe filas y no borra nada. Un índice es
una estructura de búsqueda: la tabla no se toca.

### Compatibilidad con Agents antiguos

Comprobado con un Agent simulado que **no declara capacidades**, como
haría uno anterior:

```
GET /api/recordings/<id>/local-video
  -> 409  code: agent_sin_transmision
  -> mensajes enviados al agente: []     <- ninguno
```

El backend nuevo **no le manda ni una sola orden que no entienda**. Y
lo sigue viendo en el listado con normalidad.

### Respaldo, primero

```bash
cd /opt/remoteadmin/RemoteAdmin

# El commit actual: es a donde se vuelve si algo sale mal
sudo -u remoteadmin git rev-parse HEAD            # ANOTA ESTO
sudo -u remoteadmin git status --short            # debe salir vacio

# Respaldo completo y verificado
sudo -u remoteadmin ./.venv/bin/python scripts/respaldo.py crear
sudo -u remoteadmin ./.venv/bin/python scripts/respaldo.py probar \
    respaldos/veyra-AAAAMMDD-HHMMSS
```

`probar` tiene que decir **«el respaldo SIRVE para restaurar»**. Si
dice otra cosa, se para aquí.

Y una copia de los archivos que se van a tocar, por separado del
respaldo general:

```bash
RAIZ=/opt/remoteadmin/RemoteAdmin
FECHA=$(date +%Y%m%d-%H%M%S)

sudo -u remoteadmin mkdir -p $RAIZ/respaldos/codigo-$FECHA

for f in backend/database.py backend/main.py backend/recordings.py \
         frontend/app.js frontend/index.html; do
    sudo -u remoteadmin cp -p --parents "$f" \
        $RAIZ/respaldos/codigo-$FECHA/
done

sudo -u remoteadmin sha256sum \
    $RAIZ/respaldos/codigo-$FECHA/backend/*.py \
    $RAIZ/respaldos/codigo-$FECHA/frontend/*

ls -lR $RAIZ/respaldos/codigo-$FECHA
```

Y sácate una copia fuera del servidor:

```bash
# desde tu equipo
scp -r ubuntu@159.54.156.221:/opt/remoteadmin/RemoteAdmin/respaldos/veyra-... .
```

Un respaldo en el mismo disco que la base no protege del fallo más
probable.

### Desplegar

```bash
cd /opt/remoteadmin/RemoteAdmin

sudo -u remoteadmin git fetch origin
sudo -u remoteadmin git pull --ff-only origin main
sudo -u remoteadmin git rev-parse --short HEAD

# Dependencias: solo si cambiaron. En esta version NO cambian.
sudo -u remoteadmin git diff --name-only <commit-anterior> HEAD \
    | grep requirements   # no debe salir nada

sudo systemctl restart remoteadmin
systemctl is-active remoteadmin
```

### Comprobar — lo que debes ejecutar en tu sesión de Ubuntu

```bash
RAIZ=/opt/remoteadmin/RemoteAdmin
URL=https://remoteadmin.tail37c528.ts.net

# 1 · El servicio responde
curl -s $URL/api/health
curl -sI $URL/panel | head -1

# 2 · El indice existe y la tabla esta entera
sudo -u remoteadmin sqlite3 "file:$RAIZ/remoteadmin.db?mode=ro" \
  "SELECT name FROM sqlite_master
    WHERE type='index' AND tbl_name='recordings';"
#   debe incluir idx_recordings_orden

sudo -u remoteadmin sqlite3 "file:$RAIZ/remoteadmin.db?mode=ro" \
  "SELECT 'equipos', COUNT(*) FROM devices
   UNION ALL SELECT 'grabaciones', COUNT(*) FROM recordings
   UNION ALL SELECT 'usuarios', COUNT(*) FROM users
   UNION ALL SELECT 'auditoria', COUNT(*) FROM audit_log;"
#   los mismos recuentos que antes de actualizar

# 3 · Integridad de la base
sudo -u remoteadmin sqlite3 "file:$RAIZ/remoteadmin.db?mode=ro" \
  "PRAGMA integrity_check;"
#   ok

# 4 · Nada roto en el registro
journalctl -u remoteadmin -n 80 --no-pager | grep -iE "error|traceback"
sudo tail -n 40 /var/log/nginx/error.log

# 5 · Los equipos vuelven solos, sin que nadie los toque
journalctl -u remoteadmin --since "5 minutes ago" | grep -i "agent connected"
```

Y en el navegador, con tu sesión:

| Qué | Qué debe pasar |
|---|---|
| Abrir Grabaciones | 10 filas y, debajo, «10 de 173» con «Ver más» |
| Pulsar «Ver más» | 20 filas, ninguna repetida, «20 de 173» |
| Elegir **solo una fecha**, sin horas | Filtra ese día. Antes no filtraba nada |
| Poner hora final anterior a la inicial | Mensaje explicando el problema, no una lista vacía |
| Cambiar de equipo | El listado vuelve al primer bloque |
| Ver una grabación guardada en el servidor | Se reproduce, como siempre |
| Una «Solo local» con el equipo apagado | Dice que está desconectado y que la grabación sigue en su disco |

### Volver atrás

Revertir el código **no necesita restaurar la base**. El índice le
sobra a la versión anterior, que simplemente no lo usa.

```bash
sudo systemctl stop remoteadmin
sudo -u remoteadmin git -C /opt/remoteadmin/RemoteAdmin \
    checkout <commit-anterior>
sudo systemctl start remoteadmin

curl -s https://remoteadmin.tail37c528.ts.net/api/health
```

Si quisieras quitar también el índice —no hace falta, pero por
completar:

```bash
sudo -u remoteadmin sqlite3 /opt/remoteadmin/RemoteAdmin/remoteadmin.db \
  "DROP INDEX IF EXISTS idx_recordings_orden;"
```

`DROP INDEX` no borra ni una fila.

---

## Fase 2 — El instalador 1.4.0

### El candidato

```
archivo : installer\dist\VEYRA-Instalador.exe
tamano  : 79 964 656 bytes (76.3 MB)
SHA-256 : 6798B55BAA746673A5AAF7B88B99C09D3FAB42A68D7B23444376259B3AE71A80
construido : 2026-10-29 13:32:48
```

Construido con `installer/build-installer.ps1` desde el árbol actual,
no desde la compilación anterior: el registro de PyInstaller dice
*«Building because `agent.cpython-313.pyc` changed»* y *«Building
because toc changed»*.

### Verificado extrayendo el contenido del `.exe`

`installer/verificar-exe.py` — **30/30**. Abre el archivo que
PyInstaller pega al final del ejecutable y compara lo de dentro con
el repositorio:

```
agent/agent.py                3ca99c7fe1b6b39a  identico al arbol
agent/commands.py             cbfde7a16aab4918  identico al arbol
agent/version.py              74b363555ec1703b  identico al arbol
agent/scheduler.py            c7757de49bccbdfa  identico al arbol
agent/owners.py               4fcf91ee02c3622f  identico al arbol
agent/recorder.py             648c2085ab21107a  identico al arbol
agent/storage.py              f63ca54848fbe882  identico al arbol
agent/updater.py              f663910533e3b432  identico al arbol
installer/install-agent.ps1   707fbe7960d0bda2  identico al arbol
installer/uninstall-agent.ps1 e4fa6e748859dc01  identico al arbol

VERSION DEL AGENT DENTRO DEL EXE: 1.4.0
SERVIDOR PRECARGADO: https://remoteadmin.tail37c528.ts.net
```

La transmisión viaja dentro: `handle_recording_stream_message`,
`_enviar_grabacion`, `recording_stream_start`,
`recording_stream_abort`, `agent_capabilities`,
`find_managed_recording`.

Las guardas de identidad también: `yaEnrolado`, `RespaldoAgent`,
`agent.anterior`, `-Forzar`, y **no borra ni renombra
`identity.json`** en ningún caso.

Sin secretos. El único `.pem` es `certifi/cacert.pem`, el paquete
público de CA que sirve para **validar** TLS.

> El verificador dio al principio un falso positivo: buscaba la
> cabecera `BEGIN ... PRIVATE KEY`, que la librería `cryptography`
> lleva como constante para *leer* claves ajenas
> (`_SK_START = b"-----BEGIN OPENSSH PRIVATE KEY-----"`). Ahora exige
> que detrás venga el cuerpo en base64, así que distingue un
> delimitador de una clave de verdad sin dejar de detectar una real.

### Qué se conserva al actualizar un equipo ya registrado

`tests/test_actualizacion_instalador.py` — **29/29**, ejecutando el
bloque real del instalador sobre carpetas temporales:

| | |
|---|---|
| `config\identity.json` | se conserva: `device_id` y `agent_token` |
| `.env` | se conserva |
| `recordings\` | se conservan |
| `logs\` | se conservan |
| **La clave RAE** | **no se vuelve a pedir** |

Solo se reemplazan `agent\` e `installer\`. El `agent\` anterior se
mueve a `agent.anterior\`, y si la copia falla se repone solo.

### El manifiesto no se toca

Sigue en **1.2.0**, sin firmar nada. Como `AGENT_VERSION` es 1.4.0 y
es posterior, `updater.py` no ve ninguna versión más nueva: **la
flota no se mueve sola**.

Efecto conocido: `installer_info()` toma la versión del manifiesto,
así que el panel seguirá etiquetando el instalador como **1.2.0**. El
tamaño y el SHA-256 sí serán los nuevos, y lo que instale será 1.4.0.

### Publicar el `.exe` — bloqueado por SSH

```bash
# 1 · Subirlo con nombre provisional: todavia no pisa nada
scp installer/dist/VEYRA-Instalador.exe \
    ubuntu@159.54.156.221:/tmp/VEYRA-Instalador.nuevo.exe

ssh ubuntu@159.54.156.221
RAIZ=/opt/remoteadmin/RemoteAdmin

# 2 · Que llego entero
sha256sum /tmp/VEYRA-Instalador.nuevo.exe
#   6798b55baa746673a5aaf7b88b99c09d3fab42a68d7b23444376259b3ae71a80

# 3 · Respaldo del actual, verificado ANTES de sustituir
sudo cp -p $RAIZ/releases/VEYRA-Instalador.exe \
           $RAIZ/releases/VEYRA-Instalador.1.2.0.exe
sudo sha256sum $RAIZ/releases/VEYRA-Instalador.1.2.0.exe
#   fa83df9a71b8f977e3413fba9f9b9e18288029c2b071e451274c02aaa13264ef
#   si NO coincide: PARA. No sustituyas nada.

# 4 · Sustituir conservando dueño y permisos del original
sudo install -o $(stat -c %U $RAIZ/releases/VEYRA-Instalador.exe) \
             -g $(stat -c %G $RAIZ/releases/VEYRA-Instalador.exe) \
             -m $(stat -c %a $RAIZ/releases/VEYRA-Instalador.exe) \
             /tmp/VEYRA-Instalador.nuevo.exe \
             $RAIZ/releases/VEYRA-Instalador.exe

# 5 · Verificar en destino
sudo sha256sum $RAIZ/releases/VEYRA-Instalador.exe
ls -l $RAIZ/releases/
rm /tmp/VEYRA-Instalador.nuevo.exe
```

No hace falta reiniciar el servicio: el endpoint sirve el archivo que
encuentre en ese momento.

**Volver atrás** es una línea:

```bash
sudo cp -p $RAIZ/releases/VEYRA-Instalador.1.2.0.exe \
           $RAIZ/releases/VEYRA-Instalador.exe
```

### Comprobar la descarga

Desde el navegador, con tu sesión: entrar al panel, pulsar «Descargar
instalador», y verificar lo que baje:

```powershell
Get-FileHash $HOME\Downloads\VEYRA-Instalador.exe -Algorithm SHA256
# 6798B55BAA746673A5AAF7B88B99C09D3FAB42A68D7B23444376259B3AE71A80
```

---

## Fase 3 — Un equipo, no la flota

**Un solo equipo registrado.** Hasta que esto no esté hecho, la
reproducción local no está probada en Windows real.

### Antes de tocar nada

Copia `installer/comprobar-equipo.ps1` a ese equipo —**no viaja
dentro del `.exe`**— y ejecútalo. Solo lee, no necesita
administrador:

```powershell
powershell -ExecutionPolicy Bypass -File .\comprobar-equipo.ps1
```

Tiene que decir **`APTO`**. Anota lo que imprima: `device_id`,
servidor, número de grabaciones y versión actual. Son los valores que
después hay que comparar.

### Actualizar

```powershell
# Como administrador, en ese equipo
.\VEYRA-Instalador.exe
```

SmartScreen avisará: el `.exe` no está firmado.

**No** se pide la clave RAE: el instalador detecta la instalación
existente, compara el `.env` instalado con el servidor de destino
(guarda A) y confirma la identidad contra el servidor con el token
del equipo (guarda B).

### Verificar, punto por punto

| # | Qué | Dónde se mira | Qué debe salir |
|---|---|---|---|
| 1 | Identidad conservada | El propio instalador imprime `Identificador del equipo:` | **El mismo `device_id`** que antes |
| 2 | Token conservado | `config\identity.json` sigue existiendo y el equipo conecta sin re-enrolar | El equipo aparece en línea, no uno nuevo |
| 3 | Versión | El instalador imprime `Version instalada:` | **1.4.0** |
| 4 | Conectado | Panel → el equipo en línea | Sí, en unos segundos |
| 5 | Catálogo local | Panel → Grabaciones, filtrar por ese equipo | Las grabaciones anteriores, con «Solo local» |
| 6 | **Grabaciones intactas** | Comparar el recuento con el del paso previo | **El mismo número** |
| 7 | **Reproducir un MP4 local** | Pulsar «Ver grabación» en una «Solo local» | El vídeo se reproduce en el navegador |
| 8 | **No se sube** | La insignia tras reproducir | Sigue diciendo «Solo local» |
| 9 | **Avanzar y retroceder** | Arrastrar la barra adelante y atrás | Salta sin recargar el vídeo entero |
| 10 | **Cancelación** | Cerrar el reproductor y mirar el equipo | El Agent deja de leer; sin CPU ni red residual |
| 11 | **Conservar** | Marcar y desmarcar «Conservar» | Cambia y se mantiene al recargar |
| 12 | Guardar en servidor | «Guardar en el servidor» en una local | Pasa a «En el servidor» y se puede descargar |

Para el punto 10, en el equipo:

```powershell
Get-Process python, pythonw -ErrorAction SilentlyContinue |
  Select-Object Name, Id, CPU
```

Y en el servidor, que no quede nada colgado:

```bash
journalctl -u remoteadmin --since "10 minutes ago" | grep -i "stream"
```

### Si algo sale mal

El `agent\` anterior está en `agent.anterior\`. Con el Agent parado:

```powershell
Stop-ScheduledTask -TaskName RemoteAdminAgent
Remove-Item 'C:\Program Files\RemoteAdmin\agent' -Recurse -Force
Rename-Item 'C:\Program Files\RemoteAdmin\agent.anterior' 'agent'
Start-ScheduledTask -TaskName RemoteAdminAgent
```

La identidad, las grabaciones y la configuración no se tocan en
ningún momento de ese camino.

---

## Lo que NO se hace en ninguna fase

- Firmar o publicar el manifiesto.
- Crear `agent-1.4.0.zip`.
- Activar la actualización automática de la flota.
- Actualizar más de un equipo hasta que la fase 3 esté verificada.
- Borrar grabaciones, registros, configuraciones ni credenciales.
