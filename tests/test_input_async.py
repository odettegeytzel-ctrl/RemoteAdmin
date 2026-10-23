"""
La entrada remota no bloquea el bucle de eventos del Agent.

    .venv\\Scripts\\python tests/test_input_async.py

Mejora #1 del diagnostico de fluidez: las operaciones de mouse y
teclado eran SINCRONAS y corrian dentro del bucle de eventos del
Agent. Mientras pyautogui inyectaba, el bucle no leia el siguiente
mensaje ni enviaba la pantalla. Ahora cada una se ejecuta con
'await asyncio.to_thread(...)', como ya se hacia con capture_screen.

Que mide esto
-------------

1. A NIVEL DE CODIGO: que los cinco puntos de entrada del bucle
   WebSocket (move_mouse, click_mouse, mouse_down, mouse_up,
   handle_keyboard) usan 'await asyncio.to_thread(...)', y que NO se
   lanzan con asyncio.create_task (eso seria concurrencia sin tope y
   rompe el orden).

2. EJECUTANDO EL CODIGO REAL del Agent (con pyautogui simulado para
   no mover el raton de verdad): que el orden se conserva, que un
   arrastre sale down -> move -> up, que los botones se sueltan y que
   release_all limpia las teclas. Tambien que el estado compartido
   (pressed_buttons, held_keys) queda coherente, porque al serializar
   con 'await' nunca hay dos inyecciones a la vez.
"""

import asyncio
import io
import re
import sys
import types

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ------------------------------------------------------------------
# 1. Comprobaciones sobre el codigo fuente del Agent
# ------------------------------------------------------------------

def _region_dispatch():
    """
    El trozo del bucle WebSocket donde se despachan las entradas.

    Se acota entre el primer 'mouse_move:' y el final del bloque de
    teclado para no confundir las LLAMADAS del bucle con las
    DEFINICIONES de las funciones, que estan mas arriba.
    """

    inicio = AGENTE.index('"mouse_move:"')
    fin = AGENTE.index("Keyboard error")

    return AGENTE[inicio:fin]


def test_las_cinco_entradas_van_a_un_hilo():

    region = _region_dispatch()

    for funcion in (
        "move_mouse",
        "click_mouse",
        "mouse_down",
        "mouse_up",
        "handle_keyboard"
    ):

        patron = "await asyncio.to_thread(\n" + " " * 28 + funcion + ","

        # Tolerante a la cantidad exacta de espacios: basta con que
        # aparezca 'await asyncio.to_thread(' e inmediatamente la
        # funcion como primer argumento.
        encontrado = re.search(
            r"await\s+asyncio\.to_thread\(\s*" + funcion + r"\s*,",
            region
        )

        comprobar(
            f"{funcion} se ejecuta con await asyncio.to_thread",
            encontrado is not None
        )


def test_no_hay_create_task_en_las_entradas():

    region = _region_dispatch()

    comprobar(
        "las entradas no usan create_task (evita concurrencia sin tope)",
        "create_task" not in region
    )


def test_el_coalescing_del_mouse_sigue_en_el_frontend():

    # Req. 1: no tocar d1a295a. Se comprueba que las piezas del
    # resumen de movimientos siguen presentes en app.js.
    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

    comprobar(
        "el coalescing del mouse (d1a295a) sigue en app.js",
        "movimientosEnVuelo" in js
        and "pendingMove" in js
        and "flushPendingMove" in js
    )


# ------------------------------------------------------------------
# 2. Prueba ejecutable del codigo real, con pyautogui simulado
# ------------------------------------------------------------------

def _cargar_agent_con_pyautogui_falso():
    """
    Importa el modulo del Agent sustituyendo pyautogui por un doble
    que solo apunta en que orden se le llama. Asi se ejecuta la logica
    real de move_mouse/mouse_down/... sin mover el raton de esta PC.
    """

    registro = []

    falso = types.ModuleType("pyautogui")
    falso.PAUSE = 0
    falso.FAILSAFE = False
    falso.KEYBOARD_KEYS = ["a", "b", "c", "enter", "ctrl", "shift"]

    def _anota(nombre):
        def _fn(*args, **kwargs):
            registro.append((nombre, args, kwargs))
        return _fn

    for metodo in (
        "moveTo", "click", "mouseDown", "mouseUp",
        "press", "keyDown", "keyUp", "hotkey"
    ):
        setattr(falso, metodo, _anota(metodo))

    falso.position = lambda: (0, 0)

    sys.modules["pyautogui"] = falso

    sys.path.insert(0, str(RAIZ / "agent"))

    import agent as modulo

    # El modulo se importa una sola vez (cache de sys.modules), asi que
    # en llamadas sucesivas hay que reenlazar SU referencia a pyautogui
    # al doble nuevo; si no, seguiria apuntando al primero y el registro
    # de esta llamada quedaria vacio.
    modulo.pyautogui = falso

    # Que las conversiones de coordenadas no dividan por cero.
    modulo.screen_geometry.update({
        "left": 0, "top": 0,
        "real_width": 1920, "real_height": 1080,
        "width": 1280, "height": 720
    })

    # El movimiento nulo de arrastre usa ctypes.windll; se neutraliza
    # para no depender del SO en la prueba.
    modulo.ctypes.windll.user32.mouse_event = _anota("win_mouse_event")

    return modulo, registro


