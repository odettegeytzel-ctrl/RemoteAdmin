"""
Protección y limpieza del almacenamiento local de grabaciones del Agent.

Resuelve dos problemas del almacenamiento local:

1. Los MP4 ya subidos no se borraban nunca, así que la carpeta crecía sin
   límite hasta llenar el disco del equipo gestionado.

2. Con el disco lleno, ffmpeg fallaba y el grabador abría otro segmento de
   inmediato, una y otra vez, sin pausa.

Vive en su propio módulo, sin dependencias de Windows ni de captura, para
poder probarlo de forma aislada: la eliminación de archivos es la operación
más delicada del Agent y necesita pruebas reproducibles.

El borrado está deliberadamente restringido: solo toca archivos que estén
DENTRO de la carpeta de grabaciones y cuyo nombre siga el patrón que genera
el propio grabador. Una ruta que venga de fuera nunca puede borrar otra cosa.
"""

import os
import re
import shutil

from paths import get_recordings_dir


# ==============================
# LÍMITES (configurables por entorno)
# ==============================

def _mb_desde_entorno(nombre, por_defecto_mb):

    try:
        valor = int(float(os.getenv(nombre, por_defecto_mb)))
    except (TypeError, ValueError):
        return por_defecto_mb * 1024 * 1024

    if valor <= 0:
        return por_defecto_mb * 1024 * 1024

    return valor * 1024 * 1024


# Tope del espacio que pueden ocupar las grabaciones locales.
# 5 GB: con los segmentos observados (~1,5-2,5 MB por minuto) da margen para
# más de un día de grabación continua pendiente de subir.
STORAGE_LIMIT_BYTES = _mb_desde_entorno("REMOTEADMIN_LOCAL_STORAGE_MB", 5120)

# Espacio libre que debe quedar SIEMPRE en el disco, al margen del tope
# anterior: el equipo gestionado no puede quedarse sin disco por grabar.
MIN_FREE_BYTES = _mb_desde_entorno("REMOTEADMIN_MIN_FREE_MB", 2048)

# Los nombres que genera el grabador: rec_<marca de tiempo>.mp4
MANAGED_NAME = re.compile(r"^rec_\d+\.mp4$")


def is_managed_recording(path):
    """
    True solo si la ruta es un archivo de grabación gestionado por RemoteAdmin.

    Dos condiciones, ambas obligatorias:
      - está dentro de la carpeta de grabaciones (comparando rutas reales,
        así que ni los enlaces simbólicos ni los '..' se escapan);
      - su nombre sigue el patrón del grabador.

    Es la única puerta al borrado: si esto devuelve False, no se borra nada.
    """

    if not path:
        return False

    base = os.path.realpath(str(get_recordings_dir()))
    objetivo = os.path.realpath(str(path))

    dentro = (
        objetivo == base
        or objetivo.startswith(base + os.sep)
    )

    if not dentro:
        return False

    return bool(MANAGED_NAME.match(os.path.basename(objetivo)))


def delete_recording_file(path, is_protected=None):
    """
    Borra un MP4 local ya subido. Devuelve True si se borró.

    is_protected: función opcional que recibe la ruta y devuelve True si el
    archivo NO debe tocarse todavía (por ejemplo, si sigue en la cola de
    pendientes o si es el segmento que se está grabando ahora mismo). Se
    comprueba justo antes de borrar, no antes, para reducir la ventana entre
    la comprobación y el borrado.
    """

    if not is_managed_recording(path):
        print(f"[storage] No se borra, no es una grabación gestionada: {path}")
        return False

    if is_protected is not None and is_protected(path):
        print(f"[storage] No se borra, sigue en uso o pendiente: {path}")
        return False

    try:
        os.remove(path)

    except FileNotFoundError:
        # Ya no estaba: el objetivo (que no ocupe espacio) se cumple igual
        return True

    except OSError as error:
        # No se silencia: si no se puede borrar, hay que saberlo
        print(f"[storage] No se pudo borrar {path}: {error}")
        return False

    print(f"[storage] Grabación local eliminada tras confirmarse la subida: "
          f"{os.path.basename(path)}")

    return True


# ==============================
# RETENCION LOCAL
# ==============================
#
# Desde el cambio de politica, una subida correcta YA NO borra la copia
# local: el equipo conserva sus grabaciones y es la retencion quien las
# retira cuando les llega la hora. El servidor manda cuantos dias se
# guardan y cuales estan marcadas para conservar.

