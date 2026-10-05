import hmac
import sqlite3

from datetime import datetime, timezone, timedelta

from backend.database import get_connection
from backend.models import DeviceRegister, DeviceHeartbeat
from backend.auth import (
    generate_device_id,
    generate_agent_token,
    hash_agent_token,
    agent_token_matches
)


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


def set_continuous_recording(device_id, enabled):
    connection = get_connection()

    cursor = connection.execute(
        "UPDATE devices SET continuous_recording = ? WHERE device_id = ?",
        (1 if enabled else 0, device_id)
    )

    connection.commit()
    changed = cursor.rowcount
    connection.close()

    return changed > 0


def get_continuous_recording(device_id):
    connection = get_connection()

    row = connection.execute(
        "SELECT continuous_recording FROM devices WHERE device_id = ?",
        (device_id,)
    ).fetchone()

    connection.close()

    if row is None:
        return None

    return bool(row["continuous_recording"])


def update_system_info(device_id, system_info):
    connection = get_connection()

    disks = system_info.get("disks", [])

    storage_total = None
    storage_free = None
    storage_used = None

    if disks:
        system_disk = next(
            (
                disk
                for disk in disks
                if disk.get("mountpoint", "").upper() == "C:\\"
            ),
            disks[0]
        )

        storage_total = system_disk.get("total")
        storage_free = system_disk.get("free")
        storage_used = system_disk.get("used")

        cursor = connection.execute(
        """
        UPDATE devices
        SET
            hostname = ?,
            username = ?,
            operating_system = ?,
            ip_address = ?,
            processor = ?,
            cpu_count = ?,
            ram_total = ?,
            ram_available = ?,
            ram_used = ?,
            ram_percent = ?,
            storage_total = ?,
            storage_free = ?,
            storage_used = ?,
            windows_version = ?,
            architecture = ?,
            manufacturer = ?,
            model = ?
        WHERE device_id = ?
        """,
        (
            system_info.get("hostname"),
            system_info.get("username"),
            system_info.get("operating_system"),
            system_info.get("ip_address"),
            system_info.get("processor"),
            system_info.get("cpu_count"),
            system_info.get("ram_total"),
            system_info.get("ram_available"),
            system_info.get("ram_used"),
            system_info.get("ram_percent"),
            storage_total,
            storage_free,
            storage_used,
            system_info.get("windows_version"),
            system_info.get("architecture"),
            system_info.get("manufacturer"),
            system_info.get("model"),
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

def update_installed_software(device_id, software):
    connection = get_connection()

    connection.execute(
        "DELETE FROM installed_software WHERE device_id = ?",
        (device_id,)
    )

    for item in software:
        connection.execute(
            """
            INSERT INTO installed_software (
                device_id,
                name,
                version,
                publisher,
                install_date
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                device_id,
                item.get("name"),
                item.get("version"),
                item.get("publisher"),
                item.get("install_date")
            )
        )

    connection.commit()
    connection.close()

    return {
        "status": "updated",
        "device_id": device_id,
        "software_count": len(software)
    }


def get_installed_software(device_id):
    connection = get_connection()

    software = connection.execute(
        """
        SELECT
            name,
            version,
            publisher,
            install_date
        FROM installed_software
        WHERE device_id = ?
        ORDER BY name
        """,
        (device_id,)
    ).fetchall()

    connection.close()

    return [dict(item) for item in software]


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
             ram_total,
             ram_available,
             ram_used,
             ram_percent,
             storage_total,
             storage_free,
             storage_used,
             windows_version,
             architecture,
             manufacturer,
             model,
             status,
             last_seen,
             created_at,
             organization_id
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

# ==============================
# CREDENCIALES INDIVIDUALES POR DISPOSITIVO
# ==============================
#
# Cada Agent tiene su propio token. En la base se guarda SOLO su SHA-256:
# el valor en claro se entrega una única vez al Agent y no se conserva.
#
# Esta etapa solo añade el modelo y las funciones. Los endpoints de registro,
# heartbeat, WebSocket y subidas siguen usando el AGENT_TOKEN compartido.


def issue_agent_token(device_id):
    """
    Genera un token individual para un dispositivo YA existente y guarda su
    hash, marcándolo como activo.

    Devuelve el token en claro (única vez que existe fuera del Agent) o None
    si el device_id no existe.
    """

    token = generate_agent_token()
    now = datetime.now(timezone.utc).isoformat()

    connection = get_connection()

    cursor = connection.execute(
        """
        UPDATE devices
        SET
            agent_token_hash = ?,
            agent_token_issued_at = ?,
            agent_token_active = 1
        WHERE device_id = ?
        """,
        (
            hash_agent_token(token),
            now,
            device_id
        )
    )

    connection.commit()
    connection.close()

    if cursor.rowcount == 0:
        return None

    return token


def get_device_id_for_token(token):
    """
    Devuelve el device_id al que pertenece un token individual, o None si el
    token no es válido o el dispositivo está revocado.

    La comparación se hace en tiempo constante sobre el hash. Se busca por
    hash en lugar de recorrer todas las filas: el hash es determinista, así
    que una sola consulta indexable basta.
    """

    if not token:
        return None

    connection = get_connection()

    row = connection.execute(
        """
        SELECT device_id, agent_token_hash, agent_token_active
        FROM devices
        WHERE agent_token_hash = ?
        """,
        (hash_agent_token(token),)
    ).fetchone()

    connection.close()

    if row is None:
        return None

    # Revocado (0) o sin token individual asignado (NULL)
    if not row["agent_token_active"]:
        return None

    # Confirmación en tiempo constante: la consulta ya filtró por hash, pero
    # así la comparación final nunca depende del contenido del token.
    if not agent_token_matches(token, row["agent_token_hash"]):
        return None

    return row["device_id"]


def verify_device_token(token, device_id):
    """
    Comprueba que un token individual pertenece exactamente a ese device_id.
    Útil para endpoints que reciben el device_id en la ruta.
    """

    resolved = get_device_id_for_token(token)

    if resolved is None:
        return False

    return hmac.compare_digest(resolved, device_id or "")


def revoke_agent_token(device_id):
    """
    Desactiva la credencial de un dispositivo sin borrar sus datos.
    Devuelve True si se revocó, False si el device_id no existe.
    """

    connection = get_connection()

    cursor = connection.execute(
        "UPDATE devices SET agent_token_active = 0 WHERE device_id = ?",
        (device_id,)
    )

    connection.commit()
    connection.close()

    return cursor.rowcount > 0


def get_device_organization(device_id):
    """
    Organizacion a la que pertenece un equipo.

    La asigna el servidor en el alta y no hay ninguna via por la que el
    Agent pueda cambiarla: su peticion no lleva organizacion, y si la
    llevara no se leeria.
    """

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT organization_id FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()

    finally:
        connection.close()

    return fila["organization_id"] if fila else None


def device_exists(device_id):
    """True si el equipo esta dado de alta."""

    connection = get_connection()

    row = connection.execute(
        "SELECT 1 FROM devices WHERE device_id = ?",
        (device_id,)
    ).fetchone()

    connection.close()

    return row is not None


def device_has_agent_token(device_id):
    """True si el dispositivo ya tiene una credencial individual asignada."""

    connection = get_connection()

    row = connection.execute(
        "SELECT agent_token_hash FROM devices WHERE device_id = ?",
        (device_id,)
    ).fetchone()

    connection.close()

    return bool(row and row["agent_token_hash"])


def enroll_device(hostname, operating_system, ip_address,
                  organization_id=None):
    """
    Alta de un Agent NUEVO.

    El servidor genera el device_id y el token individual: el cliente no
    elige su identidad, así que no puede reclamar la de otro equipo. Siempre
    INSERT, nunca UPDATE, de modo que un alta no puede tocar una fila
    existente.

    Devuelve (device_id, token). El token va en claro SOLO en esta respuesta;
    en la base queda únicamente su SHA-256.
    """

    now = datetime.now(timezone.utc).isoformat()

    connection = get_connection()

    try:

        # Reintentos por si un device_id aleatorio colisionara (improbable:
        # 64 bits), para no fallar un alta legítima por azar.
        for _ in range(5):

            device_id = generate_device_id()
            token = generate_agent_token()

            try:
                connection.execute(
                    """
                    INSERT INTO devices (
                        device_id,
                        hostname,
                        operating_system,
                        ip_address,
                        status,
                        last_seen,
                        agent_token_hash,
                        agent_token_issued_at,
                        agent_token_active,
                        organization_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        device_id,
                        hostname,
                        operating_system,
                        ip_address,
                        "online",
                        now,
                        hash_agent_token(token),
                        now,
                        organization_id
                    )
                )

            except sqlite3.IntegrityError:
                # device_id ya en uso: se prueba con otro
                continue

            connection.commit()

            return device_id, token

        raise RuntimeError(
            "No se pudo generar un device_id libre para el alta"
        )

    finally:
        connection.close()
