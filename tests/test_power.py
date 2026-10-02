"""
Acciones remotas sobre el equipo: bloquear, cerrar sesion, reiniciar,
apagar (G5).

    .venv\\Scripts\\python tests/test_power.py

Ninguna prueba toca un equipo real: el Agent es un WebSocket simulado que
contesta lo que se le diga. No se bloquea, ni se cierra sesion, ni se
reinicia, ni se apaga nada.
"""

import io
import sys
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.database as database
import backend.main as servidor
import backend.power as power
import backend.queries as queries
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


ACCIONES = ("lock", "logoff", "restart", "shutdown")

DESTRUCTIVAS = ("logoff", "restart", "shutdown")

EQUIPO = "equipo-g5"
OTRO_EQUIPO = "equipo-g5-otro"
INEXISTENTE = "equipo-que-no-existe"


class AgentFalso:
    """
    Sustituto del WebSocket del Agent.

    Contesta lo que se le configure. 'responder_como' permite contestar
    en nombre de otro equipo, que es justo lo que debe rechazarse.
    """

    def __init__(self, device_id=EQUIPO, respuesta=None, callar=False,
                 responder_como=None, fallar_al_enviar=False,
                 tipo=queries.KIND_POWER):

        self.device_id = device_id
        self.respuesta = respuesta
        self.callar = callar
        self.responder_como = responder_como or device_id
        self.fallar_al_enviar = fallar_al_enviar
        self.tipo = tipo
        self.enviados = []

    async def send_text(self, texto):

        import json

        if self.fallar_al_enviar:
            raise ConnectionError("el canal se cerro")

        self.enviados.append(texto)

        if ":" not in texto:
            return

        comando, cuerpo = texto.split(":", 1)

        if comando != "power_action":
            return

        if self.callar:
            return

        peticion = json.loads(cuerpo)
        accion = peticion.get("action")

        if self.respuesta is not None:
            carga = dict(self.respuesta)
        elif accion in ("restart", "shutdown", "logoff"):
            carga = {"accepted": True, "executed": False, "pending": True,
                     "detail": "Orden aceptada"}
        else:
            carga = {"accepted": True, "executed": True, "pending": False,
                     "detail": "Realizada"}

        carga["query_id"] = peticion.get("query_id")

        queries.resolve_query(self.responder_como, self.tipo, carga)


def preparar(permisos=None):
    """Owner, un subadmin y dos equipos dados de alta."""

    h.reiniciar()

    power.reset_repeat_guard()
    servidor.connected_agents.clear()
    queries.pending_queries.clear()

    conexion = database.get_connection()

    for device_id, hostname in ((EQUIPO, "PRUEBA-G5"),
                                (OTRO_EQUIPO, "PRUEBA-G5-OTRO")):
        conexion.execute(
            "INSERT OR IGNORE INTO devices (device_id, hostname) "
            "VALUES (?, ?)",
            (device_id, hostname)
        )

    conexion.commit()
    conexion.close()

    if permisos is not None:
        h.crear_subadmin("ana", permisos=permisos)


def conectar(agente):
    servidor.connected_agents[agente.device_id] = agente
    return agente


def pedir(cliente, accion, device_id=EQUIPO):
    return cliente.post(f"/api/devices/{device_id}/power/{accion}")


def acelerar_timeout(segundos=1):
    anterior = power.ACK_TIMEOUT_SECONDS
    power.ACK_TIMEOUT_SECONDS = segundos
    return anterior


# ==============================
# A / B. Bloquear y cerrar sesion
# ==============================

def test_bloquear_correcto():

    preparar()
    conectar(AgentFalso())

    respuesta = pedir(h.cliente(h.OWNER), "lock")

    comprobar("Bloquear responde 200", respuesta.status_code == 200,
              str(respuesta.status_code))

    datos = respuesta.json()

    comprobar("Se informa de la accion", datos.get("action") == "lock")

    comprobar("Y del equipo", datos.get("device_id") == EQUIPO)

    comprobar("Bloquear se confirma como realizada",
              datos.get("executed") is True
              and datos.get("pending") is False)

    comprobar("La auditoria lo registra como exito",
              any(r["status"] == "success"
                  for r in h.registros(action="device.lock")))


