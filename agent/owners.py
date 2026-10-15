"""
Quien sostiene la captura de pantalla en cada momento.

El problema que resuelve
------------------------

Habia un solo grabador global y dos modalidades usandolo:

    screen_recorder = ScreenRecorder(...)      # uno solo

    def start(self):
        if self._running:
            return {"status": "already_recording"}

Con eso, iniciar una grabacion manual mientras corria la programada no
hacia nada, y detener la manual paraba la captura ENTERA: la continua
moria con ella.

La solucion
-----------

Una sola captura, varios duenos. La pantalla se graba mientras al
menos una modalidad la reclame:

    tomar("programada")  ->  True   arranca la captura
    tomar("manual")      ->  False  ya estaba grabando, no se toca
    soltar("manual")     ->  False  la programada sigue sosteniendola
    soltar("programada") ->  True   ya no la quiere nadie: se para

No se duplica la captura. Grabar dos veces la misma pantalla seria el
doble de CPU y el doble de disco para obtener dos videos identicos.

Los tramos
----------

Un segmento de 15 minutos puede haber empezado sosteniendolo solo la
programada y terminar con las dos modalidades activas. Atribuirlo
entero a una de ellas seria mentir sobre la mitad del archivo.

Por eso se guardan TRAMOS: cada vez que cambia quien sostiene la
captura se cierra el tramo anterior y se abre otro. Despues se
pregunta que tramos solapan con un segmento y se obtiene el reparto
real, con sus horas.

El limite que hay que conocer
-----------------------------

Mientras las dos modalidades coinciden, el video es UNO. Son la misma
pantalla en el mismo instante: no existen dos archivos distintos que
separar. Los tramos dicen QUE PARTE de cada archivo corresponde a
cada modalidad, que es lo mas cerca que se puede estar de separarlas
sin grabar la pantalla dos veces.
"""

from datetime import datetime, timezone


# Las modalidades que pueden sostener la captura.
#
# 'programada' y 'continua' son distintas a proposito: la primera es
# una franja horaria, la segunda es "graba siempre que puedas". El
# planificador decide cual aplica; aqui solo se anota cual fue.
MANUAL = "manual"
PROGRAMADA = "programada"
CONTINUA = "continua"

ORIGENES = (MANUAL, PROGRAMADA, CONTINUA)

# Las que manda el horario. Soltarlas no debe tocar la manual.
DEL_HORARIO = (PROGRAMADA, CONTINUA)


def _ahora():
    return datetime.now(timezone.utc)


def _iso(momento):
    return momento.isoformat() if momento else None


