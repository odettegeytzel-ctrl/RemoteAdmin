"""
Grabacion continua y horario: quien manda y quien lo dice.

    .venv\\Scripts\\python tests/test_continua_y_horario.py

Dos sintomas con la misma raiz: "grabacion continua" y "grabar segun
horario" eran mecanismos independientes que no se hablaban.

  - La continua no sabia de horas. El servidor la reenvia en CADA
    conexion del Agent, asi que una reconexion de madrugada abria una
    grabacion fuera de la franja configurada.

  - El estado que mandaba el Agent decia SI se grababa, nunca POR
    QUE, asi que el panel encendia a la vez el control manual y el de
    continua.

Aqui se comprueba la decision, no el dibujo: el planificador no graba
ni para nada, solo responde que procede, y por eso se puede recorrer
una semana entera en milisegundos con un reloj inventado.

No se toca ningun equipo, ninguna grabacion ni ninguna base de datos.
"""

import io
import sys

from datetime import datetime, timedelta
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

from scheduler import (  # noqa: E402
    RecordingScheduler,
    should_be_recording,
)


AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()


def cuerpo_js(nombre):
    """
    El cuerpo de una funcion de app.js, contando llaves.

    Partir por "
}
" no vale: los cuerpos de las funciones de nivel
    superior no van sangrados en ese archivo, asi que hay cierres en
    la columna 0 dentro de la propia funcion.
    """

    inicio = JS.index(f"function {nombre}(")

    nivel = 0
    visto = False

    for i in range(inicio, len(JS)):

        if JS[i] == "{":
            nivel += 1
            visto = True

        elif JS[i] == "}":
            nivel -= 1

            if visto and nivel == 0:
                return JS[inicio:i + 1]

    raise AssertionError(nombre)


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# Lunes a viernes, de nueve a cinco
HORARIO = {
    "enabled": True,
    "days": [0, 1, 2, 3, 4],
    "start_time": "09:00",
    "end_time": "17:00",
    "timezone": "America/Mexico_City",
}

SIN_HORARIO = {"enabled": False}


def cuando(texto):
    return datetime.fromisoformat(texto)


# ==============================
# EL HORARIO, EN SUS LIMITES
# ==============================

def test_el_horario_respeta_sus_bordes():
    """Los minutos exactos de entrada y salida, que es donde se falla."""

    CASOS = [
        ("2026-10-12T08:59", False, "lunes, un minuto antes"),
        ("2026-10-12T09:00", True, "lunes, el minuto de entrada"),
        ("2026-10-12T16:59", True, "lunes, el ultimo minuto"),
        ("2026-10-12T17:00", False, "lunes, el minuto de salida"),
        ("2026-10-12T23:59", False, "lunes, casi medianoche"),
        ("2026-10-13T03:00", False, "martes de madrugada"),
        ("2026-10-16T16:59", True, "viernes, el ultimo minuto"),
        ("2026-10-17T12:00", False, "sabado al mediodia"),
        ("2026-10-18T12:00", False, "domingo al mediodia"),
    ]

    for momento, esperado, descripcion in CASOS:

        real = should_be_recording(HORARIO, cuando(momento))

        comprobar(f"Horario: {descripcion}", real == esperado,
                  f"esperaba {esperado}, dio {real}")


def test_una_semana_entera_minuto_a_minuto():
    """
    Cuarenta horas de las ciento sesenta y ocho que tiene la semana.
    Si sobra o falta un solo minuto, aqui se ve.
    """

    inicio = cuando("2026-10-12T00:00")   # un lunes

    minutos_grabando = 0

    for i in range(7 * 24 * 60):

        if should_be_recording(HORARIO, inicio + timedelta(minutes=i)):
            minutos_grabando += 1

    esperados = 5 * 8 * 60   # cinco dias de ocho horas

    comprobar("La semana suma exactamente 40 horas",
              minutos_grabando == esperados,
              f"{minutos_grabando} minutos frente a {esperados}")


