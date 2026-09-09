from datetime import datetime, timezone, timedelta

from backend.database import get_connection
from backend.models import DeviceRegister, DeviceHeartbeat


OFFLINE_AFTER_SECONDS = 30


def register_device(data: DeviceRegister):
    connection = get_connection()
    now = datetime.now(timezone.utc).isoformat()

    connection.execute(
        """
        INSERT INTO devices (
            device_id,
            hostname,
            operating_system,
            ip_address,
            status,
            last_seen
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
            hostname = excluded.hostname,
            operating_system = excluded.operating_system,
            ip_address = excluded.ip_address,
            status = excluded.status,
            last_seen = excluded.last_seen
        """,
        (
            data.device_id,
            data.hostname,
            data.operating_system,
            data.ip_address,
            "online",
            now
        )
    )

    connection.commit()
    connection.close()

    return {
        "status": "registered",
        "device_id": data.device_id
    }


def update_heartbeat(data: DeviceHeartbeat):
    connection = get_connection()
    now = datetime.now(timezone.utc).isoformat()

    cursor = connection.execute(
        """
        UPDATE devices
        SET
            ip_address = ?,
            status = ?,
            last_seen = ?
        WHERE device_id = ?
        """,
        (
            data.ip_address,
            "online",
            now,
            data.device_id
        )
    )

    connection.commit()
    connection.close()

    if cursor.rowcount == 0:
        return {
            "status": "error",
            "message": "Device not registered"
        }

    return {
        "status": "heartbeat_received",
        "device_id": data.device_id
    }


def update_system_info(device_id, system_info):
    connection = get_connection()

    cursor = connection.execute(
        """
        UPDATE devices
        SET
            hostname = ?,
            username = ?,
            operating_system = ?,
            ip_address = ?,
            processor = ?,
            cpu_count = ?
        WHERE device_id = ?
        """,
        (
            system_info.get("hostname"),
            system_info.get("username"),
            system_info.get("operating_system"),
            system_info.get("ip_address"),
            system_info.get("processor"),
            system_info.get("cpu_count"),
            device_id
        )
    )

    connection.commit()
    connection.close()

    if cursor.rowcount == 0:
        return {
            "status": "error",
            "message": "Device not registered"
        }

    return {
        "status": "updated",
        "device_id": device_id
    }


def get_devices():
    connection = get_connection()

    devices = connection.execute(
        """
        SELECT
            id,
            device_id,
            hostname,
            operating_system,
            ip_address,
            username,
            processor,
            cpu_count,
            status,
            last_seen,
            created_at
        FROM devices
        ORDER BY hostname
        """
    ).fetchall()

    connection.close()

    now = datetime.now(timezone.utc)
    result = []

    for device in devices:
        device = dict(device)

        if device["last_seen"]:
            last_seen = datetime.fromisoformat(device["last_seen"])
            difference = now - last_seen

            if difference > timedelta(seconds=OFFLINE_AFTER_SECONDS):
                device["status"] = "offline"

        result.append(device)

    return result