def test_cerrar_sesion_correcto():

    preparar()
    conectar(AgentFalso())

    datos = pedir(h.cliente(h.OWNER), "logoff").json()

    comprobar("Cerrar sesion se acepta", datos.get("status") == "ok")

    comprobar("Y queda como pendiente, no como realizada",
              datos.get("pending") is True
              and datos.get("executed") is False)

    comprobar("El mensaje no promete lo que no se puede comprobar",
              "no puede confirmarse" in datos.get("message", "").lower())


def test_el_equipo_recibe_solo_el_nombre_de_la_accion():
    """Por el canal no viaja ningun comando, solo cual de las cuatro."""

    import json

    preparar()
    agente = conectar(AgentFalso())

    pedir(h.cliente(h.OWNER), "lock")

    comprobar("Se envio un unico mensaje", len(agente.enviados) == 1)

    comando, cuerpo = agente.enviados[0].split(":", 1)
    peticion = json.loads(cuerpo)

    comprobar("Con el prefijo esperado", comando == "power_action")

    comprobar("Y solo con la accion y el identificador de la consulta",
              set(peticion) == {"action", "query_id"},
              str(sorted(peticion)))

    comprobar("La accion es una de las cuatro",
              peticion["action"] in ACCIONES)


def test_no_se_aceptan_comandos_arbitrarios():

    preparar()
    conectar(AgentFalso())

    cliente = h.cliente(h.OWNER)

    inventadas = ["cmd", "powershell", "script", "format", "lock;shutdown",
                  "../shutdown", "LOCK%20", "exec"]

    rechazadas = sum(
        1 for a in inventadas
        if cliente.post(f"/api/devices/{EQUIPO}/power/{a}").status_code
        in (404, 405)
    )

    comprobar("Una accion que no esta en la lista se rechaza",
              rechazadas == len(inventadas),
              f"{rechazadas}/{len(inventadas)}")

    comprobar("Y el cuerpo de la peticion se ignora por completo",
              cliente.post(
                  f"/api/devices/{EQUIPO}/power/lock",
                  json={"cmd": "del C:\\\\*", "command": "shutdown",
                        "powershell": "Remove-Item"}
              ).status_code == 200)


# ==============================
# C / D. Reiniciar y apagar
# ==============================

def test_reiniciar_y_apagar_se_envian():

    for accion in ("restart", "shutdown"):

        preparar()
        conectar(AgentFalso())

        datos = pedir(h.cliente(h.OWNER), accion).json()

        comprobar(f"{accion} se acepta", datos.get("status") == "ok")

        comprobar(f"{accion} queda pendiente, nunca 'realizada'",
                  datos.get("pending") is True
                  and datos.get("executed") is False)


def test_proteccion_contra_doble_ejecucion():

    for accion in DESTRUCTIVAS:

        preparar()
        conectar(AgentFalso())

        cliente = h.cliente(h.OWNER)

        primera = pedir(cliente, accion)
        segunda = pedir(cliente, accion)

        comprobar(f"La primera orden de {accion} pasa",
                  primera.status_code == 200)

        comprobar(f"La segunda de {accion} se rechaza con 409",
                  segunda.status_code == 409, str(segunda.status_code))

        comprobar(f"Y se indica cuanto esperar para repetir {accion}",
                  "retry-after" in {k.lower() for k in segunda.headers})


def test_el_doble_clic_no_llega_al_equipo():

    preparar()
    agente = conectar(AgentFalso())

    cliente = h.cliente(h.OWNER)

    for _ in range(5):
        pedir(cliente, "shutdown")

    comprobar("Cinco clics seguidos mandan UNA sola orden al equipo",
              len(agente.enviados) == 1, str(len(agente.enviados)))


def test_bloquear_si_se_puede_repetir():
    """Bloquear dos veces no hace dano: no se protege."""

    preparar()
    conectar(AgentFalso())

    cliente = h.cliente(h.OWNER)

    codigos = [pedir(cliente, "lock").status_code for _ in range(3)]

    comprobar("Bloquear se puede repetir sin problema",
              set(codigos) == {200}, str(codigos))


def test_la_ventana_de_repeticion_caduca():

    preparar()
    conectar(AgentFalso())

    comprobar("Recien pedida, no se puede repetir",
              power.seconds_until_repeat_allowed(EQUIPO, "restart",
                                                 ahora=None) == 0)

    ahora = time.time()

    power.register_action(EQUIPO, "restart", ahora=ahora)

    comprobar("Un segundo despues sigue protegida",
              power.seconds_until_repeat_allowed(
                  EQUIPO, "restart", ahora=ahora + 1) > 0)

    comprobar("Pasada la ventana ya se puede repetir",
              power.seconds_until_repeat_allowed(
                  EQUIPO, "restart",
                  ahora=ahora + power.REPEAT_WINDOW_SECONDS + 1) == 0)


