"""
Validación multimedia de las grabaciones (bloque F4).

F3 garantiza que el archivo llegó entero. Este módulo responde a otra
pregunta: ¿lo que llegó es realmente un vídeo utilizable?

Los dos casos reales que motivan esto, encontrados en la auditoría:

  - Una grabación de 3,1 MB sin la caja 'moov': ffmpeg no puede abrirla. El
    Agent murió antes de que ffmpeg escribiera el índice.
  - Una grabación de 261 bytes con contenedor perfecto pero CERO streams:
    la VM no capturó ni un fotograma. Un MP4 válido y completamente vacío.

Ninguno de los dos se detecta mirando el tamaño, así que la validación se
basa en la estructura y en los streams, no en los bytes.

Módulo aislado a propósito: la validación es la parte más delicada de F4 y
necesita pruebas sin levantar el backend entero.
"""

import os
import re
import subprocess

import imageio_ffmpeg


# ==============================
# PARÁMETROS
# ==============================

# Tiempo máximo que puede tardar ffmpeg en leer los metadatos.
#
# Solo se leen las cabeceras, no se decodifica el vídeo, así que un segmento
# de 15 minutos responde en menos de un segundo. 60 s deja muchísimo margen
# para un disco lento o un archivo grande, y a la vez impide que un archivo
# malicioso deje un proceso colgado para siempre.
FFMPEG_TIMEOUT_SECONDS = 60

# Tolerancia entre la duración declarada por el Agent y la real del MP4.
#
# No son la misma medida: el grabador cuenta tiempo de reloj entre el inicio
# y el cierre del segmento, mientras que ffmpeg cuenta fotogramas reales
# dividido por los fps. Cuando el equipo va cargado se descartan fotogramas y
# la duración real sale MENOR.
#
# En las grabaciones reales auditadas la desviación llegó al 17,3%. El 35%
# deja margen sobre ese peor caso observado sin dejar pasar disparates como
# una duración declarada de 999999 segundos.
DURATION_TOLERANCE_RATIO = 0.35

# Para segmentos muy cortos el porcentaje se queda en nada: 35% de 4 s es
# 1,4 s, y ahí un solo fotograma perdido ya desviaría demasiado.
DURATION_TOLERANCE_MIN_SECONDS = 3

# En el protocolo de subida, una duración declarada de CERO significa
# "no la sé", no "dura cero segundos".
#
# Son dos caminos los que la producen, y ninguno es un archivo defectuoso:
#
#   - el Agent construye la ficha escaneando la carpeta, y el sistema de
#     archivos solo sabe tamaño y fecha de modificación;
#   - el parámetro duration_sec del endpoint tiene 0 por defecto, así que un
#     Agent que no lo mande llega indistinguible de uno que mande 0.
#
# Tratarlo como "dura cero" rechazaba grabaciones perfectamente buenas. No se
# pierde ninguna protección al tratarlo como desconocido: la duración REAL la
# mide ffmpeg por su cuenta en el paso 7, y una grabación vacía o sin duración
# legible se rechaza allí sin mirar lo declarado.
#
# Un valor NEGATIVO es otra cosa: ninguno de los dos caminos lo produce, así
# que sigue siendo un dato malformado y se rechaza.
DECLARED_DURATION_UNKNOWN = 0

# Marca de contenedor MP4/ISO-BMFF: los bytes 4 a 8 de un MP4 son 'ftyp'.
FTYP_OFFSET = 4
FTYP_MARK = b"ftyp"


class ValidationResult:
    """
    Resultado estructurado de validar una grabación.

    Se devuelve siempre, tanto si es válida como si no: quien llama decide
    qué hacer, este módulo solo informa.
    """

    def __init__(self, valid, reason=None, duration=None, codec=None,
                 resolution=None, streams=0, detail=None):

        self.valid = valid
        self.reason = reason            # código corto y estable
        self.duration = duration        # duración real en segundos, o None
        self.codec = codec
        self.resolution = resolution
        self.streams = streams
        self.detail = detail            # texto de ffmpeg, para diagnóstico

    def as_dict(self):
        return {
            "valid": self.valid,
            "reason": self.reason,
            "duration": self.duration,
            "codec": self.codec,
            "resolution": self.resolution,
            "streams": self.streams
        }

    def __repr__(self):
        estado = "válida" if self.valid else f"inválida ({self.reason})"
        return f"<ValidationResult {estado} duración={self.duration}>"


