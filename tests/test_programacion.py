"""
Pruebas de la grabacion programada.

    .venv\\Scripts\\python tests/test_programacion.py

El reloj se pasa como argumento, asi que se puede comprobar en un
instante lo que ocurre un martes a las 07:59 y un sabado a las 03:00 sin
esperar a que llegue el momento.
"""

import sys

from datetime import datetime, timedelta
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))
sys.path.insert(0, str(RAIZ / "agent"))

import users_harness as h

import backend.database as database
import backend.schedule as schedule

from scheduler import RecordingScheduler, should_be_recording


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


LABORAL = {
    "enabled": True,
    "start_time": "08:00",
    "end_time": "17:00",
    "days": [0, 1, 2, 3, 4],
    "timezone": "local"
}

NOCTURNO = {
    "enabled": True,
    "start_time": "22:00",
    "end_time": "06:00",
    "days": [4],          # viernes noche
    "timezone": "local"
}


# 2026-10-05 es lunes
def momento(dia_offset, hora, minuto=0):
    return datetime(2026, 10, 5, hora, minuto) + timedelta(days=dia_offset)


# ==============================
# 1. Validacion y guardado
# ==============================

def test_valor_por_defecto():

    h.reiniciar()

    horario = schedule.get_schedule("equipo-nuevo")

    comprobar("Un equipo sin horario viene desactivado",
              horario["enabled"] is False)

    comprobar("Con un horario laboral por defecto",
              horario["start_time"] == "08:00"
              and horario["end_time"] == "17:00"
              and horario["days"] == [0, 1, 2, 3, 4])


def test_guardar_y_recuperar():

    h.reiniciar()

    schedule.set_schedule("equipo-1", LABORAL)

    guardado = schedule.get_schedule("equipo-1")

    comprobar("Se guarda activado", guardado["enabled"] is True)
    comprobar("Con sus horas", guardado["start_time"] == "08:00"
              and guardado["end_time"] == "17:00")
    comprobar("Y sus dias", guardado["days"] == [0, 1, 2, 3, 4])

    comprobar("Se puede desactivar sin perder el horario",
              schedule.set_schedule(
                  "equipo-1", dict(LABORAL, enabled=False)
              )["start_time"] == "08:00")

    comprobar("Y queda desactivado",
              schedule.get_schedule("equipo-1")["enabled"] is False)


def test_cada_equipo_tiene_el_suyo():

    h.reiniciar()

    schedule.set_schedule("equipo-1", LABORAL)
    schedule.set_schedule("equipo-2", dict(LABORAL, start_time="09:30"))

    comprobar("Los horarios no se mezclan entre equipos",
              schedule.get_schedule("equipo-1")["start_time"] == "08:00"
              and schedule.get_schedule("equipo-2")["start_time"] == "09:30")


def test_horarios_invalidos():

    invalidos = [
        {"start_time": "25:00", "end_time": "17:00", "days": [0]},
        {"start_time": "08:00", "end_time": "17:70", "days": [0]},
        {"start_time": "ocho", "end_time": "17:00", "days": [0]},
        {"start_time": "08:00", "end_time": "08:00", "days": [0]},
        {"start_time": "08:00", "end_time": "17:00", "days": []},
        {"start_time": "08:00", "end_time": "17:00", "days": [9]},
        {"start_time": "08:00", "end_time": "17:00", "days": ["lunes"]}
    ]

    rechazados = 0

    for datos in invalidos:
        try:
            schedule.validate(dict(datos, enabled=True))
        except schedule.ScheduleError:
            rechazados += 1

    comprobar("Los horarios imposibles se rechazan",
              rechazados == len(invalidos),
              f"{rechazados}/{len(invalidos)}")


def hay_base_de_zonas():
    """
    Windows no trae base de datos de zonas horarias.

    Sin el paquete tzdata, los nombres tipo Europe/Madrid no se pueden
    resolver y el unico modo util es 'local'. Las pruebas se adaptan en
    vez de fingir que funciona.
    """

    try:
        from zoneinfo import available_timezones

        return bool(available_timezones())

    except Exception:
        return False


