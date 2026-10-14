"""
El aviso de mantenimiento en la pantalla del equipo.

    .venv\\Scripts\\python tests/test_mantenimiento.py

Es un AVISO, no una pantalla de bloqueo. Estas pruebas comprueban
sobre todo lo que NO debe hacer: no capturar el raton ni el teclado,
no impedir usar Windows, no imitar al sistema operativo y, la mas
importante, no quedarse puesto si algo falla.

El plazo
--------

El aviso no se muestra "hasta nuevo aviso": se muestra HASTA UNA
HORA, guardada en un archivo del propio equipo. Mientras el tecnico
trabaja, el panel renueva esa hora. Si se desconecta, se le cae la
red o cierra el navegador, nadie renueva y el aviso se retira solo.

Que el plazo viva en el EQUIPO y no en el servidor es lo que hace que
el aviso desaparezca aunque el servidor deje de existir. Eso se
prueba aqui de verdad, con relojes falsos.
"""

import io
import json
import os
import sys
import tempfile
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))
sys.path.insert(0, str(RAIZ / "tests"))

AVISO = io.open(RAIZ / "agent" / "maintenance.py", encoding="utf-8").read()
AYUDANTE = io.open(RAIZ / "agent" / "helper.py", encoding="utf-8").read()
ORDENES = io.open(RAIZ / "agent" / "commands.py", encoding="utf-8").read()
JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==================================================================
# 1. EL PLAZO, PROBADO DE VERDAD
# ==================================================================

def test_el_plazo_se_guarda_y_se_lee():

    import maintenance

    carpeta = tempfile.mkdtemp(prefix="aviso-")

    original = maintenance.archivo_de_plazo

    maintenance.archivo_de_plazo = lambda: os.path.join(
        carpeta, "mantenimiento.plazo"
    )

    try:
        comprobar("Sin archivo, no hay plazo",
                  maintenance.leer_plazo() == 0)

        hasta = int(time.time()) + 600
        maintenance.escribir_plazo(hasta)

        comprobar("Se guarda y se lee igual",
                  maintenance.leer_plazo() == hasta,
                  str(maintenance.leer_plazo()))

        maintenance.borrar_plazo()

        comprobar("Borrarlo lo deja en cero",
                  maintenance.leer_plazo() == 0)

        # Borrar dos veces no puede reventar: pasa cuando se desactiva
        # un aviso que ya habia caducado solo.
        maintenance.borrar_plazo()

        comprobar("Borrarlo dos veces no revienta", True)

    finally:
        maintenance.archivo_de_plazo = original


def test_un_plazo_caducado_retira_el_aviso():
    """
    La red de seguridad: si nadie renueva, el aviso se va.

    Se comprueba la DECISION, que es lo que vigila la ventana cada
    pocos segundos, sin abrir ninguna ventana.
    """

    import maintenance

    carpeta = tempfile.mkdtemp(prefix="aviso-")

    original = maintenance.archivo_de_plazo

    maintenance.archivo_de_plazo = lambda: os.path.join(
        carpeta, "mantenimiento.plazo"
    )

    try:
        maintenance.escribir_plazo(time.time() + 300)

        comprobar("Con plazo vigente, se sigue viendo",
                  time.time() < maintenance.leer_plazo())

        maintenance.escribir_plazo(time.time() - 1)

        comprobar("Con el plazo cumplido, se retira",
                  time.time() >= maintenance.leer_plazo())

        maintenance.borrar_plazo()

        comprobar("Y sin archivo tambien se retira",
                  time.time() >= maintenance.leer_plazo())

    finally:
        maintenance.archivo_de_plazo = original


