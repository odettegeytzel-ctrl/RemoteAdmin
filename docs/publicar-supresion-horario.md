# Publicar la supresión del horario tras una parada manual

**Nada de esto se ha ejecutado.** Es el procedimiento, con lo
comprobado en local y lo que falta por ver en un equipo real.

---

## Qué arregla

Pulsar **«Detener grabación»** durante un horario activo no detenía
nada, o detenía y el horario la reanudaba al tic siguiente.

La causa resultó concreta: `agent/scheduler.py` ya tenía el
mecanismo —`notify_manual()` anota la franja en curso como excepción
e `intencion()` la respeta— pero **nadie lo llamaba**. Las cinco
llamadas del repositorio estaban todas en pruebas, que por eso
pasaban en verde comprobando una función que el producto nunca
invocaba.

Detrás había un segundo fallo: aunque se llamara, la excepción vivía
solo en memoria y `set_schedule()` la borraba. El servidor reenvía el
horario **en cada conexión**, así que un corte de red de dos segundos
la anulaba.

## Un cambio de comportamiento que hay que anunciar

**«Detener grabación» ahora corta una grabación programada.** Antes
soltaba solo el dueño manual y, con una franja activa, la captura
seguía: el botón no detenía nada.

Una parada originada por el **propio horario** no suprime nada.

---

## Qué se publica

Esta es una etapa distinta de la 2, que ya está desplegada.

| archivo | SHA-256 |
|---|---|
| `backend/database.py` | `d8971d515d7cf11839b77984dd6c5f1ce33d1be33d457e869d346e7b643e7d60` |
| `backend/devices.py` | `d586702151a460b0ad588f10b86b8ced1ef542027bf3d019d4091c109ace37ce` |
| `backend/main.py` | `149594844ff73fd855e4f88b7c52a6e891100318907777dc9f1d17b2a3a51702` |
| `frontend/app.js` | `385702f555db6ddd9f6d672aaf4211a68e9d0774c1ebe44977469433dbd18f13` |
| `agent/scheduler.py` | `c7757de49bccbdfa38e126c941a4b6487000a60ca4704bcb0c17496f10bcb3fc` |
| `agent/agent.py` | `7c39fab3b52486d96d3a881508514a04e59643ca0bea08a315d491c43877b621` |

**Las tres primeras ya no coinciden con las desplegadas en la etapa
2.** Cualquier publicación tiene que medirlas de nuevo.

### El orden importa, y no es el habitual

```
1. backend  (migración + persistencia)   → reinicio del servicio
2. frontend (el rótulo)                   → sin reinicio
3. agent    (es quien suprime)            → paquete firmado
```

**El backend va primero.** Si fuera el Agent, informaría una
supresión que el servidor no sabe guardar: el mensaje
`schedule_suppression:` caería en el `else` y se perdería en
silencio. No rompe nada, pero la supresión no sobreviviría a la
reconexión, que es justo lo que se quiere arreglar.

**Sin el Agent, el arreglo no llega.** El backend y el frontend solos
no cambian nada visible: quien suprime es el Agent. Hasta que no se
publique y se actualicen los equipos, el botón sigue comportándose
como hoy.

---

## La migración

Una sola columna, aditiva, anulable e idempotente:

```sql
ALTER TABLE devices ADD COLUMN schedule_suppressed_slot TEXT
```

Guarda una clave compuesta **`zona|franja|instante`**. La parte
decisiva es la del medio: el identificador de la franja, el mismo que
calcula `inicio_de_la_franja()` en el Agent.

La migración también **traslada el valor** de una columna anterior
llamada `schedule_suppressed_until`, por si alguna base de desarrollo
la tuviera. **Esa columna nunca se desplegó**, así que en producción
no existe y ese camino no se ejecutará. No se borra: `DROP COLUMN`
reescribe la tabla entera y aquí no aporta nada.

### Verificarla

```bash
cd /opt/remoteadmin/RemoteAdmin
sqlite3 remoteadmin.db "PRAGMA table_info(devices);" | grep schedule_suppressed
sqlite3 remoteadmin.db "SELECT COUNT(*) FROM devices;"
sqlite3 remoteadmin.db "PRAGMA integrity_check;"
```

