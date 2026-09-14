import asyncio
import base64
import json
import uuid

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from backend.database import init_db
from backend.models import DeviceRegister, DeviceHeartbeat
from backend.devices import (
    register_device,
    update_heartbeat,
    update_system_info,
    update_installed_software,
    get_installed_software,
    get_devices
)
from backend.alerts import (
    detect_alerts,
    get_alerts,
    mark_alert_read,
    mark_all_alerts_read
)
from backend.settings import (
    get_settings,
    save_settings
)
from backend.auth import (
    authenticate,
    verify_token,
    token_from_header
)


app = FastAPI(title="RemoteAdmin")


connected_agents = {}
latest_screens = {}
latest_cursors = {}


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/devices/register")
def register(data: DeviceRegister):
    return register_device(data)


@app.post("/api/devices/heartbeat")
def heartbeat(data: DeviceHeartbeat):
    return update_heartbeat(data)


@app.get("/api/devices")
def devices():
    # Detecta cambios de estado antes de devolver la lista
    detect_alerts()
    return get_devices()


@app.get("/api/alerts")
def alerts():
    detect_alerts()
    return get_alerts()


@app.post("/api/alerts/{alert_id}/read")
def alert_read(alert_id: int):
    return mark_alert_read(alert_id)


@app.post("/api/alerts/read-all")
def alerts_read_all():
    return mark_all_alerts_read()


@app.get("/api/settings")
def settings():
    return get_settings()


@app.post("/api/settings")
def update_settings(data: dict):
    return save_settings(data)


# ==============================
# AUTENTICACION
# ==============================

@app.post("/api/auth/login")
def auth_login(data: dict):

    token = authenticate(
        data.get("username", ""),
        data.get("password", "")
    )

    if not token:

        return JSONResponse(
            status_code=401,
            content={
                "status": "error",
                "message": "Usuario o contraseña incorrectos"
            }
        )

    return {
        "status": "ok",
        "token": token,
        "username": data.get("username", "")
    }


@app.get("/api/auth/me")
def auth_me(authorization: str = Header(default=None)):

    username = verify_token(
        token_from_header(authorization)
    )

    if not username:

        return JSONResponse(
            status_code=401,
            content={
                "status": "error",
                "message": "Sesión no válida"
            }
        )

    return {
        "status": "ok",
        "username": username
    }


@app.post("/api/auth/logout")
def auth_logout():

    # Los tokens son sin estado: el cliente descarta el suyo
    return {
        "status": "ok"
    }


@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):

    await websocket.accept()

    device_id = None

    try:

        message = await websocket.receive_text()

        if message.startswith("Agent connected:"):

            device_id = message.split(
                ":",
                1
            )[1].strip()

            connected_agents[device_id] = websocket

            print(
                f"Agent connected: {device_id}"
            )

        while True:

            raw = await websocket.receive()

            if raw["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(raw.get("code", 1000))

            # Frames binarios: solo chunks de descarga (PC remota -> PC tecnica)
            if raw.get("bytes") is not None:
                feed_download_chunk(raw["bytes"])
                continue

            message = raw.get("text")

            if message is None:
                continue

            # No se imprimen los frames para no saturar la consola
            if not message.startswith("screen_info:"):

                print(
                    f"Message from {device_id}: {message}"
                )


            if message.startswith("screen_info:"):

                screen_data = message.split(
                    ":",
                    1
                )[1]

                try:

                    screen_json = json.loads(
                        screen_data
                    )

                except json.JSONDecodeError:

                    # Formato antiguo: solo base64
                    screen_json = {
                        "image": screen_data
                    }

                if screen_json.get("image"):

                    latest_screens[device_id] = screen_json["image"]

                mouse = screen_json.get("mouse")

                if mouse:

                    latest_cursors[device_id] = {
                        "x": mouse.get("x", 0),
                        "y": mouse.get("y", 0),
                        "width": screen_json.get("width"),
                        "height": screen_json.get("height"),
                        "cursor": mouse.get("cursor", "arrow")
                    }


            elif message.startswith("system_info:"):

                system_info = json.loads(
                    message.split(
                        ":",
                        1
                    )[1]
                )

                print(
                    f"System info from {device_id}:"
                )

                print(system_info)

                update_system_info(
                    device_id,
                    system_info
                )


            elif message.startswith("software_info:"):

                software = json.loads(
                    message.split(
                        ":",
                        1
                    )[1]
                )

                print(
                    f"Software recibido de {device_id}: "
                    f"{len(software)} programas"
                )

                update_installed_software(
                    device_id,
                    software
                )


            elif message.startswith((
                "file_transfer_ready:",
                "file_transfer_complete:",
                "file_transfer_error:"
            )):

                resolve_file_transfer(
                    message
                )


            elif message.startswith((
                "file_download_ready:",
                "file_download_complete:",
                "file_download_error:"
            )):

                resolve_file_download(
                    message
                )


            elif message == "pong":

                print(
                    f"Pong received from {device_id}"
                )

    except WebSocketDisconnect:

        if device_id:

            connected_agents.pop(
                device_id,
                None
            )

            latest_screens.pop(
                device_id,
                None
            )

            latest_cursors.pop(
                device_id,
                None
            )

            fail_device_file_transfers(
                device_id
            )

            fail_device_file_downloads(
                device_id
            )

            print(
                f"Agent disconnected: {device_id}"
            )


@app.post("/api/devices/{device_id}/ping")
async def ping_agent(device_id: str):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "ping"
    )

    return {
        "status": "sent",
        "device_id": device_id
    }