def test_el_plazo_esta_acotado():
    """
    Ni tan corto que parpadee, ni tan largo que se olvide puesto.
    """

    import maintenance

    carpeta = tempfile.mkdtemp(prefix="aviso-")

    original_archivo = maintenance.archivo_de_plazo
    original_popen = None

    maintenance.archivo_de_plazo = lambda: os.path.join(
        carpeta, "mantenimiento.plazo"
    )

    import subprocess

    class FalsoProceso:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    original_popen = subprocess.Popen
    subprocess.Popen = lambda *a, **k: FalsoProceso()

    try:
        maintenance._proceso = None
        maintenance.activar(1)

        restante = maintenance.leer_plazo() - time.time()

        comprobar("Un plazo ridiculo se sube a un minimo",
                  restante >= 25, "quedaban %.0f s" % restante)

        maintenance._proceso = None
        maintenance.activar(99999999)

        restante = maintenance.leer_plazo() - time.time()

        comprobar("Y uno enorme se recorta",
                  restante <= 8 * 3600 + 5, "quedaban %.0f s" % restante)

    finally:
        subprocess.Popen = original_popen
        maintenance.archivo_de_plazo = original_archivo
        maintenance._proceso = None


def test_activarlo_varias_veces_no_abre_varias_ventanas():
    """Lo pediste expresamente, y es lo que pasa al renovar."""

    import maintenance
    import subprocess

    carpeta = tempfile.mkdtemp(prefix="aviso-")

    original_archivo = maintenance.archivo_de_plazo
    original_popen = subprocess.Popen

    maintenance.archivo_de_plazo = lambda: os.path.join(
        carpeta, "mantenimiento.plazo"
    )

    lanzamientos = []

    class FalsoProceso:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def falso_popen(*a, **k):
        lanzamientos.append(a)
        return FalsoProceso()

    subprocess.Popen = falso_popen

    try:
        maintenance._proceso = None

        primera = maintenance.activar(600)

        comprobar("La primera vez abre la ventana",
                  primera is True and len(lanzamientos) == 1,
                  "lanzamientos: %d" % len(lanzamientos))

        anterior = maintenance.leer_plazo()

        time.sleep(1.1)

        for _ in range(5):
            maintenance.activar(600)

        comprobar("Activarlo cinco veces mas NO abre otra",
                  len(lanzamientos) == 1,
                  "lanzamientos: %d" % len(lanzamientos))

        comprobar("Pero si renueva el plazo",
                  maintenance.leer_plazo() > anterior,
                  "antes %d, ahora %d" % (anterior,
                                          maintenance.leer_plazo()))

        comprobar("Desactivarlo lo retira",
                  maintenance.desactivar() is True)

        comprobar("Y borra el plazo",
                  maintenance.leer_plazo() == 0)

        comprobar("Desactivarlo dos veces no revienta",
                  maintenance.desactivar() is False)

    finally:
        subprocess.Popen = original_popen
        maintenance.archivo_de_plazo = original_archivo
        maintenance._proceso = None


# ==================================================================
# 2. LO QUE NO DEBE HACER
# ==================================================================

def test_no_es_una_pantalla_de_bloqueo():

    comprobar("No toma el control exclusivo del raton",
              "grab_set" not in AVISO)

    comprobar("No captura el teclado",
              "grab_set_global" not in AVISO
              and "focus_force" not in AVISO)

    comprobar("No ocupa la pantalla entera",
              "fullscreen" not in AVISO
              and "-fullscreen" not in AVISO)

    comprobar("No se queda con el foco",
              "takefocus" not in AVISO or "takefocus=0" in AVISO)

    # Esta encima, que es distinto de bloquear
    comprobar("Si esta siempre encima",
              '"-topmost", True' in AVISO)

    comprobar("Y sin barra de titulo",
              "overrideredirect(True)" in AVISO)


def test_dice_lo_que_es_y_no_imita_a_windows():

    # El titulo va en dos lineas, la segunda en azul, como la
    # referencia. Se comprueban las dos piezas y que juntas digan lo
    # que tienen que decir.
    comprobar("Lleva el titulo pedido",
              'TITULO_1 = "EQUIPO EN"' in AVISO
              and 'TITULO_2 = "MANTENIMIENTO"' in AVISO)

    comprobar("Y el texto pedido",
              "no utilices esta computadora" in AVISO.lower()
              and "tareas de mantenimiento" in AVISO.lower())

    comprobar("Se identifica como VEYRA",
              "VEYRA" in AVISO)

    for palabra in ("Windows Update", "Microsoft",
                    "Inicio de sesion de Windows"):
        comprobar("No se hace pasar por %s" % palabra,
                  palabra.lower() not in AVISO.lower())


