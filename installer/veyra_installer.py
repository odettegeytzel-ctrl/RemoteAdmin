"""
Instalador grafico de VEYRA para Windows.

Pensado para que alguien sin conocimientos tecnicos instale el Agent
sin abrir PowerShell ni teclear comandos: rellena dos campos, pulsa un
boton y acepta el aviso de Windows.

Por que es una envoltura y no una reimplementacion
--------------------------------------------------

La instalacion la sigue haciendo installer\\install-agent.ps1, que ya
esta probado: copia el Agent, escribe la configuracion con permisos
restringidos, instala las dependencias, registra las dos tareas y
espera a confirmar el alta.

Reimplementar todo eso en Python daria dos versiones de la misma
logica, y en cuanto una cambiara, la otra instalaria algo distinto. Lo
que aporta esta ventana es lo que al script le falta: que no haya que
escribir nada, comprobar el servidor ANTES de instalar, y reconocer un
equipo que ya esta dado de alta para no volver a pedir la credencial.

Lo que NO hace, a proposito
---------------------------

No elude el aviso de Windows (UAC): pide la elevacion con el dialogo
normal del sistema, y si se rechaza, no instala. Instalar un servicio
que arranca como SYSTEM requiere permisos de administrador, y saltarse
ese aviso seria exactamente lo que hace el software que no deberia
estar en un equipo.

No lleva ninguna credencial dentro. La credencial de alta se teclea al
instalar, viaja al proceso elevado en un archivo temporal que se
sobrescribe y se borra al leerlo —nunca como argumento, que quedaria a
la vista en la lista de procesos de todo el equipo— y despues solo
vive en el .env del equipo, legible unicamente por administradores.

Uso:

    VEYRA-Instalador.exe

o, sin empaquetar:

    python installer\\veyra_installer.py
"""

import atexit
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

import tkinter as tk

from tkinter import messagebox, ttk


PRODUCTO = "VEYRA"
EMPRESA = "NYRA"
DESCRIPCION = "Remote Administration & Monitoring"

# Carpeta de datos del Agent. Es un identificador tecnico: la usan
# agent\paths.py y las tareas ya registradas en los equipos, asi que
# no cambia con el nombre comercial.
INSTALL_DIR = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "RemoteAdmin"
)

IDENTITY_FILE = os.path.join(INSTALL_DIR, "config", "identity.json")

TAREA_SERVICIO = "RemoteAdminAgent"

PREFIJO_CREDENCIAL = "rae_"

# Tamano maximo del archivo temporal de la credencial. Una credencial
# son unas decenas de bytes; el limite esta para que, si el archivo
# fuese sustituido por otra cosa, no se lea un archivo enorme.
MAX_CREDENCIAL_BYTES = 4096


# Temporales de credencial todavia sin limpiar.
#
# Existe para que la limpieza ocurra tambien cuando no se llega al
# final de instalar(): una excepcion, el cierre de la ventana o la
# salida del programa. Lo que NO se puede cubrir es una terminacion
# forzada del proceso (Administrador de tareas, corte de luz): ahi no
# corre codigo propio. En ese caso queda el borrado que hace el propio
# script al leer el archivo, que es lo primero que hace.
_pendientes = set()

TIEMPO_DE_ESPERA = 20


# ==============================
# ENTORNO
# ==============================

def carpeta_del_paquete():
    """
    Carpeta donde se descomprimio el paquete.

    Empaquetado con PyInstaller, sys.executable es el .exe y el
    script no existe como archivo, asi que la ruta se calcula distinto
    en cada caso.
    """

    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))

    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def guion_de_instalacion():
    return os.path.join(
        carpeta_del_paquete(), "installer", "install-agent.ps1"
    )


def servidor_del_paquete():
    """
    Direccion que el panel dejo escrita en el .env del paquete.

    Asi la persona que instala no tiene que saberla ni teclearla.
    """

    ruta = os.path.join(carpeta_del_paquete(), ".env")

    try:
        with open(ruta, "r", encoding="utf-8", errors="replace") as archivo:
            for linea in archivo:
                if linea.startswith("REMOTEADMIN_SERVER="):
                    return linea.split("=", 1)[1].strip()

    except OSError:
        pass

    return ""


