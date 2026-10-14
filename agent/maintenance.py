"""
Aviso de mantenimiento en la pantalla del equipo administrado.

Que es y que NO es
------------------

Es un AVISO: informa a quien esta delante de que alguien esta
trabajando en su equipo. No es una pantalla de bloqueo.

  - no impide usar Windows;
  - no ocupa el escritorio ni se pone en el centro;
  - no roba el foco a ninguna ventana;
  - no imita al sistema: lleva la marca VEYRA visible;
  - y se quita solo.

Como se comporta
----------------

    activar  ->  ficha completa, arriba a la derecha, 10 segundos
             ->  se encoge sola a una barra pequeña en una esquina
             ->  se puede volver a abrir pulsandola

Los 10 segundos son SOLO para encogerla. No tienen nada que ver con
el plazo del mantenimiento: la barra pequeña sigue ahi mientras dure,
y es el plazo —no la animacion— quien decide cuando desaparece todo.

Por que un proceso aparte y no una ventana del ayudante
-------------------------------------------------------

Tkinter quiere su propio hilo principal y un bucle de eventos que no
suelta. El ayudante ya tiene el suyo atendiendo el canal con el
servicio.

Con un proceso aparte se gana ademas lo que de verdad importa: el
aviso lleva SU PROPIO plazo y se retira solo aunque el ayudante
muera, aunque el servicio se caiga y aunque el servidor desaparezca.
Un aviso que dependiera de que alguien venga a quitarlo es justo lo
que no debe existir en la pantalla de un cliente.

El plazo
--------

No se muestra "hasta nuevo aviso": se muestra HASTA UNA HORA, que se
guarda en un archivo. Mientras el tecnico sigue ahi, el panel renueva
esa hora. Si cierra, se desconecta o se le cae la red, nadie renueva
y el aviso desaparece al cumplirse el plazo de gracia.

Asi una desconexion breve no lo retira de golpe —el plazo es de
minutos— y un error no lo deja puesto para siempre.
"""

import json
import os
import sys
import time


# El texto. Literal y en un solo sitio, para que no haya dos
# versiones distintas diciendo cosas parecidas.
TITULO_1 = "EQUIPO EN"
TITULO_2 = "MANTENIMIENTO"

MENSAJE_1 = "Por favor, no utilices esta computadora."
MENSAJE_2 = "Estamos realizando tareas de mantenimiento."

MARCA = "VEYRA"
SUBMARCA = "Administración remota"

COMPACTO = "VEYRA · Mantenimiento activo"


# Paleta. Azul marino de fondo y azul electrico de acento, como el
# resto de VEYRA. No se leen de veyra.css porque esto no es el panel:
# es una ventana de Windows y no hay hoja de estilos.
FONDO = "#0A1628"
FONDO_PANEL = "#0E1E35"
BORDE = "#1E3A5F"
ACENTO = "#2E8BFF"
ACENTO_CLARO = "#5CA8FF"
TEXTO = "#F2F7FF"
TEXTO_TENUE = "#8FA9C8"
PISTA = "#16304F"

# La llave del icono: casi blanca, para que destaque sobre el disco
LLAVE = "#EAF2FF"


# Cuanto se ve la ficha completa antes de encogerse sola.
#
# Es SOLO eso: una animacion. No desactiva el mantenimiento ni
# sustituye al plazo de vencimiento.
SEGUNDOS_EXPANDIDO = 10

# Cada cuanto se repinta el reloj y se mira si el plazo sigue vivo
LATIDO_MS = 500

# Margen con el borde de la pantalla
MARGEN = 24

ANCHO_FICHA, ALTO_FICHA = 620, 348
ANCHO_BARRA, ALTO_BARRA = 352, 58


# ==============================
# EL PLAZO
# ==============================

def archivo_de_plazo():
    """
    Donde se guarda hasta cuando debe verse el aviso.

    En la carpeta de datos del Agent, la unica que existe seguro y
    sobrevive a las actualizaciones.
    """

    try:
        from paths import get_data_dir

        carpeta = str(get_data_dir())

    except Exception:
        carpeta = os.path.join(
            os.environ.get("ProgramData", os.path.expanduser("~")),
            "RemoteAdmin"
        )

    os.makedirs(carpeta, exist_ok=True)

    return os.path.join(carpeta, "mantenimiento.plazo")


def escribir_plazo(hasta, total=None):
    """
    Deja en disco la hora limite y cuanto duraba el plazo entero.

    El total hace falta para la barra de progreso: sin el no se sabe
    que fraccion queda. Se escribe en JSON, pero leer admite tambien
    el formato anterior —un numero suelto— para no romper un aviso
    que estuviera puesto al actualizar.
    """

    ruta = archivo_de_plazo()

    hasta = int(hasta)

    if not total:
        total = max(1, hasta - int(time.time()))

    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump({"hasta": hasta, "total": int(total)}, archivo)

    return ruta


def _leer_crudo():

    try:
        with open(archivo_de_plazo(), "r", encoding="utf-8") as archivo:
            texto = archivo.read().strip()

    except OSError:
        return None

    if not texto:
        return None

    try:
        datos = json.loads(texto)

        if isinstance(datos, dict):
            return datos

    except ValueError:
        pass

    # Formato anterior: solo el instante limite
    try:
        return {"hasta": int(texto), "total": 0}

    except ValueError:
        return None


def leer_plazo():
    """La hora limite, o 0 si no hay aviso que mostrar."""

    datos = _leer_crudo()

    return int(datos.get("hasta", 0)) if datos else 0


def leer_total():
    """Cuanto duraba el plazo entero, para la barra de progreso."""

    datos = _leer_crudo()

    return int(datos.get("total", 0)) if datos else 0


def borrar_plazo():

    try:
        os.remove(archivo_de_plazo())

    except OSError:
        pass


# ==============================
# LA VENTANA
# ==============================

def mostrar_ventana():
    """
    Pinta el aviso y no vuelve hasta que se cumple el plazo.

    Se ejecuta en su propio proceso: aqui el bucle de Tkinter es el
    dueño del hilo principal, como quiere.
    """

    import tkinter as tk

    raiz = tk.Tk()

    # Sin barra de titulo: no hay nada que minimizar desde Windows, y
    # una X del sistema invitaria a cerrarlo, que no es decision de
    # quien esta delante del equipo.
    raiz.overrideredirect(True)
    raiz.attributes("-topmost", True)
    raiz.configure(bg=FONDO)

    estado = {"compacto": False, "desde": time.time()}

    # --- Las dos caras del aviso ---

    ficha = tk.Frame(raiz, bg=FONDO)
    barra = tk.Frame(raiz, bg=FONDO)

    _pintar_ficha(tk, ficha, raiz, estado)
    reloj_barra = _pintar_barra(tk, barra, estado)

    def colocar():
        """
        Arriba a la derecha cuando esta abierta, abajo a la derecha
        cuando esta encogida. Nunca en el centro: ahi taparia justo
        donde se trabaja.
        """

        ancho_pantalla = raiz.winfo_screenwidth()
        alto_pantalla = raiz.winfo_screenheight()

        if estado["compacto"]:
            ancho, alto = ANCHO_BARRA, ALTO_BARRA
            x = ancho_pantalla - ancho - MARGEN
            # Por encima de la barra de tareas
            y = alto_pantalla - alto - MARGEN - 48

        else:
            ancho, alto = ANCHO_FICHA, ALTO_FICHA
            x = ancho_pantalla - ancho - MARGEN
            y = MARGEN

        raiz.geometry("%dx%d+%d+%d" % (ancho, alto, max(0, x), max(0, y)))

    def cambiar(compacto):

        estado["compacto"] = bool(compacto)
        estado["desde"] = time.time()

        if estado["compacto"]:
            ficha.pack_forget()
            barra.pack(fill="both", expand=True)

        else:
            barra.pack_forget()
            ficha.pack(fill="both", expand=True)

        colocar()

    estado["cambiar"] = cambiar

    cambiar(False)

    def vigilar():
        """
        Tres cosas, cada medio segundo.

        La primera es la que importa: si el plazo se cumplio, el
        aviso se va. Es la red que impide que un fallo en cualquier
        otro sitio —el ayudante, el servicio, el servidor, la red—
        deje esto puesto en la pantalla de un cliente para siempre.
        """

        restante = leer_plazo() - time.time()

        if restante <= 0:
            raiz.destroy()
            return

        # Se encoge sola a los diez segundos. Solo cambia de aspecto:
        # el mantenimiento sigue activo y el plazo, intacto.
        if (not estado["compacto"]
                and time.time() - estado["desde"] >= SEGUNDOS_EXPANDIDO):
            cambiar(True)

        estado["repintar"](restante)
        reloj_barra(restante)

        # Hay ventanas que se ponen encima despues. Se reafirma.
        try:
            raiz.attributes("-topmost", True)

        except Exception:
            pass

        raiz.after(LATIDO_MS, vigilar)

    raiz.after(LATIDO_MS, vigilar)

    raiz.update_idletasks()
    _no_robar_el_foco(raiz)

    raiz.mainloop()


