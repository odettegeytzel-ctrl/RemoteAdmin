"""
Almacenamiento e historial de grabaciones (Fase 3).

Los MP4 llegan del Agent y se guardan en una carpeta del SERVIDOR, separada de
la del Agent. En SQLite se registra una fila por segmento.
"""

import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

from backend.database import get_connection


RETENTION_DAYS = 90


# Carpeta de grabaciones del servidor (separada de la del Agent)
BASE_DIR = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = BASE_DIR / "server_recordings"


def get_recordings_dir():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDINGS_DIR


def add_recording(device_id, path, started_at, ended_at, duration_sec, size_bytes):

    connection = get_connection()

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
    recording_id = cursor.lastrowid
    connection.close()

    return recording_id


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


def apply_retention(days=RETENTION_DAYS):
    """
    Elimina grabaciones cuya fecha de fin supera `days` días, excepto las
    marcadas con keep=1 o las que no estén en estado 'stored' (activas).
    Devuelve un resumen. Es seguro: nunca borra fuera de server_recordings.
    """

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    connection = get_connection()

    rows = connection.execute(
        """
        SELECT id, path, ended_at, keep, status
        FROM recordings
        WHERE keep = 0 AND status = 'stored'
        """
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


def get_recording(recording_id):

    connection = get_connection()

    row = connection.execute(
        """
        SELECT id, device_id, started_at, ended_at,
               duration_sec, size_bytes, path, status, keep, created_at
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
               r.created_at, d.hostname
        FROM recordings r
        LEFT JOIN devices d ON d.device_id = r.device_id
    """

    if device_id:
        rows = connection.execute(
            base_query + " WHERE r.device_id = ? ORDER BY r.started_at DESC",
            (device_id,)
        ).fetchall()
    else:
        rows = connection.execute(
            base_query + " ORDER BY r.started_at DESC"
        ).fetchall()

    connection.close()

    return [dict(row) for row in rows]
