from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.database import init_db
from backend.models import DeviceRegister, DeviceHeartbeat
from backend.devices import (
    register_device,
    update_heartbeat,
    update_system_info,
    get_devices
)

import json


app = FastAPI(title="RemoteAdmin")


connected_agents = {}


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
    return get_devices()


@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()

    device_id = None

    try:
        message = await websocket.receive_text()

        if message.startswith("Agent connected:"):
            device_id = message.split(":", 1)[1].strip()

            connected_agents[device_id] = websocket

            print(
                f"Agent connected: {device_id}"
            )

        while True:
            message = await websocket.receive_text()

            print(
                f"Message from {device_id}: {message}"
            )

            if message.startswith("system_info:"):
                system_info = json.loads(
                    message.split(":",1)[1]
                )

                print(
                    f"system info form {device_id}:"
                )

                print(system_info)

                update_system_info(
                    device_id,
                    system_info
                )

            if message == "pong":
                print(
                    f"Pong received from {device_id}"
                )

    except WebSocketDisconnect:
        if device_id:
            connected_agents.pop(device_id, None)

            print(
                f"Agent disconnected: {device_id}"
            )


@app.post("/api/devices/{device_id}/ping")
async def ping_agent(device_id: str):
    websocket = connected_agents.get(device_id)

    if not websocket:
        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text("ping")

    return {
        "status": "sent",
        "device_id": device_id
    }


@app.post("/api/devices/{device_id}/system-info")
async def request_system_info(device_id: str):
    websocket = connected_agents.get(device_id)

    if not websocket:
        return {
            "status": "error",
            "message": "Agent is not connected"
        }

    await websocket.send_text("get_system_info")

    return {
        "status": "sent",
        "device_id": device_id,
        "command": "get_system_info"
    }


app.mount(
    "/frontend",
    StaticFiles(directory="frontend"),
    name="frontend"
)


@app.get("/")
def root():
    return FileResponse("frontend/index.html")