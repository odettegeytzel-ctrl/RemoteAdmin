from pydantic import BaseModel


class DeviceRegister(BaseModel):
    device_id: str
    hostname: str
    operating_system: str
    ip_address: str


class DeviceHeartbeat(BaseModel):
    device_id: str
    ip_address: str