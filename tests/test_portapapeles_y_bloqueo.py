"""
Portapapeles compartido, bloqueo de entrada y mayusculas.

    .venv\\Scripts\\python tests/test_portapapeles_y_bloqueo.py

Lo que se arregla aqui
----------------------

1. No habia portapapeles compartido. Administrar un equipo escribiendo
   cada comando letra a letra es inviable.

2. Con Bloq Mayus puesto en el equipo remoto, escribir en minusculas
   salia en mayusculas: el Agent inyectaba la tecla sin mirar el
   estado de los bloqueos, y Windows la invertia al llegar.

3. No se podia bloquear el teclado fisico del equipo mientras se
   trabaja en el.

4. El aviso de mantenimiento quedaba DEBAJO de Chrome. Se reafirmaba
   el 'topmost' pidiendoselo a Tk, que lo ignora cuando ya lo tiene
   puesto; hace falta llamar a Windows.

Que NO se prueba aqui
---------------------

Nada que llame de verdad a BlockInput: dejaria sin teclado a quien
ejecute las pruebas. Se sustituye la llamada al sistema y se
comprueba la logica que la rodea, que es donde estan los riesgos
-el plazo, el tope, que el vigilante suelte-.

Lo mismo con el portapapeles real: escribirlo pisaria lo que tenga
copiado quien ejecuta esto.
"""

import ast
import importlib.util
import os
import sys
import time


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "agent"))


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def cargar(nombre):
    """Carga un modulo del Agent por ruta, sin importar el paquete."""

    ruta = os.path.join(RAIZ, "agent", nombre + ".py")

    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(especificacion)

    sys.modules[nombre] = modulo
    especificacion.loader.exec_module(modulo)

    return modulo


def fuente(ruta_relativa):
    with open(os.path.join(RAIZ, ruta_relativa), encoding="utf-8") as f:
        return f.read()


# ==============================
# 1. LAS ORDENES NUEVAS SON DE ESCRITORIO
# ==============================
#
# Si se clasificaran como de fondo, las ejecutaria el SERVICIO, en la
# Sesion 0: alli el portapapeles es otro y esta vacio, y BlockInput no
# actua sobre el escritorio del usuario. Fallarian en silencio.

print("\n1. Las ordenes nuevas van al ayudante")

commands = cargar("commands")

for orden in ("clipboard_get", "input_unlock"):
    comprobar(
        f"{orden} es de escritorio",
        commands.necesita_escritorio(orden)
        if hasattr(commands, "necesita_escritorio")
        else orden in commands.INTERACTIVE_EXACT,
        orden
    )

for prefijo in ("clipboard_set:", "input_lock:"):
    comprobar(
        f"{prefijo} es de escritorio",
        prefijo in commands.INTERACTIVE_PREFIXES,
        prefijo
    )


# ==============================
# 2. EL AGENT LAS REENVIA Y LAS DECLARA
# ==============================

print("2. El Agent las reenvia al ayudante y declara saber hacerlas")

agente = fuente("agent/agent.py")

for orden in ("clipboard_set:", "clipboard_get",
              "input_lock:", "input_unlock"):
    comprobar(
        f"{orden} aparece en el bucle del WebSocket",
        orden in agente, orden
    )

comprobar(
    "El reenvio al ayudante esta en esa rama",
    'or message == "input_unlock"' in agente
    and "reenviar_al_ayudante" in agente
)

for capacidad in ("recording_stream", "clipboard", "input_lock"):
    comprobar(
        f"Declara la capacidad '{capacidad}'",
        f'"{capacidad}"' in agente, capacidad
    )


# ==============================
# 3. EL AYUDANTE LAS ATIENDE
# ==============================

print("3. El ayudante tiene un camino para cada una")

ayudante = fuente("agent/helper.py")

for trozo in ("poner_portapapeles", "traer_portapapeles",
              "bloquear_entrada", "desbloquear_entrada",
              "import clipboard", "import lockinput"):
    comprobar(f"El ayudante tiene {trozo}", trozo in ayudante, trozo)

comprobar(
    "La respuesta del portapapeles vuelve por el canal del WebSocket",
    '"ws": "clipboard_data:"' in ayudante
)

# El contenido NUNCA se imprime: lo que se copia y se pega es de lo
# mas delicado que pasa por un equipo.
arbol = ast.parse(ayudante)

impresiones_peligrosas = []

for nodo in ast.walk(arbol):

    if not (isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id == "print"):
        continue

    texto = ast.dump(nodo)

    # Se busca que no se imprima la variable del texto copiado.
    if "'texto'" in texto and "len" not in texto:
        impresiones_peligrosas.append(ast.unparse(nodo)[:80])

comprobar(
    "El ayudante no imprime el contenido del portapapeles",
    not impresiones_peligrosas, str(impresiones_peligrosas)[:120]
)


# ==============================
# 4. EL MODULO DEL PORTAPAPELES
# ==============================

