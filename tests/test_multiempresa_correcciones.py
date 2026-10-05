"""
Correcciones del bloque multiempresa: plataforma separada, aislamiento
de auditoria y alertas, y alta de Agents por organizacion.

    .venv\\Scripts\\python tests/test_multiempresa_correcciones.py

Base temporal y datos inventados. No se toca produccion ni se ejecuta
ninguna accion destructiva.
"""

import io
import sys
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.alerts as alerts
import backend.audit as audit
import backend.auth as auth
import backend.database as database
import backend.enrollment as enrollment
import backend.main as servidor
import backend.organizations as orgs
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeCorreccionesMulti1!"

EQUIPO_P = "equipo-plastika"
EQUIPO_O = "equipo-otra"

escenario = {}


def preparar():
    """
    Plastika y Otra, cada una con su Owner, su subadmin y su equipo.
    Mas un operador de plataforma que no pertenece a ninguna.
    """

    h.reiniciar()

    servidor.connected_agents.clear()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    plastika = orgs.create_organization(
        "Plastika", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE,
        billing_mode=orgs.BILLING_COURTESY
    )

    otra = orgs.create_organization(
        "Otra", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, plastika["id"])

    todos = list(users.PERMISSIONS)

    users.create_user("sub_plastika", CLAVE, permissions=todos,
                      organization_id=plastika["id"])

    users.create_user("owner_otra", CLAVE, permissions=todos,
                      organization_id=otra["id"])

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE users SET role = 'owner' WHERE username = 'owner_otra'"
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES (?, 'PC-PLASTIKA', ?)", (EQUIPO_P, plastika["id"])
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES (?, 'PC-OTRA', ?)", (EQUIPO_O, otra["id"])
    )
    conexion.commit()
    conexion.close()

    # Operador de plataforma: cuenta aparte, sin organizacion
    users.create_user("operador", CLAVE, organization_id=None)
    users.set_platform_owner("operador", True)

    escenario.update({"plastika": plastika, "otra": otra})

    return escenario


def aviso(device_id, organization_id, mensaje="prueba"):
    """Crea un aviso directamente, como haria detect_alerts()."""

    conexion = database.get_connection()

    cursor = conexion.execute(
        "INSERT INTO alerts (device_id, hostname, type, message, "
        "created_at, organization_id) "
        "VALUES (?, 'PC', 'usage-critical', ?, '2026-10-05', ?)",
        (device_id, mensaje, organization_id)
    )

    conexion.commit()
    identificador = cursor.lastrowid
    conexion.close()

    return identificador


# ==============================
# 1-8. PLATFORM OWNER
# ==============================

def test_la_plataforma_no_pertenece_a_ninguna_organizacion():

    preparar()

    operador = users.get_user("operador")

    comprobar("Su organizacion es NULL",
              operador["organization_id"] is None)

    comprobar("Y su rol es el de plataforma",
              operador["role"] == users.ROLE_PLATFORM_OWNER)


def test_plastika_conserva_su_owner():

    preparar()

    propietario = users.get_user(h.OWNER)

    comprobar("El Owner de Plastika sigue siendo owner",
              propietario["role"] == users.ROLE_OWNER)

    comprobar("Y sigue perteneciendo a Plastika",
              propietario["organization_id"]
              == escenario["plastika"]["id"])

    comprobar("Y puede entrar",
              auth.authenticate(h.OWNER, h.CLAVE_OWNER) is not None)


def test_la_plataforma_no_aparece_entre_los_usuarios_de_plastika():

    preparar()

    nombres = {
        u["username"]
        for u in h.cliente(h.OWNER).get("/api/users").json()["users"]
    }

    comprobar("El operador de plataforma no figura en Plastika",
              "operador" not in nombres, str(sorted(nombres)))

    comprobar("Ni el Owner de la otra organizacion",
              "owner_otra" not in nombres)


