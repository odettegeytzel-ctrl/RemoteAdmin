import asyncio
import hashlib
import platform
import socket
import time
import getpass
import os
import json

import requests
import websockets


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


def get_system_info():
    return {
        "hostname": socket.gethostname(),
        "username": getpass.getuser(),
        "operating_system": platform.platform(),
        "ip_address": get_local_ip(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count()
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