class Duenos:
    """
    Lleva la cuenta de quien reclama la captura, y desde cuando.

    No graba ni para nada: responde si hay que arrancar o parar, y
    quien lo use decide que hacer. Asi se puede comprobar una jornada
    entera en milisegundos.
    """

    def __init__(self):

        # origen -> momento en que lo tomo
        self._activos = {}

        # Tramos cerrados y el abierto, como diccionarios:
        #   {"origen": ..., "desde": iso, "hasta": iso o None}
        self._tramos = []

    # ------------------------------------------------------------
    # TOMAR Y SOLTAR
    # ------------------------------------------------------------

    def tomar(self, origen, ahora=None):
        """
        Una modalidad reclama la captura.

        Devuelve True solo si hay que ARRANCARLA, es decir, si antes
        no la sostenia nadie. Si ya estaba grabando se devuelve False
        y la captura no se toca: ese es justo el caso que antes
        respondia 'already_recording' y dejaba la manual sin efecto.
        """

        if origen not in ORIGENES:
            raise ValueError("Origen desconocido: %r" % (origen,))

        if origen in self._activos:
            return False

        momento = ahora or _ahora()

        habia = bool(self._activos)

        self._activos[origen] = momento

        self._tramos.append({
            "origen": origen,
            "desde": _iso(momento),
            "hasta": None
        })

        return not habia

    def soltar(self, origen, ahora=None):
        """
        Una modalidad deja de reclamar la captura.

        Devuelve True solo si hay que PARARLA, o sea, si no queda
        ninguna otra sosteniendola. Soltar la manual mientras el
        horario sigue vigente devuelve False y la captura continua
        sin un solo fotograma perdido.
        """

        if origen not in self._activos:
            return False

        momento = ahora or _ahora()

        del self._activos[origen]

        for tramo in reversed(self._tramos):

            if tramo["origen"] == origen and tramo["hasta"] is None:
                tramo["hasta"] = _iso(momento)
                break

        return not self._activos

    def soltar_el_horario(self, ahora=None):
        """
        Suelta las modalidades del horario, nunca la manual.

        Es lo que se llama al terminar una franja: la manual, si la
        hay, no tiene por que enterarse.
        """

        parar = False

        for origen in DEL_HORARIO:

            if origen in self._activos:
                parar = self.soltar(origen, ahora) or parar

        return parar

    # ------------------------------------------------------------
    # CONSULTA
    # ------------------------------------------------------------

    def activos(self):
        """Los origenes que sostienen la captura, en orden estable."""

        return tuple(o for o in ORIGENES if o in self._activos)

    def sostiene(self, origen):
        return origen in self._activos

    def grabando(self):
        return bool(self._activos)

    def fuente_principal(self):
        """
        Como se resume el estado en una sola palabra.

        El horario manda sobre la manual: si las dos estan activas,
        lo que sostiene la grabacion "de fondo" es la programacion, y
        la manual es un añadido. Al reves daria a entender que parar
        la manual termina la grabacion, que es justo lo que ya no
        pasa.
        """

        for origen in (PROGRAMADA, CONTINUA, MANUAL):

            if origen in self._activos:
                return origen

        return None

    # ------------------------------------------------------------
    # TRAMOS
    # ------------------------------------------------------------

    def tramos(self, cerrar_en=None):
        """
        Todos los tramos. Los abiertos se cierran en 'cerrar_en' para
        poder informarlos sin esperar a que terminen.
        """

        cierre = _iso(cerrar_en or _ahora())

        salida = []

        for tramo in self._tramos:

            copia = dict(tramo)

            if copia["hasta"] is None:
                copia["hasta"] = cierre

            salida.append(copia)

        return salida

    def tramos_entre(self, desde, hasta):
        """
        Los tramos que solapan con [desde, hasta], recortados.

        Es lo que convierte "este archivo es manual" —que seria falso
        la mitad del tiempo— en "de 10:03 a 10:07 lo sostenian la
        programada y la manual; el resto, solo la programada".

        Las fechas entran y salen en ISO 8601, como el resto del
        catalogo de grabaciones.
        """

        if not desde or not hasta:
            return []

        inicio = _parsear(desde)
        fin = _parsear(hasta)

        if inicio is None or fin is None or fin <= inicio:
            return []

        salida = []

        for tramo in self.tramos(cerrar_en=fin):

            t_inicio = _parsear(tramo["desde"])
            t_fin = _parsear(tramo["hasta"])

            if t_inicio is None or t_fin is None:
                continue

            recorte_inicio = max(inicio, t_inicio)
            recorte_fin = min(fin, t_fin)

            if recorte_fin <= recorte_inicio:
                continue

            salida.append({
                "origen": tramo["origen"],
                "desde": _iso(recorte_inicio),
                "hasta": _iso(recorte_fin),
                "segundos": int(
                    (recorte_fin - recorte_inicio).total_seconds()
                )
            })

        return salida

    def olvidar_antes_de(self, momento):
        """
        Tira los tramos ya informados, para que la lista no crezca
        sin limite en un equipo que lleva semanas encendido.

        Nunca se tira un tramo abierto ni uno que termine despues del
        corte: se perderia la atribucion del segmento en curso.
        """

        corte = _parsear(momento) if isinstance(momento, str) else momento

        if corte is None:
            return 0

        antes = len(self._tramos)

        self._tramos = [
            t for t in self._tramos
            if t["hasta"] is None or _parsear(t["hasta"]) > corte
        ]

        return antes - len(self._tramos)

    def reconciliar(self, grabando_de_verdad, ahora=None):
        """
        Pone la cuenta al dia con lo que hace el grabador REALMENTE.

        Hace falta tras un reinicio del Agent, una reconexion o un
        fallo del grabador: si aqui consta que algo sostiene la
        captura y la captura no existe, el panel enseñaria "grabando"
        sobre una grabacion que no esta. Eso es un estado falso, y es
        peor que no informar.

        Devuelve True si hubo que corregir algo.
        """

        if grabando_de_verdad or not self._activos:
            return False

        momento = ahora or _ahora()

        for origen in list(self._activos):
            self.soltar(origen, momento)

        return True


def _parsear(texto):

    if not texto:
        return None

    try:
        momento = datetime.fromisoformat(str(texto))

    except (TypeError, ValueError):
        return None

    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)

    return momento