def test_la_plataforma_administra_organizaciones():

    preparar()

    cliente = h.cliente("operador")

    comprobar("Lista organizaciones",
              cliente.get("/api/organizations").status_code == 200)

    comprobar("Crea organizaciones",
              cliente.post("/api/organizations",
                           json={"name": "Tercera"}).status_code == 200)

    comprobar("Y las desactiva",
              cliente.post(
                  f"/api/organizations/{escenario['otra']['id']}",
                  json={"active": False}
              ).status_code == 200)


def test_un_owner_no_crea_organizaciones():

    preparar()

    for etiqueta, quien in (("Owner", h.OWNER),
                            ("subadmin", "sub_plastika")):

        cliente = h.cliente(quien)

        comprobar(f"Un {etiqueta} no crea organizaciones",
                  cliente.post("/api/organizations",
                               json={"name": "Colada"}).status_code == 403)


def test_no_hay_forma_de_ascenderse_por_http():

    preparar()

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    comprobar("Ningun endpoint asciende a plataforma",
              "set_platform_owner" not in codigo)

    comprobar("Ni mueve usuarios de organizacion",
              "set_organization(" not in codigo)

    # Intentarlo por las vias que si existen tampoco sirve
    cliente = h.cliente(h.OWNER)

    cliente.post("/api/users", json={
        "username": "intruso", "password": CLAVE,
        "role": "platform_owner"
    })

    colado = users.get_user("intruso")

    comprobar("Un alta con rol de plataforma crea un subadmin normal",
              colado is None or colado["role"] == users.ROLE_SUBADMIN,
              str(colado and colado["role"]))


def test_no_se_asciende_al_unico_owner_de_una_organizacion():

    preparar()

    try:
        users.set_platform_owner(h.OWNER, True)
        ascendido = True

    except users.UserError:
        ascendido = False

    comprobar("Ascender al unico Owner se rechaza", not ascendido)

    comprobar("Plastika conserva su Owner",
              users.get_user(h.OWNER)["role"] == users.ROLE_OWNER)

    comprobar("Y sigue en su organizacion",
              users.get_user(h.OWNER)["organization_id"]
              == escenario["plastika"]["id"])


def test_la_consola_crea_una_cuenta_de_plataforma_aparte():

    preparar()

    codigo = io.open(RAIZ / "platform_admin.py", encoding="utf-8").read()

    comprobar("El script ofrece crear una cuenta nueva",
              "def crear(" in codigo and '"crear"' in codigo)

    comprobar("Explica por que es mejor que promover",
              "sin quien la administre" in codigo
              or "se queda sin" in codigo)

    # Crear una segunda cuenta de plataforma funciona
    users.create_user("operador2", CLAVE, organization_id=None)
    segundo = users.set_platform_owner("operador2", True)

    comprobar("Se puede tener mas de un operador de plataforma",
              segundo["role"] == users.ROLE_PLATFORM_OWNER
              and segundo["organization_id"] is None)

    comprobar("Y Plastika sigue con su Owner intacto",
              users.get_user(h.OWNER)["role"] == users.ROLE_OWNER)


# ==============================
# 9-18. AUDITORIA
# ==============================

def test_las_acciones_sin_equipo_quedan_asociadas_al_usuario():

    preparar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM audit_log")
    conexion.commit()
    conexion.close()

    # Login, logout y cambio de contrasena: ninguno lleva device_id
    cliente = h.cliente()
    cliente.post("/api/auth/login",
                 json={"username": h.OWNER, "password": h.CLAVE_OWNER})

    cliente.post("/api/auth/password", json={
        "current_password": h.CLAVE_OWNER,
        "new_password": "ClaveNuevaDeCorrecciones1!"
    })

    cliente.post("/api/auth/logout")

    plastika = escenario["plastika"]["id"]

    for accion in ("auth.login", "auth.logout", "auth.password_change"):

        filas = [
            f for f in audit.list_audit(limit=500, action=accion)
            if f["username"] == h.OWNER
        ]

        comprobar(f"{accion} queda registrada", len(filas) >= 1)

        if filas:
            comprobar(f"{accion} se asocia a la organizacion del usuario",
                      all(f["organization_id"] == plastika for f in filas),
                      str([f["organization_id"] for f in filas]))


