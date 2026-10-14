# Sesiones de grabación: plan en seis fases

Plan aprobado. El diagnóstico y las pruebas que lo sostienen están en
[grabacion-sesion-unica.md](grabacion-sesion-unica.md).

**Nada de esto está implementado.** Solo la fase 1 tiene trabajo hecho
—el cambio de versión— y está sin firmar y sin publicar.

## Decisiones tomadas

| | |
|---|---|
| Orden | **1.2.0 primero**, validada en Plastika, y después las sesiones |
| Segmentos tras unir | **Se conservan.** La retención decidirá, respetando «Conservar» |
| Precisión del horario | **Al límite exacto**, sin depender del ciclo de 30 s |
| Histórico | **Las 496 no se tocan.** Ni se unen ni se modifican |
| Alcance | Una grabación final por equipo e intervalo, **solo para las nuevas** |

---

# Fase 1 — Publicar y validar la 1.2.0

**Hecho y verificado. Falta que firmes.**

## Revisión del paquete

Construido con el mismo código que usará el firmador, y examinado:

```
19 archivos · 73 001 bytes
sha256 0cbae61d282e61569a6d8e58f0624ed10bb591ef984b831f193d94fc81ad6748
```

```
agent/__init__.py       agent/paths.py      agent/updater.py
agent/agent.py          agent/release.py    agent/version.py
agent/commands.py       agent/power.py      requirements-base.txt
agent/helper.py         agent/recorder.py   requirements-agent-windows.txt
agent/inventory.py      agent/scheduler.py  installer/install-agent.ps1
agent/ipc.py            agent/storage.py    installer/uninstall-agent.ps1
                                            installer/README.md
```

Verificado leyendo el ZIP, no el repositorio:

```
OK   apply_continuous ya no arranca a ciegas
OK   la continua NO se marca como manual
OK   informa quien sostiene la grabacion
OK   la continua se limita al horario
OK   distingue la fuente
OK   excepcion manual con fecha
OK   version.py: 1.2.0
OK   NO viaja ningun .env
OK   NO viaja ninguna clave privada
```

Versionado: `is_newer("1.2.0", "1.1.0")` es cierto; el camino inverso,
falso. Un Agent en 1.1.0 la acepta y no puede volver atrás.

## Pasos de firma, en tu equipo

```powershell
# 1. Firmar. La clave se lee donde este y se usa EN MEMORIA.
#    No se copia al proyecto ni al servidor.
python tools\sign_agent_release.py --firmar --clave <tu_ruta>\agent-signing.pem

# 2. Comprobar antes de mover nada.
#    Debe decir  "version": "1.2.0"  y el sha256 de arriba.
type releases\manifest.json

# 3. Copiar los DOS archivos a Oracle. No viajan por git.
scp releases\agent-1.2.0.zip releases\manifest.json ^
    <usuario>@<servidor>:/opt/remoteadmin/RemoteAdmin/releases/

# 4. Confirmar que el servidor lo sirve
curl -s https://<url>/api/agent/manifest
```

La herramienta **comprueba su propia firma contra la clave pública que
lleva el Agent** antes de escribir nada: con la clave equivocada te lo
dice en vez de publicar algo que ningún Agent podría instalar.

**Si algo sale mal:** borrar `manifest.json` del servidor deja a todos
los Agents en 1.1.0, donde están hoy. Si falla la instalación en el
equipo, el propio actualizador vuelve atrás solo.

## Criterios de aceptación

1. El Agent de Plastika informa **versión 1.2.0** en el panel.
2. `config\identity.json` **no ha cambiado**: mismo `device_id`, y no
   ha aparecido un equipo duplicado.
3. Sus grabaciones locales y sus registros siguen ahí.
4. Con horario de **09:00 a 17:00**: a las 08:59 no graba, a las 09:01
   sí, a las 17:01 no.
5. **Ninguna grabación nueva fuera del intervalo**, especialmente de
   madrugada, que era el síntoma.
