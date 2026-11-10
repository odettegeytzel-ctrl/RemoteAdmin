r"""
El horario se cumple SOLO, sin que nadie mande un mensaje.

    .venv\Scripts\python tests/test_el_horario_corre_solo.py

Este es el defecto que costo una semana de grabaciones perdidas, y
ninguna prueba lo vio porque todas comprobaban la DECISION del
horario -si a tal hora toca grabar- y ninguna comprobaba que alguien
preguntara.

El hilo que pregunta se arrancaba en agent.main():

    if AGENT_ROLE != ROLE_SERVICE:
        threading.Thread(target=_schedule_loop).start()

con un comentario que decia "el horario lo cumple el ayudante, que si
tiene escritorio". Correcto en la intencion y falso en los hechos: el
ayudante NO ejecuta agent.main(). Tiene el suyo en helper.py e
importa agent como biblioteca. Ese hilo no lo arrancaba nadie.

Lo que quedaba funcionando era un accidente: apply_schedule y
apply_continuous evaluan la decision al recibir el mensaje, asi que
la grabacion empezaba cuando el SERVIDOR mandaba sus ajustes -al
conectarse el equipo, o al tocar algo en el panel- y jamas a la hora
que decia el horario.

En el registro de un equipo real se ve sin margen de duda. Cada
"Empieza la franja" cae en el mismo segundo que un mensaje entrante:

    11-06 15:35:46  [agent] Grabacion continua ACTIVADA
    11-06 15:35:46  [horario] Empieza la franja (continua)

    11-09 10:51:33  [agent] Grabacion continua ACTIVADA
    11-09 10:51:33  [horario] Empieza la franja (continua)

Ni uno solo lo disparo el reloj. Y el 10 de noviembre, con el horario
de 08:00 a 17:00 recibido y guardado, a las 08:00 no paso nada.

Lo que se comprueba aqui NO es si el horario decide bien -de eso ya
hay pruebas de sobra- sino que exista alguien preguntando.
"""

import io
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))


AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

AYUDANTE = io.open(RAIZ / "agent" / "helper.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def prueba_el_ayudante_lo_arranca():
    """
    La correccion: quien tiene escritorio arranca el hilo.

    Se comprueba en helper.py y no en agent.py porque el problema era
    precisamente ese: estaba en el sitio equivocado.
    """

    comprobar(
        "el ayudante arranca el horario al conectarse",
        "self.agente.arrancar_el_horario()" in AYUDANTE
    )

    comprobar(
        "y lo hace en correr(), no en un sitio que no se ejecute",
        AYUDANTE.index("def correr(self):")
        < AYUDANTE.index("self.agente.arrancar_el_horario()")
    )

    comprobar(
        "existe la funcion en el Agent",
        "def arrancar_el_horario():" in AGENTE
    )


def prueba_no_se_arranca_dos_veces():
    """
    El ayudante se reconecta al servicio cada vez que el canal cae, y
    correr() vuelve a pasar por ahi. Dos hilos evaluando la misma
    decision se pisan: uno abre la grabacion y el otro la ve abierta
    a medias.
    """

    comprobar(
        "hay una marca para no repetirlo",
        "_horario_en_marcha = threading.Event()" in AGENTE
    )

    comprobar(
        "se comprueba antes de arrancar",
        "if _horario_en_marcha.is_set():" in AGENTE
    )


def prueba_el_servicio_nunca_lo_arranca():
    """
    El servicio vive en la Sesion 0: lo que capture ahi es una
    pantalla negra. Esto ya costo una tanda de videos de 261 bytes.
    """

    trozo = AGENTE.split("def arrancar_el_horario():", 1)[1]
    trozo = trozo.split("def _schedule_loop", 1)[0]

    comprobar(
        "el servicio se excluye explicitamente",
        "if AGENT_ROLE == ROLE_SERVICE:" in trozo
        and "return False" in trozo
    )


def prueba_el_bucle_sigue_preguntando():
    """
    Que el hilo exista no basta: tiene que preguntar una y otra vez.
    """

    trozo = AGENTE.split("def _schedule_loop():", 1)[1]
    trozo = trozo.split("\ndef ", 1)[0]

    comprobar(
        "el bucle no termina solo",
        "while True:" in trozo
    )

    comprobar(
        "pregunta por la decision del horario",
        "_aplicar_decision_del_horario()" in trozo
    )

    comprobar(
        "y espera entre vueltas",
        "SCHEDULE_CHECK_SECONDS" in trozo
    )

    comprobar(
        "un error en una vuelta no mata el hilo",
        "except Exception" in trozo
    )


def prueba_el_hilo_arranca_de_verdad():
    """
    La comprobacion que vale por todas las de texto: se arranca y se
    mira si el hilo existe.

    No se importa agent -arrastra wmi, pyautogui y mss- sino que se
    comprueba que la funcion es idempotente y que el papel decide,
    usando el modulo ya importado si se puede.
    """

    try:
        import agent as modulo

    except Exception as error:
        comprobar("se puede importar el Agent para probarlo de verdad",
                  False, error)
        return

    comprobar("se puede importar el Agent para probarlo de verdad", True)

    # El papel por defecto en una importacion suelta no es 'service',
    # asi que el hilo debe arrancar.
    primera = modulo.arrancar_el_horario()

    hilos = [h.name for h in __import__("threading").enumerate()]

    if modulo.AGENT_ROLE == modulo.ROLE_SERVICE:

        comprobar("como 'service', NO arranca", primera is False)
        comprobar("y no deja hilo del horario",
                  "RecordingSchedule" not in hilos, hilos)

    else:

        comprobar("arranca el hilo la primera vez", primera is True)

        comprobar("el hilo existe de verdad",
                  "RecordingSchedule" in hilos, hilos)

        comprobar("la segunda llamada no arranca otro",
                  modulo.arrancar_el_horario() is False)

        comprobar("y sigue habiendo uno solo",
                  [h.name for h in
                   __import__("threading").enumerate()].count(
                      "RecordingSchedule") == 1)


def principal():

    prueba_el_ayudante_lo_arranca()
    prueba_no_se_arranca_dos_veces()
    prueba_el_servicio_nunca_lo_arranca()
    prueba_el_bucle_sigue_preguntando()
    prueba_el_hilo_arranca_de_verdad()

    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  bien   %s" % nombre)
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
