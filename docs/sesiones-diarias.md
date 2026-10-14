# Sesiones diarias de grabación: diseño

**Esto es un diseño. No hay nada implementado.** Ni la base, ni el
Agent, ni el panel se han tocado.

---

## El problema, medido

| | |
|---|---|
| Corte de segmento | **15 minutos** — `DEFAULT_SEGMENT_SECONDS`, [agent/recorder.py:30](../agent/recorder.py) |
| Quién lo fija | Nadie: el Agent construye `ScreenRecorder()` sin pasar el parámetro ([agent.py:915](../agent/agent.py)) |
| Qué es cada segmento | Un **MP4 propio**, en `recordings\AAAA\MM\DD\`, con **su propia fila** |
| Agrupación | **Ninguna.** La tabla no tiene nada que diga «estos doce son el mismo día» |

496 segmentos ≈ 124 horas. No son duplicados ni fragmentos de un
contenedor: son 496 vídeos independientes, cada uno reproducible por su
cuenta. El panel los lista uno a uno porque **no tiene con qué
agruparlos**.

---

## 1. Cómo agrupar: la fecha la pone el Agent

La agrupación tiene que respetar dos cosas que el servidor **no sabe**:
en qué zona horaria vive el equipo, y si su horario cruza la
medianoche. Un turno de 22:00 a 06:00 es **una jornada**, no dos.

Lo primero que hay que ver es que **esto ya está resuelto**, en el
Agent, en `inicio_de_la_franja()`:

> *"Devolver el inicio (y no solo si o no) permite distinguir una
> franja de la siguiente… el dia que cuenta es el del INICIO."*
> — [agent/scheduler.py:78](../agent/scheduler.py)

Esa función ya devuelve `"2026-10-13T22:00"` para un segmento grabado a
las 02:00 del día 14, porque la franja empezó el 13.

**Así que el Agent manda la fecha de sesión junto con la ficha del
segmento.** No se recalcula en el servidor:

- el Agent conoce su reloj, su zona y su horario; el servidor, ninguna
  de las tres con certeza;
- el servidor almacena lo que recibe y no tiene que adivinar;
- y se reutiliza código ya probado (`test_continua_y_horario`, 58
  comprobaciones) en vez de escribir una segunda versión que se
  desincronice de la primera.

**Fuera de todo horario** —una grabación manual un domingo— no hay
franja. Entonces la fecha de sesión es la **fecha local del equipo**.

### La columna

```sql
ALTER TABLE recordings ADD COLUMN session_date TEXT;
CREATE INDEX IF NOT EXISTS idx_recordings_session
    ON recordings (device_id, session_date);
```

`TEXT` con formato `AAAA-MM-DD`, igual que el resto de fechas del
proyecto. `NULL` significa «histórico, sin calcular».

---

## 2. El listado: una fila por equipo y día

```sql
SELECT
    r.device_id,
    COALESCE(r.session_date, substr(r.started_at, 1, 10)) AS dia,
    COUNT(*)                                   AS segmentos,
    MIN(r.started_at)                          AS desde,
    MAX(r.ended_at)                            AS hasta,
    SUM(r.duration_sec)                        AS segundos_grabados,

    -- Tamaño REAL en el servidor: solo lo archivado
    SUM(CASE WHEN COALESCE(r.storage_state, CASE WHEN r.status='stored'
             THEN 'server_stored' ELSE 'local_only' END) = 'server_stored'
             THEN r.size_bytes ELSE 0 END)     AS bytes_en_servidor,

    -- Lo que ocupa en el equipo
    SUM(r.size_bytes)                          AS bytes_totales,

    SUM(CASE WHEN COALESCE(r.storage_state,'') = 'server_stored'
             OR (r.storage_state IS NULL AND r.status='stored')
             THEN 1 ELSE 0 END)                AS segmentos_archivados

