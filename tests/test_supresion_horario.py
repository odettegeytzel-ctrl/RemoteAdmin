r"""
Parar a mano durante una franja no debe reanudarse sola.

    .venv\Scripts\python tests/test_supresion_horario.py

El fallo
--------

En el panel, pulsar "Detener grabacion" durante un horario activo no
detenia nada, o detenia y el horario la reanudaba al tic siguiente.

La causa resulto ser concreta: agent/scheduler.py YA tenia el
mecanismo -notify_manual() anota la franja en curso como excepcion e
intencion() la respeta- pero NADIE lo llamaba. Las cinco llamadas que
existian en el repositorio estaban todas en pruebas, que por eso
pasaban en verde comprobando una funcion que el producto nunca
invocaba.

Y habia un segundo fallo detras: aunque se llamara, la excepcion
vivia solo en memoria del Agent, y set_schedule() la borraba. El
servidor reenvia el horario CADA VEZ que el Agent conecta, asi que
un corte de red de dos segundos anulaba la parada manual.

Que se comprueba
----------------

  - que parar a mano suprime la franja en curso;
  - que el horario NO reanuda dentro de esa franja;
  - que SI vuelve a mandar en la siguiente;
  - que la supresion sobrevive a reconexion, a reinicio del Agent y
    a reinicio del backend, porque se guarda en la base;
  - que reenviar el mismo horario no la borra, pero reprogramar si;
  - que la grabacion manual puntual sigue siendo independiente;
  - y que el camino REAL del Agent llama a notify_manual, que es lo
    que no se comprobaba.
"""

import io
import os
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-supresion")

from backend import database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="supresion-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")

from backend import devices as devices_mod  # noqa: E402

import scheduler  # noqa: E402
from scheduler import RecordingScheduler  # noqa: E402


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


LABORAL = {
    "enabled": True,
    "days": [0, 1, 2, 3, 4],
    "start_time": "08:00",
    "end_time": "17:00",
    "timezone": "UTC",
}


def momento(dia, hora, minuto=0):
    """Un instante de la semana del 2026-10-26, que es lunes."""

    from datetime import datetime, timedelta, timezone

    lunes = datetime(2026, 10, 26, hora, minuto, tzinfo=timezone.utc)

    return lunes + timedelta(days=dia)


# ==================================================================
# 1. La causa raiz: el camino real llama a notify_manual
# ==================================================================
#
# Esta es la comprobacion que faltaba. Las pruebas anteriores
# llamaban a notify_manual a mano y por eso no detectaron que
# agent.py no la invocaba nunca.

AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

comprobar(
    "agent.py llama a notify_manual (antes NADIE lo hacia)",
    "recording_scheduler.notify_manual(" in AGENTE
)

comprobar(
    "y lo hace al soltar, no en cualquier sitio",
    "supresion_del_operador" in AGENTE
    and AGENTE.index("supresion_del_operador")
    < AGENTE.index("recording_scheduler.notify_manual(")
)

comprobar(
    "la parada del OPERADOR suelta tambien el horario",
    "if duenos.soltar_el_horario():" in AGENTE
)

comprobar(
    "una parada del propio horario NO suprime nada",
    "origen == owners.MANUAL" in AGENTE
)

comprobar(
    "el Agent informa la supresion al servidor",
    "def report_schedule_suppression(" in AGENTE
    and '"schedule_suppression:"' in AGENTE
)

# Que la funcion EXISTA no basta: tiene que llamarse desde el camino
# de la parada. Sin esto, la supresion se quedaria en memoria y una
# reconexion la perderia, que era el segundo fallo.
# Se ancla a notify_manual, que es donde se decide suprimir: hay dos
# bloques 'if supresion_del_operador' y el primero es el que suelta
# el horario, no el que informa.
_desde = AGENTE.index("recording_scheduler.notify_manual(False)")
_bloque = AGENTE[_desde:_desde + 400]

comprobar(
    "y la llama al suprimir, no solo la define",
    "report_schedule_suppression()" in _bloque,
    _bloque[:140].replace(chr(10), " | ")
)

comprobar(
    "y la recibe al conectar",
    'message.startswith("schedule_suppression:")' in AGENTE
)


# ==================================================================
# 2. Parar durante la franja no reanuda; la siguiente si
# ==================================================================

p = RecordingScheduler(LABORAL)

