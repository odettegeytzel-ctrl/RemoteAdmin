"""
Grabacion manual y continua: una captura, dos duenos.

    .venv\\Scripts\\python tests/test_grabacion_duenos.py

El acoplamiento que esto corrige
--------------------------------

Habia un solo grabador global y dos modalidades usandolo:

    screen_recorder = ScreenRecorder(...)      # agent.py, uno solo

    def start(self):
        if self._running:
            return {"status": "already_recording"}

Con eso, dentro de la franja programada:

  - iniciar una grabacion manual NO hacia nada;
  - y detenerla paraba la captura entera, matando la continua.

Ahora la captura corre mientras al menos una modalidad la reclame, y
se anotan TRAMOS de quien la sostenia, porque un segmento de 15
minutos puede empezar con una modalidad y terminar con dos.

Estas pruebas recorren la matriz completa del encargo y no tocan el
grabador de verdad: el reloj se inyecta y la captura se simula, asi
que una jornada entera se comprueba en milisegundos.
"""

import io
import sys

from datetime import datetime, timedelta, timezone
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

import owners  # noqa: E402

from scheduler import RecordingScheduler  # noqa: E402


AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def momento(dia, hora, minuto=0):
    """Un instante concreto, para no depender del reloj de la maquina."""

    return datetime(2026, 10, dia, hora, minuto, tzinfo=timezone.utc)


# ==================================================================
# 1. LO BASICO DE LOS DUENOS
# ==================================================================

def test_arranca_una_vez_y_para_una_vez():

    d = owners.Duenos()

    comprobar("Sin duenos no se graba", not d.grabando())

    comprobar("El primero arranca la captura",
              d.tomar(owners.PROGRAMADA) is True)

    comprobar("El segundo NO la arranca otra vez",
              d.tomar(owners.MANUAL) is False,
              "habria duplicado la captura")

    comprobar("Las dos constan como activas",
              d.activos() == (owners.MANUAL, owners.PROGRAMADA),
              str(d.activos()))

    comprobar("Soltar una NO para la captura",
              d.soltar(owners.MANUAL) is False,
              "habria matado la continua")

    comprobar("Y sigue grabando", d.grabando())

    comprobar("Soltar la ultima SI la para",
              d.soltar(owners.PROGRAMADA) is True)

    comprobar("Ya no graba nadie", not d.grabando())


def test_soltar_lo_que_no_se_tiene_no_hace_nada():
    """Pasa de verdad: parar dos veces, o parar lo que nunca empezo."""

    d = owners.Duenos()

    comprobar("Soltar sin tener nada no para nada",
              d.soltar(owners.MANUAL) is False)

    d.tomar(owners.MANUAL)

    comprobar("Tomar dos veces el mismo no arranca dos veces",
              d.tomar(owners.MANUAL) is False)

    comprobar("Soltarlo lo para", d.soltar(owners.MANUAL) is True)

    comprobar("Y soltarlo otra vez no revienta",
              d.soltar(owners.MANUAL) is False)


def test_soltar_el_horario_no_toca_la_manual():
    """Lo que pasa a las 17:00 con una manual en marcha."""

    d = owners.Duenos()

    d.tomar(owners.PROGRAMADA)
    d.tomar(owners.MANUAL)

    comprobar("Termina la franja y la captura NO se para",
              d.soltar_el_horario() is False)

    comprobar("La manual sigue sosteniendola",
              d.activos() == (owners.MANUAL,), str(d.activos()))

    comprobar("Y ahora si la suelta la manual",
              d.soltar(owners.MANUAL) is True)


# ==================================================================
# 2. LOS TRAMOS
# ==================================================================

def test_un_segmento_no_se_atribuye_entero_a_una_modalidad():
    """
    El requisito que obliga a guardar tramos.

    Un segmento de 10 minutos donde la manual entro a mitad no es
    "manual" ni "programada": es las dos, cada una en su parte.
    """

    d = owners.Duenos()

    d.tomar(owners.PROGRAMADA, momento(13, 10, 0))
    d.tomar(owners.MANUAL, momento(13, 10, 4))
    d.soltar(owners.MANUAL, momento(13, 10, 7))

    tramos = d.tramos_entre(
        momento(13, 10, 0).isoformat(),
        momento(13, 10, 10).isoformat()
    )

    porOrigen = {}

    for t in tramos:
        porOrigen.setdefault(t["origen"], 0)
        porOrigen[t["origen"]] += t["segundos"]

    comprobar("La programada cubre los 10 minutos",
              porOrigen.get(owners.PROGRAMADA) == 600,
              str(porOrigen))

    comprobar("Y la manual solo sus 3 minutos",
              porOrigen.get(owners.MANUAL) == 180,
              str(porOrigen))

    comprobar("Son dos tramos, no uno",
              len(tramos) == 2, str(len(tramos)))


