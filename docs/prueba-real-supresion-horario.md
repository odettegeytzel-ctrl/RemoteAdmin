# Probar en un equipo real que la parada manual aguanta

**Nada de esto se ha ejecutado.** Es el guion de la prueba que las
pruebas automaticas no pueden hacer: no hay pantalla que capturar ni
servicio que reiniciar.

Requisito: el codigo de [publicar-supresion-horario.md](publicar-supresion-horario.md)
ya publicado, en este orden —backend, frontend, Agent—. Sin el Agent
nuevo esta prueba falla por diseno, y eso no seria un fallo del
arreglo.

---

## Lo que se quiere demostrar

Que pulsar **«Detener grabacion»** dentro de una franja activa detiene
de verdad, y que **sigue detenido despues de reiniciar el Agent**. Eso
ultimo es lo que no podia funcionar antes y lo unico que justifica
haber tocado la base de datos.

Y lo contrario: que **la franja siguiente vuelve a grabar sola**. Una
supresion que no caduca seria peor que el fallo original.

---

## Preparacion

Hace falta **un equipo real** con el Agent instalado, y el panel
abierto en otra maquina.

| dato | donde |
|---|---|
| Equipo de prueba | el que sea, **no** uno con grabaciones que importen |
| Registro del Agent | `C:\ProgramData\RemoteAdmin\logs\agent.log` |
| Tarea que lo arranca | `RemoteAdminAgent` (programador de tareas) |
| Panel | pestana del equipo, bloque de grabacion |

Deja una ventana de PowerShell **en el equipo** siguiendo el registro:

```powershell
Get-Content "$env:ProgramData\RemoteAdmin\logs\agent.log" -Wait -Tail 20
```

Anota la hora de cada paso. Sin horas no se puede distinguir «no se
reanudo» de «todavia no le ha tocado».

### Un horario que sirva para probar hoy

El horario tiene **una sola franja**: un `start_time`, un `end_time` y
los dias de la semana. No se pueden encadenar dos franjas en un mismo
dia, y eso condiciona la prueba.

Programa una franja **corta**, no la de ocho horas:

```
de  HH:00  a  HH:40      dias: hoy y manana
```

Cuarenta minutos bastan para los pasos 1 a 4, que son los que prueban
el arreglo. Guardalo desde el panel.

> **La caducidad no se puede comprobar el mismo dia.** El
> identificador de la franja lleva la fecha, asi que la franja
> siguiente es la de **manana**. No hay atajo: reprogramar retiraria
> la supresion y no probaria nada. El paso 5 queda para el dia
> siguiente.

> Guardar el horario **retira cualquier supresion anterior**: es una
> decision expresa del operador. Por eso se programa **antes** de
> empezar, nunca a mitad de la prueba.

---

## El recorrido

### 1 · Que la franja arranca sola

Espera a que entre la franja A.

- Panel: **GRABANDO (PROGRAMADA)**
- Registro: el horario toma la captura

Si no arranca, para aqui: el problema es anterior a este cambio.

### 2 · Que «Detener grabacion» detiene de verdad

A mitad de la franja A, pulsa **Detener grabacion** en el panel.

- Panel deja de decir GRABANDO, **en segundos**
- Registro: `suelta manual` y los duenos quedan en `nadie`
- Y el rotulo del interruptor continuo dice
  **«DESACTIVADA (el horario manda)»**, no «DESACTIVADA» a secas

**Esto ya fallaba antes.** Si sigue grabando, el Agent nuevo no esta
puesto.

### 3 · Que no se reanuda sola

**Espera tres minutos sin tocar nada.** El programador evalua cada
pocos segundos, asi que tres minutos son muchas oportunidades de
reanudar.

- Panel: sigue sin grabar
- Registro: **ninguna** linea de arranque por horario

### 4 · Que sobrevive al reinicio del Agent — el paso que decide

Todavia dentro de la franja A:

```powershell
Stop-ScheduledTask  -TaskName RemoteAdminAgent
Start-ScheduledTask -TaskName RemoteAdminAgent
```

El Agent arranca de cero: no recuerda nada. Si la supresion aguanta,
es porque **el servidor se la devolvio** al conectar.

Lo que debe verse en el registro, en este orden:

1. conexion al servidor
2. `[horario] Programacion recibida:` con las dos franjas
3. **y la captura NO arranca**

Espera otros **dos minutos**. Si en algun momento empieza a grabar, el
arreglo no funciona: antalo con la hora y el fragmento del registro.

> El horario y la supresion viajan en el **mismo mensaje**. Si llegaran
> en dos, entre uno y otro se veria un arranque y una parada
> inmediata. Ver eso tambien es un fallo.

### 5 · Que una grabacion manual sigue funcionando

Todavia dentro de la franja, con la supresion puesta, pulsa **Iniciar
grabacion**.

- Arranca, y el panel dice **MANUAL**, no PROGRAMADA

Suprimir el horario no debe impedir grabar a mano: son dos modalidades
independientes.

### 6 · Que no se perdio nada

En el servidor, **solo lectura**:

```bash
sqlite3 remoteadmin.db "SELECT COUNT(*) FROM recordings;"
sqlite3 remoteadmin.db "PRAGMA integrity_check;"
```

El recuento debe ser **mayor o igual** que antes de empezar. Si bajo,
para todo.

---

### 7 · AL DIA SIGUIENTE: que la franja vuelve a grabar

**Este paso es de manana, y no es opcional.**

Sin tocar nada —sin reprogramar, sin reiniciar—, cuando entre la
franja del dia siguiente:

- Panel: **GRABANDO (PROGRAMADA)**, sin que nadie pulse nada

La supresion caduca sola porque el identificador lleva la fecha. **Si
manana no graba, el arreglo es peor que el fallo original**: habria
dejado el equipo sin grabar indefinidamente, y hay que revertir.

---

## Mirar la supresion en el servidor

Util para distinguir «el Agent no la guardo» de «el Agent no la
recibio». **Solo SELECT**:

```bash
sqlite3 remoteadmin.db \
  "SELECT device_id, schedule_suppressed_slot FROM devices
   WHERE schedule_suppressed_slot IS NOT NULL;"
```

- Despues del paso 2 debe aparecer una clave `zona|franja|instante`
- Al dia siguiente puede **seguir ahi**: caduca por comparacion, no
  por borrado. Que la fila conserve la clave vieja no es un fallo
  mientras el equipo este grabando.

---

## Si algo sale mal

No hace falta revertir la base de datos. Basta con volver a guardar el
horario desde el panel: eso retira la supresion de ese equipo.

Para dejarlo como estaba del todo, ver la seccion de reversion de
[publicar-supresion-horario.md](publicar-supresion-horario.md).

---

## Hoja de resultados

| paso | que | hora | resultado |
|---|---|---|---|
| 1 | La franja A arranca sola | | |
| 2 | Detener detiene de verdad | | |
| 3 | No se reanuda en 3 min | | |
| 4 | **Aguanta el reinicio del Agent** | | |
| 5 | La manual sigue funcionando | | |
| 6 | No falta ninguna grabacion | | |
| 7 | **Manana: la franja vuelve a grabar** | | |

Un paso sin hora anotada **no cuenta como probado**.