def test_orden_arrastre_y_estado_compartido():

    try:
        modulo, registro = _cargar_agent_con_pyautogui_falso()
    except Exception as error:
        comprobar(
            "se puede importar y ejercitar el Agent",
            False,
            str(error)
        )
        return

    comprobar("se puede importar y ejercitar el Agent", True)

    async def arrastrar():
        # Simula lo que hace el bucle: cada entrada, en su hilo y en
        # orden, esperando a que termine antes de la siguiente.
        await asyncio.to_thread(modulo.mouse_down, 10, 10, "left")
        await asyncio.to_thread(modulo.move_mouse, 20, 20)
        await asyncio.to_thread(modulo.move_mouse, 30, 30)
        await asyncio.to_thread(modulo.mouse_up, 40, 40, "left")

    asyncio.run(arrastrar())

    secuencia = [nombre for (nombre, _a, _k) in registro]

    # Arrastre correcto: el boton se presiona antes de soltarse, hay
    # movimiento entre medias, y el 'up' es lo ultimo (decide donde se
    # suelta). Nota: mouse_down posiciona con move_mouse ANTES de
    # presionar, por eso el primer 'moveTo' precede a 'mouseDown'; lo
    # que importa para el arrastre es down < move(s) < up.
    hay_move_entre = any(
        s == "moveTo"
        for s in secuencia[
            secuencia.index("mouseDown") + 1: secuencia.index("mouseUp")
        ]
    )

    comprobar(
        "el arrastre mantiene el orden down -> move -> up",
        secuencia.index("mouseDown") < secuencia.index("mouseUp")
        and hay_move_entre
        and secuencia[-1] == "mouseUp"
    )

    # El boton quedo liberado: pressed_buttons vacio al terminar.
    comprobar(
        "el boton se libera (pressed_buttons queda vacio)",
        len(modulo.pressed_buttons) == 0
    )


def test_teclas_se_sueltan_con_release_all():

    try:
        modulo, registro = _cargar_agent_con_pyautogui_falso()
    except Exception as error:
        comprobar("release_all limpia las teclas", False, str(error))
        return

    async def teclear():
        await asyncio.to_thread(
            modulo.handle_keyboard, {"action": "down", "key": "ctrl"}
        )
        await asyncio.to_thread(
            modulo.handle_keyboard, {"action": "down", "key": "shift"}
        )
        await asyncio.to_thread(
            modulo.handle_keyboard, {"action": "release_all"}
        )

    asyncio.run(teclear())

    comprobar(
        "release_all limpia las teclas (held_keys queda vacio)",
        len(modulo.held_keys) == 0
    )


# ------------------------------------------------------------------
# 3. release_all_buttons: botones que no quedan colgados
# ------------------------------------------------------------------

def test_release_all_buttons_existe_y_se_integra():

    # Definicion presente.
    comprobar(
        "existe la funcion release_all_buttons",
        re.search(r"def\s+release_all_buttons\s*\(", AGENTE) is not None
    )

    # Se llama en el except de desconexion (junto a abort_file_transfer).
    inicio = AGENTE.index("except Exception as error:\n\n            release_all_keys()")
    bloque_desconexion = AGENTE[inicio: inicio + 400]

    comprobar(
        "la desconexion suelta los botones (release_all_buttons en el except)",
        "release_all_buttons()" in bloque_desconexion
    )

    # Se llama al parar el stream.
    stop = AGENTE.index('message == "stop_screen_stream"')
    bloque_stop = AGENTE[stop: stop + 500]

    comprobar(
        "stop_screen_stream suelta los botones",
        "release_all_buttons()" in bloque_stop
    )

    # NO se llama en el release_all del teclado (seria liberacion
    # innecesaria en operacion normal: el blur del teclado no debe
    # soltar un boton del raton).
    kb = AGENTE.index("def handle_keyboard(data):")
    cuerpo_kb = AGENTE[kb: kb + 400]

    comprobar(
        "handle_keyboard(release_all) NO suelta botones del raton",
        "release_all_buttons" not in cuerpo_kb
    )


