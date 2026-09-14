"""
Grabación de pantalla del Agent (Fase 1: solo local).

- Captura la pantalla con mss a la resolución actual.
- Codifica a MP4 / H.264 con ffmpeg (binario de imageio-ffmpeg).
- Divide la grabación en segmentos (por defecto 15 minutos).
- Guarda los segmentos en la carpeta de datos persistente:
      C:\\ProgramData\\RemoteAdmin\\recordings\\AAAA\\MM\\DD\\

Esta fase NO toca el WebSocket, el backend ni la captura en vivo. El grabador
usa su propio contexto mss y su propio hilo; no comparte estado mutable con el
resto del Agent.
"""

import os
import threading
import time
import subprocess
from datetime import datetime

import mss
import imageio_ffmpeg

from paths import get_recordings_dir


# Parámetros por defecto (configurables al crear el grabador)
DEFAULT_FPS = 15
DEFAULT_SEGMENT_SECONDS = 15 * 60  # 15 minutos


class ScreenRecorder:

    def __init__(self, fps=DEFAULT_FPS, segment_seconds=DEFAULT_SEGMENT_SECONDS):

        self.fps = max(1, int(fps))
        self.segment_seconds = max(5, int(segment_seconds))

        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._running = False
        self._current_path = None
        self._segments = []

    # ---------- Control ----------

    def is_recording(self):
        with self._lock:
            return self._running

    def start(self):

        with self._lock:

            if self._running:
                return {
                    "status": "already_recording",
                    "path": self._current_path
                }

            self._stop_event.clear()
            self._segments = []
            self._running = True

            self._thread = threading.Thread(
                target=self._record_loop,
                name="ScreenRecorder",
                daemon=True
            )
            self._thread.start()

        return {"status": "recording_started"}

    def stop(self):

        with self._lock:

            if not self._running:
                return {"status": "not_recording"}

            self._stop_event.set()
            thread = self._thread

        # Espera a que el hilo cierre el segmento en curso (fuera del lock)
        if thread is not None:
            thread.join(timeout=30)

        with self._lock:
            self._running = False
            segments = list(self._segments)

        return {
            "status": "recording_stopped",
            "segments": segments
        }

    # ---------- Interno ----------

    def _segment_path(self):

        now = datetime.now()

        folder = os.path.join(
            get_recordings_dir(),
            now.strftime("%Y"),
            now.strftime("%m"),
            now.strftime("%d")
        )

        os.makedirs(folder, exist_ok=True)

        filename = f"rec_{int(now.timestamp())}.mp4"

        return os.path.join(folder, filename)

    def _open_ffmpeg(self, width, height):

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        # Recibe frames BGRA crudos por stdin y produce MP4 H.264 reproducible
        command = [
            ffmpeg_exe,
            "-y",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-pix_fmt", "bgra",
            "-s", f"{width}x{height}",
            "-r", str(self.fps),
            "-i", "-",
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            self._current_path
        ]

        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )

    def _record_loop(self):

        frame_interval = 1.0 / self.fps

        try:

            with mss.mss() as screen:

                monitor = screen.monitors[1]
                width = monitor["width"]
                height = monitor["height"]

                while not self._stop_event.is_set():

                    # Abre un segmento nuevo
                    self._current_path = self._segment_path()
                    process = self._open_ffmpeg(width, height)

                    segment_start = time.time()
                    next_frame = segment_start

                    try:

                        while not self._stop_event.is_set():

                            frame = screen.grab(monitor)

                            # Si la resolución cambió, cierra el segmento y reabre
                            if frame.width != width or frame.height != height:
                                break

                            try:
                                process.stdin.write(frame.raw)
                            except (BrokenPipeError, OSError):
                                break

                            # Corta el segmento al cumplir la duración
                            if time.time() - segment_start >= self.segment_seconds:
                                break

                            next_frame += frame_interval
                            sleep_for = next_frame - time.time()

                            if sleep_for > 0:
                                if self._stop_event.wait(timeout=sleep_for):
                                    break
                            else:
                                # Va retrasado: no acumula deuda de tiempo
                                next_frame = time.time()

                    finally:

                        self._close_segment(process, width, height)

                    # Si cambió la resolución, relee el tamaño actual
                    monitor = screen.monitors[1]
                    width = monitor["width"]
                    height = monitor["height"]

        except Exception as error:

            print(f"[recorder] Error en la grabación: {error}")

        finally:

            with self._lock:
                self._running = False

    def _close_segment(self, process, width, height):

        path = self._current_path

        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass

        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

        # Registra el segmento si quedó un archivo válido
        if path and os.path.exists(path) and os.path.getsize(path) > 0:

            with self._lock:
                self._segments.append({
                    "path": path,
                    "size_bytes": os.path.getsize(path),
                    "width": width,
                    "height": height
                })

            print(f"[recorder] Segmento guardado: {path}")

        else:

            stderr = b""
            try:
                if process.stderr:
                    stderr = process.stderr.read() or b""
            except OSError:
                pass

            print(
                "[recorder] Segmento vacío o fallido"
                + (f": {stderr.decode('utf-8', 'replace').strip()}" if stderr else "")
            )
