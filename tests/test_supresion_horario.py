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
from scheduler import RecordingScheduler as _Planificador  # noqa: E402


def RecordingScheduler(horario=None):
    """
    Un planificador con el interruptor general encendido.

    La grabacion continua paso a ser EL INTERRUPTOR -si se graba- y
    el horario solo SUS HORAS -cuando-. Antes el horario mandaba por
    encima: con horario puesto se grababa aunque la continua
    estuviera apagada.

    El cambio es deliberado: hacia falta poder parar la grabacion de
    un equipo a media mañana sin borrarle el horario, y que al
    volver a encenderla siguiera cumpliendolo desde ese punto.

    Este archivo prueba la SUPRESION manual, que es otra cosa, asi
    que parte del interruptor encendido. Que un planificador con el
    interruptor apagado no grabe se comprueba en
    test_interruptor_general.
    """

    planificador = _Planificador(horario)
    planificador.set_continuous(True)

    return planificador


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

# Un horario que SIEMPRE esta en franja.
#
# La seccion 9 entra por el camino real del Agent, y ese camino
# -_aplicar_decision_del_horario()- consulta el RELOJ DE VERDAD:
# no admite un instante de prueba. Con un horario de 8 a 17 de
# lunes a viernes, esas comprobaciones salian verdes por la
# mañana y rojas por la tarde, que es peor que no tenerlas.
#
# Los siete dias y el dia entero: asi la franja esta activa se
# ejecute cuando se ejecute. Lo que ahi se prueba es el reparto
# entre la jornada y el trozo manual, no a que hora empieza.
SIEMPRE = {
    "enabled": True,
    "days": [0, 1, 2, 3, 4, 5, 6],
    "start_time": "00:00",
    "end_time": "23:59",
    "timezone": "UTC",
}


def momento(dia, hora, minuto=0):
    """Un instante de la semana del 2026-10-26, que es lunes."""

    from datetime import datetime, timedelta, timezone

    lunes = datetime(2026, 10, 26, hora, minuto, tzinfo=timezone.utc)

    return lunes + timedelta(days=dia)


# ==================================================================
# 1. El boton manual ya NO toca el horario
# ==================================================================
#
# Esta seccion decia lo contrario, y con razon en su momento:
# "Detener grabacion" no detenia nada, asi que se le hizo soltar
# tambien el horario y anotar la franja como suprimida.
#
# El requisito cambio. El boton manual es para capturar un trozo
# concreto -un error que enseñar, una gestion que documentar- y la
# grabacion del dia se para con su propio interruptor, la grabacion
# continua. Mezclarlos tenia un efecto que nadie esperaba: grabar
# diez segundos a mano dejaba al equipo sin grabar la jornada.
#
# La supresion sigue existiendo en el planificador y se sigue
# probando de la seccion 2 en adelante, pero el producto ya no la
# dispara desde la parada manual.

AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

comprobar(
    "parar a mano NO anota ninguna supresion",
    "recording_scheduler.notify_manual(False)" not in AGENTE
)

comprobar(
    "ni arrastra al horario",
    "supresion_del_operador" not in AGENTE
)

_parada = AGENTE.split("def stop_screen_recording(", 1)[1][:2600]

comprobar(
    "la parada manual suelta su dueno y nada mas",
    "hay_que_parar = duenos.soltar(origen)" in _parada
)

comprobar(
    "y una parada del horario sigue soltando el horario",
    "duenos.soltar_el_horario()" in _parada
)

comprobar(
    "el estado real se informa igual",
    "recording_scheduler.notify_state(" in _parada
)

# El camino que SI queda: el interruptor general. Apagarlo para la
# jornada; encenderlo la reanuda en las horas que tenga puestas.
comprobar(
    "el interruptor general sigue llegando al planificador",
    "recording_scheduler.set_continuous(" in AGENTE
)

