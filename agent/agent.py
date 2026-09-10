import asyncio
import hashlib
import platform
import socket
import getpass
import os
import json
import subprocess

import psutil
import requests
import websockets
import wmi

SERVER_URL = "http://127.0.0.1:8000"
WEBSOCKET_URL = "ws://127.0.0.1:8000/ws/agent"

HEARTBEAT_INTERVAL = 10


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

        connection.connect(("8.8.8.8", 80))

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
        timeout=10
    )

    response.raise_for_status()

    print("Heartbeat sent")


def get_computer_info():
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model | ConvertTo-Json"
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10
    )

    computer_info = json.loads(result.stdout)

    return {
        "manufacturer": computer_info.get("Manufacturer"),
        "model": computer_info.get("Model")
    }


def get_system_info():
    memory = psutil.virtual_memory()

    computer_info = get_computer_info()

    disks = []

    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)

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
        "model": computer_info["model"],
    }



def get_hardware_info():
    memory = psutil.virtual_memory()

    disks = []

    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)

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

async def websocket_connection():
    while True:
        try:
            print("Connecting to WebSocket...")

            async with websockets.connect(
                WEBSOCKET_URL
            ) as websocket:

                print("WebSocket connected")

                await websocket.send(
                    f"Agent connected: {get_device_id()}"
                )

                while True:
                    message = await websocket.recv()

                    print(
                        f"Server message: {message}"
                    )

                    if message == "ping":
                        await websocket.send("pong")

                    elif message == "get_system_info":
                        print("Obteniendo información del sistema...")

                        system_info = get_system_info()

                        print("Información obtenida:")
                        print(system_info)

                        message_to_send = f"system_info:{json.dumps(system_info)}"

                        print("Enviando información al servidor...")

                        await websocket.send(message_to_send)

                        print("Información enviada")

        except Exception as error:
            print(
                f"WebSocket error: {error}"
            )

            print(
                "Retrying WebSocket connection in 5 seconds..."
            )

            await asyncio.sleep(5)


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
    print("RemoteAdmin Agent")
    print("------------------")

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

            await asyncio.sleep(5)

    await asyncio.gather(
        heartbeat_loop(),
        websocket_connection()
    )


if __name__ == "__main__":
    asyncio.run(main())