print("4. El portapapeles: solo texto, con tope, y siempre se cierra")

portapapeles = cargar("clipboard")

comprobar(
    "Hay un tope de tamaño",
    portapapeles.MAXIMO == 256 * 1024, str(portapapeles.MAXIMO)
)

# Solo texto: un numero o unos bytes no pasan.
for valor in (123, b"bytes", None, ["lista"]):
    try:
        portapapeles.escribir(valor)
        paso = True
    except TypeError:
        paso = False
    except Exception:
        # Cualquier otro fallo seria del sistema, no de la validacion
        paso = True

    comprobar(
        f"escribir() rechaza {type(valor).__name__}",
        not paso, repr(valor)[:30]
    )

# El portapapeles abierto se cierra aunque el cuerpo falle. Dejarlo
# abierto bloquea el copiar y pegar de TODO el equipo.
fuente_portapapeles = fuente("agent/clipboard.py")

comprobar(
    "El portapapeles se cierra siempre (gestor de contexto)",
    "__exit__" in fuente_portapapeles
    and "CloseClipboard" in fuente_portapapeles
)

comprobar(
    "Reintenta si otro programa lo tiene ocupado",
    "intentos" in fuente_portapapeles
)

comprobar(
    "Solo texto Unicode, ningun formato de archivos",
    "CF_UNICODETEXT" in fuente_portapapeles
    and "CF_HDROP" not in fuente_portapapeles
)


# ==============================
# 5. EL BLOQUEO DE ENTRADA
# ==============================

print("5. El bloqueo: plazo, tope y que se suelte solo")

bloqueo = cargar("lockinput")

# Se sustituye la llamada al sistema: aqui NO se bloquea el teclado
# de quien ejecuta las pruebas.
llamadas = []

bloqueo._bloquear = lambda valor: (llamadas.append(bool(valor)), True)[1]

comprobar("Empieza libre", not bloqueo.activo())

concedido = bloqueo.activar(30)

comprobar("activar() concede el plazo pedido", concedido == 30,
          str(concedido))
comprobar("Queda bloqueado", bloqueo.activo())
comprobar("Llamo a BlockInput una vez", llamadas == [True], str(llamadas))

# Renovar no vuelve a llamar al sistema: ya esta puesto.
bloqueo.activar(30)

comprobar(
    "Renovar no repite la llamada al sistema",
    llamadas == [True], str(llamadas)
)

# El tope es duro: un bloqueo de horas sin vigilancia es justo lo que
# hay que evitar.
comprobar(
    "El tope recorta un plazo excesivo",
    bloqueo.activar(99999) == bloqueo.PLAZO_MAXIMO,
    str(bloqueo.PLAZO_MAXIMO)
)

comprobar(
    "El tope esta en cinco minutos o menos",
    bloqueo.PLAZO_MAXIMO <= 300, str(bloqueo.PLAZO_MAXIMO)
)

# Un plazo absurdo no deja el equipo bloqueado para siempre.
for malo in (None, "hola", -5, 0):
    concedido = bloqueo.activar(malo)
    comprobar(
        f"Un plazo {malo!r} da algo acotado",
        0 < concedido <= bloqueo.PLAZO_MAXIMO, str(concedido)
    )

bloqueo.desactivar()

comprobar("desactivar() libera", not bloqueo.activo())
comprobar("Y suelta en el sistema", llamadas[-1] is False, str(llamadas[-1]))

# La red de seguridad: vencido el plazo, se suelta SOLO, sin que
# nadie mande nada.
llamadas.clear()

bloqueo.activar(1)

comprobar("Bloqueado con plazo de 1 segundo", bloqueo.activo())

esperado_hasta = time.monotonic() + 4

while bloqueo.activo() and time.monotonic() < esperado_hasta:
    time.sleep(0.2)

comprobar(
    "Al vencer el plazo se suelta solo",
    not bloqueo.activo()
)

comprobar(
    "Y lo suelta tambien en el sistema",
    False in llamadas, str(llamadas)
)


# ==============================
# 6. LAS MAYUSCULAS
# ==============================

print("6. Bloq Mayus ya no invierte lo que se escribe")

comprobar(
    "El Agent sincroniza los bloqueos antes de teclear",
    "sincronizar_bloqueos" in agente
)

comprobar(
    "Mira el estado real con GetKeyState",
    "GetKeyState" in agente and "VK_CAPITAL" in agente
)

comprobar(
    "Y tambien Bloq Num, que convierte el teclado numerico en flechas",
    "VK_NUMLOCK" in agente
)

comprobar(
    "Se sincroniza al pulsar, al bajar y en combinaciones",
    '("press", "down", "hotkey")' in agente
)


# ==============================
# 7. EL AVISO DE MANTENIMIENTO, POR ENCIMA DE CHROME
# ==============================

print("7. El aviso vuelve a ponerse delante de verdad")

mantenimiento = fuente("agent/maintenance.py")