@app.post("/api/devices/{device_id}/system-info")
async def request_system_info(device_id: str):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "get_system_info"
    )

    return {
        "status": "sent",
        "device_id": device_id,
        "command": "get_system_info"
    }


@app.post("/api/devices/{device_id}/software")
async def request_installed_software(device_id: str):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "get_installed_software"
    )

    return {
        "status": "sent",
        "device_id": device_id,
        "command": "get_installed_software"
    }


@app.get("/api/devices/{device_id}/software")
def installed_software(device_id: str):

    return get_installed_software(
        device_id
    )


@app.post("/api/devices/{device_id}/screen")
async def request_screen(device_id: str):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "start_screen_stream"
    )

    return {
        "status": "sent",
        "device_id": device_id
    }


@app.get("/api/devices/{device_id}/screen")
def get_screen(device_id: str):

    screen = latest_screens.get(
        device_id
    )

    if not screen:

        return {
            "status": "error",
            "message": "No screen available"
        }

    return {
        "status": "ok",
        "image": screen
    }


@app.get("/api/devices/{device_id}/cursor")
def get_cursor(device_id: str):

    cursor = latest_cursors.get(
        device_id
    )

    if not cursor:

        return {
            "status": "error",
            "message": "No cursor available"
        }

    return {
        "status": "ok",
        **cursor
    }


async def screen_generator(device_id):

    last_screen = None

    while True:

        screen = latest_screens.get(
            device_id
        )

        if screen and screen != last_screen:

            image = base64.b64decode(
                screen
            )

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"\r\n"
                + image
                + b"\r\n"
            )

            last_screen = screen

        await asyncio.sleep(
            0.05
        )


@app.get("/api/devices/{device_id}/screen-stream")
async def screen_stream(device_id: str):

    if device_id not in connected_agents:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    return StreamingResponse(
        screen_generator(device_id),
        media_type=(
            "multipart/x-mixed-replace; "
            "boundary=frame"
        )
    )


@app.post("/api/devices/{device_id}/screen/stop")
async def stop_screen(device_id: str):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        "stop_screen_stream"
    )

    return {
        "status": "stopped",
        "device_id": device_id
    }


@app.post("/api/devices/{device_id}/mouse/move")
async def mouse_move(device_id: str, data: dict):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"mouse_move:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


@app.post("/api/devices/{device_id}/mouse/click")
async def mouse_click(device_id: str, data: dict):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"mouse_click:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


@app.post("/api/devices/{device_id}/mouse/down")
async def mouse_down(device_id: str, data: dict):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"mouse_down:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


@app.post("/api/devices/{device_id}/mouse/up")
async def mouse_up(device_id: str, data: dict):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"mouse_up:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


# ==============================
# TRANSFERENCIA DE ARCHIVOS (PC tecnica -> Agent)
# ==============================

MAX_FILE_SIZE = 200 * 1024 * 1024

# Menor que el limite de 1 MB por mensaje de la libreria websockets del Agent
FILE_CHUNK_SIZE = 256 * 1024

file_transfers = {}
file_transfer_locks = {}


