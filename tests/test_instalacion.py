"""
Identidad de la instalacion y modalidad de despliegue (cloud /
self_hosted).

    .venv\\Scripts\\python tests/test_instalacion.py

Lo que se comprueba aqui es que la clasificacion es SOLO eso: un dato.
Cambiarla no mueve equipos, no toca tokens, no reconfigura Agents y no
redirige a nadie.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import re
import sys
import uuid

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.database as database
import backend.enrollment as enrollment
import backend.installation as instalacion
import backend.main as servidor
import backend.organizations as orgs
import backend.recordings as recordings
import backend.users as users

from backend.auth import hash_agent_token


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeInstalacion1!"

EQUIPO_P = "equipo-plastika-inst"
EQUIPO_B = "equipo-beta-inst"

TOKEN_AGENT = "token-individual-inst"

escenario = {}


def preparar():
    """Plastika y Beta, con usuarios, equipos y un operador de plataforma."""

    h.reiniciar()

    servidor.connected_agents.clear()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organization_settings",
                  "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    plastika = orgs.create_organization(
        "Plastika", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE,
        billing_mode=orgs.BILLING_COURTESY
    )

    beta = orgs.create_organization(
        "Beta", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, plastika["id"])

    todos = list(users.PERMISSIONS)

    users.create_user("sub_plastika", CLAVE, permissions=todos,
                      organization_id=plastika["id"])

    users.create_user("owner_beta", CLAVE, permissions=todos,
                      organization_id=beta["id"])

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE users SET role = 'owner' WHERE username = 'owner_beta'"
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id, "
        "agent_token_hash, agent_token_active) "
        "VALUES (?, 'PC-PLASTIKA', ?, ?, 1)",
        (EQUIPO_P, plastika["id"], hash_agent_token(TOKEN_AGENT))
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES (?, 'PC-BETA', ?)", (EQUIPO_B, beta["id"])
    )
    conexion.commit()
    conexion.close()

    users.create_user("operador", CLAVE, organization_id=None)
    users.set_platform_owner("operador", True)

    recordings.register_local_recording(
        EQUIPO_P, f"{EQUIPO_P}/2026/10/06/rec_1700000001.mp4",
        "2026-10-06T10:00:00+00:00", "2026-10-06T10:01:00+00:00", 60, 1000
    )

    enrollment.create_token(plastika["id"], label="Plastika")

    escenario.update({"plastika": plastika, "beta": beta})

    return escenario


def inventario():
    conexion = database.get_connection()
    datos = {
        t: conexion.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("devices", "users", "recordings", "alerts",
                  "organizations", "enrollment_tokens", "audit_log",
                  "user_permissions")
    }
    conexion.close()
    return datos


def estado_de_los_agents():
    conexion = database.get_connection()
    datos = [
        (r["device_id"], r["agent_token_hash"], r["agent_token_active"],
         r["organization_id"])
        for r in conexion.execute(
            "SELECT device_id, agent_token_hash, agent_token_active, "
            "organization_id FROM devices ORDER BY device_id"
        )
    ]
    conexion.close()
    return datos


# ==============================
# 1-7. deployment_type y server_url
# ==============================

def test_se_aceptan_las_dos_modalidades():

    preparar()

    nube = orgs.create_organization(
        "Nube", deployment_type=orgs.DEPLOYMENT_CLOUD
    )

    propia = orgs.create_organization(
        "Propia", deployment_type=orgs.DEPLOYMENT_SELF_HOSTED
    )

    comprobar("Se acepta cloud",
              nube["deployment_type"] == "cloud")

    comprobar("Se acepta self_hosted",
              propia["deployment_type"] == "self_hosted")


def test_una_modalidad_inventada_se_rechaza():

    preparar()

    rechazadas = 0

    for valor in ("oracle", "hibrido", "", "CLOUD", "self hosted", None):

        try:
            orgs.create_organization(f"X{valor}", deployment_type=valor)

        except orgs.OrganizationError:
            rechazadas += 1

    comprobar("Una modalidad desconocida se rechaza",
              rechazadas == 6, str(rechazadas))

    try:
        orgs.update_organization(escenario["plastika"]["id"],
                                 deployment_type="inventada")
        rechazo = False

    except orgs.OrganizationError:
        rechazo = True

    comprobar("Tampoco se puede cambiar a una desconocida", rechazo)


def test_las_organizaciones_existentes_quedan_como_cloud():
    """La migracion no debe cambiar el significado de lo que ya habia."""

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM organizations")

    # Una fila como la que dejaria una base anterior a esta columna
    conexion.execute(
        "INSERT INTO organizations (name, slug, plan, "
        "subscription_status, billing_mode, active) "
        "VALUES ('Antigua', 'antigua', 'pro', 'active', 'courtesy', 1)"
    )
    conexion.commit()
    conexion.close()

    antigua = orgs.get_organization_by_slug("antigua")

    comprobar("Una organizacion previa queda como cloud",
              antigua["deployment_type"] == "cloud",
              str(antigua["deployment_type"]))

    comprobar("Y sin direccion inventada",
              antigua["server_url"] is None,
              str(antigua["server_url"]))


def test_plastika_queda_como_cloud():

    preparar()

    plastika = orgs.get_organization(escenario["plastika"]["id"])

    comprobar("Plastika es cloud",
              plastika["deployment_type"] == "cloud")

    comprobar("Y conserva su plan y estado",
              plastika["plan"] == orgs.PLAN_PRO
              and plastika["subscription_status"] == orgs.STATUS_ACTIVE
              and plastika["billing_mode"] == orgs.BILLING_COURTESY)


def test_server_url_admite_vacio_y_direccion():

    preparar()

    plastika = escenario["plastika"]["id"]

    comprobar("Puede estar vacia",
              orgs.get_organization(plastika)["server_url"] is None)

    actualizada = orgs.update_organization(
        plastika, server_url="https://panel.ejemplo.com/"
    )

    comprobar("Admite una direccion valida",
              actualizada["server_url"] == "https://panel.ejemplo.com",
              str(actualizada["server_url"]))

    comprobar("Se puede volver a dejar vacia",
              orgs.update_organization(
                  plastika, server_url=None
              )["server_url"] is None)


def test_server_url_rechaza_lo_que_no_es_una_direccion():

    preparar()

    plastika = escenario["plastika"]["id"]

    rechazadas = 0

    for valor in ("javascript:alert(1)", "file:///etc/passwd",
                  "panel.ejemplo.com", "ftp://x", "a" * 400):
        try:
            orgs.update_organization(plastika, server_url=valor)
        except orgs.OrganizationError:
            rechazadas += 1

    comprobar("Solo se admiten http y https, y con un largo razonable",
              rechazadas == 5, str(rechazadas))


def test_cambiar_el_plan_no_borra_la_direccion():

    preparar()

    plastika = escenario["plastika"]["id"]

    orgs.update_organization(plastika, server_url="https://panel.ejemplo")

    orgs.update_organization(plastika, plan=orgs.PLAN_BASIC)

    comprobar("No indicar la direccion no la borra",
              orgs.get_organization(plastika)["server_url"]
              == "https://panel.ejemplo")


# ==============================
# 8-15. La instalacion
# ==============================

def test_la_instalacion_existe_y_es_unica():

    ficha = instalacion.ensure_installation()

    comprobar("La instalacion existe", ficha is not None)

    conexion = database.get_connection()
    total = conexion.execute(
        "SELECT COUNT(*) FROM installation"
    ).fetchone()[0]
    conexion.close()

    comprobar("Hay exactamente una fila", total == 1, str(total))


def test_la_base_impide_una_segunda_fila():

    instalacion.ensure_installation()

    conexion = database.get_connection()

    try:
        conexion.execute(
            "INSERT INTO installation (singleton, id, mode, created_at) "
            "VALUES (2, 'otra', 'cloud', '2026-01-01')"
        )
        conexion.commit()
        colo = True

    except Exception:
        colo = False

    finally:
        conexion.close()

    comprobar("La base rechaza una segunda instalacion", not colo)


def test_el_identificador_es_un_uuid():

    ficha = instalacion.ensure_installation()

    try:
        uuid.UUID(ficha["id"])
        valido = True

    except (ValueError, AttributeError, TypeError):
        valido = False

    comprobar("El identificador es un UUID valido", valido,
              str(ficha["id"])[:12])


def test_el_identificador_sobrevive_a_los_reinicios():

    primero = instalacion.ensure_installation()["id"]

    # Varias inicializaciones seguidas, como varios arranques
    for _ in range(5):
        database.init_db()
        instalacion.ensure_installation()

    comprobar("El identificador no cambia entre arranques",
              instalacion.get_installation()["id"] == primero)

    conexion = database.get_connection()
    total = conexion.execute(
        "SELECT COUNT(*) FROM installation"
    ).fetchone()[0]
    conexion.close()

    comprobar("Ni se duplica la fila", total == 1, str(total))


def test_la_modalidad_de_la_instalacion():

    instalacion.ensure_installation()

    comprobar("Admite cloud",
              instalacion.set_mode("cloud")["mode"] == "cloud")

    comprobar("Admite self_hosted",
              instalacion.set_mode("self_hosted")["mode"] == "self_hosted")

    rechazadas = 0

    for valor in ("oracle", "", None, "CLOUD", "hibrido"):
        try:
            instalacion.set_mode(valor)
        except instalacion.InstallationError:
            rechazadas += 1

    comprobar("Rechaza una modalidad desconocida", rechazadas == 5,
              str(rechazadas))

    # Se deja como estaba
    instalacion.set_mode("cloud")


def test_licensed_to_es_texto_descriptivo():

    instalacion.ensure_installation()

    ficha = instalacion.set_licensed_to("Plastika SA")

    comprobar("Se puede anotar a nombre de quien esta",
              ficha["licensed_to"] == "Plastika SA")

    comprobar("Y se puede dejar vacio",
              instalacion.set_licensed_to(None)["licensed_to"] is None)

    codigo = io.open(RAIZ / "backend" / "installation.py",
                     encoding="utf-8").read()

    plano = " ".join(codigo.lower().split())

    comprobar("Se documenta que no es una credencial",
              "no debe usarse nunca para guardar un secreto" in plano
              and "no es una licencia ni una credencial" in plano)


def test_una_instalacion_nueva_no_inventa_organizaciones():
    """Lo mas importante de este bloque para self-hosted."""

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM organizations")
    conexion.execute("DELETE FROM devices")
    conexion.execute("DELETE FROM user_permissions")
    conexion.execute("DELETE FROM users")
    conexion.execute("DELETE FROM installation")
    conexion.commit()
    conexion.close()

    ficha = instalacion.ensure_installation()

    conexion = database.get_connection()
    organizaciones = conexion.execute(
        "SELECT COUNT(*) FROM organizations"
    ).fetchone()[0]
    conexion.close()

    comprobar("La instalacion se crea", ficha is not None)

    comprobar("Y no aparece ninguna organizacion",
              organizaciones == 0, str(organizaciones))

    comprobar("El comportamiento anterior se conserva",
              orgs.ensure_default_organization() is None)


# ==============================
# 16-22, 49-52. Migracion
# ==============================

def test_la_migracion_no_pierde_nada():

    preparar()

    antes = inventario()
    agents_antes = estado_de_los_agents()

    for _ in range(3):
        database.init_db()
        instalacion.ensure_installation()
        orgs.ensure_default_organization()

    despues = inventario()

    comprobar("No se pierde ningun dato",
              antes == despues, f"{antes} -> {despues}")

    comprobar("Ni se tocan los equipos ni sus tokens",
              agents_antes == estado_de_los_agents())

    comprobar("Ni se crean organizaciones durante la migracion",
              antes["organizations"] == despues["organizations"])


def test_la_migracion_no_duplica_columnas():

    for _ in range(3):
        database.init_db()

    conexion = database.get_connection()

    columnas = [
        c["name"]
        for c in conexion.execute("PRAGMA table_info(organizations)")
    ]

    conexion.close()

    comprobar("deployment_type aparece una sola vez",
              columnas.count("deployment_type") == 1)

    comprobar("server_url aparece una sola vez",
              columnas.count("server_url") == 1)

    comprobar("Y se conservan las columnas anteriores",
              {"id", "name", "slug", "plan", "subscription_status",
               "billing_mode", "active", "created_at", "updated_at",
               "notes", "suspended_at"} <= set(columnas),
              str(sorted(columnas)))


# ==============================
# 23-29. API y aislamiento
# ==============================

def test_el_endpoint_exige_autenticacion():

    preparar()

    comprobar("Sin sesion responde 401",
              h.cliente().get("/api/installation").status_code == 401)


def test_solo_la_plataforma_consulta_la_instalacion():

    preparar()

    respuesta = h.cliente("operador").get("/api/installation")

    comprobar("El operador de plataforma si puede",
              respuesta.status_code == 200, str(respuesta.status_code))

    ficha = respuesta.json()["installation"]

    comprobar("Y recibe los cuatro datos",
              set(ficha) == {"id", "mode", "licensed_to", "created_at"},
              str(sorted(ficha)))

    for etiqueta, quien in (("Owner", h.OWNER),
                            ("subadmin", "sub_plastika")):

        comprobar(f"Un {etiqueta} de organizacion no la consulta",
                  h.cliente(quien).get(
                      "/api/installation"
                  ).status_code == 403)


def test_la_instalacion_no_expone_secretos():

    preparar()

    texto = h.cliente("operador").get("/api/installation").text

    prohibidos = ["password", "hash", "token", "secret", "key",
                  "pbkdf2", "rae_", "AUTH_"]

    encontrados = [p for p in prohibidos if p.lower() in texto.lower()]

    comprobar("La respuesta no contiene nada sensible",
              not encontrados, str(encontrados))

    codigo = io.open(RAIZ / "backend" / "installation.py",
                     encoding="utf-8").read()

    conexion = database.get_connection()
    columnas = {
        c["name"] for c in conexion.execute("PRAGMA table_info(installation)")
    }
    conexion.close()

    comprobar("Y la tabla solo tiene columnas de identificacion",
              columnas == {"singleton", "id", "mode", "licensed_to",
                           "created_at"},
              str(sorted(columnas)))


def test_el_owner_ve_la_modalidad_de_su_organizacion():

    preparar()

    orgs.update_organization(escenario["plastika"]["id"],
                             server_url="https://panel.plastika")

    datos = h.cliente(h.OWNER).get("/api/users/me").json()

    organizacion = datos.get("organization") or {}

    comprobar("Ve la modalidad de la suya",
              organizacion.get("deployment_type") == "cloud")

    comprobar("Y su direccion informativa",
              organizacion.get("server_url") == "https://panel.plastika")

    comprobar("Pero nada de la instalacion",
              "installation" not in datos
              and "licensed_to" not in str(datos))


def test_un_owner_no_toca_otra_organizacion():

    preparar()

    beta = escenario["beta"]["id"]

    antes = orgs.get_organization(beta)

    cliente = h.cliente(h.OWNER)

    comprobar("No puede consultar otra organizacion",
              cliente.get("/api/organizations").status_code == 403)

    comprobar("Ni cambiar su modalidad",
              cliente.post(f"/api/organizations/{beta}",
                           json={"deployment_type": "self_hosted"}
                           ).status_code == 403)

    comprobar("Ni su direccion",
              cliente.post(f"/api/organizations/{beta}",
                           json={"server_url": "https://colado"}
                           ).status_code == 403)

    comprobar("Y Beta sigue igual",
              orgs.get_organization(beta) == antes)


def test_la_plataforma_administra_la_modalidad():

    preparar()

    cliente = h.cliente("operador")
    plastika = escenario["plastika"]["id"]

    respuesta = cliente.post(
        f"/api/organizations/{plastika}",
        json={"deployment_type": "self_hosted",
              "server_url": "https://panel.plastika.local"}
    )

    comprobar("La plataforma cambia la modalidad",
              respuesta.status_code == 200, str(respuesta.status_code))

    organizacion = respuesta.json()["organization"]

    comprobar("Queda como self_hosted",
              organizacion["deployment_type"] == "self_hosted")

    comprobar("Con su direccion",
              organizacion["server_url"] == "https://panel.plastika.local")

    comprobar("Puede volver a cloud",
              cliente.post(f"/api/organizations/{plastika}",
                           json={"deployment_type": "cloud"}
                           ).json()["organization"]["deployment_type"]
              == "cloud")

    comprobar("Una modalidad inventada se rechaza con 400",
              cliente.post(f"/api/organizations/{plastika}",
                           json={"deployment_type": "oracle"}
                           ).status_code == 400)


def test_el_alta_admite_la_modalidad():

    preparar()

    respuesta = h.cliente("operador").post("/api/organizations", json={
        "name": "Cliente Propio",
        "deployment_type": "self_hosted",
        "server_url": "https://remoteadmin.cliente.com"
    })

    comprobar("Se puede crear una organizacion self-hosted",
              respuesta.status_code == 200, str(respuesta.status_code))

    if respuesta.status_code == 200:

        organizacion = respuesta.json()["organization"]

        comprobar("Con su modalidad",
                  organizacion["deployment_type"] == "self_hosted")

        comprobar("Y su direccion",
                  organizacion["server_url"]
                  == "https://remoteadmin.cliente.com")

    comprobar("Por defecto se crea como cloud",
              h.cliente("operador").post(
                  "/api/organizations", json={"name": "Por Defecto"}
              ).json()["organization"]["deployment_type"] == "cloud")


# ==============================
# 30-38. Cambiar la clasificacion no toca nada mas
# ==============================

def test_cambiar_la_modalidad_no_toca_los_agents():

    preparar()

    antes_agents = estado_de_los_agents()
    antes_todo = inventario()

    credenciales_antes = [
        (t["id"], t["organization_id"], t["active"])
        for t in enrollment.list_tokens()
    ]

    cliente = h.cliente("operador")
    plastika = escenario["plastika"]["id"]

    cliente.post(f"/api/organizations/{plastika}",
                 json={"deployment_type": "self_hosted",
                       "server_url": "https://panel.plastika.local"})

    comprobar("No cambia ningun device_id ni token de Agent",
              estado_de_los_agents() == antes_agents)

    despues_todo = inventario()

    # La auditoria SI debe crecer: el cambio queda registrado.
    comprobar("Registra el cambio en la auditoria",
              despues_todo.pop("audit_log") > antes_todo.pop("audit_log"))

    comprobar("Y no se pierde ni se crea nada mas",
              despues_todo == antes_todo,
              f"{antes_todo} -> {despues_todo}")

    comprobar("Las credenciales de alta siguen igual",
              [(t["id"], t["organization_id"], t["active"])
               for t in enrollment.list_tokens()] == credenciales_antes)


def test_el_agent_sigue_funcionando_tras_el_cambio():

    preparar()

    cliente = h.cliente("operador")

    cliente.post(f"/api/organizations/{escenario['plastika']['id']}",
                 json={"deployment_type": "self_hosted",
                       "server_url": "https://otro.servidor.ejemplo"})

    from backend.devices import get_device_id_for_token

    comprobar("El token individual sigue identificando a su equipo",
              get_device_id_for_token(TOKEN_AGENT) == EQUIPO_P)

    latido = h.cliente().post(
        "/api/devices/heartbeat",
        json={"device_id": EQUIPO_P, "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": TOKEN_AGENT}
    )

    comprobar("El heartbeat sigue funcionando",
              latido.status_code == 200, str(latido.status_code))

    rerregistro = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-PLASTIKA", "operating_system": "Windows",
              "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": TOKEN_AGENT}
    )

    comprobar("Y el re-registro tambien",
              rerregistro.status_code == 200, str(rerregistro.status_code))


def test_nada_redirige_ni_reconfigura_al_agent():
    """server_url es informacion, no un mecanismo de control."""

    backend = io.open(RAIZ / "backend" / "main.py",
                      encoding="utf-8").read()

    # Solo el cuerpo del manejador del Agent, no todo lo que venga
    # despues en el archivo.
    resto = backend.split('@app.websocket("/ws/agent")', 1)[1]
    canal = resto.split(chr(10) + "@app.", 1)[0]

    comprobar("El servidor no manda server_url por el canal del Agent",
              "server_url" not in canal and "deployment_type" not in canal)

    agente = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    comprobar("El Agent no conoce server_url",
              "server_url" not in agente)

    comprobar("Ni deployment_type",
              "deployment_type" not in agente)

    comprobar("Y sigue tomando el servidor de su configuracion",
              'os.getenv(' in agente and "REMOTEADMIN_SERVER" in agente)

    comprobar("No hay ninguna orden de cambiar de servidor",
              "set_server" not in backend and "change_server" not in backend)


def test_server_url_no_provoca_reenrollment():

    preparar()

    antes = estado_de_los_agents()

    cliente = h.cliente("operador")
    plastika = escenario["plastika"]["id"]

    for direccion in ("https://uno.ejemplo", "https://dos.ejemplo", None):
        cliente.post(f"/api/organizations/{plastika}",
                     json={"server_url": direccion})

    comprobar("Cambiar la direccion varias veces no toca los equipos",
              estado_de_los_agents() == antes)

    comprobar("Las credenciales de alta siguen valiendo",
              len(enrollment.list_tokens(plastika)) == 1)


# ==============================
# 39-48. El bloque anterior sigue en pie
# ==============================

def test_la_suspension_sigue_funcionando():

    preparar()

    plastika = escenario["plastika"]["id"]

    for estado in (orgs.STATUS_ACTIVE, orgs.STATUS_TRIAL,
                   orgs.STATUS_PAST_DUE):

        orgs.update_organization(plastika, subscription_status=estado)

        comprobar(f"Con '{estado}' el acceso sigue siendo normal",
                  h.cliente(h.OWNER).get("/api/devices").status_code == 200)

    orgs.update_organization(plastika,
                             subscription_status=orgs.STATUS_SUSPENDED)

    respuesta = h.cliente(h.OWNER).get("/api/devices")

    comprobar("Suspendida sigue bloqueando el panel",
              respuesta.status_code == 403
              and respuesta.json().get("code") == "organization_suspended")

    comprobar("La plataforma sigue administrandola",
              h.cliente("operador").get("/api/organizations").status_code
              == 200)

    comprobar("Y los Agents siguen funcionando",
              h.cliente().post(
                  "/api/devices/heartbeat",
                  json={"device_id": EQUIPO_P, "ip_address": "10.0.0.1"},
                  headers={"X-Agent-Token": TOKEN_AGENT}
              ).status_code == 200)


def test_la_modalidad_no_afecta_a_la_suspension():

    preparar()

    plastika = escenario["plastika"]["id"]

    orgs.update_organization(plastika, deployment_type="self_hosted")

    comprobar("Una organizacion self-hosted activa opera con normalidad",
              h.cliente(h.OWNER).get("/api/devices").status_code == 200)

    orgs.update_organization(plastika,
                             subscription_status=orgs.STATUS_SUSPENDED)

    comprobar("Y suspendida se bloquea igual",
              h.cliente(h.OWNER).get("/api/devices").status_code == 403)

    orgs.update_organization(plastika,
                             subscription_status=orgs.STATUS_ACTIVE)

    comprobar("Al reactivarla vuelve, conservando su modalidad",
              h.cliente(h.OWNER).get("/api/devices").status_code == 200
              and orgs.get_organization(plastika)["deployment_type"]
              == "self_hosted")


def test_el_aislamiento_sigue_en_pie():

    preparar()

    cliente = h.cliente(h.OWNER)

    comprobar("No se alcanza un equipo de otra organizacion",
              cliente.get(f"/api/devices/{EQUIPO_B}/software").status_code
              == 404)

    comprobar("Cada uno ve sus equipos",
              len(cliente.get("/api/devices").json()) == 1)

    comprobar("La otra organizacion opera con normalidad",
              h.cliente("owner_beta").get("/api/devices").status_code == 200)


# ==============================
# 54. Sin infraestructura concreta
# ==============================

def test_no_hay_infraestructura_acoplada():

    sospechosos = ("oracle", "cloudflare", "trycloudflare", "nginx",
                   "systemd", "ngrok")

    encontrados = []

    for archivo in (RAIZ / "backend").glob("*.py"):

        texto = io.open(archivo, encoding="utf-8").read().lower()

        for palabra in sospechosos:
            if palabra in texto:
                encontrados.append(f"{archivo.name}:{palabra}")

    comprobar("La logica de despliegue no menciona ningun proveedor",
              not encontrados, str(encontrados))

    comprobar("Las modalidades son genericas",
              set(orgs.DEPLOYMENT_TYPES) == {"cloud", "self_hosted"})

    comprobar("Y las de la instalacion tambien",
              set(instalacion.MODES) == {"cloud", "self_hosted"})


def test_el_panel_ensena_la_modalidad():

    html = io.open(RAIZ / "frontend" / "index.html",
                   encoding="utf-8").read()

    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

    comprobar("La tabla de organizaciones tiene su columna",
              "Despliegue" in html)

    comprobar("Con etiquetas legibles",
              "ETIQUETA_DE_DESPLIEGUE" in js
              and 'cloud: "Cloud"' in js
              and 'self_hosted: "Self-hosted"' in js)

    comprobar("Y se ensena la direccion si la hay",
              "organizacion.server_url" in js)

    comprobar("Sin pagina comercial ni instalador",
              "stripe" not in js.lower()
              and "precio" not in html.lower()
              and "wizard" not in js.lower())


# ==============================

def main():

    pruebas = [
        test_se_aceptan_las_dos_modalidades,
        test_una_modalidad_inventada_se_rechaza,
        test_las_organizaciones_existentes_quedan_como_cloud,
        test_plastika_queda_como_cloud,
        test_server_url_admite_vacio_y_direccion,
        test_server_url_rechaza_lo_que_no_es_una_direccion,
        test_cambiar_el_plan_no_borra_la_direccion,
        test_la_instalacion_existe_y_es_unica,
        test_la_base_impide_una_segunda_fila,
        test_el_identificador_es_un_uuid,
        test_el_identificador_sobrevive_a_los_reinicios,
        test_la_modalidad_de_la_instalacion,
        test_licensed_to_es_texto_descriptivo,
        test_una_instalacion_nueva_no_inventa_organizaciones,
        test_la_migracion_no_pierde_nada,
        test_la_migracion_no_duplica_columnas,
        test_el_endpoint_exige_autenticacion,
        test_solo_la_plataforma_consulta_la_instalacion,
        test_la_instalacion_no_expone_secretos,
        test_el_owner_ve_la_modalidad_de_su_organizacion,
        test_un_owner_no_toca_otra_organizacion,
        test_la_plataforma_administra_la_modalidad,
        test_el_alta_admite_la_modalidad,
        test_cambiar_la_modalidad_no_toca_los_agents,
        test_el_agent_sigue_funcionando_tras_el_cambio,
        test_nada_redirige_ni_reconfigura_al_agent,
        test_server_url_no_provoca_reenrollment,
        test_la_suspension_sigue_funcionando,
        test_la_modalidad_no_afecta_a_la_suspension,
        test_el_aislamiento_sigue_en_pie,
        test_no_hay_infraestructura_acoplada,
        test_el_panel_ensena_la_modalidad
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
