from datetime import datetime, timezone, timedelta

from backend.database import get_connection
from backend.settings import get_settings


OFFLINE_AFTER_SECONDS = 30


# ==============================
# SALUD DEL EQUIPO (RAM y DISCO)
# ==============================
#
# Puntos porcentuales que hay que BAJAR por debajo de un umbral para
# considerar que el recurso salió de ese nivel. Sin este margen, un equipo
# oscilando entre 89% y 91% generaría una alerta en cada comprobación.
HYSTERESIS_MARGIN = 5

# Umbrales por defecto si la configuración no tuviera valores válidos
FALLBACK_THRESHOLDS = {
    "ram": (75, 90),
    "disk": (75, 90)
}


def _read_threshold(settings, key, default):
    """Lee un umbral de settings (texto) y lo devuelve como entero."""

    try:
        return int(float(settings.get(key, default)))
    except (TypeError, ValueError):
        return default


def get_health_thresholds():
    """
    Umbrales de aviso y crítico para RAM y disco, desde la configuración.

    Devuelve {"ram": (warning, critical), "disk": (warning, critical)}.
    Si algún valor es inválido o incoherente (aviso por encima de crítico),
    se usa el de respaldo: es preferible avisar de más que dejar de avisar.
    """

    settings = get_settings()

    thresholds = {}

    for recurso in ("ram", "disk"):

        por_defecto = FALLBACK_THRESHOLDS[recurso]

        warning = _read_threshold(
            settings,
            f"{recurso}_warning_percent",
            por_defecto[0]
        )

        critical = _read_threshold(
            settings,
            f"{recurso}_critical_percent",
            por_defecto[1]
        )

        if warning >= critical:
            warning, critical = por_defecto

        thresholds[recurso] = (warning, critical)

    return thresholds


def _next_health_status(percent, previous, warning, critical):
    """
    Estado que corresponde a un porcentaje, aplicando histéresis.

    Se SUBE de nivel al alcanzar el umbral, pero se BAJA solo al quedar
    HYSTERESIS_MARGIN puntos por debajo. Así una oscilación alrededor del
    umbral no produce una alerta por cada lectura.
    """

    if percent >= critical:
        return "critical"

    if previous == "critical":
        # Sigue crítico hasta bajar del umbral crítico menos el margen
        if percent > critical - HYSTERESIS_MARGIN:
            return "critical"

    if percent >= warning:
        return "warning"

    if previous in ("warning", "critical"):
        # Sigue en aviso hasta bajar del umbral de aviso menos el margen
        if percent > warning - HYSTERESIS_MARGIN:
            return "warning"

    return "ok"


def _health_alert(resource, previous, current, percent, hostname):
    """
    Alerta que corresponde a una transición, o None si no hay que avisar.

    - Subidas a warning o critical: se avisa.
    - Bajada de critical a warning: NO se avisa (el equipo sigue mal; avisar
      de una mejora parcial solo añadiría ruido).
    - Vuelta a ok desde warning o critical: alerta de recuperación.
    - Primera observación (previous None): se avisa solo si ya nace en
      warning o critical; de lo contrario un equipo que se registra con el
      disco al 98% no avisaría nunca.
    """

    if previous == current:
        return None

    etiqueta = "memoria RAM" if resource == "ram" else "disco"
    redondeado = int(round(percent))

    if current == "critical":
        return (
            f"{resource}_critical",
            f"{hostname}: {etiqueta} al {redondeado}%"
        )

    if current == "warning":

        # De critical a warning no se avisa: sigue estando mal
        if previous == "critical":
            return None

        return (
            f"{resource}_warning",
            f"{hostname}: {etiqueta} al {redondeado}%"
        )

    # current == "ok"
    if previous is None:
        # Primera observación y ya está bien: solo se fija la base
        return None

    return (
        f"{resource}_recovered",
        f"{hostname}: {etiqueta} recuperada ({redondeado}%)"
        if resource == "ram"
        else f"{hostname}: {etiqueta} recuperado ({redondeado}%)"
    )


def _disk_percent(device):
    """Porcentaje de disco usado, o None si el equipo no reportó datos."""

    total = device.get("storage_total")
    free = device.get("storage_free")

    if not total:
        return None

    if free is None:
        return None

    return (total - free) / total * 100


def detect_alerts():
    # Compara el estado real (segun last_seen) con el estado guardado.
    # Solo genera una alerta cuando hay un cambio, asi se evitan duplicados.
    connection = get_connection()

    devices = connection.execute(
        """
        SELECT
            device_id,
            hostname,
            last_alert_status,
            last_seen,
            ram_percent,
            storage_total,
            storage_free,
            last_ram_status,
            last_disk_status
        FROM devices
        """
    ).fetchall()

    # Se leen una sola vez para todos los equipos
    thresholds = get_health_thresholds()

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

        def guardar_alerta(alert_type, message):
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

        # --- Conexion (comportamiento original, sin cambios) ---
        if last_alert_status is None:
            # Primera vez: se fija la base sin generar alerta
            connection.execute(
                "UPDATE devices SET last_alert_status = ? WHERE device_id = ?",
                (real_status, device["device_id"])
            )

        elif real_status != last_alert_status:

            if real_status == "online":
                guardar_alerta("online", f"{device['hostname']} se conecto")
            else:
                guardar_alerta("offline", f"{device['hostname']} se desconecto")

            connection.execute(
                "UPDATE devices SET last_alert_status = ? WHERE device_id = ?",
                (real_status, device["device_id"])
            )

        # --- Salud: RAM y disco, cada uno con su estado ---
        lecturas = (
            ("ram", device["ram_percent"], device["last_ram_status"], "last_ram_status"),
            ("disk", _disk_percent(device), device["last_disk_status"], "last_disk_status")
        )

        for recurso, percent, previo, columna in lecturas:

            # Sin datos: no se toca el estado ni se genera alerta. No saber
            # como esta un equipo no es lo mismo que saber que esta mal.
            if percent is None:
                continue

            warning, critical = thresholds[recurso]

            actual = _next_health_status(percent, previo, warning, critical)

            if actual == previo:
                continue

            alerta = _health_alert(
                recurso,
                previo,
                actual,
                percent,
                device["hostname"]
            )

            if alerta is not None:
                guardar_alerta(*alerta)

            connection.execute(
                f"UPDATE devices SET {columna} = ? WHERE device_id = ?",
                (actual, device["device_id"])
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
