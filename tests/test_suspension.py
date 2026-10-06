"""
Suspension de organizaciones: que deja de funcionar y que no.

    .venv\\Scripts\\python tests/test_suspension.py

La idea que se comprueba aqui: suspender es una politica de acceso al
PANEL, no una orden para apagar la infraestructura del cliente. Los
equipos siguen grabando y subiendo; lo que se cierra es la puerta de la
aplicacion. Y no se borra nada.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.auth as auth
import backend.database as database
import backend.enrollment as enrollment
import backend.main as servidor
import backend.organizations as orgs
import backend.recordings as recordings
import backend.users as users

from backend.auth import hash_agent_token


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeSuspension1!"

EQUIPO_A = "equipo-suspension-a"
EQUIPO_B = "equipo-suspension-b"

TOKEN_AGENT_A = "token-individual-del-agent-a"

escenario = {}


def preparar():
    """
    Dos organizaciones completas y un operador de plataforma.

      Alfa -> owner (h.OWNER), subadmin alfa_sub, equipo EQUIPO_A
      Beta -> owner beta_owner,                   equipo EQUIPO_B
    """

    h.reiniciar()

    servidor.connected_agents.clear()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organization_settings",
                  "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    alfa = orgs.create_organization(
        "Alfa", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    beta = orgs.create_organization(
        "Beta", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, alfa["id"])

    todos = list(users.PERMISSIONS)

    users.create_user("alfa_sub", CLAVE, permissions=todos,
                      organization_id=alfa["id"])

    users.create_user("beta_owner", CLAVE, permissions=todos,
                      organization_id=beta["id"])

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE users SET role = 'owner' WHERE username = 'beta_owner'"
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id, "
        "agent_token_hash, agent_token_active) "
        "VALUES (?, 'PC-ALFA', ?, ?, 1)",
        (EQUIPO_A, alfa["id"], hash_agent_token(TOKEN_AGENT_A))
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES (?, 'PC-BETA', ?)", (EQUIPO_B, beta["id"])
    )
    conexion.commit()
    conexion.close()

    users.create_user("operador", CLAVE, organization_id=None)
    users.set_platform_owner("operador", True)

    grabacion = recordings.register_local_recording(
        EQUIPO_A, f"{EQUIPO_A}/2026/10/06/rec_1700000001.mp4",
        "2026-10-06T10:00:00+00:00", "2026-10-06T10:01:00+00:00", 60, 1000
    )[0]

    escenario.update({"alfa": alfa, "beta": beta, "grabacion": grabacion})

    return escenario


def suspender(organization_id=None):
    return orgs.update_organization(
        organization_id or escenario["alfa"]["id"],
        subscription_status=orgs.STATUS_SUSPENDED
    )


def reactivar(organization_id=None):
    return orgs.update_organization(
        organization_id or escenario["alfa"]["id"],
        subscription_status=orgs.STATUS_ACTIVE
    )


# Endpoints operativos y administrativos del panel
OPERATIVOS = [
    ("GET", "/api/devices", None),
    ("GET", "/api/alerts", None),
    ("GET", "/api/recordings", None),
    ("GET", "/api/settings", None),
    ("GET", f"/api/devices/{EQUIPO_A}/software", None),
    ("GET", f"/api/devices/{EQUIPO_A}/processes", None),
    ("GET", f"/api/devices/{EQUIPO_A}/services", None),
    ("POST", f"/api/devices/{EQUIPO_A}/ping", None),
    ("POST", f"/api/devices/{EQUIPO_A}/power/lock", None),
]

ADMINISTRATIVOS = [
    ("GET", "/api/users", None),
    ("POST", "/api/users", {"username": "colado", "password": CLAVE}),
    ("POST", "/api/users/alfa_sub/active", {"active": False}),
    ("POST", "/api/settings", {"server_name": "X"}),
    ("GET", "/api/audit", None),
    ("GET", "/api/enrollment-tokens", None),
    ("POST", "/api/enrollment-tokens", {"label": "X"}),
]


def pedir(cliente, metodo, ruta, cuerpo=None):

    if metodo == "GET":
        return cliente.get(ruta)

    if metodo == "DELETE":
        return cliente.delete(ruta)

    return cliente.post(ruta, json=cuerpo or {})


# ==============================
# 1-3. Estados que permiten operar
# ==============================

def test_los_estados_normales_permiten_acceso():

    for estado in (orgs.STATUS_ACTIVE, orgs.STATUS_TRIAL,
                   orgs.STATUS_PAST_DUE):

        preparar()

        orgs.update_organization(escenario["alfa"]["id"],
                                 subscription_status=estado)

        respuesta = h.cliente(h.OWNER).get("/api/devices")

        comprobar(f"Con estado '{estado}' el acceso es normal",
                  respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Un pago atrasado NO corta el acceso",
              orgs.STATUS_PAST_DUE in orgs.USABLE_STATUSES)


# ==============================
# 4-5, 9-10. Suspension bloquea el panel
# ==============================

def test_suspendida_bloquea_al_owner():

    preparar()
    suspender()

    cliente = h.cliente(h.OWNER)

    bloqueados = 0

    for metodo, ruta, cuerpo in OPERATIVOS:

        respuesta = pedir(cliente, metodo, ruta, cuerpo)

        if respuesta.status_code == 403 \
                and respuesta.json().get("code") == "organization_suspended":
            bloqueados += 1
        else:
            comprobar(f"{metodo} {ruta} deberia bloquearse",
                      False, str(respuesta.status_code))

    comprobar("Todos los endpoints operativos quedan bloqueados",
              bloqueados == len(OPERATIVOS),
              f"{bloqueados}/{len(OPERATIVOS)}")


def test_suspendida_bloquea_lo_administrativo():

    preparar()
    suspender()

    cliente = h.cliente(h.OWNER)

    bloqueados = sum(
        1 for metodo, ruta, cuerpo in ADMINISTRATIVOS
        if pedir(cliente, metodo, ruta, cuerpo).status_code == 403
    )

    comprobar("Todos los endpoints administrativos quedan bloqueados",
              bloqueados == len(ADMINISTRATIVOS),
              f"{bloqueados}/{len(ADMINISTRATIVOS)}")

    comprobar("Y no se crea el usuario que se intento colar",
              users.get_user("colado") is None)

    comprobar("Ni se desactiva a nadie",
              users.get_user("alfa_sub")["active"] is True)


def test_suspendida_bloquea_al_subadmin():

    preparar()
    suspender()

    respuesta = h.cliente("alfa_sub").get("/api/devices")

    comprobar("El subadmin tambien queda bloqueado",
              respuesta.status_code == 403, str(respuesta.status_code))

    comprobar("Con el mismo motivo",
              respuesta.json().get("code") == "organization_suspended")


# ==============================
# 6-8. Entrar, salir y entender
# ==============================

def test_se_puede_iniciar_sesion_estando_suspendida():

    preparar()
    suspender()

    respuesta = h.cliente().post(
        "/api/auth/login",
        json={"username": h.OWNER, "password": h.CLAVE_OWNER}
    )

    comprobar("Se puede iniciar sesion con la organizacion suspendida",
              respuesta.status_code == 200, str(respuesta.status_code))


def test_se_puede_cerrar_sesion():

    preparar()
    suspender()

    cliente = h.cliente(h.OWNER)

    comprobar("Se puede cerrar sesion",
              cliente.post("/api/auth/logout").status_code == 200)


def test_el_usuario_recibe_informacion_clara():

    preparar()
    suspender()

    datos = h.cliente(h.OWNER).get("/api/users/me")

    comprobar("Se puede consultar la propia sesion",
              datos.status_code == 200, str(datos.status_code))

    cuerpo = datos.json()

    comprobar("Y dice quien es", cuerpo.get("username") == h.OWNER)

    organizacion = cuerpo.get("organization") or {}

    comprobar("Informa de que no puede operar",
              organizacion.get("usable") is False)

    comprobar("Con el estado concreto",
              organizacion.get("subscription_status")
              == orgs.STATUS_SUSPENDED)

    comprobar("Y una explicacion legible",
              "suspendida" in (organizacion.get("access_message") or "")
              .lower(),
              str(organizacion.get("access_message"))[:60])

    comprobar("Que aclara que los datos se conservan",
              "conserva" in (organizacion.get("access_message") or "")
              .lower())

    comprobar("Se registra desde cuando",
              bool(organizacion.get("suspended_at")))


def test_el_rechazo_se_distingue_de_un_sin_permiso():

    preparar()
    suspender()

    respuesta = h.cliente(h.OWNER).get("/api/devices")

    cuerpo = respuesta.json()

    comprobar("El rechazo lleva una marca propia",
              cuerpo.get("code") == "organization_suspended")

    comprobar("Y el estado de la organizacion",
              cuerpo.get("organization_status") == orgs.STATUS_SUSPENDED)

    comprobar("Con un mensaje util, no un 'no tienes permiso'",
              "permiso" not in cuerpo.get("message", "").lower())


# ==============================
# 11-13. Platform Owner
# ==============================

def test_la_plataforma_no_queda_bloqueada():

    preparar()
    suspender()

    cliente = h.cliente("operador")

    comprobar("El operador de plataforma sigue operando",
              cliente.get("/api/devices").status_code == 200)

    comprobar("Y puede consultar la organizacion suspendida",
              cliente.get("/api/organizations").status_code == 200)

    organizaciones = cliente.get("/api/organizations").json()["organizations"]

    alfa = next(o for o in organizaciones if o["name"] == "Alfa")

    comprobar("Que figura como suspendida",
              alfa["subscription_status"] == orgs.STATUS_SUSPENDED
              and alfa["usable"] is False)

    comprobar("Ve los equipos de la organizacion suspendida",
              len(cliente.get("/api/devices").json()) == 2)


def test_la_plataforma_puede_reactivarla():

    preparar()
    suspender()

    respuesta = h.cliente("operador").post(
        f"/api/organizations/{escenario['alfa']['id']}",
        json={"subscription_status": orgs.STATUS_ACTIVE}
    )

    comprobar("La plataforma reactiva la organizacion",
              respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Y queda operativa",
              respuesta.json()["organization"]["usable"] is True)


# ==============================
# 14. Reactivacion sin perdida
# ==============================

def test_al_reactivar_vuelve_el_acceso():

    preparar()

    cliente = h.cliente(h.OWNER)

    antes = len(cliente.get("/api/devices").json())

    suspender()

    comprobar("Suspendida, no hay acceso",
              cliente.get("/api/devices").status_code == 403)

    reactivar()

    respuesta = cliente.get("/api/devices")

    comprobar("Reactivada, el acceso vuelve",
              respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Con los mismos equipos, sin reconstruir nada",
              len(respuesta.json()) == antes)

    comprobar("Y sus grabaciones siguen ahi",
              len(cliente.get("/api/recordings").json()) == 1)

    comprobar("La marca de suspension se limpia",
              orgs.get_organization(escenario["alfa"]["id"])["suspended_at"]
              is None)

    comprobar("Sin volver a iniciar sesion: la sesion seguia valiendo",
              cliente.get("/api/users/me").status_code == 200)


# ==============================
# 15. Otras organizaciones
# ==============================

def test_otra_organizacion_no_se_ve_afectada():

    preparar()
    suspender()

    cliente = h.cliente("beta_owner")

    comprobar("La otra organizacion sigue operando",
              cliente.get("/api/devices").status_code == 200)

    comprobar("Ve sus equipos",
              len(cliente.get("/api/devices").json()) == 1)

    comprobar("Y puede administrar los suyos",
              cliente.get("/api/users").status_code == 200)


# ==============================
# 16-20. Los Agents NO se ven afectados
# ==============================

def test_la_suspension_no_invalida_el_token_del_agent():

    preparar()
    suspender()

    from backend.devices import get_device_id_for_token

    comprobar("El token individual sigue identificando a su equipo",
              get_device_id_for_token(TOKEN_AGENT_A) == EQUIPO_A)

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT agent_token_hash, agent_token_active FROM devices "
        "WHERE device_id = ?", (EQUIPO_A,)
    ).fetchone()
    conexion.close()

    comprobar("El hash del token no se toca",
              fila["agent_token_hash"] == hash_agent_token(TOKEN_AGENT_A))

    comprobar("Y sigue activo", fila["agent_token_active"] == 1)


def test_el_agent_sigue_enviando_heartbeat():

    preparar()
    suspender()

    respuesta = h.cliente().post(
        "/api/devices/heartbeat",
        json={"device_id": EQUIPO_A, "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": TOKEN_AGENT_A}
    )

    comprobar("El heartbeat sigue funcionando con la organizacion suspendida",
              respuesta.status_code == 200, str(respuesta.status_code))


def test_el_agent_sigue_registrandose():

    preparar()
    suspender()

    respuesta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-ALFA", "operating_system": "Windows",
              "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": TOKEN_AGENT_A}
    )

    comprobar("Un Agent ya enrolado se re-registra igualmente",
              respuesta.status_code == 200, str(respuesta.status_code))


def test_el_websocket_sigue_aceptando_al_agent():

    preparar()
    suspender()

    from backend.devices import get_device_id_for_token

    # Es lo que comprueba el handshake del WebSocket antes de aceptar
    comprobar("El WebSocket seguiria aceptando al Agent",
              get_device_id_for_token(TOKEN_AGENT_A) == EQUIPO_A)

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    bloque = codigo.split('@app.websocket("/ws/agent")', 1)[1]
    bloque = bloque.split("\n@app.", 1)[0]

    comprobar("Y su handshake no consulta el estado de suscripcion",
              "subscription_status" not in bloque
              and "_suspension_bloquea" not in bloque)


def test_el_agent_sigue_subiendo_grabaciones():

    preparar()
    suspender()

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    comprobar("La subida de grabaciones no pasa por la sesion del panel",
              'RUTAS_DE_AGENT = ("/recordings/upload",)' in codigo)

    bloque = codigo.split("def _suspension_bloquea(", 1)[1]
    bloque = bloque.split("\ndef ", 1)[0]

    comprobar("La suspension solo mira al usuario de la sesion",
              "current_user(request)" in bloque
              and "if usuario is None:" in bloque
              and "return None" in bloque)

    # Sin sesion de panel, la comprobacion no bloquea nada
    respuesta = h.cliente().post(
        f"/api/devices/{EQUIPO_A}/recordings/upload?filename=rec_1.mp4",
        headers={"X-Agent-Token": TOKEN_AGENT_A},
        content=b""
    )

    comprobar("La subida no se rechaza por suspension",
              respuesta.status_code != 403
              or respuesta.json().get("code") != "organization_suspended",
              str(respuesta.status_code))


def test_la_grabacion_local_no_depende_del_servidor():

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    comprobar("El grabador corre en su propio hilo",
              "ScreenRecorder(" in codigo)

    comprobar("Y el Agent no consulta ningun estado de suscripcion",
              "subscription" not in codigo.lower()
              and "suspend" not in codigo.lower())


# ==============================
# 21. No se borra nada
# ==============================

def test_suspender_no_borra_nada():

    preparar()

    def inventario():
        conexion = database.get_connection()
        datos = {
            t: conexion.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("devices", "users", "recordings", "alerts",
                      "organizations", "enrollment_tokens",
                      "user_permissions")
        }
        conexion.close()
        return datos

    antes = inventario()

    suspender()
    despues_suspender = inventario()

    reactivar()
    despues_reactivar = inventario()

    comprobar("Suspender no borra ni una fila",
              antes == despues_suspender,
              f"{antes} -> {despues_suspender}")

    comprobar("Reactivar tampoco",
              antes == despues_reactivar)


def test_cancelled_sigue_la_politica_existente():
    """
    No se inventa una politica: la que ya habia.

    USABLE_STATUSES no incluye 'cancelled', asi que is_usable() ya lo
    trataba como sin acceso desde que se escribio. Aplicar esa funcion
    es lo que hace este bloque; el significado no cambia.
    """

    comprobar("'cancelled' ya estaba fuera de los estados que operan",
              orgs.STATUS_CANCELLED not in orgs.USABLE_STATUSES)

    preparar()

    orgs.update_organization(escenario["alfa"]["id"],
                             subscription_status=orgs.STATUS_CANCELLED)

    respuesta = h.cliente(h.OWNER).get("/api/devices")

    comprobar("Una organizacion cancelada tampoco opera",
              respuesta.status_code == 403, str(respuesta.status_code))

    comprobar("Con su propio mensaje",
              "cancelada" in respuesta.json().get("message", "").lower())

    comprobar("Y sin borrar nada: se puede reactivar",
              reactivar()["usable"] is True)


def test_desactivar_la_organizacion_tambien_cierra_el_panel():

    preparar()

    orgs.update_organization(escenario["alfa"]["id"], active=False)

    comprobar("Una organizacion inactiva tampoco opera",
              h.cliente(h.OWNER).get("/api/devices").status_code == 403)

    orgs.update_organization(escenario["alfa"]["id"], active=True)

    comprobar("Y al reactivarla vuelve",
              h.cliente(h.OWNER).get("/api/devices").status_code == 200)


# ==============================
# 22. Sin escapatoria
# ==============================

def test_no_se_escapa_con_organization_id():

    preparar()
    suspender()

    cliente = h.cliente(h.OWNER)

    intentos = [
        ("POST", "/api/settings",
         {"server_name": "X", "organization_id": escenario["beta"]["id"]}),
        ("POST", "/api/users",
         {"username": "colado", "password": CLAVE,
          "organization_id": escenario["beta"]["id"]}),
        ("GET", f"/api/devices/{EQUIPO_B}/software", None),
    ]

    bloqueados = sum(
        1 for metodo, ruta, cuerpo in intentos
        if pedir(cliente, metodo, ruta, cuerpo).status_code in (403, 404)
    )

    comprobar("No se escapa de la suspension indicando otra organizacion",
              bloqueados == len(intentos), f"{bloqueados}/{len(intentos)}")

    comprobar("Y no se crea nada en la otra organizacion",
              users.get_user("colado") is None)

    comprobar("La otra organizacion sigue intacta",
              h.cliente("beta_owner").get("/api/devices").status_code == 200)


def test_la_comprobacion_esta_centralizada():

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    comprobar("Hay una sola funcion que decide la suspension",
              codigo.count("def _suspension_bloquea(") == 1)

    # Dos apariciones: la definicion y la unica llamada
    comprobar("Y se llama desde un unico punto",
              codigo.count("_suspension_bloquea(request, path)") == 2
              and codigo.count("def _suspension_bloquea(request, path)")
              == 1)

    comprobar("Ningun endpoint la repite por su cuenta",
              codigo.count("organization_suspended") == 1)

    comprobar("La organizacion sale de la sesion",
              "organization_of(usuario)" in codigo.split(
                  "def _suspension_bloquea(", 1)[1].split("\ndef ", 1)[0])


# ==============================
# Migracion
# ==============================

def test_la_migracion_es_aditiva_e_idempotente():

    conexion = database.get_connection()
    columnas = {
        c["name"]
        for c in conexion.execute("PRAGMA table_info(organizations)")
    }
    conexion.close()

    originales = {"id", "name", "slug", "plan", "subscription_status",
                  "billing_mode", "active", "created_at", "updated_at",
                  "notes"}

    comprobar("Se conservan todas las columnas anteriores",
              originales <= columnas,
              str(sorted(originales - columnas)))

    comprobar("Y se anade suspended_at", "suspended_at" in columnas)

    preparar()

    antes = orgs.get_organization(escenario["alfa"]["id"])

    for _ in range(3):
        database.init_db()

    despues = orgs.get_organization(escenario["alfa"]["id"])

    comprobar("Repetir la migracion no cambia los datos",
              antes == despues)


def test_suspended_at_se_pone_y_se_quita():

    preparar()

    comprobar("Una organizacion activa no tiene marca",
              orgs.get_organization(
                  escenario["alfa"]["id"]
              )["suspended_at"] is None)

    suspender()

    marca = orgs.get_organization(escenario["alfa"]["id"])["suspended_at"]

    comprobar("Al suspender se anota el momento", bool(marca))

    # Cambiar otra cosa no debe mover la marca
    orgs.update_organization(escenario["alfa"]["id"], plan=orgs.PLAN_BASIC)

    comprobar("Cambiar el plan no mueve la marca",
              orgs.get_organization(
                  escenario["alfa"]["id"]
              )["suspended_at"] == marca)

    reactivar()

    comprobar("Al reactivar se limpia",
              orgs.get_organization(
                  escenario["alfa"]["id"]
              )["suspended_at"] is None)


# ==============================
# Frontend
# ==============================

def test_el_panel_avisa():

    html = io.open(RAIZ / "frontend" / "index.html",
                   encoding="utf-8").read()

    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

    comprobar("Existe el aviso", 'id="suspension-banner"' in html)

    comprobar("Y arranca oculto",
              "hidden" in html.split('id="suspension-banner"')[1][:80])

    comprobar("Dice claramente que esta suspendida",
              "suspendida" in html.lower())

    comprobar("Aclara que los equipos siguen grabando",
              "siguen grabando" in html)

    comprobar("Se ensena segun lo que diga el servidor",
              "organizacion.usable === false" in js)

    comprobar("Con el mensaje del servidor, no uno inventado",
              "organizacion.access_message" in js)

    # No se ha anadido nada comercial. 'billing' aparece solo en el
    # selector de modalidad de facturacion, que ya existia.
    comprobar("No se ha anadido ninguna pasarela de pago",
              "stripe" not in js.lower()
              and "mercadopago" not in js.lower()
              and "checkout" not in js.lower())

    comprobar("Ni una pagina comercial",
              "precio" not in html.lower() and "suscribir" not in html.lower()
              and "contratar" not in html.lower())


# ==============================

def main():

    pruebas = [
        test_los_estados_normales_permiten_acceso,
        test_suspendida_bloquea_al_owner,
        test_suspendida_bloquea_lo_administrativo,
        test_suspendida_bloquea_al_subadmin,
        test_se_puede_iniciar_sesion_estando_suspendida,
        test_se_puede_cerrar_sesion,
        test_el_usuario_recibe_informacion_clara,
        test_el_rechazo_se_distingue_de_un_sin_permiso,
        test_la_plataforma_no_queda_bloqueada,
        test_la_plataforma_puede_reactivarla,
        test_al_reactivar_vuelve_el_acceso,
        test_otra_organizacion_no_se_ve_afectada,
        test_la_suspension_no_invalida_el_token_del_agent,
        test_el_agent_sigue_enviando_heartbeat,
        test_el_agent_sigue_registrandose,
        test_el_websocket_sigue_aceptando_al_agent,
        test_el_agent_sigue_subiendo_grabaciones,
        test_la_grabacion_local_no_depende_del_servidor,
        test_suspender_no_borra_nada,
        test_cancelled_sigue_la_politica_existente,
        test_desactivar_la_organizacion_tambien_cierra_el_panel,
        test_no_se_escapa_con_organization_id,
        test_la_comprobacion_esta_centralizada,
        test_la_migracion_es_aditiva_e_idempotente,
        test_suspended_at_se_pone_y_se_quita,
        test_el_panel_avisa
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:80])

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
