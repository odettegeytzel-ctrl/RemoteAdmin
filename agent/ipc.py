"""
Canal local entre el servicio de fondo y el ayudante interactivo.

Por que hacen falta dos procesos
--------------------------------

Windows aisla los servicios en la Sesion 0. Ahi no hay escritorio: la
captura de pantalla sale en negro y el teclado y el raton no llegan a
ninguna parte. Pero el resto del Agent —latido, inventario, procesos,
servicios, apagado— no necesita escritorio y deberia funcionar desde
que arranca el equipo, sin esperar a que alguien inicie sesion.

De ahi la division:

    servicio de fondo    Sesion 0, arranca con Windows, habla con el
                         servidor y guarda la identidad

    ayudante             sesion del usuario, arranca al iniciar
                         sesion, es el unico que toca el escritorio

Este modulo es el tubo entre los dos.

Como esta protegido
-------------------

Es un named pipe LOCAL: '\\\\.\\pipe\\...' no se puede alcanzar desde la
red porque se crea con FILE_FLAG_FIRST_PIPE_INSTANCE y un descriptor de
seguridad que no concede nada a ANONYMOUS ni a NETWORK.

Hay dos barreras mas, y conviene entender por que no basta con una:

  1. el descriptor de seguridad limita QUIEN puede abrirlo (SYSTEM,
     administradores y usuarios interactivos locales);

  2. al conectarse se comprueba de que EJECUTABLE viene el cliente.

La segunda hace falta porque el ayudante corre como el usuario que
inicio sesion, que puede no ser administrador: el permiso tiene que
alcanzarle, y entonces cualquier programa de ese usuario podria abrir
el tubo.

La tercera barrera es de diseno, y es la que de verdad contiene el
problema: por aqui NO pasa el token del dispositivo ni ningun secreto,
y el ayudante no puede pedir nada al servidor. Lo peor que consigue un
proceso que se cuele es ver ordenes de captura y responder con imagenes
falsas en su propia pantalla. No puede dar de alta equipos, ni leer la
identidad, ni ejecutar comandos administrativos: esos no salen nunca
del servicio de fondo.
"""

import json
import os
import struct
import sys
import threading


# Nombre del tubo. Local por definicion: el prefijo '\\.\pipe\' no se
# enruta fuera de la maquina.
PIPE_NAME = r"\\.\pipe\RemoteAdmin.Agent"

# Tamano maximo de un mensaje. Un marco de pantalla comprimido cabe de
# sobra; el limite esta para que un cliente que se vuelva loco no
# reserve memoria sin fin.
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

# Cuanto se espera al escribir o leer antes de dar el canal por
# perdido. Sin esto, un ayudante colgado dejaria al servicio esperando
# para siempre, que es justo lo que no puede pasar: el fondo tiene que
# seguir latiendo pase lo que pase.
IO_TIMEOUT_MS = 5000

# Descriptor de seguridad del tubo:
#
#   D:          DACL
#   (A;;GA;;;SY)    control total para SYSTEM (el servicio)
#   (A;;GA;;;BA)    control total para administradores
#   (A;;GRGW;;;IU)  leer y escribir para usuarios INTERACTIVOS locales
#
# IU (Interactive Users) es lo que permite que el ayudante funcione con
# un usuario normal, sin darle permisos de administrador. No se incluye
# AN (anonimo) ni NU (acceso por red): de ahi que el tubo no sea
# alcanzable desde fuera del equipo.
PIPE_SDDL = "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)"


class IPCError(RuntimeError):
    """Fallo del canal local, con un motivo legible."""


class HelperUnavailable(IPCError):
    """
    No hay ayudante conectado.

    Es una situacion NORMAL, no una averia: ocurre siempre que nadie ha
    iniciado sesion. Tiene su propio tipo para que el servicio pueda
    contestar al servidor 'esto necesita una sesion abierta' en vez de
    tratarlo como un error.
    """


# ==============================
# MARCOS
# ==============================
#
# Un named pipe entrega bytes, no mensajes: dos envios seguidos pueden
# llegar pegados o partidos. Se antepone la longitud para saber donde
# acaba cada uno.

