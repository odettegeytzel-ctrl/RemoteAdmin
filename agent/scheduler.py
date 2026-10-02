"""
Decision local de cuando grabar segun el horario programado.

La toma el Agent, no el servidor, por dos razones: es el unico que sabe
que hora es de verdad en esta maquina, y debe seguir funcionando con el
panel cerrado y hasta sin conexion. El servidor solo manda el horario; a
partir de ahi, si el Agent se queda aislado, sigue cumpliendolo.

Esta en un modulo aparte, sin red ni grabador, para poder comprobar a
cualquier hora que a las 07:59 no graba y a las 08:00 si.

Convivencia con los botones de inicio y parada
----------------------------------------------
El mando manual tiene prioridad dentro de la franja en curso, pero no la
destruye:

  - parar a mano dentro de la franja -> no se reanuda hasta la siguiente;
  - arrancar a mano fuera de la franja -> graba, y la programacion lo
    detendra al llegar el final del siguiente tramo, no antes;
  - al terminar la franja, la excepcion se olvida y manana vuelve a
    cumplirse el horario.

Asi nadie se encuentra con que el programa le vuelve a arrancar la
grabacion treinta segundos despues de pararla.
"""

from datetime import datetime


DIAS_VALIDOS = (0, 1, 2, 3, 4, 5, 6)


def _minutos(texto, por_defecto):

    try:
        horas, minutos = str(texto).split(":")
        return int(horas) * 60 + int(minutos)

    except Exception:
        return por_defecto


def _zona(nombre):
    """Objeto de zona horaria, o None para usar la hora local del equipo."""

    if not nombre or nombre == "local":
        return None

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(nombre)

    except Exception:
        # Zona desconocida en esta maquina: mejor la hora local que no
        # grabar nunca.
        return None


def ahora_en_zona(horario, ahora=None):
    """Momento actual en la zona del horario."""

    zona = _zona((horario or {}).get("timezone"))

    if ahora is not None:

        if zona is None:
            return ahora

        if ahora.tzinfo is None:
            return ahora

        return ahora.astimezone(zona)

    return datetime.now(zona) if zona else datetime.now()


def inicio_de_la_franja(horario, momento):
    """
    Momento de inicio de la franja que contiene a 'momento', o None.

    Devolver el inicio (y no solo si o no) permite distinguir una franja
    de la siguiente: es lo que hace que una parada manual caduque sola
    cuando empieza el tramo de manana.
    """

    if not horario or not horario.get("enabled"):
        return None

    dias = horario.get("days") or []

    if isinstance(dias, str):
        dias = [int(d) for d in dias.split(",") if d.strip() != ""]

    if not dias:
        return None

    inicio = _minutos(horario.get("start_time"), 8 * 60)
    fin = _minutos(horario.get("end_time"), 17 * 60)

    minuto_actual = momento.hour * 60 + momento.minute
    dia_actual = momento.weekday()

    if inicio < fin:

        # Franja normal, dentro del mismo dia
        if dia_actual in dias and inicio <= minuto_actual < fin:
            return f"{momento.date().isoformat()}T{horario['start_time']}"

        return None

    # Franja que cruza la medianoche: el dia que cuenta es el del INICIO
    if dia_actual in dias and minuto_actual >= inicio:
        return f"{momento.date().isoformat()}T{horario['start_time']}"

    dia_anterior = (dia_actual - 1) % 7

    if dia_anterior in dias and minuto_actual < fin:

        from datetime import timedelta

        ayer = (momento - timedelta(days=1)).date().isoformat()

        return f"{ayer}T{horario['start_time']}"

    return None


def should_be_recording(horario, ahora=None):
    """True si, segun el horario, ahora mismo tocaria estar grabando."""

    momento = ahora_en_zona(horario, ahora)

    return inicio_de_la_franja(horario, momento) is not None


class RecordingScheduler:
    """
    Lleva la cuenta del horario y del mando manual.

    No graba ni para nada: solo responde 'start', 'stop' o None, y quien
    lo use decide que hacer. Asi se puede comprobar su comportamiento a lo
    largo de una semana entera en milisegundos.
    """

    def __init__(self, horario=None):

        self.horario = horario or {}

        # Franja en la que el operador decidio otra cosa. Mientras siga
        # siendo la franja en curso, la programacion no le lleva la
        # contraria.
        #
        # Hacen falta DOS campos y no uno: fuera de todo horario la franja
        # es None, que es tambien el valor de "no hay excepcion". Con un
        # solo campo, arrancar a mano un domingo se tomaba por "sin
        # excepcion" y la programacion detenia la grabacion acto seguido.
        self._excepcion_activa = False
        self._franja_con_excepcion = None

        # Dia en que se tomo la excepcion. Hace falta para las que se
        # toman FUERA de todo horario: ahi no hay franja que identificar,
        # asi que la referencia es la fecha. Sin esto, arrancar a mano un
        # domingo dejaba la programacion anulada para siempre.
        self._fecha_con_excepcion = None

        # Ultimo estado conocido de la grabacion
        self._grabando = False

    def set_schedule(self, horario):
        """
        Cambia el horario. Olvida la excepcion manual.

        Si alguien acaba de reprogramar desde el panel, lo que quiere es
        que mande el horario nuevo.
        """

        self.horario = horario or {}
        self._excepcion_activa = False
        self._franja_con_excepcion = None
        self._fecha_con_excepcion = None

    def notify_manual(self, grabando, ahora=None):
        """
        El operador ha pulsado iniciar o detener.

        Se anota la franja en curso como excepcion: la programacion no
        volvera a tocar nada hasta que empiece la siguiente.
        """

        momento = ahora_en_zona(self.horario, ahora)

        self._grabando = bool(grabando)
        self._excepcion_activa = True
        self._franja_con_excepcion = inicio_de_la_franja(
            self.horario, momento
        )
        self._fecha_con_excepcion = momento.date()

    def notify_state(self, grabando):
        """Estado real informado por el grabador, sin intervencion manual."""

        self._grabando = bool(grabando)

    def decide(self, ahora=None):
        """
        Que habria que hacer ahora mismo: 'start', 'stop' o None.

        None significa "esta como debe estar".
        """

        momento = ahora_en_zona(self.horario, ahora)

        franja = inicio_de_la_franja(self.horario, momento)

        if self._excepcion_activa:

            misma_franja = franja == self._franja_con_excepcion

            # Fuera de todo horario no hay franja que comparar: entonces
            # la excepcion dura lo que queda de dia. Dentro de una franja
            # manda su identidad, para que un turno de noche que cruza la
            # medianoche no se reanude solo al cambiar la fecha.
            if misma_franja and franja is None:
                misma_franja = momento.date() == self._fecha_con_excepcion

            if misma_franja:
                return None

            self._excepcion_activa = False
            self._franja_con_excepcion = None
            self._fecha_con_excepcion = None

        deberia = franja is not None

        if deberia and not self._grabando:
            return "start"

        if not deberia and self._grabando:
            return "stop"

        return None

    def estado(self, ahora=None):
        """
        Estado para la interfaz: 'grabando', 'programada' o 'detenida'.

        'programada' es el caso util: no esta grabando ahora, pero hay un
        horario activo que la arrancara.
        """

        if self._grabando:
            return "grabando"

        if self.horario.get("enabled"):
            return "programada"

        return "detenida"