6. Al reconectar, la grabación **no** se presenta como manual.

Pruebas automáticas ya en verde: `test_actualizacion` 181/181,
`test_continua_y_horario` 58/58, `test_instalador` 86/86.

**Lo que no puedo comprobar yo:** nada de los seis puntos de arriba.
Todos necesitan el equipo Windows real.

---

# Fase 2 — El horario, al límite exacto

## Qué cambia

Hoy `_schedule_loop` duerme 30 segundos fijos, así que una franja de
09:00 puede empezar hasta medio minuto tarde.

Se sustituye por un sueño calculado: el planificador dice **cuántos
segundos faltan para el próximo borde** —el inicio o el fin de franja
más cercano— y el bucle duerme hasta justo antes, con un techo de 30 s
para seguir reaccionando a un horario nuevo o a un cambio de hora del
sistema.

No se añade otra lógica de programación: `inicio_de_la_franja()` ya
sabe calcular los bordes; solo hay que exponerlos.

| archivo | qué |
|---|---|
| `agent/scheduler.py` | `segundos_hasta_el_proximo_borde(ahora)` |
| `agent/agent.py` | el bucle duerme lo que diga, con techo de 30 s |

**Migraciones:** ninguna.

## Pruebas

- Franja **09:00–17:00** y franja **14:00–18:00**: borde exacto en
  ambos extremos, con el reloj inyectado.
- Un segundo antes y un segundo después de cada borde.
- Cruce de medianoche: 22:00–06:00, el día que cuenta es el del inicio.
- Días de la semana: lunes a viernes activo, sábado y domingo no.
- Zonas horarias: `America/Mexico_City`, `UTC`, una desconocida
  (cae a hora local), y el cambio de horario de verano.
- Cambio de horario a mitad de franja: se aplica sin reiniciar.
- Que el techo de 30 s se respete aunque el próximo borde esté a horas.

## Criterios de aceptación

- El arranque y la parada ocurren **dentro del segundo** del borde.
- Ninguna prueba del horario actual se rompe.
- Sin conexión, el último horario recibido se sigue cumpliendo.

---

# Fase 3 — La sesión en el Agent

## Qué se construye

Un módulo nuevo, `agent/session.py`. Al abrirse una franja se crea una
sesión identificada por `device_id` + **inicio de franja** —no la fecha
natural, para que un turno de noche sea una sola sesión.

Su índice vive **en disco**, junto a las grabaciones:

```
recordings\sesiones\<clave>.json
    { clave, abierta_en, fuente, segmentos: [ {ruta, desde, hasta,
      segundos, bytes, ancho, alto} ], estado }
```

En disco y no en memoria **porque el Agent puede reiniciarse a mitad
de jornada**. Al arrancar busca sesiones abiertas: si su franja sigue
en curso, la retoma; si ya pasó, la cierra y une lo que haya.

Los segmentos internos **no cambian**: 15 minutos, por la misma razón
de siempre —si el equipo se apaga se pierden 15 minutos, no la jornada.

| archivo | qué |
|---|---|
| `agent/session.py` | **nuevo**: índice, apertura, cierre, recuperación |
| `agent/recorder.py` | avisar del inicio y fin; exponer **ancho y alto** de cada segmento |
| `agent/agent.py` | abrir y cerrar sesión al cambiar de franja; retomar al arrancar |

**Migraciones:** ninguna en la base. El índice es un archivo nuevo.

## Pruebas

- Una franja completa produce **una** sesión con sus N segmentos.
- Reinicio del Agent a mitad: **se retoma**, no se abre otra.
- Reinicio después de que la franja haya pasado: se cierra y se une.
- Dos franjas el mismo día: dos sesiones distintas.
- Turno de noche: 22:00–06:00 da **una** sesión, no dos.
- Una sesión sin segmentos no produce nada ni deja basura.

## Criterios de aceptación