def test_se_retira_solo_aunque_todo_lo_demas_falle():
    """
    Lo que de verdad impide que esto acabe mal.
    """

    comprobar("La ventana vigila su plazo",
              "def vigilar(" in AVISO and "leer_plazo()" in AVISO)

    comprobar("Y se destruye al cumplirse",
              "raiz.destroy()" in AVISO)

    comprobar("Vive en su propio proceso",
              "subprocess.Popen" in AVISO)

    # Si se abriera sin plazo, quedaria una ventana sin motivo
    comprobar("No se abre sin un plazo vigente",
              "if time.time() < leer_plazo():" in AVISO)


# ==================================================================
# 3. EL CAMINO COMPLETO
# ==================================================================

def test_la_orden_llega_al_escritorio():
    """
    El servicio corre en la Sesion 0, donde no hay escritorio: una
    ventana ahi no la ve nadie. Tiene que atenderla el ayudante.
    """

    import commands

    comprobar("maintenance_on va al escritorio",
              commands.requires_desktop('maintenance_on:{"seconds": 600}'))

    comprobar("maintenance_off tambien",
              commands.requires_desktop("maintenance_off"))

    comprobar("El ayudante la ejecuta",
              "maintenance.activar(" in AYUDANTE
              and "maintenance.desactivar()" in AYUDANTE)

    comprobar("Y el Agent se la reenvia",
              "maintenance_on:" in io.open(
                  RAIZ / "agent" / "agent.py", encoding="utf-8"
              ).read())


def test_el_panel_lo_enciende_y_lo_renueva():

    comprobar("Hay un boton en la ventana remota",
              'id="remote-mantenimiento"' in HTML)

    comprobar("Llama al endpoint",
              "/maintenance`" in JS)

    comprobar("Renueva mientras dura",
              "MANTENIMIENTO_RENUEVA_MS" in JS
              and "setInterval" in JS)

    comprobar("Y deja de renovar al cerrar",
              "detenerRenovacionDeMantenimiento()" in JS)

    cerrar = JS.split("async function stopRemoteDesktop() {", 1)[1][:1800]

    comprobar("Al cerrar la sesion se olvida el aviso",
              "olvidarMantenimiento()" in cerrar)

    comprobar("Y se pide retirarlo",
              "enabled: false" in cerrar)


# ==================================================================
# 4. PERMISOS
# ==================================================================

def test_solo_quien_puede_controlar_puede_avisar():

    import users_harness as h
    import backend.users as users
    import backend.organizations as organizations
    from backend.database import get_connection

    h.reiniciar()

    organizacion = organizations.ensure_default_organization()
    propietario = users.get_user(h.OWNER)

    if propietario and propietario.get("organization_id"):
        organizacion = propietario["organization_id"]

    conexion = get_connection()
    conexion.execute(
        "INSERT OR REPLACE INTO devices (device_id, hostname, "
        "organization_id) VALUES (?, ?, ?)",
        ("equipo-m", "PC-M", organizacion)
    )
    conexion.commit()
    conexion.close()

    h.crear_subadmin("sin-remoto", permisos=["devices.view"])
    h.crear_subadmin("con-remoto",
                     permisos=["devices.view", "remote.control"])

    ruta = "/api/devices/equipo-m/maintenance"

    sin = h.cliente("sin-remoto").post(ruta, json={"enabled": True})

    comprobar("Sin remote.control no puede avisar",
              sin.status_code == 403,
              "respondio %d" % sin.status_code)

    con = h.cliente("con-remoto").post(ruta, json={"enabled": True})

    comprobar("Con remote.control si puede",
              con.status_code != 403,
              "respondio %d" % con.status_code)

    fuera = h.cliente().post(ruta, json={"enabled": True})

    comprobar("Sin sesion, no",
              fuera.status_code in (401, 403),
              "respondio %d" % fuera.status_code)


