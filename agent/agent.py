import asyncio
import hashlib
import platform
import socket
import ssl
import getpass
import os
import json
import subprocess
import base64
import io
import re
import urllib.parse
import threading
import time
from datetime import datetime, timezone

import psutil
import requests
import websockets
import wmi
import pyautogui
import mss

from PIL import Image

import ctypes

# Sin pausa entre llamadas: los eventos de arrastre llegan seguidos
pyautogui.PAUSE = 0
# El failsafe aborta al tocar la esquina (0,0) y rompe redimensionar
pyautogui.FAILSAFE = False

MOUSEEVENTF_MOVE = 0x0001


# El .env vive en la raíz del proyecto. Se carga por ruta absoluta para que
# funcione aunque el servicio arranque con otro directorio de trabajo.
from dotenv import load_dotenv

ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".env"
)

load_dotenv(ENV_PATH)

# Dirección del servidor RemoteAdmin (configurable para PCs remotas)
SERVER_URL = os.getenv(
    "REMOTEADMIN_SERVER",
    "http://127.0.0.1:8000"
).rstrip("/")

if SERVER_URL.startswith("https://"):
    WEBSOCKET_URL = "wss://" + SERVER_URL[len("https://"):] + "/ws/agent"
else:
    WEBSOCKET_URL = "ws://" + SERVER_URL[len("http://"):] + "/ws/agent"

# CA propia para validar el certificado del servidor (desarrollo con TLS local).
# Opcional: vacío = se usan las autoridades estándar del sistema.
#
# Hace falta porque requests valida con el paquete certifi y NO consulta el
# almacén de certificados de Windows, así que no reconoce una CA local aunque
# esté instalada en el sistema. Indicar la CA NO desactiva la verificación:
# solo añade en quién confiar; el certificado se sigue validando.
_ca_cert_setting = os.getenv("REMOTEADMIN_CA_CERT", "").strip()

if _ca_cert_setting:
    # Relativa a la raíz del proyecto: el Agent corre como tarea programada y
    # el directorio de trabajo no es fiable (mismo motivo que ENV_PATH).
    CA_CERT = os.path.normpath(
        os.path.join(
            os.path.dirname(ENV_PATH),
            _ca_cert_setting
        )
    )
else:
    CA_CERT = ""

if CA_CERT and not os.path.isfile(CA_CERT):

    message = (
        f"ERROR: REMOTEADMIN_CA_CERT apunta a un archivo que no existe:\n  {CA_CERT}\n"
        "Genera los certificados con: bash certs/generate-dev-cert.sh"
    )

    if SERVER_URL.startswith("https://"):
        # Con HTTPS la CA es imprescindible: fallar aquí evita un error TLS opaco
        raise SystemExit(message)

    print(message)
    print("El servidor es HTTP, se continúa sin usar la CA.")

    # Se descarta: dejarla apuntando a un archivo inexistente rompería requests
    CA_CERT = ""

# verify de requests: ruta de la CA si está configurada, o True (validación
# estándar). Nunca False: eso desactivaría la verificación.
REQUESTS_VERIFY = CA_CERT or True


def build_ssl_context():
    """
    Contexto TLS para el WebSocket (wss://).

    Devuelve None si no hay CA propia configurada, para que websockets use su
    contexto seguro por defecto. Con CA configurada se mantiene la verificación
    completa de certificado y nombre de host.
    """

    if not CA_CERT:
        return None

    return ssl.create_default_context(cafile=CA_CERT)


HEARTBEAT_INTERVAL = 10

# Token de este Agent: se configura en el .env, nunca en el código.
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")

if not AGENT_TOKEN:
    print(
        "ADVERTENCIA: falta AGENT_TOKEN en el .env. "
        "El servidor rechazará este Agent."
    )

# Grabación de pantalla tipo "cámara de seguridad".
# - El grabador (Fase 1) corre en su propio hilo: no depende del WebSocket, así
#   que si se cae la conexión la grabación LOCAL continúa.
# - Cada segmento cerrado se encola y se sube; si el servidor está caído queda
#   pendiente y se reintenta. El MP4 local NUNCA se borra hasta confirmarse.
from recorder import ScreenRecorder
from paths import get_data_dir, get_config_dir
import storage
import inventory

# ---- Estado del Agent (leído por el bucle asíncrono, escrito por hilos) ----
_state_lock = threading.Lock()
continuous_enabled = False
agent_state = "idle"          # idle | recording | uploading | offline | error
last_segment_at = None        # ISO del último segmento cerrado
last_upload_at = None         # ISO de la última subida confirmada

# ---- Cola de subidas pendientes (persistente en ProgramData) ----
_pending_lock = threading.Lock()
PENDING_DIR = os.path.join(get_data_dir(), "data")
os.makedirs(PENDING_DIR, exist_ok=True)
PENDING_FILE = os.path.join(PENDING_DIR, "pending_uploads.json")


def _load_pending():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []


def _save_pending(items):
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as handle:
            json.dump(items, handle)
    except OSError:
        pass


def _pending_count():
    with _pending_lock:
        return len(_load_pending())


def enqueue_pending(segment):
    with _pending_lock:
        items = _load_pending()
        if not any(it.get("path") == segment.get("path") for it in items):
            items.append({
                "path": segment.get("path"),
                "started_at": segment.get("started_at", ""),
                "ended_at": segment.get("ended_at", ""),
                "duration_sec": segment.get("duration_sec", 0)
            })
            _save_pending(items)


def _remove_pending(path):
    with _pending_lock:
        items = [it for it in _load_pending() if it.get("path") != path]
        _save_pending(items)