- Nunca dos sesiones para el mismo equipo e intervalo.
- El índice sobrevive a un reinicio.
- Los segmentos se guardan exactamente como hoy.

---

# Fase 4 — La unión, con verificación

Es la fase con el riesgo real. El orden importa y **cada paso solo se
da si el anterior salió bien**:

```
1. comprobar ESPACIO           el doble de la jornada, antes de nada
2. agrupar por COMPATIBILIDAD  resolucion y codec de cada segmento
3. unir con -c copy            por grupo, a un archivo TEMPORAL
4. VERIFICAR el resultado      existe, se lee, duracion y tamano coherentes
5. renombrar al nombre final   solo ahora pasa a existir
6. registrar en el catalogo
7. los originales se QUEDAN
```

## Por qué la verificación no es opcional

Probado: dos segmentos de distinta resolución **se unen con código de
salida 0** y producen un archivo que declara la resolución del primero
para todo el contenido. ffmpeg no avisa. Si confiáramos en su código
de salida, publicaríamos grabaciones rotas.

Por eso el paso 2 agrupa **antes**, y el paso 4 comprueba **después**:

- el archivo existe y tiene tamaño razonable;
- `ffprobe` lo abre y declara vídeo;
- la **duración** es la suma de las partes, con tolerancia de 1 s por
  segmento;
- la **resolución** es la esperada;
- el códec es el esperado.

**Si cualquiera falla**: se borra el temporal, los originales quedan
intactos, se anota el error en la sesión, y la sesión queda
**reintentable**. El temporal más el renombrado final hacen el
reintento idempotente: no puede duplicar la grabación.

Si hay varios grupos de resolución, se producen **varios finales**, uno
por grupo, numerados y con su rango horario. **Nunca un archivo que
describa mal su contenido.**

| archivo | qué |
|---|---|
| `agent/session.py` | espacio, agrupación, unión, verificación, reintento |
| `agent/storage.py` | consulta de espacio libre (ya existe) |

**Migraciones:** ninguna.

## Pruebas

- Tres segmentos compatibles → **un** final, duración exacta.
- Dos resoluciones → **dos** finales, ninguno mal descrito.
- **El caso que lo justifica todo**: comprobar que unir resoluciones
  distintas produce un archivo que miente, y que nuestra verificación
  lo rechaza.
- Unión fallida: originales intactos, error anotado, reintentable.
- Reintento tras un fallo: un solo final, sin duplicar.
- Corte a mitad de la unión: el temporal se descarta, nada a medias.
- Sin espacio suficiente: no se empieza, se pospone, se anota.
- Un segmento corrupto entre buenos: se detecta y no entra.

## Criterios de aceptación

- **Ningún archivo final describe mal su contenido**, nunca.
- Los originales existen al terminar, salga bien o mal.
- Un reintento jamás produce dos finales.

---

# Fase 5 — El servidor y los huecos

## Qué se registra

Una fila por sesión, con hora **real** de inicio y fin, frente a la
**programada**:

```sql
CREATE TABLE IF NOT EXISTS recording_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       TEXT NOT NULL,
    session_key     TEXT NOT NULL,
    scheduled_start TEXT,            -- lo que decia el horario
    scheduled_end   TEXT,
    started_at      TEXT,            -- lo que paso de verdad
    ended_at        TEXT,
    captured_sec    INTEGER,         -- tiempo REALMENTE grabado
    size_bytes      INTEGER,
    source          TEXT,            -- programada | continua | manual
    state           TEXT,            -- grabando | uniendo | lista | error
    error           TEXT,
    gaps            TEXT,            -- JSON
    final_path      TEXT,
    storage_state   TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_key
    ON recording_sessions (device_id, session_key);

ALTER TABLE recordings ADD COLUMN session_id INTEGER;
```

El **índice único** es lo que impide registrar dos veces la misma
sesión aunque el Agent reintente o se reinicie.

`session_id` en `NULL` = segmento histórico. **Las 496 se quedan
exactamente como están**: se siguen viendo y reproduciendo igual.