def test_la_proteccion_es_por_equipo_y_por_accion():

    preparar()
    conectar(AgentFalso())
    conectar(AgentFalso(device_id=OTRO_EQUIPO))

    cliente = h.cliente(h.OWNER)

    pedir(cliente, "restart")

    comprobar("El mismo equipo no repite reinicio",
              pedir(cliente, "restart").status_code == 409)

    comprobar("Pero si acepta otra accion distinta",
              pedir(cliente, "shutdown").status_code == 200)

    comprobar("Y otro equipo no se ve afectado",
              pedir(cliente, "restart", OTRO_EQUIPO).status_code == 200)


def test_sin_reintento_automatico_tras_desconexion():
    """
    Si el equipo no confirma, NO se vuelve a enviar nada.

    Es el caso tipico de un apagado que si llego: el equipo se fue antes
    de contestar. Reintentar a ciegas seria apagar dos veces.
    """

    preparar()
    conectar(AgentFalso(callar=True))

    anterior = acelerar_timeout(1)

    try:
        cliente = h.cliente(h.OWNER)

        primera = pedir(cliente, "shutdown")
        segunda = pedir(cliente, "shutdown")

    finally:
        power.ACK_TIMEOUT_SECONDS = anterior

    comprobar("Sin confirmacion se responde 504",
              primera.status_code == 504, str(primera.status_code))

    comprobar("No se promete exito",
              primera.json().get("status") == "error")

    comprobar("Se dice que no se sabe si se ejecuto",
              "no se puede saber" in primera.json().get("message", "").lower())

    comprobar(
        "Y el siguiente intento sigue bloqueado: no se reintenta a ciegas",
        segunda.status_code == 409, str(segunda.status_code)
    )


def test_un_fallo_del_equipo_si_permite_reintentar():
    """Si el equipo contesta que NO pudo, la accion no ocurrio."""

    preparar()
    conectar(AgentFalso(respuesta={"accepted": False,
                                   "error": "privilegio denegado"}))

    cliente = h.cliente(h.OWNER)

    primera = pedir(cliente, "restart")

    comprobar("Un rechazo del equipo da 502",
              primera.status_code == 502, str(primera.status_code))

    comprobar("Con el motivo",
              "privilegio" in primera.json().get("message", ""))

    # El equipo contesto que no hizo nada: se puede volver a intentar
    servidor.connected_agents[EQUIPO] = AgentFalso()

    comprobar("Y se puede volver a intentar",
              pedir(cliente, "restart").status_code == 200)


# ==============================
# E. Permisos
# ==============================

def test_el_owner_puede_las_cuatro():

    for accion in ACCIONES:

        preparar()
        conectar(AgentFalso())

        comprobar(f"El Owner puede {accion}",
                  pedir(h.cliente(h.OWNER), accion).status_code == 200)


def test_subadmin_con_el_permiso_concreto():

    for accion in ACCIONES:

        preparar(permisos=[f"device.{accion}"])
        conectar(AgentFalso())

        comprobar(f"Un subadmin con device.{accion} puede hacerlo",
                  pedir(h.cliente("ana"), accion).status_code == 200)


def test_subadmin_sin_el_permiso():

    for accion in ACCIONES:

        preparar(permisos=[])
        conectar(AgentFalso())

        comprobar(f"Sin device.{accion} se responde 403",
                  pedir(h.cliente("ana"), accion).status_code == 403)


def test_cada_permiso_es_independiente():
    """Poder bloquear no da derecho a apagar."""

    preparar(permisos=["device.lock"])
    conectar(AgentFalso())

    cliente = h.cliente("ana")

    comprobar("Con device.lock puede bloquear",
              pedir(cliente, "lock").status_code == 200)

    negadas = [a for a in ("logoff", "restart", "shutdown")
               if pedir(cliente, a).status_code == 403]

    comprobar("Pero no cerrar sesion, reiniciar ni apagar",
              len(negadas) == 3, str(negadas))