def _is_recording_protected(path):
    """
    True si un MP4 NO debe borrarse todavía.

    Solo protege el segmento que el grabador está escribiendo ahora mismo.

    NO se comprueba la cola de pendientes: el borrado ocurre justo después de
    que el backend confirme ESE archivo y antes de retirarlo de la cola, así
    que estar en la cola es lo esperado en ese momento. Comprobarlo aquí
    impediría todo borrado.

    La garantía de que no se borra algo sin subir viene del único punto de
    llamada: _process_pending() solo llama al borrado cuando _upload_one()
    devolvió True para ese archivo.
    """

    en_curso = screen_recorder.current_segment_path()

    if en_curso and os.path.normcase(os.path.abspath(en_curso)) == \
            os.path.normcase(os.path.abspath(path)):
        return True

    return False


def _delete_uploaded_recording(path):
    """
    Borra el MP4 local de un segmento ya confirmado por el backend.

    El borrado solo ocurre DESPUÉS de que el servidor haya respondido
    correctamente y solo sobre archivos gestionados por RemoteAdmin: la
    comprobación vive en storage.py y no admite rutas de fuera de la carpeta
    de grabaciones.
    """

    if not path:
        return False

    return storage.delete_recording_file(
        path,
        is_protected=_is_recording_protected
    )


# Códigos HTTP que merecen otro intento con exactamente la misma petición.
# El resto de errores 4xx son permanentes: repetir no los resuelve.
RETRYABLE_STATUS = {
    408,   # tiempo de espera agotado en el servidor
    425,   # demasiado pronto
    429,   # demasiadas peticiones
    500, 502, 503, 504, 507, 509
}


# Rutas cuya subida falló de forma permanente. Se dejan de reintentar, pero
# el archivo local NO se borra y la entrada sigue en la cola: la grabación no
# se pierde, simplemente espera a que alguien mire el log.
#
# En memoria a propósito: persistirlo cambiaría el formato de
# pending_uploads.json, que es terreno de F5. Tras reiniciar el Agent se
# reintenta una vez más y, si vuelve a fallar igual, se marca de nuevo.
_permanent_failures = set()
_permanent_lock = threading.Lock()


def mark_permanent_failure(path):

    with _permanent_lock:
        nuevo = path not in _permanent_failures
        _permanent_failures.add(path)

    return nuevo


def is_permanently_failed(path):

    with _permanent_lock:
        return path in _permanent_failures


def clear_permanent_failure(path):

    with _permanent_lock:
        _permanent_failures.discard(path)


def is_retryable_status(status_code):
    """
    True si conviene reintentar esta subida tal cual.

    Reintentables: fallos de red (no llegan aquí, se ven como excepción),
    408, 425, 429 y los 5xx: el servidor no pudo, pero podrá.

    NO reintentables: 401/403 (token inválido o ajeno), 400 (petición mal
    formada), 404 (dispositivo inexistente), 413 (archivo demasiado grande).
    Reintentar eso es gastar red y disco sin ninguna posibilidad de éxito.
    """

    if status_code in RETRYABLE_STATUS:
        return True

    # Cualquier otro 5xx desconocido también se reintenta
    return 500 <= status_code < 600


def _upload_one(segment):
    """Sube un segmento. Devuelve True si el servidor lo recibió (o ya lo tenía)."""

    path = segment.get("path")

    if not path or not os.path.exists(path):
        # El archivo local ya no está: se descarta de la cola
        return True

    nombre = os.path.basename(path)

    params = {
        "filename": os.path.basename(path),
        "started_at": segment.get("started_at", ""),
        "ended_at": segment.get("ended_at", ""),
        "duration_sec": segment.get("duration_sec", 0)
    }

    try:
        headers = device_auth_headers()

    except RuntimeError as error:
        # Sin identidad no se sube nada: el segmento queda pendiente y se
        # reintenta cuando el Agent esté enrolado.
        print(f"[subida] {error}")
        return False

    headers["Content-Type"] = "application/octet-stream"

    try:
        with open(path, "rb") as handle:
            response = requests.post(
                f"{SERVER_URL}/api/devices/{get_device_id()}/recordings/upload",
                params=params,
                data=handle,
                headers=headers,
                timeout=120,
                verify=REQUESTS_VERIFY
            )

    except requests.RequestException as error:
        # Red caída, DNS, TLS, timeout: siempre reintentable
        print(f"[subida] Error de red al subir {nombre}: {type(error).__name__}")
        return False

    if response.ok:

        # El servidor puede aceptar la subida y a la vez rechazar el
        # contenido: la grabación llegó entera pero no es un vídeo utilizable
        # (F4). Para la cola es una resolución definitiva igual que un éxito
        # —no hay nada que reintentar—, pero conviene que se vea en el log en
        # lugar de anunciarlo como una subida correcta.
        try:
            resultado = response.json()

        except ValueError:
            resultado = {}

        if resultado.get("status") == "invalid":
            print(
                f"[subida] {nombre}: el servidor la marcó como INVÁLIDA "
                f"({resultado.get('reason')}). Queda en cuarentena en el "
                "servidor; no se reintenta."
            )

        return True

    if is_retryable_status(response.status_code):
        print(
            f"[subida] {nombre}: el servidor respondió {response.status_code}, "
            "se reintentará"
        )
        return False

    # Error permanente: repetir la MISMA subida no lo va a arreglar, así que
    # se deja de reintentar.
    #
    # El archivo local NO se borra y la entrada sigue en la cola: perder la
    # grabación sería peor que ocupar disco. Simplemente deja de consumir red
    # cada 30 segundos y queda registrada para intervención manual.
    if mark_permanent_failure(path):
        print(
            f"[subida] ERROR PERMANENTE al subir {nombre}: "
            f"el servidor respondió {response.status_code}. "
            "Se deja de reintentar. El archivo local se conserva y sigue en "
            "la cola; requiere intervención manual."
        )

    return False


def _process_pending():
    """Reintenta todas las subidas pendientes. Actualiza el estado."""

    global last_upload_at, agent_state

    with _pending_lock:
        items = list(_load_pending())

    if not items:
        return

    with _state_lock:
        if agent_state == "recording":
            agent_state = "uploading"

    any_ok = False

    for segment in items:

        # Los fallos permanentes no se vuelven a intentar: el archivo sigue
        # en disco y en la cola, esperando intervención manual, pero no
        # gasta red ni llena el log cada 30 segundos.
        if is_permanently_failed(segment.get("path")):
            continue

        if _upload_one(segment):
            # Se borra el archivo ANTES de sacarlo de la cola. Si el borrado
            # falla (permisos, antivirus, archivo bloqueado), la entrada sigue
            # pendiente y el hilo de reintentos volverá a intentarlo: no hay
            # ningún barrido que recoja huérfanos, así que la cola es la única
            # garantía de que el archivo acabe eliminándose.
            #
            # El caso inverso también es seguro: si el borrado funciona pero el
            # proceso muere antes de limpiar la cola, en el siguiente arranque
            # _upload_one() ve que el archivo ya no existe y devuelve True, con
            # lo que la entrada se limpia sola.
            if _delete_uploaded_recording(segment.get("path")):
                _remove_pending(segment.get("path"))
            any_ok = True
            with _state_lock:
                last_upload_at = datetime.now(timezone.utc).isoformat()
            print(f"[agent] Grabación subida: {os.path.basename(segment.get('path',''))}")
        else:
            # Servidor no disponible: se deja pendiente y se marca offline
            with _state_lock:
                agent_state = "offline"
            break

    with _state_lock:
        if _pending_count() == 0 and screen_recorder.is_recording():
            agent_state = "recording"

    return any_ok


def on_segment_complete(segment):
    """Llamado por el hilo del grabador al cerrar cada segmento."""

    global last_segment_at

    with _state_lock:
        last_segment_at = datetime.now(timezone.utc).isoformat()

    enqueue_pending(segment)

    # Intento inmediato; si falla, el hilo de reintentos lo tomará luego
    _process_pending()

    report_status_threadsafe()


def _retry_loop():
    while True:
        try:
            _process_pending()
        except Exception as error:
            print(f"[agent] Error en reintento de subida: {error}")
        time.sleep(30)


screen_recorder = ScreenRecorder(
    on_segment_complete=on_segment_complete
)


def start_screen_recording():
    global agent_state
    result = screen_recorder.start()
    with _state_lock:
        agent_state = "recording"
    return result


def stop_screen_recording():
    global agent_state
    result = screen_recorder.stop()
    with _state_lock:
        if not screen_recorder.is_recording():
            agent_state = "idle"
    return result


def apply_continuous(enabled):
    """Aplica la configuración de grabación continua recibida del servidor."""

    global continuous_enabled

    with _state_lock:
        continuous_enabled = bool(enabled)

    if enabled:
        if not screen_recorder.is_recording():
            print("[agent] Grabación continua ACTIVADA: iniciando grabación")
            start_screen_recording()
    else:
        if screen_recorder.is_recording():
            print("[agent] Grabación continua DESACTIVADA: deteniendo grabación")
            stop_screen_recording()


def build_recording_status():
    with _state_lock:
        recording = screen_recorder.is_recording()
        state = agent_state
        return {
            "continuous_recording_enabled": continuous_enabled,
            "recording": recording,
            "state": state,
            "last_segment": last_segment_at,
            "last_upload": last_upload_at,
            "pending_uploads": _pending_count()
        }


# Referencia al WebSocket/loop para que los hilos reporten el estado en vivo
_event_loop = None
_ws = None


def report_status_threadsafe():
    if _event_loop is None or _ws is None:
        return
    try:
        payload = "recording_status:" + json.dumps(build_recording_status())
        asyncio.run_coroutine_threadsafe(_ws.send(payload), _event_loop)
    except Exception:
        pass

# Cabecera de ALTA: el AGENT_TOKEN compartido solo sirve para enrolarse.
# Una vez que el Agent tiene identidad propia, no vuelve a usarse.
AGENT_HEADERS = {
    "X-Agent-Token": AGENT_TOKEN
}


def device_auth_headers():
    """
    Cabecera de autenticación para las operaciones normales (heartbeat,
    subidas): siempre el token INDIVIDUAL de este dispositivo.

    Si no hay identidad, falla de forma explícita en vez de recurrir al token
    compartido: usarlo aquí sería volver a la credencial que estamos retirando.
    """

    token = get_agent_device_token()

    if not token:
        raise RuntimeError(
            "El Agent no tiene token individual (falta identity.json). "
            "Debe enrolarse antes de operar."
        )

    return {"X-Agent-Token": token}

screen_stream_task = None


# ==============================
# IDENTIDAD PERSISTENTE DEL AGENT
# ==============================
#
# El device_id y el token individual los asigna el SERVIDOR en el alta y se
# guardan aquí, en la carpeta de datos que sobrevive a actualizaciones del
# Agent (ver agent/paths.py). Mientras el archivo exista, el Agent conserva su
# identidad entre reinicios.
#
# Si el archivo se pierde, el Agent se da de alta como dispositivo NUEVO: no
# hay recuperación automática, y el hostname nunca sirve para reclamar la
# identidad de un equipo ya registrado.