p.decide(momento(0, 8, 0))
p.notify_state(True)

comprobar(
    "dentro de la franja, el horario quiere grabar",
    p.intencion(momento(0, 9, 0))[0] is True
)

p.notify_manual(False, momento(0, 10, 0))

comprobar(
    "tras parar a mano, el horario YA NO quiere",
    p.intencion(momento(0, 10, 1))[0] is False
)

comprobar(
    "y sigue sin querer el resto de la franja",
    p.intencion(momento(0, 16, 59))[0] is False
)

comprobar(
    "al dia siguiente, dentro de su franja, vuelve a mandar",
    p.intencion(momento(1, 9, 0))[0] is True,
    str(p.intencion(momento(1, 9, 0)))
)


# ==================================================================
# 3. La clave que viaja al servidor
# ==================================================================

q = RecordingScheduler(LABORAL)
q.decide(momento(0, 8, 0))
q.notify_state(True)
q.notify_manual(False, momento(0, 10, 0))

clave = q.clave_suprimida()

comprobar(
    "la supresion se puede exportar como una cadena",
    isinstance(clave, str) and clave,
    str(clave)
)

# zona|franja|instante. La parte del medio identifica la franja; es
# lo que caduca sola al empezar la siguiente.
comprobar(
    "lleva las tres partes: zona, franja e instante",
    len(clave.split("|")) == 3,
    clave
)

comprobar(
    "y la del medio es el identificador de la franja",
    clave.split("|")[1].endswith("T08:00"),
    clave
)


# ==================================================================
# 4. Reinicio del Agent: con la clave, no reanuda
# ==================================================================

recien = RecordingScheduler(LABORAL)

comprobar(
    "un Agent recien arrancado SI reanudaria (ese era el fallo)",
    recien.intencion(momento(0, 11, 0))[0] is True
)

restaurado = RecordingScheduler(LABORAL)
restaurado.aplicar_supresion(clave)

comprobar(
    "con la supresion restaurada, NO reanuda",
    restaurado.intencion(momento(0, 11, 0))[0] is False
)

comprobar(
    "y en la franja siguiente vuelve a mandar",
    restaurado.intencion(momento(1, 9, 0))[0] is True
)


# ==================================================================
# 5. Reconexion: el mismo horario no borra la supresion
# ==================================================================

r = RecordingScheduler(LABORAL)
r.decide(momento(0, 8, 0))
r.notify_state(True)
r.notify_manual(False, momento(0, 10, 0))

# Lo que hace el servidor en CADA conexion
r.set_schedule(LABORAL)

comprobar(
    "reenviar el MISMO horario conserva la supresion",
    r.intencion(momento(0, 11, 0))[0] is False
)

otro = dict(LABORAL)
otro["end_time"] = "18:00"

r.set_schedule(otro)

comprobar(
    "cambiar el horario de verdad la retira",
    r.intencion(momento(0, 11, 0))[0] is True
)


# ==================================================================
# 6. Persistencia: sobrevive al reinicio del backend
# ==================================================================

database.init_db()

columnas = {
    c["name"]
    for c in database.get_connection().execute("PRAGMA table_info(devices)")
}

comprobar(
    "la migracion crea schedule_suppressed_slot",
    "schedule_suppressed_slot" in columnas,
    ", ".join(sorted(columnas))[:120]
)

# Un equipo que ya existia ANTES de la migracion: ni se pierde ni se
# le cambia nada, y la columna nueva le nace vacia.
previa = os.path.join(CARPETA, "previa.db")

import sqlite3

anterior = sqlite3.connect(previa)
anterior.execute(
    "CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "device_id TEXT UNIQUE NOT NULL, hostname TEXT NOT NULL)"
)
anterior.execute(
    "INSERT INTO devices (device_id, hostname) VALUES (?, ?)",
    ("VIEJO-1", "EQUIPO-DE-ANTES")
)
anterior.commit()
anterior.close()

_guardada = database.DATABASE_PATH
database.DATABASE_PATH = previa
database.init_db()

con = database.get_connection()
fila = con.execute(
    "SELECT * FROM devices WHERE device_id = ?", ("VIEJO-1",)
).fetchone()
con.close()

database.DATABASE_PATH = _guardada

