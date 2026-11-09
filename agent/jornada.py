r"""
Une los segmentos de un dia en un solo video.

Lo que se guarda de cada dia y equipo es UN archivo, no treinta y seis.
Eso es lo que se pidio y es lo que una persona espera cuando dice "la
grabacion del lunes de Recepcion".

Por que entonces se graba en trozos
-----------------------------------

Porque un MP4 no se puede reproducir hasta que se cierra: el indice va
al FINAL del archivo. Si la jornada se grabara en un solo archivo
abierto desde las 08:00 y a las 16:00 alguien apaga el equipo de un
botonazo, se perderia el dia entero.

Con segmentos de quince minutos se pierde, como mucho, el ultimo
cuarto de hora. Y son kioscos: los apagan sin avisar.

Asi que se captura en trozos y se entrega uno solo. La union va aqui,
al terminar la jornada, cuando ya no hay nada que perder.

Como se une
-----------

Con el ffmpeg que el propio Agent ya trae (imageio_ffmpeg), y con
'-c copy': no se recodifica nada. Ni se pierde calidad ni se calienta
el equipo, y una jornada de nueve horas se une en segundos porque solo
se copian bytes y se reescribe el indice.

Lo que puede salir mal, y por eso se verifica
---------------------------------------------

El concatenador por copia exige que todos los trozos tengan los MISMOS
parametros de video. Si a mitad del dia cambio la resolucion de la
pantalla, los segmentos no encajan y ffmpeg produce un archivo
*reproducible pero truncado*, sin dar error.

Por eso nunca se borra por fe: se comprueba que el archivo del dia
dura lo que suman sus trozos. Si no cuadra, se conservan los
segmentos y el dia queda sin unir. Vale mas un dia en treinta y seis
pedazos que un dia perdido.
"""

import os
import re
import subprocess
import tempfile
import time

import imageio_ffmpeg


# Los segmentos que escribe el grabador. El archivo del dia se llama
# de otra forma a proposito: asi una segunda pasada no lo confunde con
# un trozo y lo mete dentro de si mismo.
PATRON_DE_SEGMENTO = re.compile(r"^rec_\d+\.mp4$")

PREFIJO_DE_JORNADA = "jornada_"


# Margen al comparar duraciones. La union por copia reescribe el
# indice y puede desviarse unas decimas por trozo; no es un error.
# Un 2% sobre nueve horas son once minutos, de sobra para el ruido y
# muy lejos de lo que falla de verdad -ahi se pierden horas, no
# minutos-.
TOLERANCIA = 0.02

# Si la union tarda mas que esto, algo va mal: copiar bytes de una
# jornada son segundos.
PLAZO_DE_UNION = 600

PLAZO_DE_MEDIDA = 60


_DURACION = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d\.\d+)")

# Codec y tamano del flujo de video, de la linea 'Stream #0:0 ...'
_FLUJO = re.compile(r"Stream #\d+:\d+.*?Video:\s*(\w+).*?(\d{2,5})x(\d{2,5})")


def _sin_ventana():
    """Para que ffmpeg no abra una consola negra en el escritorio."""

    if os.name != "nt":
        return {}

    return {"creationflags": 0x08000000}


def segmentos_del_dia(carpeta):
    """
    Los segmentos de esa carpeta, en orden de grabacion.

    Se ordenan por nombre y no por fecha del archivo: el nombre lleva
    la marca de tiempo del inicio, que es el orden real de lo grabado.
    La fecha del archivo cambia si algo lo toca despues.
    """

    try:
        nombres = os.listdir(carpeta)

    except OSError:
        return []

    trozos = [
        os.path.join(carpeta, n)
        for n in nombres
        if PATRON_DE_SEGMENTO.match(n)
    ]

    return sorted(trozos)


def duracion(ruta):
    """
    Segundos que dura un video, o None si no se puede medir.

    Se lee con el propio ffmpeg -no hace falta ffprobe, que no viene
    en el paquete- pidiendole informacion del archivo y leyendo la
    linea 'Duration' de su salida de error.
    """

    try:

        resultado = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", ruta],
            capture_output=True,
            text=True,
            timeout=PLAZO_DE_MEDIDA,
            **_sin_ventana()
        )

    except (OSError, subprocess.SubprocessError):
        return None

    # ffmpeg sale con error porque no se le pidio ninguna salida: eso
    # es lo normal aqui y no significa que el archivo este mal.
    encaje = _DURACION.search(resultado.stderr or "")

    if not encaje:
        return None

    horas, minutos, segundos = encaje.groups()

    return int(horas) * 3600 + int(minutos) * 60 + float(segundos)