comprobar(
    "la supresion se sigue recibiendo del servidor",
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

# El instante se elige DENTRO de la franja en la zona NUEVA.
#
# Mexico va seis horas por detras de UTC, asi que las 11:00 UTC son
# las 05:00 alli: fuera del horario de 8 a 17. Con ese instante, lo
# que respondia el planificador era "fuera de hora" y la caducidad
# del tope no llegaba a evaluarse nunca; la comprobacion salia
# verde sin comprobar lo suyo.
#
# No se noto porque en Windows no hay base de zonas horarias:
# ZoneInfo("America/Mexico_City") fallaba, el planificador caia a
# UTC y las 11:00 si entraban en franja. Al instalar tzdata -que
# hacia falta en el servidor para agrupar por dia- la zona empezo a
# resolverse y el fallo salio a la luz.
#
# 16:00 UTC son las 10:00 en Mexico: dentro.
comprobar(
    "pero caduca pasado el tope de 24 h",
    z2.intencion(momento(0, 16, 0))[0] is True,
    str(z2.intencion(momento(0, 16, 0)))
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

# Lo que el producto hace de verdad al arrancar a mano: avisar del
# estado. NO llama a notify_manual(True) -esta prueba lo daba por
# hecho y por eso no vio que el panel etiquetaba mal la grabacion-.
mixto.notify_state(True)

comprobar(
    "pero se puede arrancar una manual dentro de la misma franja",
    mixto._grabando is True
)

comprobar(
    "y el horario sigue sin reclamar en esa franja",
    mixto.intencion(momento(0, 12, 0))[0] is False
)


# ==================================================================
# 7. La manual puntual sigue siendo independiente
# ==================================================================

# Aqui SI con el interruptor apagado, que es lo que se quiere
# probar: sin horario y sin continua no se graba nada. Con la
# continua encendida y sin horario se grabaria siempre, que es otro
# caso y tiene su propia prueba.
m = _Planificador({"enabled": False})

comprobar(
    "sin horario ni continua, no se quiere grabar nada",
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

# El rotulo decia "DESACTIVADA (el horario manda)", y era cierto
# entonces: el horario grababa por encima del interruptor. Ya no.
# Apagada la continua no se graba, haya horario o no, asi que la
# matizacion sobraba y lo que hacia era contradecir al estado de al
# lado.
comprobar(
    "apagada es apagada, sin matices",
    '"DESACTIVADA (el horario manda)"' not in APP
)

comprobar(
    "y encendida con horario se ve encendida",
    '"ACTIVADA (en su horario)"' in APP
)

comprobar(
    "el indicador de grabacion real no se toco",
    'programada: "Grabando (programada)"' in APP
)


# ==================================================================
# 9. La fuente que informa el Agent: por el camino REAL
# ==================================================================
#
# Esta seccion existe por un fallo que las pruebas anteriores no
# vieron y que aparecio al probar en un equipo de verdad: una
# grabacion manual arrancada dentro de una franja suprimida se
# informaba al panel como "programada".
#
# La causa era la misma clase de error que el fallo original. La
# prueba de 6d llamaba a notify_manual(True) a mano, y el producto
# NUNCA la llama en el arranque: start_screen_recording solo avisa
# con notify_state(True), que no anota la fuente. Entonces fuente()
# caia en su deduccion -"la franja cubre esta hora, luego es
# programada"- que ignora la supresion.
#
# Por eso aqui no se llama a ninguna funcion suelta: se ejecuta
# start_screen_recording() y se lee build_recording_status(), que es
# literalmente lo que recibe el panel.

def _cargar_agent():
    """
    Importa el modulo real del Agent con dobles para lo que toca el
    sistema: pyautogui y el grabador de pantalla. Todo lo demas -los
    duenos, el planificador, el armado del estado- es el codigo que
    corre en produccion.
    """

    import types

    falso = types.ModuleType("pyautogui")
    falso.PAUSE = 0
    falso.FAILSAFE = False
    falso.KEYBOARD_KEYS = []
    falso.position = lambda: (0, 0)

    for metodo in ("moveTo", "click", "mouseDown", "mouseUp",
                   "press", "keyDown", "keyUp", "hotkey"):
        setattr(falso, metodo, lambda *a, **k: None)

    sys.modules["pyautogui"] = falso

    import agent as modulo

    class GrabadorFalso:
        """Lleva la cuenta de si grabaria, sin tocar la pantalla."""

        def __init__(self):
            self.activo = False

        def start(self):
            self.activo = True
            return {"status": "started"}

        def stop(self):
            self.activo = False
            return {"status": "stopped", "path": None, "duration": 0}

        def is_recording(self):
            return self.activo

        def current_segment_path(self):
            return None

    modulo.screen_recorder = GrabadorFalso()

    # Sin WebSocket: que informar al servidor no reviente ni intente
    # salir a la red.
    modulo.send_ws_threadsafe = lambda mensaje: False

    return modulo


agente = _cargar_agent()

# Estado de partida limpio: nadie sostiene nada.
agente.duenos = agente.owners.Duenos()
agente.recording_scheduler = RecordingScheduler(SIEMPRE)
agente.recording_scheduler.set_schedule(SIEMPRE)

comprobar(
    "de partida, el panel no ve ninguna grabacion",
    agente.build_recording_status()["recording_source"] is None
)

# --- el horario toma la captura, por el camino real ---------------

agente.recording_scheduler.set_continuous(True)
agente._aplicar_decision_del_horario()

estado_jornada = agente.build_recording_status()

comprobar(
    "la grabacion del dia se informa como continua",
    estado_jornada["recording_source"] == "continua",
    str(estado_jornada["recording_source"])
)

comprobar(
    "y no consta ningun trozo manual",
    estado_jornada["manual_recording"] is False
)

# --- un trozo manual DENTRO de la jornada -------------------------
#
# El caso que el usuario describio: el boton de grabar a mano sirve
# para capturar algo concreto, no para mandar sobre la jornada.

agente.start_screen_recording()

estado_con_trozo = agente.build_recording_status()

comprobar(
    "el trozo manual consta aparte",
    estado_con_trozo["manual_recording"] is True
)

comprobar(
    "y la jornada sigue siendo quien manda",
    estado_con_trozo["recording_source"] == "continua",
    str(estado_con_trozo["recording_source"])
)

# --- y al pararlo, la jornada SIGUE -------------------------------
#
# Esto es lo que antes no pasaba: parar el trozo manual soltaba
# tambien el horario y suprimia la franja, asi que grabar diez
# segundos a mano dejaba al equipo sin grabar el resto del dia.

agente.stop_screen_recording()

estado_despues = agente.build_recording_status()

comprobar(
    "parado el trozo manual, la jornada no se entera",
    estado_despues["recording"] is True,
    str(estado_despues)
)

comprobar(
    "y la sigue sosteniendo el horario",
    estado_despues["recording_source"] == "continua",
    str(estado_despues["recording_source"])
)

comprobar(
    "el trozo manual ya no consta",
    estado_despues["manual_recording"] is False
)

comprobar(
    "y no se ha suprimido ninguna franja",
    agente.recording_scheduler.clave_suprimida() is None,
    str(agente.recording_scheduler.clave_suprimida())
)

# --- apagar el interruptor SI para la jornada ---------------------
#
# El camino que queda para detenerla, y el que el usuario pidio:
# apagarla a las 10 y volver a encenderla a las 10:30, siguiendo el
# horario desde ese punto.

agente.apply_continuous(False)

comprobar(
    "apagar la continua detiene la jornada",
    agente.build_recording_status()["recording"] is False
)

agente.apply_continuous(True)

comprobar(
    "y encenderla la reanuda dentro de su horario",
    agente.build_recording_status()["recording"] is True
)

agente.stop_screen_recording(manual=False, origen=agente.owners.PROGRAMADA)
agente.duenos = agente.owners.Duenos()

# --- sin horario, el interruptor graba siempre --------------------

agente.recording_scheduler = RecordingScheduler({"enabled": False})
agente.recording_scheduler.set_continuous(True)

agente._aplicar_decision_del_horario()

comprobar(
    "sin horario, la continua graba igual",
    agente.build_recording_status()["recording"] is True
)

agente.stop_screen_recording(manual=False, origen=agente.owners.PROGRAMADA)
agente.duenos = agente.owners.Duenos()

# --- la deduccion sigue de respaldo -------------------------------
#
# Los duenos viven en memoria: tras reiniciar el Agent con una
# captura ya en marcha no consta ninguno, y la fuente hay que
# deducirla. Ese respaldo no se ha quitado.

agente.screen_recorder.activo = True
agente.recording_scheduler = RecordingScheduler(LABORAL)
agente.recording_scheduler.notify_state(True)

comprobar(
    "sin duenos anotados, la fuente se deduce de la situacion",
    agente.build_recording_status()["recording_source"] is not None
)

agente.screen_recorder.activo = False

comprobar(
    "el producto NO llama a notify_manual(True) al arrancar",
    "recording_scheduler.notify_manual(True)" not in AGENTE
)

comprobar(
    "el estado del panel sale de los duenos, no de la deduccion sola",
    "duenos.fuente_principal()" in AGENTE
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
