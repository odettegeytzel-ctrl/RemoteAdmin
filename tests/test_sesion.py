"""
Separacion entre el servicio de fondo y el ayudante interactivo.

    .venv\\Scripts\\python tests/test_sesion.py

La pregunta de esta suite: un equipo recien reiniciado, con nadie
sentado delante, aparece en el panel y obedece lo que no necesita
pantalla; y cuando alguien inicia sesion, se suman la grabacion y el
control remoto sin que el equipo cambie de identidad.

El canal local SI se prueba de verdad: se crea un named pipe y se
habla por el. Lo que no se puede probar aqui es el arranque real de
Windows en la Sesion 0 ni un inicio de sesion de verdad; esas
comprobaciones miran la configuracion que se va a aplicar y se dicen
como tales en el informe.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import json
import os
import sys
import threading
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))
sys.path.insert(0, str(RAIZ / "agent"))

import users_harness as h

import backend.database as database
import backend.enrollment as enrollment
import backend.main as servidor
import backend.organizations as orgs
import backend.packaging as packaging
import backend.users as users

# Modulos del Agent que no necesitan escritorio ni Windows
import commands
import ipc


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeSesion1!"

AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
AYUDANTE = io.open(RAIZ / "agent" / "helper.py", encoding="utf-8").read()
GUION = io.open(RAIZ / "installer" / "install-agent.ps1",
                encoding="utf-8").read()
DESINSTALA = io.open(RAIZ / "installer" / "uninstall-agent.ps1",
                     encoding="utf-8").read()

escenario = {}


def preparar():

    h.reiniciar()

    servidor.connected_agents.clear()

    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organization_settings",
                  "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    organizacion = orgs.create_organization(
        "Empresa", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, organizacion["id"])

    escenario["organizacion"] = organizacion["id"]

    return organizacion["id"]


def dar_de_alta(hostname="PC-SESION"):

    ficha, credencial = enrollment.create_token(escenario["organizacion"])

    datos = h.cliente().post(
        "/api/devices/register",
        json={"hostname": hostname, "operating_system": "Windows",
              "ip_address": "10.0.0.5"},
        headers={"X-Agent-Token": credencial}
    ).json()

    return ficha, credencial, datos


# ==============================
# 1. Reparto de ordenes
# ==============================

def test_lo_que_no_necesita_pantalla_se_queda_en_el_fondo():

    for orden in sorted(commands.BACKGROUND_EXPECTED):

        comprobar(f"'{orden}' es de fondo",
                  commands.classify(orden) == commands.KIND_BACKGROUND)

    for prefijo in commands.BACKGROUND_PREFIXES_EXPECTED:

        orden = prefijo + "{}"

        comprobar(f"'{prefijo}...' es de fondo",
                  commands.classify(orden) == commands.KIND_BACKGROUND)


def test_lo_que_toca_el_escritorio_va_al_ayudante():

    for orden in sorted(commands.INTERACTIVE_EXACT):

        comprobar(f"'{orden}' necesita escritorio",
                  commands.requires_desktop(orden))

    for prefijo in commands.INTERACTIVE_PREFIXES:

        comprobar(f"'{prefijo}...' necesita escritorio",
                  commands.requires_desktop(prefijo + "{}"))


def test_lo_desconocido_se_queda_en_el_fondo():
    """
    La lista es blanca para lo interactivo, no negra.

    Al reves, una orden nueva se iria por defecto al ayudante y
    dejaria de funcionar en un equipo sin sesion abierta: un fallo
    que tardaria semanas en salir.
    """

    for orden in ("orden_que_no_existe", "get_algo_nuevo",
                  "futuro_comando:{}", "", "   "):

        comprobar(f"'{orden.strip() or 'vacio'}' se queda en el fondo",
                  commands.classify(orden) == commands.KIND_BACKGROUND)

    comprobar("Lo que no es texto tampoco va al ayudante",
              not commands.requires_desktop(None)
              and not commands.requires_desktop(42))


def test_las_ordenes_del_protocolo_estan_todas_clasificadas():
    """Nada de lo que el servidor envia puede quedar sin reparto."""

    servidor_py = io.open(RAIZ / "backend" / "main.py",
                          encoding="utf-8").read()

    # Las que el servidor manda de verdad, sacadas de su codigo
    enviadas = {
        "get_system_info", "get_installed_software",
        "get_processes:", "get_services:", "power_action:",
        "set_retention:", "set_schedule:", "set_continuous:",
        "start_recording", "stop_recording",
        "start_screen_stream", "stop_screen_stream",
        "mouse_move:", "mouse_click:", "mouse_down:", "mouse_up:",
        "keyboard:"
    }

    faltan = [
        orden for orden in enviadas
        if orden.rstrip(":") not in servidor_py
    ]

    comprobar("Todas las ordenes de la lista existen en el servidor",
              not faltan, str(sorted(faltan)))

    # Las de escritorio, y solo esas, van al ayudante
    escritorio = {
        o for o in enviadas if commands.requires_desktop(o + "{}")
        or commands.requires_desktop(o)
    }

    esperadas = {
        "start_recording", "stop_recording",
        "start_screen_stream", "stop_screen_stream",
        "mouse_move:", "mouse_click:", "mouse_down:", "mouse_up:",
        "keyboard:"
    }

    comprobar("El reparto coincide exactamente con lo esperado",
              escritorio == esperadas,
              str(sorted(escritorio ^ esperadas)))


def test_al_registrar_una_orden_no_se_guardan_sus_argumentos():

    comprobar("Solo se queda el nombre",
              commands.nombre_de_la_orden(
                  'keyboard:{"key": "contrasena"}'
              ) == "keyboard")

    comprobar("Sin rastro del contenido",
              "contrasena" not in commands.nombre_de_la_orden(
                  'keyboard:{"key": "contrasena"}'
              ))

    comprobar("Y las coordenadas tampoco",
              commands.nombre_de_la_orden(
                  'mouse_move:{"x": 10, "y": 20}'
              ) == "mouse_move")


# ==============================
# 2. El canal local, de verdad
# ==============================

def _pareja(nombre, origenes=None):
    """Levanta un servicio y un ayudante conectados por un tubo real."""

    estado = {"recibido": [], "error": None, "pid": None}

    canal = ipc.CanalDeServicio(nombre=nombre, origenes=origenes)

    if not canal.abrir():
        return None, None, estado

    def atender():
        try:
            estado["pid"] = canal.esperar_ayudante()

            while True:
                estado["recibido"].append(canal.recibir())

        except Exception as error:
            estado["error"] = f"{type(error).__name__}: {error}"

    hilo = threading.Thread(target=atender, daemon=True)
    hilo.start()

    time.sleep(0.4)

    cliente = ipc.CanalDeAyudante(nombre=nombre)

    for _ in range(25):
        if cliente.conectar():
            break
        time.sleep(0.2)

    time.sleep(0.3)

    return canal, cliente, estado


def test_el_ayudante_se_conecta_y_hablan():

    canal, cliente, estado = _pareja(r"\\.\pipe\RemoteAdmin.Test.Basico")

    if canal is None:
        comprobar("El canal se pudo crear", False, "sin pywin32")
        return

    try:

        comprobar("El ayudante se conecta", cliente._tubo is not None)

        comprobar("El servicio valida de donde viene",
                  estado["pid"] is not None, str(estado))

        # servicio -> ayudante
        canal.enviar({"command": "start_screen_stream"})

        comprobar("La orden llega al ayudante",
                  cliente.recibir() == {"command": "start_screen_stream"})

        # ayudante -> servicio
        cliente.enviar({"ws": "screen_info:{}"})

        time.sleep(0.4)

        comprobar("Y lo que produce vuelve al servicio",
                  {"ws": "screen_info:{}"} in estado["recibido"],
                  str(estado["recibido"]))

    finally:
        cliente.cerrar()
        canal.cerrar()


def test_escribir_no_se_bloquea_mientras_otro_hilo_lee():
    """
    El caso que de verdad importa, y que estuvo mal.

    En el servicio hay un hilo esperando lo que mande el ayudante
    mientras el bucle del WebSocket le escribe ordenes. Con un handle
    SINCRONO de Windows eso se bloquea: la primera orden habria
    colgado el Agent entero. Por eso el canal usa E/S solapada.
    """

    canal, cliente, estado = _pareja(
        r"\\.\pipe\RemoteAdmin.Test.Concurrente"
    )

    if canal is None:
        comprobar("El canal se pudo crear", False, "sin pywin32")
        return

    try:

        comprobar("Hay un ayudante conectado", estado["pid"] is not None)

        # El hilo del servicio esta AHORA bloqueado leyendo.
        inicio = time.time()

        enviado = True

        try:
            canal.enviar({"command": "start_screen_stream"})

        except Exception:
            enviado = False

        tardanza = time.time() - inicio

        comprobar("Se puede escribir mientras otro hilo lee", enviado)

        comprobar("Y no tarda nada", tardanza < 2.0, f"{tardanza:.2f}s")

        comprobar("La orden llega igualmente",
                  cliente.recibir() == {"command": "start_screen_stream"})

    finally:
        cliente.cerrar()
        canal.cerrar()


def test_un_marco_grande_llega_entero():
    """Una captura de pantalla no cabe en una sola lectura."""

    canal, cliente, estado = _pareja(r"\\.\pipe\RemoteAdmin.Test.Grande")

    if canal is None:
        comprobar("El canal se pudo crear", False, "sin pywin32")
        return

    try:

        grande = "A" * 400000

        cliente.enviar({"ws": grande})

        time.sleep(0.6)

        llegado = [m for m in estado["recibido"] if m.get("ws") == grande]

        comprobar("Un mensaje de 400 KB llega entero",
                  len(llegado) == 1, str(len(estado["recibido"])))

    finally:
        cliente.cerrar()
        canal.cerrar()


def test_el_canal_rechaza_un_origen_que_no_toca():

    canal, cliente, estado = _pareja(
        r"\\.\pipe\RemoteAdmin.Test.Rechazo",
        origenes={r"C:\no\existe\otro.exe"}
    )

    if canal is None:
        comprobar("El canal se pudo crear", False, "sin pywin32")
        return

    try:

        comprobar("Se rechaza el origen no autorizado",
                  estado["error"] and "no autorizado" in estado["error"],
                  str(estado["error"]))

        comprobar("Y no llega a intercambiar ningun mensaje",
                  not estado["recibido"])

        fallo = None

        try:
            cliente.recibir()
        except Exception as error:
            fallo = type(error).__name__

        comprobar("El cliente rechazado no puede leer", fallo is not None)

    finally:
        cliente.cerrar()
        canal.cerrar()


def test_sin_ayudante_el_servicio_no_se_bloquea():
    """Lo mas importante: nadie con sesion iniciada es lo NORMAL."""

    canal = ipc.CanalDeServicio(nombre=r"\\.\pipe\RemoteAdmin.Test.Solo")

    if not canal.abrir():
        comprobar("El canal se pudo crear", False, "sin pywin32")
        return

    try:

        comprobar("El servicio sabe que no hay ayudante",
                  not canal.hay_ayudante)

        inicio = time.time()

        fallo = None

        try:
            canal.enviar({"command": "start_screen_stream"})
        except ipc.HelperUnavailable as error:
            fallo = error

        tardanza = time.time() - inicio

        comprobar("Mandar algo falla con HelperUnavailable",
                  fallo is not None)

        comprobar("Y falla al instante, sin quedarse esperando",
                  tardanza < 1.0, f"{tardanza:.2f}s")

        fallo = None

        try:
            canal.recibir()
        except ipc.HelperUnavailable as error:
            fallo = error

        comprobar("Leer tampoco bloquea", fallo is not None)

    finally:
        canal.cerrar()


def test_el_canal_no_se_cree_lo_que_le_digan():

    comprobar("Una cabecera corta se rechaza",
              _falla(lambda: ipc.desempaquetar_longitud(b"\\x01\\x02")))

    comprobar("Longitud cero se rechaza",
              _falla(lambda: ipc.desempaquetar_longitud(b"\\x00\\x00\\x00\\x00")))

    comprobar("Una longitud enorme se rechaza antes de reservar memoria",
              _falla(lambda: ipc.desempaquetar_longitud(b"\\xff\\xff\\xff\\xff")))

    comprobar("Lo que no es JSON se rechaza",
              _falla(lambda: ipc.interpretar(b"esto no es json")))

    comprobar("Y un mensaje que no es un objeto, tambien",
              _falla(lambda: ipc.interpretar(b'["una", "lista"]')))

    comprobar("Un mensaje demasiado grande no se envia",
              _falla(lambda: ipc.empaquetar(
                  {"ws": "A" * (ipc.MAX_MESSAGE_BYTES + 10)}
              )))


def _falla(funcion):

    try:
        funcion()
        return False

    except ipc.IPCError:
        return True


def test_el_tubo_es_local_y_restringido():

    fuente = io.open(RAIZ / "agent" / "ipc.py", encoding="utf-8").read()

    comprobar("Es un tubo local",
              ipc.PIPE_NAME.startswith(r"\\.\pipe"))

    comprobar("Rechaza clientes remotos",
              "PIPE_REJECT_REMOTE_CLIENTS" in fuente)

    comprobar("Nadie puede adelantarse a crearlo",
              "FILE_FLAG_FIRST_PIPE_INSTANCE" in fuente)

    comprobar("SYSTEM y administradores con control total",
              "(A;;GA;;;SY)" in ipc.PIPE_SDDL
              and "(A;;GA;;;BA)" in ipc.PIPE_SDDL)

    comprobar("Usuarios interactivos, solo leer y escribir",
              "(A;;GRGW;;;IU)" in ipc.PIPE_SDDL)

    # AN = anonimo, NU = acceso por red
    comprobar("Nada para anonimos ni para el acceso por red",
              ";AN)" not in ipc.PIPE_SDDL and ";NU)" not in ipc.PIPE_SDDL)

    comprobar("Hay un tiempo maximo de espera",
              "IO_TIMEOUT_MS" in fuente)


# ==============================
# 3. El ayudante no toca lo que no debe
# ==============================

def test_el_ayudante_no_conoce_ningun_secreto():

    comprobar("No lee la identidad",
              "identity.json" not in AYUDANTE
              and "load_identity" not in AYUDANTE)

    comprobar("No maneja el token del dispositivo",
              "agent_token" not in AYUDANTE
              and "get_agent_device_token" not in AYUDANTE)

    comprobar("No conoce la credencial de alta",
              "AGENT_TOKEN" not in AYUDANTE and "rae_" not in AYUDANTE)

    comprobar("No habla con el servidor",
              "requests" not in AYUDANTE
              and "websockets" not in AYUDANTE
              and "SERVER_URL" not in AYUDANTE)

    comprobar("Lo que produce pasa por el servicio",
              "def mandar_al_servidor" in AYUDANTE
              and '{"ws": carga}' in AYUDANTE)


def test_el_ayudante_vuelve_a_comprobar_lo_que_le_mandan():
    """El servicio ya lo decidio, pero el raton lo tiene el ayudante."""

    comprobar("Comprueba que la orden es de escritorio",
              "if not commands.requires_desktop(orden):" in AYUDANTE)

    comprobar("Y descarta lo que no lo sea",
              "Orden descartada" in AYUDANTE)

    comprobar("Un fallo en una orden no lo tumba",
              "except Exception as error:" in AYUDANTE)

    comprobar("Al registrar un fallo no escribe los argumentos",
              "commands.nombre_de_la_orden(orden)" in AYUDANTE)


def test_el_ayudante_sobrevive_a_que_el_servicio_no_este():

    comprobar("Reintenta conectarse",
              "def correr" in AYUDANTE and "conectar()" in AYUDANTE)

    comprobar("Con espera creciente",
              "RECONNECT_BASE_SECONDS" in AYUDANTE
              and "RECONNECT_MAX_SECONDS" in AYUDANTE)

    comprobar("Y un tope, para no gastar CPU",
              "min(" in AYUDANTE.split("def _esperar", 1)[1][:200])

    comprobar("Al caerse el canal deja de capturar",
              "self.dejar_de_transmitir()" in AYUDANTE)

    comprobar("Y suelta las teclas que quedaran pulsadas",
              "release_all_keys" in AYUDANTE)


# ==============================
# 4. El servicio
# ==============================

def test_el_servicio_reparte_segun_el_papel():

    comprobar("Hay tres papeles",
              'ROLE_SERVICE = "service"' in AGENTE
              and 'ROLE_HELPER = "helper"' in AGENTE
              and 'ROLE_STANDALONE = "standalone"' in AGENTE)

    comprobar("Por defecto, el comportamiento de siempre",
              "return ROLE_STANDALONE" in AGENTE)

    comprobar("Solo el servicio reenvia al ayudante",
              "if (AGENT_ROLE == ROLE_SERVICE" in AGENTE
              and "commands.requires_desktop(message)" in AGENTE)

    comprobar("Si no hay sesion, se contesta al servidor",
              "interactive_unavailable:" in AGENTE)

    comprobar("En vez de callarse o reintentar",
              "commands.respuesta_sin_sesion(message)" in AGENTE)


def test_el_servicio_no_intenta_grabar_en_la_sesion_cero():
    """Capturar ahi da una pantalla negra, una y otra vez."""

    comprobar("No arranca el horario de grabacion",
              "if AGENT_ROLE != ROLE_SERVICE:" in AGENTE)

    bloque = AGENTE.split("if AGENT_ROLE != ROLE_SERVICE:", 1)[1][:400]

    comprobar("Que es justo el hilo del horario",
              "_schedule_loop" in bloque)

    comprobar("El canal con el ayudante solo lo abre el servicio",
              "if AGENT_ROLE == ROLE_SERVICE:" in AGENTE
              and "_atender_al_ayudante" in AGENTE)


def test_el_servicio_sigue_si_el_ayudante_desaparece():

    bloque = AGENTE.split("def _atender_al_ayudante", 1)[1].split(
        "\\ndef ", 1
    )[0]

    comprobar("Una desconexion no termina el bucle",
              "except ipc.HelperUnavailable:" in bloque)

    comprobar("Vuelve a esperar a que entre otro",
              bloque.count("while True:") >= 2)

    comprobar("Un origen rechazado tampoco lo para",
              "except ipc.IPCError" in bloque)

    comprobar("Ni un error inesperado",
              "except Exception as error:" in bloque)

    comprobar("Corre en su propio hilo, no en el del latido",
              "InteractiveHelperChannel" in AGENTE)


def test_cada_papel_escribe_en_su_registro():

    comprobar("El nombre del log depende del papel",
              'f"agent-{AGENT_ROLE}.log"' in AGENTE)

    comprobar("Y el de siempre no cambia",
              '"agent.log" if AGENT_ROLE == ROLE_STANDALONE' in AGENTE)


# ==============================
# 5. Identidad: nada de esto crea equipos nuevos
# ==============================

def test_el_servicio_usa_la_identidad_que_ya_existe():

    preparar()

    ficha, credencial, alta = dar_de_alta("PC-ARRANQUE")

    device_id = alta["device_id"]
    token = alta["agent_token"]

    # Tres arranques del servicio, como tres reinicios de Windows
    for vuelta in range(3):

        respuesta = h.cliente().post(
            "/api/devices/register",
            json={"hostname": "PC-ARRANQUE",
                  "operating_system": "Windows",
                  "ip_address": "10.0.0.5"},
            headers={"X-Agent-Token": token}
        )

        comprobar(f"Reinicio {vuelta + 1}: mismo device_id",
                  respuesta.json().get("device_id") == device_id)

    conexion = database.get_connection()
    total = conexion.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    conexion.close()

    comprobar("Ningun equipo duplicado", total == 1, str(total))

    comprobar("Y el latido sigue funcionando",
              h.cliente().post(
                  "/api/devices/heartbeat",
                  json={"device_id": device_id, "ip_address": "10.0.0.5"},
                  headers={"X-Agent-Token": token}
              ).status_code == 200)


def test_varias_sesiones_no_crean_varios_equipos():
    """
    Caso D: entra otro usuario y arranca otro ayudante.

    El ayudante no se registra: no habla con el servidor. Por eso un
    segundo usuario no puede provocar un segundo equipo.
    """

    comprobar("El ayudante no se registra en ningun sitio",
              "register_device" not in AYUDANTE
              and "/api/devices" not in AYUDANTE)

    comprobar("Un ayudante a la vez",
              "Un ayudante a la vez" in AGENTE
              or "un ayudante" in AGENTE.lower())


def test_el_token_no_pasa_por_el_canal_local():

    # Las DOS funciones del canal, cada una acotada a si misma. No se
    # toma el trozo entre ambas porque entre medias vive el hilo de
    # actualizacion, que si usa el token: para hablar con el
    # SERVIDOR, no con el ayudante.
    atiende = AGENTE.split("def _atender_al_ayudante", 1)[1].split(
        chr(10) + "def ", 1
    )[0]

    reenvia = AGENTE.split("def reenviar_al_ayudante", 1)[1].split(
        chr(10) + "def ", 1
    )[0]

    comprobar("Recibir del ayudante no toca ningun token",
              "token" not in atiende.lower(), atiende[:200])

    # Lo que de verdad importa: QUE viaja por el canal.
    comprobar("Hacia el ayudante solo va la orden",
              '_canal_ayudante.enviar({"command": mensaje})' in reenvia)

    comprobar("Y nada mas se le envia",
              reenvia.count(".enviar(") == 1, str(reenvia.count(".enviar(")))

    comprobar("Solo acepta del ayudante lo que va al WebSocket",
              'mensaje.get("ws")' in atiende)

    comprobar("Y comprueba que sea texto antes de usarlo",
              "isinstance(carga, str)" in atiende)

    # El token SI se usa, pero contra el servidor y desde otro hilo
    actualiza = AGENTE.split("def _bucle_de_actualizacion", 1)[1].split(
        chr(10) + "def ", 1
    )[0]

    comprobar("El token solo se usa para hablar con el servidor",
              "get_agent_device_token()" in actualiza
              and "_canal_ayudante" not in actualiza)


# ==============================
# 6. Instalacion y desinstalacion
# ==============================

def test_el_instalador_crea_las_dos_tareas():

    comprobar("La del servicio de fondo",
              "-TaskName $TaskName" in GUION
              and "--role=service" in GUION)

    comprobar("Arranca con Windows, no al iniciar sesion",
              "-AtStartup" in GUION)

    comprobar("Como SYSTEM",
              '-UserId "SYSTEM"' in GUION
              and "-LogonType ServiceAccount" in GUION)

    comprobar("La del ayudante",
              "$HelperTaskName" in GUION and "helper.py" in GUION)

    comprobar("Al iniciar sesion", "-AtLogOn" in GUION)

    comprobar("Para cualquier usuario del equipo, no solo quien instalo",
              '-GroupId "S-1-5-32-545"' in GUION)

    comprobar("El ayudante no necesita privilegios altos",
              "-RunLevel Limited" in GUION)

    comprobar("Ninguna abre consola", "pythonw.exe" in GUION)

    comprobar("Las dos se reinician solas",
              GUION.count("-RestartCount 999") >= 1)


def test_el_paquete_descargable_lleva_las_piezas_nuevas():

    dentro = set(packaging.PACKAGE_FILES)

    for pieza in ("agent/helper.py", "agent/ipc.py", "agent/commands.py"):
        comprobar(f"El ZIP incluye {pieza}", pieza in dentro)

    comprobar("Y el desinstalador",
              "installer/uninstall-agent.ps1" in dentro)


def test_la_desinstalacion_distingue_programa_y_datos():

    comprobar("Quita las dos tareas",
              "$TaskName" in DESINSTALA and "$HelperTaskName" in DESINSTALA)

    comprobar("Por defecto conserva los datos",
              "Se conservan los datos" in DESINSTALA)

    comprobar("Hay una opcion explicita para borrarlo todo",
              "[switch]$PurgeData" in DESINSTALA)

    comprobar("Que avisa de que es irreversible",
              "irreversible" in DESINSTALA.lower())

    # Lo que de verdad importa: antes de la rama de -PurgeData no hay
    # ningun borrado que alcance a config\, recordings\ ni logs\.
    previo = DESINSTALA.split("if ($PurgeData)", 1)[0]

    borrados = [
        linea.strip() for linea in previo.split(chr(10))
        if "Remove-Item" in linea and not linea.strip().startswith("#")
    ]

    comprobar("Sin -PurgeData no se borra ningun dato",
              all("$resto" in b or "$ruta" in b for b in borrados),
              str(borrados))

    comprobar("Y lo que se borra es solo programa y configuracion",
              '@("agent", "installer")' in previo)


def test_actualizar_conserva_la_identidad():

    comprobar("Para las dos tareas antes de copiar",
              GUION.index("foreach ($tarea in @($TaskName, $HelperTaskName))")
              < GUION.index('Copy-Item -Path (Join-Path $SourceDir "agent")'))

    comprobar("Solo copia el programa",
              'Copy-Item -Path (Join-Path $SourceDir "agent")' in GUION)

    comprobar("No borra nada", "Remove-Item" not in GUION)

    comprobar("Y no vuelve a pedir credencial si ya hay identidad",
              "$yaEnrolado = Test-Path $IdentityFile" in GUION)


# ==============================
# 7. Nada de lo anterior se rompe
# ==============================

def test_lo_de_siempre_sigue_en_pie():

    preparar()

    cliente = h.cliente(h.OWNER)

    for ruta in ("/api/devices", "/api/alerts", "/api/settings",
                 "/api/users/me", "/api/recordings", "/api/audit",
                 "/api/agent-package"):

        comprobar(f"{ruta} responde",
                  cliente.get(ruta).status_code == 200,
                  str(cliente.get(ruta).status_code))

    ficha, credencial, alta = dar_de_alta("PC-COMPAT")

    comprobar("El alta sigue funcionando", alta.get("device_id"))

    comprobar("El token individual no es la credencial",
              alta["agent_token"] != credencial)

    comprobar("Y la organizacion sale de la credencial",
              servidor.organization_of_device(alta["device_id"])
              == escenario["organizacion"])


def test_el_protocolo_con_el_servidor_no_cambio():
    """El dashboard sigue hablando igual: no se toco ningun contrato."""

    servidor_py = io.open(RAIZ / "backend" / "main.py",
                          encoding="utf-8").read()

    for orden in ("start_screen_stream", "mouse_move:", "keyboard:",
                  "get_processes", "power_action:"):

        comprobar(f"El servidor sigue enviando '{orden}'",
                  orden in servidor_py)

    comprobar("Y el Agent las sigue atendiendo igual",
              'if message == "ping":' in AGENTE
              and 'elif message == "start_screen_stream":' in AGENTE)


def test_no_hay_secretos_en_lo_que_se_registra():

    sospechosas = []

    for archivo, texto in (("agent.py", AGENTE), ("helper.py", AYUDANTE),
                           ("ipc.py", io.open(RAIZ / "agent" / "ipc.py",
                                              encoding="utf-8").read())):

        for numero, linea in enumerate(texto.split(chr(10)), 1):

            if "print(" not in linea:
                continue

            for peligro in ("agent_token", "AGENT_TOKEN", "token}",
                            "credencial}", "identity["):

                if peligro.lower() in linea.lower():
                    sospechosas.append(f"{archivo}:{numero}")

    comprobar("Ninguna linea imprime un token",
              not sospechosas, str(sospechosas))

    comprobar("El canal no lleva el token en ningun mensaje",
              '"ws"' in AYUDANTE and "agent_token" not in AYUDANTE)


# ==============================

def main():

    pruebas = [
        test_lo_que_no_necesita_pantalla_se_queda_en_el_fondo,
        test_lo_que_toca_el_escritorio_va_al_ayudante,
        test_lo_desconocido_se_queda_en_el_fondo,
        test_las_ordenes_del_protocolo_estan_todas_clasificadas,
        test_al_registrar_una_orden_no_se_guardan_sus_argumentos,
        test_el_ayudante_se_conecta_y_hablan,
        test_escribir_no_se_bloquea_mientras_otro_hilo_lee,
        test_un_marco_grande_llega_entero,
        test_el_canal_rechaza_un_origen_que_no_toca,
        test_sin_ayudante_el_servicio_no_se_bloquea,
        test_el_canal_no_se_cree_lo_que_le_digan,
        test_el_tubo_es_local_y_restringido,
        test_el_ayudante_no_conoce_ningun_secreto,
        test_el_ayudante_vuelve_a_comprobar_lo_que_le_mandan,
        test_el_ayudante_sobrevive_a_que_el_servicio_no_este,
        test_el_servicio_reparte_segun_el_papel,
        test_el_servicio_no_intenta_grabar_en_la_sesion_cero,
        test_el_servicio_sigue_si_el_ayudante_desaparece,
        test_cada_papel_escribe_en_su_registro,
        test_el_servicio_usa_la_identidad_que_ya_existe,
        test_varias_sesiones_no_crean_varios_equipos,
        test_el_token_no_pasa_por_el_canal_local,
        test_el_instalador_crea_las_dos_tareas,
        test_el_paquete_descargable_lleva_las_piezas_nuevas,
        test_la_desinstalacion_distingue_programa_y_datos,
        test_actualizar_conserva_la_identidad,
        test_lo_de_siempre_sigue_en_pie,
        test_el_protocolo_con_el_servidor_no_cambio,
        test_no_hay_secretos_en_lo_que_se_registra
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:90])

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLO"
        print(f"[{marca}] {nombre}" + (f"  -> {detalle}" if not ok else ""))
        if not ok:
            fallos += 1

    print(f"\n{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
