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
             organization_id,
             decommissioned_at,
             succeeds_device_id
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


def decommission_device(device_id):
    """
    Da de baja un equipo. No borra nada.

    Dos cosas, en una sola transaccion:

      1. Se revoca la credencial, para que el Agent no pueda volver a
         autenticarse.

      2. Se anota la fecha de baja.

    Van juntas a proposito: un unico commit al final. Si cualquiera
    de las dos falla, SQLite descarta ambas al cerrar la conexion sin
    confirmar, y el equipo se queda exactamente como estaba.

    Eso evita el estado peligroso, que seria quedar marcado como dado
    de baja conservando el acceso: el panel diria que el equipo esta
    retirado mientras su Agent sigue conectandose. Comprobado
    provocando el fallo en cada uno de los dos pasos.

    La fila se conserva entera —hostname, fechas, organizacion— junto
    con sus grabaciones, sus alertas y su rastro en la auditoria. Dar
    de baja es decir "este equipo ya no esta en servicio", no "este
    equipo no existio nunca".

    Devuelve False si el equipo no existe o ya estaba de baja, para
    que quien llame no anuncie una baja que no ha ocurrido.
    """

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT decommissioned_at FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()

        if fila is None or fila["decommissioned_at"]:
            return False

        # 1. La credencial, primero
        connection.execute(
            "UPDATE devices SET agent_token_active = 0 WHERE device_id = ?",
            (device_id,)
        )

        # 2. La baja
        connection.execute(
            "UPDATE devices SET decommissioned_at = ? WHERE device_id = ?",
            (datetime.now(timezone.utc).isoformat(), device_id)
        )

        connection.commit()

    finally:
        connection.close()

    return True


def is_decommissioned(device_id):
    """True si el equipo esta dado de baja."""

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT decommissioned_at FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()

    finally:
        connection.close()

    return bool(fila and fila["decommissioned_at"])


def reinstate_device(device_id):
    """
    Devuelve un equipo al servicio.

    Quita la marca de baja, pero NO devuelve la credencial: sigue
    revocada. Para volver a conectarse, el equipo tiene que darse de
    alta otra vez con una credencial de alta valida, y asi recupera su
    historial sin que una credencial vieja vuelva a valer.

    Devuelve False si no existe o no estaba de baja.
    """

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT decommissioned_at FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()

        if fila is None or not fila["decommissioned_at"]:
            return False

        connection.execute(
            "UPDATE devices SET decommissioned_at = NULL WHERE device_id = ?",
            (device_id,)
        )

        connection.commit()

    finally:
        connection.close()

    return True


class LinkError(Exception):
    """La vinculacion no se puede hacer sin arriesgar el historial."""


def link_device_history(nuevo_id, anterior_id):
    """
    Hace que un equipo recien registrado herede el historial de uno
    dado de baja.

    No toca ninguna grabacion: solo anota en el equipo nuevo a quien
    sucede. El viejo se queda de baja y con su credencial revocada
    para siempre.

    Lanza LinkError si no se puede garantizar que la vinculacion es
    limpia. Ante la duda no se vincula: un historial partido es un
    problema menor que uno mezclado con el de otra maquina.
    """

    if not nuevo_id or not anterior_id:
        raise LinkError("Faltan los equipos que vincular")

    if nuevo_id == anterior_id:
        raise LinkError("Un equipo no puede sucederse a si mismo")

    connection = get_connection()

    try:
        nuevo = connection.execute(
            "SELECT device_id, organization_id, decommissioned_at, "
            "succeeds_device_id FROM devices WHERE device_id = ?",
            (nuevo_id,)
        ).fetchone()

        anterior = connection.execute(
            "SELECT device_id, organization_id, decommissioned_at "
            "FROM devices WHERE device_id = ?",
            (anterior_id,)
        ).fetchone()

        if nuevo is None:
            raise LinkError("El equipo nuevo no existe")

        if anterior is None:
            raise LinkError("El equipo anterior no existe")

        # Misma organizacion. Sin esto, una empresa podria quedarse
        # con el historial de otra.
        if nuevo["organization_id"] != anterior["organization_id"]:
            raise LinkError(
                "Los dos equipos deben ser de la misma organizacion"
            )

        # El predecesor tiene que estar de baja: heredar el historial
        # de un equipo que sigue en servicio lo dejaria asociado a dos
        # maquinas activas a la vez.
        if not anterior["decommissioned_at"]:
            raise LinkError(
                "El equipo anterior debe estar dado de baja"
            )

        if nuevo["decommissioned_at"]:
            raise LinkError(
                "El equipo nuevo esta dado de baja"
            )

        if nuevo["succeeds_device_id"]:
            raise LinkError(
                "El equipo nuevo ya tiene un historial vinculado"
            )

        # Un historial, un heredero.
        ya_heredado = connection.execute(
            "SELECT device_id FROM devices WHERE succeeds_device_id = ?",
            (anterior_id,)
        ).fetchone()

        if ya_heredado is not None:
            raise LinkError(
                "Ese historial ya esta vinculado a otro equipo"
            )

        connection.execute(
            "UPDATE devices SET succeeds_device_id = ? WHERE device_id = ?",
            (anterior_id, nuevo_id)
        )

        connection.commit()

    finally:
        connection.close()

    return True