def test_la_creacion_de_usuario_queda_asociada():

    preparar()

    h.cliente(h.OWNER).post("/api/users", json={
        "username": "nuevo_p", "password": CLAVE
    })

    filas = audit.list_audit(limit=500, action="user.created")

    comprobar("El alta queda auditada", len(filas) >= 1)

    comprobar("Con la organizacion de quien la hizo",
              all(f["organization_id"] == escenario["plastika"]["id"]
                  for f in filas))


def test_las_acciones_sobre_equipos_usan_la_organizacion_del_equipo():

    preparar()

    h.cliente(h.OWNER).post(f"/api/devices/{EQUIPO_P}/ping")

    filas = audit.list_audit(limit=500, action="device.ping")

    comprobar("La accion sobre el equipo queda auditada", len(filas) >= 1)

    comprobar("Con la organizacion del equipo",
              all(f["organization_id"] == escenario["plastika"]["id"]
                  for f in filas))


def test_la_auditoria_esta_aislada_por_organizacion():

    preparar()

    # Una accion de cada organizacion
    h.cliente(h.OWNER).post(f"/api/devices/{EQUIPO_P}/ping")
    h.cliente("owner_otra").post(f"/api/devices/{EQUIPO_O}/ping")

    de_plastika = h.cliente(h.OWNER).get("/api/audit").json()["records"]
    de_otra = h.cliente("owner_otra").get("/api/audit").json()["records"]

    plastika = escenario["plastika"]["id"]
    otra = escenario["otra"]["id"]

    comprobar("El Owner de Plastika solo ve lo suyo",
              all(f["organization_id"] == plastika for f in de_plastika),
              str({f["organization_id"] for f in de_plastika}))

    comprobar("Y no aparece ningun equipo ajeno",
              all(f["device_id"] != EQUIPO_O for f in de_plastika))

    comprobar("La otra organizacion solo ve lo suyo",
              all(f["organization_id"] == otra for f in de_otra))


def test_un_subadmin_no_ve_la_auditoria():

    preparar()

    comprobar("La auditoria sigue reservada al Owner",
              h.cliente("sub_plastika").get("/api/audit").status_code
              == 403)


def test_filtrar_por_un_equipo_ajeno_no_devuelve_nada():

    preparar()

    h.cliente("owner_otra").post(f"/api/devices/{EQUIPO_O}/ping")

    respuesta = h.cliente(h.OWNER).get(
        f"/api/audit?device_id={EQUIPO_O}"
    )

    comprobar("Filtrar por un equipo de otra organizacion no devuelve nada",
              respuesta.json()["count"] == 0,
              str(respuesta.json()["count"]))


def test_la_plataforma_consulta_globalmente():

    preparar()

    h.cliente(h.OWNER).post(f"/api/devices/{EQUIPO_P}/ping")
    h.cliente("owner_otra").post(f"/api/devices/{EQUIPO_O}/ping")

    registros = h.cliente("operador").get("/api/audit").json()["records"]

    organizaciones = {f["organization_id"] for f in registros}

    comprobar("El operador de plataforma ve mas de una organizacion",
              len(organizaciones & {escenario["plastika"]["id"],
                                    escenario["otra"]["id"]}) == 2,
              str(organizaciones))


def test_las_acciones_de_plataforma_quedan_sin_organizacion():

    preparar()

    h.cliente("operador").post("/api/organizations",
                               json={"name": "Cuarta"})

    filas = audit.list_audit(limit=500, action="organization.created")

    comprobar("Crear una organizacion queda auditado", len(filas) >= 1)

    comprobar("Sin organizacion: es una accion de plataforma",
              all(f["organization_id"] is None for f in filas),
              str([f["organization_id"] for f in filas]))

    # Y por tanto no la ve ningun Owner de empresa
    de_plastika = h.cliente(h.OWNER).get("/api/audit").json()["records"]

    comprobar("Un Owner no ve las acciones de plataforma",
              all(f["action"] != "organization.created"
                  for f in de_plastika))


