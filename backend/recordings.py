"""
Almacenamiento e historial de grabaciones (Fase 3).

Los MP4 llegan del Agent y se guardan en una carpeta del SERVIDOR, separada de
la del Agent. En SQLite se registra una fila por segmento.
"""

import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

import sqlite3
import uuid

from backend.database import get_connection


RETENTION_DAYS = 90


# Carpeta de grabaciones del servidor (separada de la del Agent)
BASE_DIR = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = BASE_DIR / "server_recordings"


def get_recordings_dir():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDINGS_DIR


class RecordingAlreadyExists(Exception):
    """
    La grabación ya estaba registrada (índice único (device_id, path)).

    Ocurre cuando dos subidas del mismo segmento llegan a la vez: la primera
    inserta y la segunda choca con el índice. No es un error del cliente, así
    que el endpoint la trata como duplicado, no como fallo.
    """

    def __init__(self, recording_id):
        super().__init__(f"La grabación ya existe (id={recording_id})")
        self.recording_id = recording_id


def add_recording(device_id, path, started_at, ended_at, duration_sec, size_bytes):

    connection = get_connection()

    try:

        cursor = connection.execute(
            """
            INSERT INTO recordings (
                device_id, started_at, ended_at,
                duration_sec, size_bytes, path, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'stored')
            """,
            (
                device_id,
                started_at,
                ended_at,
                duration_sec,
                size_bytes,
                path
            )
        )

        connection.commit()

        return cursor.lastrowid

    except sqlite3.IntegrityError:

        # Otra subida del mismo segmento ganó la carrera: se devuelve su id
        # en lugar de crear una segunda fila para el mismo archivo.
        fila = connection.execute(
            "SELECT id FROM recordings WHERE device_id = ? AND path = ?",
            (device_id, path)
        ).fetchone()

        raise RecordingAlreadyExists(fila["id"] if fila else None)

    finally:
        connection.close()


# ==============================
# ESTADO DE ALMACENAMIENTO
# ==============================
#
# Desde el cambio de modelo, el equipo es el almacen principal: las
# grabaciones nuevas NO se suben solas. El servidor conoce su ficha
# (device_id, nombre, fechas, tamano) y solo recibe el archivo cuando un
# administrador decide archivarlo.

STORAGE_LOCAL_ONLY = "local_only"
STORAGE_PENDING = "server_pending"
STORAGE_STORED = "server_stored"
STORAGE_ERROR = "server_error"


def storage_state_of(fila):
    """
    Estado de almacenamiento de una fila, interpretando el historico.

    Las filas creadas antes de esta columna llevan NULL. Son del modelo
    anterior, en el que subir era automatico: si tienen archivo en el
    servidor, estan archivadas.
    """

    if not fila:
        return None

    estado = fila["storage_state"]

    if estado:
        return estado

    return STORAGE_STORED if fila["status"] == "stored" else STORAGE_LOCAL_ONLY


def set_storage_state(recording_id, estado, status=None):
    """Cambia el estado de almacenamiento. Nunca toca el archivo."""

    connection = get_connection()

    try:
        if status is None:
            connection.execute(
                "UPDATE recordings SET storage_state = ? WHERE id = ?",
                (estado, recording_id)
            )
        else:
            connection.execute(
                "UPDATE recordings SET storage_state = ?, status = ? "
                "WHERE id = ?",
                (estado, status, recording_id)
            )

        connection.commit()

    finally:
        connection.close()

    return True


def register_local_recording(device_id, path, started_at, ended_at,
                             duration_sec, size_bytes):
    """
    Anota una grabacion que existe SOLO en el equipo.

    Es la ficha, no el archivo: el servidor sabe que la grabacion existe y
    puede ofrecerla en el panel, pero no tiene el video. status='local' la
    mantiene fuera de la retencion del servidor y de todo lo que sirve
    archivos, que filtran por status='stored'.

    Si la ficha ya existe no se duplica ni se pisa: una grabacion ya
    archivada no debe volver a "solo local" porque llegue un aviso
    repetido del Agent.

    Devuelve (id, creada).
    """

    connection = get_connection()

    try:

        existente = connection.execute(
            "SELECT id FROM recordings WHERE device_id = ? AND path = ?",
            (device_id, path)
        ).fetchone()

        if existente is not None:
            return existente["id"], False

        cursor = connection.execute(
            """
            INSERT INTO recordings (
                device_id, started_at, ended_at, duration_sec,
                size_bytes, path, status, keep, storage_state
            )
            VALUES (?, ?, ?, ?, ?, ?, 'local', 0, ?)
            """,
            (device_id, started_at, ended_at, duration_sec,
             size_bytes, path, STORAGE_LOCAL_ONLY)
        )

        connection.commit()

        return cursor.lastrowid, True

    except sqlite3.IntegrityError:

        # El indice unico (device_id, path) gano la carrera: la ficha ya
        # la creo otro aviso simultaneo.
        fila = connection.execute(
            "SELECT id FROM recordings WHERE device_id = ? AND path = ?",
            (device_id, path)
        ).fetchone()

        return (fila["id"] if fila else None), False

    finally:
        connection.close()


