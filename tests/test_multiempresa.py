"""
Arquitectura multiempresa: organizaciones, aislamiento, planes y estados.

    .venv\\Scripts\\python tests/test_multiempresa.py

Dos organizaciones con sus propios usuarios, equipos y grabaciones. Lo
que se comprueba aqui, sobre todo, es que ninguna ve nada de la otra
aunque escriba las URLs a mano.

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
import backend.main as servidor
import backend.organizations as orgs
import backend.queries as queries
import backend.recordings as recordings
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDePruebaMultiempresa!"

# Equipos de cada organizacion
EQUIPO_A = "equipo-org-a"
EQUIPO_B = "equipo-org-b"


escenario = {}


def preparar():
    """
    Dos organizaciones completas y un operador de plataforma.

      Alfa  -> owner alfa_owner, subadmin alfa_sub, equipo EQUIPO_A
      Beta  -> owner beta_owner,                    equipo EQUIPO_B
      plataforma -> operador
    """

    h.reiniciar()

    servidor.connected_agents.clear()
    queries.pending_queries.clear()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM recordings")
    conexion.execute("DELETE FROM devices")
    conexion.execute("DELETE FROM organizations")
    conexion.execute("DELETE FROM alerts")
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

    # El owner que crea el andamiaje se queda en Alfa
    users.set_organization(h.OWNER, alfa["id"])

    todos = list(users.PERMISSIONS)

    users.create_user("alfa_sub", CLAVE, permissions=todos,
                      organization_id=alfa["id"])

    users.create_user("beta_admin", CLAVE, permissions=todos,
                      organization_id=beta["id"])

    # Un segundo owner, para Beta
    conexion = database.get_connection()
    conexion.execute(
        "UPDATE users SET role = 'owner' WHERE username = 'beta_admin'"
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES (?, 'PC-ALFA', ?)", (EQUIPO_A, alfa["id"])
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES (?, 'PC-BETA', ?)", (EQUIPO_B, beta["id"])
    )
    conexion.commit()
    conexion.close()

    # Un operador de plataforma
    users.create_user("plataforma", CLAVE, organization_id=alfa["id"])
    users.set_platform_owner("plataforma", True)

    grabacion_a = recordings.register_local_recording(
        EQUIPO_A, f"{EQUIPO_A}/2026/10/05/rec_1700000001.mp4",
        "2026-10-05T10:00:00+00:00", "2026-10-05T10:01:00+00:00", 60, 1000
    )[0]

    grabacion_b = recordings.register_local_recording(
        EQUIPO_B, f"{EQUIPO_B}/2026/10/05/rec_1700000002.mp4",
        "2026-10-05T10:00:00+00:00", "2026-10-05T10:01:00+00:00", 60, 1000
    )[0]

    escenario.update({
        "alfa": alfa, "beta": beta,
        "grabacion_a": grabacion_a, "grabacion_b": grabacion_b
    })

    return escenario


# Rutas de equipo que un usuario de otra organizacion NO debe alcanzar
RUTAS_DE_EQUIPO = [
    ("GET", "/software"),
    ("GET", "/processes"),
    ("GET", "/services"),
    ("GET", "/recording/status"),
    ("GET", "/recording/continuous"),
    ("GET", "/recording/schedule"),
    ("POST", "/ping"),
    ("POST", "/system-info"),
    ("POST", "/software"),
    ("POST", "/screen"),
    ("POST", "/recording/start"),
    ("POST", "/recording/stop"),
    ("POST", "/power/lock"),
    ("POST", "/power/shutdown"),
]


def pedir(cliente, metodo, ruta, cuerpo=None):

    if metodo == "GET":
        return cliente.get(ruta)

    if metodo == "DELETE":
        return cliente.delete(ruta)

    return cliente.post(ruta, json=cuerpo or {})


# ==============================
# Migracion
# ==============================

def test_la_instalacion_existente_se_convierte_en_organizacion():

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM organizations")
    conexion.execute("DELETE FROM devices")
    conexion.execute(
        "INSERT INTO devices (device_id, hostname) VALUES ('viejo', 'PC')"
    )
    conexion.execute("UPDATE users SET organization_id = NULL")
    conexion.commit()
    conexion.close()

    identificador = orgs.ensure_default_organization()

    organizacion = orgs.get_organization(identificador)

    comprobar("Se crea la primera organizacion", organizacion is not None)

    if organizacion:
        comprobar("Se llama Plastika", organizacion["name"] == "Plastika")
        comprobar("Con plan Pro", organizacion["plan"] == orgs.PLAN_PRO)
        comprobar("En cortesia",
                  organizacion["billing_mode"] == orgs.BILLING_COURTESY)
        comprobar("Y activa",
                  organizacion["subscription_status"] == orgs.STATUS_ACTIVE
                  and organizacion["usable"] is True)

    conexion = database.get_connection()
    sin_org = conexion.execute(
        "SELECT COUNT(*) FROM devices WHERE organization_id IS NULL"
    ).fetchone()[0]
    sin_usr = conexion.execute(
        "SELECT COUNT(*) FROM users WHERE organization_id IS NULL "
        "AND role != 'platform_owner'"
    ).fetchone()[0]
    conexion.close()

    comprobar("Los equipos existentes quedan asociados", sin_org == 0)
    comprobar("Los usuarios existentes tambien", sin_usr == 0)


def test_la_migracion_es_idempotente():

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM organizations")
    conexion.execute("DELETE FROM devices")
    conexion.execute(
        "INSERT INTO devices (device_id, hostname) VALUES ('viejo', 'PC')"
    )
    conexion.commit()
    conexion.close()

    identificadores = {orgs.ensure_default_organization() for _ in range(5)}

    conexion = database.get_connection()
    total = conexion.execute(
        "SELECT COUNT(*) FROM organizations"
    ).fetchone()[0]
    conexion.close()

    comprobar("Repetirla devuelve siempre la misma",
              len(identificadores) == 1)

    comprobar("Y no duplica organizaciones", total == 1, str(total))


def test_la_migracion_no_toca_equipos_ni_tokens():

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM organizations")
    conexion.execute("DELETE FROM devices")
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, agent_token_hash, "
        "agent_token_active) VALUES ('mi-equipo', 'PC', 'hash-intacto', 1)"
    )
    conexion.commit()
    conexion.close()

    orgs.ensure_default_organization()

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT device_id, agent_token_hash, agent_token_active "
        "FROM devices WHERE device_id = 'mi-equipo'"
    ).fetchone()
    conexion.close()

    comprobar("El device_id no se regenera",
              fila["device_id"] == "mi-equipo")

    comprobar("El token del Agent no se toca",
              fila["agent_token_hash"] == "hash-intacto"
              and fila["agent_token_active"] == 1)


# ==============================
# Aislamiento: equipos
# ==============================

def test_cada_organizacion_ve_solo_sus_equipos():

    preparar()

    de_alfa = h.cliente(h.OWNER).get("/api/devices").json()
    de_beta = h.cliente("beta_admin").get("/api/devices").json()

    comprobar("Alfa ve un equipo", len(de_alfa) == 1, str(len(de_alfa)))
    comprobar("Y es el suyo", de_alfa[0]["device_id"] == EQUIPO_A)

    comprobar("Beta ve un equipo", len(de_beta) == 1)
    comprobar("Y es el suyo", de_beta[0]["device_id"] == EQUIPO_B)


def test_no_se_alcanza_un_equipo_ajeno_por_url():

    preparar()

    cliente = h.cliente(h.OWNER)

    bloqueadas = 0

    for metodo, sufijo in RUTAS_DE_EQUIPO:

        respuesta = pedir(
            cliente, metodo, f"/api/devices/{EQUIPO_B}{sufijo}"
        )

        if respuesta.status_code == 404:
            bloqueadas += 1
        else:
            comprobar(f"{metodo} {sufijo} de otra organizacion",
                      False, str(respuesta.status_code))

    comprobar(
        "Ninguna ruta de un equipo ajeno responde con datos",
        bloqueadas == len(RUTAS_DE_EQUIPO),
        f"{bloqueadas}/{len(RUTAS_DE_EQUIPO)}"
    )


def test_un_equipo_ajeno_no_devuelve_ningun_dato():
    """
    Lo que no debe pasar es que se filtre informacion del recurso.

    Queda una diferencia conocida: un equipo de otra organizacion
    responde 404 y uno inexistente puede responder 200 con una lista
    vacia, asi que el codigo distingue "existe pero no es tuyo" de "no
    existe". Para aprovecharlo hay que acertar antes un device_id de 16
    caracteres hexadecimales —64 bits— que el servidor genera al azar,
    de modo que solo confirma un identificador que ya se conocia. Se
    deja anotado; el dato en si no se filtra.
    """

    preparar()

    cliente = h.cliente(h.OWNER)

    ajeno = cliente.get(f"/api/devices/{EQUIPO_B}/software")

    comprobar("Un equipo ajeno no devuelve datos",
              ajeno.status_code == 404, str(ajeno.status_code))

    comprobar("Y su respuesta no contiene nada del equipo",
              "PC-BETA" not in ajeno.text and EQUIPO_B not in ajeno.text,
              ajeno.text[:80])


def test_el_propio_equipo_si_funciona():

    preparar()

    respuesta = h.cliente(h.OWNER).get(
        f"/api/devices/{EQUIPO_A}/software"
    )

    comprobar("El equipo propio responde con normalidad",
              respuesta.status_code == 200, str(respuesta.status_code))


def test_las_acciones_remotas_respetan_la_organizacion():

    preparar()

    class AgentFalso:
        def __init__(self):
            self.enviados = []

        async def send_text(self, texto):
            self.enviados.append(texto)

    agente = AgentFalso()
    servidor.connected_agents[EQUIPO_B] = agente

    respuesta = h.cliente(h.OWNER).post(
        f"/api/devices/{EQUIPO_B}/power/shutdown"
    )

    comprobar("No se puede apagar un equipo de otra organizacion",
              respuesta.status_code == 404, str(respuesta.status_code))

    comprobar("Y no se le manda absolutamente nada",
              agente.enviados == [], str(agente.enviados))


# ==============================
# Aislamiento: grabaciones
# ==============================

def test_cada_organizacion_ve_solo_sus_grabaciones():

    preparar()

    de_alfa = h.cliente(h.OWNER).get("/api/recordings").json()
    de_beta = h.cliente("beta_admin").get("/api/recordings").json()

    comprobar("Alfa ve una grabacion", len(de_alfa) == 1,
              str(len(de_alfa)))

    comprobar("Y es de su equipo",
              de_alfa[0]["device_id"] == EQUIPO_A)

    comprobar("Beta ve la suya",
              len(de_beta) == 1 and de_beta[0]["device_id"] == EQUIPO_B)


def test_no_se_alcanza_una_grabacion_ajena():

    preparar()

    cliente = h.cliente(h.OWNER)
    ajena = escenario["grabacion_b"]

    rutas = [
        ("GET", f"/api/recordings/{ajena}/video"),
        ("GET", f"/api/recordings/{ajena}/download"),
        ("POST", f"/api/recordings/{ajena}/keep"),
        ("POST", f"/api/recordings/{ajena}/store"),
    ]

    bloqueadas = sum(
        1 for metodo, ruta in rutas
        if pedir(cliente, metodo, ruta).status_code == 404
    )

    comprobar("Ninguna grabacion ajena es alcanzable",
              bloqueadas == len(rutas), f"{bloqueadas}/{len(rutas)}")


def test_filtrar_por_un_equipo_ajeno_no_revela_nada():

    preparar()

    respuesta = h.cliente(h.OWNER).get(
        f"/api/recordings?device_id={EQUIPO_B}"
    )

    comprobar("Filtrar por un equipo ajeno devuelve vacio",
              respuesta.status_code == 200 and respuesta.json() == [],
              respuesta.text[:60])


# ==============================
# Aislamiento: usuarios
# ==============================

def test_cada_owner_ve_solo_sus_usuarios():

    preparar()

    de_alfa = h.cliente(h.OWNER).get("/api/users").json()["users"]
    de_beta = h.cliente("beta_admin").get("/api/users").json()["users"]

    nombres_alfa = {u["username"] for u in de_alfa}
    nombres_beta = {u["username"] for u in de_beta}

    comprobar("Alfa ve a los suyos",
              "alfa_sub" in nombres_alfa and h.OWNER in nombres_alfa)

    comprobar("Y no ve a los de Beta",
              "beta_admin" not in nombres_alfa, str(sorted(nombres_alfa)))

    comprobar("Beta no ve a los de Alfa",
              "alfa_sub" not in nombres_beta, str(sorted(nombres_beta)))

    comprobar("Ni al operador de la plataforma",
              "plataforma" not in nombres_alfa)


def test_no_se_administra_un_usuario_de_otra_organizacion():

    preparar()

    cliente = h.cliente(h.OWNER)

    operaciones = [
        ("POST", "/api/users/beta_admin/permissions",
         {"permissions": ["devices.view"]}),
        ("POST", "/api/users/beta_admin/active", {"active": False}),
        ("POST", "/api/users/beta_admin/email",
         {"email": "colado@ejemplo.com"}),
        ("POST", "/api/users/beta_admin/password",
         {"new_password": "OtraClaveLarga123!"}),
        ("DELETE", "/api/users/beta_admin", None),
    ]

    bloqueadas = sum(
        1 for metodo, ruta, cuerpo in operaciones
        if pedir(cliente, metodo, ruta, cuerpo).status_code == 404
    )

    comprobar("Ninguna operacion alcanza a un usuario ajeno",
              bloqueadas == len(operaciones),
              f"{bloqueadas}/{len(operaciones)}")

    objetivo = users.get_user("beta_admin")

    comprobar("Y el usuario de Beta sigue intacto",
              objetivo is not None and objetivo["active"] is True)


def test_el_alta_crea_dentro_de_la_propia_organizacion():

    preparar()

    respuesta = h.cliente(h.OWNER).post("/api/users", json={
        "username": "nuevo_alfa",
        "password": CLAVE,
        "permissions": ["devices.view"],
        # Intento de colocarlo en Beta: debe ignorarse
        "organization_id": escenario["beta"]["id"]
    })

    comprobar("El alta funciona", respuesta.status_code == 200,
              str(respuesta.status_code))

    creado = users.get_user("nuevo_alfa")

    comprobar("Y el usuario queda en la organizacion de quien lo creo",
              creado["organization_id"] == escenario["alfa"]["id"],
              str(creado["organization_id"]))


def test_un_subadmin_no_administra_usuarios():

    preparar()

    cliente = h.cliente("alfa_sub")

    comprobar("Ni con todos los permisos operativos",
              cliente.get("/api/users").status_code == 403)


# ==============================
# Platform owner
# ==============================

def test_solo_la_plataforma_administra_organizaciones():

    preparar()

    casos = [
        ("el Owner de una organizacion", h.cliente(h.OWNER)),
        ("un subadmin", h.cliente("alfa_sub")),
    ]

    for etiqueta, cliente in casos:

        comprobar(f"No puede listar organizaciones: {etiqueta}",
                  cliente.get("/api/organizations").status_code == 403)

        comprobar(f"Ni crearlas: {etiqueta}",
                  cliente.post("/api/organizations",
                               json={"name": "Colada"}).status_code == 403)

        comprobar(f"Ni cambiarlas: {etiqueta}",
                  cliente.post(
                      f"/api/organizations/{escenario['beta']['id']}",
                      json={"plan": "enterprise"}
                  ).status_code == 403)


def test_la_plataforma_si_puede():

    preparar()

    cliente = h.cliente("plataforma")

    listado = cliente.get("/api/organizations")

    comprobar("La plataforma lista organizaciones",
              listado.status_code == 200, str(listado.status_code))

    comprobar("Y las ve todas",
              len(listado.json()["organizations"]) == 2)

    creada = cliente.post("/api/organizations", json={
        "name": "Gamma", "plan": "basic"
    })

    comprobar("Puede crear una nueva", creada.status_code == 200,
              str(creada.status_code))

    if creada.status_code == 200:
        comprobar("Con el plan indicado",
                  creada.json()["organization"]["plan"] == "basic")


def test_la_plataforma_ve_los_recursos_de_todas():

    preparar()

    cliente = h.cliente("plataforma")

    comprobar("Ve los equipos de todas",
              len(cliente.get("/api/devices").json()) == 2)

    comprobar("Y alcanza un equipo de cualquiera",
              cliente.get(f"/api/devices/{EQUIPO_B}/software").status_code
              == 200)


def test_la_plataforma_no_pertenece_a_ninguna_organizacion():

    preparar()

    operador = users.get_user("plataforma")

    comprobar("Su organizacion es nula",
              operador["organization_id"] is None)

    comprobar("Y su rol es el de plataforma",
              operador["role"] == users.ROLE_PLATFORM_OWNER)

    datos = h.cliente("plataforma").get("/api/users/me").json()

    comprobar("La API lo identifica como tal",
              datos.get("is_platform_owner") is True)

    comprobar("Y sin organizacion", datos.get("organization") is None)


def test_no_hay_endpoint_para_ascenderse():
    """El ascenso solo ocurre desde la consola del servidor."""

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    comprobar("Ningun endpoint cambia el rol a plataforma",
              "set_platform_owner" not in codigo)

    comprobar("Ni mueve usuarios de organizacion por HTTP",
              "set_organization(" not in codigo)

    comprobar("Existe el script de consola",
              (RAIZ / "platform_admin.py").exists())


# ==============================
# Planes y limites
# ==============================

def test_los_planes_definen_limites():

    for nombre, plan in orgs.PLANS.items():
        for clave in ("label", "max_devices", "max_users",
                      "storage_mb", "features"):
            comprobar(f"El plan {nombre} define {clave}", clave in plan)

    comprobar("Enterprise no tiene limite de equipos",
              orgs.PLANS[orgs.PLAN_ENTERPRISE]["max_devices"] == 0)


def test_el_limite_de_usuarios_se_aplica_en_backend():

    preparar()

    # Free permite 2 usuarios; Alfa ya tiene owner + subadmin
    orgs.update_organization(escenario["alfa"]["id"], plan=orgs.PLAN_FREE)

    respuesta = h.cliente(h.OWNER).post("/api/users", json={
        "username": "uno_de_mas", "password": CLAVE
    })

    comprobar("Al alcanzar el limite del plan se rechaza con 409",
              respuesta.status_code == 409, str(respuesta.status_code))

    comprobar("Con un mensaje que explica el plan",
              "plan" in respuesta.json().get("message", "").lower())

    comprobar("Y el usuario no se crea",
              users.get_user("uno_de_mas") is None)


def test_al_ampliar_el_plan_se_puede_de_nuevo():

    preparar()

    orgs.update_organization(escenario["alfa"]["id"], plan=orgs.PLAN_FREE)

    h.cliente(h.OWNER).post("/api/users",
                            json={"username": "bloqueado",
                                  "password": CLAVE})

    orgs.update_organization(escenario["alfa"]["id"], plan=orgs.PLAN_PRO)

    respuesta = h.cliente(h.OWNER).post("/api/users", json={
        "username": "ahora_si", "password": CLAVE
    })

    comprobar("Con un plan mayor el alta vuelve a funcionar",
              respuesta.status_code == 200, str(respuesta.status_code))


def test_limites_y_capacidades_se_consultan_en_backend():

    preparar()

    alfa = escenario["alfa"]["id"]

    comprobar("Se puede preguntar si el plan incluye una capacidad",
              orgs.has_feature(alfa, "scheduling") is True)

    orgs.update_organization(alfa, plan=orgs.PLAN_FREE)

    comprobar("Y deja de incluirla al bajar de plan",
              orgs.has_feature(alfa, "scheduling") is False)

    comprobar("Un limite de 0 significa sin limite",
              orgs.limit_reached(alfa, "devices") is not None)


# ==============================
# Estados de suscripcion
# ==============================

def test_los_estados_estan_definidos():

    for estado in ("trial", "active", "past_due", "suspended", "cancelled"):
        comprobar(f"Existe el estado {estado}",
                  estado in orgs.SUBSCRIPTION_STATUSES)

    comprobar("Trial, activa y pago pendiente permiten trabajar",
              set(orgs.USABLE_STATUSES)
              == {"trial", "active", "past_due"})


def test_plan_estado_y_permisos_son_cosas_distintas():

    preparar()

    alfa = escenario["alfa"]["id"]

    orgs.update_organization(alfa, plan=orgs.PLAN_ENTERPRISE,
                             subscription_status=orgs.STATUS_SUSPENDED)

    organizacion = orgs.get_organization(alfa)

    comprobar("Una organizacion puede tener el mejor plan y estar suspendida",
              organizacion["plan"] == orgs.PLAN_ENTERPRISE
              and organizacion["usable"] is False)

    orgs.update_organization(alfa, plan=orgs.PLAN_FREE,
                             subscription_status=orgs.STATUS_ACTIVE)

    comprobar("Y el plan mas basico estando perfectamente activa",
              orgs.get_organization(alfa)["usable"] is True)

    comprobar("Los permisos del usuario no cambian con el plan",
              users.has_permission(
                  users.get_user("alfa_sub"), "devices.view"
              ) is True)


def test_cortesia_permite_operar_sin_pago():

    preparar()

    alfa = orgs.update_organization(
        escenario["alfa"]["id"],
        billing_mode=orgs.BILLING_COURTESY,
        subscription_status=orgs.STATUS_ACTIVE
    )

    comprobar("Una organizacion en cortesia puede operar",
              alfa["usable"] is True)

    comprobar("Y se distingue de una de pago",
              alfa["billing_mode"] == orgs.BILLING_COURTESY)


def test_suspender_no_borra_nada():

    preparar()

    alfa = escenario["alfa"]["id"]

    conexion = database.get_connection()
    antes = (
        conexion.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
        conexion.execute("SELECT COUNT(*) FROM recordings").fetchone()[0],
        conexion.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    )
    conexion.close()

    orgs.update_organization(alfa, active=False,
                             subscription_status=orgs.STATUS_SUSPENDED)

    conexion = database.get_connection()
    despues = (
        conexion.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
        conexion.execute("SELECT COUNT(*) FROM recordings").fetchone()[0],
        conexion.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    )
    conexion.close()

    comprobar("Suspender no borra equipos, grabaciones ni usuarios",
              antes == despues, f"{antes} -> {despues}")

    comprobar("La organizacion queda sin acceso",
              orgs.get_organization(alfa)["usable"] is False)


def test_reactivar_devuelve_el_acceso():

    preparar()

    alfa = escenario["alfa"]["id"]

    orgs.update_organization(alfa, active=False,
                             subscription_status=orgs.STATUS_SUSPENDED)

    orgs.update_organization(alfa, active=True,
                             subscription_status=orgs.STATUS_ACTIVE)

    organizacion = orgs.get_organization(alfa)

    comprobar("Reactivar devuelve el acceso",
              organizacion["usable"] is True)

    comprobar("Sin reconstruir equipos",
              len(h.cliente(h.OWNER).get("/api/devices").json()) == 1)

    comprobar("Ni grabaciones",
              len(h.cliente(h.OWNER).get("/api/recordings").json()) == 1)


# ==============================
# Auditoria
# ==============================

def test_la_auditoria_distingue_plataforma_y_organizacion():

    preparar()
    h.limpiar_auditoria() if hasattr(h, "limpiar_auditoria") else None

    conexion = database.get_connection()
    conexion.execute("DELETE FROM audit_log")
    conexion.commit()
    conexion.close()

    h.cliente("plataforma").post("/api/organizations",
                                 json={"name": "Delta"})

    filas = h.registros(action="organization.created")

    comprobar("Crear una organizacion queda auditado", len(filas) >= 1)

    if filas:
        comprobar("Marcado como accion de plataforma",
                  "platform" in (filas[0]["details"] or ""))

        comprobar("Con quien la creo",
                  filas[0]["username"] == "plataforma")


def test_la_auditoria_no_guarda_secretos():

    preparar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM audit_log")
    conexion.commit()
    conexion.close()

    h.cliente("plataforma").post("/api/organizations",
                                 json={"name": "Epsilon"})

    h.cliente(h.OWNER).post("/api/users",
                            json={"username": "otro", "password": CLAVE})

    todo = " ".join(str(r["details"]) for r in h.registros())

    for secreto in (CLAVE, h.CLAVE_OWNER, "pbkdf2_sha256"):
        comprobar(f"La auditoria no guarda {secreto[:16]}",
                  secreto not in todo)


# ==============================
# Self-hosted / sin acoplamiento
# ==============================

def test_no_hay_infraestructura_acoplada():
    """El codigo no asume un servidor concreto ni un proveedor."""

    sospechosos = ("oracle", "cloudflare", "trycloudflare")

    encontrados = []

    for archivo in (RAIZ / "backend").glob("*.py"):

        texto = io.open(archivo, encoding="utf-8").read().lower()

        for palabra in sospechosos:
            if palabra in texto:
                encontrados.append(f"{archivo.name}:{palabra}")

    comprobar("El backend no menciona ningun proveedor concreto",
              not encontrados, str(encontrados))

    agente = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    comprobar("La URL del servidor sigue siendo configurable",
              'os.getenv(\n    "REMOTEADMIN_SERVER"' in agente
              or 'REMOTEADMIN_SERVER' in agente)

    comprobar("Y el WebSocket se deriva de ella",
              "WEBSOCKET_URL = " in agente
              and "SERVER_URL" in agente)


def test_una_instalacion_nueva_no_inventa_organizacion():
    """Self-hosted: una base vacia no arranca con datos de ejemplo."""

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM organizations")
    conexion.execute("DELETE FROM devices")
    conexion.execute("DELETE FROM user_permissions")
    conexion.execute("DELETE FROM users")
    conexion.commit()
    conexion.close()

    identificador = orgs.ensure_default_organization()

    comprobar("Sin datos previos no se crea ninguna organizacion",
              identificador is None, str(identificador))


# ==============================

def main():

    pruebas = [
        test_la_instalacion_existente_se_convierte_en_organizacion,
        test_la_migracion_es_idempotente,
        test_la_migracion_no_toca_equipos_ni_tokens,
        test_cada_organizacion_ve_solo_sus_equipos,
        test_no_se_alcanza_un_equipo_ajeno_por_url,
        test_un_equipo_ajeno_no_devuelve_ningun_dato,
        test_el_propio_equipo_si_funciona,
        test_las_acciones_remotas_respetan_la_organizacion,
        test_cada_organizacion_ve_solo_sus_grabaciones,
        test_no_se_alcanza_una_grabacion_ajena,
        test_filtrar_por_un_equipo_ajeno_no_revela_nada,
        test_cada_owner_ve_solo_sus_usuarios,
        test_no_se_administra_un_usuario_de_otra_organizacion,
        test_el_alta_crea_dentro_de_la_propia_organizacion,
        test_un_subadmin_no_administra_usuarios,
        test_solo_la_plataforma_administra_organizaciones,
        test_la_plataforma_si_puede,
        test_la_plataforma_ve_los_recursos_de_todas,
        test_la_plataforma_no_pertenece_a_ninguna_organizacion,
        test_no_hay_endpoint_para_ascenderse,
        test_los_planes_definen_limites,
        test_el_limite_de_usuarios_se_aplica_en_backend,
        test_al_ampliar_el_plan_se_puede_de_nuevo,
        test_limites_y_capacidades_se_consultan_en_backend,
        test_los_estados_estan_definidos,
        test_plan_estado_y_permisos_son_cosas_distintas,
        test_cortesia_permite_operar_sin_pago,
        test_suspender_no_borra_nada,
        test_reactivar_devuelve_el_acceso,
        test_la_auditoria_distingue_plataforma_y_organizacion,
        test_la_auditoria_no_guarda_secretos,
        test_no_hay_infraestructura_acoplada,
        test_una_instalacion_nueva_no_inventa_organizacion
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