def test_el_aviso_no_se_pone_delante_de_lo_que_escribes():
    """
    La version anterior dejaba pasar TODOS los clics
    (WS_EX_TRANSPARENT) porque no tenia ningun boton.

    El rediseño si los tiene —ocultar, encoger, volver a abrir— y
    una ventana que no recibe clics tendria botones de adorno. Asi
    que la transparencia total se cambia por tres cosas que molestan
    mucho menos que la ficha enorme y centrada de antes:

      - NUNCA roba el foco (WS_EX_NOACTIVATE): se puede seguir
        escribiendo aunque aparezca encima;
      - no sale en Alt+Tab (WS_EX_TOOLWINDOW): no es una aplicacion
        entre las que cambiar, es un aviso;
      - y la ficha solo esta 10 segundos; despues queda una barra de
        352x58 en una esquina.
    """

    # Se comprueba el VALOR, no el nombre.
    #
    # La primera version miraba solo que la palabra estuviera
    # escrita, y por eso no se entero cuando puse la constante a
    # cero para ver si la prueba servia. No servia.
    comprobar("Nunca roba el foco",
              "WS_EX_NOACTIVATE = 0x08000000" in AVISO,
              "la constante no vale lo que debe")

    comprobar("Y no aparece en Alt+Tab",
              "WS_EX_TOOLWINDOW = 0x00000080" in AVISO,
              "la constante no vale lo que debe")

    comprobar("Y las dos se aplican de verdad",
              "estilo | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW" in AVISO)

    comprobar("Si algo falla, el aviso se muestra igual",
              "except Exception:" in AVISO.split(
                  "_no_robar_el_foco", 1)[1])

    comprobar("No se pone en el centro de la pantalla",
              "ancho_pantalla - ancho - MARGEN" in AVISO)


def test_la_ficha_se_encoge_sola_sin_tocar_el_plazo():
    """
    Los 10 segundos son SOLO para encogerla.

    Es la confusion que mas daño haria: si ese contador apagara el
    mantenimiento, el aviso desapareceria a los diez segundos y el
    tecnico creeria que sigue puesto.
    """

    import maintenance

    comprobar("Hay un plazo para encogerse",
              maintenance.SEGUNDOS_EXPANDIDO == 10,
              str(maintenance.SEGUNDOS_EXPANDIDO))

    # Y en el codigo, ese contador solo llama a cambiar(True)
    bloque = AVISO.split("SEGUNDOS_EXPANDIDO):", 1)[1][:200]

    comprobar("Y solo cambia de aspecto",
              "cambiar(True)" in bloque, bloque[:120])

    comprobar("No borra el plazo",
              "borrar_plazo" not in bloque)

    comprobar("No cierra la ventana",
              "destroy" not in bloque)

    # El unico que cierra es el vencimiento de verdad
    vigilar = AVISO.split("def vigilar():", 1)[1][:700]

    comprobar("Quien cierra es el vencimiento del plazo",
              "restante <= 0" in vigilar and "raiz.destroy()" in vigilar)


def test_la_barra_pequena_se_puede_volver_a_abrir():
    """Quien no llego a leer el mensaje tiene que poder volver."""

    barra = AVISO.split("def _pintar_barra(", 1)[1]

    comprobar("Pulsar la barra la abre",
              'estado["cambiar"](False)' in barra)

    comprobar("Con cursor de mano, para que se note",
              'cursor="hand2"' in barra)

    comprobar("Y ensena cuanto queda",
              "_reloj(restante)" in barra)


def test_la_x_encoge_pero_no_apaga_el_mantenimiento():
    """
    Quien esta delante del equipo no decide cuando termina.

    Si la X apagara el aviso, bastaria con pulsarla para quedarse
    sin saber que alguien esta dentro de la maquina.
    """

    ficha = AVISO.split("def _pintar_ficha(", 1)[1]
    ficha = ficha.split("def _pintar_barra(", 1)[0]

    comprobar("La X encoge",
              'boton_icono("✕", lambda: estado["cambiar"](True)'
              in ficha or 'estado["cambiar"](True)' in ficha)

    comprobar("Ningun boton de la ficha desactiva nada",
              "desactivar()" not in ficha and "borrar_plazo" not in ficha)

    comprobar("Y el boton de ocultar tambien encoge",
              "Ocultar aviso" in ficha)