def identidad_existente():
    """
    Identidad del equipo si ya estaba dado de alta, o None.

    Solo se devuelve el identificador. El token individual esta en el
    mismo archivo y NO se lee: no hace falta para nada aqui.
    """

    try:
        with open(IDENTITY_FILE, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)

    except (OSError, ValueError):
        return None

    identificador = datos.get("device_id")

    return {"device_id": identificador} if identificador else None


def tarea_registrada():
    """True si la tarea del servicio ya existe en este equipo."""

    try:
        resultado = subprocess.run(
            ["schtasks", "/Query", "/TN", TAREA_SERVICIO],
            capture_output=True, timeout=20
        )

        return resultado.returncode == 0

    except Exception:
        return False


def es_administrador():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())

    except Exception:
        return False


# ==============================
# COMPROBACION DEL SERVIDOR
# ==============================

def comprobar_servidor(url):
    """
    Comprueba que el servidor responde ANTES de instalar nada.

    Se hace aqui porque el fallo mas probable en un equipo ajeno es
    que la direccion no sea alcanzable —sin internet, con un
    cortafuegos por medio, mal escrita— y descubrirlo despues de
    copiar archivos y registrar tareas no ayuda a nadie.

    La validacion del certificado se deja ACTIVADA. Si falla, se dice
    cual es el problema en vez de ignorarlo: un servidor cuyo
    certificado no valida no es el servidor al que se queria conectar.
    """

    direccion = (url or "").strip().rstrip("/")

    if not direccion:
        return False, "Falta la direccion del servidor."

    if not direccion.startswith(("http://", "https://")):
        return False, "La direccion debe empezar por http:// o https://"

    contexto = ssl.create_default_context()

    peticion = urllib.request.Request(
        direccion + "/api/health",
        headers={"User-Agent": f"{PRODUCTO}-Instalador"}
    )

    try:
        with urllib.request.urlopen(
            peticion, timeout=TIEMPO_DE_ESPERA, context=contexto
        ) as respuesta:

            cuerpo = respuesta.read(300).decode("utf-8", "replace")

            if respuesta.status == 200 and "ok" in cuerpo:
                return True, "El servidor responde correctamente."

            return False, f"Respuesta inesperada del servidor ({cuerpo[:80]})."

    except urllib.error.HTTPError as error:

        # Responde, pero no como se esperaba: puede ser otra cosa
        # escuchando en esa direccion.
        return False, (
            f"El servidor respondio {error.code}. Comprueba que la "
            "direccion sea la del panel."
        )

    except urllib.error.URLError as error:

        # urllib envuelve el fallo real en 'reason', asi que el tipo
        # concreto hay que mirarlo ahi: un 'except SSLError' de
        # primer nivel nunca se alcanzaria.
        motivo = getattr(error, "reason", error)

        if isinstance(motivo, ssl.SSLCertVerificationError):
            return False, (
                "El certificado del servidor no se pudo validar. No se "
                "continua: la conexion no seria de fiar, y podria no "
                "ser el servidor que se cree. "
                f"({motivo.verify_message})"
            )

        if isinstance(motivo, ssl.SSLError):
            return False, f"Fallo la conexion segura: {motivo}"

        if isinstance(motivo, socket.gaierror):
            return False, (
                "No se pudo encontrar el servidor. Revisa la direccion "
                "y la conexion a internet."
            )

        if isinstance(motivo, (TimeoutError, socket.timeout)):
            return False, (
                "El servidor no respondio a tiempo. Revisa la conexion "
                "a internet."
            )

        return False, f"No se pudo conectar: {motivo}"

    except Exception as error:
        return False, f"No se pudo conectar: {error}"


# ==============================
# INSTALACION
# ==============================