def unlink_device_history(device_id):
    """
    Deshace una vinculacion.

    Como no se movio ninguna grabacion al vincular, deshacerlo es
    quitar el puntero: los dos historiales vuelven a estar separados y
    ninguno ha perdido nada.
    """

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT succeeds_device_id FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()

        if fila is None or not fila["succeeds_device_id"]:
            return None

        anterior = fila["succeeds_device_id"]

        connection.execute(
            "UPDATE devices SET succeeds_device_id = NULL "
            "WHERE device_id = ?",
            (device_id,)
        )

        connection.commit()

    finally:
        connection.close()

    return anterior


# Tope de saltos al seguir la cadena de predecesores.
#
# Un equipo reinstalado muchas veces encadena varios registros. El
# tope existe para que un dato corrupto —una cadena que se muerda la
# cola— no deje el servidor dando vueltas.
MAX_PREDECESORES = 50


def device_history_chain(device_id):
    """
    El equipo y todos los que le precedieron, del mas nuevo al mas
    viejo.

    Sirve para enseñar el historial completo sin haber tocado ni una
    grabacion: se consultan las de todos los identificadores de la
    cadena.
    """

    if not device_id:
        return []

    cadena = [device_id]
    vistos = {device_id}

    connection = get_connection()

    try:
        actual = device_id

        for _ in range(MAX_PREDECESORES):

            fila = connection.execute(
                "SELECT succeeds_device_id FROM devices "
                "WHERE device_id = ?",
                (actual,)
            ).fetchone()

            if fila is None or not fila["succeeds_device_id"]:
                break

            actual = fila["succeeds_device_id"]

            # Una cadena que se muerde la cola no deberia existir,
            # pero si existiera no puede colgar el servidor.
            if actual in vistos:
                break

            vistos.add(actual)
            cadena.append(actual)

    finally:
        connection.close()

    return cadena


def decommissioned_devices(organization_id):
    """
    Equipos dados de baja de una organizacion que todavia no tienen
    heredero.

    Es lo que se ofrece al vincular: los que ya fueron heredados no
    salen, porque un historial solo puede tener un sucesor.
    """

    connection = get_connection()

    try:
        filas = connection.execute(
            """
            SELECT d.device_id, d.hostname, d.operating_system,
                   d.created_at, d.decommissioned_at, d.last_seen
            FROM devices d
            WHERE d.organization_id = ?
              AND d.decommissioned_at IS NOT NULL
              AND NOT EXISTS (
                    SELECT 1 FROM devices s
                    WHERE s.succeeds_device_id = d.device_id
              )
            ORDER BY d.decommissioned_at DESC
            """,
            (organization_id,)
        ).fetchall()

    finally:
        connection.close()

    return [dict(f) for f in filas]


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
