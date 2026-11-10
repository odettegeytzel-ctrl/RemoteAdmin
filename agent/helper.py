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

import clipboard
import commands
import ipc
import lockinput
import maintenance


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

                # Lo que cuesta la foto, medido donde se hace.
                #
                # Es la parte del retardo que NO depende de la red, y
                # separarla importa: sin este numero, un remoto lento
                # se achaca siempre a la conexion, y puede que el
                # tiempo se vaya aqui. Vale para decidir si cambiar
                # la captura o cambiar el camino.
                comenzado = time.monotonic()

                imagen = self.agente.capture_screen()

                captura_ms = round((time.monotonic() - comenzado) * 1000, 1)

                mensaje = {
                    "image": imagen,
                    "mouse": self.agente.get_mouse_position(),
                    "width": self.agente.screen_geometry["width"],
                    "height": self.agente.screen_geometry["height"],
                    "captura_ms": captura_ms
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

        Se vuelve a comprobar aqui lo que le corresponde. El
        servicio ya lo decidio, pero este proceso es el que tiene el
        raton: no ejecuta nada que no le toque, aunque se lo pidan
        por el canal.

        La comprobacion es el_ayudante_debe_atender y no
        requires_desktop, y la diferencia costo tres dias sin grabar
        en los siete equipos: los ajustes de grabacion NO son de
        escritorio -guardar un horario no necesita pantalla- pero
        los cumple el ayudante, porque el grabador vive aqui.

        Con requires_desktop se descartaban nada mas llegar:

            [ayudante] Orden descartada: no es de escritorio (set_schedule)

        El servicio habia dejado de grabar, que era lo correcto, y
        el ayudante nunca recibia el horario. No grababa nadie.
        """

        if not commands.el_ayudante_debe_atender(orden):
            print(
                "[ayudante] Orden descartada: no es de escritorio "
                f"({commands.nombre_de_la_orden(orden)})"
            )
            return

        agente = self.agente

        # --- los ajustes de grabacion ---
        #
        # Los recibe el servicio y los cumple el ayudante, porque el
        # grabador vive aqui. Se aplican por las MISMAS funciones
        # que usaba el servicio: un segundo camino para lo mismo
        # seria un segundo sitio donde olvidarse de algo.

        if orden.startswith("set_schedule:"):

            try:
                horario = json.loads(orden.split(":", 1)[1])
            except json.JSONDecodeError:
                horario = {}

            agente.apply_schedule(horario)
            agente.report_status_threadsafe()
            return

        if orden.startswith("set_continuous:"):

            try:
                datos = json.loads(orden.split(":", 1)[1])
            except json.JSONDecodeError:
                datos = {}

            agente.apply_continuous(bool(datos.get("enabled")))
            agente.report_status_threadsafe()
            return

        if orden.startswith("schedule_suppression:"):

            try:
                datos = json.loads(orden.split(":", 1)[1])
            except json.JSONDecodeError:
                datos = {}

            agente.recording_scheduler.aplicar_supresion(datos.get("slot"))
            agente._aplicar_decision_del_horario()
            return

        if orden == "start_screen_stream":
            self.empezar_a_transmitir()

        elif orden == "stop_screen_stream":
            self.dejar_de_transmitir()

        elif orden == "start_recording":
            agente.start_screen_recording(manual=True)
            agente.report_status_threadsafe()

        elif orden == "stop_recording":
            agente.stop_screen_recording(manual=True)

            # El panel tiene que enterarse YA, no en el proximo
            # segmento: entre uno y otro pasan quince minutos, y
            # durante ese rato el boton seguiria diciendo
            # "Detener grabacion" sobre algo ya detenido.
            agente.report_status_threadsafe()

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

        elif orden.startswith("maintenance_on:"):
            datos = json.loads(orden.split(":", 1)[1])
            maintenance.activar(datos.get("seconds"))

        elif orden == "maintenance_off":
            maintenance.desactivar()

        elif orden.startswith("keyboard:"):
            datos = json.loads(orden.split(":", 1)[1])
            agente.handle_keyboard(datos)

        elif orden.startswith("clipboard_set:"):
            datos = json.loads(orden.split(":", 1)[1])
            self.poner_portapapeles(datos.get("text"))

        elif orden == "clipboard_get":
            self.traer_portapapeles()

        elif orden.startswith("input_lock:"):
            datos = json.loads(orden.split(":", 1)[1])
            self.bloquear_entrada(datos.get("seconds"))

        elif orden == "input_unlock":
            self.desbloquear_entrada()

        else:
            print(
                "[ayudante] Orden de escritorio no reconocida: "
                f"{commands.nombre_de_la_orden(orden)}"
            )

    # --- portapapeles ---

    def poner_portapapeles(self, texto):
        """
        Deja en el equipo el texto que copio quien administra.

        Solo se informa del numero de caracteres. El contenido no se
        escribe en ningun sitio: es de lo mas delicado que pasa por
        un equipo.
        """

        if not isinstance(texto, str):
            return

        try:
            puestos = clipboard.escribir(texto)

            print(f"[ayudante] Portapapeles recibido: {puestos} caracteres")

        except Exception as error:
            print(f"[ayudante] No se pudo escribir el portapapeles: {error}")

    def traer_portapapeles(self):
        """
        Manda al panel lo que haya en el portapapeles del equipo.

        Este sentido es SIEMPRE a peticion, nunca automatico: lo
        contrario seria llevarse todo lo que el usuario del equipo
        copie -una contraseña de su gestor, un dato personal- sin que
        nadie lo haya pedido.
        """

        try:
            texto = clipboard.leer()

        except Exception as error:
            print(f"[ayudante] No se pudo leer el portapapeles: {error}")
            texto = None

        self.canal.enviar({
            "ws": "clipboard_data:" + json.dumps({
                "text": texto,
                "empty": texto is None
            })
        })

    # --- bloqueo del teclado y el raton fisicos ---

    def bloquear_entrada(self, segundos):

        concedidos = lockinput.activar(segundos)

        self.canal.enviar({
            "ws": "input_lock_state:" + json.dumps({
                "locked": concedidos > 0,
                "seconds": concedidos
            })
        })

        if not concedidos:
            print("[ayudante] Windows rechazo el bloqueo de entrada")

    def desbloquear_entrada(self):

        lockinput.desactivar()

        self.canal.enviar({
            "ws": "input_lock_state:" + json.dumps({
                "locked": False,
                "seconds": 0
            })
        })

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

        # Por aqui sale todo lo que el ayudante tenga que contar.
        #
        # El grabador, el catalogo de grabaciones locales y la franja
        # suprimida avisan desde sus propios hilos, llamando a
        # send_ws_threadsafe. En este proceso no hay WebSocket, asi
        # que sin esto esas llamadas se iban sin hacer nada y el
        # panel no se enteraba de nada de lo que pasa en el
        # escritorio: ni de que se esta grabando, ni de que hay
        # grabaciones guardadas en el equipo.
        self.agente.registrar_emisor(self.mandar_al_servidor)

        # EL HILO DEL HORARIO. Aqui, y en ningun otro sitio.
        #
        # Vivia en agent.main(), con esta condicion:
        #
        #     if AGENT_ROLE != ROLE_SERVICE:
        #         threading.Thread(target=_schedule_loop).start()
        #
        # y un comentario que decia "el horario lo cumple el
        # ayudante, que si tiene escritorio". Correcto en la
        # intencion y falso en los hechos: el ayudante NO ejecuta
        # agent.main() -tiene el suyo, e importa agent como
        # biblioteca-, asi que ese hilo no lo arrancaba nadie.
        #
        # Lo que quedaba era esto: el horario solo se evaluaba cuando
        # llegaba un mensaje, porque apply_schedule y apply_continuous
        # llaman a la decision directamente. Asi, una jornada
        # empezaba en el mismo segundo en que el servidor mandaba sus
        # ajustes -al conectarse, o al tocar algo en el panel- y
        # nunca a la hora que decia el horario.
        #
        # En los registros se ve exacto: cada "Empieza la franja" cae
        # en el mismo segundo que un "Grabacion continua ACTIVADA".
        # Ni uno lo disparo el reloj. Las 08:00 no llegaron nunca.
        self.agente.arrancar_el_horario()

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


def _dejar_constancia_del_fallo(error):
    """
    Escribe por que no arranco el ayudante, pase lo que pase.

    Hace falta porque el ayudante corre con pythonw: sin consola, un
    fallo al importar o al arrancar no se ve EN NINGUN SITIO. El
    proceso muere y no queda rastro.

    Paso en dos equipos. El servicio pedia el arranque cada minuto,
    Windows contestaba que si, y el registro del ayudante llevaba un
    dia entero sin una sola linea nueva. Sin saber si moria o si ni
    siquiera llegaba a ejecutarse, no habia por donde empezar.

    Se escribe con la biblioteca estandar y nada mas: lo que hay que
    cazar es justo que falle un import del proyecto, asi que esta
    funcion no puede depender de ninguno. Y si tampoco se puede
    escribir, se calla: no va a morir dos veces por contarlo.
    """

    import datetime
    import traceback

    carpeta = os.path.join(
        os.environ.get("ProgramData", r"C:\ProgramData"),
        "RemoteAdmin", "logs"
    )

    try:
        os.makedirs(carpeta, exist_ok=True)

        with open(os.path.join(carpeta, "agent-helper-arranque.log"),
                  "a", encoding="utf-8") as archivo:

            archivo.write("\n%s  EL AYUDANTE NO ARRANCO\n"
                          % datetime.datetime.now().isoformat(" ", "seconds"))
            archivo.write("  python: %s\n" % sys.executable)
            archivo.write("  guion:  %s\n" % os.path.abspath(__file__))
            archivo.write("  %s: %s\n" % (type(error).__name__, error))
            archivo.write(traceback.format_exc())

    except Exception:
        pass


if __name__ == "__main__":

    try:
        main()

    except Exception as problema:
        _dejar_constancia_del_fallo(problema)
        raise