IDENTITY_FILE = os.path.join(
    get_config_dir(),
    "identity.json"
)

# Identidad en memoria; se rellena al cargar el archivo o al darse de alta
_identity = None


def load_identity():
    """Lee la identidad guardada. Devuelve None si el Agent aún no tiene."""

    global _identity

    if _identity is not None:
        return _identity

    try:
        with open(IDENTITY_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)

    except (OSError, ValueError):
        return None

    if not data.get("device_id") or not data.get("agent_token"):
        return None

    _identity = data

    return _identity


def save_identity(device_id, agent_token):
    """
    Guarda la identidad recibida en el alta.

    Escritura atómica (temporal + reemplazo) para que un corte de luz no deje
    un archivo a medias que obligaría a darse de alta otra vez.
    """

    global _identity

    data = {
        "device_id": device_id,
        "agent_token": agent_token
    }

    temporary = IDENTITY_FILE + ".tmp"

    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle)

    os.replace(temporary, IDENTITY_FILE)

    _identity = data

    return data


def get_device_id():
    """
    device_id asignado por el servidor. None si el Agent todavía no se ha
    dado de alta (solo ocurre antes del primer registro correcto).
    """

    identity = load_identity()

    return identity["device_id"] if identity else None


def get_agent_device_token():
    """
    Token individual de este Agent. Autentica todas sus operaciones normales:
    heartbeat, WebSocket y subidas. El AGENT_TOKEN compartido solo se usa para
    el alta inicial.
    """

    identity = load_identity()

    return identity["agent_token"] if identity else None


def get_local_ip():
    try:
        connection = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        connection.connect(
            ("8.8.8.8", 80)
        )

        ip_address = connection.getsockname()[0]

        connection.close()

        return ip_address

    except Exception:
        return "127.0.0.1"


def register_device():
    """
    Da de alta el Agent o actualiza sus datos si ya tiene identidad.

    Sin identidad local: no se envía device_id y el servidor asigna uno, junto
    con el token individual, que se guardan para los reinicios siguientes.
    Con identidad: se envía el device_id guardado y solo se actualizan los
    datos del equipo.
    """

    device_id = get_device_id()

    data = {
        "hostname": socket.gethostname(),
        "operating_system": platform.platform(),
        "ip_address": get_local_ip()
    }

    # Con identidad: token individual. Sin identidad: AGENT_TOKEN, que es su
    # único uso legítimo (enrolamiento).
    if device_id:
        data["device_id"] = device_id
        headers = device_auth_headers()
    else:
        headers = AGENT_HEADERS

    response = requests.post(
        f"{SERVER_URL}/api/devices/register",
        json=data,
        headers=headers,
        timeout=10,
        verify=REQUESTS_VERIFY
    )

    response.raise_for_status()

    result = response.json()

    if not device_id:

        # Alta: el token individual solo llega en esta respuesta
        save_identity(
            result["device_id"],
            result["agent_token"]
        )

        print(f"Agent dado de alta con device_id: {result['device_id']}")
        print(f"Identidad guardada en: {IDENTITY_FILE}")

    else:
        print(f"Device registered: {device_id}")


def send_heartbeat():
    device_id = get_device_id()
    ip_address = get_local_ip()

    # El device_id sigue en el cuerpo por compatibilidad, pero el servidor
    # deriva la identidad del token, no de este campo.
    data = {
        "device_id": device_id,
        "ip_address": ip_address
    }

    response = requests.post(
        f"{SERVER_URL}/api/devices/heartbeat",
        json=data,
        headers=device_auth_headers(),
        timeout=10,
        verify=REQUESTS_VERIFY
    )

    response.raise_for_status()

    print("Heartbeat sent")


def get_computer_info():

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_ComputerSystem | "
        "Select-Object Manufacturer, Model | "
        "ConvertTo-Json"
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10
    )

    computer_info = json.loads(
        result.stdout
    )

    return {
        "manufacturer": computer_info.get(
            "Manufacturer"
        ),
        "model": computer_info.get(
            "Model"
        )
    }


def get_installed_software():

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        'Get-ItemProperty '
        '"HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*", '
        '"HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*" '
        '| Where-Object { $_.DisplayName } '
        '| Select-Object DisplayName, DisplayVersion, Publisher, InstallDate '
        '| ConvertTo-Json'
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=20
    )

    if not result.stdout.strip():
        return []

    software = json.loads(
        result.stdout
    )

    if isinstance(
        software,
        dict
    ):
        software = [software]

    return [
        {
            "name": item.get("DisplayName"),
            "version": item.get("DisplayVersion"),
            "publisher": item.get("Publisher"),
            "install_date": item.get("InstallDate")
        }
        for item in software
    ]


