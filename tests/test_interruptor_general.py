r"""
La grabacion continua es el interruptor; el horario, sus horas.

    .venv\Scripts\python tests/test_interruptor_general.py

Como era
--------

El horario mandaba por encima de la grabacion continua:

    horario puesto + continua apagada  ->  grababa igual
    sin horario    + continua encendida ->  grababa siempre
    sin horario    + continua apagada   ->  no grababa

Dos consecuencias feas. Una, que el panel enseñaba "Grabacion
continua: DESACTIVADA" al lado de "GRABANDO", cada rotulo hablando
de una cosa distinta. Y dos, que no habia forma de parar la
grabacion de un equipo a media mañana sin borrarle el horario: el
unico boton que parecia servir -"Detener grabacion"- era el de la
grabacion manual, y usarlo dejaba al equipo sin grabar el resto de
la jornada.

Como es
-------

    la grabacion continua   EL INTERRUPTOR: si el equipo graba
    el horario              LAS HORAS: cuando

Apagado no se graba, haya horario o no. Encendido, se graba en las
horas que tenga puestas, o siempre si no tiene ninguna. Y el boton
de grabar a mano es otra cosa: captura un trozo concreto y no manda
sobre la jornada.

Lo que hay que vigilar
----------------------

Que el cambio no deje a oscuras a los equipos que YA grababan: hoy
la mayoria tiene horario puesto y la continua apagada, que era la
unica combinacion que funcionaba. Esos tienen que seguir grabando
mañana. De eso se encarga una migracion, y se comprueba aqui.
"""

import io
import os
import sys
import tempfile

from datetime import datetime, timedelta, timezone
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-interruptor")

from scheduler import RecordingScheduler  # noqa: E402

JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()


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

    lunes = datetime(2026, 10, 26, hora, minuto, tzinfo=timezone.utc)

    return lunes + timedelta(days=dia)


# ==================================================================
# 1. EL INTERRUPTOR MANDA
# ==================================================================

def test_apagado_no_se_graba_aunque_haya_horario():
    """
    El caso que antes no existia, y que era justo el que se pedia.
    """

    p = RecordingScheduler(LABORAL)

    # Sin tocar nada: el interruptor nace apagado
    comprobar("Lunes a las 10, con horario pero sin continua: no graba",
              p.intencion(momento(0, 10, 0))[0] is False,
              str(p.intencion(momento(0, 10, 0))))

    p.set_continuous(True)

    comprobar("Encendido el interruptor, si graba",
              p.intencion(momento(0, 10, 0))[0] is True)


def test_el_horario_sigue_poniendo_las_horas():
    """
    Que el interruptor mande no significa que grabe a deshora.

    Quien configura "lunes a viernes de 8 a 17" no quiere que se
    grabe el domingo de madrugada.
    """

    p = RecordingScheduler(LABORAL)
    p.set_continuous(True)

    comprobar("A las 7:59 todavia no",
              p.intencion(momento(0, 7, 59))[0] is False)

    comprobar("A las 8:00 si",
              p.intencion(momento(0, 8, 0))[0] is True)

    comprobar("A las 17:30 ya no",
              p.intencion(momento(0, 17, 30))[0] is False)

    comprobar("Y el domingo tampoco",
              p.intencion(momento(6, 10, 0))[0] is False)


def test_sin_horario_el_interruptor_graba_siempre():

    p = RecordingScheduler({"enabled": False})

    comprobar("Sin horario y apagado, nada",
              p.intencion(momento(0, 10, 0))[0] is False)

    p.set_continuous(True)

    comprobar("Sin horario y encendido, siempre",
              p.intencion(momento(0, 10, 0))[0] is True)

    comprobar("Incluso el domingo de madrugada",
              p.intencion(momento(6, 3, 0))[0] is True)