def test_el_reloj_y_la_barra_de_progreso():

    comprobar("Se guarda cuanto duraba el plazo entero",
              "def leer_total(" in AVISO)

    comprobar("Para poder dibujar la fraccion que queda",
              "restante / float(total)" in AVISO)

    comprobar("El reloj no ensena numeros negativos",
              "max(0, int(segundos))" in AVISO)

    import maintenance

    comprobar("Formato mm:ss", maintenance._reloj(125) == "02:05",
              maintenance._reloj(125))

    comprobar("Y un vencido se ve como 00:00",
              maintenance._reloj(-40) == "00:00",
              maintenance._reloj(-40))


def test_se_lee_el_formato_anterior():
    """
    Al actualizar puede haber un aviso puesto con el formato viejo
    —un numero suelto en vez de JSON. No puede quedarse colgado.
    """

    import maintenance
    import tempfile

    carpeta = tempfile.mkdtemp(prefix="aviso-")
    original = maintenance.archivo_de_plazo

    ruta = os.path.join(carpeta, "mantenimiento.plazo")
    maintenance.archivo_de_plazo = lambda: ruta

    try:
        hasta = int(time.time()) + 300

        with io.open(ruta, "w", encoding="utf-8") as archivo:
            archivo.write(str(hasta))

        comprobar("Se entiende el formato anterior",
                  maintenance.leer_plazo() == hasta,
                  str(maintenance.leer_plazo()))

        comprobar("Y sin total no se rompe",
                  maintenance.leer_total() == 0)

        # Un archivo con basura tampoco puede colgar el aviso
        with io.open(ruta, "w", encoding="utf-8") as archivo:
            archivo.write("{esto no es json")

        comprobar("Un archivo corrupto se trata como sin plazo",
                  maintenance.leer_plazo() == 0)

    finally:
        maintenance.archivo_de_plazo = original



# ==================================================================

def principal():

    pruebas = [
        test_el_plazo_se_guarda_y_se_lee,
        test_un_plazo_caducado_retira_el_aviso,
        test_el_plazo_esta_acotado,
        test_activarlo_varias_veces_no_abre_varias_ventanas,
        test_no_es_una_pantalla_de_bloqueo,
        test_dice_lo_que_es_y_no_imita_a_windows,
        test_se_retira_solo_aunque_todo_lo_demas_falle,
        test_el_aviso_no_se_pone_delante_de_lo_que_escribes,
        test_la_ficha_se_encoge_sola_sin_tocar_el_plazo,
        test_la_barra_pequena_se_puede_volver_a_abrir,
        test_la_x_encoge_pero_no_apaga_el_mantenimiento,
        test_el_reloj_y_la_barra_de_progreso,
        test_se_lee_el_formato_anterior,
        test_la_orden_llega_al_escritorio,
        test_el_panel_lo_enciende_y_lo_renueva,
        test_solo_quien_puede_controlar_puede_avisar
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("AVISO DE MANTENIMIENTO")
    print("=" * 66)
    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if not bien:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    if not fallos:
        print("  todo correcto")

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    print()
    print("-" * 66)
    print("PENDIENTE EN UNA MAQUINA REAL")
    print("-" * 66)
    print("""
La ventana no se ha abierto aqui: esto comprueba la decision, el
plazo y el camino, no los pixeles. Queda por ver en Windows:

  - que el aviso aparece y se lee bien
  - que el tecnico puede seguir pulsando en las ventanas de debajo
  - que el usuario del equipo puede seguir usando Windows
  - que al desconectar al tecnico desaparece tras el plazo de gracia
  - que no se abre un segundo aviso al renovar
""")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