## Duración programada contra capturada

Las dos se guardan y las dos se enseñan. Una franja de 09:00 a 17:00
con una desconexión de una hora son **ocho horas programadas y siete
capturadas**. Enseñar solo una de las dos engaña.

## Los huecos

Más de **60 segundos** entre el fin de un segmento y el inicio del
siguiente es un hueco real: desconexión, apagado o pausa. Se anota en
`gaps` y el panel lo enseña: *«sin grabación de 11:42 a 12:07»*.

**No se rellena ni se disimula.** El archivo final salta, y se dice.

| archivo | qué |
|---|---|
| `backend/database.py` | la tabla, el índice, la columna |
| `backend/recordings.py` | alta y consulta de sesiones; cálculo de huecos |
| `backend/main.py` | endpoints de sesión; **exclusión manual/continua** |
| `agent/agent.py` | informar de la sesión al servidor |

## La exclusión que falta

Hoy la exclusión entre manual y continua vive **solo en el Agent**. El
servidor acepta una orden manual durante una continua y el Agent la
ignora, así que el panel cree que pasó algo que no pasó. Se añade la
comprobación **también en el endpoint**, para que el panel reciba un
error claro.

## Pruebas

- Alta de sesión; alta repetida de la misma → **una** fila.
- Reinicio del servidor a mitad: la sesión se recupera.
- Huecos: ninguno, uno, varios, uno de 59 s (**no** es hueco).
- Programada 8 h con 1 h de hueco → 8 programadas, 7 capturadas.
- Las 496 históricas siguen listándose y reproduciéndose.
- Manual durante una continua: **rechazada con un motivo legible**.
- Continua durante una manual: misma regla.
- Reconexión del Agent: la sesión sigue siendo `programada`, no pasa a
  `manual`.
- Reversión: con la columna presente, el código anterior lee y escribe.

## Criterios de aceptación

- Una fila por equipo e intervalo, pase lo que pase.
- Ninguna grabación histórica cambia.
- Manual y continua nunca activas a la vez para el mismo equipo.

---

# Fase 6 — Panel y retención

## El panel

- Una entrada por equipo y sesión, con: intervalo programado, tiempo
  capturado, tamaño, estado y huecos.
- Al abrirla, los segmentos en orden, con reproducción encadenada: un
  solo `<video>` que cambia de `src` al evento `ended`.
- Si hay final único, se reproduce ese. Si la unión falló o hay varios
  grupos, se encadenan los que haya, y **se dice cuál es el caso**.
- Los botones se pintan desde **una sola lectura del estado
  confirmado**. Hoy salen de dos consultas distintas
  (`refreshContinuousStatus` y `refreshRecordingStatus`) que pueden
  llegar desfasadas.

## La retención

Lo que ya existe y **no hay que rehacer**:

| | |
|---|---|
| Plazo configurable | `RETENTION_CHOICES = ("15", "30", "90")` |
| Por defecto | **90 días** — `DEFAULT_SETTINGS` |
| Por organización | Sí, cada empresa el suyo |
| «Conservar» por grabación | Sí, columna `keep`, y la retención la respeta |

Lo que **falta**, y es lo que pides:

**La retención no mira `storage_state`.** Selecciona con
`WHERE keep = 0 AND status = 'stored'`
([recordings.py:312](../backend/recordings.py)), que es del modelo
anterior, cuando subir era automático. Con el modelo local-first hay
que comprobarlo: si una fila tiene `status='stored'` pero su archivo
está **solo en el equipo**, la retención borraría del catálogo una
grabación que sigue existiendo allí, y el servidor **no tiene ni
autorización ni forma de borrar el archivo local**.

Pendiente de la consulta a producción que te pedí para saber si ese
caso se da hoy. Si se da, es un fallo activo y hay que tratarlo antes
que el resto de esta fase.