def empaquetar(objeto):
    """Convierte un mensaje en bytes: 4 de longitud y el resto JSON."""

    cuerpo = json.dumps(objeto).encode("utf-8")

    if len(cuerpo) > MAX_MESSAGE_BYTES:
        raise IPCError("El mensaje excede el tamano maximo")

    return struct.pack("<I", len(cuerpo)) + cuerpo


def desempaquetar_longitud(cabecera):
    """
    Longitud anunciada por una cabecera de 4 bytes.

    Se valida contra el maximo ANTES de reservar memoria: creerse una
    longitud que venga del otro lado es como se agota la memoria de un
    proceso.
    """

    if len(cabecera) != 4:
        raise IPCError("Cabecera incompleta")

    (longitud,) = struct.unpack("<I", cabecera)

    if longitud == 0 or longitud > MAX_MESSAGE_BYTES:
        raise IPCError(f"Longitud de mensaje invalida: {longitud}")

    return longitud


def interpretar(cuerpo):
    """Mensaje a partir de sus bytes. Lo que no sea JSON se rechaza."""

    try:
        objeto = json.loads(cuerpo.decode("utf-8"))

    except (ValueError, UnicodeDecodeError) as error:
        raise IPCError(f"Mensaje ilegible: {error}")

    if not isinstance(objeto, dict):
        raise IPCError("Un mensaje debe ser un objeto")

    return objeto


# ==============================
# ORIGEN DEL CLIENTE
# ==============================

def _ruta_del_proceso(pid):
    """Ejecutable de un proceso, o None si no se puede averiguar."""

    try:
        import win32api
        import win32con
        import win32process

    except ImportError:
        return None

    manejador = None

    try:
        manejador = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )

        return win32process.GetModuleFileNameEx(manejador, 0)

    except Exception:

        # Puede fallar legitimamente: el proceso termino entre que se
        # conecto y que se consulto.
        return None

    finally:

        if manejador is not None:
            try:
                import win32api as cierre
                cierre.CloseHandle(manejador)
            except Exception:
                pass


def origen_permitido(ruta, permitidas):
    """
    Decide si un cliente puede hablar por el canal.

    Se compara la ruta real del ejecutable contra una lista de rutas
    esperadas. Si no se pudo averiguar la ruta se dice que NO: ante la
    duda sobre quien esta al otro lado, se cierra.
    """

    if not ruta:
        return False

    normalizada = os.path.normcase(os.path.normpath(ruta))

    for esperada in permitidas:

        if not esperada:
            continue

        if normalizada == os.path.normcase(os.path.normpath(esperada)):
            return True

    return False


def interpretes_permitidos():
    """
    Ejecutables que pueden conectarse como ayudante.

    El ayudante corre bajo el MISMO interprete que el servicio, asi
    que se parte de la imagen real de este proceso y se acepta tambien
    su pareja (python.exe / pythonw.exe), porque el servicio arranca
    sin ventana y el ayudante tambien.

    La imagen real se consulta con la misma llamada que se usara para
    el cliente, y no solo sys.executable, porque dentro de un entorno
    virtual no coinciden: sys.executable apunta al del entorno y la
    imagen del proceso al interprete base. Comparar cosas distintas
    rechazaria al ayudante legitimo.
    """

    rutas = set()

    candidatos = [
        _ruta_del_proceso(os.getpid()),
        sys.executable,
        getattr(sys, "_base_executable", None)
    ]

    for candidato in candidatos:

        if not candidato:
            continue

        completa = os.path.abspath(candidato)

        rutas.add(completa)

        carpeta = os.path.dirname(completa)

        for nombre in ("python.exe", "pythonw.exe"):

            pareja = os.path.join(carpeta, nombre)

            if os.path.isfile(pareja):
                rutas.add(pareja)

    return rutas