def instalar(url, credencial, registrar):
    """
    Lanza el script de instalacion con permisos de administrador.

    Devuelve (correcto, mensaje). La credencial viaja en un archivo
    temporal que el script borra al leerlo.
    """

    guion = guion_de_instalacion()

    if not os.path.isfile(guion):
        return False, (
            "No se encontro el instalador junto a este programa.\n\n"
            "Descomprime el paquete completo y vuelve a ejecutarlo "
            "desde esa carpeta."
        )

    archivo_credencial = None

    if credencial:

        # Archivo temporal solo para el traspaso. No se pasa como
        # argumento porque los argumentos se ven en la lista de
        # procesos de todo el equipo.
        #
        # mkstemp crea con O_CREAT|O_EXCL y permisos de solo el dueno,
        # asi que no se puede adelantar nadie dejando ahi un enlace ni
        # un archivo previo.
        descriptor, archivo_credencial = tempfile.mkstemp(
            prefix="veyra-", suffix=".tmp"
        )

        _pendientes.add(archivo_credencial)

        with os.fdopen(descriptor, "w", encoding="utf-8") as temporal:
            temporal.write(credencial)

        # Se restringe a administradores, al sistema y a quien instala.
        #
        # Incluir a quien instala no es un descuido: la amenaza son los
        # OTROS usuarios del equipo, y el archivo esta en su propia
        # carpeta temporal, a la que ya tiene acceso. Quitarselo no
        # protegeria de nada y si impediria que este proceso
        # sobrescriba el contenido al limpiar, que es justo lo que
        # evita que la credencial quede recuperable en disco.
        permisos = ["Administrators:(F)", "SYSTEM:(F)"]

        quien = os.environ.get("USERNAME", "").strip()
        dominio = os.environ.get("USERDOMAIN", "").strip()

        if quien:
            permisos.append(
                (dominio + chr(92) + quien + ":(F)") if dominio
                else (quien + ":(F)")
            )

        try:
            subprocess.run(
                ["icacls", archivo_credencial, "/inheritance:r",
                 "/grant:r"] + permisos,
                capture_output=True, timeout=20
            )

        except Exception:
            # Si no se pudieron restringir los permisos se sigue: el
            # archivo vive segundos y el script lo borra al leerlo.
            pass

    # Justo antes de entregar la ruta a un proceso con mas permisos
    # que este, se comprueba que sigue siendo el archivo que se
    # escribio.
    if archivo_credencial and not _es_archivo_normal(archivo_credencial):
        _borrar(archivo_credencial)
        return False, (
            "El archivo temporal de la credencial no es el esperado. "
            "No se continua."
        )

    argumentos = [
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", guion,
        "-Server", url
    ]

    if archivo_credencial:
        argumentos += ["-EnrollmentTokenFile", archivo_credencial]

    registrar("Pidiendo permisos de administrador a Windows...")

    # El finally cubre todo lo que pueda pasar aqui dentro, incluida
    # una excepcion inesperada: la credencial no se queda en disco por
    # no haber previsto un camino de salida.
    try:

        try:
            codigo = _lanzar_elevado("powershell.exe", argumentos)

        except Exception as error:
            return False, f"No se pudo iniciar la instalacion: {error}"

        if codigo is None:
            return False, (
                "Windows no concedio los permisos de administrador.\n\n"
                "La instalacion los necesita para registrar el servicio "
                "que arranca con el equipo. No se ha cambiado nada en "
                "este equipo. Vuelve a intentarlo y acepta el aviso."
            )

        if codigo != 0:
            return False, (
                f"La instalacion termino con errores (codigo {codigo})."
                "\n\nRevisa el registro en:\n"
                + os.path.join(INSTALL_DIR, "logs", "agent-service.log")
            )

        return True, "Instalacion terminada."

    finally:
        _borrar(archivo_credencial)