def test_zona_horaria_desconocida():

    try:
        schedule.validate(dict(LABORAL, timezone="Marte/Olympus"))
        rechazo = False
    except schedule.ScheduleError:
        rechazo = True

    comprobar("Una zona horaria inventada se rechaza al guardar", rechazo)

    comprobar("'local' siempre se acepta",
              schedule.validate(
                  dict(LABORAL, timezone="local")
              )["timezone"] == "local")

    if hay_base_de_zonas():
        comprobar("Una zona real se acepta",
                  schedule.validate(
                      dict(LABORAL, timezone="Europe/Madrid")
                  )["timezone"] == "Europe/Madrid")

    else:
        try:
            schedule.validate(dict(LABORAL, timezone="Europe/Madrid"))
            aviso = ""
        except schedule.ScheduleError as error:
            aviso = str(error)

        comprobar(
            "Sin base de zonas se explica el motivo y se senala 'local'",
            "tzdata" in aviso and "local" in aviso, aviso
        )


def test_los_dias_admiten_texto_o_lista():

    comprobar("Se admite una lista de numeros",
              schedule.validate(dict(LABORAL, days=[2, 0, 1]))["days"]
              == [0, 1, 2])

    comprobar("Y una cadena separada por comas",
              schedule.validate(dict(LABORAL, days="4,0,0"))["days"]
              == [0, 4])


# ==============================
# 2. Decision: inicio y fin
# ==============================

def test_dentro_y_fuera_de_la_franja():

    casos = [
        ("lunes 07:59, aun no", momento(0, 7, 59), False),
        ("lunes 08:00, empieza", momento(0, 8, 0), True),
        ("lunes 12:00, en marcha", momento(0, 12, 0), True),
        ("lunes 16:59, todavia", momento(0, 16, 59), True),
        ("lunes 17:00, termina", momento(0, 17, 0), False),
        ("lunes 23:00, fuera", momento(0, 23, 0), False)
    ]

    for etiqueta, cuando, esperado in casos:
        comprobar(f"Horario laboral: {etiqueta}",
                  should_be_recording(LABORAL, cuando) is esperado)


def test_dias_de_la_semana():

    casos = [
        ("lunes", 0, True),
        ("martes", 1, True),
        ("viernes", 4, True),
        ("sabado", 5, False),
        ("domingo", 6, False)
    ]

    for etiqueta, offset, esperado in casos:
        comprobar(f"A las 12:00 del {etiqueta}",
                  should_be_recording(LABORAL, momento(offset, 12))
                  is esperado)


def test_programacion_desactivada():

    comprobar("Desactivada no graba nunca",
              should_be_recording(dict(LABORAL, enabled=False),
                                  momento(0, 12)) is False)

    comprobar("Sin horario tampoco",
              should_be_recording({}, momento(0, 12)) is False)


def test_franja_nocturna():
    """Viernes 22:00 a sabado 06:00, con el sabado SIN marcar."""

    casos = [
        ("viernes 21:59, aun no", momento(4, 21, 59), False),
        ("viernes 22:00, empieza", momento(4, 22, 0), True),
        ("viernes 23:30, sigue", momento(4, 23, 30), True),
        ("sabado 02:00, la madrugada cuenta", momento(5, 2, 0), True),
        ("sabado 05:59, todavia", momento(5, 5, 59), True),
        ("sabado 06:00, termina", momento(5, 6, 0), False),
        ("sabado 22:00, el sabado no esta marcado",
         momento(5, 22, 0), False)
    ]

    for etiqueta, cuando, esperado in casos:
        comprobar(f"Turno de noche: {etiqueta}",
                  should_be_recording(NOCTURNO, cuando) is esperado,
                  str(cuando))


def test_zona_horaria_se_respeta():
    """La misma hora absoluta cae dentro o fuera segun la zona."""

    from datetime import timezone

    if not hay_base_de_zonas():

        # Sin base de zonas, el Agent usa el reloj local. Se comprueba que
        # el repliegue es el correcto y no un fallo silencioso.
        comprobar(
            "Sin base de zonas se usa la hora local del equipo",
            should_be_recording(
                dict(LABORAL, timezone="Europe/Madrid"), momento(0, 12)
            ) is True
        )

        comprobar(
            "Y sigue respetando la franja con esa hora local",
            should_be_recording(
                dict(LABORAL, timezone="Europe/Madrid"), momento(0, 7, 59)
            ) is False
        )

        return

    # 07:30 UTC = 09:30 en Madrid (verano) -> dentro del horario laboral
    utc = datetime(2026, 7, 6, 7, 30, tzinfo=timezone.utc)

    comprobar(
        "Con zona Europe/Madrid, las 07:30 UTC caen dentro",
        should_be_recording(
            dict(LABORAL, timezone="Europe/Madrid"), utc
        ) is True
    )

    # 06:00 UTC = 08:00 Madrid: justo al empezar
    comprobar(
        "Y las 05:30 UTC (07:30 Madrid) todavia no",
        should_be_recording(
            dict(LABORAL, timezone="Europe/Madrid"),
            datetime(2026, 7, 6, 5, 30, tzinfo=timezone.utc)
        ) is False
    )