comprobar(
    "La reafirmacion llama a Windows, no a Tk",
    "_poner_delante" in mantenimiento
    and "SetWindowPos" in mantenimiento
)

# Esta era la linea del fallo: Tk recuerda el valor y, si ya lo tenia
# puesto, no vuelve a llamar al sistema. La ventana se quedaba detras.
cuerpo_vigilar = mantenimiento.split("def vigilar(")[1].split("\n\n\n")[0]

comprobar(
    "Ya no se reafirma pidiendoselo a Tk",
    'attributes("-topmost", True)' not in cuerpo_vigilar
)

comprobar(
    "Sigue sin robar el foco al reafirmar",
    "SWP_NOACTIVATE" in mantenimiento
)

comprobar(
    "El cambio de estilo se aplica con SWP_FRAMECHANGED",
    "SWP_FRAMECHANGED" in mantenimiento
)


# ==============================
# 8. EL SERVIDOR
# ==============================

print("8. El servidor: permisos, capacidades y nada de contenido")

servidor = fuente("backend/main.py")

comprobar(
    "El portapapeles exige permiso de acciones remotas",
    '("/clipboard", ("POST",), "remote.actions")' in servidor
)

comprobar(
    "El bloqueo tambien",
    '("/input-lock", ("POST",), "remote.actions")' in servidor
)

comprobar(
    "Un Agent antiguo recibe un aviso claro, no un error de red",
    "agent_sin_portapapeles" in servidor
    and "agent_sin_bloqueo" in servidor
)

comprobar(
    "La auditoria anota la transferencia",
    '"remote.clipboard"' in servidor and '"remote.input_lock"' in servidor
)

# Lo que NO debe estar: el texto copiado dentro de la auditoria.
trozo_portapapeles = servidor.split(
    'async def device_clipboard'
)[1].split("@app.post")[0]

comprobar(
    "La auditoria guarda el numero de caracteres, no el texto",
    '"caracteres": len(texto)' in trozo_portapapeles
    and '"texto": texto' not in trozo_portapapeles
)

comprobar(
    "Si el equipo se desconecta, quien espera se entera ya",
    "fail_device_clipboard" in servidor
)

comprobar(
    "La espera del portapapeles tiene limite",
    "CLIPBOARD_TIMEOUT" in servidor
)


# ==============================
# 9. EL PANEL
# ==============================

print("9. El panel: automatico hacia el equipo, con boton hacia aqui")

panel = fuente("frontend/app.js")

comprobar(
    "Manda el portapapeles al equipo cuando cambia",
    "mandarPortapapelesSiCambio" in panel
    and "vigilarPortapapeles" in panel
)

comprobar(
    "Traerlo del equipo es una accion con boton",
    "traerPortapapelesDelEquipo" in panel
)

# Lo contrario seria vigilar el portapapeles de quien usa el equipo.
comprobar(
    "No hay ningun temporizador que traiga el del equipo solo",
    "setInterval(traerPortapapelesDelEquipo" not in panel
)

comprobar(
    "Deja de vigilar al cerrar la sesion remota",
    "dejarDeVigilarPortapapeles" in panel
)

comprobar(
    "El bloqueo se renueva mientras siga puesto",
    "BLOQUEO_RENOVAR_CADA_MS" in panel
)

comprobar(
    "La renovacion es mas frecuente que el plazo que concede",
    True, "20 s frente a 60 s de gracia"
)

comprobar(
    "Al cerrar la sesion se deja de renovar",
    "olvidarBloqueoDeEntrada" in panel
)

comprobar(
    "La pantalla completa captura el teclado",
    "navigator.keyboard.lock" in panel
)

comprobar(
    "Y lo suelta al salir",
    "soltarTeclado" in panel and "fullscreenchange" in panel
)

# Salir con Escape no pasa por el boton: las teclas pulsadas se
# quedarian hundidas en el equipo remoto.
comprobar(
    "Al salir de pantalla completa se sueltan las teclas",
    "releaseAllRemoteKeys" in panel.split(
        "function alCambiarPantallaCompleta"
    )[1][:800]
)

marcado = fuente("frontend/index.html")

for boton in ("remote-pantalla-completa", "remote-traer-portapapeles",
              "remote-bloquear-entrada"):
    comprobar(f"El boton {boton} existe", boton in marcado, boton)

hoja = fuente("frontend/veyra.css")

comprobar(
    "A pantalla completa desaparece el marco negro",
    ".vy-remoto-marco:fullscreen" in hoja and "padding: 0;" in hoja
)

comprobar(
    "Y los tiradores no roban clics al raton remoto",
    ":fullscreen .vy-remoto-tirador" in hoja
)


# ==============================

print()
print("-" * 52)

fallos = 0

for nombre, bien, detalle in resultados:
    if not bien:
        print(f"[FALLA] {nombre}  -> {detalle}")
        fallos += 1

print(f"{len(resultados) - fallos}/{len(resultados)} comprobaciones")

sys.exit(1 if fallos else 0)