def test_los_tramos_se_recortan_al_segmento():
    """Un tramo que empezo antes del segmento no cuenta entero."""

    d = owners.Duenos()

    d.tomar(owners.PROGRAMADA, momento(13, 9, 0))

    tramos = d.tramos_entre(
        momento(13, 10, 0).isoformat(),
        momento(13, 10, 15).isoformat()
    )

    comprobar("Un solo tramo", len(tramos) == 1, str(tramos))

    if tramos:
        comprobar("Recortado al segmento, no desde las 09:00",
                  tramos[0]["segundos"] == 900,
                  str(tramos[0]))


def test_un_segmento_sin_duenos_no_inventa_tramos():

    d = owners.Duenos()

    comprobar("Sin actividad, ningun tramo",
              d.tramos_entre(momento(13, 10).isoformat(),
                             momento(13, 11).isoformat()) == [])

    comprobar("Fechas vacias tampoco inventan nada",
              d.tramos_entre("", "") == [])

    comprobar("Ni un rango al reves",
              d.tramos_entre(momento(13, 11).isoformat(),
                             momento(13, 10).isoformat()) == [])


def test_la_lista_de_tramos_no_crece_sin_limite():
    """Un equipo encendido semanas no puede acumular tramos infinitos."""

    d = owners.Duenos()

    for hora in range(0, 20, 2):
        d.tomar(owners.MANUAL, momento(13, hora))
        d.soltar(owners.MANUAL, momento(13, hora + 1))

    antes = len(d.tramos())

    tirados = d.olvidar_antes_de(momento(13, 11).isoformat())

    comprobar("Se tiran los tramos ya pasados",
              tirados > 0, "tirados %d de %d" % (tirados, antes))

    # Y uno abierto nunca se tira: se perderia el segmento en curso
    d.tomar(owners.PROGRAMADA, momento(13, 20))

    d.olvidar_antes_de(momento(14, 23).isoformat())

    comprobar("El tramo abierto se conserva",
              any(t["origen"] == owners.PROGRAMADA for t in d.tramos()),
              str(d.tramos()))


# ==================================================================
# 3. ESTADOS FALSOS
# ==================================================================

def test_si_el_grabador_se_cae_no_se_miente():
    """
    Un panel que dice "grabando" sin grabacion es peor que uno que
    no informa.
    """

    d = owners.Duenos()

    d.tomar(owners.PROGRAMADA)
    d.tomar(owners.MANUAL)

    comprobar("Con el grabador vivo no se corrige nada",
              d.reconciliar(True) is False)

    comprobar("Si el grabador no esta, se corrige",
              d.reconciliar(False) is True)

    comprobar("Y no queda nadie sosteniendo nada",
              d.activos() == (), str(d.activos()))

    comprobar("Reconciliar dos veces no hace nada",
              d.reconciliar(False) is False)


def test_la_fuente_principal_no_engana():
    """
    Con las dos activas manda el horario: decir "manual" daria a
    entender que pararla termina la grabacion, y ya no es asi.
    """

    d = owners.Duenos()

    comprobar("Sin nada, ninguna fuente",
              d.fuente_principal() is None)

    d.tomar(owners.MANUAL)

    comprobar("Solo manual -> manual",
              d.fuente_principal() == owners.MANUAL)

    d.tomar(owners.PROGRAMADA)

    comprobar("Con las dos -> manda la programada",
              d.fuente_principal() == owners.PROGRAMADA)

    d.soltar(owners.PROGRAMADA)

    comprobar("Y al soltarla, vuelve a ser manual",
              d.fuente_principal() == owners.MANUAL)


# ==================================================================
# 4. LA MATRIZ DEL ENCARGO
# ==================================================================

HORARIO = {
    "enabled": True,
    "days": [0, 1, 2, 3, 4],
    "start_time": "09:00",
    "end_time": "17:00",
    "timezone": "UTC"
}


class Captura:
    """Un grabador de mentira que solo cuenta arranques y paradas."""

    def __init__(self):
        self.arranques = 0
        self.paradas = 0
        self.corriendo = False

    def start(self):
        self.arranques += 1
        self.corriendo = True

    def stop(self):
        self.paradas += 1
        self.corriendo = False