El cambio: seleccionar por **`storage_state`**, no por `status`.
Borrar el archivo **solo** cuando está en el servidor. De lo que está
en el equipo, la retención local del Agent ya se ocupa
([agent/storage.py:125](../agent/storage.py)).

Y las sesiones entran en la retención como una unidad: si todos sus
segmentos vencen y ninguno está marcado «Conservar», se retira la
sesión entera. Si uno está protegido, **no se toca la sesión**.

| archivo | qué |
|---|---|
| `frontend/app.js` | listado por sesión, encadenado, estado único |
| `frontend/index.html` | la vista de sesión |
| `backend/recordings.py` | retención por `storage_state`; sesiones |
| `backend/settings.py` | sin cambios: el plazo y el 90 ya están |

## Pruebas

- Plazo 15 / 30 / 90; por defecto 90.
- «Conservar» protege, también dentro de una sesión.
- Una grabación `local_only` **no se borra desde el servidor**.
- Una `server_stored` vencida sí.
- Histórico con `storage_state` en `NULL`: se interpreta como hoy.
- El resumen diario de la retención sigue publicándose
  (`test_retencion_resumen`, 11/11).
- Reproducción encadenada: orden correcto, hueco señalado, segmento
  local saltado con aviso.
- Botones: los seis estados, cada uno desde el estado confirmado.

## Criterios de aceptación

- **Ninguna grabación histórica se borra ni se modifica.**
- La retención nunca intenta borrar un archivo que no está en el
  servidor.
- «Conservar» gana siempre.
- El panel muestra una entrada por sesión, con las dos duraciones.

---

# Resumen de migraciones

Todas aditivas y reversibles. **Ninguna borra, mueve ni sobrescribe
grabaciones.**

| fase | migración |
|---|---|
| 1, 2, 3, 4 | ninguna |
| 5 | tabla `recording_sessions`, índice único, columna `recordings.session_id` |
| 6 | ninguna |

Revertir deja la tabla y la columna sin usar. SQLite no se queja de
columnas que nadie nombra: ya lo comprobamos al revertir el despliegue
anterior, donde el código de `f911f4e` leyó **y escribió** sobre una
base con columnas de más.

---

# Riesgos que siguen abiertos

**1. La unión silenciosamente corrupta.** Demostrada. Acotada por la
fase 4, pero es el riesgo principal del trabajo.

**2. La retención y `storage_state`.** Puede ser un fallo **activo
hoy**, no una mejora. Depende de la consulta a producción.

**3. Disco durante la unión.** Hace falta el doble de la jornada,
~340 MB para 8 h. Si no hay, se pospone.

**4. Nada de esto se ve en Plastika hasta publicar la 1.2.0.**

---

# Lo que necesito de ti

**Para empezar la fase 1:** firmar y publicar, y después validar los
seis criterios en el equipo real. Yo no puedo hacer ninguno.

**Para planificar bien la fase 6**, la consulta de solo lectura que
sigue pendiente:

```bash
cd /opt/remoteadmin/RemoteAdmin
sudo -u remoteadmin sqlite3 "file:$PWD/remoteadmin.db?mode=ro" <<'SQL'
.mode column
SELECT status, COALESCE(storage_state,'(NULL)') estado, COUNT(*) n,
       ROUND(SUM(size_bytes)/1048576.0,1) mb
  FROM recordings GROUP BY 1,2;

SELECT duration_sec/60 minutos, COUNT(*) n
  FROM recordings GROUP BY 1 ORDER BY n DESC LIMIT 6;

SELECT substr(started_at,1,10) dia, COUNT(*) seg,
       MIN(substr(started_at,12,5)) desde, MAX(substr(ended_at,12,5)) hasta
  FROM recordings GROUP BY 1 ORDER BY 1 DESC LIMIT 10;
SQL
du -sh server_recordings
```

La primera consulta dice si la retención tiene hoy un fallo activo. La
tercera dice si hay grabaciones fuera del horario, que es la prueba
independiente de que el Agent viejo arrancaba de madrugada.