def get_ffmpeg_path():
    """Ruta del ffmpeg que trae imageio-ffmpeg, o None si no está."""

    try:
        ruta = imageio_ffmpeg.get_ffmpeg_exe()

    except Exception:
        return None

    return ruta if ruta and os.path.exists(ruta) else None


def has_ftyp_header(path):
    """
    True si el archivo empieza como un MP4.

    Comprobación instantánea que descarta texto plano, imágenes y basura sin
    necesidad de lanzar un proceso.
    """

    try:
        with open(path, "rb") as handle:
            cabecera = handle.read(FTYP_OFFSET + len(FTYP_MARK))

    except OSError:
        return False

    return cabecera[FTYP_OFFSET:FTYP_OFFSET + len(FTYP_MARK)] == FTYP_MARK


def _run_ffmpeg(path, ffmpeg_path):
    """
    Lanza ffmpeg para que describa el archivo. Devuelve (ok, texto).

    Se usa 'ffmpeg -i archivo' sin salida: imprime los metadatos por stderr y
    termina. No decodifica el vídeo, así que es rápido incluso con archivos
    grandes.
    """

    opciones = {}

    if os.name == "nt":
        # Sin ventana de consola cuando el backend corre sin terminal
        opciones["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        proceso = subprocess.run(
            # -nostdin y stdin cerrado: sin esto ffmpeg puede quedarse
            # esperando una respuesta por teclado (por ejemplo al preguntar
            # si sobreescribe) y colgar la petición indefinidamente.
            [ffmpeg_path, "-hide_banner", "-nostdin", "-i", path],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            **opciones
        )

    except subprocess.TimeoutExpired:
        # subprocess.run ya mata el proceso al agotarse el tiempo, así que no
        # queda ningún ffmpeg colgado.
        return False, "timeout"

    except OSError as error:
        return False, f"no se pudo ejecutar ffmpeg: {error}"

    return True, proceso.stderr.decode("utf-8", "replace")


def _parse_duration(texto):
    """Duración en segundos a partir de la línea 'Duration:' de ffmpeg."""

    encontrado = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", texto)

    if not encontrado:
        return None

    horas, minutos, segundos = encontrado.groups()

    return int(horas) * 3600 + int(minutos) * 60 + float(segundos)


def _parse_video_stream(texto):
    """Códec y resolución del primer stream de vídeo, o (None, None)."""

    encontrado = re.search(
        r"Stream #\d+:\d+.*?:\s*Video:\s*(\w+).*?,\s*(\d+x\d+)",
        texto
    )

    if not encontrado:
        return None, None

    return encontrado.group(1), encontrado.group(2)


def declared_duration_is_known(declared):
    """
    True si el Agent dijo de verdad cuánto dura la grabación.

    Ausente y cero son ambos "no lo sé", por los motivos de arriba. Un
    negativo no es desconocido, es incorrecto, y no se declara conocido para
    que no entre en la comparación: validate_recording lo rechaza aparte.
    """

    if declared is None:
        return False

    return declared > DECLARED_DURATION_UNKNOWN


def duration_for_storage(validation, declared=None):
    """
    Duración en segundos que debe quedar guardada, o None si no se sabe.

    La columna duration_sec es INTEGER y en este esquema el 0 significa
    "desconocida", así que una grabación que SÍ tiene duración nunca debe
    guardarse como 0: por eso un resultado positivo se redondea con un
    suelo de 1 segundo en vez de dejar que un 0,4 se convierta en cero.

    El orden de preferencia es deliberado: ffmpeg mide el archivo que de
    verdad está en el servidor, mientras que lo declarado es lo que dijo un
    equipo remoto. Cuando hay medición, gana la medición.
    """

    medida = getattr(validation, "duration", None) if validation else None

    if medida is not None and medida > 0:
        return max(1, int(round(medida)))

    # Sin medición utilizable se conserva lo declarado, pero solo si el Agent
    # lo sabía. Si no, None: quien llama deja el valor que ya hubiera.
    if declared_duration_is_known(declared):
        return max(1, int(round(declared)))

    return None


def duration_within_tolerance(declared, actual):
    """
    True si la duración real encaja con la declarada.

    La tolerancia es el mayor entre el 35% de lo declarado y 3 segundos, por
    los motivos explicados arriba.
    """

    if actual is None:
        return False

    if not declared_duration_is_known(declared):
        # Sin una duración declarada que contrastar, esta función no puede
        # afirmar que encaje. Quien llama comprueba primero con
        # declared_duration_is_known si la comparación tiene sentido; devolver
        # False aquí evita que un uso descuidado la dé por buena.
        return False

    tolerancia = max(
        DURATION_TOLERANCE_MIN_SECONDS,
        declared * DURATION_TOLERANCE_RATIO
    )

    return abs(actual - declared) <= tolerancia


