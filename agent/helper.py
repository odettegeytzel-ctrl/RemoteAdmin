"""
Ayudante interactivo del Agent de RemoteAdmin.

Corre en la sesion del usuario y es el UNICO que toca el escritorio:
captura de pantalla, grabacion, raton y teclado. Windows aisla los
servicios en la Sesion 0, donde la pantalla sale en negro y el raton
no llega a ninguna parte, asi que esto no puede vivir ahi.

Lo que este proceso NO hace, a proposito:

  - no habla con el servidor de RemoteAdmin;
  - no lee ni guarda el token del dispositivo;
  - no conoce la credencial de alta;
  - no ejecuta comandos administrativos.

Todo eso se queda en el servicio de fondo. Esta separacion es lo que
contiene el riesgo del canal local: el ayudante corre como el usuario
que inicio sesion, que puede no ser administrador, asi que se da por
hecho que es el extremo menos protegido de los dos y no se le confia
nada que importe.

Se arranca al iniciar sesion, sin ventana:

    pythonw.exe agent\\helper.py

Si el servicio todavia no esta listo, espera y lo reintenta: al
iniciar sesion los dos arrancan casi a la vez y el orden no esta
garantizado.
"""

import json
import os
import sys
import threading
import time


# El ayudante vive en la misma carpeta que el Agent, y reutiliza sus
# funciones de escritorio en vez de duplicarlas: la captura y la
# inyeccion de teclado ya estaban resueltas ahi.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commands
import ipc


# Espera entre intentos de conexion. Creciente hasta un tope, por lo
# mismo que en el Agent: si el servicio no esta, reintentar cada
# segundo durante horas es CPU gastada para nada.
RECONNECT_BASE_SECONDS = 2
RECONNECT_MAX_SECONDS = 60

# Ritmo de la transmision de pantalla. El mismo que usaba el Agent en
# un solo proceso, para que se vea igual que antes.
STREAM_INTERVAL = 0.08


def _esperar(intento):

    return min(
        RECONNECT_BASE_SECONDS * (2 ** max(0, intento - 1)),
        RECONNECT_MAX_SECONDS
    )


