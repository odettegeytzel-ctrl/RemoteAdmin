import hashlib
import platform
import socket
import time

import requests


SERVER_URL = "http://127.0.0.1:8000"
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


def main():
    print("RemoteAdmin Agent")
    print("------------------")

    while True:
        try:
            register_device()

            break

        except requests.RequestException as error:
            print(f"Server unavailable: {error}")
            print("Retrying in 5 seconds...")
            time.sleep(5)

    while True:
        try:
            send_heartbeat()

        except requests.RequestException as error:
            print(f"Heartbeat error: {error}")

        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    main()