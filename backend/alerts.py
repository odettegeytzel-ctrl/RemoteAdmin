from datetime import datetime, timezone, timedelta

from backend.database import get_connection


OFFLINE_AFTER_SECONDS = 30


def detect_alerts():
    # Compara el estado real (segun last_seen) con el estado guardado.
    # Solo genera una alerta cuando hay un cambio, asi se evitan duplicados.
    connection = get_connection()

    devices = connection.execute(
        "SELECT device_id, hostname, last_alert_status, last_seen FROM devices"
    ).fetchall()

    now = datetime.now(timezone.utc)

    for device in devices:
        device = dict(device)

        # last_alert_status guarda el ultimo estado por el que ya se aviso,
        # independiente del status en vivo que toca el heartbeat
        last_alert_status = device["last_alert_status"]
        real_status = "offline"

        if device["last_seen"]:
            last_seen = datetime.fromisoformat(device["last_seen"])
            difference = now - last_seen

            if difference <= timedelta(seconds=OFFLINE_AFTER_SECONDS):
                real_status = "online"

        # Primera vez: se fija la base sin generar alerta
        if last_alert_status is None:
            connection.execute(
                "UPDATE devices SET last_alert_status = ? WHERE device_id = ?",
                (real_status, device["device_id"])
            )
            continue

        if real_status == last_alert_status:
            continue

        # Hubo una transicion: se guarda una unica alerta
        if real_status == "online":
            alert_type = "online"
            message = f"{device['hostname']} se conecto"
        else:
            alert_type = "offline"
            message = f"{device['hostname']} se desconecto"

        connection.execute(
            """
            INSERT INTO alerts (
                device_id,
                hostname,
                type,
                message,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                device["device_id"],
                device["hostname"],
                alert_type,
                message,
                now.isoformat()
            )
        )

        connection.execute(
            "UPDATE devices SET last_alert_status = ? WHERE device_id = ?",
            (real_status, device["device_id"])
        )

    connection.commit()
    connection.close()


def get_alerts():
    connection = get_connection()

    alerts = connection.execute(
        """
        SELECT
            id,
            device_id,
            hostname,
            type,
            message,
            is_read,
            created_at
        FROM alerts
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()

    connection.close()

    return [dict(alert) for alert in alerts]


def mark_alert_read(alert_id):
    connection = get_connection()

    connection.execute(
        "UPDATE alerts SET is_read = 1 WHERE id = ?",
        (alert_id,)
    )

    connection.commit()
    connection.close()

    return {"status": "ok", "id": alert_id}


def mark_all_alerts_read():
    connection = get_connection()

    connection.execute(
        "UPDATE alerts SET is_read = 1 WHERE is_read = 0"
    )

    connection.commit()
    connection.close()

    return {"status": "ok"}