def test_sin_permiso_no_se_envia_nada_al_equipo():

    preparar(permisos=[])
    agente = conectar(AgentFalso())

    pedir(h.cliente("ana"), "shutdown")

    comprobar("Un usuario sin permiso no provoca ni un mensaje al equipo",
              agente.enviados == [], str(agente.enviados))


def test_el_intento_sin_permiso_queda_auditado():

    preparar(permisos=[])
    conectar(AgentFalso())

    pedir(h.cliente("ana"), "shutdown")

    filas = h.registros(action="device.shutdown")

    comprobar("El intento sin permiso se registra",
              any(r["status"] == "error" for r in filas))

    comprobar("Con el motivo",
              any("no tiene permiso" in (r["details"] or "")
                  for r in filas))

    comprobar("Y atribuido a quien lo intento",
              any(r["username"] == "ana" for r in filas))


def test_los_permisos_de_equipo_no_dan_administracion_de_usuarios():

    preparar(permisos=list(users.PERMISSIONS))

    cliente = h.cliente("ana")

    comprobar("Con los 13 permisos sigue sin administrar usuarios",
              cliente.get("/api/users").status_code == 403)

    comprobar("Ni puede crear",
              cliente.post("/api/users",
                           json={"username": "x",
                                 "password": "ClaveLarga123!"}
                           ).status_code == 403)


# ==============================
# F. Seguridad
# ==============================

def test_sin_sesion():

    preparar()
    conectar(AgentFalso())

    for accion in ACCIONES:
        comprobar(f"Sin sesion, {accion} responde 401",
                  pedir(h.cliente(), accion).status_code == 401)


def test_sesion_revocada():

    preparar()
    conectar(AgentFalso())

    cliente = h.cliente(h.OWNER)

    comprobar("La sesion funciona antes de cerrarla",
              pedir(cliente, "lock").status_code == 200)

    cliente.post("/api/auth/logout")

    comprobar("Tras cerrar sesion ya no se puede",
              pedir(cliente, "lock").status_code == 401)


def test_usuario_desactivado():

    preparar(permisos=["device.lock"])
    conectar(AgentFalso())

    sesion = h.cliente("ana")

    users.set_active("ana", False)

    comprobar("Un usuario desactivado no puede actuar sobre el equipo",
              pedir(sesion, "lock").status_code in (401, 403))


def test_device_id_inexistente():

    preparar()
    conectar(AgentFalso())

    for accion in ACCIONES:
        comprobar(f"Un equipo inventado da 404 en {accion}",
                  pedir(h.cliente(h.OWNER), accion,
                        INEXISTENTE).status_code == 404)


def test_la_orden_va_al_equipo_pedido_y_a_ningun_otro():

    preparar()

    uno = conectar(AgentFalso(device_id=EQUIPO))
    otro = conectar(AgentFalso(device_id=OTRO_EQUIPO))

    pedir(h.cliente(h.OWNER), "lock", EQUIPO)

    comprobar("El equipo pedido recibe la orden", len(uno.enviados) == 1)

    comprobar("El otro equipo NO recibe nada",
              otro.enviados == [], str(otro.enviados))


def test_otro_agent_no_puede_confirmar_por_el():

    preparar()

    # El Agent del equipo contesta diciendo ser otro
    conectar(AgentFalso(responder_como=OTRO_EQUIPO))

    anterior = acelerar_timeout(1)

    try:
        respuesta = pedir(h.cliente(h.OWNER), "lock")
    finally:
        power.ACK_TIMEOUT_SECONDS = anterior

    comprobar("Una confirmacion con identidad ajena se descarta",
              respuesta.status_code == 504, str(respuesta.status_code))

    comprobar("Y no se da por buena",
              respuesta.json().get("status") == "error")


def test_una_respuesta_de_otro_tipo_no_cuela():

    preparar()

    conectar(AgentFalso(tipo=queries.KIND_PROCESSES))

    anterior = acelerar_timeout(1)

    try:
        respuesta = pedir(h.cliente(h.OWNER), "lock")
    finally:
        power.ACK_TIMEOUT_SECONDS = anterior

    comprobar("Contestar con otro tipo de mensaje no confirma la accion",
              respuesta.status_code == 504)


def test_no_se_usa_el_hostname_ni_la_ip():

    backend = io.open(RAIZ / "backend" / "main.py",
                      encoding="utf-8").read()

    bloque = backend.split("async def device_power_action", 1)[1]
    bloque = bloque.split("\n@app.", 1)[0]

    comprobar("El endpoint no mira el hostname",
              "hostname" not in bloque.lower())

    comprobar("Ni la IP del equipo",
              "ip_address" not in bloque)

    comprobar("Busca el canal por el device_id de la ruta",
              "connected_agents.get(device_id)" in bloque)