def _antiguedad_en_dias(ruta, ahora=None):
    """Dias transcurridos desde la ultima modificacion del archivo."""

    import time as _time

    try:
        modificado = os.path.getmtime(ruta)
    except OSError:
        return None

    referencia = ahora if ahora is not None else _time.time()

    return (referencia - modificado) / 86400.0


def apply_local_retention(days, keep_names=None, is_protected=None,
                          is_pending=None, ahora=None):
    """
    Retira las grabaciones locales que ya han cumplido su tiempo.

    Un archivo solo se borra si pasa TODAS estas condiciones:

      - esta dentro de la carpeta administrada y se llama como un segmento
        (is_managed_recording: ni enlaces ni '..' se escapan);
      - no es el segmento que se esta escribiendo ahora mismo;
      - no esta pendiente de subir: mientras la cola lo necesite para un
        reintento, no se toca;
      - no esta marcado para conservar;
      - su antiguedad supera los dias configurados.

    Haber subido el archivo al servidor NO es motivo para borrarlo. La
    copia local se conserva hasta que le toque por antiguedad.

    Si 'days' no es un numero positivo no se borra nada: ante una
    configuracion rara, mejor gastar disco que perder grabaciones.
    """

    resumen = {
        "revisados": 0,
        "eliminados": 0,
        "conservados_por_keep": 0,
        "en_uso": 0,
        "pendientes": 0,
        "errores": 0
    }

    try:
        dias = float(days)
    except (TypeError, ValueError):
        return resumen

    if dias <= 0:
        return resumen

    protegidos = {str(n) for n in (keep_names or ())}

    base = str(get_recordings_dir())

    for raiz, _, archivos in os.walk(base):

        for nombre in archivos:

            ruta = os.path.join(raiz, nombre)

            # La misma puerta de siempre: fuera de la carpeta o con otro
            # nombre, no se toca.
            if not is_managed_recording(ruta):
                continue

            resumen["revisados"] += 1

            if nombre in protegidos:
                resumen["conservados_por_keep"] += 1
                continue

            if is_protected is not None and is_protected(ruta):
                resumen["en_uso"] += 1
                continue

            if is_pending is not None and is_pending(ruta):
                resumen["pendientes"] += 1
                continue

            antiguedad = _antiguedad_en_dias(ruta, ahora)

            if antiguedad is None or antiguedad < dias:
                continue

            # Se vuelve a comprobar justo antes de borrar: entre el
            # recorrido y este punto, el grabador puede haber abierto el
            # archivo o la cola puede haberlo reclamado.
            if delete_recording_file(ruta, is_protected=is_protected):
                resumen["eliminados"] += 1
            else:
                resumen["errores"] += 1

    if resumen["eliminados"]:
        print(
            f"[storage] Retencion local: {resumen['eliminados']} grabaciones "
            f"de mas de {dias:.0f} dias eliminadas"
        )

    return resumen


def recordings_size_bytes():
    """Espacio que ocupan ahora mismo las grabaciones locales."""

    total = 0
    base = str(get_recordings_dir())

    for raiz, _, archivos in os.walk(base):
        for nombre in archivos:
            try:
                total += os.path.getsize(os.path.join(raiz, nombre))
            except OSError:
                # Un archivo que desaparece a mitad del recuento no es un error
                continue

    return total


def free_space_bytes():
    """Espacio libre en el disco donde viven las grabaciones."""

    try:
        return shutil.disk_usage(str(get_recordings_dir())).free
    except OSError as error:
        print(f"[storage] No se pudo consultar el espacio libre: {error}")
        # Ante la duda se asume que hay sitio: es peor dejar de grabar por un
        # fallo al consultar que arriesgar un poco de disco.
        return None


def storage_blocked_reason():
    """
    Motivo por el que NO se debe abrir un segmento nuevo, o None si se puede.

    Devuelve un texto listo para registrar, con las cifras concretas, para que
    en el log quede claro por qué se pausó la grabación.
    """

    usado = recordings_size_bytes()

    if usado >= STORAGE_LIMIT_BYTES:
        return (
            "las grabaciones locales ocupan "
            f"{usado / 1048576:.0f} MB y el límite es "
            f"{STORAGE_LIMIT_BYTES / 1048576:.0f} MB"
        )

    libre = free_space_bytes()

    if libre is not None and libre < MIN_FREE_BYTES:
        return (
            f"solo quedan {libre / 1048576:.0f} MB libres en el disco y el "
            f"mínimo exigido es {MIN_FREE_BYTES / 1048576:.0f} MB"
        )

    return None


def can_start_new_segment():
    """True si hay sitio para abrir un segmento nuevo."""

    return storage_blocked_reason() is None