class FileTransferError(Exception):

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def file_transfer_error(status_code, message):

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "message": message
        }
    )


def resolve_file_transfer(message):

    command, _, payload = message.partition(":")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    if not isinstance(data, dict):
        return

    transfer = file_transfers.get(
        data.get("transfer_id")
    )


    if not transfer:
        return

    if command == "file_transfer_ready":

        if not transfer["ready"].done():
            transfer["ready"].set_result({})

    elif command == "file_transfer_complete":

        if not transfer["done"].done():
            transfer["done"].set_result(data)

    elif command == "file_transfer_error":

        error = {
            "error": data.get("message") or "Error en el Agent"
        }

        for future in (transfer["ready"], transfer["done"]):
            if not future.done():
                future.set_result(error)


def fail_device_file_transfers(device_id):

    error = {
        "error": "El Agent se desconectó"
    }

    for transfer in file_transfers.values():

        if transfer["device_id"] != device_id:
            continue

        for future in (transfer["ready"], transfer["done"]):
            if not future.done():
                future.set_result(error)


@app.post("/api/devices/{device_id}/files/upload")
async def upload_file(
    device_id: str,
    request: Request,
    filename: str,
    size: int
):


    websocket = connected_agents.get(
        device_id
    )

    if not websocket:
        return file_transfer_error(404, "Agent is not connected")

    if not filename.strip() or size < 0:
        return file_transfer_error(400, "Nombre o tamaño de archivo inválido")

    if size > MAX_FILE_SIZE:
        return file_transfer_error(
            413,
            f"El archivo supera el límite de {MAX_FILE_SIZE // (1024 * 1024)} MB"
        )

    lock = file_transfer_locks.setdefault(
        device_id,
        asyncio.Lock()
    )

    if lock.locked():
        return file_transfer_error(409, "Ya hay una transferencia en curso para este equipo")

    async with lock:

        loop = asyncio.get_running_loop()
        transfer_id = uuid.uuid4().hex
        prefix = transfer_id.encode("ascii")

        transfer = {
            "device_id": device_id,
            "ready": loop.create_future(),
            "done": loop.create_future()
        }

        file_transfers[transfer_id] = transfer

        try:

            # 1-3. Inicio con nombre y tamano; el Agent confirma que esta listo
            await websocket.send_text(
                "file_transfer_start:"
                + json.dumps({
                    "transfer_id": transfer_id,
                    "filename": filename,
                    "size": size
                })
            )


            ready = await asyncio.wait_for(transfer["ready"], 15)


            if ready.get("error"):
                raise FileTransferError(400, ready["error"])

            # 4. Datos binarios en trozos, a medida que llegan del navegador
            received = 0
            buffer = bytearray()


            async for chunk in request.stream():

                received += len(chunk)

                if received > size:
                    raise FileTransferError(400, "El archivo es mayor que el tamaño indicado")

                buffer.extend(chunk)

                while len(buffer) >= FILE_CHUNK_SIZE:
                    await websocket.send_bytes(prefix + bytes(buffer[:FILE_CHUNK_SIZE]))
                    del buffer[:FILE_CHUNK_SIZE]

                if transfer["done"].done():
                    break

            if transfer["done"].done():
                result = transfer["done"].result()
                raise FileTransferError(502, result.get("error") or "El Agent canceló la transferencia")

            if buffer:
                await websocket.send_bytes(prefix + bytes(buffer))


            if received != size:
                raise FileTransferError(400, "La subida se interrumpió antes de terminar")

            # 5. Fin; 6. confirmacion del Agent
            await websocket.send_text(
                "file_transfer_end:"
                + json.dumps({"transfer_id": transfer_id})
            )


            result = await asyncio.wait_for(transfer["done"], 60)

            if result.get("error"):
                raise FileTransferError(502, result["error"])


            return {
                "status": "file_transfer_complete",
                "file": result
            }

        except FileTransferError as error:


            await send_file_transfer_abort(websocket, transfer_id)

            return file_transfer_error(error.status_code, error.message)

        except asyncio.TimeoutError:


            await send_file_transfer_abort(websocket, transfer_id)

            return file_transfer_error(504, "El Agent no respondió a tiempo")

        except Exception as error:


            await send_file_transfer_abort(websocket, transfer_id)

            return file_transfer_error(500, f"Error en la transferencia: {error}")

        finally:

            file_transfers.pop(
                transfer_id,
                None
            )