def test_ya_no_hay_dos_motivos():
    """
    Lo que graba a sus horas ES la continua. 'programada' como
    fuente sobraba: eran la misma cosa con dos nombres, y el panel
    los presentaba como si compitieran.
    """

    p = RecordingScheduler(LABORAL)
    p.set_continuous(True)

    quiere, fuente = p.intencion(momento(0, 10, 0))

    comprobar("Dentro del horario, la fuente es la continua",
              quiere is True and fuente == "continua",
              repr(fuente))

    q = RecordingScheduler({"enabled": False})
    q.set_continuous(True)

    comprobar("Y sin horario, tambien",
              q.intencion(momento(0, 10, 0))[1] == "continua",
              repr(q.intencion(momento(0, 10, 0))[1]))


# ==================================================================
# 2. EL CASO QUE DESCRIBIO EL USUARIO
# ==================================================================

def test_apagar_a_las_diez_y_encender_a_las_diez_y_media():
    """
    "Si yo a las 10 entro al panel y le doy en desactivar grabacion
    continua, se desactiva y deja de grabar. A las 10:30 entro
    nuevamente y le doy grabar continuamente, sigue grabando en el
    horario que tiene asignado a partir de ese punto."
    """

    p = RecordingScheduler(LABORAL)
    p.set_continuous(True)

    comprobar("A las 9:00 esta grabando",
              p.intencion(momento(0, 9, 0))[0] is True)

    # --- 10:00, el tecnico lo apaga ---
    p.set_continuous(False)

    comprobar("A las 10:00 se apaga y deja de grabar",
              p.intencion(momento(0, 10, 0))[0] is False)

    comprobar("Y sigue sin grabar a las 10:15",
              p.intencion(momento(0, 10, 15))[0] is False)

    # --- 10:30, lo vuelve a encender ---
    p.set_continuous(True)

    comprobar("A las 10:30 se enciende y vuelve a grabar",
              p.intencion(momento(0, 10, 30))[0] is True)

    comprobar("Y sigue hasta el final de su horario",
              p.intencion(momento(0, 16, 59))[0] is True)

    comprobar("Pero no se pasa de las 17:00",
              p.intencion(momento(0, 17, 1))[0] is False)

    # Y al dia siguiente, su horario de siempre: apagar y encender a
    # media mañana no deja ninguna huella.
    comprobar("El martes vuelve a grabar a su hora",
              p.intencion(momento(1, 8, 30))[0] is True)


# ==================================================================
# 3. EL BOTON MANUAL ES OTRA COSA
# ==================================================================

def test_el_boton_manual_mira_solo_su_grabacion():
    """
    Se ponia en "Detener grabacion" en cuanto el horario empezaba la
    jornada: ofrecia parar algo que el no habia arrancado.
    """

    comprobar("El Agent informa si hay un trozo manual",
              '"manual_recording": duenos.sostiene(owners.MANUAL)'
              in AGENTE)

    comprobar("El panel lo recibe",
              "data.manual_recording" in JS)

    cuerpo = JS.split("function setRecordingUI(", 1)[1][:2600]

    comprobar("Y los botones miran eso, no 'recording'",
              'startButton.classList.toggle("hidden", hayTrozoManual)'
              in cuerpo
              and 'stopButton.classList.toggle("hidden", !hayTrozoManual)'
              in cuerpo)

    comprobar("El indicador si habla de la captura entera",
              'indicator.classList.toggle("hidden", !recording)' in cuerpo)

    # Un Agent sin actualizar no manda el dato. Ahi no se puede hacer
    # mejor que antes, pero tampoco peor: los botones no pueden
    # quedarse los dos escondidos.
    comprobar("Un Agent antiguo no se queda sin botones",
              "manual === undefined || manual === null" in cuerpo)


def test_el_rotulo_de_la_continua_ya_no_se_contradice():

    cuerpo = JS.split("const stateText = enabled", 1)[1][:400]

    comprobar("Encendida y con horario: ACTIVADA, no DESACTIVADA",
              '"ACTIVADA (en su horario)"' in cuerpo,
              cuerpo[:160].replace(chr(10), " | "))

    # El rotulo que veia el usuario, no la palabra suelta: la
    # explicacion de por que cambio sigue escrita en un comentario,
    # y eso debe seguir ahi.
    comprobar("Y desaparece el rotulo 'DESACTIVADA (el horario manda)'",
              '"DESACTIVADA (el horario manda)"' not in JS)