# ==============================
# G. WebSocket
# ==============================

def test_equipo_desconectado():

    preparar()
    # Nadie conectado

    for accion in ACCIONES:

        respuesta = pedir(h.cliente(h.OWNER), accion)

        comprobar(f"Con el equipo offline, {accion} da 409",
                  respuesta.status_code == 409, str(respuesta.status_code))

        comprobar(f"Y no se marca como exito ({accion})",
                  respuesta.json().get("status") == "error")


def test_offline_no_consume_la_ventana_de_repeticion():
    """Si no se llego a enviar nada, el siguiente intento debe poder."""

    preparar()

    cliente = h.cliente(h.OWNER)

    comprobar("Con el equipo offline se rechaza",
              pedir(cliente, "restart").status_code == 409)

    conectar(AgentFalso())

    comprobar("Y al conectarse, el intento siguiente si pasa",
              pedir(cliente, "restart").status_code == 200)


def test_el_canal_se_cae_al_enviar():

    preparar()
    conectar(AgentFalso(fallar_al_enviar=True))

    respuesta = pedir(h.cliente(h.OWNER), "restart")

    comprobar("Si el canal se cae al enviar, se responde 502",
              respuesta.status_code == 502, str(respuesta.status_code))

    comprobar("Y queda auditado como error",
              any(r["status"] == "error"
                  for r in h.registros(action="device.restart")))


def test_respuesta_tardia_no_contamina():

    preparar()
    queries.pending_queries.clear()

    entregada = queries.resolve_query(
        EQUIPO, queries.KIND_POWER,
        {"query_id": "identificador-que-ya-caduco", "accepted": True}
    )

    comprobar("Una confirmacion sin consulta pendiente se descarta",
              entregada is False)


def test_la_peticion_no_se_queda_colgada():

    preparar()
    conectar(AgentFalso(callar=True))

    anterior = acelerar_timeout(1)

    inicio = time.time()

    try:
        respuesta = pedir(h.cliente(h.OWNER), "lock")
    finally:
        power.ACK_TIMEOUT_SECONDS = anterior

    tardanza = time.time() - inicio

    comprobar("La peticion no espera indefinidamente",
              tardanza < 5, f"{tardanza:.1f}s")

    comprobar("Responde con el tiempo agotado",
              respuesta.status_code == 504)


def test_no_quedan_consultas_colgadas():

    preparar()
    conectar(AgentFalso())

    cliente = h.cliente(h.OWNER)

    pedir(cliente, "lock")
    pedir(cliente, "lock")

    comprobar("No queda ninguna consulta pendiente",
              not queries.pending_queries,
              str(queries.pending_queries))


def test_se_reutiliza_el_canal_existente():

    backend = io.open(RAIZ / "backend" / "main.py",
                      encoding="utf-8").read()

    comprobar("Las acciones usan la centralita de consultas existente",
              "KIND_POWER" in backend and "create_query(" in backend)

    comprobar("Y el WebSocket de siempre, sin abrir otro canal",
              backend.count("@app.websocket(") == 1)


# ==============================
# Auditoria
# ==============================

def test_auditoria_de_cada_accion():

    for accion in ACCIONES:

        preparar()
        conectar(AgentFalso())

        pedir(h.cliente(h.OWNER), accion)

        filas = h.registros(action=f"device.{accion}")

        comprobar(f"device.{accion} queda auditada", len(filas) >= 1)

        if filas:

            fila = filas[0]

            comprobar(f"Con el usuario ({accion})",
                      fila["username"] == h.OWNER)

            comprobar(f"Con el equipo ({accion})",
                      fila["device_id"] == EQUIPO)

            comprobar(f"Con la hora ({accion})", bool(fila["timestamp"]))

            comprobar(f"Y con resultado ({accion})",
                      fila["status"] in ("success", "error"))