def test_el_historico_no_determinable_se_queda_sin_organizacion():

    preparar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM audit_log")

    # Un registro antiguo: equipo que ya no existe y usuario desconocido
    conexion.execute(
        "INSERT INTO audit_log (timestamp, action, device_id, username, "
        "status) VALUES ('2026-01-01', 'device.ping', 'equipo-borrado', "
        "'usuario-que-ya-no-esta', 'requested')"
    )

    # Otro deducible por usuario
    conexion.execute(
        "INSERT INTO audit_log (timestamp, action, username, status) "
        "VALUES ('2026-01-01', 'auth.login', ?, 'success')",
        (h.OWNER,)
    )

    conexion.commit()
    conexion.close()

    audit.backfill_organizations()

    conexion = database.get_connection()
    filas = conexion.execute(
        "SELECT action, organization_id FROM audit_log ORDER BY id"
    ).fetchall()
    conexion.close()

    por_accion = {f["action"]: f["organization_id"] for f in filas}

    comprobar("Lo que no se puede deducir se queda en NULL",
              por_accion.get("device.ping") is None,
              str(por_accion))

    comprobar("Lo que si se puede deducir se rellena",
              por_accion.get("auth.login")
              == escenario["plastika"]["id"])


def test_el_relleno_es_idempotente():

    preparar()

    h.cliente(h.OWNER).post(f"/api/devices/{EQUIPO_P}/ping")

    antes = [
        (f["id"], f["organization_id"])
        for f in audit.list_audit(limit=500)
    ]

    for _ in range(3):
        audit.backfill_organizations()

    despues = [
        (f["id"], f["organization_id"])
        for f in audit.list_audit(limit=500)
    ]

    comprobar("Repetir el relleno no cambia nada", antes == despues)


# ==============================
# 19-23. ALERTAS
# ==============================

def test_las_alertas_estan_aisladas():

    preparar()

    aviso(EQUIPO_P, escenario["plastika"]["id"], "de plastika")
    aviso(EQUIPO_O, escenario["otra"]["id"], "de la otra")

    de_plastika = h.cliente(h.OWNER).get("/api/alerts").json()
    de_otra = h.cliente("owner_otra").get("/api/alerts").json()

    comprobar("Plastika ve su aviso",
              any(a["device_id"] == EQUIPO_P for a in de_plastika))

    comprobar("Y no ve el de la otra",
              all(a["device_id"] != EQUIPO_O for a in de_plastika),
              str([a["device_id"] for a in de_plastika]))

    comprobar("La otra ve el suyo y no el de Plastika",
              all(a["device_id"] != EQUIPO_P for a in de_otra))


def test_no_se_marca_como_leido_un_aviso_ajeno():

    preparar()

    ajeno = aviso(EQUIPO_O, escenario["otra"]["id"])

    respuesta = h.cliente(h.OWNER).post(f"/api/alerts/{ajeno}/read")

    comprobar("Un aviso de otra organizacion responde 404",
              respuesta.status_code == 404, str(respuesta.status_code))

    conexion = database.get_connection()
    leido = conexion.execute(
        "SELECT is_read FROM alerts WHERE id = ?", (ajeno,)
    ).fetchone()[0]
    conexion.close()

    comprobar("Y sigue sin leer", leido == 0)


def test_el_aviso_propio_si_se_marca():

    preparar()

    propio = aviso(EQUIPO_P, escenario["plastika"]["id"])

    respuesta = h.cliente(h.OWNER).post(f"/api/alerts/{propio}/read")

    comprobar("El aviso propio se marca", respuesta.status_code == 200,
              str(respuesta.status_code))


