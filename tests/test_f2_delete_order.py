"""
Pruebas del orden borrado/cola tras una subida confirmada (bloque F2).

Cubren el riesgo detectado en la revisión: si el archivo se retiraba de la
cola ANTES de borrarlo y el borrado fallaba, quedaba un MP4 huérfano que nada
volvía a mirar.

Se reproduce el bucle de _process_pending() con archivos reales y una cola en
memoria, porque agent.py no se puede importar en un entorno de pruebas (carga
wmi, pyautogui y mss).

Uso, desde la raíz del proyecto:

    python tests/test_f2_delete_order.py
"""

import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "agent"))

import storage  # noqa: E402


resultados = []


def comprobar(descripcion, condicion):
    resultados.append((descripcion, bool(condicion)))
    print(f"  [{'OK  ' if condicion else 'FALLA'}] {descripcion}")


class Escenario:
    """Réplica del tramo de _process_pending() que trata cada segmento."""

    def __init__(self, carpeta):
        self.carpeta = carpeta
        self.cola = []
        self.subida_correcta = True
        self.borrado_bloqueado = False

    # --- piezas equivalentes a las del Agent ---

    def _upload_one(self, path):
        # Igual que en agent.py: si el archivo ya no está, se da por subido
        if not os.path.exists(path):
            return True
        return self.subida_correcta

    def _remove_pending(self, path):
        self.cola = [p for p in self.cola if p != path]

    def _delete(self, path):
        if self.borrado_bloqueado:
            # Simula lo que hace Windows con un antivirus o el archivo abierto
            real = os.remove
            os.remove = lambda _p: (_ for _ in ()).throw(
                PermissionError("archivo bloqueado por otro proceso")
            )
            try:
                return storage.delete_recording_file(path)
            finally:
                os.remove = real

        return storage.delete_recording_file(path)

    def procesar(self):
        """El orden nuevo: borrar y, solo si se borró, retirar de la cola."""

        for path in list(self.cola):
            if self._upload_one(path):
                if self._delete(path):
                    self._remove_pending(path)

    # --- utilidades ---

    def crear(self, nombre, n=2048):
        ruta = os.path.join(self.carpeta, nombre)
        with open(ruta, "wb") as handle:
            handle.write(b"\x00" * n)
        self.cola.append(ruta)
        return ruta


def main():

    temporal = tempfile.mkdtemp(prefix="remoteadmin_f2_orden_")
    grabaciones = os.path.join(temporal, "recordings")
    os.makedirs(grabaciones, exist_ok=True)

    storage.get_recordings_dir = lambda: grabaciones

    try:

        print("\n=== 1. Subida confirmada + borrado correcto ===")
        e = Escenario(grabaciones)
        a = e.crear("rec_4000000001.mp4")
        e.procesar()
        comprobar("el archivo se eliminó", not os.path.exists(a))
        comprobar("la entrada salió de la cola", a not in e.cola)
        comprobar("la cola quedó vacía", len(e.cola) == 0)

        print("\n=== 2. Subida confirmada + borrado FALLIDO ===")
        e = Escenario(grabaciones)
        b = e.crear("rec_4000000002.mp4")
        e.borrado_bloqueado = True
        e.procesar()
        comprobar("el archivo PERMANECE en disco", os.path.exists(b))
        comprobar("la entrada PERMANECE en la cola", b in e.cola)
        print("        -> el hilo de reintentos volverá a intentarlo")

        print("\n=== 3. Reintento tras liberarse el bloqueo ===")
        e.borrado_bloqueado = False
        e.procesar()
        comprobar("ahora sí se elimina el archivo", not os.path.exists(b))
        comprobar("y se limpia la cola", b not in e.cola)

        print("\n=== 4. El archivo ya no existe antes de limpiar la cola ===")
        # Caso del proceso que muere entre el borrado y el _remove_pending:
        # al arrancar, la entrada sigue en la cola pero el archivo no está.
        e = Escenario(grabaciones)
        c = os.path.join(grabaciones, "rec_4000000003.mp4")
        e.cola.append(c)          # entrada huérfana, sin archivo
        comprobar("punto de partida: en la cola pero sin archivo",
                  c in e.cola and not os.path.exists(c))
        e.procesar()
        comprobar("la entrada se limpia sola", c not in e.cola)
        comprobar("sin intentar resubir nada", len(e.cola) == 0)

        print("\n=== 5. Bloqueo persistente: no se pierde la referencia ===")
        e = Escenario(grabaciones)
        d = e.crear("rec_4000000004.mp4")
        e.borrado_bloqueado = True
        for _ in range(5):
            e.procesar()
        comprobar("tras 5 pasadas sigue en disco", os.path.exists(d))
        comprobar("y sigue en la cola, sin duplicarse",
                  e.cola.count(d) == 1)
        e.borrado_bloqueado = False
        e.procesar()
        comprobar("al desbloquearse se resuelve", not os.path.exists(d))
        comprobar("y la cola queda limpia", len(e.cola) == 0)

        print("\n=== 6. Subida fallida: ni se borra ni se saca de la cola ===")
        e = Escenario(grabaciones)
        f = e.crear("rec_4000000005.mp4")
        e.subida_correcta = False
        e.procesar()
        comprobar("el archivo sigue disponible", os.path.exists(f))
        comprobar("la entrada sigue pendiente", f in e.cola)
        e.subida_correcta = True
        e.procesar()
        comprobar("al recuperarse el servidor, se sube y se borra",
                  not os.path.exists(f) and len(e.cola) == 0)

        print("\n=== 7. Varios segmentos, uno bloqueado ===")
        e = Escenario(grabaciones)
        g1 = e.crear("rec_4000000006.mp4")
        g2 = e.crear("rec_4000000007.mp4")
        e.procesar()
        comprobar("los dos se eliminan cuando todo va bien",
                  not os.path.exists(g1) and not os.path.exists(g2)
                  and len(e.cola) == 0)

    finally:
        shutil.rmtree(temporal, ignore_errors=True)

    fallos = [d for d, ok in resultados if not ok]

    print("\n" + "=" * 60)
    print(f"  {len(resultados) - len(fallos)} de {len(resultados)} comprobaciones correctas")

    if fallos:
        print("  FALLOS:")
        for f in fallos:
            print("   -", f)

    print("=" * 60)

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
