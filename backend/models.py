from pydantic import BaseModel


class DeviceRegister(BaseModel):
    # device_id opcional: los Agents nuevos NO lo envían (el servidor genera
    # uno aleatorio en el alta). Los que ya tienen identidad lo mandan para
    # actualizar sus datos. El hostname nunca sirve para reclamar identidad.
    device_id: str | None = None
    hostname: str
    operating_system: str
    ip_address: str


class DeviceHeartbeat(BaseModel):
    device_id: str
    ip_address: str