def parametros(ruta):
    """
    (codec, ancho, alto) del video, o None si no se puede leer.

    Es lo que tiene que coincidir entre todos los trozos para poder
    unirlos copiando.
    """

    try:

        resultado = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", ruta],
            capture_output=True,
            text=True,
            timeout=PLAZO_DE_MEDIDA,
            **_sin_ventana()
        )

    except (OSError, subprocess.SubprocessError):
        return None

    encaje = _FLUJO.search(resultado.stderr or "")

    if not encaje:
        return None

    codec, ancho, alto = encaje.groups()

    return (codec, int(ancho), int(alto))


def mismos_parametros(segmentos):
    """
    True si todos los trozos se pueden unir copiando.

    Por que hace falta mirar esto ANTES de unir, y no basta con
    comprobar la duracion despues
    ----------------------------------------------------------------

    Porque el contenedor conserva las marcas de tiempo de cada trozo
    aunque el video no se pueda descodificar. Si a media jornada
    cambia la resolucion, ffmpeg copia los dos flujos, devuelve 0, y
    el archivo resultante DURA lo que debe: la comprobacion de
    duracion lo da por bueno y se borran unos segmentos que eran la
    unica copia legible.

    Esto no es una hipotesis: la prueba de resoluciones distintas
    pasaba por un defecto de la union, y en cuanto la union empezo a
    funcionar, el dia se unio mal y los trozos se borraron igual.
    """

    visto = None

    for trozo in segmentos:

        propios = parametros(trozo)

        if propios is None:
            return False, "no se pudieron leer los parametros de %s" % (
                os.path.basename(trozo)
            )

        if visto is None:
            visto = propios

        elif propios != visto:
            return False, (
                "%s es %s %dx%d y el resto %s %dx%d"
                % ((os.path.basename(trozo),) + propios + visto)
            )

    return True, ""


def _lista_para_ffmpeg(segmentos):
    """
    Archivo de lista que entiende el concatenador de ffmpeg.

    Las rutas van con barra normal incluso en Windows: el
    concatenador trata la barra invertida como escape y una ruta como
    C:\\...\\rec_1.mp4 se le rompe.

    Y las comillas simples del propio nombre se escapan, porque
    delimitan el campo.
    """

    mano, ruta = tempfile.mkstemp(prefix="jornada-", suffix=".txt")

    with os.fdopen(mano, "w", encoding="utf-8") as lista:

        for trozo in segmentos:
            limpia = os.path.abspath(trozo).replace("\\", "/")
            limpia = limpia.replace("'", "'\\''")
            lista.write("file '%s'\n" % limpia)

    return ruta


def unir(segmentos, destino):
    """
    Une los segmentos en 'destino' copiando, sin recodificar.

    Devuelve (True, "") o (False, motivo). No borra nada: unir y
    borrar son dos pasos separados a proposito, con una verificacion
    en medio.
    """

    if not segmentos:
        return False, "no hay segmentos que unir"

    # Antes de copiar nada: si los trozos no son homogeneos, unirlos
    # por copia produce un archivo que parece bueno y no lo es.
    compatibles, motivo = mismos_parametros(segmentos)

    if not compatibles:
        return False, motivo

    lista = _lista_para_ffmpeg(segmentos)

    # Se escribe a un temporal y se mueve al final: si la union se
    # corta por la mitad -un apagon, justo lo que esto quiere
    # resistir- no queda un archivo del dia a medias que parezca
    # bueno.
    a_medio_hacer = destino + ".parcial"

    try:

        resultado = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-loglevel", "error",
                "-f", "concat",
                # Sin esto rechaza las rutas absolutas.
                "-safe", "0",
                "-i", lista,
                "-c", "copy",
                # El destino se llama '.parcial' para que un archivo a
                # medio escribir no pase por bueno, y entonces ffmpeg
                # ya no puede deducir el formato por la extension. Hay
                # que decirselo.
                "-f", "mp4",
                "-movflags", "+faststart",
                a_medio_hacer
            ],
            capture_output=True,
            text=True,
            timeout=PLAZO_DE_UNION,
            **_sin_ventana()
        )

    except subprocess.TimeoutExpired:
        _borrar_sin_ruido(a_medio_hacer)
        return False, "la union tardo demasiado"

    except OSError as fallo:
        _borrar_sin_ruido(a_medio_hacer)
        return False, "no se pudo lanzar ffmpeg: %s" % fallo

    finally:
        _borrar_sin_ruido(lista)

    if resultado.returncode != 0:
        _borrar_sin_ruido(a_medio_hacer)
        return False, (resultado.stderr or "").strip()[:300] or "ffmpeg fallo"

    if not os.path.isfile(a_medio_hacer):
        return False, "ffmpeg no dejo ningun archivo"

    try:
        os.replace(a_medio_hacer, destino)

    except OSError as fallo:
        _borrar_sin_ruido(a_medio_hacer)
        return False, "no se pudo colocar el archivo del dia: %s" % fallo

    return True, ""