def test_marcar_todas_no_silencia_las_de_otras():

    preparar()

    propio = aviso(EQUIPO_P, escenario["plastika"]["id"])
    ajeno = aviso(EQUIPO_O, escenario["otra"]["id"])

    h.cliente(h.OWNER).post("/api/alerts/read-all")

    conexion = database.get_connection()
    estados = {
        r["id"]: r["is_read"]
        for r in conexion.execute("SELECT id, is_read FROM alerts")
    }
    conexion.close()

    comprobar("El aviso propio queda leido", estados[propio] == 1)

    comprobar("El de la otra organizacion NO se toca",
              estados[ajeno] == 0, str(estados))


def test_los_avisos_nacen_con_su_organizacion():

    codigo = io.open(RAIZ / "backend" / "alerts.py",
                     encoding="utf-8").read()

    bloque = codigo.split("def guardar_alerta(", 1)[1].split("\n\n", 1)[0]

    comprobar("El aviso se guarda con la organizacion del equipo",
              "organization_id" in bloque
              and 'device.get("organization_id")' in codigo)


def test_el_relleno_de_alertas_no_inventa():

    preparar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM alerts")
    conexion.execute(
        "INSERT INTO alerts (device_id, hostname, type, message, "
        "created_at) VALUES ('equipo-borrado', 'PC', 'x', 'y', '2026-01-01')"
    )
    conexion.execute(
        "INSERT INTO alerts (device_id, hostname, type, message, "
        "created_at) VALUES (?, 'PC', 'x', 'y', '2026-01-01')",
        (EQUIPO_P,)
    )
    conexion.commit()
    conexion.close()

    alerts.backfill_alert_organizations()

    conexion = database.get_connection()
    filas = {
        r["device_id"]: r["organization_id"]
        for r in conexion.execute(
            "SELECT device_id, organization_id FROM alerts"
        )
    }
    conexion.close()

    comprobar("Un aviso de un equipo que ya no existe se queda en NULL",
              filas.get("equipo-borrado") is None, str(filas))

    comprobar("Y uno de un equipo conocido se rellena",
              filas.get(EQUIPO_P) == escenario["plastika"]["id"])


# ==============================
# 24-34. ENROLLMENT
# ==============================

def test_la_credencial_determina_la_organizacion():

    preparar()

    _, valor_p = enrollment.create_token(escenario["plastika"]["id"],
                                         label="Plastika")
    _, valor_o = enrollment.create_token(escenario["otra"]["id"],
                                         label="Otra")

    cliente = h.cliente()

    alta_p = cliente.post(
        "/api/devices/register",
        json={"hostname": "NUEVO-P", "operating_system": "Windows",
              "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": valor_p}
    )

    alta_o = cliente.post(
        "/api/devices/register",
        json={"hostname": "NUEVO-O", "operating_system": "Windows",
              "ip_address": "10.0.0.2"},
        headers={"X-Agent-Token": valor_o}
    )

    comprobar("El alta con credencial de Plastika funciona",
              alta_p.status_code == 200, str(alta_p.status_code))

    comprobar("Y la de la otra tambien",
              alta_o.status_code == 200, str(alta_o.status_code))

    if alta_p.status_code == 200 and alta_o.status_code == 200:

        from backend.devices import get_device_organization

        comprobar("El equipo entra en Plastika",
                  get_device_organization(alta_p.json()["device_id"])
                  == escenario["plastika"]["id"])

        comprobar("Y el otro en la otra organizacion",
                  get_device_organization(alta_o.json()["device_id"])
                  == escenario["otra"]["id"])

        comprobar("No se mezclan",
                  alta_p.json()["device_id"] != alta_o.json()["device_id"])