def test_release_boton_izquierdo_y_derecho():

    try:
        modulo, registro = _cargar_agent_con_pyautogui_falso()
    except Exception as error:
        comprobar("release de botones izq/der", False, str(error))
        return

    async def presionar_y_limpiar(boton):
        await asyncio.to_thread(modulo.mouse_down, 10, 10, boton)

    for boton in ("left", "right"):

        registro.clear()
        modulo.pressed_buttons.clear()

        asyncio.run(presionar_y_limpiar(boton))

        comprobar(
            f"{boton}: queda marcado como presionado tras mouse_down",
            boton in modulo.pressed_buttons
        )

        modulo.release_all_buttons()

        soltados = [
            k.get("button")
            for (nombre, _a, k) in registro
            if nombre == "mouseUp"
        ]

        comprobar(
            f"{boton}: release_all_buttons lo suelta con mouseUp",
            boton in soltados
        )

        comprobar(
            f"{boton}: pressed_buttons queda vacio",
            len(modulo.pressed_buttons) == 0
        )


def test_arrastre_interrumpido_y_desconexion():

    try:
        modulo, registro = _cargar_agent_con_pyautogui_falso()
    except Exception as error:
        comprobar("arrastre interrumpido", False, str(error))
        return

    async def arrastre_sin_soltar():
        # Un arrastre que se corta: down + move y NUNCA llega el up.
        await asyncio.to_thread(modulo.mouse_down, 10, 10, "left")
        await asyncio.to_thread(modulo.move_mouse, 20, 20)
        await asyncio.to_thread(
            modulo.handle_keyboard, {"action": "down", "key": "ctrl"}
        )

    asyncio.run(arrastre_sin_soltar())

    comprobar(
        "antes de limpiar hay un boton y una tecla presionados",
        "left" in modulo.pressed_buttons and "ctrl" in modulo.held_keys
    )

    # Lo que hace el bloque de desconexion: soltar teclas Y botones.
    modulo.release_all_keys()
    modulo.release_all_buttons()

    comprobar(
        "tras la desconexion no queda ningun boton presionado",
        len(modulo.pressed_buttons) == 0
    )

    comprobar(
        "tras la desconexion no queda ninguna tecla presionada",
        len(modulo.held_keys) == 0
    )


def test_limpieza_repetida_es_segura():

    try:
        modulo, registro = _cargar_agent_con_pyautogui_falso()
    except Exception as error:
        comprobar("limpieza repetida", False, str(error))
        return

    asyncio.run(asyncio.to_thread(modulo.mouse_down, 5, 5, "left"))

    modulo.release_all_buttons()
    registro.clear()

    # Segunda llamada: no debe fallar ni volver a soltar nada.
    try:
        modulo.release_all_buttons()
        sin_error = True
    except Exception:
        sin_error = False

    mouseups_segunda_vez = [
        n for (n, _a, _k) in registro if n == "mouseUp"
    ]

    comprobar(
        "release_all_buttons repetido no lanza error",
        sin_error
    )

    comprobar(
        "release_all_buttons repetido no vuelve a soltar (idempotente)",
        len(mouseups_segunda_vez) == 0 and len(modulo.pressed_buttons) == 0
    )


def main():

    pruebas = [
        test_las_cinco_entradas_van_a_un_hilo,
        test_no_hay_create_task_en_las_entradas,
        test_el_coalescing_del_mouse_sigue_en_el_frontend,
        test_orden_arrastre_y_estado_compartido,
        test_teclas_se_sueltan_con_release_all,
        test_release_all_buttons_existe_y_se_integra,
        test_release_boton_izquierdo_y_derecho,
        test_arrastre_interrumpido_y_desconexion,
        test_limpieza_repetida_es_segura,
    ]

    for prueba in pruebas:
        prueba()

    print()

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLA"
        extra = f"  ({detalle})" if detalle and not ok else ""
        print(f"  [{marca}] {nombre}{extra}")
        if not ok:
            fallos += 1

    print()

    if fallos:
        print(f"{fallos} comprobacion(es) fallaron.")
        sys.exit(1)

    print("Todas las comprobaciones pasaron.")


if __name__ == "__main__":
    main()