# ==================================================================
# 4. QUE NADIE SE QUEDE SIN GRABAR
# ==================================================================

def _base_nueva():

    from backend import database

    carpeta = tempfile.mkdtemp(prefix="interruptor-")
    database.DATABASE_PATH = os.path.join(carpeta, "prueba.db")
    database.init_db()

    return database


def test_la_migracion_enciende_a_quien_ya_grababa():
    """
    Hoy la combinacion corriente es horario puesto + continua
    apagada, porque era la unica que grababa. Sin migracion, el
    cambio dejaria esos equipos a oscuras y nadie se enteraria hasta
    buscar una grabacion que no existe.
    """

    database = _base_nueva()

    MARCA = "migracion.continua_es_el_interruptor"

    conexion = database.get_connection()

    conexion.execute(
        "INSERT INTO devices (device_id, hostname, continuous_recording) "
        "VALUES ('con-horario', 'PC-1', 0)"
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, continuous_recording) "
        "VALUES ('sin-horario', 'PC-2', 0)"
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, continuous_recording) "
        "VALUES ('horario-apagado', 'PC-3', 0)"
    )
    conexion.execute(
        "INSERT INTO recording_schedule (device_id, enabled) "
        "VALUES ('con-horario', 1)"
    )
    conexion.execute(
        "INSERT INTO recording_schedule (device_id, enabled) "
        "VALUES ('horario-apagado', 0)"
    )

    # Se borra la marca para simular una base anterior al cambio
    conexion.execute("DELETE FROM settings WHERE key = ?", (MARCA,))
    conexion.commit()
    conexion.close()

    database.init_db()

    def interruptor(identificador):

        conexion = database.get_connection()

        fila = conexion.execute(
            "SELECT continuous_recording FROM devices WHERE device_id = ?",
            (identificador,)
        ).fetchone()

        conexion.close()

        return fila[0]

    comprobar("Al equipo con horario se le enciende",
              interruptor("con-horario") == 1)

    comprobar("Al que no tiene horario no se le toca",
              interruptor("sin-horario") == 0)

    comprobar("Ni al que lo tiene desactivado",
              interruptor("horario-apagado") == 0)

    # --- y lo mas importante: no revive ---
    #
    # Sin marca, esto correria en cada arranque y volveria a
    # encender lo que el tecnico acaba de apagar. Apagar la
    # grabacion de un equipo duraria hasta el siguiente reinicio del
    # servidor, y relacionar una cosa con la otra seria durisimo.

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET continuous_recording = 0 "
        "WHERE device_id = 'con-horario'"
    )
    conexion.commit()
    conexion.close()

    database.init_db()

    comprobar("Apagarlo y reiniciar el servidor NO lo vuelve a encender",
              interruptor("con-horario") == 0,
              "la migracion revivio")

    conexion = database.get_connection()

    marca = conexion.execute(
        "SELECT value FROM settings WHERE key = ?", (MARCA,)
    ).fetchone()

    conexion.close()

    comprobar("Y queda constancia de cuando se hizo",
              marca is not None and marca[0],
              str(marca))

    # La base se aparta a un temporal vacio: si se dejara apuntando
    # a la que se acaba de usar, la siguiente suite escribiria ahi.
    database.DATABASE_PATH = os.path.join(
        tempfile.mkdtemp(prefix="apartado-"), "vacia.db"
    )


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("LA CONTINUA ES EL INTERRUPTOR; EL HORARIO, SUS HORAS")
    print("=" * 66)
    print()

    pruebas = [
        test_apagado_no_se_graba_aunque_haya_horario,
        test_el_horario_sigue_poniendo_las_horas,
        test_sin_horario_el_interruptor_graba_siempre,
        test_ya_no_hay_dos_motivos,
        test_apagar_a_las_diez_y_encender_a_las_diez_y_media,
        test_el_boton_manual_mira_solo_su_grabacion,
        test_el_rotulo_de_la_continua_ya_no_se_contradice,
        test_la_migracion_enciende_a_quien_ya_grababa,
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      "%s: %s" % (type(error).__name__, error))

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  ok     %s" % nombre)

        else:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