def test_el_agent_no_elige_su_organizacion():

    preparar()

    _, valor = enrollment.create_token(escenario["plastika"]["id"])

    alta = h.cliente().post(
        "/api/devices/register",
        json={
            "hostname": "INTRUSO", "operating_system": "Windows",
            "ip_address": "10.0.0.9",
            # Intento de colarse en la otra organizacion
            "organization_id": escenario["otra"]["id"]
        },
        headers={"X-Agent-Token": valor}
    )

    comprobar("El alta funciona", alta.status_code == 200)

    if alta.status_code == 200:

        from backend.devices import get_device_organization

        comprobar(
            "Pero el organization_id enviado se ignora por completo",
            get_device_organization(alta.json()["device_id"])
            == escenario["plastika"]["id"]
        )

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    bloque = codigo.split("def register(", 1)[1].split("\n@app.", 1)[0]

    comprobar("El endpoint no lee ningun organization_id del cuerpo",
              "data.organization_id" not in bloque)


def test_una_credencial_invalida_se_rechaza():

    preparar()

    respuesta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "X", "operating_system": "Windows",
              "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": "rae_credencial-inventada"}
    )

    comprobar("Una credencial inventada no da de alta nada",
              respuesta.status_code in (401, 403),
              str(respuesta.status_code))


def test_una_credencial_revocada_se_rechaza():

    preparar()

    ficha, valor = enrollment.create_token(escenario["plastika"]["id"])

    enrollment.revoke_token(ficha["id"])

    respuesta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "X", "operating_system": "Windows",
              "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": valor}
    )

    comprobar("Una credencial revocada no sirve",
              respuesta.status_code in (401, 403),
              str(respuesta.status_code))

    comprobar("La credencial queda como no utilizable",
              enrollment.get_token(ficha["id"])["usable"] is False)


def test_una_credencial_caducada_se_rechaza():

    preparar()

    ficha, valor = enrollment.create_token(escenario["plastika"]["id"])

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE enrollment_tokens SET expires_at = ? WHERE id = ?",
        (int(time.time()) - 10, ficha["id"])
    )
    conexion.commit()
    conexion.close()

    comprobar("Una credencial caducada no resuelve organizacion",
              enrollment.organization_for_token(valor) is None)

    comprobar("Y se marca como caducada",
              enrollment.get_token(ficha["id"])["expired"] is True)


def test_la_credencial_solo_se_guarda_hasheada():

    preparar()

    ficha, valor = enrollment.create_token(escenario["plastika"]["id"])

    conexion = database.get_connection()
    guardado = conexion.execute(
        "SELECT token_hash FROM enrollment_tokens WHERE id = ?",
        (ficha["id"],)
    ).fetchone()[0]
    conexion.close()

    comprobar("El valor en claro no esta en la base",
              valor not in guardado)

    comprobar("Lo guardado es un SHA-256", len(guardado) == 64)

    comprobar("La ficha publica no expone el hash",
              "token_hash" not in ficha)


def test_la_api_no_vuelve_a_mostrar_el_valor():

    preparar()

    cliente = h.cliente(h.OWNER)

    creada = cliente.post("/api/enrollment-tokens",
                          json={"label": "Equipos nuevos"})

    comprobar("El Owner crea credenciales de su organizacion",
              creada.status_code == 200, str(creada.status_code))

    valor = creada.json().get("value")

    comprobar("El valor se entrega una vez", bool(valor))

    listado = cliente.get("/api/enrollment-tokens").json()

    comprobar("Y no vuelve a aparecer en el listado",
              valor not in listado.__str__())

    comprobar("Ni el hash",
              all("token_hash" not in t for t in listado["tokens"]))


def test_las_credenciales_estan_aisladas():

    preparar()

    enrollment.create_token(escenario["otra"]["id"], label="de la otra")

    propias = h.cliente(h.OWNER).get("/api/enrollment-tokens").json()

    comprobar("Un Owner solo ve las de su organizacion",
              all(t["organization_id"] == escenario["plastika"]["id"]
                  for t in propias["tokens"]),
              str([t["organization_id"] for t in propias["tokens"]]))

    ajena = h.cliente(h.OWNER).get(
        f"/api/enrollment-tokens?organization_id={escenario['otra']['id']}"
    )

    comprobar("Pedir las de otra organizacion se rechaza",
              ajena.status_code == 403, str(ajena.status_code))