FROM recordings r
WHERE r.status != 'invalid'
GROUP BY r.device_id, dia
ORDER BY dia DESC, r.device_id;
```

El `COALESCE` sobre `session_date` es lo que permite **no migrar nada
de golpe**: las filas históricas caen en su fecha UTC, que para un
horario diurno es la correcta.

### Duración, intervalos y huecos

- **Duración total** = `SUM(duration_sec)`. Es el tiempo **grabado**, no
  el transcurrido: si hubo una desconexión, son distintos, y esa
  diferencia es justo la información útil.
- **Intervalos disponibles** = los `[started_at, ended_at]` de cada
  segmento, en orden.
- **Huecos** = se calculan comparando el `ended_at` de un segmento con
  el `started_at` del siguiente. Por encima de un umbral —**60
  segundos**, porque entre segmentos consecutivos hay siempre un salto
  de milisegundos al cerrar y abrir ffmpeg— es un hueco real y se
  presenta: *«sin grabación de 11:42 a 12:07»*.

El hueco **no se rellena ni se disimula**. Que falte media hora es un
dato, no un defecto de presentación.

---

## 3. El tamaño real: la consulta de hoy está mal

```sql
SELECT COALESCE(SUM(r.size_bytes), 0)
FROM recordings r JOIN devices d ON d.device_id = r.device_id
WHERE d.organization_id = ?
```

[backend/organizations.py:402](../backend/organizations.py) suma
**todas** las grabaciones del catálogo, sin mirar `storage_state`.

Pero el modelo es local-first: el equipo guarda el vídeo y el servidor
recibe solo una ficha; el archivo sube **únicamente cuando alguien lo
archiva a mano**. Así que esa cifra **incluye vídeos que están en el PC
del cliente, no en Oracle**, y el `storage_mb` del plan mide algo que
no es el disco del servidor.

Corrección, respetando el histórico `NULL`:

```sql
SELECT COALESCE(SUM(r.size_bytes), 0)
FROM recordings r
JOIN devices d ON d.device_id = r.device_id
WHERE d.organization_id = ?
  AND (r.storage_state = 'server_stored'
       OR (r.storage_state IS NULL AND r.status = 'stored'))
```

Es la misma regla que ya aplica `storage_state_of()`
([recordings.py:103](../backend/recordings.py)), escrita en SQL.

**Consecuencia que hay que saber antes de aplicarla:** el `storage_mb`
de la organización **bajará**, probablemente mucho. No se habrá borrado
nada; es que ahora medirá lo que dice medir. Y el panel debe enseñar
**las dos cifras por separado** —en el servidor / en los equipos— o el
cambio parecerá una pérdida de datos.

---

## 4. Reproducción encadenada, sin tocar los archivos

Un endpoint nuevo devuelve los segmentos de una sesión, ordenados, con
su URL de reproducción y su estado:

```
GET /api/recordings/session/{device_id}/{fecha}
  → { "segmentos": [ {id, desde, hasta, segundos, reproducible}, ... ],
      "huecos":    [ {desde, hasta} ],
      "total_segundos": 28800,
      "bytes_en_servidor": 1234567 }
```

El reproductor del panel usa **un solo `<video>`** y, al llegar el
evento `ended`, cambia su `src` al siguiente segmento y sigue. Una
barra de progreso propia, sumando las duraciones, da la sensación de
una sola grabación.

**No se concatena nada, no se recodifica nada, no se crea ningún
archivo.** Solo se reproducen en orden los que ya existen.

Un segmento `local_only` **no es reproducible desde el panel** —su
archivo está en el equipo. Aparece en la línea de tiempo marcado como
«solo en el equipo», y el reproductor lo salta avisando. Esa distinción
sale directa de `storage_state_of()`, que ya existe y ya interpreta el
histórico.

### Por qué no unir los archivos

Lo preguntaste expresamente, y la respuesta es que no compensa:

| | segmentos | archivo único |
|---|---|---|
| Si se corrompe | se pierden 15 min | **se pierde el día entero** |
| Coste de unir | ninguno | leer y reescribir ~170 MB por día y equipo |
| Disco durante la operación | — | **el doble**: original y copia conviven |
| Huecos por desconexión | se ven | **se ocultan**: el vídeo miente sobre lo que pasó |
| Reversible | sí | no, salvo conservando el original |

Con `ffmpeg -c copy` se podría unir sin recodificar, y si aun así lo
prefieres se puede diseñar. Pero **segmentos en disco y sesión en la
interfaz** da lo que pides sin ninguno de esos riesgos.

---

## 5. Migración: aditiva y reversible

**No se borra, no se mueve y no se sobrescribe ningún archivo.** La
migración solo escribe en una columna nueva.

```sql
-- 1. La columna y su indice (idempotente)
ALTER TABLE recordings ADD COLUMN session_date TEXT;
CREATE INDEX IF NOT EXISTS idx_recordings_session
    ON recordings (device_id, session_date);