def validate_recording(path, declared_duration=None):
    """
    Comprueba que un archivo sea una grabación utilizable.

    Las comprobaciones van de más barata a más cara: primero el archivo,
    después la cabecera y solo al final se lanza ffmpeg.
    """

    # --- 1. El archivo existe y no está vacío ---
    if not os.path.isfile(path):
        return ValidationResult(False, "archivo_inexistente")

    tamano = os.path.getsize(path)

    if tamano == 0:
        return ValidationResult(False, "archivo_vacio")

    # --- 2. ¿Parece un MP4? ---
    # El tamaño NO se usa como criterio: un archivo pequeño puede ser válido.
    if not has_ftyp_header(path):
        return ValidationResult(
            False,
            "no_es_mp4",
            detail="el archivo no empieza con la caja 'ftyp'"
        )

    # --- 3. ffmpeg disponible ---
    ffmpeg_path = get_ffmpeg_path()

    if ffmpeg_path is None:
        # Sin herramienta no se puede afirmar que sea inválido. Se informa de
        # forma explícita y quien llama decide; nunca se descarta una
        # grabación por un problema del servidor.
        return ValidationResult(
            False,
            "validacion_no_disponible",
            detail="ffmpeg no está disponible en el servidor"
        )

    # --- 4. ffmpeg lee el archivo ---
    ejecutado, salida = _run_ffmpeg(path, ffmpeg_path)

    if not ejecutado:

        if salida == "timeout":
            return ValidationResult(
                False,
                "timeout_validacion",
                detail=f"ffmpeg superó los {FFMPEG_TIMEOUT_SECONDS} s"
            )

        return ValidationResult(False, "validacion_no_disponible", detail=salida)

    # --- 5. ¿Se puede abrir el contenedor? ---
    if "moov atom not found" in salida:
        return ValidationResult(
            False,
            "contenedor_incompleto",
            detail="falta el índice 'moov': la grabación no se cerró bien",
            streams=0
        )

    if "Invalid data found" in salida:
        return ValidationResult(
            False,
            "contenedor_invalido",
            detail="ffmpeg no reconoce el contenido como un vídeo",
            streams=0
        )

    # --- 6. ¿Hay al menos un stream de vídeo? ---
    codec, resolucion = _parse_video_stream(salida)

    numero_streams = len(re.findall(r"Stream #\d+:\d+", salida))

    if codec is None:
        return ValidationResult(
            False,
            "sin_video",
            detail="el contenedor es válido pero no contiene vídeo",
            streams=numero_streams
        )

    # --- 7. Duración real ---
    duracion = _parse_duration(salida)

    if duracion is None:
        return ValidationResult(
            False,
            "sin_duracion",
            codec=codec,
            resolution=resolucion,
            streams=numero_streams,
            detail="ffmpeg no pudo determinar la duración"
        )

    if duracion <= 0:
        return ValidationResult(
            False,
            "duracion_nula",
            duration=duracion,
            codec=codec,
            resolution=resolucion,
            streams=numero_streams
        )

    # --- 8. ¿Coincide con lo declarado? ---
    #
    # Llegados aquí el archivo ya está validado por sí mismo: es un MP4 que
    # ffmpeg abre, con vídeo y con una duración real mayor que cero. Esta
    # comprobación solo contrasta ese dato con lo que dijo el Agent, así que
    # cuando el Agent no lo sabe simplemente no hay nada que contrastar.
    if declared_duration is not None:

        if declared_duration < DECLARED_DURATION_UNKNOWN:
            return ValidationResult(
                False,
                "duracion_declarada_invalida",
                duration=duracion,
                codec=codec,
                resolution=resolucion,
                streams=numero_streams,
                detail=f"el Agent declaró {declared_duration} s"
            )

        contrastable = declared_duration_is_known(declared_duration)

        if contrastable and not duration_within_tolerance(
                declared_duration, duracion):
            return ValidationResult(
                False,
                "duracion_no_coincide",
                duration=duracion,
                codec=codec,
                resolution=resolucion,
                streams=numero_streams,
                detail=(
                    f"declarada {declared_duration} s, real {duracion:.2f} s"
                )
            )

    return ValidationResult(
        True,
        duration=duracion,
        codec=codec,
        resolution=resolucion,
        streams=numero_streams
    )
