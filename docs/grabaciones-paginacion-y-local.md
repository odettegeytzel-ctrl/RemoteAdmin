# Grabaciones: paginación y reproducción local

Qué cambió en el módulo Grabaciones, por qué, y qué sigue pendiente
de probar en un equipo real.

---

## 1. Lo que fallaba

| Síntoma | Causa |
|---|---|
| Demasiados registros | `GET /api/recordings` hacía `SELECT` sin `LIMIT` y el panel pintaba todas las filas |
| La fecha no filtraba | El panel solo enviaba `start`/`end` si había hora; una fecha sola se ignoraba en silencio |
| Filtros poco fiables | El rango se filtraba en Python sobre el listado completo, sin validar que el fin fuera posterior al inicio |
| Paginar habría repetido filas | `ORDER BY started_at DESC` sin desempate, con fechas repetidas en la base |
| Las locales no se reproducían | No existía ninguna vía para leer el vídeo del equipo; `/video` exige copia en el servidor |
| «No se pudo reproducir» | El `<video>` recibía un JSON de error donde esperaba un MP4 y lo convertía en un mensaje genérico |

---

## 2. Paginación

El panel pide bloques de 10 con `limit` y `offset`. El recorte ocurre
en SQL, no en el navegador: una página son diez filas desde la base
hasta la pantalla.

```
GET /api/recordings?limit=10&offset=0
    -> { recordings: [...], total: 173, offset: 0,
         limit: 10, has_more: true }
```

`limit` es **opcional**. Sin él la respuesta es la de siempre, el
historial completo, porque hay dos llamadas que lo necesitan entero:
la ficha de un equipo y el resumen del panel. Solo la pantalla de
Grabaciones pagina.

### El orden

`ORDER BY started_at DESC, id DESC`. El desempate por `id` no es
decorativo. El catálogo local fecha cada archivo por su hora de
modificación, así que hay grabaciones que comparten `started_at` —en
la base de desarrollo hay cuatro pares—. Sin desempate, SQLite puede
colocarlas distinto en dos consultas, y entre la página 1 y la 2 eso
se traduce en una grabación repetida y otra que no aparece nunca.

Hay un índice nuevo, `idx_recordings_orden`, para que cada «Ver más»
no obligue a ordenar la tabla entera.

### El filtro por empresa

También en SQL (`device_id IN (...)`). Antes se recortaba la lista en
Python después de consultarla; con páginas eso devolvería bloques
cortos o vacíos con el botón «Ver más» todavía visible.

---

## 3. Ver una grabación sin subirla

Una grabación que sigue en el disco del equipo ya se puede reproducir.
El vídeo **no pasa por el disco del servidor**: llega del Agent por el
WebSocket que ya estaba abierto y sale hacia el navegador según llega.

```
navegador                backend                     Agent
    |  GET .../local-video   |                          |
    |----------------------->|  recording_stream_start: |
    |                        |  {filename, offset, len} |
    |                        |------------------------->|
    |                        |  recording_stream_ready: |
    |                        |<-------------------------|
    |                        |  <id>+bytes  (256 KB)    |
    |<== StreamingResponse ==|<-------------------------|
    |  (cierra el reproductor)                          |
    |                        |  recording_stream_abort: |
    |                        |------------------------->|
```

### Por qué es seguro

- Al equipo viaja un **nombre de archivo**, nunca una ruta. El
  navegador solo manda un número de fila; el nombre lo saca el
  servidor de su propia base.
- El Agent lo resuelve con `find_managed_recording()`, que hace
  `basename()` y busca dentro de su carpeta administrada. Un `..` o
  una ruta absoluta no llevan a ninguna parte.
- El endpoint comprueba el permiso `recordings.view` **y** que el
  equipo pertenezca a la organización del usuario. Sin lo segundo,
  cualquiera podría ver las grabaciones de otra empresa probando
  números.
- TLS y la autenticación del WebSocket son los que ya había.

### Saltar en el vídeo

El endpoint admite `Range`, así que el reproductor puede adelantar y
retroceder sin descargar el vídeo entero: pide `offset` y `length`, y
el Agent lee solo ese trozo del archivo.

### Cancelar

Cerrar el reproductor o cambiar de grabación corta la petición. El
servidor lo detecta y manda `recording_stream_abort`, y el Agent deja
de leer. El envío corre en una tarea aparte, no en el bucle que recibe
mensajes: si corriera dentro, el Agent estaría sordo a la cancelación
hasta haber mandado el archivo completo.

---