def _reloj(segundos):
    """mm:ss, sin numeros negativos."""

    segundos = max(0, int(segundos))

    return "%02d:%02d" % (segundos // 60, segundos % 60)


def _marca(tk, padre, grande=True):
    """
    El logotipo: una V angular y la palabra VEYRA.

    Se dibuja con lineas en vez de cargar una imagen para que el
    aviso no dependa de ningun archivo suelto: si faltara, el equipo
    se quedaria sin aviso por un adorno.
    """

    caja = tk.Frame(padre, bg=FONDO_PANEL if grande else FONDO_PANEL)

    lado = 30 if grande else 22

    lienzo = tk.Canvas(
        caja, width=lado, height=lado, bg=FONDO_PANEL,
        highlightthickness=0, bd=0
    )
    lienzo.pack(side="left")

    u = lado / 30.0

    lienzo.create_polygon(
        3 * u, 5 * u, 11 * u, 5 * u, 15 * u, 17 * u,
        19 * u, 5 * u, 27 * u, 5 * u, 15 * u, 27 * u,
        fill=ACENTO, outline=""
    )

    lienzo.create_polygon(
        11 * u, 5 * u, 15 * u, 5 * u, 15 * u, 17 * u,
        fill=ACENTO_CLARO, outline=""
    )

    # En la barra pequeña va SOLO el simbolo: su texto ya dice
    # "VEYRA · Mantenimiento activo", y repetir la palabra dejaba
    # "VEYRA VEYRA ·..." en 330 pixeles.
    if not grande:
        return caja

    texto = tk.Frame(caja, bg=FONDO_PANEL)
    texto.pack(side="left", padx=(7, 0))

    tk.Label(
        texto, text=MARCA, bg=FONDO_PANEL, fg=TEXTO,
        font=("Segoe UI", 19, "bold")
    ).pack(anchor="w")

    tk.Label(
        texto, text=SUBMARCA, bg=FONDO_PANEL, fg=TEXTO_TENUE,
        font=("Segoe UI", 8)
    ).pack(anchor="w")

    return caja


def _pintar_ficha(tk, ficha, raiz, estado):
    """La tarjeta completa, la que se ve los primeros segundos."""

    panel = tk.Frame(
        ficha, bg=FONDO_PANEL,
        highlightbackground=BORDE, highlightthickness=1
    )
    panel.pack(fill="both", expand=True)

    # Franja de acento arriba, como en el panel web
    tk.Frame(panel, bg=ACENTO, height=4).pack(fill="x")

    # --- Encabezado: marca a la izquierda, controles a la derecha ---

    cabeza = tk.Frame(panel, bg=FONDO_PANEL)
    cabeza.pack(fill="x", padx=22, pady=(16, 0))

    _marca(tk, cabeza, grande=True).pack(side="left")

    controles = tk.Frame(cabeza, bg=FONDO_PANEL)
    controles.pack(side="right")

    def boton_icono(texto, accion, ayuda):

        etiqueta = tk.Label(
            controles, text=texto, bg=FONDO_PANEL, fg=TEXTO_TENUE,
            font=("Segoe UI", 15), cursor="hand2", padx=9
        )
        etiqueta.pack(side="left")
        etiqueta.bind("<Button-1>", lambda e: accion())
        etiqueta.bind("<Enter>", lambda e: etiqueta.config(fg=TEXTO))
        etiqueta.bind("<Leave>", lambda e: etiqueta.config(fg=TEXTO_TENUE))

        return etiqueta

    boton_icono("—", lambda: estado["cambiar"](True), "Encoger")

    # La X encoge, NO desactiva el mantenimiento.
    #
    # Quien esta delante del equipo no decide cuando termina: eso lo
    # decide el tecnico desde el panel, o el plazo. Si la X apagara
    # el aviso, bastaria con pulsarla para quedarse sin saber que
    # alguien esta dentro.
    boton_icono("✕", lambda: estado["cambiar"](True), "Encoger")

    # --- Cuerpo: icono y textos ---

    cuerpo = tk.Frame(panel, bg=FONDO_PANEL)
    cuerpo.pack(fill="both", expand=True, padx=22, pady=(12, 0))

    lienzo = tk.Canvas(
        cuerpo, width=96, height=96, bg=FONDO_PANEL,
        highlightthickness=0, bd=0
    )
    lienzo.pack(side="left", padx=(4, 20))

    lienzo.create_oval(2, 2, 94, 94, outline=ACENTO, width=2)
    lienzo.create_oval(10, 10, 86, 86, fill="#11294A", outline="")

    # Una llave inglesa.
    #
    # La cabeza es un arco con una ABERTURA ANCHA mirando arriba a la
    # derecha, y el mango sale de ella hacia abajo a la izquierda.
    # Con una abertura estrecha —el primer intento— la cabeza parecia
    # una letra C y el conjunto, una lupa.
    #
    # Los angulos de create_arc se cuentan en sentido antihorario
    # desde las 3 en punto: start=100 con extent=250 deja el hueco
    # entre los -10 y los 100 grados, o sea arriba a la derecha.

    cabeza_x, cabeza_y, radio = 60, 36, 15

    # Mango: sale del centro de la cabeza hacia abajo a la izquierda
    lienzo.create_line(
        cabeza_x - 4, cabeza_y + 4, 34, 64,
        fill=LLAVE, width=11, capstyle="round"
    )

    # Cabeza abierta
    lienzo.create_arc(
        cabeza_x - radio, cabeza_y - radio,
        cabeza_x + radio, cabeza_y + radio,
        start=100, extent=250,
        style="arc", outline=LLAVE, width=9
    )

    textos = tk.Frame(cuerpo, bg=FONDO_PANEL)
    textos.pack(side="left", fill="both", expand=True)

    tk.Label(
        textos, text=TITULO_1, bg=FONDO_PANEL, fg=TEXTO,
        font=("Segoe UI", 22, "bold")
    ).pack(anchor="w")

    tk.Label(
        textos, text=TITULO_2, bg=FONDO_PANEL, fg=ACENTO,
        font=("Segoe UI", 22, "bold")
    ).pack(anchor="w")

    tk.Label(
        textos, text=MENSAJE_1, bg=FONDO_PANEL, fg=TEXTO,
        font=("Segoe UI", 11)
    ).pack(anchor="w", pady=(10, 0))

    tk.Label(
        textos, text=MENSAJE_2, bg=FONDO_PANEL, fg=TEXTO,
        font=("Segoe UI", 11)
    ).pack(anchor="w")

    # --- Pie: reloj, progreso y el boton de ocultar ---

    pie = tk.Frame(
        panel, bg=FONDO,
        highlightbackground=BORDE, highlightthickness=1
    )
    pie.pack(fill="x", side="bottom", padx=22, pady=16)

    izquierda = tk.Frame(pie, bg=FONDO)
    izquierda.pack(side="left", fill="x", expand=True, padx=16, pady=12)

    linea = tk.Frame(izquierda, bg=FONDO)
    linea.pack(anchor="w", fill="x")

    tk.Label(
        linea, text="Aviso visible durante", bg=FONDO, fg=TEXTO_TENUE,
        font=("Segoe UI", 10)
    ).pack(side="left")

    reloj = tk.Label(
        linea, text="--:--", bg=FONDO, fg=ACENTO,
        font=("Segoe UI", 10, "bold")
    )
    reloj.pack(side="left", padx=(6, 0))

    progreso = tk.Canvas(
        izquierda, height=6, bg=FONDO, highlightthickness=0, bd=0
    )
    progreso.pack(fill="x", pady=(8, 0))

    boton = tk.Label(
        pie, text="Ocultar aviso", bg=FONDO, fg=ACENTO_CLARO,
        font=("Segoe UI", 10), cursor="hand2",
        padx=18, pady=9,
        highlightbackground=ACENTO, highlightthickness=1
    )
    boton.pack(side="right", padx=16, pady=12)
    boton.bind("<Button-1>", lambda e: estado["cambiar"](True))
    boton.bind("<Enter>", lambda e: boton.config(fg=TEXTO))
    boton.bind("<Leave>", lambda e: boton.config(fg=ACENTO_CLARO))

    def repintar(restante):

        reloj.config(text=_reloj(restante))

        ancho = progreso.winfo_width()

        if ancho < 2:
            return

        total = leer_total() or max(1, int(restante))

        fraccion = max(0.0, min(1.0, restante / float(total)))

        progreso.delete("all")

        progreso.create_rectangle(
            0, 1, ancho, 6, fill=PISTA, outline=""
        )

        if fraccion > 0:
            progreso.create_rectangle(
                0, 1, max(3, ancho * fraccion), 6,
                fill=ACENTO, outline=""
            )

    estado["repintar"] = repintar


def _pintar_barra(tk, barra, estado):
    """La barra pequeña: lo que se ve el resto del tiempo."""

    panel = tk.Frame(
        barra, bg=FONDO_PANEL,
        highlightbackground=BORDE, highlightthickness=1
    )
    panel.pack(fill="both", expand=True)

    fila = tk.Frame(panel, bg=FONDO_PANEL)
    fila.pack(fill="both", expand=True, padx=14, pady=10)

    _marca(tk, fila, grande=False).pack(side="left")

    centro = tk.Frame(fila, bg=FONDO_PANEL)
    centro.pack(side="left", padx=(12, 0))

    tk.Label(
        centro, text=COMPACTO, bg=FONDO_PANEL, fg=TEXTO,
        font=("Segoe UI", 10)
    ).pack(anchor="w")

    reloj = tk.Label(
        fila, text="--:--", bg=FONDO_PANEL, fg=TEXTO_TENUE,
        font=("Segoe UI", 9)
    )
    reloj.pack(side="right", padx=(8, 4))

    # Pulsar la barra la vuelve a abrir: quien no llego a leer el
    # mensaje tiene que poder volver a verlo.
    def abrir(evento=None):
        estado["cambiar"](False)

    for nodo in (panel, fila, centro, reloj):
        nodo.bind("<Button-1>", abrir)
        nodo.config(cursor="hand2")

    for hijo in centro.winfo_children():
        hijo.bind("<Button-1>", abrir)
        hijo.config(cursor="hand2")

    def repintar(restante):
        reloj.config(text=_reloj(restante))

    return repintar


def _no_robar_el_foco(raiz):
    """
    El aviso nunca se pone delante de lo que estes escribiendo.

    WS_EX_NOACTIVATE: Windows no le da el foco al mostrarlo ni al
    pulsarlo. Se ve y se puede pulsar, pero no interrumpe.

    Lo que NO se pone aqui es WS_EX_TRANSPARENT, que dejaba pasar los
    clics por completo. Lo llevaba la version anterior, que no tenia
    botones; esta si los tiene, y una ventana que no recibe clics
    tendria botones de adorno. A cambio, la ficha solo esta diez
    segundos y la barra que queda ocupa 330x58 en una esquina.

    Si algo falla, se sigue adelante: un aviso imperfecto es mejor
    que ningun aviso.
    """

    if os.name != "nt":
        return False

    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080

        usuario = ctypes.windll.user32

        ventana = usuario.GetParent(raiz.winfo_id()) or raiz.winfo_id()

        estilo = usuario.GetWindowLongW(ventana, GWL_EXSTYLE)

        # TOOLWINDOW ademas lo saca de Alt+Tab: no es una aplicacion
        # entre la que cambiar, es un aviso.
        usuario.SetWindowLongW(
            ventana, GWL_EXSTYLE,
            estilo | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        )

        return True

    except Exception:
        return False


# ==============================
# CONTROL DESDE EL AYUDANTE
# ==============================

_proceso = None


def activo():
    """True si el aviso se esta mostrando ahora mismo."""

    return _proceso is not None and _proceso.poll() is None


def activar(segundos):
    """
    Muestra el aviso, o renueva su plazo si ya estaba.

    UNA SOLA VENTANA. Activarlo diez veces no abre diez avisos:
    renueva el plazo del que ya hay, que es lo que se quiere cuando
    el panel va refrescando mientras dura el mantenimiento.
    """

    global _proceso

    import subprocess

    segundos = max(30, min(int(segundos or 600), 8 * 3600))

    escribir_plazo(time.time() + segundos, segundos)

    if activo():
        # Ya hay ventana: con renovar el plazo basta
        return False

    opciones = {}

    if os.name == "nt":
        # Sin consola negra detras del aviso
        opciones["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    _proceso = subprocess.Popen(
        [_interprete_sin_consola(), os.path.abspath(__file__), "--ventana"],
        close_fds=True, **opciones
    )

    return True


def desactivar():
    """Retira el aviso. Idempotente: si no habia, no pasa nada."""

    global _proceso

    borrar_plazo()

    if _proceso is None:
        return False

    try:
        if _proceso.poll() is None:
            _proceso.terminate()

            try:
                _proceso.wait(timeout=5)

            except Exception:
                _proceso.kill()

    except Exception:
        pass

    _proceso = None

    return True


def _interprete_sin_consola():
    """pythonw si existe, para que no parpadee una consola."""

    ejecutable = sys.executable or "python.exe"

    sin_consola = ejecutable.replace("python.exe", "pythonw.exe")

    return sin_consola if os.path.isfile(sin_consola) else ejecutable


if __name__ == "__main__":

    if "--ventana" in sys.argv:

        # Sin plazo vigente no se abre nada: evita que un arranque
        # suelto deje una ventana sin motivo.
        if time.time() < leer_plazo():
            mostrar_ventana()