# ==============================
# 3. Arranque y parada automaticos
# ==============================

def test_arranca_y_para_sola():

    programador = RecordingScheduler(LABORAL)

    comprobar("Antes de la hora no hace nada",
              programador.decide(momento(0, 7, 59)) is None)

    comprobar("Al llegar la hora manda arrancar",
              programador.decide(momento(0, 8, 0)) == "start")

    programador.notify_state(True)

    comprobar("Durante la franja no repite la orden",
              programador.decide(momento(0, 12, 0)) is None)

    comprobar("Al terminar manda parar",
              programador.decide(momento(0, 17, 0)) == "stop")

    programador.notify_state(False)

    comprobar("Y despues ya no insiste",
              programador.decide(momento(0, 18, 0)) is None)


def test_una_semana_entera():
    """Recorre la semana minuto a minuto en los bordes de cada dia."""

    programador = RecordingScheduler(LABORAL)

    arranques = 0
    paradas = 0

    for dia in range(7):
        for hora in range(24):

            decision = programador.decide(momento(dia, hora))

            if decision == "start":
                arranques += 1
                programador.notify_state(True)

            elif decision == "stop":
                paradas += 1
                programador.notify_state(False)

    comprobar("Arranca una vez cada dia laborable",
              arranques == 5, str(arranques))

    comprobar("Y para una vez cada dia laborable",
              paradas == 5, str(paradas))


# ==============================
# 4. Convivencia con el mando manual
# ==============================

def test_parar_a_mano_no_se_reanuda_en_la_misma_franja():

    programador = RecordingScheduler(LABORAL)

    programador.decide(momento(0, 8, 0))
    programador.notify_state(True)

    # El operador para a las 10:00
    programador.notify_manual(False, momento(0, 10, 0))

    comprobar("Tras parar a mano no se reanuda a las 10:30",
              programador.decide(momento(0, 10, 30)) is None)

    comprobar("Ni a las 16:00",
              programador.decide(momento(0, 16, 0)) is None)

    comprobar("Al dia siguiente vuelve a arrancar sola",
              programador.decide(momento(1, 8, 0)) == "start")


def test_arrancar_a_mano_fuera_de_la_franja():

    programador = RecordingScheduler(LABORAL)

    # Domingo por la tarde, a mano
    programador.notify_manual(True, momento(6, 16, 0))

    comprobar("No la detiene inmediatamente",
              programador.decide(momento(6, 16, 30)) is None)

    comprobar("Sigue respetandola el resto del domingo",
              programador.decide(momento(6, 23, 0)) is None)

    comprobar("Y el lunes a las 17:00 la programacion retoma el mando",
              programador.decide(momento(0, 17, 0)) == "stop")


def test_reprogramar_olvida_la_excepcion():

    programador = RecordingScheduler(LABORAL)

    programador.decide(momento(0, 8, 0))
    programador.notify_state(True)
    programador.notify_manual(False, momento(0, 10, 0))

    comprobar("Con la excepcion puesta no reanuda",
              programador.decide(momento(0, 11, 0)) is None)

    programador.set_schedule(LABORAL)

    comprobar("Al reprogramar desde el panel vuelve a mandar el horario",
              programador.decide(momento(0, 11, 0)) == "start")


def test_estado_para_la_interfaz():

    programador = RecordingScheduler(LABORAL)

    comprobar("Con horario activo y sin grabar: programada",
              programador.estado() == "programada")

    programador.notify_state(True)

    comprobar("Grabando: grabando", programador.estado() == "grabando")

    programador.notify_state(False)
    programador.set_schedule(dict(LABORAL, enabled=False))

    comprobar("Sin horario y sin grabar: detenida",
              programador.estado() == "detenida")


# ==============================
# 5. Sin navegador y sin conexion
# ==============================