def get_system_info():

    memory = psutil.virtual_memory()

    computer_info = get_computer_info()

    disks = []

    for partition in psutil.disk_partitions():

        try:

            usage = psutil.disk_usage(
                partition.mountpoint
            )

            disks.append({
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "filesystem": partition.fstype,
                "total": usage.total,
                "free": usage.free,
                "used": usage.used
            })

        except PermissionError:
            continue

    return {
        "hostname": socket.gethostname(),
        "username": getpass.getuser(),
        "operating_system": platform.platform(),
        "ip_address": get_local_ip(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "ram_total": memory.total,
        "ram_available": memory.available,
        "ram_used": memory.used,
        "ram_percent": memory.percent,
        "disks": disks,
        "windows_version": platform.version(),
        "architecture": platform.machine(),
        "manufacturer": computer_info["manufacturer"],
        "model": computer_info["model"]
    }


def get_hardware_info():

    memory = psutil.virtual_memory()

    disks = []

    for partition in psutil.disk_partitions():

        try:

            usage = psutil.disk_usage(
                partition.mountpoint
            )

            disks.append({
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "filesystem": partition.fstype,
                "total": usage.total,
                "free": usage.free,
                "used": usage.used
            })

        except PermissionError:
            continue

    return {
        "ram_total": memory.total,
        "ram_available": memory.available,
        "ram_used": memory.used,
        "ram_percent": memory.percent,
        "disks": disks
    }


# Datos de la ultima captura para convertir coordenadas imagen <-> pantalla real
screen_geometry = {
    "left": 0,
    "top": 0,
    "real_width": 1,
    "real_height": 1,
    "width": 1,
    "height": 1
}


def click_mouse(button="left"):

    pyautogui.click(
        button=button
    )


pressed_buttons = set()


def move_mouse(x, y):

    # Las coordenadas llegan en pixeles de la imagen enviada
    scale_x = screen_geometry["real_width"] / screen_geometry["width"]
    scale_y = screen_geometry["real_height"] / screen_geometry["height"]

    pyautogui.moveTo(
        round(screen_geometry["left"] + x * scale_x),
        round(screen_geometry["top"] + y * scale_y),
        duration=0
    )

    if pressed_buttons:
        # SetCursorPos no genera entrada real; este movimiento nulo si,
        # necesario para arrastrar archivos (OLE) y el umbral de arrastre
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)


def mouse_down(x, y, button="left"):

    # Mueve a la posicion y mantiene presionado el boton
    move_mouse(x, y)

    pyautogui.mouseDown(
        button=button
    )

    pressed_buttons.add(button)


def mouse_up(x, y, button="left"):

    # Mueve a la posicion (si llega) y suelta el boton
    if x is not None and y is not None:
        move_mouse(x, y)

    pyautogui.mouseUp(
        button=button
    )

    pressed_buttons.discard(button)


KEY_ALIASES = {
    "control": "ctrl",
    "escape": "esc",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "windows": "win",
    "meta": "win",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right"
}

# Teclas que siguen presionadas en este equipo (para no dejarlas atascadas)
held_keys = set()


def normalize_key(key):

    # Solo nombres de teclas validos de PyAutoGUI; nunca se ejecuta nada
    if not isinstance(key, str) or not key:
        return None

    if len(key) == 1:
        return key if key.isprintable() else None

    name = key.lower()
    name = KEY_ALIASES.get(name, name)

    if name in pyautogui.KEYBOARD_KEYS:
        return name

    return None


def press_key(key):

    key = normalize_key(key)

    if key is None:
        return

    pyautogui.press(
        key
    )


def key_down(key):

    key = normalize_key(key)

    if key is None:
        return

    pyautogui.keyDown(
        key
    )

    held_keys.add(key)


def key_up(key):

    key = normalize_key(key)

    if key is None:
        return

    pyautogui.keyUp(
        key
    )

    held_keys.discard(key)


def hotkey(keys):

    if not isinstance(keys, list):
        return

    normalized = [normalize_key(key) for key in keys]

    if not normalized or None in normalized:
        return

    pyautogui.hotkey(
        *normalized
    )


def release_all_keys():

    for key in list(held_keys):

        try:
            pyautogui.keyUp(key)
        except Exception:
            pass

    held_keys.clear()


def handle_keyboard(data):

    action = data.get("action", "press")

    if action == "press":
        press_key(data.get("key"))

    elif action == "down":
        key_down(data.get("key"))

    elif action == "up":
        key_up(data.get("key"))

    elif action == "hotkey":
        hotkey(data.get("keys"))

    elif action == "release_all":
        release_all_keys()


class POINT(ctypes.Structure):

    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long)
    ]


class CURSORINFO(ctypes.Structure):

    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", POINT)
    ]


CURSOR_SHOWING = 0x00000001

user32 = ctypes.windll.user32

user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = ctypes.c_bool
user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.LoadCursorW.restype = ctypes.c_void_p

# Los cursores estandar son compartidos por todo el sistema:
# basta comparar el handle actual con estos
SYSTEM_CURSORS = {
    32512: "arrow",        # IDC_ARROW
    32644: "ew",           # IDC_SIZEWE
    32645: "ns",           # IDC_SIZENS
    32642: "nwse",         # IDC_SIZENWSE
    32643: "nesw",         # IDC_SIZENESW
    32646: "move",         # IDC_SIZEALL
    32513: "text",         # IDC_IBEAM
    32649: "pointer",      # IDC_HAND
    32514: "wait",         # IDC_WAIT
    32650: "progress",     # IDC_APPSTARTING
    32648: "not-allowed",  # IDC_NO
    32515: "crosshair",    # IDC_CROSS
    32651: "help"          # IDC_HELP
}

cursor_handles = {}

for cursor_id, cursor_name in SYSTEM_CURSORS.items():

    handle = user32.LoadCursorW(None, ctypes.c_void_p(cursor_id))

    if handle:
        cursor_handles[handle] = cursor_name


def get_cursor_type():

    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)

    if not user32.GetCursorInfo(ctypes.byref(info)):
        return "arrow"

    if not info.flags & CURSOR_SHOWING or not info.hCursor:
        return "hidden"

    return cursor_handles.get(info.hCursor, "arrow")


def get_mouse_position():

    position = pyautogui.position()

    # Convierte la posicion real a pixeles de la imagen enviada
    scale_x = screen_geometry["width"] / screen_geometry["real_width"]
    scale_y = screen_geometry["height"] / screen_geometry["real_height"]

    return {
        "x": round((position.x - screen_geometry["left"]) * scale_x),
        "y": round((position.y - screen_geometry["top"]) * scale_y),
        "cursor": get_cursor_type()
    }


