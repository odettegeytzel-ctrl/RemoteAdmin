r"""
Quien graba es el ayudante, y solo el ayudante.

    .venv\Scripts\python tests/test_quien_graba.py

El fallo
--------

El Agent corre partido en dos procesos:

    servicio    arranca con Windows, no tiene escritorio (Sesion 0)
    ayudante    existe mientras hay un usuario con la sesion abierta

Capturar la pantalla solo puede hacerlo el ayudante. El hilo del
horario ya se arrancaba solo fuera del servicio por ese motivo, con
un comentario que lo decia con todas las letras. Pero quedaba una
puerta abierta:

    set_schedule:   la atiende el SERVICIO
    apply_schedule  termina llamando a _aplicar_decision_del_horario

...que arranca el grabador. En el servicio. En la Sesion 0.

El servidor reenvia el horario en CADA conexion del Agent, asi que
cada reconexion abria en el servicio un video de una pantalla que no
existe, y nadie lo cerraba nunca, porque el hilo que termina las
franjas no corre ahi. De ahi las grabaciones de 261 bytes y cero
segundos.

Y de ahi tambien el boton que no detenia nada: el panel preguntaba el
estado al servicio -que decia "grabando"- mientras 'stop_recording'
se reenviaba al ayudante, que no estaba grabando. Dos procesos, cada
uno con su idea de la realidad.

El tercer sintoma, el mas callado
---------------------------------

send_ws_threadsafe() se iba sin hacer nada si no habia WebSocket. En
el ayudante NUNCA lo hay: la conexion la tiene el servicio. Asi que
todo lo que el ayudante tenia que contar se perdia: el estado del
grabador, el catalogo de grabaciones guardadas en el equipo y la
franja suprimida al parar a mano.

Que se comprueba
----------------

  - que el servicio no arranca el grabador ni aunque se le pida;
  - que el ayudante si;
  - que los ajustes de grabacion llegan al ayudante, y que el
    ayudante que entra tarde los recibe igual;
  - que lo que el ayudante produce sale por el canal del servicio;
  - y que el estado lo cuenta quien graba, no quien solo guarda el
    ajuste.
"""

import io
import os
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-quien-graba")

AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
AYUDANTE = io.open(RAIZ / "agent" / "helper.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


# ==================================================================
# DOBLES
# ==================================================================

class GrabadorFalso:
    """Un grabador que anota lo que le piden y no toca la pantalla."""

    def __init__(self):
        self.arranques = 0
        self.paradas = 0
        self.grabando = False

    def start(self):
        self.arranques += 1
        self.grabando = True
        return {"status": "recording"}

    def stop(self):
        self.paradas += 1
        self.grabando = False
        return {"status": "stopped"}

    def is_recording(self):
        return self.grabando

    def current_segment_path(self):
        return None


class HorarioFalso:
    """Un planificador que siempre quiere grabar."""

    def __init__(self, quiere=True):
        self.quiere = quiere
        self.horario = {"enabled": True}

    def intencion(self):
        return (self.quiere, "programada")

    def notify_state(self, _estado):
        pass

    def notify_manual(self, _estado):
        pass

    def fuente(self):
        return "programada"

    def clave_suprimida(self):
        return None

    def aplicar_supresion(self, _clave):
        pass

    def set_continuous(self, _valor):
        pass


def con_papel(agent, papel, quiere=True):
    """
    Deja el modulo listo para probar un papel concreto.

    Devuelve el grabador falso para poder preguntarle despues.
    """

    import owners

    grabador = GrabadorFalso()

    agent.AGENT_ROLE = papel
    agent.screen_recorder = grabador
    agent.recording_scheduler = HorarioFalso(quiere)
    agent.duenos = owners.Duenos()

    return grabador


# ==================================================================
# 1. LA CAUSA RAIZ
# ==================================================================

def test_el_servicio_no_graba_nunca():
    """
    Lo mas importante de todo el archivo.

    En la Sesion 0 no hay pantalla que capturar. Si el servicio
    arranca el grabador, el resultado es un archivo vacio que ademas
    nadie cierra.
    """

    import agent

    papel_real = agent.AGENT_ROLE

    try:

        grabador = con_papel(agent, agent.ROLE_SERVICE)

        resultado = agent._aplicar_decision_del_horario()

        comprobar("El servicio no arranca el grabador",
                  grabador.arranques == 0,
                  "arranco %d veces" % grabador.arranques)

        comprobar("Y lo dice no decidiendo nada",
                  resultado is None,
                  repr(resultado))

        # Por el camino de verdad: la orden que llega del servidor
        agent.apply_continuous(True)

        comprobar("Tampoco por 'set_continuous'",
                  grabador.arranques == 0,
                  "arranco %d veces" % grabador.arranques)

    finally:
        agent.AGENT_ROLE = papel_real


def test_el_ayudante_si_graba():
    """
    Y la otra mitad: que la proteccion no haya apagado la funcion.
    """

    import agent

    papel_real = agent.AGENT_ROLE

    try:

        grabador = con_papel(agent, agent.ROLE_HELPER)

        resultado = agent._aplicar_decision_del_horario()

        comprobar("El ayudante arranca el grabador",
                  grabador.arranques == 1,
                  "arranco %d veces" % grabador.arranques)

        comprobar("Y lo dice", resultado == "start", repr(resultado))

        # Termina la franja
        agent.recording_scheduler.quiere = False

        resultado = agent._aplicar_decision_del_horario()

        comprobar("Y la termina cuando toca",
                  grabador.paradas == 1 and resultado == "stop",
                  "paradas %d, %r" % (grabador.paradas, resultado))

    finally:
        agent.AGENT_ROLE = papel_real


def test_el_agent_de_una_pieza_sigue_grabando():
    """
    El Agent sin partir (standalone) ES su propio escritorio.

    Si la comprobacion se hubiera escrito como "solo el ayudante",
    este caso se quedaria sin grabar.
    """

    import agent

    papel_real = agent.AGENT_ROLE

    try:

        grabador = con_papel(agent, agent.ROLE_STANDALONE)

        agent._aplicar_decision_del_horario()

        comprobar("Standalone graba",
                  grabador.arranques == 1,
                  "arranco %d veces" % grabador.arranques)

    finally:
        agent.AGENT_ROLE = papel_real


# ==================================================================
# 2. EL HORARIO LLEGA A QUIEN LO CUMPLE
# ==================================================================

def test_el_ayudante_ACEPTA_el_horario_que_le_mandan():
    """
    El fallo que dejo a los siete equipos sin grabar tres dias.

    El servicio recibe el horario -guardarlo no necesita pantalla- y
    se lo pasa al ayudante, que es quien tiene que cumplirlo porque
    el grabador vive ahi.

    Pero el ayudante vuelve a comprobar cada orden que le llega, y
    lo hacia con requires_desktop. El horario NO es de escritorio,
    asi que lo descartaba nada mas recibirlo:

        [ayudante] Orden descartada: no es de escritorio (set_schedule)

    Esa linea salio en los siete equipos, decenas de veces, durante
    tres dias. El servicio habia dejado de grabar -que era lo
    correcto, en la Sesion 0 no hay pantalla- y el ayudante nunca
    recibia el horario. No grababa NADIE, y el panel no tenia forma
    de saberlo.
    """

    import commands

    for ajuste in ('set_schedule:{"enabled": true}',
                   'set_continuous:{"enabled": true}',
                   'schedule_suppression:{"slot": null}'):

        nombre = ajuste.split(":", 1)[0]

        # Siguen sin ser de escritorio: eso era correcto y no cambia.
        comprobar("%s no necesita pantalla" % nombre,
                  not commands.requires_desktop(ajuste))

        # Pero el ayudante SI tiene que atenderlos.
        comprobar("y aun asi el ayudante lo atiende" ,
                  commands.el_ayudante_debe_atender(ajuste),
                  "lo descartaria: " + ajuste)

    # La guarda del ayudante usa la comprobacion amplia, no la otra.
    comprobar("El ayudante comprueba con el_ayudante_debe_atender",
              "commands.el_ayudante_debe_atender(orden)" in AYUDANTE
              and "if not commands.requires_desktop(orden):" not in AYUDANTE)

    # Y sabe ejecutarlos: dejarlos pasar sin hacer nada seria el
    # mismo resultado con otra cara.
    comprobar("Y sabe aplicarlos",
              "agente.apply_schedule(horario)" in AYUDANTE
              and "agente.apply_continuous(" in AYUDANTE)

    comprobar("Por las mismas funciones que usaba el servicio",
              "apply_schedule" in AGENTE and "apply_continuous" in AGENTE)

    # Lo que NO debe pasar: que el ayudante acepte cualquier cosa.
    # Sigue siendo una lista blanca.
    for ajena in ("get_processes:{}", "power_action:{}",
                  "store_recording:{}", "file_download_start:{}"):

        comprobar("El ayudante sigue sin atender %s" % ajena.split(":")[0],
                  not commands.el_ayudante_debe_atender(ajena))


def test_los_ajustes_llegan_al_ayudante():
    """
    El servicio los guarda porque sobrevive al cierre de sesion. El
    ayudante los cumple porque es el unico con pantalla. Asi que
    tienen que ir a los dos.
    """

    import agent

    enviados = []

    canal_real = agent._canal_ayudante

    class CanalFalso:
        hay_ayudante = True

        def enviar(self, mensaje):
            enviados.append(mensaje["command"])

    try:

        agent._canal_ayudante = CanalFalso()
        agent._ajustes_vigentes.clear()

        agent.recordar_ajuste('set_schedule:{"enabled": true}')
        agent.recordar_ajuste('set_continuous:{"enabled": false}')

        comprobar("El horario se le pasa al ayudante",
                  'set_schedule:{"enabled": true}' in enviados,
                  str(enviados))

        comprobar("Y la grabacion continua tambien",
                  'set_continuous:{"enabled": false}' in enviados)

        # --- y el que entra tarde ---
        #
        # El servidor manda el horario cuando se conecta el
        # SERVICIO. Si alguien inicia sesion tres horas despues, no
        # hay ninguna reconexion que lo repita.
        enviados.clear()

        agent.poner_al_dia_al_ayudante()

        comprobar("Un ayudante que entra tarde recibe el horario",
                  'set_schedule:{"enabled": true}' in enviados,
                  str(enviados))

        comprobar("Sin que nadie toque nada en el panel",
                  len(enviados) == 2,
                  str(enviados))

        # El orden importa: el horario lleva dentro la franja
        # suprimida, y mandar la supresion ANTES la borraria.
        agent.recordar_ajuste('schedule_suppression:{"slot": "x"}')

        enviados.clear()

        agent.poner_al_dia_al_ayudante()

        comprobar("La supresion va despues del horario",
                  enviados.index('schedule_suppression:{"slot": "x"}')
                  > enviados.index('set_schedule:{"enabled": true}'),
                  str(enviados))

    finally:
        agent._canal_ayudante = canal_real
        agent._ajustes_vigentes.clear()


def test_sin_ayudante_el_ajuste_no_se_pierde():
    """
    Un equipo encendido sin nadie dentro recibe su horario igual: lo
    guarda el servicio y se lo da al ayudante cuando aparezca.
    """

    import agent

    canal_real = agent._canal_ayudante

    try:

        agent._canal_ayudante = None
        agent._ajustes_vigentes.clear()

        # No revienta aunque no haya a quien mandarselo
        agent.recordar_ajuste('set_schedule:{"enabled": true}')

        comprobar("El ajuste queda guardado",
                  agent._ajustes_vigentes.get("set_schedule:")
                  == 'set_schedule:{"enabled": true}',
                  str(agent._ajustes_vigentes))

    finally:
        agent._canal_ayudante = canal_real
        agent._ajustes_vigentes.clear()


# ==================================================================
# 3. LO QUE EL AYUDANTE CUENTA, SALE
# ==================================================================

def test_lo_que_produce_el_ayudante_no_se_pierde():
    """
    El sintoma mas callado de los tres.

    El ayudante no tiene WebSocket: la conexion la tiene el
    servicio. send_ws_threadsafe miraba _ws, lo encontraba en None y
    se iba sin hacer nada. Todo lo que el ayudante tenia que contar
    -que esta grabando, que hay grabaciones guardadas en el equipo,
    que el operador suprimio una franja- se perdia en silencio.
    """

    import agent

    salidas = []

    try:

        comprobar("Sin emisor y sin WebSocket, no sale nada",
                  agent.send_ws_threadsafe("recording_status:{}") is False)

        agent.registrar_emisor(lambda carga: salidas.append(carga) or True)

        comprobar("Con emisor, sale",
                  agent.send_ws_threadsafe("recording_status:{}") is True)

        comprobar("Y sale lo que era",
                  salidas == ["recording_status:{}"],
                  str(salidas))

        # Un emisor roto no puede tumbar al grabador: lo que va por
        # aqui es informativo.
        def se_rompe(_carga):
            raise RuntimeError("canal caido")

        agent.registrar_emisor(se_rompe)

        comprobar("Un canal caido no revienta a quien informa",
                  agent.send_ws_threadsafe("recording_status:{}") is False)

    finally:
        agent.registrar_emisor(None)


def test_el_ayudante_engancha_su_salida_al_arrancar():

    comprobar("El ayudante registra por donde sale lo suyo",
              "self.agente.registrar_emisor(self.mandar_al_servidor)"
              in AYUDANTE)

    # Y lo hace al arrancar, no al atender la primera orden: el
    # grabador puede tener algo que contar antes de que llegue
    # ninguna.
    arranque = AYUDANTE.split("def correr(self):", 1)[1]

    comprobar("Dentro de correr()", "registrar_emisor" in arranque)

    comprobar("Y antes del bucle de reconexion",
              arranque.index("registrar_emisor")
              < arranque.index("while True:"),
              "se registra despues de entrar al bucle")


def test_el_estado_lo_cuenta_quien_graba():
    """
    Si contestara el servicio, diria "no se esta grabando" justo
    cuando el ayudante acaba de empezar, y el panel se lo creeria
    por ser el ultimo en hablar.
    """

    comprobar("El servicio no contesta el estado al recibir el horario",
              AGENTE.count("if AGENT_ROLE != ROLE_SERVICE:") >= 3,
              "aparece %d veces"
              % AGENTE.count("if AGENT_ROLE != ROLE_SERVICE:"))

    comprobar("El ayudante contesta al detener",
              "agente.report_status_threadsafe()" in AYUDANTE)

    comprobar("Y al empezar y terminar la franja",
              AGENTE.count("report_status_threadsafe()") >= 3,
              "aparece %d veces"
              % AGENTE.count("report_status_threadsafe()"))


def test_detener_y_grabar_van_al_mismo_proceso():
    """
    El origen del boton que no detenia nada.
    """

    import commands

    comprobar("Grabar va al escritorio",
              commands.requires_desktop("start_recording"))

    comprobar("Detener tambien",
              commands.requires_desktop("stop_recording"))

    # Y el horario, que es la tercera forma de arrancar la captura,
    # acaba en el mismo sitio aunque lo reciba el servicio.
    comprobar("Y el horario acaba ahi tambien",
              "set_schedule:" in str(agent_ajustes()),
              str(agent_ajustes()))


def agent_ajustes():

    import agent

    return agent.AJUSTES_DEL_AYUDANTE


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("QUIEN GRABA: EL SERVICIO NO, EL AYUDANTE SI")
    print("=" * 66)
    print()

    pruebas = [
        test_el_servicio_no_graba_nunca,
        test_el_ayudante_si_graba,
        test_el_agent_de_una_pieza_sigue_grabando,
        test_el_ayudante_ACEPTA_el_horario_que_le_mandan,
        test_los_ajustes_llegan_al_ayudante,
        test_sin_ayudante_el_ajuste_no_se_pierde,
        test_lo_que_produce_el_ayudante_no_se_pierde,
        test_el_ayudante_engancha_su_salida_al_arrancar,
        test_el_estado_lo_cuenta_quien_graba,
        test_detener_y_grabar_van_al_mismo_proceso,
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
