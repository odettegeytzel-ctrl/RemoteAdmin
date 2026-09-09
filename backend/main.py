from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.database import init_db
from backend.models import DeviceRegister, DeviceHeartbeat
from backend.devices import register_device, update_heartbeat, get_devices

app = FastAPI(title="RemoteAdmin")


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


app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")


@app.get("/")
def root():
    return FileResponse("frontend/index.html")
    