def capture_screen():

    with mss.mss() as screen:

        monitor = screen.monitors[1]

        screenshot = screen.grab(
            monitor
        )

        image = Image.frombytes(
            "RGB",
            screenshot.size,
            screenshot.rgb
        )

        image.thumbnail(
            (1280, 720)
        )

        screen_geometry.update({
            "left": monitor["left"],
            "top": monitor["top"],
            "real_width": screenshot.size[0],
            "real_height": screenshot.size[1],
            "width": image.width,
            "height": image.height
        })

        buffer = io.BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=50
        )

        return base64.b64encode(
            buffer.getvalue()
        ).decode("utf-8")


async def screen_stream(websocket):

    while True:

        screen_data = capture_screen()

        mouse_position = get_mouse_position()

        message = {
            "image": screen_data,
            "mouse": mouse_position,
            "width": screen_geometry["width"],
            "height": screen_geometry["height"]
        }

        await websocket.send(
            f"screen_info:{json.dumps(message)}"
        )

        await asyncio.sleep(
            0.08
        )


# ==============================
# TRANSFERENCIA DE ARCHIVOS (PC tecnica -> esta PC)
# ==============================

# La carpeta de destino la decide siempre el Agent, nunca el navegador
DOWNLOADS_DIR = r"C:\RemoteAdmin\Downloads"

MAX_FILE_SIZE = 200 * 1024 * 1024

# Cada frame binario empieza con el transfer_id (32 caracteres hex)
TRANSFER_ID_LENGTH = 32

DOWNLOAD_CHUNK_SIZE = 256 * 1024

INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10))
}

file_transfer = None


def safe_filename(name):

    # Solo el nombre, sin rutas ni caracteres invalidos de Windows
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = INVALID_NAME_CHARS.sub("_", name).strip().rstrip(". ")

    stem, ext = os.path.splitext(name)

    stem = stem[:180] or "archivo"
    ext = ext[:20]

    if stem.upper() in RESERVED_NAMES:
        stem = f"_{stem}"

    return stem + ext


def start_file_transfer(data):

    global file_transfer

    if file_transfer is not None:
        raise ValueError("Ya hay una transferencia en curso")

    transfer_id = str(data.get("transfer_id", ""))

    if not re.fullmatch(r"[0-9a-f]{32}", transfer_id):
        raise ValueError("Identificador de transferencia inválido")

    size = data.get("size")

    if not isinstance(size, int) or size < 0:
        raise ValueError("Tamaño de archivo inválido")

    if size > MAX_FILE_SIZE:
        raise ValueError(
            f"El archivo supera el límite de {MAX_FILE_SIZE // (1024 * 1024)} MB"
        )

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    # Se escribe en un temporal y se renombra al terminar
    temp_path = os.path.join(DOWNLOADS_DIR, f".{transfer_id}.part")

    file_transfer = {
        "transfer_id": transfer_id,
        "filename": safe_filename(data.get("filename")),
        "size": size,
        "received": 0,
        "temp_path": temp_path,
        "handle": open(temp_path, "xb")
    }


def write_file_chunk(chunk):

    if file_transfer is None:
        return

    transfer_id = chunk[:TRANSFER_ID_LENGTH].decode("ascii", "replace")

    if transfer_id != file_transfer["transfer_id"]:
        return

    data = chunk[TRANSFER_ID_LENGTH:]

    if file_transfer["received"] + len(data) > file_transfer["size"]:
        raise ValueError("Se recibieron más datos que el tamaño indicado")

    file_transfer["handle"].write(data)
    file_transfer["received"] += len(data)


def finish_file_transfer(data):

    global file_transfer

    transfer = file_transfer

    if transfer is None or data.get("transfer_id") != transfer["transfer_id"]:
        raise ValueError("No hay una transferencia activa con ese identificador")

    transfer["handle"].close()

    if transfer["received"] != transfer["size"]:
        raise ValueError("El archivo llegó incompleto")

    stem, ext = os.path.splitext(transfer["filename"])
    destination = os.path.join(DOWNLOADS_DIR, transfer["filename"])
    counter = 1

    # Nunca sobrescribe: archivo (1).pdf, archivo (2).pdf...
    while True:

        try:
            if os.path.exists(destination):
                raise FileExistsError
            os.rename(transfer["temp_path"], destination)
            break

        except FileExistsError:
            destination = os.path.join(
                DOWNLOADS_DIR,
                f"{stem} ({counter}){ext}"
            )
            counter += 1

    file_transfer = None

    return {
        "transfer_id": transfer["transfer_id"],
        "filename": transfer["filename"],
        "saved_as": os.path.basename(destination),
        "path": destination,
        "size": transfer["received"]
    }


def abort_file_transfer():

    global file_transfer

    if file_transfer is None:
        return

    try:
        file_transfer["handle"].close()
    except Exception:
        pass

    try:
        os.remove(file_transfer["temp_path"])
    except OSError:
        pass

    file_transfer = None