## 4. Lo que NO cambió

**Ver una grabación local no la sube.** El estado sigue en `local` /
`local_only` después de reproducirla, y hay una prueba que lo
comprueba.

Guardar en el servidor sigue siendo una decisión explícita, con el
mismo botón y el mismo flujo. `stored` se pone solo cuando el servidor
confirma la copia. No se borra ningún archivo del equipo.

**Descargar** sigue siendo solo de la copia del servidor. Bajar un
vídeo entero desde el equipo por el WebSocket del Agent competiría con
su trabajo real; para eso está «Guardar en el servidor».

---

## 5. Estados en la interfaz

| Insignia | Significa | Se puede ver |
|---|---|---|
| **Solo local** | El archivo está en el equipo y el equipo está conectado | Sí, desde el equipo |
| **Solo local** (equipo desconectado) | El archivo está en su disco pero no se puede pedir ahora | No, y se explica |
| **En el servidor** | Hay copia centralizada confirmada | Sí |
| **Guardando…** | Transferencia en curso | No |
| **Error al guardar** | Falló la subida; la copia del equipo sigue intacta | No |

Cuando falla la reproducción se muestra la causa real —equipo
desconectado, archivo borrado, sin permiso— en vez del mensaje
genérico de antes.

---

## 6. La línea de tiempo

Con el listado paginado, la línea de tiempo solo conoce lo que ya se
ha traído. Mientras queden páginas por cargar lo dice en su etiqueta:

> *solo las 10 cargadas de 173; pulsa «Ver más» para completarla*

Callarlo convertiría cada página pendiente en un hueco gris, y un
hueco significa «aquí no se grabó nada», que es una afirmación
distinta y falsa.

---

## 7. Archivos tocados

| Archivo | Cambio |
|---|---|
| `backend/recordings.py` | `list_recordings_page()`, `count_recordings()`, filtro SQL compartido |
| `backend/database.py` | Índice `idx_recordings_orden` |
| `backend/main.py` | `limit`/`offset` y validación de rango en `/api/recordings`; endpoint `/local-video`; registro y encaminamiento de transmisiones |
| `agent/agent.py` | `handle_recording_stream_message()` y `_enviar_grabacion()` |
| `agent/commands.py` | Las órdenes nuevas son de fondo, no de escritorio |
| `frontend/app.js` | Paginación, filtros, estados y reproductor |
| `frontend/index.html` | Pie de la tabla con el botón «Ver más» |
| `tests/test_grabaciones_paginadas.py` | Suite nueva, 80 comprobaciones |

---

## 7bis. Correcciones posteriores a la primera versión

Al preparar la validación aparecieron tres defectos en el código
recién escrito. Los tres están corregidos.

**Un viaje de ida y vuelta de más por cada salto.** El endpoint
preguntaba primero el tamaño del archivo y pedía el trozo después.
Un vídeo se reproduce a base de decenas de peticiones Range —cada
salto del usuario es una—, así que ese viaje extra se pagaba decenas
de veces. Ahora el equipo devuelve el tamaño real y el trozo ya
ajustado en la **misma** respuesta. Una prueba cuenta los mensajes y
exige que sea uno.

**Un Range imposible se contestaba 502.** El backend se quedaba solo
con el texto del error del equipo y descartaba su código, así que
«el trozo pedido está fuera del archivo» llegaba como avería del
equipo en vez de como **416**, que es lo que el reproductor sabe
interpretar.

**El agente antiguo tardaba 20 s en dar un error equivocado.** Ver
el apartado 10.

---

## 8. Pruebas

Tres niveles, y conviene no confundirlos.

### Nivel 1 — Automatizadas, con Agent simulado

`tests/test_grabaciones_paginadas.py` — **83/83**.

Paginación, filtros, aislamiento entre empresas, permisos, duplicados
y el camino del endpoint. El Agent es un simulacro escrito para la
prueba.

### Nivel 2 — Código real del Agent, Windows real, MP4 reales

`tests/test_grabaciones_transmision_real.py` — **56/56**.

Aquí **se importa `agent/agent.py` y se ejecutan sus funciones de
verdad** sobre dos MP4 generados con ffmpeg y leídos del disco de
esta máquina:

```
rec_1000000001.mp4   47 210 bytes   indice al inicio (faststart)
rec_1000000002.mp4   47 210 bytes   indice al final
```

Qué se comprobó con ellos:

| | |
|---|---|
| El vídeo entero llega byte a byte igual al del disco | los dos |
| El índice `moov` **al final** se encuentra leyendo el final | sí, con `bytes=-N` |
| Saltar adelante, atrás y rango abierto | sí |
| Rango imposible → **416**, no 502 | sí |
| Una petición Range = **un** viaje al equipo | sí |
| Cancelación recibida **con el envío a medias** | sí |
| Desconexión a media transmisión | sí, sin dejar nada colgado |
| Conexión lenta | sí, el vídeo llega igual |
| Travesía de rutas (`..`, absolutas, `SAM`) | rechazadas por el código real |
| Reproducir no sube ni deja copia en el servidor | sí |
| Agent antiguo → aviso inmediato, no error de archivo | sí |

El aislamiento es total: `ProgramData` apunta a una carpeta temporal
**antes** de importar el Agent, así que el Agent instalado en esta
máquina, su identidad, su configuración y sus grabaciones no se tocan
ni se leen.

**Lo que este nivel NO prueba**, y no se puede afirmar a partir de
aquí: el servicio `RemoteAdminAgent` instalado en un equipo, un
WebSocket de verdad por la red con su latencia y sus cortes, y el
reproductor de un navegador.

### Nivel 3 — Equipo Windows registrado

**Nada.** No se ha ejecutado ninguna prueba en un equipo real.

### Regresión

Las 61 suites, **todas pasan** salvo `test_f4_validation`, **45/46**,
con un fallo **preexistente y ajeno a este trabajo**: la base de
desarrollo tiene 162 filas `local` de una prueba real del 27-10-2026
y esa comprobación espera que el listado solo traiga `stored`. No se
ha tocado la base para hacerla pasar.

`test_panel_acciones` se actualizó de 32 a **47/47**: fijaba el
comportamiento anterior —que una grabación local no se podía abrir—
que es justo lo que esta mejora cambia. Lo que protegía sigue en pie:
una acción solo se ofrece si puede funcionar, y cuando no se ofrece,
se dice por qué.

---

## 9. Qué bloquea la prueba real, y qué falta por comprobar

### El bloqueo

**No hay acceso SSH al servidor de producción**
(`159.54.156.221`, solo acepta clave pública y no hay ninguna para él
en esta máquina). Sin eso no se puede publicar el `.exe`, y sin el
`.exe` publicado no se puede actualizar ningún equipo registrado.

Tampoco hay acceso a ningún equipo Windows registrado en producción.

### Lo que queda por comprobar allí

1. Que el servicio instalado —no el código importado en una prueba—
   lea un MP4 de su carpeta y el vídeo se vea en el navegador.
2. Que los saltos funcionen contra un MP4 **grande** y por la red. Lo
   probado aquí son archivos de 46 KB en disco local.
3. Que cerrar el reproductor detenga la lectura en el equipo de
   verdad, con el navegador cortando la conexión.
4. Una grabación larga sobre una conexión real y lenta.
5. Que el reproductor del navegador acepte el flujo. Las cabeceras
   son correctas, pero ningún navegador ha intervenido todavía.

## 10. Compatibilidad con Agents ya instalados

### Cómo se reconoce un Agent antiguo

Al conectarse, el Agent nuevo declara lo que sabe hacer:

```
agent_capabilities:{"version":"1.4.0",
                    "capabilities":["recording_stream"]}
```

Un Agent anterior **no manda ese mensaje**, y esa ausencia es la
señal. No hay que tocar nada en los equipos ya instalados, ni guardar
su versión en la base, ni forzar ninguna actualización. El dato vive
en memoria mientras dura la conexión y se vuelve a declarar al
reconectar.

### Qué pasa al pulsar «Ver» en uno de esos equipos

Antes de pedir el vídeo, el panel comprueba el origen. Con un Agent
antiguo la respuesta es inmediata:

```
409  { "code": "agent_sin_transmision",
       "message": "Este equipo tiene una version del Agent que
                   todavia no sabe transmitir grabaciones. Guardala
                   en el servidor para verla, o actualiza el Agent
                   del equipo." }
```

Tres cosas importan aquí:

- **No es un error de archivo.** No se dice que la grabación no
  exista ni que el equipo falle: el equipo está perfectamente.
- **No hay espera.** Sin esta comprobación habría que agotar los
  20 s del tiempo de espera para decir «no respondió a tiempo», que
  suena a avería y no lo es.
- **Se ofrece la alternativa que sí funciona**: guardarla en el
  servidor.

El panel distingue el caso por `code`, no por el texto: el mensaje
está escrito para una persona y puede cambiar.