async def send_file_transfer_abort(websocket, transfer_id):

    try:
        await websocket.send_text(
            "file_transfer_abort:"
            + json.dumps({"transfer_id": transfer_id})
        )
    except Exception:
        pass


# ==============================
# DESCARGA DE ARCHIVOS (PC remota -> PC tecnica)
# ==============================

import os as _os
import urllib.parse as _urlparse

DOWNLOADS_BASE = r"C:\RemoteAdmin\Downloads"

file_downloads = {}


def path_within_downloads(path):

    # Validacion ligera en backend; el Agent hace la validacion definitiva
    if not path or ".." in path.replace("/", "\\").split("\\"):
        return False

    base = _os.path.normpath(DOWNLOADS_BASE).lower()
    target = _os.path.normpath(path).lower()

    return target == base or target.startswith(base + "\\")


def feed_download_chunk(chunk):

    transfer_id = chunk[:32].decode("ascii", "replace")

    download = file_downloads.get(transfer_id)

    if download:
        download["queue"].put_nowait(chunk[32:])


def resolve_file_download(message):

    command, _, payload = message.partition(":")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    if not isinstance(data, dict):
        return

    download = file_downloads.get(
        data.get("transfer_id")
    )


    if not download:
        return

    if command == "file_download_ready":

        if not download["meta"].done():
            download["meta"].set_result(data)

    elif command == "file_download_complete":

        # Sentinela de fin de stream
        download["queue"].put_nowait(None)

    elif command == "file_download_error":

        error = {"error": data.get("message") or "Error en el Agent"}

        if not download["meta"].done():
            download["meta"].set_result(error)
        else:
            download["queue"].put_nowait(error)


def fail_device_file_downloads(device_id):

    for download in file_downloads.values():

        if download["device_id"] != device_id:
            continue

        error = {"error": "El Agent se desconectó"}

        if not download["meta"].done():
            download["meta"].set_result(error)
        else:
            download["queue"].put_nowait(error)


@app.get("/api/devices/{device_id}/files/download")
async def download_file(device_id: str, path: str):


    websocket = connected_agents.get(
        device_id
    )

    if not websocket:
        return file_transfer_error(404, "Agent is not connected")

    if not path_within_downloads(path):
        return file_transfer_error(403, "Solo se permiten archivos dentro de C:\\RemoteAdmin\\Downloads")

    loop = asyncio.get_running_loop()
    transfer_id = uuid.uuid4().hex

    download = {
        "device_id": device_id,
        "meta": loop.create_future(),
        "queue": asyncio.Queue()
    }

    file_downloads[transfer_id] = download

    await websocket.send_text(
        "file_download_start:"
        + json.dumps({"transfer_id": transfer_id, "path": path})
    )


    try:
        meta = await asyncio.wait_for(download["meta"], 20)
    except asyncio.TimeoutError:
        file_downloads.pop(transfer_id, None)
        return file_transfer_error(504, "El Agent no respondió a tiempo")

    if meta.get("error"):
        file_downloads.pop(transfer_id, None)
        return file_transfer_error(502, meta["error"])

    filename = meta.get("filename") or "archivo"
    size = meta.get("size", 0)


    async def stream_file():

        received = 0

        try:

            while True:

                try:
                    item = await asyncio.wait_for(download["queue"].get(), 60)
                except asyncio.TimeoutError:
                    break

                if item is None:
                    break

                if isinstance(item, dict):
                    break

                received += len(item)
                yield item

        finally:
            file_downloads.pop(transfer_id, None)

    encoded = _urlparse.quote(filename)

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        "Content-Length": str(size)
    }

    return StreamingResponse(
        stream_file(),
        media_type="application/octet-stream",
        headers=headers
    )


@app.post("/api/devices/{device_id}/keyboard")
async def keyboard_input(device_id: str, data: dict):

    websocket = connected_agents.get(
        device_id
    )

    if not websocket:

        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text(
        f"keyboard:{json.dumps(data)}"
    )

    return {
        "status": "sent"
    }


app.mount(
    "/frontend",
    StaticFiles(
        directory="frontend"
    ),
    name="frontend"
)


@app.get("/")
def root():

    return FileResponse(
        "frontend/index.html"
    )