# ==============================
# ENTRADA Y SALIDA SOLAPADA
# ==============================
#
# Los dos extremos usan FILE_FLAG_OVERLAPPED, y no es un detalle: un
# handle SINCRONO de Windows serializa las operaciones. Con el, un
# hilo bloqueado leyendo deja colgada la escritura de otro hilo sobre
# el mismo handle.
#
# Y eso es exactamente lo que hace el servicio: un hilo espera lo que
# mande el ayudante mientras el bucle del WebSocket le envia ordenes.
# Con un handle sincrono, la primera orden que se mandara habria
# bloqueado el Agent entero.
#
# Solapada, cada operacion lleva su propio evento, las dos direcciones
# son independientes, y ademas se puede poner un limite de tiempo de
# verdad: sin el, un ayudante colgado dejaria al servicio esperando
# para siempre.


def _nuevo_overlapped():

    import pywintypes
    import win32event

    overlapped = pywintypes.OVERLAPPED()

    # Evento manual y sin senalar: cada operacion espera el suyo.
    overlapped.hEvent = win32event.CreateEvent(None, True, False, None)

    return overlapped


def _esperar_operacion(handle, overlapped, timeout_ms):
    """
    Espera a que termine una operacion solapada.

    Devuelve los bytes transferidos. Si se agota el tiempo, cancela la
    operacion antes de fallar: dejarla viva escribiendo sobre un
    buffer que ya no existe corrompe memoria.
    """

    import win32event
    import win32file

    resultado = win32event.WaitForSingleObject(
        overlapped.hEvent, timeout_ms
    )

    if resultado != win32event.WAIT_OBJECT_0:

        try:
            win32file.CancelIo(handle)
        except Exception:
            pass

        raise IPCError("Se agoto el tiempo de espera del canal")

    return win32file.GetOverlappedResult(handle, overlapped, True)


def _escribir(handle, datos, timeout_ms, lock=None):
    """
    Escribe todos los bytes.

    El lock serializa a los escritores: dos hilos escribiendo a la vez
    entrelazarian sus mensajes y el otro extremo leeria basura.
    """

    import pywintypes
    import win32file

    if lock is not None:
        lock.acquire()

    try:

        enviados = 0

        while enviados < len(datos):

            overlapped = _nuevo_overlapped()

            try:

                try:
                    win32file.WriteFile(
                        handle, datos[enviados:], overlapped
                    )

                except pywintypes.error as error:

                    # 997 = ERROR_IO_PENDING: lo normal en solapada
                    if error.winerror != 997:
                        raise

                enviados += _esperar_operacion(
                    handle, overlapped, timeout_ms
                )

            finally:
                _cerrar_evento(overlapped)

    finally:

        if lock is not None:
            lock.release()


def _leer_exactamente(handle, cantidad, timeout_ms):
    """Lee exactamente `cantidad` bytes, o falla."""

    import pywintypes
    import win32file

    buffer = win32file.AllocateReadBuffer(cantidad)

    leidos = b""

    while len(leidos) < cantidad:

        pendiente = cantidad - len(leidos)

        trozo = (
            buffer if pendiente == cantidad
            else win32file.AllocateReadBuffer(pendiente)
        )

        overlapped = _nuevo_overlapped()

        try:

            try:
                win32file.ReadFile(handle, trozo, overlapped)

            except pywintypes.error as error:

                if error.winerror != 997:
                    raise

            obtenidos = _esperar_operacion(handle, overlapped, timeout_ms)

            if obtenidos == 0:
                raise IPCError("El canal se cerro a mitad de un mensaje")

            leidos += bytes(trozo)[:obtenidos]

        finally:
            _cerrar_evento(overlapped)

    return leidos


def _leer_sin_prisa(handle, cantidad):
    """
    Como _leer_exactamente, pero sin limite de tiempo.

    Se usa para esperar el SIGUIENTE mensaje: ahi no hay nada que se
    agote, porque lo normal es estar mucho rato sin que llegue nada.
    El limite de tiempo se aplica una vez empezado el mensaje, con
    _leer_exactamente, para que un mensaje a medias no deje el canal
    colgado.
    """

    import pywintypes
    import win32event
    import win32file

    buffer = win32file.AllocateReadBuffer(cantidad)

    overlapped = _nuevo_overlapped()

    try:

        try:
            win32file.ReadFile(handle, buffer, overlapped)

        except pywintypes.error as error:

            if error.winerror != 997:
                raise

        win32event.WaitForSingleObject(
            overlapped.hEvent, win32event.INFINITE
        )

        obtenidos = win32file.GetOverlappedResult(handle, overlapped, True)

        if obtenidos == 0:
            raise IPCError("El canal se cerro")

        return bytes(buffer)[:obtenidos]

    finally:
        _cerrar_evento(overlapped)