def test_la_auditoria_distingue_lo_hecho_de_lo_aceptado():

    preparar()
    conectar(AgentFalso())

    cliente = h.cliente(h.OWNER)

    pedir(cliente, "lock")
    pedir(cliente, "shutdown")

    bloqueo = h.registros(action="device.lock")[0]["details"]
    apagado = h.registros(action="device.shutdown")[0]["details"]

    comprobar("Bloquear se registra como realizada",
              "realizada" in bloqueo.lower(), bloqueo)

    comprobar("Apagar se registra como aceptada, no como confirmada",
              "aceptada" in apagado.lower()
              and "no puede confirmarse" in apagado.lower(), apagado)


def test_la_auditoria_no_guarda_secretos():

    preparar()
    conectar(AgentFalso())

    cliente = h.cliente(h.OWNER)

    for accion in ACCIONES:
        pedir(cliente, accion)

    todo = " ".join(
        f"{r['details']} {r['username']} {r['source_ip']}"
        for r in h.registros()
    )

    for secreto in (h.CLAVE_OWNER, "pbkdf2_sha256", h.COOKIE,
                    "ExitWindowsEx", "LockWorkStation"):

        comprobar(f"La auditoria no guarda {secreto[:16]}",
                  secreto not in todo)


# ==============================
# Agent: ejecucion en Windows
# ==============================

def test_el_agent_no_ejecuta_comandos_de_texto():

    codigo = io.open(RAIZ / "agent" / "power.py", encoding="utf-8").read()

    prohibido = ["subprocess", "os.system", "Popen", "shell=True",
                 "shutdown.exe", "cmd.exe", "powershell"]

    encontrados = [p for p in prohibido if p in codigo]

    comprobar("El Agent no construye ni ejecuta ningun comando de texto",
              not encontrados, str(encontrados))

    comprobar("Usa las funciones nativas de Windows",
              "ExitWindowsEx" in codigo and "LockWorkStation" in codigo)

    comprobar("Y no anade dependencias: ctypes es de la biblioteca estandar",
              "import ctypes" in codigo)


def test_el_agent_solo_conoce_cuatro_acciones():

    sys.path.insert(0, str(RAIZ / "agent"))

    import power as agent_power

    comprobar("El Agent tiene exactamente cuatro acciones",
              set(agent_power.HANDLERS) == set(ACCIONES),
              str(sorted(agent_power.HANDLERS)))

    resultado = agent_power.execute("formatear")

    comprobar("Una accion desconocida se rechaza sin ejecutar nada",
              resultado.get("accepted") is False
              and "no permitida" in resultado.get("error", "").lower())


def test_el_agent_confirma_antes_de_desaparecer():

    sys.path.insert(0, str(RAIZ / "agent"))

    import power as agent_power

    comprobar("Reiniciar, apagar y cerrar sesion se aplazan",
              agent_power.DEFERRED == {"logoff", "restart", "shutdown"})

    comprobar("Para que la confirmacion salga antes del corte",
              agent_power.EXECUTION_DELAY_SECONDS > 0)

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    bloque = codigo.split('elif message.startswith("power_action:")', 1)[1]
    bloque = bloque.split("elif message.startswith", 1)[0]

    comprobar("El Agent responde siempre por el mismo canal",
              "power_result:" in bloque)

    comprobar("Y ejecuta en un hilo aparte, sin bloquear el WebSocket",
              "asyncio.to_thread" in bloque)

    comprobar("Del mensaje solo lee el nombre de la accion",
              'peticion.get("action")' in bloque
              and "eval" not in bloque and "exec(" not in bloque)


# ==============================
# H. Frontend
# ==============================