async def handle_file_transfer_message(websocket, message):

    # Devuelve True si el mensaje pertenece a la transferencia de archivos
    if isinstance(message, bytes):


        try:
            write_file_chunk(message)

        except Exception as error:

            transfer_id = file_transfer["transfer_id"] if file_transfer else None

            abort_file_transfer()

            await websocket.send(
                "file_transfer_error:"
                + json.dumps({"transfer_id": transfer_id, "message": str(error)})
            )

        return True

    if not message.startswith("file_transfer_"):
        return False

    command, _, payload = message.partition(":")

    try:
        data = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        data = {}

    if not isinstance(data, dict):
        data = {}

    transfer_id = data.get("transfer_id")


    try:

        if command == "file_transfer_start":

            start_file_transfer(data)

            print(f"Recibiendo archivo: {file_transfer['filename']}")

            await websocket.send(
                "file_transfer_ready:"
                + json.dumps({"transfer_id": transfer_id})
            )


        elif command == "file_transfer_end":

            info = finish_file_transfer(data)

            print(f"Archivo guardado: {info['path']}")

            await websocket.send(
                "file_transfer_complete:" + json.dumps(info)
            )


        elif command == "file_transfer_abort":

            if file_transfer and file_transfer["transfer_id"] == transfer_id:
                abort_file_transfer()

    except Exception as error:


        if file_transfer and file_transfer["transfer_id"] == transfer_id:
            abort_file_transfer()

        await websocket.send(
            "file_transfer_error:"
            + json.dumps({"transfer_id": transfer_id, "message": str(error)})
        )

    return True


def resolve_download_path(path):

    # La ruta siempre debe quedar DENTRO de C:\RemoteAdmin\Downloads
    if not path or not isinstance(path, str):
        raise ValueError("Ruta vacía")

    base = os.path.realpath(DOWNLOADS_DIR)
    target = os.path.realpath(path)

    if target != base and not target.startswith(base + os.sep):
        raise ValueError("La ruta está fuera de la carpeta permitida")

    if not os.path.exists(target):
        raise FileNotFoundError("El archivo no existe")

    if not os.path.isfile(target):
        raise ValueError("La ruta no es un archivo")

    size = os.path.getsize(target)

    if size > MAX_FILE_SIZE:
        raise ValueError(
            f"El archivo supera el límite de {MAX_FILE_SIZE // (1024 * 1024)} MB"
        )

    return target, size


async def handle_file_download_message(websocket, message):

    # PC remota -> PC tecnica. Devuelve True si el mensaje es de descarga.
    if isinstance(message, bytes) or not message.startswith("file_download_"):
        return False

    command, _, payload = message.partition(":")

    try:
        data = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        data = {}

    if not isinstance(data, dict):
        data = {}

    transfer_id = data.get("transfer_id")


    if command != "file_download_start":
        return True

    try:

        target, size = resolve_download_path(data.get("path"))

        filename = os.path.basename(target)

        await websocket.send(
            "file_download_ready:"
            + json.dumps({
                "transfer_id": transfer_id,
                "filename": filename,
                "size": size
            })
        )


        prefix = transfer_id.encode("ascii")
        sent = 0

        with open(target, "rb") as handle:

            while True:

                chunk = handle.read(DOWNLOAD_CHUNK_SIZE)

                if not chunk:
                    break

                await websocket.send(prefix + chunk)
                sent += len(chunk)


        await websocket.send(
            "file_download_complete:"
            + json.dumps({
                "transfer_id": transfer_id,
                "filename": filename,
                "size": sent
            })
        )


    except Exception as error:


        await websocket.send(
            "file_download_error:"
            + json.dumps({"transfer_id": transfer_id, "message": str(error)})
        )

    return True