def escenario():
    """Un planificador y unos duenos, listos para la matriz."""

    plan = RecordingScheduler(HORARIO)

    return plan, owners.Duenos(), Captura()


def aplicar_horario(plan, d, cap, ahora):
    """Lo mismo que hace _aplicar_decision_del_horario en el Agent."""

    d.reconciliar(cap.corriendo, ahora)

    plan.notify_state(cap.corriendo)

    quiere, fuente = plan.intencion(ahora)

    if quiere:

        if not d.sostiene(fuente):
            if d.tomar(fuente, ahora):
                cap.start()

        return

    if any(d.sostiene(o) for o in owners.DEL_HORARIO):
        if d.soltar_el_horario(ahora):
            cap.stop()


def manual(d, cap, encender, ahora):

    if encender:
        if d.tomar(owners.MANUAL, ahora):
            cap.start()

    else:
        if d.soltar(owners.MANUAL, ahora):
            cap.stop()


def test_matriz_completa():

    # --- Dentro del horario, sin accion manual ---
    plan, d, cap = escenario()

    aplicar_horario(plan, d, cap, momento(13, 10))

    comprobar("Dentro + sin manual: solo continua",
              d.activos() == (owners.PROGRAMADA,) and cap.corriendo,
              str(d.activos()))

    comprobar("Y una sola captura", cap.arranques == 1)

    # --- Dentro del horario, se inicia manual ---
    manual(d, cap, True, momento(13, 10, 30))

    comprobar("Dentro + iniciar manual: AMBAS activas",
              d.activos() == (owners.MANUAL, owners.PROGRAMADA),
              str(d.activos()))

    comprobar("Sin arrancar una segunda captura",
              cap.arranques == 1, "arranques: %d" % cap.arranques)

    # --- Se detiene la manual: la continua sigue ---
    manual(d, cap, False, momento(13, 11))

    comprobar("Detener manual: la continua SIGUE",
              cap.corriendo and d.activos() == (owners.PROGRAMADA,),
              str(d.activos()))

    comprobar("Y no se paro la captura",
              cap.paradas == 0, "paradas: %d" % cap.paradas)

    # --- Termina el horario ---
    aplicar_horario(plan, d, cap, momento(13, 17, 1))

    comprobar("Termina la franja: se para",
              not cap.corriendo and d.activos() == (),
              str(d.activos()))

    comprobar("Una sola parada", cap.paradas == 1)

    # --- Fuera del horario, manual sola ---
    plan, d, cap = escenario()

    aplicar_horario(plan, d, cap, momento(13, 20))

    comprobar("Fuera: la continua no arranca",
              not cap.corriendo)

    manual(d, cap, True, momento(13, 20))

    comprobar("Fuera + iniciar manual: solo manual",
              cap.corriendo and d.activos() == (owners.MANUAL,),
              str(d.activos()))

    manual(d, cap, False, momento(13, 21))

    comprobar("Fuera + detener manual: ambas inactivas",
              not cap.corriendo and d.activos() == ())


def test_el_horario_toma_su_turno_con_una_manual_en_marcha():
    """
    El caso que antes fallaba en silencio.

    A las 08:30 se inicia una manual. A las 09:00 empieza la franja.
    Antes el planificador veia que ya se grababa y no tomaba su
    turno; al parar la manual a las 09:30 se paraba todo, y la
    programada no volvia hasta el dia siguiente.
    """

    plan, d, cap = escenario()

    manual(d, cap, True, momento(13, 8, 30))

    comprobar("08:30 manual: grabando",
              cap.corriendo and d.activos() == (owners.MANUAL,))

    aplicar_horario(plan, d, cap, momento(13, 9, 0))

    comprobar("09:00 el horario TOMA su turno",
              d.sostiene(owners.PROGRAMADA), str(d.activos()))

    comprobar("Sin arrancar una segunda captura",
              cap.arranques == 1, "arranques: %d" % cap.arranques)

    manual(d, cap, False, momento(13, 9, 30))

    comprobar("09:30 se para la manual y la programada SIGUE",
              cap.corriendo and d.activos() == (owners.PROGRAMADA,),
              str(d.activos()))

    comprobar("No se corto la captura",
              cap.paradas == 0, "paradas: %d" % cap.paradas)