def mark_stored(recording_id, size_bytes=None, duration_sec=None):
    """Pasa una grabacion a archivada en el servidor, ya validada."""

    connection = get_connection()

    try:
        connection.execute(
            """
            UPDATE recordings
            SET status = 'stored',
                storage_state = ?,
                size_bytes = COALESCE(?, size_bytes),
                duration_sec = COALESCE(?, duration_sec)
            WHERE id = ?
            """,
            (STORAGE_STORED, size_bytes, duration_sec, recording_id)
        )
        connection.commit()

    finally:
        connection.close()

    return True


def get_recording_by_path(device_id, rel_path):
    """Fila completa por su ruta relativa, o None."""

    connection = get_connection()

    try:
        fila = connection.execute(
            "SELECT * FROM recordings WHERE device_id = ? AND path = ?",
            (device_id, rel_path)
        ).fetchone()

    finally:
        connection.close()

    return dict(fila) if fila else None


def find_recording_by_path(device_id, rel_path):
    # Evita filas duplicadas cuando el Agent reintenta subir un segmento
    connection = get_connection()
    row = connection.execute(
        "SELECT id FROM recordings WHERE device_id = ? AND path = ?",
        (device_id, rel_path)
    ).fetchone()
    connection.close()
    return row["id"] if row else None


def set_keep(recording_id, keep):

    connection = get_connection()

    cursor = connection.execute(
        "UPDATE recordings SET keep = ? WHERE id = ?",
        (1 if keep else 0, recording_id)
    )

    connection.commit()
    changed = cursor.rowcount
    connection.close()

    return changed > 0


def _resolve_inside_dir(rel_path):

    # Devuelve la ruta absoluta SOLO si queda dentro de la carpeta de grabaciones;
    # en caso contrario None (protección contra path traversal).
    base = os.path.realpath(str(get_recordings_dir()))
    target = os.path.realpath(os.path.join(base, rel_path or ""))

    if target != base and not target.startswith(base + os.sep):
        return None

    return target


def apply_retention(days=RETENTION_DAYS, organization_id=None):
    """
    Elimina grabaciones cuya fecha de fin supera `days` días, excepto las
    marcadas con keep=1 o las que no estén en estado 'stored' (activas).
    Devuelve un resumen. Es seguro: nunca borra fuera de server_recordings.

    Con organization_id solo se tocan las grabaciones de los equipos de
    esa empresa. Cada organizacion tiene su propio periodo, y aplicar el
    de una a las grabaciones de otra borraria material que todavia
    debia conservarse.
    """

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    connection = get_connection()

    if organization_id is None:
        rows = connection.execute(
            """
            SELECT id, path, ended_at, keep, status
            FROM recordings
            WHERE keep = 0 AND status = 'stored'
            """
        ).fetchall()

    else:
        rows = connection.execute(
            """
            SELECT r.id, r.path, r.ended_at, r.keep, r.status
            FROM recordings r
            JOIN devices d ON d.device_id = r.device_id
            WHERE r.keep = 0 AND r.status = 'stored'
              AND d.organization_id = ?
            """,
            (organization_id,)
        ).fetchall()

    deleted = []           # fila eliminada y su archivo MP4 borrado
    missing_file = []      # fila eliminada, pero el archivo ya no existía (disjunto de deleted)
    errors = []            # no se pudo borrar el archivo; la fila se conserva
    skipped_traversal = [] # ruta fuera de la carpeta; archivo intacto, fila purgada

    for row in rows:

        row = dict(row)

        ended_at = row.get("ended_at")

        if not ended_at:
            continue

        try:
            ended = datetime.fromisoformat(ended_at)
        except ValueError:
            continue

        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)

        if ended >= cutoff:
            # Reciente: se conserva
            continue

        target = _resolve_inside_dir(row.get("path"))

        if target is None:
            # Ruta sospechosa: NO se toca ningún archivo; se purga la fila huérfana
            skipped_traversal.append(row["id"])
            connection.execute(
                "DELETE FROM recordings WHERE id = ?",
                (row["id"],)
            )
            continue

        if os.path.isfile(target):
            try:
                os.remove(target)
            except OSError:
                # No se pudo borrar el archivo: se deja la fila para reintentar
                errors.append(row["id"])
                continue

            # Archivo borrado: se elimina la fila
            connection.execute(
                "DELETE FROM recordings WHERE id = ?",
                (row["id"],)
            )
            deleted.append(row["id"])

        else:
            # El archivo ya no existía: se elimina la fila huérfana.
            # Se contabiliza solo aquí (disjunto de deleted).
            connection.execute(
                "DELETE FROM recordings WHERE id = ?",
                (row["id"],)
            )
            missing_file.append(row["id"])

    connection.commit()
    connection.close()

    return {
        "deleted": deleted,
        "missing_file": missing_file,
        "errors": errors,
        "skipped_traversal": skipped_traversal
    }