def test_la_franja_que_cruza_la_medianoche():
    """Un turno de noche no puede perderse al cambiar la fecha."""

    nocturno = {
        "enabled": True,
        "days": [0],                 # empieza los lunes
        "start_time": "22:00",
        "end_time": "06:00",
        "timezone": "America/Mexico_City",
    }

    CASOS = [
        ("2026-10-12T21:59", False, "lunes, antes de empezar"),
        ("2026-10-12T22:00", True, "lunes, al empezar"),
        ("2026-10-13T02:00", True, "martes de madrugada, misma franja"),
        ("2026-10-13T05:59", True, "martes, el ultimo minuto"),
        ("2026-10-13T06:00", False, "martes, al acabar"),
        ("2026-10-13T22:00", False, "martes por la noche: no toca"),
    ]

    for momento, esperado, descripcion in CASOS:

        real = should_be_recording(nocturno, cuando(momento))

        comprobar(f"Turno de noche: {descripcion}", real == esperado,
                  f"esperaba {esperado}, dio {real}")


# ==============================
# LA CONTINUA, LIMITADA POR EL HORARIO
# ==============================

def test_sin_horario_la_continua_graba_siempre():
    """Sin franjas configuradas, continua quiere decir continua."""

    p = RecordingScheduler(SIN_HORARIO)
    p.set_continuous(True)

    for momento in ("2026-10-12T10:00", "2026-10-12T23:00",
                    "2026-10-18T04:00"):

        p.notify_state(False)

        comprobar(f"Sin horario, a las {momento[11:]} arranca",
                  p.decide(ahora=cuando(momento)) == "start")


def test_con_horario_la_continua_se_limita_a_sus_franjas():
    """
    El cambio de comportamiento, y el motivo de todo esto: quien
    configura "lunes a viernes de 9 a 17" no quiere que se grabe el
    domingo de madrugada.
    """

    p = RecordingScheduler(HORARIO)
    p.set_continuous(True)

    # Dentro de la franja: arranca
    p.notify_state(False)

    comprobar("Lunes a las 10:00 arranca",
              p.decide(ahora=cuando("2026-10-12T10:00")) == "start")

    # Grabando y llega la hora de salida: para
    p.notify_state(True)

    comprobar("A las 17:00 para, aunque la continua este activa",
              p.decide(ahora=cuando("2026-10-12T17:00")) == "stop")

    # Fuera de franja no vuelve a arrancar
    p.notify_state(False)

    for momento, descripcion in (
            ("2026-10-12T22:00", "lunes por la noche"),
            ("2026-10-13T04:00", "martes de madrugada"),
            ("2026-10-18T12:00", "domingo al mediodia")):

        comprobar(f"No arranca el {descripcion}",
                  p.decide(ahora=cuando(momento)) != "start",
                  str(p.decide(ahora=cuando(momento))))


def test_la_reconexion_de_madrugada_ya_no_abre_una_grabacion():
    """
    El caso que producia las grabaciones fuera de hora.

    El servidor reenvia la continua en CADA conexion del Agent. Antes
    eso arrancaba a grabar sin mirar el reloj: cualquier corte de red
    de madrugada dejaba una grabacion nueva.
    """

    p = RecordingScheduler(HORARIO)

    # Reconexion un domingo a las 3 de la mañana: llega la continua
    p.set_continuous(True)
    p.notify_state(False)

    decision = p.decide(ahora=cuando("2026-10-18T03:00"))

    comprobar("Una reconexion de madrugada no arranca nada",
              decision != "start", str(decision))

    # Y el lunes, a su hora, si
    comprobar("Pero el lunes a las 9 arranca",
              p.decide(ahora=cuando("2026-10-19T09:00")) == "start")


def test_apagar_la_continua_detiene_lo_que_sostenia():

    p = RecordingScheduler(SIN_HORARIO)

    p.set_continuous(True)
    p.notify_state(True)

    comprobar("Mientras esta activa, no para",
              p.decide(ahora=cuando("2026-10-12T10:00")) is None)

    p.set_continuous(False)

    comprobar("Al apagarla, para",
              p.decide(ahora=cuando("2026-10-12T10:00")) == "stop")