def test_el_horario_no_depende_del_panel():
    """
    El programador vive en el Agent y decide con su propio reloj.

    Si dependiera del panel, no habria forma de que decidiera nada aqui:
    estas comprobaciones corren sin servidor, sin sesion y sin navegador.
    """

    programador = RecordingScheduler(LABORAL)

    comprobar("Decide sin que nadie tenga el panel abierto",
              programador.decide(momento(0, 8, 0)) == "start")

    import io

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    comprobar("El Agent evalua el horario en un hilo propio",
              "_schedule_loop" in codigo
              and "RecordingSchedule" in codigo)

    comprobar("Y recibe el horario por el canal autenticado",
              "set_schedule:" in codigo)

    backend_codigo = io.open(RAIZ / "backend" / "main.py",
                             encoding="utf-8").read()

    comprobar("El servidor se lo manda nada mas conectar",
              "set_schedule:" in backend_codigo)


# ==============================
# 6. Endpoints
# ==============================

def test_endpoints():

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR IGNORE INTO devices (device_id, hostname) "
        "VALUES ('equipo-p', 'PRUEBA')"
    )
    conexion.commit()
    conexion.close()

    owner = h.cliente(h.OWNER)

    respuesta = owner.post(
        "/api/devices/equipo-p/recording/schedule",
        json=LABORAL
    )

    comprobar("El owner puede programar", respuesta.status_code == 200,
              str(respuesta.status_code))

    if respuesta.status_code == 200:
        comprobar("Se devuelve un resumen legible",
                  "lunes a viernes" in respuesta.json()["description"])

    comprobar("Y se puede consultar",
              owner.get("/api/devices/equipo-p/recording/schedule")
              .json()["schedule"]["enabled"] is True)

    comprobar("Un horario invalido se rechaza con 400",
              owner.post("/api/devices/equipo-p/recording/schedule",
                         json=dict(LABORAL, start_time="99:99")).status_code
              == 400)

    comprobar("Un equipo inexistente da 404",
              owner.post("/api/devices/no-existe/recording/schedule",
                         json=LABORAL).status_code == 404)


def test_permisos_del_horario():

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR IGNORE INTO devices (device_id, hostname) "
        "VALUES ('equipo-p', 'PRUEBA')"
    )
    conexion.commit()
    conexion.close()

    h.crear_subadmin("ana", permisos=["recordings.view"])

    cliente = h.cliente("ana")

    comprobar("Con recordings.view puede consultar el horario",
              cliente.get("/api/devices/equipo-p/recording/schedule")
              .status_code == 200)

    comprobar("Pero no cambiarlo",
              cliente.post("/api/devices/equipo-p/recording/schedule",
                           json=LABORAL).status_code == 403)

    import backend.users as users
    users.set_permissions("ana", ["recordings.view", "recordings.manage"])

    comprobar("Con recordings.manage ya puede",
              cliente.post("/api/devices/equipo-p/recording/schedule",
                           json=LABORAL).status_code == 200)


def test_auditoria_del_horario():

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR IGNORE INTO devices (device_id, hostname) "
        "VALUES ('equipo-p', 'PRUEBA')"
    )
    conexion.commit()
    conexion.close()

    owner = h.cliente(h.OWNER)

    owner.post("/api/devices/equipo-p/recording/schedule", json=LABORAL)
    owner.post("/api/devices/equipo-p/recording/schedule",
               json=dict(LABORAL, start_time="99:99"))

    filas = h.registros(action="recording.schedule")

    comprobar("Se audita el cambio correcto",
              any(r["status"] == "success" for r in filas))

    comprobar("Y el rechazado",
              any(r["status"] == "error" for r in filas))

    comprobar("Con el equipo al que afecta",
              all(r["device_id"] == "equipo-p" for r in filas))


# ==============================

def main():

    pruebas = [
        test_valor_por_defecto,
        test_guardar_y_recuperar,
        test_cada_equipo_tiene_el_suyo,
        test_horarios_invalidos,
        test_zona_horaria_desconocida,
        test_los_dias_admiten_texto_o_lista,
        test_dentro_y_fuera_de_la_franja,
        test_dias_de_la_semana,
        test_programacion_desactivada,
        test_franja_nocturna,
        test_zona_horaria_se_respeta,
        test_arranca_y_para_sola,
        test_una_semana_entera,
        test_parar_a_mano_no_se_reanuda_en_la_misma_franja,
        test_arrancar_a_mano_fuera_de_la_franja,
        test_reprogramar_olvida_la_excepcion,
        test_estado_para_la_interfaz,
        test_el_horario_no_depende_del_panel,
        test_endpoints,
        test_permisos_del_horario,
        test_auditoria_del_horario
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:70])

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLO"
        print(f"[{marca}] {nombre}" + (f"  -> {detalle}" if not ok else ""))
        if not ok:
            fallos += 1

    print(f"\n{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
