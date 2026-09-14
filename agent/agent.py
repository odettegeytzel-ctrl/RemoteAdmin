import asyncio
import hashlib
import platform
import socket
import getpass
import os
import json
import subprocess
import base64
import io
import re
import urllib.parse

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

HEARTBEAT_INTERVAL = 10

# Token de este Agent: se configura en el .env, nunca en el código.
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")

if not AGENT_TOKEN:
    print(
        "ADVERTENCIA: falta AGENT_TOKEN en el .env. "
        "El servidor rechazará este Agent."
    )

# Cabecera que autentica register y heartbeat
AGENT_HEADERS = {
    "X-Agent-Token": AGENT_TOKEN
}

screen_stream_task = None


def get_device_id():
    hostname = socket.gethostname()

    return hashlib.sha256(
        hostname.encode("utf-8")
    ).hexdigest()[:16]


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
    device_id = get_device_id()
    hostname = socket.gethostname()
    operating_system = platform.platform()
    ip_address = get_local_ip()

    data = {
        "device_id": device_id,
        "hostname": hostname,
        "operating_system": operating_system,
        "ip_address": ip_address
    }

    response = requests.post(
        f"{SERVER_URL}/api/devices/register",
        json=data,
        headers=AGENT_HEADERS,
        timeout=10
    )

    response.raise_for_status()

    print("Device registered:")
    print(response.json())


def send_heartbeat():
    device_id = get_device_id()
    ip_address = get_local_ip()

    data = {
        "device_id": device_id,
        "ip_address": ip_address
    }

    response = requests.post(
        f"{SERVER_URL}/api/devices/heartbeat",
        json=data,
        headers=AGENT_HEADERS,
        timeout=10
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

    global screen_stream_task

    while True:

        try:

            print(
                "Connecting to WebSocket..."
            )

            # El token viaja como query param y se valida antes del handshake
            websocket_url = (
                f"{WEBSOCKET_URL}?token={urllib.parse.quote(AGENT_TOKEN)}"
            )

            async with websockets.connect(
                websocket_url
            ) as websocket:

                print(
                    "WebSocket connected"
                )

                await websocket.send(
                    f"Agent connected: {get_device_id()}"
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

    await asyncio.gather(
        heartbeat_loop(),
        websocket_connection()
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )