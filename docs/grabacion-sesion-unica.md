# Grabación por sesión: diagnóstico y plan

**Diagnóstico y plan. No hay nada implementado.** Ni el Agent, ni el
grabador, ni el servidor, ni el panel se han tocado.

---

## Lo primero: ffmpeg ya está en todos los equipos

Era la condición que podía hundir la idea, y está resuelta de
antemano.

```
imageio-ffmpeg==0.6.0        requirements-base.txt:20
```

Es una dependencia **fijada** que viaja en `PACKAGE_FILES`, y el
paquete **trae el binario de ffmpeg dentro**. El grabador ya lo usa
([recorder.py:228](../agent/recorder.py)):

```python
ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
```

Comprobado en esta máquina:

```
ffmpeg: ...\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe
existe: True
```

**No hay nada que instalar ni que distribuir.** Ni una dependencia
nueva, ni un instalador aparte, ni suponer que el equipo lo tenga.

---

## Lo segundo: ¿se pueden unir sin recodificar? Lo probé

Generé segmentos con **exactamente** los parámetros de
`_open_ffmpeg()` —`libx264`, `veryfast`, `yuv420p`, `+faststart`,
15 fps— y los uní con el *concat demuxer* y `-c copy`.

### Caso normal: funciona, y bien

```
seg1  00:00:04.00  640x360
seg2  00:00:04.00  640x360
seg3  00:00:02.00  640x360

union sin recodificar: OK
final: 00:00:10.00  640x360
```

**Diez segundos exactos.** Sin recodificar, sin pérdida de calidad, en
milisegundos: solo se copian los paquetes a un contenedor nuevo. Para
una jornada de ocho horas es una operación de disco, no de CPU.

El archivo final pesó **8 051 bytes** frente a **9 644** de las partes
sumadas: se ahorra una cabecera por segmento.

### Caso peligroso: ffmpeg NO avisa

El grabador **cierra y reabre el segmento si cambia la resolución**
([recorder.py:289](../agent/recorder.py)). Así que en una sesión real
puede haber segmentos de distinto tamaño. Probé qué pasa:

```
a: 00:00:03.00  640x360
b: 00:00:03.00  800x450

union sin recodificar: OK          <-- codigo de salida 0
resultado: 00:00:06.00  640x360    <-- MIENTE
```

**ffmpeg devolvió éxito y produjo un archivo corrupto en la práctica.**
El contenedor conserva la cabecera del primer segmento, así que el
archivo *declara* 640x360 para los seis segundos, pero la segunda
mitad son fotogramas de 800x450. Un reproductor los mostrará
deformados o fallará a mitad.

**Esto es lo más importante de toda la prueba.** Si uniéramos a ciegas
confiando en el código de salida, produciríamos grabaciones rotas y
borraríamos los originales creyendo que todo fue bien. Hay que
**comprobar la resolución de cada segmento antes de unir**, nunca
confiar en que ffmpeg falle.

---

## El estado real del horario

**No he encontrado ningún fallo nuevo en el código.** El flujo está
bien construido:

| | |
|---|---|
| Quién decide | El Agent, con su reloj, para seguir funcionando sin conexión |
| Zona horaria | `ZoneInfo` de la zona del horario; si es desconocida, hora local |
| Cruce de medianoche | Tratado aparte: cuenta el día del **inicio** de la franja |
| Días | Lista o texto `"0,1,2"` |
| Persistencia | Se guarda aunque el Agent esté desconectado |
| Al reconectar | El servidor manda horario **y** continua en el saludo |
| Sin red | `_schedule_loop` corre en su propio hilo cada 30 s |

**El problema es que ese código no está en el equipo.** La corrección
([`9ea7c61`](../agent/scheduler.py)) tocó solo `agent/agent.py` y
`agent/scheduler.py`, y el Agent de Plastika sigue en **1.1.0**. La
entrega publicada es del 7 de octubre, anterior al arreglo.

**La 1.2.0 ya está preparada y verificada; falta firmarla y
publicarla.** Hasta entonces, nada de lo que escriba aquí cambiará el
comportamiento de ese equipo.