# ==============================
# QUIEN SOSTIENE LA GRABACION
# ==============================

def test_el_planificador_dice_quien_graba():
    """
    El dato que faltaba. Sin el, el panel no podia distinguir una
    grabacion manual de una programada.
    """

    p = RecordingScheduler(HORARIO)

    comprobar("Sin grabar, no hay fuente",
              p.fuente(ahora=cuando("2026-10-12T10:00")) is None)

    # Arranca la programacion
    p.notify_state(False)
    p.decide(ahora=cuando("2026-10-12T10:00"))
    p.notify_state(True)

    comprobar("Dentro de la franja, la sostiene la programacion",
              p.fuente(ahora=cuando("2026-10-12T10:30")) == "programada",
              str(p.fuente(ahora=cuando("2026-10-12T10:30"))))

    # Una persona la arranca un domingo
    q = RecordingScheduler(HORARIO)
    q.notify_manual(True, ahora=cuando("2026-10-18T12:00"))

    comprobar("Lo que arranca una persona es manual",
              q.fuente(ahora=cuando("2026-10-18T12:05")) == "manual",
              str(q.fuente(ahora=cuando("2026-10-18T12:05"))))

    # La continua, sin horario
    r = RecordingScheduler(SIN_HORARIO)
    r.set_continuous(True)
    r.notify_state(False)
    r.decide(ahora=cuando("2026-10-18T03:00"))
    r.notify_state(True)

    comprobar("Y la continua se llama continua",
              r.fuente(ahora=cuando("2026-10-18T03:05")) == "continua",
              str(r.fuente(ahora=cuando("2026-10-18T03:05"))))


def test_al_dejar_de_grabar_no_queda_fuente():

    p = RecordingScheduler(SIN_HORARIO)

    p.set_continuous(True)
    p.notify_state(True)

    comprobar("Grabando hay fuente", p.fuente() is not None)

    p.notify_state(False)

    comprobar("Parada, no", p.fuente() is None, str(p.fuente()))


def test_el_mando_manual_sigue_mandando_en_su_franja():
    """
    Lo que ya funcionaba y no podia romperse: parar a mano dentro de
    una franja no se deshace medio minuto despues.
    """

    p = RecordingScheduler(HORARIO)
    p.set_continuous(True)

    # Lunes a las 10, alguien la para a mano
    p.notify_manual(False, ahora=cuando("2026-10-12T10:00"))

    for momento in ("2026-10-12T10:01", "2026-10-12T12:00",
                    "2026-10-12T16:59"):

        comprobar(f"No se reanuda sola a las {momento[11:]}",
                  p.decide(ahora=cuando(momento)) is None,
                  str(p.decide(ahora=cuando(momento))))

    # Al dia siguiente, la programacion vuelve a mandar
    comprobar("Pero el martes a las 9 vuelve a arrancar",
              p.decide(ahora=cuando("2026-10-13T09:00")) == "start")


# ==============================
# EL AGENT
# ==============================

def test_la_continua_ya_no_arranca_por_su_cuenta():
    """
    Era la causa: apply_continuous llamaba a start_screen_recording()
    sin mirar el horario, y ademas sin argumento, asi que manual=True.
    """

    bloque = AGENTE.split("def apply_continuous", 1)[1].split(
        "\ndef ", 1
    )[0]

    comprobar("Ya no arranca la grabacion directamente",
              "start_screen_recording()" not in bloque, bloque[-300:])

    comprobar("Ni la detiene directamente",
              "stop_screen_recording()" not in bloque)

    comprobar("Avisa al planificador",
              "recording_scheduler.set_continuous" in bloque)

    comprobar("Y deja que el decida",
              "_aplicar_decision_del_horario()" in bloque)