def test_un_subadmin_no_administra_credenciales():

    preparar()

    cliente = h.cliente("sub_plastika")

    comprobar("Un subadmin no lista credenciales",
              cliente.get("/api/enrollment-tokens").status_code == 403)

    comprobar("Ni las crea",
              cliente.post("/api/enrollment-tokens",
                           json={}).status_code == 403)


def test_el_agent_token_compartido_ya_no_da_de_alta():
    """
    Cambio de politica del bloque de cierre.

    Cuando se introdujeron las credenciales por organizacion, el
    AGENT_TOKEN compartido se mantuvo un tiempo por compatibilidad.
    Ya no: era una puerta global, presente en el .env de cada equipo
    administrado, con la que cualquiera que lo tuviera podia meter
    equipos en el sistema. Los Agents ya enrolados no se ven afectados
    porque se autentican con su token individual.
    """

    preparar()

    respuesta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "ANTIGUO", "operating_system": "Windows",
              "ip_address": "10.0.0.5"},
        headers={"X-Agent-Token": auth.AGENT_TOKEN
                 or "token-compartido-de-prueba"}
    )

    comprobar("El AGENT_TOKEN compartido ya no registra equipos nuevos",
              respuesta.status_code in (401, 403),
              str(respuesta.status_code))

    comprobar("La unica via de alta es una credencial de organizacion",
              enrollment.organization_for_token(auth.AGENT_TOKEN) is None)


def test_los_agents_existentes_conservan_identidad_y_token():

    preparar()

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET agent_token_hash = 'hash-existente', "
        "agent_token_active = 1 WHERE device_id = ?", (EQUIPO_P,)
    )
    conexion.commit()
    conexion.close()

    # Un re-registro de un Agent ya enrolado
    from backend.auth import hash_agent_token

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET agent_token_hash = ? WHERE device_id = ?",
        (hash_agent_token("token-individual"), EQUIPO_P)
    )
    conexion.commit()
    conexion.close()

    respuesta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-PLASTIKA", "operating_system": "Windows",
              "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": "token-individual"}
    )

    comprobar("Un Agent ya enrolado se re-registra sin problema",
              respuesta.status_code == 200, str(respuesta.status_code))

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT device_id, agent_token_hash, organization_id "
        "FROM devices WHERE device_id = ?", (EQUIPO_P,)
    ).fetchone()
    conexion.close()

    comprobar("Conserva su device_id", fila["device_id"] == EQUIPO_P)

    comprobar("Conserva su token",
              fila["agent_token_hash"] == hash_agent_token(
                  "token-individual"
              ))

    comprobar("Y su organizacion",
              fila["organization_id"] == escenario["plastika"]["id"])


def test_el_heartbeat_sigue_funcionando():

    preparar()

    from backend.auth import hash_agent_token

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET agent_token_hash = ?, agent_token_active = 1 "
        "WHERE device_id = ?",
        (hash_agent_token("token-hb"), EQUIPO_P)
    )
    conexion.commit()
    conexion.close()

    respuesta = h.cliente().post(
        "/api/devices/heartbeat",
        json={"device_id": EQUIPO_P, "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": "token-hb"}
    )

    comprobar("El heartbeat con token individual sigue valiendo",
              respuesta.status_code == 200, str(respuesta.status_code))


def test_el_alta_respeta_el_limite_del_plan():

    preparar()

    orgs.update_organization(escenario["plastika"]["id"],
                             plan=orgs.PLAN_FREE)

    _, valor = enrollment.create_token(escenario["plastika"]["id"])

    # Free permite 2 equipos y Plastika ya tiene 1; el segundo entra
    primero = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "A", "operating_system": "W", "ip_address": "1"},
        headers={"X-Agent-Token": valor}
    )

    segundo = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "B", "operating_system": "W", "ip_address": "2"},
        headers={"X-Agent-Token": valor}
    )

    comprobar("El equipo que cabe se da de alta",
              primero.status_code == 200, str(primero.status_code))

    comprobar("El que supera el limite del plan se rechaza",
              segundo.status_code == 409, str(segundo.status_code))