def query_recordings(device_id=None, start=None, end=None):
    """
    Historial por rango. Devuelve los segmentos del dispositivo (o de todos)
    que se solapan con el intervalo [start, end] (ISO 8601). Un segmento
    [started_at, ended_at] se solapa si started_at <= end y ended_at >= start.
    Si start/end no se indican, no se filtra por tiempo.
    """

    rows = list_recordings(device_id)

    def parse(value):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    start_dt = parse(start)
    end_dt = parse(end)

    if start_dt is None and end_dt is None:
        return rows

    result = []

    for row in rows:

        seg_start = parse(row.get("started_at"))
        # Si no hay fin, se usa el inicio como fin (segmento puntual)
        seg_end = parse(row.get("ended_at")) or seg_start

        if seg_start is None:
            continue

        if seg_end is None:
            seg_end = seg_start

        if start_dt is not None and seg_end < start_dt:
            continue

        if end_dt is not None and seg_start > end_dt:
            continue

        result.append(row)

    return result


def get_recording(recording_id):

    connection = get_connection()

    row = connection.execute(
        """
        SELECT id, device_id, started_at, ended_at,
               duration_sec, size_bytes, path, status, keep,
               storage_state, created_at
        FROM recordings
        WHERE id = ?
        """,
        (recording_id,)
    ).fetchone()

    connection.close()

    return dict(row) if row else None


def list_recordings(device_id=None):

    connection = get_connection()

    # LEFT JOIN a devices para incluir el hostname (sin alterar recordings)
    base_query = """
        SELECT r.id, r.device_id, r.started_at, r.ended_at,
               r.duration_sec, r.size_bytes, r.path, r.status, r.keep,
               r.storage_state, r.created_at, d.hostname
        FROM recordings r
        LEFT JOIN devices d ON d.device_id = r.device_id
    """

    # Las grabaciones en cuarentena (status='invalid') no se mezclan con las
    # buenas: siguen en la base y en disco, pero no aparecen en el listado.
    #
    # Si aparecen las que estan solo en el equipo (status='local'): el
    # panel debe poder ensenarlas y ofrecer archivarlas. Lo que no tienen
    # es archivo en el servidor, y de eso se ocupa cada endpoint que lo
    # necesite.
    visibles = " WHERE r.status IN ('stored', 'local')"

    if device_id:
        rows = connection.execute(
            base_query + visibles + " AND r.device_id = ?"
                         " ORDER BY r.started_at DESC",
            (device_id,)
        ).fetchall()
    else:
        rows = connection.execute(
            base_query + visibles + " ORDER BY r.started_at DESC"
        ).fetchall()

    connection.close()

    resultado = []

    for row in rows:

        fila = dict(row)

        # El historico sin columna se interpreta aqui, una sola vez, para
        # que el panel y la API no tengan que saber de la migracion.
        fila["storage_state"] = storage_state_of(row)

        resultado.append(fila)

    return resultado


# ==============================
# LIMPIEZA DE SUBIDAS INTERRUMPIDAS
# ==============================

# Extensión del archivo temporal mientras se recibe una subida.
# Un .part nunca es una grabación: es una subida a medias.
PART_SUFFIX = ".part"

# Antigüedad a partir de la cual un .part se considera abandonado.
# Una subida normal tarda segundos; el propio Agent corta a los 120 s de
# tiempo de espera. 6 horas deja un margen enorme antes de tocar nada.
PART_MAX_AGE_SECONDS = 6 * 3600