comprobar(
    "un equipo anterior a la migracion sobrevive",
    fila is not None and fila["hostname"] == "EQUIPO-DE-ANTES",
    "" if fila is None else dict(fila).get("hostname")
)

comprobar(
    "y la columna nueva le nace vacia, sin tocar sus datos",
    fila is not None and fila["schedule_suppressed_slot"] is None
)

# init_db dos veces no debe fallar ni duplicar nada
database.init_db()

columnas2 = {
    c["name"]
    for c in database.get_connection().execute("PRAGMA table_info(devices)")
}

comprobar(
    "la migracion es idempotente",
    columnas == columnas2
)

conexion = database.get_connection()
conexion.execute(
    "INSERT INTO devices (device_id, hostname) VALUES (?, ?)",
    ("EQUIPO-SUP", "PRUEBA")
)
conexion.commit()
conexion.close()

comprobar(
    "un equipo nuevo nace sin supresion",
    devices_mod.get_schedule_suppression("EQUIPO-SUP") is None
)

devices_mod.set_schedule_suppression("EQUIPO-SUP", clave)

comprobar(
    "se guarda",
    devices_mod.get_schedule_suppression("EQUIPO-SUP") == clave
)

# "Reiniciar el backend" es abrir la base de nuevo: el valor sigue
comprobar(
    "y sobrevive a reabrir la base (reinicio del backend)",
    devices_mod.get_schedule_suppression("EQUIPO-SUP") == clave
)

devices_mod.set_schedule_suppression("EQUIPO-SUP", None)

comprobar(
    "retirarla con None funciona",
    devices_mod.get_schedule_suppression("EQUIPO-SUP") is None
)

comprobar(
    "un equipo que no existe no revienta",
    devices_mod.get_schedule_suppression("NO-EXISTE") is None
)


# ==================================================================
# 6a-bis. Migracion desde el nombre antiguo de la columna
# ==================================================================
#
# La columna se llamo schedule_suppressed_until mientras se creia que
# guardaria un instante. Nunca se desplego -no esta en HEAD ni en
# ninguna base de este equipo- pero si alguna base de desarrollo la
# tuviera, el valor debe trasladarse en vez de perderse.

vieja = os.path.join(CARPETA, "con-nombre-viejo.db")

import sqlite3 as _sq

ant = _sq.connect(vieja)
ant.execute(
    "CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "device_id TEXT UNIQUE NOT NULL, hostname TEXT NOT NULL, "
    "schedule_suppressed_until TEXT)"
)
ant.execute(
    "INSERT INTO devices (device_id, hostname, schedule_suppressed_until) "
    "VALUES (?, ?, ?)",
    ("CON-SUPRESION", "EQUIPO", "local|2026-10-26T08:00|2026-10-26T10:00:00+00:00")
)
ant.execute(
    "INSERT INTO devices (device_id, hostname) VALUES (?, ?)",
    ("SIN-SUPRESION", "OTRO")
)
ant.commit()
ant.close()

_g = database.DATABASE_PATH
database.DATABASE_PATH = vieja
database.init_db()

con = database.get_connection()
cols = {c["name"] for c in con.execute("PRAGMA table_info(devices)")}
con_sup = con.execute(
    "SELECT * FROM devices WHERE device_id = ?", ("CON-SUPRESION",)
).fetchone()
sin_sup = con.execute(
    "SELECT * FROM devices WHERE device_id = ?", ("SIN-SUPRESION",)
).fetchone()
con.close()

database.DATABASE_PATH = _g

comprobar(
    "la migracion crea la columna nueva junto a la vieja",
    "schedule_suppressed_slot" in cols and "schedule_suppressed_until" in cols
)

comprobar(
    "el valor del nombre antiguo se traslada, no se pierde",
    con_sup["schedule_suppressed_slot"]
    == "local|2026-10-26T08:00|2026-10-26T10:00:00+00:00",
    str(con_sup["schedule_suppressed_slot"])
)

comprobar(
    "la columna vieja NO se borra (no se reescribe la tabla)",
    con_sup["schedule_suppressed_until"] is not None
)

comprobar(
    "un equipo sin supresion sigue sin ella",
    sin_sup["schedule_suppressed_slot"] is None
)

comprobar(
    "y ningun equipo se perdio",
    con_sup["hostname"] == "EQUIPO" and sin_sup["hostname"] == "OTRO"
)