def test_reconexion_y_reinicio_sin_duplicados():
    """
    Desconectar y reconectar no puede dejar estados falsos ni
    arrancar una segunda captura.
    """

    plan, d, cap = escenario()

    aplicar_horario(plan, d, cap, momento(13, 10))

    # El Agent se reinicia: los duenos se pierden, la captura tambien
    plan2, d2, cap2 = escenario()

    aplicar_horario(plan2, d2, cap2, momento(13, 10, 5))

    comprobar("Tras reiniciar, la programada vuelve a tomar su turno",
              d2.sostiene(owners.PROGRAMADA))

    comprobar("Una sola captura, no dos",
              cap2.arranques == 1, "arranques: %d" % cap2.arranques)

    # Varias pasadas del bucle no acumulan nada
    for minuto in (10, 20, 30, 40):
        aplicar_horario(plan2, d2, cap2, momento(13, 11, minuto))

    comprobar("El bucle repetido no arranca de nuevo",
              cap2.arranques == 1, "arranques: %d" % cap2.arranques)

    comprobar("Ni para nada",
              cap2.paradas == 0, "paradas: %d" % cap2.paradas)

    # Y si el grabador se cae por su cuenta
    cap2.corriendo = False

    aplicar_horario(plan2, d2, cap2, momento(13, 12))

    comprobar("Si el grabador se cayo, se vuelve a arrancar",
              cap2.corriendo and cap2.arranques == 2,
              "arranques: %d" % cap2.arranques)


def test_cruce_de_medianoche_y_dias_no_laborables():

    nocturno = dict(HORARIO, start_time="22:00", end_time="06:00")

    plan = RecordingScheduler(nocturno)
    d = owners.Duenos()
    cap = Captura()

    # Martes 23:00, dentro del turno de noche
    aplicar_horario(plan, d, cap, momento(13, 23))

    comprobar("Turno de noche: graba a las 23:00", cap.corriendo)

    # Miercoles 05:00, aun dentro
    aplicar_horario(plan, d, cap, momento(14, 5))

    comprobar("Y sigue a las 05:00 del dia siguiente", cap.corriendo)

    # Miercoles 07:00, fuera
    aplicar_horario(plan, d, cap, momento(14, 7))

    comprobar("A las 07:00 ya no", not cap.corriendo)

    # Sabado: no es dia laborable
    plan2 = RecordingScheduler(HORARIO)
    d2 = owners.Duenos()
    cap2 = Captura()

    aplicar_horario(plan2, d2, cap2, momento(17, 10))

    comprobar("Sabado a las 10:00 no graba", not cap2.corriendo)

    # Pero la manual si puede
    manual(d2, cap2, True, momento(17, 10))

    comprobar("Y la manual funciona igual en sabado",
              cap2.corriendo and d2.activos() == (owners.MANUAL,))


# ==================================================================
# 5. EL AGENT ESTA CABLEADO ASI
# ==================================================================

def test_el_agent_usa_los_duenos():

    comprobar("El Agent tiene un registro de duenos",
              "duenos = owners.Duenos()" in AGENTE)

    comprobar("Arrancar pasa por tomar()",
              "duenos.tomar(origen)" in AGENTE)

    comprobar("Y parar por soltar()",
              "duenos.soltar(origen)" in AGENTE)

    comprobar("El horario suelta solo lo suyo",
              "duenos.soltar_el_horario()" in AGENTE)

    comprobar("Solo se arranca la captura si hace falta",
              "if hay_que_arrancar:" in AGENTE)

    comprobar("Y solo se para si no queda nadie",
              "if hay_que_parar:" in AGENTE)

    comprobar("El horario pregunta por su INTENCION",
              "recording_scheduler.intencion()" in AGENTE)

    comprobar("Y se reconcilia con el grabador real",
              "duenos.reconciliar(screen_recorder.is_recording())"
              in AGENTE)


# ==================================================================

def principal():

    pruebas = [
        test_arranca_una_vez_y_para_una_vez,
        test_soltar_lo_que_no_se_tiene_no_hace_nada,
        test_soltar_el_horario_no_toca_la_manual,
        test_un_segmento_no_se_atribuye_entero_a_una_modalidad,
        test_los_tramos_se_recortan_al_segmento,
        test_un_segmento_sin_duenos_no_inventa_tramos,
        test_la_lista_de_tramos_no_crece_sin_limite,
        test_si_el_grabador_se_cae_no_se_miente,
        test_la_fuente_principal_no_engana,
        test_matriz_completa,
        test_el_horario_toma_su_turno_con_una_manual_en_marcha,
        test_reconexion_y_reinicio_sin_duplicados,
        test_cruce_de_medianoche_y_dias_no_laborables,
        test_el_agent_usa_los_duenos
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("GRABACION: UNA CAPTURA, DOS DUENOS")
    print("=" * 66)
    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if not bien:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    if not fallos:
        print("  todo correcto")

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