def cuadra(segmentos, unido):
    """
    True si el archivo del dia dura lo que suman sus trozos.

    Es la unica garantia real de que la union salio bien. Comparar
    tamanos no sirve: el archivo unido pesa algo distinto porque
    lleva un solo indice en vez de treinta y seis.

    Si alguna duracion no se puede medir se responde False: ante la
    duda, no se borra.
    """

    total = 0.0

    for trozo in segmentos:

        medida = duracion(trozo)

        if medida is None:
            return False, "no se pudo medir %s" % os.path.basename(trozo)

        total += medida

    resultante = duracion(unido)

    if resultante is None:
        return False, "no se pudo medir el archivo del dia"

    if total <= 0:
        return False, "los segmentos no suman ninguna duracion"

    desvio = abs(resultante - total) / total

    if desvio > TOLERANCIA:
        return False, (
            "el dia dura %.1f s y sus trozos suman %.1f s"
            % (resultante, total)
        )

    return True, ""


def _borrar_sin_ruido(ruta):

    try:
        os.remove(ruta)

    except OSError:
        pass


def ruta_de_la_jornada(carpeta, fecha):
    """Donde vive el video del dia: dentro de la carpeta de ese dia."""

    return os.path.join(carpeta, "%s%s.mp4" % (PREFIJO_DE_JORNADA, fecha))


def ya_esta_unida(carpeta, fecha):
    """True si ese dia ya tiene su video."""

    return os.path.isfile(ruta_de_la_jornada(carpeta, fecha))


def cerrar_la_jornada(carpeta, fecha, registrar=None):
    """
    Deja el dia en un solo archivo: une, verifica y borra los trozos.

    El orden no es negociable:

        1. unir a un temporal y colocarlo
        2. comprobar que la duracion cuadra
        3. SOLO entonces borrar los segmentos

    Si el paso 2 falla, los segmentos se quedan donde estan y el dia
    no se da por cerrado. Se podra reintentar. Unir mal y borrar es
    perder el dia; unir mal y no borrar es un dia en pedazos, que se
    sigue pudiendo ver.

    Devuelve un diccionario con lo que paso, para que quien lo llame
    pueda contarselo al servidor.
    """

    def avisar(texto):
        if registrar:
            registrar(texto)

    segmentos = segmentos_del_dia(carpeta)

    if not segmentos:
        return {"unida": False, "motivo": "sin segmentos"}

    destino = ruta_de_la_jornada(carpeta, fecha)

    # Un dia ya unido no se vuelve a unir: si quedan trozos junto a un
    # archivo del dia, es que el borrado se quedo a medias. Se retoma
    # desde la verificacion, no desde la union.
    if os.path.isfile(destino):

        bien, motivo = cuadra(segmentos, destino)

        if not bien:
            avisar("[jornada] %s ya estaba unida pero no cuadra: %s"
                   % (fecha, motivo))
            return {"unida": False, "motivo": motivo}

        borrados = _borrar_los_trozos(segmentos)

        avisar("[jornada] %s ya estaba unida; se retiraron %d trozos"
               % (fecha, borrados))

        return {
            "unida": True,
            "archivo": destino,
            "segmentos": segmentos,
            "borrados": borrados,
            "reintento": True
        }

    avisar("[jornada] Uniendo %s: %d segmentos" % (fecha, len(segmentos)))

    bien, motivo = unir(segmentos, destino)

    if not bien:
        avisar("[jornada] No se pudo unir %s: %s" % (fecha, motivo))
        return {"unida": False, "motivo": motivo}

    bien, motivo = cuadra(segmentos, destino)

    if not bien:
        # El archivo del dia se queda: no estorba, y permite ver que
        # salio mal sin volver a unir nueve horas.
        avisar("[jornada] %s unida pero NO cuadra, no se borra nada: %s"
               % (fecha, motivo))
        return {"unida": False, "motivo": motivo, "archivo": destino}

    borrados = _borrar_los_trozos(segmentos)

    avisar("[jornada] %s lista: %d trozos en un archivo (%d retirados)"
           % (fecha, len(segmentos), borrados))

    return {
        "unida": True,
        "archivo": destino,
        "segmentos": segmentos,
        "borrados": borrados,
        "reintento": False
    }