El recuento de equipos debe ser **el mismo que antes**.

---

## Despliegue

Es el mismo esqueleto que la etapa 2 —respaldo probado, subir a
`/tmp`, verificar huellas, parar, `install`, puerta de hashes,
arrancar— descrito en [respaldo-y-reversion.md](respaldo-y-reversion.md).
Lo que cambia:

**Son tres archivos de backend**, no cuatro: `database.py`,
`devices.py` y `main.py`. `recordings.py` y `packaging.py` no se
tocan.

**El frontend es un archivo más**, y no necesita reinicio.

> ⚠ `frontend/app.js` en producción está **modificado a mano** con el
> parche del mouse. Publicar el del repositorio lo reemplaza, y el
> del repositorio **ya lo contiene**. Haz respaldo antes.

**El Agent va aparte**, con su paquete firmado. Ver
[publicar-1.3.0.md](publicar-1.3.0.md).

---

## Reversión

**El código**, restaurando por nombre desde el respaldo:

```bash
cd /opt/remoteadmin/RemoteAdmin
sudo systemctl stop remoteadmin
sudo cp -p "/root/backend-antes-$SELLO/database.py" backend/database.py
sudo cp -p "/root/backend-antes-$SELLO/devices.py"  backend/devices.py
sudo cp -p "/root/backend-antes-$SELLO/main.py"     backend/main.py
sudo sha256sum -c /root/backend-huellas-ANTES.sha256
sudo systemctl start remoteadmin
```

**La base de datos NO se revierte.** La columna es aditiva y anulable;
el código anterior la ignora. Está comprobado en este repositorio que
ningún `INSERT INTO devices` usa posiciones: todos nombran sus
columnas.

**Si se revierte el backend pero no el Agent**, los equipos seguirán
informando `schedule_suppression:`, el servidor no lo entenderá y el
mensaje se descartará. La supresión dejará de sobrevivir a las
reconexiones, pero **nada se rompe**: el bucle de mensajes es una
cadena `if/elif` sin `else`.

---

## Qué está comprobado y qué no

### Comprobado en local

58 comprobaciones en `tests/test_supresion_horario.py`, más la suite
completa. Y once fallos metidos a propósito —incluido el original, no
llamar a `notify_manual`— **los once cazados**.

Cubre: que parar a mano suprime la franja; que no se reanuda dentro
de ella y sí en la siguiente; que sobrevive a reconexión, a reinicio
del Agent y del backend; que reenviar el mismo horario no la borra y
reprogramar sí; que un cambio de **zona del equipo** no la evapora;
que la supresión se aplica **antes** de evaluar; y que las dos
modalidades no se pisan.

### NO comprobado: hace falta un equipo real

Nada de lo anterior toca una pantalla. Queda por ver:

| # | qué | dónde |
|---|---|---|
| 1 | Que «Detener» **detiene de verdad** durante una franja | Panel + equipo |
| 2 | Que no se reanuda sola en los minutos siguientes | Observar |
| 3 | Que tras **reiniciar el Agent** sigue sin reanudarse | Reiniciar la tarea |
| 4 | Que al llegar la **franja siguiente** vuelve a grabar | Esperar |
| 5 | Que una manual puntual sigue funcionando | Panel |
| 6 | Que el rótulo dice «DESACTIVADA (el horario manda)» | Panel |
| 7 | Que las grabaciones históricas y los segmentos siguen | `recordings/` |

**El 3 es el que de verdad decide**: es el que no podía funcionar
antes y el que justifica haber tocado la base de datos.

El guion paso a paso, con horas que anotar y hoja de resultados,
esta en [prueba-real-supresion-horario.md](prueba-real-supresion-horario.md).
Ojo con el 4: la franja siguiente es la de **manana**, porque el
horario solo admite una franja por dia.

## Riesgo conocido

Si el operador para a mano y luego se cambia la **zona del equipo**,
la franja deja de ser comparable. Entonces la supresión se respeta
**24 horas** —lo máximo que puede durar una franja— y después caduca.
Es un tope deliberado: más sería arriesgarse a no grabar nunca;
menos, a reanudar dentro de la franja que se acababa de parar.