### Una imprecisión real, y pequeña

```python
SCHEDULE_CHECK_SECONDS = 30
```

La decisión se revisa cada 30 segundos, así que una franja de 09:00
puede empezar **hasta 30 segundos tarde**, y lo mismo al terminar. No
es un fallo, es el intervalo elegido. Si «exactamente a las 09:00»
significa al segundo, el bucle puede calcular cuánto falta para el
próximo borde y dormir hasta él. **Dime si lo quieres.**

---

## El plan

### Fase A — Publicar la 1.2.0

**Primero, y no es opcional.** Sin esto, lo demás no se nota en el
equipo. Ya está todo verificado; solo falta que firmes.

### Fase B — Sesión en el Agent

Una clase nueva, `RecordingSession`, en su propio módulo:

```
abre la franja   ->  session_id = device + fecha-de-franja
graba            ->  los segmentos de 15 min se guardan IGUAL que hoy
                     y se anotan en un indice de la sesion
cierra la franja ->  se unen, se verifica, y SOLO entonces se decide
                     que hacer con los originales
```

Los segmentos internos **no cambian**. Siguen siendo de 15 minutos,
por la misma razón de siempre: si el equipo se apaga, se pierden 15
minutos y no la jornada.

### Fase C — La unión, con las comprobaciones que la prueba exige

En este orden, y cada paso solo si el anterior salió bien:

1. **Agrupar por resolución.** Si todos los segmentos coinciden, una
   sola tanda. Si no, **varias tandas**, una por resolución. Nunca se
   mezclan: lo probé y ffmpeg no avisa.
2. **Unir con `-c copy`**, por tanda.
3. **Verificar el resultado**: que exista, que `ffprobe` lo lea, que
   su duración sea la suma de las partes con un margen de tolerancia,
   y que la resolución sea la esperada.
4. **Registrar** la grabación final en el catálogo.
5. **Solo entonces**, decidir sobre los originales.

### Fase D — Qué pasa con los originales

**No se borran.** Pasan a `estado = unido` y la retención los trata
como hasta ahora. Que se borren o no es una decisión tuya, aparte, y
no entra en este trabajo.

**Si la unión falla**, los originales se quedan intactos, se anota el
error en la ficha de la sesión, y la sesión queda **reintentable**. El
reintento es idempotente: se escribe a un archivo temporal y solo se
renombra al final, así que un reintento no puede duplicar la
grabación.

### Fase E — Los huecos

Si entre dos segmentos consecutivos hay más de **60 segundos** de
diferencia entre el `ended_at` de uno y el `started_at` del siguiente,
es un hueco real: una desconexión, un apagado, una pausa.

**El archivo final no los rellena.** Los fotogramas saltan, y el hueco
se anota en los metadatos de la sesión para que el panel lo enseñe.
Nunca se presenta como una grabación ininterrumpida.

### Fase F — Exclusión manual / continua

Lo que ya hay y funciona (en 1.2.0):

- `set_continuous()` y `notify_manual()` comparten el mismo
  planificador, así que las dos decisiones ya no se pisan.
- `fuente()` dice **quién** sostiene la grabación: `manual`,
  `programada` o `continua`.
- `apply_continuous()` ya no arranca a ciegas al reconectar, y ya no
  marca la grabación como manual.

Lo que falta:

- El servidor no comprueba hoy que no se pida una manual mientras hay
  una continua en curso. La exclusión vive solo en el Agent.
  **Añadir la comprobación también en el endpoint**, para que el panel
  reciba un error claro en vez de una orden que el Agent ignora.
- El panel pinta el estado de la continua y el de la grabación desde
  **dos consultas distintas** (`refreshContinuousStatus` y
  `refreshRecordingStatus`), que pueden llegar desfasadas. Unificar en
  una sola lectura del estado confirmado.

---

## Lo que cambia en la base

Aditivo y reversible:

```sql
-- La sesion: una fila por equipo e intervalo programado
CREATE TABLE IF NOT EXISTS recording_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       TEXT NOT NULL,
    session_key     TEXT NOT NULL,   -- device + inicio de franja
    started_at      TEXT,            -- hora REAL de inicio
    ended_at        TEXT,            -- hora REAL de fin
    duration_sec    INTEGER,
    size_bytes      INTEGER,
    source          TEXT,            -- programada | continua | manual
    state           TEXT,            -- grabando | uniendo | lista | error
    error           TEXT,
    gaps            TEXT,            -- JSON con los huecos
    final_path      TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_key
    ON recording_sessions (device_id, session_key);

-- Y cada segmento sabe a que sesion pertenece
ALTER TABLE recordings ADD COLUMN session_id INTEGER;
```

El índice único sobre `(device_id, session_key)` es lo que impide
**registrar dos veces la misma sesión** aunque el Agent reintente o se
reinicie a mitad.

`session_id` en `NULL` significa «segmento histórico, sin sesión». Las
496 grabaciones que ya existen se quedan exactamente como están: se
siguen viendo y reproduciendo igual. **No se une nada hacia atrás**
salvo que me lo pidas, y sería una operación aparte.

---

## Archivos que habría que tocar

| archivo | qué |
|---|---|
| `agent/session.py` | **nuevo**: índice de la sesión, unión, verificación, reintento |
| `agent/recorder.py` | avisar del inicio y fin de sesión; exponer la resolución de cada segmento |
| `agent/agent.py` | abrir y cerrar sesión al cambiar la franja; informar al servidor |
| `backend/database.py` | `recording_sessions`, columna `session_id` |
| `backend/recordings.py` | alta y consulta de sesiones; huecos |
| `backend/main.py` | endpoints de sesión; **exclusión manual/continua en el servidor** |
| `frontend/app.js` | listar por sesión; un solo estado confirmado para los botones |
| `frontend/index.html` | la vista de sesión |
| `tests/` | los diez puntos de tu lista |

---

## Riesgos

**1. La unión silenciosamente corrupta.** Ya demostrada. Es el riesgo
principal y la razón de que la verificación posterior no sea opcional.

**2. El reinicio del Agent a mitad de sesión.** Si se reinicia a las
13:00 de una franja de 09:00 a 17:00, al volver tiene que **retomar la
sesión abierta**, no empezar otra. El índice de la sesión vive en
disco por eso. Sin esto saldrían dos finales para el mismo intervalo.

**3. Disco durante la unión.** Mientras se crea el final conviven los
segmentos y el archivo nuevo: hace falta **el doble del tamaño de la
jornada** libre. Una jornada de 8 h ronda los 170 MB, así que ~340 MB.
Hay que comprobar el espacio **antes** de empezar y, si no hay,
posponer la unión en vez de dejarla a medias. `agent/storage.py` ya
sabe mirar el espacio.

**4. Una sesión que nunca cierra.** Si el equipo se apaga a mitad de
franja y no vuelve hasta el día siguiente, esa sesión queda abierta.
Hay que cerrarla al arrancar si su franja ya pasó, y unir lo que haya.

**5. La duración no es la del intervalo.** Una sesión de 09:00 a 17:00
con una desconexión de una hora dura **siete horas de vídeo**, no
ocho. Es correcto, pero el panel tiene que enseñar las dos cosas:
intervalo programado y tiempo realmente grabado.

---

## Lo que necesito que apruebes

1. **¿Apruebas este plan?** Son seis fases y es el trabajo más grande
   de todos los que llevamos.
2. **¿El orden?** Propongo **publicar la 1.2.0 primero**, verificar en
   el equipo real que el horario se cumple, y solo después construir
   la sesión única. Si construimos encima sin haber comprobado la
   base, no sabremos qué falla.
3. **¿Qué hacer con los segmentos tras unirlos?** Mi propuesta:
   conservarlos y que la retención decida. Borrarlos antes ahorra
   disco y elimina la red de seguridad.
4. **¿El arranque al segundo exacto**, o los 30 segundos de margen
   actuales son aceptables?
5. **¿Unir el histórico?** Mi recomendación: no. Las 496 existentes se
   quedan como están y la sesión única empieza a aplicarse desde que
   se despliegue.