def _cerrar_evento(overlapped):

    try:
        import win32api
        win32api.CloseHandle(overlapped.hEvent)

    except Exception:
        pass


def _recibir_mensaje(handle, timeout_ms):
    """Un mensaje completo: cabecera sin prisa, cuerpo con limite."""

    cabecera = b""

    while len(cabecera) < 4:

        cabecera += _leer_sin_prisa(handle, 4 - len(cabecera))

    longitud = desempaquetar_longitud(cabecera)

    return interpretar(_leer_exactamente(handle, longitud, timeout_ms))


# ==============================
# LADO DEL SERVICIO DE FONDO
# ==============================

class CanalDeServicio:
    """
    Extremo del servicio: crea el tubo y atiende a UN ayudante.

    Uno solo a la vez, y a proposito: hay un escritorio activo cada
    vez, asi que admitir varios ayudantes simultaneos solo serviria
    para que dos pelearan por el raton. Cuando un usuario cierra
    sesion y entra otro, el ayudante nuevo sustituye al anterior.

    Todo lo de aqui esta pensado para no bloquear al servicio: si no
    hay ayudante, las llamadas fallan rapido con HelperUnavailable y
    el latido sigue su curso.
    """

    def __init__(self, nombre=PIPE_NAME, origenes=None):

        self.nombre = nombre
        self.origenes = origenes or interpretes_permitidos()

        self._tubo = None
        self._conectado = False

        # Serializa a los escritores. Al servicio le escriben el bucle
        # del WebSocket y, en su caso, otros hilos; dos a la vez
        # entrelazarian sus mensajes.
        self._lock_escritura = threading.Lock()

    # --- ciclo de vida ---

    def abrir(self):
        """Crea el tubo. Devuelve False si no se pudo (sin Windows)."""

        try:
            import win32pipe
            import win32security

        except ImportError:
            return False

        seguridad = win32security.SECURITY_ATTRIBUTES()

        seguridad.SECURITY_DESCRIPTOR = (
            win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                PIPE_SDDL, win32security.SDDL_REVISION_1
            )
        )

        import win32file

        self._tubo = win32pipe.CreateNamedPipe(
            self.nombre,
            # FIRST_PIPE_INSTANCE impide que otro proceso se adelante y
            # cree un tubo con el mismo nombre para suplantarnos.
            #
            # OVERLAPPED es imprescindible: sin el, un hilo leyendo
            # bloquea la escritura de otro sobre el mismo handle.
            win32pipe.PIPE_ACCESS_DUPLEX
            | win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE
            | win32file.FILE_FLAG_OVERLAPPED,
            win32pipe.PIPE_TYPE_BYTE
            | win32pipe.PIPE_READMODE_BYTE
            | win32pipe.PIPE_WAIT
            # Solo conexiones locales. Lo dice el nombre del tubo, pero
            # dejarlo explicito evita que un cambio futuro lo abra.
            | win32pipe.PIPE_REJECT_REMOTE_CLIENTS,
            1,
            65536,
            65536,
            IO_TIMEOUT_MS,
            seguridad
        )

        return True

    def esperar_ayudante(self):
        """
        Acepta una conexion y comprueba de donde viene.

        Bloquea hasta que alguien se conecte, asi que se llama desde su
        propio hilo, nunca desde el bucle del servicio.
        """

        import pywintypes
        import win32pipe

        overlapped = _nuevo_overlapped()

        try:

            try:
                win32pipe.ConnectNamedPipe(self._tubo, overlapped)

            except pywintypes.error as error:

                # 535 = ERROR_PIPE_CONNECTED: el cliente llego antes de
                # que se llamara, que es legitimo y no es un fallo.
                if error.winerror not in (535, 997):
                    raise

                if error.winerror == 535:
                    overlapped = None

            if overlapped is not None:

                import win32event

                win32event.WaitForSingleObject(
                    overlapped.hEvent, win32event.INFINITE
                )

        finally:

            if overlapped is not None:
                _cerrar_evento(overlapped)

        pid = None

        try:
            pid = win32pipe.GetNamedPipeClientProcessId(self._tubo)

        except Exception:
            pid = None

        ruta = _ruta_del_proceso(pid) if pid else None

        if not origen_permitido(ruta, self.origenes):

            # No se dice por el canal quien es ni que se esperaba: a
            # quien no deberia estar ahi no se le explica nada.
            self.desconectar()

            raise IPCError(
                f"Conexion rechazada: origen no autorizado (pid {pid})"
            )

        self._conectado = True

        return pid

    def desconectar(self):
        """Suelta al ayudante actual, dejando el tubo listo para otro."""

        self._conectado = False

        if self._tubo is None:
            return

        try:
            import win32pipe
            win32pipe.DisconnectNamedPipe(self._tubo)

        except Exception:
            pass

    def cerrar(self):

        self.desconectar()

        if self._tubo is not None:

            try:
                import win32file
                win32file.CloseHandle(self._tubo)

            except Exception:
                pass

            self._tubo = None

    # --- mensajes ---

    @property
    def hay_ayudante(self):
        return self._conectado

    def enviar(self, mensaje):
        """
        Manda un mensaje al ayudante.

        Si no hay ninguno, falla al instante con HelperUnavailable en
        vez de esperar: el servicio no puede quedarse parado porque
        nadie haya iniciado sesion.
        """

        if not self._conectado:
            raise HelperUnavailable("No hay ayudante interactivo conectado")

        try:
            _escribir(
                self._tubo, empaquetar(mensaje), IO_TIMEOUT_MS,
                self._lock_escritura
            )

        except Exception as error:

            # El ayudante se fue (cierre de sesion, cuelgue). Se deja
            # el tubo listo para el siguiente y se avisa.
            self.desconectar()

            raise HelperUnavailable(f"El ayudante se desconecto: {error}")

    def recibir(self):
        """Siguiente mensaje del ayudante."""

        if not self._conectado:
            raise HelperUnavailable("No hay ayudante interactivo conectado")

        try:
            return _recibir_mensaje(self._tubo, IO_TIMEOUT_MS)

        except Exception as error:

            self.desconectar()

            raise HelperUnavailable(f"El ayudante se desconecto: {error}")


