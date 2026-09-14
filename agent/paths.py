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


APP_NAME = "RemoteAdmin"

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


def get_logs_dir():
    return ensure_dir(
        os.path.join(get_data_dir(), "logs")
    )


def get_config_dir():
    return ensure_dir(
        os.path.join(get_data_dir(), "config")
    )
