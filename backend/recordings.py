"""
Almacenamiento e historial de grabaciones (Fase 3).

Los MP4 llegan del Agent y se guardan en una carpeta del SERVIDOR, separada de
la del Agent. En SQLite se registra una fila por segmento.
"""

import os
from pathlib import Path

from backend.database import get_connection


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


def list_recordings(device_id=None):

    connection = get_connection()

    if device_id:
        rows = connection.execute(
            """
            SELECT id, device_id, started_at, ended_at,
                   duration_sec, size_bytes, path, status, keep, created_at
            FROM recordings
            WHERE device_id = ?
            ORDER BY started_at DESC
            """,
            (device_id,)
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT id, device_id, started_at, ended_at,
                   duration_sec, size_bytes, path, status, keep, created_at
            FROM recordings
            ORDER BY started_at DESC
            """
        ).fetchall()

    connection.close()

    return [dict(row) for row in rows]