def cleanup_orphan_parts(max_age_seconds=PART_MAX_AGE_SECONDS):
    """
    Borra los .part abandonados por un reinicio o una caída del backend.

    Se ejecuta UNA vez al arrancar, no en cada petición.

    Deliberadamente conservador:
      - solo archivos que terminan en .part;
      - solo dentro de la carpeta de grabaciones;
      - solo si son más antiguos que max_age_seconds, para no pisar una
        subida que esté ocurriendo ahora mismo;
      - los .mp4 NO se tocan jamás, ni siquiera los huérfanos: eso sería un
        barrido de grabaciones y no es lo que hace esta función.

    Devuelve un resumen de lo borrado y lo conservado.
    """

    base = str(get_recordings_dir())

    ahora = datetime.now(timezone.utc).timestamp()

    resultado = {"deleted": 0, "kept_recent": 0, "errors": 0}

    for raiz, _, archivos in os.walk(base):

        for nombre in archivos:

            if not nombre.endswith(PART_SUFFIX):
                continue

            ruta = os.path.join(raiz, nombre)

            try:
                antiguedad = ahora - os.path.getmtime(ruta)

            except OSError:
                resultado["errors"] += 1
                continue

            if antiguedad < max_age_seconds:
                # Podría ser una subida en curso: no se toca
                resultado["kept_recent"] += 1
                continue

            try:
                os.remove(ruta)
                resultado["deleted"] += 1

            except OSError as error:
                print(f"[grabaciones] No se pudo borrar {ruta}: {error}")
                resultado["errors"] += 1

    if resultado["deleted"]:
        print(
            f"[grabaciones] Limpieza de subidas interrumpidas: "
            f"{resultado['deleted']} archivo(s) .part eliminado(s)"
        )

    return resultado


# ==============================
# CUARENTENA DE GRABACIONES INVÁLIDAS
# ==============================

# Carpeta donde se guardan las grabaciones que no superan la validación.
# No se borran: son la evidencia de que algo falló en el equipo de origen.
INVALID_DIR_NAME = "_invalid"


def get_invalid_dir():

    carpeta = os.path.join(str(get_recordings_dir()), INVALID_DIR_NAME)
    os.makedirs(carpeta, exist_ok=True)

    return carpeta


def quarantine_recording(source_path, device_id, filename):
    """
    Mueve una grabación inválida a la carpeta de cuarentena.

    El nombre lleva el device_id y un sufijo único, de modo que dos archivos
    con el mismo nombre de segmento procedentes de equipos distintos —o del
    mismo equipo en momentos distintos— nunca se pisan.

    Devuelve la ruta relativa dentro de la carpeta de grabaciones, o None si
    no se pudo mover.
    """

    destino_dir = get_invalid_dir()

    seguro = os.path.basename(filename or "grabacion.mp4")

    base, extension = os.path.splitext(seguro)

    unico = f"{device_id}_{base}_{uuid.uuid4().hex[:8]}{extension}"

    destino = os.path.join(destino_dir, unico)

    try:
        os.replace(source_path, destino)

    except OSError as error:
        print(f"[grabaciones] No se pudo poner en cuarentena {source_path}: {error}")
        return None

    return os.path.join(INVALID_DIR_NAME, unico).replace(os.sep, "/")


def add_invalid_recording(device_id, path, started_at, ended_at,
                          duration_sec, size_bytes, reason):
    """
    Registra una grabación que no superó la validación.

    Se guarda con status='invalid' para que quede constancia de que el
    equipo subió algo inservible, pero fuera del listado normal.
    """

    connection = get_connection()

    try:

        cursor = connection.execute(
            """
            INSERT INTO recordings (
                device_id, started_at, ended_at,
                duration_sec, size_bytes, path, status, invalid_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, 'invalid', ?)
            """,
            (
                device_id,
                started_at,
                ended_at,
                duration_sec,
                size_bytes,
                path,
                reason
            )
        )

        connection.commit()

        return cursor.lastrowid

    except sqlite3.IntegrityError:

        fila = connection.execute(
            "SELECT id FROM recordings WHERE device_id = ? AND path = ?",
            (device_id, path)
        ).fetchone()

        raise RecordingAlreadyExists(fila["id"] if fila else None)

    finally:
        connection.close()


def list_invalid_recordings(device_id=None):
    """Grabaciones en cuarentena. No se usa en el panel todavía."""

    connection = get_connection()

    consulta = """
        SELECT id, device_id, started_at, duration_sec, size_bytes,
               path, invalid_reason, created_at
        FROM recordings
        WHERE status = 'invalid'
    """

    if device_id:
        filas = connection.execute(
            consulta + " AND device_id = ? ORDER BY id DESC", (device_id,)
        ).fetchall()
    else:
        filas = connection.execute(
            consulta + " ORDER BY id DESC"
        ).fetchall()

    connection.close()

    return [dict(f) for f in filas]