# ==============================
# Migracion
# ==============================

def test_las_migraciones_son_idempotentes():

    preparar()

    conexion = database.get_connection()
    antes = {
        t: conexion.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("devices", "users", "organizations", "audit_log",
                  "alerts", "enrollment_tokens")
    }
    conexion.close()

    for _ in range(3):
        database.init_db()
        users.ensure_owner_migrated()
        orgs.ensure_default_organization()
        audit.backfill_organizations()
        alerts.backfill_alert_organizations()

    conexion = database.get_connection()
    despues = {
        t: conexion.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in antes
    }
    conexion.close()

    comprobar("Repetir todas las migraciones no duplica nada",
              antes == despues, f"{antes} -> {despues}")


def test_no_hay_secretos_en_la_auditoria():

    preparar()

    cliente = h.cliente(h.OWNER)

    cliente.post("/api/enrollment-tokens", json={"label": "X"})
    cliente.post("/api/users", json={"username": "x_user",
                                     "password": CLAVE})

    todo = " ".join(str(r["details"]) for r in audit.list_audit(limit=500))

    for secreto in (CLAVE, h.CLAVE_OWNER, "pbkdf2_sha256", "rae_"):
        comprobar(f"La auditoria no guarda {secreto[:14]}",
                  secreto not in todo)


# ==============================

def main():

    pruebas = [
        test_la_plataforma_no_pertenece_a_ninguna_organizacion,
        test_plastika_conserva_su_owner,
        test_la_plataforma_no_aparece_entre_los_usuarios_de_plastika,
        test_la_plataforma_administra_organizaciones,
        test_un_owner_no_crea_organizaciones,
        test_no_hay_forma_de_ascenderse_por_http,
        test_no_se_asciende_al_unico_owner_de_una_organizacion,
        test_la_consola_crea_una_cuenta_de_plataforma_aparte,
        test_las_acciones_sin_equipo_quedan_asociadas_al_usuario,
        test_la_creacion_de_usuario_queda_asociada,
        test_las_acciones_sobre_equipos_usan_la_organizacion_del_equipo,
        test_la_auditoria_esta_aislada_por_organizacion,
        test_un_subadmin_no_ve_la_auditoria,
        test_filtrar_por_un_equipo_ajeno_no_devuelve_nada,
        test_la_plataforma_consulta_globalmente,
        test_las_acciones_de_plataforma_quedan_sin_organizacion,
        test_el_historico_no_determinable_se_queda_sin_organizacion,
        test_el_relleno_es_idempotente,
        test_las_alertas_estan_aisladas,
        test_no_se_marca_como_leido_un_aviso_ajeno,
        test_el_aviso_propio_si_se_marca,
        test_marcar_todas_no_silencia_las_de_otras,
        test_los_avisos_nacen_con_su_organizacion,
        test_el_relleno_de_alertas_no_inventa,
        test_la_credencial_determina_la_organizacion,
        test_el_agent_no_elige_su_organizacion,
        test_una_credencial_invalida_se_rechaza,
        test_una_credencial_revocada_se_rechaza,
        test_una_credencial_caducada_se_rechaza,
        test_la_credencial_solo_se_guarda_hasheada,
        test_la_api_no_vuelve_a_mostrar_el_valor,
        test_las_credenciales_estan_aisladas,
        test_un_subadmin_no_administra_credenciales,
        test_el_agent_token_compartido_ya_no_da_de_alta,
        test_los_agents_existentes_conservan_identidad_y_token,
        test_el_heartbeat_sigue_funcionando,
        test_el_alta_respeta_el_limite_del_plan,
        test_las_migraciones_son_idempotentes,
        test_no_hay_secretos_en_la_auditoria
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