async def websocket_connection():

    global screen_stream_task, _event_loop, _ws

    while True:

        try:

            print(
                "Connecting to WebSocket..."
            )

            # El token individual va en una CABECERA del handshake, no en la
            # URL: una query string acaba en logs de servidor, proxies e
            # historiales, y ahí el token quedaría expuesto.
            # ssl=None con ws:// y para wss:// sin CA propia: websockets aplica
            # su contexto seguro por defecto. Con CA configurada se usa esa.
            async with websockets.connect(
                WEBSOCKET_URL,
                additional_headers={
                    "X-Agent-Token": get_agent_device_token() or ""
                },
                ssl=build_ssl_context()
            ) as websocket:

                print(
                    "WebSocket connected"
                )

                await websocket.send(
                    f"Agent connected: {get_device_id()}"
                )

                # Referencia para que los hilos (grabador/reintentos) reporten estado
                _event_loop = asyncio.get_running_loop()
                _ws = websocket

                # Al reconectar, se reintentan las subidas pendientes y se informa el estado
                _process_pending()
                await websocket.send(
                    "recording_status:" + json.dumps(build_recording_status())
                )

                screen_stream_task = None

                while True:

                    message = await websocket.recv()

                    # Frames binarios y mensajes file_transfer_* no llegan a la logica existente
                    if await handle_file_transfer_message(
                        websocket,
                        message
                    ):
                        continue

                    # Descarga PC remota -> PC tecnica
                    if await handle_file_download_message(
                        websocket,
                        message
                    ):
                        continue

                    print(
                        f"Server message: {message}"
                    )

                    if message == "ping":

                        await websocket.send(
                            "pong"
                        )

                    elif message == "start_recording":

                        # Fuera del event loop: no bloquea el WebSocket
                        await asyncio.to_thread(start_screen_recording)

                        await websocket.send(
                            "recording_status:" + json.dumps(build_recording_status())
                        )

                    elif message == "stop_recording":

                        # stop() hace join del hilo del grabador; se ejecuta en un
                        # hilo aparte para no congelar el event loop del Agent
                        await asyncio.to_thread(stop_screen_recording)

                        await websocket.send(
                            "recording_status:" + json.dumps(build_recording_status())
                        )

                    elif message.startswith("set_continuous:"):

                        try:
                            data = json.loads(message.split(":", 1)[1])
                        except json.JSONDecodeError:
                            data = {}

                        await asyncio.to_thread(
                            apply_continuous,
                            bool(data.get("enabled"))
                        )

                        await websocket.send(
                            "recording_status:" + json.dumps(build_recording_status())
                        )

                    elif message == "get_system_info":

                        print(
                            "Obteniendo información del sistema..."
                        )

                        system_info = get_system_info()

                        print(
                            "Información obtenida:"
                        )

                        print(
                            system_info
                        )

                        message_to_send = (
                            "system_info:"
                            + json.dumps(system_info)
                        )

                        print(
                            "Enviando información al servidor..."
                        )

                        await websocket.send(
                            message_to_send
                        )

                        print(
                            "Información enviada"
                        )

                    elif message == "start_screen_stream":

                        print(
                            "Iniciando transmisión de pantalla..."
                        )

                        if screen_stream_task is None:

                            screen_stream_task = (
                                asyncio.create_task(
                                    screen_stream(
                                        websocket
                                    )
                                )
                            )

                    elif message == "stop_screen_stream":

                        print(
                            "Deteniendo transmisión de pantalla..."
                        )

                        release_all_keys()

                        if screen_stream_task is not None:

                            screen_stream_task.cancel()

                            try:

                                await screen_stream_task

                            except asyncio.CancelledError:
                                pass

                            screen_stream_task = None

                    elif message.startswith(
                        "mouse_move:"
                    ):

                        data = json.loads(
                            message.split(
                                ":",
                                1
                            )[1]
                        )

                        move_mouse(
                            data["x"],
                            data["y"]
                        )

                    elif message.startswith(
                        "mouse_click:"
                    ):

                        data = json.loads(
                            message.split(
                                ":",
                                1
                            )[1]
                        )

                        click_mouse(
                            data.get(
                                "button",
                                "left"
                            )
                        )

                    elif message.startswith(
                        "mouse_down:"
                    ):

                        data = json.loads(
                            message.split(
                                ":",
                                1
                            )[1]
                        )

                        mouse_down(
                            data["x"],
                            data["y"],
                            data.get(
                                "button",
                                "left"
                            )
                        )

                    elif message.startswith(
                        "mouse_up:"
                    ):

                        data = json.loads(
                            message.split(
                                ":",
                                1
                            )[1]
                        )

                        mouse_up(
                            data.get("x"),
                            data.get("y"),
                            data.get(
                                "button",
                                "left"
                            )
                        )

                    elif message.startswith(
                        "keyboard:"
                    ):

                        data = json.loads(
                            message.split(
                                ":",
                                1
                            )[1]
                        )

                        try:

                            handle_keyboard(
                                data
                            )

                        except Exception as error:

                            print(
                                f"Keyboard error: {error}"
                            )

                    elif message.startswith((
                        "get_processes:", "get_services:"
                    )):

                        # Consultas de solo lectura (G2 y G3). El servidor
                        # manda un nombre de comando fijo y un
                        # identificador; nada de lo que llega se ejecuta ni
                        # se interpreta como orden del sistema.
                        comando, cuerpo = message.split(":", 1)

                        try:
                            peticion = json.loads(cuerpo)
                        except json.JSONDecodeError:
                            peticion = {}

                        query_id = peticion.get("query_id")

                        if comando == "get_processes":
                            consultar = inventory.list_processes
                            respuesta_prefijo = "processes_info:"
                        else:
                            consultar = inventory.list_services
                            respuesta_prefijo = "services_info:"

                        print(f"Consultando {comando}...")

                        try:

                            # En hilo aparte: recorrer cientos de procesos
                            # bloquea, y el WebSocket debe seguir atendiendo
                            # pantalla, teclado y grabacion mientras tanto.
                            resultado = await asyncio.to_thread(consultar)

                        except Exception as error:
                            resultado = {"error": f"Error al consultar: {error}"}

                        resultado["query_id"] = query_id

                        await websocket.send(
                            respuesta_prefijo + json.dumps(resultado)
                        )

                        print(f"Respuesta de {comando} enviada")

                    elif message == "get_installed_software":

                        print(
                            "Obteniendo software instalado..."
                        )

                        software = get_installed_software()

                        print(
                            f"Software encontrado: "
                            f"{len(software)}"
                        )

                        message_to_send = (
                            "software_info:"
                            + json.dumps(software)
                        )

                        print(
                            "Enviando software al servidor..."
                        )

                        await websocket.send(
                            message_to_send
                        )

                        print(
                            "Software enviado"
                        )

        except Exception as error:

            release_all_keys()

            abort_file_transfer()

            # Se pierde la referencia al WebSocket; la grabación LOCAL continúa
            _ws = None

            print(
                f"WebSocket error: {error}"
            )

            print(
                "Retrying WebSocket connection in 5 seconds..."
            )

            await asyncio.sleep(
                5
            )


async def heartbeat_loop():

    while True:

        try:

            send_heartbeat()

        except requests.RequestException as error:

            print(
                f"Heartbeat error: {error}"
            )

        await asyncio.sleep(
            HEARTBEAT_INTERVAL
        )


async def main():

    print(
        "RemoteAdmin Agent"
    )

    print(
        "------------------"
    )

    # Hilo de reintentos de subida (independiente del WebSocket)
    threading.Thread(
        target=_retry_loop,
        name="UploadRetry",
        daemon=True
    ).start()

    while True:

        try:

            register_device()

            break

        except requests.RequestException as error:

            print(
                f"Server unavailable: {error}"
            )

            print(
                "Retrying in 5 seconds..."
            )

            await asyncio.sleep(
                5
            )

    try:

        await asyncio.gather(
            heartbeat_loop(),
            websocket_connection()
        )

    finally:

        # Cierra cualquier grabación en curso antes de salir
        if screen_recorder.is_recording():
            stop_screen_recording()


if __name__ == "__main__":

    asyncio.run(
        main()
    )