# ==================================================================
# 6b. Cambia la zona DEL EQUIPO: la supresion no se evapora
# ==================================================================
#
# Con timezone "local", si cambia la zona del equipo nadie llama a
# set_schedule: el identificador de franja que calcula el Agent deja
# de coincidir con el guardado y la grabacion que el operador acababa
# de parar se reanudaba sola.
#
# No se puede comparar la franja, asi que se respeta la supresion un
# tiempo acotado (24 h, lo maximo que dura una franja).

from datetime import datetime, timedelta, timezone as _tz

LOCAL = dict(LABORAL)
LOCAL["timezone"] = "local"

z = RecordingScheduler(LOCAL)
z.notify_manual(False, momento(0, 10, 0))

clave_local = z.clave_suprimida()

comprobar(
    "la clave anota la zona con que se calculo",
    clave_local.split("|")[0] == "local",
    clave_local
)

# El equipo cambia de zona: mismo horario salvo la zona, y SIN pasar
# por set_schedule (es lo que distingue este caso).
z.horario = dict(LOCAL, timezone="America/Mexico_City")

comprobar(
    "con la zona cambiada, la supresion NO se evapora",
    z.intencion(momento(0, 11, 0))[0] is False
)

# Pasadas mas de 24 h deja de bloquear: no se queda suprimida para
# siempre.
z2 = RecordingScheduler(LOCAL)
z2.notify_manual(False, momento(0, 10, 0))
z2._tomada_en = momento(0, 10, 0) - timedelta(hours=25)
z2.horario = dict(LOCAL, timezone="America/Mexico_City")

comprobar(
    "pero caduca pasado el tope de 24 h",
    z2.intencion(momento(0, 11, 0))[0] is True
)

# Y el mismo caso por el otro camino: decide() tenia su propia copia
# de la comprobacion, asi que el arreglo debia valer para los dos.
z3 = RecordingScheduler(LOCAL)
z3.decide(momento(0, 8, 0))
z3.notify_state(True)
z3.notify_manual(False, momento(0, 10, 0))
z3.horario = dict(LOCAL, timezone="America/Mexico_City")

comprobar(
    "decide() respeta la supresion igual que intencion()",
    z3.decide(momento(0, 11, 0)) is None,
    str(z3.decide(momento(0, 11, 0)))
)

comprobar(
    "la comprobacion vive en UN solo sitio",
    io.open(RAIZ / "agent" / "scheduler.py", encoding="utf-8").read()
    .count("def _supresion_bloquea(") == 1
)


# ==================================================================
# 6c. La supresion se aplica ANTES de evaluar la decision
# ==================================================================
#
# El hueco: apply_schedule evalua nada mas recibir el horario. Con la
# supresion en un mensaje APARTE, entre uno y otro el horario
# arrancaba la grabacion que el operador habia parado.

comprobar(
    "el servidor manda horario y supresion en UN mensaje",
    "_suppressed_slot" in io.open(
        RAIZ / "backend" / "main.py", encoding="utf-8").read()
)

comprobar(
    "apply_schedule acepta la supresion",
    "def apply_schedule(horario, supresion=" in AGENTE
)

# El orden dentro de apply_schedule: aplicar antes de evaluar.
_ap = AGENTE[AGENTE.index("def apply_schedule("):]
_ap = _ap[:_ap.index("def _aplicar_decision_del_horario")]

comprobar(
    "y la aplica ANTES de evaluar la decision",
    _ap.index("aplicar_supresion") < _ap.index("_aplicar_decision_del_horario()")
)

comprobar(
    "una clave ausente no se confunde con una vacia",
    "_SIN_CLAVE" in AGENTE
)

# Comportamiento: el ciclo completo de una reconexion
LAB = dict(LABORAL)

parado = RecordingScheduler(LAB)
parado.decide(momento(0, 8, 0))
parado.notify_state(True)
parado.notify_manual(False, momento(0, 10, 0))

guardada = parado.clave_suprimida()

# Agent recien arrancado: horario vacio. El que llega SIEMPRE cambia.
nuevo = RecordingScheduler({})
nuevo.set_schedule(LAB)
nuevo.aplicar_supresion(guardada)

comprobar(
    "tras reconectar con horario+supresion, NO arranca",
    nuevo.intencion(momento(0, 11, 0))[0] is False
)