### Qué sigue funcionando sin actualizar nada

| | Agent antiguo |
|---|---|
| Paginación y «Ver más» | **Sí** |
| Filtros de equipo, fecha y hora | **Sí** |
| Catálogo local (saber qué grabaciones hay) | **Sí** |
| Ver y descargar desde el servidor | **Sí** |
| Guardar una grabación en el servidor | **Sí** |
| Conservar | **Sí** |
| Línea de tiempo | **Sí** |
| **Reproducir en local sin subir** | No: requiere Agent 1.4.0 |

**La paginación y los filtros son puramente de servidor y panel.** No
dependen del Agent en absoluto, así que se pueden desplegar sin tocar
un solo equipo. Solo la reproducción local necesita el Agent nuevo.

---

## 11. La versión del Agent

**1.3.0 → 1.4.0.**

| | |
|---|---|
| Por qué sube | El Agent gana una capacidad: transmitir grabaciones y declarar lo que sabe hacer |
| Por qué MENOR y no MAYOR | Nada de lo anterior cambia de significado. Un servidor nuevo trabaja con Agents viejos y un Agent nuevo con el servidor de siempre |
| Por qué no se queda en 1.3.0 | 1.3.0 es el `.exe` construido el 28-10 y **nunca publicado**. Reutilizar el número haría que dos ejecutables distintos dijeran lo mismo |

**El manifiesto firmado sigue en 1.2.0.** No se toca, no se firma
nada y no se activa ninguna actualización automática. Como
`AGENT_VERSION` (1.4.0) es posterior a la del manifiesto (1.2.0),
`updater.py` no ve ninguna versión más nueva y la flota no se mueve.

Efecto secundario conocido, el mismo de siempre: `installer_info()`
toma la versión del manifiesto, así que **el panel seguirá
etiquetando el instalador como 1.2.0**. El tamaño y el SHA-256 sí
serán los del archivo nuevo.

### Qué archivos lleva el instalador nuevo

Los tres cambios del Agent están ya en `PACKAGE_FILES` y el `.exe`
empaqueta la carpeta `agent/` entera (`--add-data "$Agente;agent"`),
así que **no hay que tocar `build-installer.ps1`**:

| Archivo | Cambio |
|---|---|
| `agent/agent.py` | Transmisión, declaración de capacidades |
| `agent/commands.py` | Las órdenes nuevas son de fondo |
| `agent/version.py` | 1.4.0 |

Y los que ya viajaban desde el trabajo anterior, sin cambios nuevos:
`install-agent.ps1` y `uninstall-agent.ps1` con las guardas de
identidad y la recuperación.

El `.exe` **no está firmado**: SmartScreen avisará, como hasta ahora.

---

## 12. Publicar de forma coordinada

El orden importa. La regla: **el servidor nuevo entiende a los Agents
viejos, pero un Agent nuevo contra un servidor viejo declararía una
capacidad que ese servidor no sabe leer**. Así que el servidor va
primero.

### Paso 1 — Backend y panel, juntos

Van en el mismo despliegue porque el panel nuevo pide `limit`/`offset`
y lee `total`, y el backend nuevo es quien los sirve.

- `backend/recordings.py`, `backend/database.py`, `backend/main.py`
- `frontend/app.js`, `frontend/index.html`

El índice `idx_recordings_orden` se crea solo al arrancar, con
`CREATE INDEX IF NOT EXISTS`. **No borra ni modifica ninguna fila.**

Tras este paso, y sin tocar un solo equipo, ya funcionan la
paginación, los filtros corregidos, la línea de tiempo honesta y los
estados nuevos. Los Agents antiguos siguen trabajando igual.

**Comprobación antes de seguir:** entrar al panel, ver que el listado
trae 10 con «Ver más», que filtrar por una fecha sola funciona, y que
una grabación guardada en el servidor se reproduce.

### Paso 2 — El instalador

Reconstruir el `.exe` con el script oficial y sustituir el publicado,
con respaldo y verificación de SHA-256 antes y después. **Sigue
bloqueado por el acceso SSH al servidor de producción.**

### Paso 3 — Un equipo, no la flota

Actualizar **un** equipo registrado con el instalador nuevo y
comprobar ahí la reproducción local de principio a fin. Hasta que eso
no esté hecho, la función no está probada en Windows real.

### Lo que NO se hace en ningún paso

- Firmar o publicar el manifiesto.
- Crear `agent-1.4.0.zip`.
- Activar la actualización automática de la flota.
- Borrar grabaciones, registros ni configuraciones.