class Ayudante:
    """Obedece ordenes de escritorio y devuelve lo que produce."""

    def __init__(self, agente):

        # El modulo del Agent, ya importado. Se recibe como argumento
        # en vez de importarlo aqui para poder probar esta clase sin
        # arrastrar wmi, mss ni pyautogui.
        self.agente = agente

        self.canal = ipc.CanalDeAyudante()

        self._transmitiendo = False
        self._hilo_pantalla = None

    # --- envio ---

    def mandar_al_servidor(self, carga):
        """
        Hace que el servicio reenvie algo por el WebSocket.

        El ayudante no tiene conexion propia: lo que produce pasa
        siempre por el servicio, que es quien esta autenticado.
        """

        try:
            self.canal.enviar({"ws": carga})
            return True

        except ipc.IPCError:
            return False

    # --- pantalla en vivo ---

    def _bucle_de_pantalla(self):

        while self._transmitiendo:

            try:

                mensaje = {
                    "image": self.agente.capture_screen(),
                    "mouse": self.agente.get_mouse_position(),
                    "width": self.agente.screen_geometry["width"],
                    "height": self.agente.screen_geometry["height"]
                }

                if not self.mandar_al_servidor(
                    "screen_info:" + json.dumps(mensaje)
                ):
                    # Canal caido: no se sigue capturando para nadie
                    break

            except Exception as error:
                print(f"[ayudante] Error capturando pantalla: {error}")
                break

            time.sleep(STREAM_INTERVAL)

        self._transmitiendo = False

    def empezar_a_transmitir(self):

        if self._transmitiendo:
            return

        self._transmitiendo = True

        self._hilo_pantalla = threading.Thread(
            target=self._bucle_de_pantalla,
            name="ScreenStream",
            daemon=True
        )

        self._hilo_pantalla.start()

    def dejar_de_transmitir(self):

        self._transmitiendo = False

        # Se sueltan las teclas que hubieran quedado pulsadas: si se
        # corta el control remoto con una tecla abajo, el equipo se
        # queda escribiendola sola.
        try:
            self.agente.release_all_keys()

        except Exception:
            pass

    # --- despacho ---

    def ejecutar(self, orden):
        """
        Atiende una orden de escritorio.

        Se vuelve a comprobar aqui que la orden es de escritorio. El
        servicio ya lo decidio, pero este proceso es el que tiene el
        raton: no ejecuta nada que no le corresponda, aunque se lo
        pidan por el canal.
        """

        if not commands.requires_desktop(orden):
            print(
                "[ayudante] Orden descartada: no es de escritorio "
                f"({commands.nombre_de_la_orden(orden)})"
            )
            return

        agente = self.agente

        if orden == "start_screen_stream":
            self.empezar_a_transmitir()

        elif orden == "stop_screen_stream":
            self.dejar_de_transmitir()

        elif orden == "start_recording":
            agente.start_screen_recording(manual=True)

        elif orden == "stop_recording":
            agente.stop_screen_recording(manual=True)

        elif orden.startswith("mouse_move:"):
            datos = json.loads(orden.split(":", 1)[1])
            agente.move_mouse(datos["x"], datos["y"])

        elif orden.startswith("mouse_click:"):
            datos = json.loads(orden.split(":", 1)[1])
            agente.click_mouse(datos.get("button", "left"))

        elif orden.startswith("mouse_down:"):
            datos = json.loads(orden.split(":", 1)[1])
            agente.mouse_down(
                datos["x"], datos["y"], datos.get("button", "left")
            )

        elif orden.startswith("mouse_up:"):
            datos = json.loads(orden.split(":", 1)[1])
            agente.mouse_up(
                datos["x"], datos["y"], datos.get("button", "left")
            )

        elif orden.startswith("keyboard:"):
            datos = json.loads(orden.split(":", 1)[1])
            agente.handle_keyboard(datos)

        else:
            print(
                "[ayudante] Orden de escritorio no reconocida: "
                f"{commands.nombre_de_la_orden(orden)}"
            )

    # --- vida del proceso ---

    def atender(self):
        """Un ciclo completo de conexion, hasta que se corte."""

        while True:

            orden = self.canal.recibir().get("command")

            if not isinstance(orden, str):
                continue

            try:
                self.ejecutar(orden)

            except Exception as error:

                # Una orden mal formada no tira al ayudante: se anota
                # el nombre, nunca los argumentos (llevan coordenadas
                # y texto tecleado).
                print(
                    "[ayudante] Error ejecutando "
                    f"{commands.nombre_de_la_orden(orden)}: {error}"
                )

    def correr(self):

        intentos = 0

        while True:

            if not self.canal.conectar():

                intentos += 1

                espera = _esperar(intentos)

                if intentos == 1:
                    print(
                        "[ayudante] El servicio no esta disponible "
                        "todavia; reintentando"
                    )

                time.sleep(espera)
                continue

            print("[ayudante] Conectado al servicio")

            intentos = 0

            try:
                self.atender()

            except ipc.IPCError as problema:
                print(f"[ayudante] Canal cerrado: {problema}")

            except Exception as error:
                print(f"[ayudante] Error inesperado: {error}")

            finally:

                self.dejar_de_transmitir()
                self.canal.cerrar()


def main():

    # El Agent decide su papel por la linea de ordenes, y de ahi sale
    # el nombre de su archivo de registro. Se marca antes de
    # importarlo para que el ayudante no escriba en el log del
    # servicio: dos procesos rotando el mismo archivo lo dejan
    # ilegible.
    if not any(a.startswith("--role=") for a in sys.argv[1:]):
        sys.argv.append("--role=helper")

    # Se importa aqui, y no arriba, para que el modulo se pueda
    # importar en una maquina sin escritorio (por ejemplo, para
    # comprobar la clasificacion de ordenes).
    import agent

    destino = agent._abrir_registro()

    print("RemoteAdmin - ayudante interactivo")

    if destino:
        print(f"Registro: {destino}")

    Ayudante(agent).correr()


if __name__ == "__main__":
    main()
