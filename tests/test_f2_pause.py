"""
Pruebas de la pausa por falta de espacio (bloque F2).

Comprueba que el grabador NO entra en un bucle rápido cuando no hay sitio, que
se recupera al liberarse espacio y que una parada durante la pausa se atiende
de inmediato.

Uso, desde la raíz del proyecto:

    python tests/test_f2_pause.py
"""

import os
import sys
import threading
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "agent"))

import recorder  # noqa: E402
import storage   # noqa: E402


resultados = []


def comprobar(descripcion, condicion):
    resultados.append((descripcion, bool(condicion)))
    print(f"  [{'OK  ' if condicion else 'FALLA'}] {descripcion}")


def main():

    # La pausa real espera 30 s entre comprobaciones; para la prueba se acorta
    recorder.STORAGE_RECHECK_SECONDS = 0.2

    grabador = recorder.ScreenRecorder()

    comprobaciones = {"n": 0}
    bloqueado = {"valor": "el disco de prueba está lleno"}

    def motivo_falso():
        comprobaciones["n"] += 1
        return bloqueado["valor"]

    original_motivo = storage.storage_blocked_reason
    original_puede = storage.can_start_new_segment

    storage.storage_blocked_reason = motivo_falso
    storage.can_start_new_segment = lambda: bloqueado["valor"] is None

    try:

        print("\n=== 1. Con espacio: no se pausa ===")
        bloqueado["valor"] = None
        comprobar("_wait_for_storage devuelve True de inmediato",
                  grabador._wait_for_storage() is True)
        comprobar("no queda marcado como pausado", grabador.is_paused() is None)

        print("\n=== 2. Sin espacio: pausa controlada, SIN bucle rápido ===")
        bloqueado["valor"] = "el disco de prueba está lleno"
        comprobaciones["n"] = 0

        resultado = {}

        def en_hilo():
            resultado["valor"] = grabador._wait_for_storage()

        hilo = threading.Thread(target=en_hilo, daemon=True)
        inicio = time.time()
        hilo.start()

        # Se le deja "pausado" un segundo: con espera de 0,2 s debe hacer unas
        # 5 comprobaciones, no miles
        time.sleep(1.0)

        intentos_en_un_segundo = comprobaciones["n"]
        comprobar("sigue esperando, no ha vuelto", hilo.is_alive())
        comprobar("queda registrado el motivo de la pausa",
                  grabador.is_paused() == "el disco de prueba está lleno")
        print(f"        comprobaciones en 1 s: {intentos_en_un_segundo}")
        comprobar("NO hay bucle rápido (menos de 20 intentos por segundo)",
                  intentos_en_un_segundo < 20)

        print("\n=== 3. Se libera espacio: se reanuda solo ===")
        bloqueado["valor"] = None
        hilo.join(timeout=3)
        comprobar("el hilo terminó tras liberarse el espacio", not hilo.is_alive())
        comprobar("_wait_for_storage devolvió True", resultado.get("valor") is True)
        comprobar("ya no está marcado como pausado", grabador.is_paused() is None)
        print(f"        tiempo total de la pausa: {time.time() - inicio:.1f} s")

        print("\n=== 4. Parar durante la pausa: responde de inmediato ===")
        bloqueado["valor"] = "sigue sin espacio"
        recorder.STORAGE_RECHECK_SECONDS = 30   # espera larga a propósito
        grabador._stop_event.clear()

        resultado2 = {}

        def en_hilo2():
            resultado2["valor"] = grabador._wait_for_storage()

        hilo2 = threading.Thread(target=en_hilo2, daemon=True)
        hilo2.start()
        time.sleep(0.2)

        inicio_parada = time.time()
        grabador._stop_event.set()
        hilo2.join(timeout=3)
        tardanza = time.time() - inicio_parada

        comprobar("el hilo terminó", not hilo2.is_alive())
        comprobar("devolvió False (no se abre segmento)",
                  resultado2.get("valor") is False)
        comprobar(f"la parada no espera los 30 s ({tardanza:.2f} s)", tardanza < 2)
        comprobar("la pausa queda limpia", grabador.is_paused() is None)

        print("\n=== 5. current_segment_path solo con grabación en curso ===")
        comprobar("sin grabar devuelve None",
                  grabador.current_segment_path() is None)
        with grabador._lock:
            grabador._running = True
            grabador._current_path = r"C:\ruta\rec_1.mp4"
        comprobar("grabando devuelve la ruta actual",
                  grabador.current_segment_path() == r"C:\ruta\rec_1.mp4")
        with grabador._lock:
            grabador._running = False
        comprobar("al parar vuelve a None",
                  grabador.current_segment_path() is None)

    finally:
        storage.storage_blocked_reason = original_motivo
        storage.can_start_new_segment = original_puede

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