def _borrar_los_trozos(segmentos):
    """
    Retira los segmentos ya contenidos en el archivo del dia.

    Se cuenta cuantos se fueron: si alguno se resiste -lo tiene
    abierto un reproductor, por ejemplo- no se interrumpe nada. El
    dia ya esta a salvo en su archivo, y el trozo sobrante se
    retirara en la siguiente pasada.
    """

    borrados = 0

    for trozo in segmentos:

        try:
            os.remove(trozo)
            borrados += 1

        except OSError:
            pass

    return borrados


# ==============================
# QUE DIAS ESTAN LISTOS PARA CERRARSE
# ==============================
#
# El grabador deja los trozos en <equipo>\AAAA\MM\DD\, por fecha
# LOCAL del equipo. Esa es la fecha que entiende una persona cuando
# dice "la grabacion del lunes", y es la que se usa aqui.

# Un dia de hoy no se cierra hasta que lleva un rato sin crecer. El
# segmento dura quince minutos, asi que veinte deja margen de sobra
# para no pillar uno a medio escribir.
QUIETUD = 20 * 60


def dias_con_segmentos(raiz):
    r"""
    Los dias que tienen trozos sueltos: [(fecha, carpeta)].

    Se recorre AAAA\MM\DD y se descarta cualquier nombre que no sea
    el que escribe el grabador: en esa carpeta no deberia haber nada
    mas, y si lo hay, no es asunto de esto.
    """

    encontrados = []

    for anio in _subcarpetas(raiz, 4):

        for mes in _subcarpetas(os.path.join(raiz, anio), 2):

            carpeta_mes = os.path.join(raiz, anio, mes)

            for dia in _subcarpetas(carpeta_mes, 2):

                carpeta = os.path.join(carpeta_mes, dia)

                if segmentos_del_dia(carpeta):
                    encontrados.append(
                        ("%s-%s-%s" % (anio, mes, dia), carpeta)
                    )

    return sorted(encontrados)


def _subcarpetas(ruta, digitos):
    """Subcarpetas cuyo nombre son exactamente N digitos."""

    try:
        nombres = os.listdir(ruta)

    except OSError:
        return []

    return sorted(
        n for n in nombres
        if len(n) == digitos
        and n.isdigit()
        and os.path.isdir(os.path.join(ruta, n))
    )


def _ultimo_cambio(segmentos):
    """Cuando se escribio por ultima vez en el dia, o None."""

    marcas = []

    for trozo in segmentos:

        try:
            marcas.append(os.path.getmtime(trozo))

        except OSError:
            pass

    return max(marcas) if marcas else None


def dias_por_cerrar(raiz, hoy, dentro_de_la_franja, ahora=None,
                    quietud=QUIETUD):
    """
    Los dias que ya se pueden unir, y por que se deja fuera el resto.

    Un dia PASADO esta cerrado por definicion: nadie va a escribir
    mas en el. Se une siempre, y eso recupera solo lo que quedo a
    medias por un apagon o por un equipo que estuvo apagado a las
    cinco.

    El dia de HOY es el delicado, y pide las dos cosas:

        fuera de la franja      si estamos dentro del horario, la
                                jornada no ha terminado. Sin esto,
                                desactivar la continua media hora a
                                media manana se veria como "el dia
                                acabo" y se uniria una jornada que
                                todavia tenia seis horas por delante

        y sin crecer un rato    porque un segmento a medio escribir
                                no se puede unir: el MP4 no tiene
                                indice hasta que se cierra

    'dentro_de_la_franja' lo decide quien llama, que es el que tiene
    el horario. Aqui no se interpreta ningun horario: este modulo
    junta archivos.
    """

    if ahora is None:
        ahora = time.time()

    listos = []

    for fecha, carpeta in dias_con_segmentos(raiz):

        if fecha < hoy:
            listos.append((fecha, carpeta))
            continue

        if fecha > hoy:
            # Un dia en el futuro: el reloj del equipo se movio. No se
            # toca, que no se sabe si todavia va a escribir ahi.
            continue

        if dentro_de_la_franja:
            continue

        ultimo = _ultimo_cambio(segmentos_del_dia(carpeta))

        if ultimo is not None and (ahora - ultimo) >= quietud:
            listos.append((fecha, carpeta))

    return listos