# Mensajes repetidos: el servidor reenvia en cada reconexion
for _ in range(3):
    nuevo.set_schedule(LAB)
    nuevo.aplicar_supresion(guardada)

comprobar(
    "repetir el mensaje tres veces no la pierde",
    nuevo.intencion(momento(0, 11, 0))[0] is False
)

comprobar(
    "y en la franja siguiente vuelve a mandar",
    nuevo.intencion(momento(1, 9, 0))[0] is True
)

# Cambio de horario: retira la supresion
distinto = dict(LAB, end_time="18:00")

nuevo.set_schedule(distinto)

comprobar(
    "cambiar el horario de verdad la retira",
    nuevo.intencion(momento(0, 11, 0))[0] is True
)

# Y el servidor retira la supresion al reprogramar desde el panel
comprobar(
    "reprogramar desde el panel la retira en el servidor",
    "set_schedule_suppression(device_id, None)" in io.open(
        RAIZ / "backend" / "main.py", encoding="utf-8").read()
)


# ==================================================================
# 6d. Detener una modalidad no rompe la otra
# ==================================================================

mixto = RecordingScheduler(LABORAL)
mixto.decide(momento(0, 8, 0))
mixto.notify_state(True)

# Parar el horario a mano no impide una manual despues
mixto.notify_manual(False, momento(0, 10, 0))

comprobar(
    "tras parar, el horario no reclama",
    mixto.intencion(momento(0, 11, 0))[0] is False
)

mixto.notify_manual(True, momento(0, 11, 0))

comprobar(
    "pero se puede arrancar una manual dentro de la misma franja",
    mixto._grabando is True
)

comprobar(
    "y el horario sigue sin reclamar en esa franja",
    mixto.intencion(momento(0, 12, 0))[0] is False
)

comprobar(
    "la fuente dice que la sostiene la manual",
    mixto.fuente(momento(0, 12, 0)) == "manual",
    str(mixto.fuente(momento(0, 12, 0)))
)


# ==================================================================
# 7. La manual puntual sigue siendo independiente
# ==================================================================

m = RecordingScheduler({"enabled": False})

comprobar(
    "sin horario, el horario no quiere nada",
    m.intencion(momento(0, 10, 0))[0] is False
)

m.notify_manual(True, momento(0, 10, 0))

comprobar(
    "arrancar a mano sin horario deja la grabacion en marcha",
    m._grabando is True
)

comprobar(
    "y el horario sigue sin reclamar nada",
    m.intencion(momento(0, 11, 0))[0] is False
)

# Con horario activo, una manual NO impide que el horario tome su
# turno al empezar la franja: es lo que arreglo intencion() en su dia
# y no se debe romper.
mh = RecordingScheduler(LABORAL)
mh.notify_manual(True, momento(6, 12, 0))      # domingo, fuera de franja

comprobar(
    "una manual fuera de franja no anula el lunes",
    mh.intencion(momento(0, 9, 0))[0] is True,
    str(mh.intencion(momento(0, 9, 0)))
)


# ==================================================================
# 8. El panel: el rotulo distingue interruptor de grabacion real
# ==================================================================

APP = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

comprobar(
    "con horario activo, DESACTIVADA se matiza",
    '"DESACTIVADA (el horario manda)"' in APP
)

comprobar(
    "sin horario sigue diciendo DESACTIVADA a secas",
    '"DESACTIVADA"' in APP
)

comprobar(
    "el indicador de grabacion real no se toco",
    'programada: "Grabando (programada)"' in APP
)


# ------------------------------------------------------------------

print()
print("=" * 66)
print("SUPRESION DEL HORARIO TRAS UNA PARADA MANUAL")
print("=" * 66)
print()

fallos = 0

for nombre, bien, detalle in resultados:

    print("  %s  %s" % ("OK   " if bien else "FALLA", nombre))

    if not bien:
        fallos += 1
        if detalle:
            print("          %s" % detalle)

print()
print("%d/%d comprobaciones" % (len(resultados) - fallos, len(resultados)))

# Se aparta DATABASE_PATH para no dejarlo apuntando a un temporal si
# otra suite corre despues en el mismo proceso.
database.DATABASE_PATH = os.path.join(
    tempfile.mkdtemp(prefix="apartado-"), "vacia.db"
)

sys.exit(1 if fallos else 0)