def test_el_agent_informa_de_la_fuente():

    bloque = AGENTE.split("def build_recording_status", 1)[1].split(
        "\ndef ", 1
    )[0]

    # Solo el diccionario que se devuelve.
    #
    # Mirar el bloque entero no valia: la cadena de documentacion
    # nombra los campos, asi que la comprobacion pasaba aunque el
    # campo se hubiera borrado del return.
    devuelto = bloque.split("return {", 1)[1].split("}", 1)[0]

    comprobar("El estado DEVUELVE recording_source",
              '"recording_source":' in devuelto, devuelto)

    comprobar("Que sale del planificador",
              "recording_scheduler.fuente()" in bloque)

    comprobar("Y devuelve si hay horario activo",
              '"schedule_enabled":' in devuelto, devuelto)

    comprobar("Sin perder lo que ya informaba",
              '"recording":' in devuelto
              and '"continuous_recording_enabled":' in devuelto
              and '"pending_uploads":' in devuelto, devuelto)


# ==============================
# EL PANEL
# ==============================

def test_el_panel_distingue_los_dos_controles():
    """
    El sintoma: los dos controles encendidos a la vez. El panel
    pintaba el control manual con solo saber que se grababa.
    """

    comprobar("Hay nombres para cada origen",
              "ORIGEN_DE_LA_GRABACION" in JS)

    for origen in ("manual", "programada", "continua"):
        mapa = JS[JS.index("const ORIGEN_DE_LA_GRABACION"):]
        mapa = mapa[:mapa.index("};") + 2]

        comprobar(f"Se nombra el origen '{origen}'",
                  origen + ":" in mapa, mapa)

    comprobar("setRecordingUI recibe el origen",
              "function setRecordingUI(recording, origen)" in JS)

    comprobar("Y se lo pasan desde el estado del Agent",
              "setRecordingUI(Boolean(data.recording), "
              "data.recording_source)" in JS)

    comprobar("El indicador tiene donde decirlo",
              'id="recording-indicator-text"' in HTML)


def test_detener_sigue_disponible_sea_cual_sea_el_origen():
    """
    Una persona tiene que poder cortar una grabacion programada sin
    ir a desactivar la programacion entera.
    """

    bloque = cuerpo_js("setRecordingUI")

    comprobar("El boton de detener depende solo de que se grabe",
              'stopButton.classList.toggle("hidden", !recording)' in bloque,
              bloque[-400:])

    comprobar("Y el de iniciar se oculta mientras se graba",
              'startButton.classList.toggle("hidden", recording)' in bloque)


def test_se_avisa_de_que_el_horario_limita_la_continua():
    """
    Decir solo "ACTIVADA" hace creer que graba a todas horas.
    """

    comprobar("El rotulo distingue los dos casos",
              "ACTIVADA (dentro del horario)" in JS)

    comprobar("Mirando si hay horario",
              "data.schedule_enabled" in JS)


def test_el_estado_del_panel_prioriza_la_fuente():

    comprobar("Grabando, se enseña quien la sostiene",
              "ORIGEN_DE_LA_GRABACION[data.recording_source]" in JS)


# ==============================

def main():

    pruebas = [
        test_el_horario_respeta_sus_bordes,
        test_una_semana_entera_minuto_a_minuto,
        test_la_franja_que_cruza_la_medianoche,
        test_sin_horario_la_continua_graba_siempre,
        test_con_horario_la_continua_se_limita_a_sus_franjas,
        test_la_reconexion_de_madrugada_ya_no_abre_una_grabacion,
        test_apagar_la_continua_detiene_lo_que_sostenia,
        test_el_planificador_dice_quien_graba,
        test_al_dejar_de_grabar_no_queda_fuente,
        test_el_mando_manual_sigue_mandando_en_su_franja,
        test_la_continua_ya_no_arranca_por_su_cuenta,
        test_el_agent_informa_de_la_fuente,
        test_el_panel_distingue_los_dos_controles,
        test_detener_sigue_disponible_sea_cual_sea_el_origen,
        test_se_avisa_de_que_el_horario_limita_la_continua,
        test_el_estado_del_panel_prioriza_la_fuente,
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:110])

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
