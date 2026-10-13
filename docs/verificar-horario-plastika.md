# Verificar el horario de grabación en producción

La base local **no demuestra** el estado de producción. Aquí el horario
de Plastika aparece con `enabled = 0`, zona `local` y franja
08:00–17:00, que no es lo que se observó en el panel.

Hasta ejecutar esto, la incidencia de «grabaciones fuera de horario»
**no está verificada en producción**.

Todas las consultas son de **solo lectura**. Ninguna modifica nada.

---

## 1. Entrar al servidor

```bash
ssh <usuario>@159.54.156.221
```

## 2. Abrir la base en modo solo lectura

El modo `ro` de SQLite impide cualquier escritura, incluso por error.
Se usa `sudo -u remoteadmin` para no ejecutar nada como root.

```bash
sudo -u remoteadmin sqlite3 \
  "file:/opt/remoteadmin/RemoteAdmin/remoteadmin.db?mode=ro" \
  -header -column
```

## 3. El horario de cada equipo

```sql
SELECT
    s.device_id,
    d.hostname,
    s.enabled,
    s.start_time,
    s.end_time,
    s.days,
    s.timezone,
    s.updated_at
FROM recording_schedule s
LEFT JOIN devices d ON d.device_id = s.device_id
ORDER BY d.hostname;
```

Qué mirar:

- **`enabled`** — `1` es activo. Si es `0`, el horario no gobierna nada
  y la grabación continua manda sola.
- **`days`** — lunes es `0` y domingo `6`. «Lunes a viernes» es
  `0,1,2,3,4`.
- **`timezone`** — `local` significa que el Agent usa el reloj del
  equipo administrado, no una zona común. Con equipos en husos
  distintos, el horario no es el mismo para todos.
- **`start_time` / `end_time`** — si el fin es menor que el inicio, la
  franja cruza la medianoche y el día que cuenta es el del inicio.

## 4. Si la grabación continua está activada

```sql
SELECT
    device_id,
    hostname,
    continuous_recording,
    status,
    last_seen
FROM devices
ORDER BY hostname;
```

`continuous_recording = 1` **con `enabled = 0`** en el horario es
exactamente la combinación que graba a todas horas: antes de la
corrección no había nada que la limitara.

## 5. Qué grabaciones se hicieron fuera de franja

Esta es la prueba directa. Lista las grabaciones de los últimos treinta
días cuya hora de inicio cae fuera de 09:00–17:00 o en fin de semana.

```sql
SELECT
    d.hostname,
    r.started_at,
    CAST(strftime('%w', r.started_at) AS INTEGER) AS dia_semana,
    strftime('%H:%M', r.started_at)               AS hora,
    r.duration_sec,
    r.size_bytes
FROM recordings r
LEFT JOIN devices d ON d.device_id = r.device_id
WHERE r.started_at >= datetime('now', '-30 days')
  AND (
        strftime('%w', r.started_at) IN ('0', '6')
     OR strftime('%H:%M', r.started_at) <  '09:00'
     OR strftime('%H:%M', r.started_at) >= '17:00'
      )
ORDER BY r.started_at DESC;
```

Dos avisos sobre esta consulta:

- `strftime('%w', ...)` cuenta el **domingo como 0**, al revés que la
  tabla `recording_schedule`, donde el lunes es 0. En la consulta,
  sábado y domingo son `'6'` y `'0'`.
- `started_at` se guarda en UTC. Si la zona del horario no es UTC, hay
  que ajustar antes de concluir que una grabación cayó fuera. Para
  México sería `datetime(r.started_at, '-6 hours')`.

## 6. Cuántas, por franja horaria

Para ver de un vistazo si el patrón es continuo o puntual:

```sql
SELECT
    strftime('%H', r.started_at) AS hora_utc,
    COUNT(*)                     AS grabaciones
FROM recordings r
WHERE r.started_at >= datetime('now', '-30 days')
GROUP BY hora_utc
ORDER BY hora_utc;
```

Una distribución repartida por las veinticuatro horas confirma el
diagnóstico. Una concentrada entre las 9 y las 17 (ajustada la zona)
significaría que el horario sí se estaba cumpliendo y que la causa es
otra.

## 7. El estado que reporta el Agent

Sin tocar nada, desde el propio servidor:

```bash
sudo journalctl -u remoteadmin.service --since "7 days ago" \
  | grep -iE "horario|continua|grabacion" | tail -50
```

Las líneas `[horario]` dicen qué decidió el planificador y cuándo.

---

## Qué hacer con el resultado

- **`enabled = 0` y `continuous_recording = 1`** → el diagnóstico se
  confirma: la grabación continua no tenía nada que la limitara. La
  corrección ya aplicada lo resuelve, pero **hay que desplegarla** para
  que surta efecto.
- **`enabled = 1` y aun así hay grabaciones fuera de franja** → la
  causa es otra y hay que seguir investigando. Conviene revisar la zona
  horaria y la hora del propio equipo administrado.
- **Sin grabaciones fuera de franja** → el problema no está donde se
  creía, y lo observado en el panel puede ser un efecto de la
  presentación, que es lo que se corrigió en la Fase 1A.

Nada de lo anterior modifica la base, el servicio ni las grabaciones.