# ==============================
# LADO DEL AYUDANTE
# ==============================

class CanalDeAyudante:
    """
    Extremo del ayudante: se conecta al tubo del servicio.

    No guarda ni recibe el token del dispositivo. Todo lo que sabe
    hacer es obedecer ordenes de escritorio y devolver el resultado.
    """

    def __init__(self, nombre=PIPE_NAME):

        self.nombre = nombre
        self._tubo = None

        # El ayudante escribe desde el hilo de la pantalla y desde el
        # que atiende ordenes.
        self._lock_escritura = threading.Lock()

    def conectar(self):
        """
        Abre el canal. Devuelve False si el servicio todavia no esta.

        Que falle es normal al iniciar sesion: el ayudante arranca a la
        vez que el escritorio y puede llegar antes que el servicio. Se
        reintenta, no se trata como una averia.
        """

        try:
            import win32file

        except ImportError:
            return False

        try:
            self._tubo = win32file.CreateFile(
                self.nombre,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                # Por lo mismo que en el servicio: el hilo que manda
                # marcos de pantalla no puede quedarse bloqueado
                # porque otro este esperando una orden.
                win32file.FILE_FLAG_OVERLAPPED,
                None
            )

            return True

        except Exception:
            self._tubo = None
            return False

    def cerrar(self):

        if self._tubo is not None:

            try:
                import win32file
                win32file.CloseHandle(self._tubo)

            except Exception:
                pass

            self._tubo = None

    def enviar(self, mensaje):

        if self._tubo is None:
            raise IPCError("El canal no esta abierto")

        _escribir(
            self._tubo, empaquetar(mensaje), IO_TIMEOUT_MS,
            self._lock_escritura
        )

    def recibir(self):

        if self._tubo is None:
            raise IPCError("El canal no esta abierto")

        return _recibir_mensaje(self._tubo, IO_TIMEOUT_MS)