-- 2. Relleno: la fecha de inicio, en la zona del horario del equipo.
--    Donde no hay horario, UTC. Solo filas sin calcular.
UPDATE recordings
   SET session_date = substr(started_at, 1, 10)
 WHERE session_date IS NULL
   AND started_at IS NOT NULL;
```

El relleno en SQL usa la fecha UTC, que es correcta para cualquier
horario diurno. **Los turnos que cruzan medianoche quedan mal
agrupados en el histórico**, y hay que decirlo claro: para arreglarlos
haría falta la zona y el horario que tenía cada equipo *entonces*, y
eso no se guarda. Los segmentos nuevos sí vendrán bien desde el primer
día, porque la fecha la pone el Agent.

Si esto importa para los datos que ya hay, se puede hacer una pasada de
corrección aplicando el horario **actual** del equipo hacia atrás. Es
una aproximación, y como tal habría que marcarla.

**Vuelta atrás:** revertir el código deja la columna y el índice sin
usar. SQLite no se queja de columnas que nadie nombra —ya lo
comprobamos al revertir el despliegue anterior, donde el código de
`f911f4e` leyó y escribió sin problema sobre una base con columnas de
más.

---

## 6. Archivos que habría que tocar

Nada de esto está hecho.

| archivo | qué |
|---|---|
| `backend/database.py` | columna `session_date`, índice, relleno |
| `backend/recordings.py` | aceptar `session_date` en el alta; consulta de sesiones; huecos |
| `backend/organizations.py` | **la suma de tamaño, filtrada por `storage_state`** |
| `backend/main.py` | `GET /api/recordings/sessions`, `GET /api/recordings/session/{id}/{fecha}` |
| `agent/agent.py` | enviar `session_date` en `report_recording_catalog` |
| `agent/scheduler.py` | exponer la fecha de la franja en curso (la lógica ya está) |
| `frontend/app.js` | listado por sesión, línea de tiempo, reproducción encadenada |
| `frontend/index.html` | la vista de sesión |
| `tests/` | agrupación, medianoche, zonas, huecos, encadenado, tamaño real, migración y su reversión |

---

## 7. Riesgos y decisiones pendientes

**1. El Agent viejo no manda `session_date`.** Hasta que Plastika
actualice a 1.2.0, sus segmentos nuevos caerán en el `COALESCE` de UTC.
Funciona, pero los turnos de noche quedarán partidos. **Esto ata este
trabajo a la publicación de la 1.2.0.**

**2. El `storage_mb` bajará al corregir la consulta.** No es pérdida de
datos, pero lo parece. Hay que presentarlo con las dos cifras separadas
y avisarte antes de aplicarlo en producción.

**3. Los huecos del histórico son inseguros.** Un hueco puede ser una
desconexión, un apagado, o que el Agent viejo arrancara fuera de
horario. No se puede distinguir hacia atrás. Propongo mostrarlos como
«sin grabación» sin aventurar el motivo.

**4. El umbral de 60 segundos es una elección.** Lo bastante alto para
no marcar el salto normal entre segmentos, lo bastante bajo para ver
una desconexión real. **Confírmalo o dame otro.**

### Lo que necesito que decidas

1. **¿El tamaño en el panel: una cifra o dos?** Mi recomendación: dos
   —«en el servidor» y «en los equipos»— porque una sola seguirá
   engañando, solo que al revés.
2. **¿Corrijo el histórico de turnos de noche** con el horario actual,
   marcándolo como aproximado, o lo dejo como está?
3. **El umbral de hueco: 60 segundos.**
4. **¿Reduzco el corte de 15 minutos?** No lo tocaría: 15 min es un
   buen equilibrio entre número de archivos y pérdida máxima ante un
   fallo. Cambiarlo no reduce los 496 que ya existen.
