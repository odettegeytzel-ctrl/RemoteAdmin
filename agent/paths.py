"""
Rutas persistentes del Agent de RemoteAdmin.

Todo lo que debe sobrevivir a una actualización del Agent (configuración,
token, identidad, logs, grabaciones, datos locales) vive en una carpeta de
datos SEPARADA del programa:

    C:\\ProgramData\\RemoteAdmin\\

Un futuro instalador/updater solo debe reemplazar el ejecutable del programa;
nunca debe tocar esta carpeta de datos.

Si la carpeta de datos no se puede crear (por permisos), se cae a una carpeta
'data' dentro del proyecto para no bloquear el funcionamiento (fallback).
"""

import os
import re


APP_NAME = "RemoteAdmin"

# Un identificador de equipo valido: letras, numeros, guion y guion bajo.
# El device_id lo genera el servidor en hexadecimal, asi que esto no
# estorba a ninguno real; su fin es que NUNCA se construya una ruta con
# algo que venga de fuera sin filtrar.
SAFE_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Carpeta para los equipos cuyo identificador aun no se conoce o no pasa
# el filtro. Tener donde dejarlo es mejor que perder la grabacion.
UNKNOWN_DEVICE_FOLDER = "sin-identificar"

# Raíz del proyecto (carpeta superior a agent\)
PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)


def _preferred_data_dir():

    # C:\ProgramData\RemoteAdmin (ubicación estable, fuera del programa)
    program_data = os.environ.get("ProgramData")

    if program_data:
        return os.path.join(program_data, APP_NAME)

    # Fallback si no existe la variable (entornos no estándar)
    return os.path.join(PROJECT_DIR, "data", APP_NAME)


def get_data_dir():

    primary = _preferred_data_dir()

    try:
        os.makedirs(primary, exist_ok=True)
        return primary

    except OSError:
        # Fallback dentro del proyecto para no interrumpir el Agent
        fallback = os.path.join(PROJECT_DIR, "data")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def get_recordings_dir():
    return ensure_dir(
        os.path.join(get_data_dir(), "recordings")
    )


def safe_device_folder(device_id):
    """
    Nombre de carpeta seguro para un equipo.

    Nunca se usa el hostname ni ningun texto de fuera para construir una
    ruta: un nombre como '..\\..\\Windows' convertiria el borrado de
    grabaciones en un borrado de cualquier cosa. Lo que no encaja en el
    patron se descarta entero, no se "arregla": arreglar una ruta
    maliciosa es como se cuelan la mitad de los fallos de este tipo.
    """

    nombre = str(device_id or "").strip()

    if not SAFE_DEVICE_ID.match(nombre):
        return UNKNOWN_DEVICE_FOLDER

    return nombre


def get_device_recordings_dir(device_id):
    """
    Carpeta dedicada de un equipo, creada si no existe:

        C:\\ProgramData\\RemoteAdmin\\recordings\\<device_id>\\

    Se comprueba ADEMAS que la ruta resultante siga dentro de la carpeta
    de grabaciones. El filtro del nombre ya deberia bastar; esta segunda
    comprobacion existe porque una sola barrera para un borrado recursivo
    es poca barrera.
    """

    base = get_recordings_dir()

    destino = os.path.join(base, safe_device_folder(device_id))

    real_base = os.path.realpath(base)
    real_destino = os.path.realpath(destino)

    if not (real_destino == real_base
            or real_destino.startswith(real_base + os.sep)):

        # No deberia ocurrir nunca con el filtro anterior. Si ocurre, se
        # cae a la carpeta neutra en lugar de escribir fuera.
        destino = os.path.join(base, UNKNOWN_DEVICE_FOLDER)

    return ensure_dir(destino)


def get_logs_dir():
    return ensure_dir(
        os.path.join(get_data_dir(), "logs")
    )


def get_config_dir():
    return ensure_dir(
        os.path.join(get_data_dir(), "config")
    )