def _lanzar_elevado(programa, argumentos):
    """
    Ejecuta con elevacion y espera a que termine.

    Se usa ShellExecute con 'runas', que es el dialogo normal de
    Windows. Devuelve el codigo de salida, o None si se rechazo la
    elevacion.
    """

    import ctypes
    from ctypes import wintypes

    class INFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HANDLE),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hKeyClass", wintypes.HANDLE),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NO_CONSOLE = 0x00008000
    ERROR_CANCELLED = 1223

    linea = subprocess.list2cmdline(argumentos)

    info = INFO()
    info.cbSize = ctypes.sizeof(INFO)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
    info.lpVerb = "runas"
    info.lpFile = programa
    info.lpParameters = linea
    info.nShow = 1

    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):

        if ctypes.get_last_error() == ERROR_CANCELLED:
            return None

        if ctypes.GetLastError() == ERROR_CANCELLED:
            return None

        return None

    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)

    codigo = wintypes.DWORD()

    ctypes.windll.kernel32.GetExitCodeProcess(
        info.hProcess, ctypes.byref(codigo)
    )

    ctypes.windll.kernel32.CloseHandle(info.hProcess)

    return codigo.value


def _borrar(ruta):
    """
    Sobrescribe y borra el archivo temporal de la credencial.

    La sobrescritura reduce la ventana en que el contenido sigue
    legible, pero NO garantiza un borrado fisico: en SSD, con
    nivelacion de desgaste, y en sistemas de archivos con journaling o
    copias en escritura, los bytes originales pueden sobrevivir en
    bloques que el sistema ya no asocia al archivo. La garantia real
    de esta credencial es otra: es de un solo uso practico, caduca, y
    se puede revocar en cuanto el equipo aparece en el panel.
    """

    if not ruta:
        return

    _pendientes.discard(ruta)

    if not os.path.isfile(ruta):
        return

    try:
        with open(ruta, "r+b") as archivo:
            largo = os.path.getsize(ruta)
            archivo.write(b"0" * largo)
            archivo.flush()
            os.fsync(archivo.fileno())

    except OSError:
        # Puede fallar si el proceso perdio el acceso. Se intenta
        # borrar igualmente: eliminar depende del permiso sobre la
        # carpeta, no sobre el archivo.
        pass

    try:
        os.remove(ruta)

    except OSError:
        pass


def _limpiar_pendientes():
    """
    Borra los temporales que quedaran sin limpiar.

    Se llama al salir del programa y al cerrar la ventana, para cubrir
    los caminos que no pasan por el final de instalar().
    """

    for ruta in list(_pendientes):
        _borrar(ruta)


atexit.register(_limpiar_pendientes)


def _es_archivo_normal(ruta):
    """
    True si la ruta es un archivo de verdad y no un enlace.

    Se comprueba antes de entregar la ruta al proceso con permisos de
    administrador: si en ese punto hubiera un enlace simbolico o una
    union de directorios, el proceso elevado leeria lo que apunte el
    enlace en vez del archivo que se escribio.

    Crear enlaces en Windows exige permisos de administrador o el modo
    de desarrollador, y el archivo se crea con O_EXCL en la carpeta
    temporal del propio usuario, asi que el hueco es estrecho. Se
    comprueba de todos modos: la ruta se la pasamos nosotros a un
    proceso mas privilegiado que nosotros.
    """

    try:
        if not os.path.isfile(ruta):
            return False

        if os.path.islink(ruta):
            return False

        # FILE_ATTRIBUTE_REPARSE_POINT: enlaces y uniones
        atributos = os.stat(ruta, follow_symlinks=False).st_file_attributes

        if atributos & 0x400:
            return False

        if os.path.getsize(ruta) > MAX_CREDENCIAL_BYTES:
            return False

        return True

    except (OSError, AttributeError):
        return False


# ==============================
# LA VENTANA
# ==============================

class Ventana:

    def __init__(self, raiz):

        self.raiz = raiz

        raiz.title(f"{PRODUCTO} - Instalar en este equipo")
        raiz.resizable(False, False)

        self.ya_instalado = identidad_existente()

        marco = ttk.Frame(raiz, padding=20)
        marco.grid(sticky="nsew")

        fila = 0

        ttk.Label(
            marco, text=PRODUCTO, font=("Segoe UI", 20, "bold")
        ).grid(row=fila, column=0, columnspan=2, sticky="w")

        fila += 1

        ttk.Label(
            marco, text=f"{DESCRIPCION}  ·  {EMPRESA}",
            foreground="#666"
        ).grid(row=fila, column=0, columnspan=2, sticky="w",
               pady=(0, 16))

        fila += 1

        # --- Estado del equipo ---
        if self.ya_instalado:
            texto = (
                "Este equipo ya esta dado de alta.\n"
                f"Identificador: {self.ya_instalado['device_id']}\n\n"
                "Al continuar se actualiza el programa y se conserva su "
                "identidad: no hace falta credencial y seguira siendo "
                "el mismo equipo en el panel."
            )
            color = "#0a6"

        else:
            texto = (
                "Este equipo todavia no esta dado de alta.\n\n"
                "Necesitas la credencial de instalacion, que se genera "
                "en el panel, en 'Instalar equipo'."
            )
            color = "#444"

        aviso = ttk.Label(
            marco, text=texto, foreground=color, wraplength=440,
            justify="left"
        )
        aviso.grid(row=fila, column=0, columnspan=2, sticky="w",
                   pady=(0, 16))

        fila += 1

        # --- Servidor ---
        ttk.Label(marco, text="Servidor:").grid(
            row=fila, column=0, sticky="w"
        )

        self.servidor = tk.StringVar(value=servidor_del_paquete())

        ttk.Entry(
            marco, textvariable=self.servidor, width=46
        ).grid(row=fila, column=1, sticky="we", pady=4)

        fila += 1

        # --- Credencial, solo si hace falta ---
        if not self.ya_instalado:

            ttk.Label(marco, text="Credencial:").grid(
                row=fila, column=0, sticky="w"
            )

            self.credencial = tk.StringVar()

            ttk.Entry(
                marco, textvariable=self.credencial, width=46, show="*"
            ).grid(row=fila, column=1, sticky="we", pady=4)

            fila += 1

            ttk.Label(
                marco,
                text=f"Empieza por {PREFIJO_CREDENCIAL} · solo se usa "
                     "para el primer registro",
                foreground="#888", font=("Segoe UI", 8)
            ).grid(row=fila, column=1, sticky="w", pady=(0, 8))

            fila += 1

        else:
            self.credencial = tk.StringVar()

        # --- Botones ---
        botones = ttk.Frame(marco)
        botones.grid(row=fila, column=0, columnspan=2, sticky="we",
                     pady=(12, 8))

        self.boton_probar = ttk.Button(
            botones, text="Comprobar conexion", command=self.probar
        )
        self.boton_probar.pack(side="left")

        self.boton_instalar = ttk.Button(
            botones,
            text="Actualizar" if self.ya_instalado else "Instalar",
            command=self.instalar
        )
        self.boton_instalar.pack(side="left", padx=8)

        fila += 1

        # --- Estado ---
        self.estado = tk.StringVar(value="")

        self.etiqueta_estado = ttk.Label(
            marco, textvariable=self.estado, wraplength=440,
            justify="left"
        )
        self.etiqueta_estado.grid(row=fila, column=0, columnspan=2,
                                  sticky="w")

        fila += 1

        self.barra = ttk.Progressbar(marco, mode="indeterminate",
                                     length=440)
        self.barra.grid(row=fila, column=0, columnspan=2, sticky="we",
                        pady=(8, 0))
        self.barra.grid_remove()

        marco.columnconfigure(1, weight=1)

        # Cerrar la ventana tambien limpia: si se cierra mientras una
        # instalacion esta en marcha, el temporal no se queda atras.
        raiz.protocol("WM_DELETE_WINDOW", self.cerrar)

    def cerrar(self):
        _limpiar_pendientes()
        self.raiz.destroy()

    # --- utilidades ---

    def decir(self, texto, color="#444"):
        self.estado.set(texto)
        self.etiqueta_estado.configure(foreground=color)
        self.raiz.update_idletasks()

    def ocupado(self, si):

        estado = "disabled" if si else "normal"

        self.boton_probar.configure(state=estado)
        self.boton_instalar.configure(state=estado)

        if si:
            self.barra.grid()
            self.barra.start(12)

        else:
            self.barra.stop()
            self.barra.grid_remove()

    # --- acciones ---

    def probar(self):

        self.ocupado(True)
        self.decir("Comprobando la conexion con el servidor...")

        def trabajo():
            correcto, mensaje = comprobar_servidor(self.servidor.get())

            def terminar():
                self.ocupado(False)
                self.decir(mensaje, "#0a6" if correcto else "#c00")

            self.raiz.after(0, terminar)

        threading.Thread(target=trabajo, daemon=True).start()

    def instalar(self):

        url = self.servidor.get().strip()
        credencial = self.credencial.get().strip()

        if not url:
            messagebox.showwarning(
                PRODUCTO, "Falta la direccion del servidor."
            )
            return

        if not self.ya_instalado:

            if not credencial:
                messagebox.showwarning(
                    PRODUCTO,
                    "Falta la credencial de instalacion.\n\n"
                    "La generas en el panel, en 'Instalar equipo'."
                )
                return

            if not credencial.startswith(PREFIJO_CREDENCIAL):
                messagebox.showwarning(
                    PRODUCTO,
                    "Esa no parece una credencial de instalacion: "
                    f"deben empezar por '{PREFIJO_CREDENCIAL}'."
                )
                return

        self.ocupado(True)

        def trabajo():

            # Se comprueba el servidor antes de tocar el equipo: si no
            # responde, no tiene sentido copiar archivos ni registrar
            # tareas.
            self.raiz.after(
                0, lambda: self.decir("Comprobando el servidor...")
            )

            correcto, mensaje = comprobar_servidor(url)

            if not correcto:

                def fallo_conexion():
                    self.ocupado(False)
                    self.decir(
                        f"No se instalo nada. {mensaje}", "#c00"
                    )

                self.raiz.after(0, fallo_conexion)
                return

            self.raiz.after(
                0, lambda: self.decir(
                    "Instalando. Puede tardar unos minutos; acepta el "
                    "aviso de Windows cuando aparezca."
                )
            )

            correcto, mensaje = instalar(
                url, credencial,
                lambda t: self.raiz.after(0, lambda: self.decir(t))
            )

            def terminar():

                self.ocupado(False)

                if not correcto:
                    self.decir(mensaje, "#c00")
                    messagebox.showerror(PRODUCTO, mensaje)
                    return

                identidad = identidad_existente()

                if identidad:
                    self.decir(
                        "Listo. El equipo ya aparece en el panel.\n"
                        f"Identificador: {identidad['device_id']}",
                        "#0a6"
                    )
                    messagebox.showinfo(
                        PRODUCTO,
                        "Instalacion terminada.\n\n"
                        "El equipo ya aparece en el panel, en "
                        "Dispositivos. No hace falta hacer nada mas."
                    )

                else:
                    self.decir(
                        "Instalado, pero todavia no se ha confirmado el "
                        "alta. Suele tardar menos de un minuto.",
                        "#a60"
                    )
                    messagebox.showwarning(
                        PRODUCTO,
                        "El Agent quedo instalado y en marcha, pero "
                        "todavia no se ha confirmado el alta.\n\n"
                        "Si en unos minutos no aparece en el panel, el "
                        "motivo estara en:\n"
                        + os.path.join(
                            INSTALL_DIR, "logs", "agent-service.log"
                        )
                    )

            self.raiz.after(0, terminar)

        threading.Thread(target=trabajo, daemon=True).start()


def main():

    if os.name != "nt":
        print(f"{PRODUCTO} solo se instala en Windows.")
        return 1

    raiz = tk.Tk()

    try:
        raiz.call("tk", "scaling", 1.3)

    except Exception:
        pass

    Ventana(raiz)

    raiz.mainloop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