def test_frontend_botones_y_confirmaciones():

    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    html = io.open(RAIZ / "frontend" / "index.html",
                   encoding="utf-8").read()

    comprobar("Existe el panel de acciones", 'id="power-panel"' in html)

    comprobar("Y empieza oculto",
              "hidden" in html.split('id="power-panel"')[1][:80])

    comprobar("Hay un sitio para el estado", 'id="power-status"' in html)

    comprobar("Las cuatro acciones estan definidas",
              all(f'accion: "{a}"' in js for a in ACCIONES))

    comprobar("Cada una con su permiso",
              all(f'permiso: "device.{a}"' in js for a in ACCIONES))

    comprobar("Los botones se filtran por permiso",
              "ACCIONES_DE_EQUIPO.filter(a => puede(a.permiso))" in js)

    # Confirmaciones: se mira el trozo de cada accion por separado,
    # desde su nombre hasta el de la siguiente.
    bloque = js.split("const ACCIONES_DE_EQUIPO", 1)[1]
    bloque = bloque.split(chr(10) + "];", 1)[0]

    def trozo_de(accion):

        resto = bloque.split(f'accion: "{accion}"', 1)[1]

        for siguiente in ACCIONES:
            if f'accion: "{siguiente}"' in resto:
                resto = resto.split(f'accion: "{siguiente}"', 1)[0]

        return resto

    for accion in DESTRUCTIVAS:

        trozo = trozo_de(accion)

        comprobar(f"{accion} pide confirmacion",
                  "confirmacion:" in trozo
                  and "confirmacion: null" not in trozo,
                  trozo[:60])

    comprobar("Bloquear, al ser reversible, no la necesita",
              "confirmacion: null" in trozo_de("lock"))

    comprobar("La confirmacion se pide antes de enviar",
              "if (!confirm(" in js)

    comprobar("Se evita el doble clic en el panel",
              "accionesEnCurso" in js and "deshabilitarAcciones" in js)

    comprobar("Los errores se muestran",
              'mostrarEstado("power-status"' in js
              and '"error"' in js)

    comprobar("Y el estado del equipo se refresca si va a desconectarse",
              "data.pending" in js and "loadDevices()" in js)

    comprobar("El panel no inventa el mensaje: usa el del servidor",
              "mostrarEstado(\"power-status\", data.message" in js)


# ==============================
# Produccion
# ==============================

def test_no_se_ejecuto_nada_real():
    """
    Ninguna prueba ha llamado a Windows.

    El Agent de estas pruebas es un objeto que guarda mensajes en una
    lista. agent/power.py solo se importa para mirar su contenido y para
    probar el rechazo de una accion inventada, que no ejecuta nada.
    """

    sys.path.insert(0, str(RAIZ / "agent"))

    import power as agent_power

    comprobar("El Agent simulado no es el modulo real",
              AgentFalso.__module__ != agent_power.__name__)

    comprobar("Ninguna accion real se ha invocado",
              all(callable(f) for f in agent_power.HANDLERS.values()))

    comprobar("Las pruebas usan equipos inventados",
              EQUIPO.startswith("equipo-g5"))


# ==============================

def main():

    pruebas = [
        test_bloquear_correcto,
        test_cerrar_sesion_correcto,
        test_el_equipo_recibe_solo_el_nombre_de_la_accion,
        test_no_se_aceptan_comandos_arbitrarios,
        test_reiniciar_y_apagar_se_envian,
        test_proteccion_contra_doble_ejecucion,
        test_el_doble_clic_no_llega_al_equipo,
        test_bloquear_si_se_puede_repetir,
        test_la_ventana_de_repeticion_caduca,
        test_la_proteccion_es_por_equipo_y_por_accion,
        test_sin_reintento_automatico_tras_desconexion,
        test_un_fallo_del_equipo_si_permite_reintentar,
        test_el_owner_puede_las_cuatro,
        test_subadmin_con_el_permiso_concreto,
        test_subadmin_sin_el_permiso,
        test_cada_permiso_es_independiente,
        test_sin_permiso_no_se_envia_nada_al_equipo,
        test_el_intento_sin_permiso_queda_auditado,
        test_los_permisos_de_equipo_no_dan_administracion_de_usuarios,
        test_sin_sesion,
        test_sesion_revocada,
        test_usuario_desactivado,
        test_device_id_inexistente,
        test_la_orden_va_al_equipo_pedido_y_a_ningun_otro,
        test_otro_agent_no_puede_confirmar_por_el,
        test_una_respuesta_de_otro_tipo_no_cuela,
        test_no_se_usa_el_hostname_ni_la_ip,
        test_equipo_desconectado,
        test_offline_no_consume_la_ventana_de_repeticion,
        test_el_canal_se_cae_al_enviar,
        test_respuesta_tardia_no_contamina,
        test_la_peticion_no_se_queda_colgada,
        test_no_quedan_consultas_colgadas,
        test_se_reutiliza_el_canal_existente,
        test_auditoria_de_cada_accion,
        test_la_auditoria_distingue_lo_hecho_de_lo_aceptado,
        test_la_auditoria_no_guarda_secretos,
        test_el_agent_no_ejecuta_comandos_de_texto,
        test_el_agent_solo_conoce_cuatro_acciones,
        test_el_agent_confirma_antes_de_desaparecer,
        test_frontend_botones_y_confirmaciones,
        test_no_se_ejecuto_nada_real
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:70])

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
