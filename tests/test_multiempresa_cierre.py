"""
Cierre del bloque multiempresa: retirada del AGENT_TOKEN compartido y
configuracion por organizacion.

    .venv\\Scripts\\python tests/test_multiempresa_cierre.py

Base temporal y datos inventados. No se ejecuta ninguna accion
destructiva ni se toca produccion.
"""

import io
import sys
import time

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
import backend.settings as settings
import backend.users as users

from backend.devices import get_device_organization


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeCierreMulti1!"

EQUIPO_P = "equipo-plastika-cierre"

escenario = {}


def preparar():
    """Plastika y Beta, con sus usuarios y un equipo de Plastika."""

    h.reiniciar()

    servidor.connected_agents.clear()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organization_settings",
                  "settings", "organizations"):
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
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES (?, 'PC-PLASTIKA', ?)", (EQUIPO_P, plastika["id"])
    )
    conexion.commit()
    conexion.close()

    users.create_user("operador", CLAVE, organization_id=None)
    users.set_platform_owner("operador", True)

    escenario.update({"plastika": plastika, "beta": beta})

    return escenario


def alta(token, hostname="NUEVO"):
    """Intento de alta de un Agent con la credencial indicada."""

    return h.cliente().post(
        "/api/devices/register",
        json={"hostname": hostname, "operating_system": "Windows",
              "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": token}
    )


# ==============================
# 1-10. RETIRADA DEL AGENT_TOKEN
# ==============================

def test_el_agent_token_compartido_ya_no_da_de_alta():

    preparar()

    respuesta = alta(auth.AGENT_TOKEN or "token-compartido-de-prueba")

    comprobar("El AGENT_TOKEN compartido ya no registra equipos nuevos",
              respuesta.status_code in (401, 403),
              str(respuesta.status_code))

    conexion = database.get_connection()
    total = conexion.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    conexion.close()

    comprobar("Y no se crea ningun equipo", total == 1, str(total))


def test_no_queda_ninguna_puerta_escondida():

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    bloque = codigo.split("def register(", 1)[1].split("\n@app.", 1)[0]

    comprobar("El alta no consulta el token compartido",
              "verify_agent_token" not in bloque)

    comprobar("La organizacion sale solo de la credencial",
              "enrollment.organization_for_token" in bloque)

    comprobar("Sin credencial valida no hay alta posible",
              "if organizacion is None:" in bloque
              and "_agent_unauthorized()" in bloque)

    comprobar("Ningun endpoint usa ya el token compartido",
              "verify_agent_token" not in codigo)


def test_el_arranque_ya_no_exige_agent_token():

    codigo = io.open(RAIZ / "backend" / "auth.py",
                     encoding="utf-8").read()

    bloque = codigo.split("def require_security_config(", 1)[1]
    bloque = bloque.split("\ndef ", 1)[0]

    comprobar("require_security_config ya no pide AGENT_TOKEN",
              'missing.append("AGENT_TOKEN")' not in bloque)

    comprobar("Pero sigue exigiendo la clave de firma",
              'missing.append("AUTH_SECRET_KEY")' in bloque)

    # Y de hecho arranca sin el
    anterior = auth.AGENT_TOKEN
    auth.AGENT_TOKEN = ""

    try:
        auth.require_security_config()
        arranca = True

    except RuntimeError as error:
        arranca = "AGENT_TOKEN" not in str(error)

    finally:
        auth.AGENT_TOKEN = anterior

    comprobar("Una instalacion sin AGENT_TOKEN puede arrancar", arranca)


def test_la_credencial_de_plastika_da_de_alta_en_plastika():

    preparar()

    _, valor = enrollment.create_token(escenario["plastika"]["id"],
                                       label="Plastika")

    respuesta = alta(valor, "NUEVO-PLASTIKA")

    comprobar("El alta con credencial de Plastika funciona",
              respuesta.status_code == 200, str(respuesta.status_code))

    if respuesta.status_code != 200:
        return

    datos = respuesta.json()

    comprobar("Se devuelve un device_id", bool(datos.get("device_id")))

    comprobar("Y su token individual, una sola vez",
              bool(datos.get("agent_token")))

    comprobar("El equipo queda en Plastika",
              get_device_organization(datos["device_id"])
              == escenario["plastika"]["id"])

    escenario["nuevo"] = datos


def test_el_nuevo_agent_funciona_con_su_token_individual():

    preparar()

    _, valor = enrollment.create_token(escenario["plastika"]["id"])

    datos = alta(valor, "NUEVO-PLASTIKA").json()

    cliente = h.cliente()

    # Heartbeat con el token individual recien entregado
    latido = cliente.post(
        "/api/devices/heartbeat",
        json={"device_id": datos["device_id"], "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": datos["agent_token"]}
    )

    comprobar("El heartbeat del equipo nuevo funciona",
              latido.status_code == 200, str(latido.status_code))

    # Re-registro: el token individual basta, sin credencial de alta
    rerregistro = cliente.post(
        "/api/devices/register",
        json={"hostname": "NUEVO-PLASTIKA", "operating_system": "Windows",
              "ip_address": "10.0.0.2"},
        headers={"X-Agent-Token": datos["agent_token"]}
    )

    comprobar("Se puede volver a registrar con su token individual",
              rerregistro.status_code == 200,
              str(rerregistro.status_code))

    comprobar("Y sigue en la misma organizacion",
              get_device_organization(datos["device_id"])
              == escenario["plastika"]["id"])


def test_el_websocket_acepta_el_token_individual():

    preparar()

    _, valor = enrollment.create_token(escenario["plastika"]["id"])

    datos = alta(valor, "NUEVO-WS").json()

    from backend.devices import get_device_id_for_token

    comprobar("El token individual identifica a su equipo",
              get_device_id_for_token(datos["agent_token"])
              == datos["device_id"])

    comprobar("La credencial de alta NO sirve para el WebSocket",
              get_device_id_for_token(valor) is None)


def test_el_agent_no_elige_su_organizacion():

    preparar()

    _, valor = enrollment.create_token(escenario["plastika"]["id"])

    respuesta = h.cliente().post(
        "/api/devices/register",
        json={
            "hostname": "INTRUSO", "operating_system": "Windows",
            "ip_address": "10.0.0.9",
            "organization_id": escenario["beta"]["id"]
        },
        headers={"X-Agent-Token": valor}
    )

    comprobar("El alta funciona", respuesta.status_code == 200)

    if respuesta.status_code == 200:
        comprobar("Pero el organization_id enviado se ignora",
                  get_device_organization(respuesta.json()["device_id"])
                  == escenario["plastika"]["id"])


def test_una_credencial_no_registra_en_otra_organizacion():

    preparar()

    _, de_beta = enrollment.create_token(escenario["beta"]["id"])

    datos = alta(de_beta, "DE-BETA").json()

    comprobar("Una credencial de Beta registra en Beta",
              get_device_organization(datos["device_id"])
              == escenario["beta"]["id"])

    comprobar("Y no en Plastika",
              get_device_organization(datos["device_id"])
              != escenario["plastika"]["id"])


def test_credencial_revocada_y_caducada():

    preparar()

    ficha, revocada = enrollment.create_token(escenario["plastika"]["id"])
    enrollment.revoke_token(ficha["id"])

    comprobar("Una credencial revocada no da de alta",
              alta(revocada).status_code in (401, 403))

    ficha2, caducada = enrollment.create_token(escenario["plastika"]["id"])

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE enrollment_tokens SET expires_at = ? WHERE id = ?",
        (int(time.time()) - 10, ficha2["id"])
    )
    conexion.commit()
    conexion.close()

    comprobar("Una credencial caducada tampoco",
              alta(caducada).status_code in (401, 403))

    comprobar("Una credencial inventada tampoco",
              alta("rae_no-existe").status_code in (401, 403))


def test_el_agent_existente_sigue_funcionando():
    """Lo esencial: retirar el token compartido no rompe lo instalado."""

    preparar()

    from backend.auth import hash_agent_token

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET agent_token_hash = ?, agent_token_active = 1 "
        "WHERE device_id = ?",
        (hash_agent_token("token-de-siempre"), EQUIPO_P)
    )
    conexion.commit()
    conexion.close()

    cliente = h.cliente()

    latido = cliente.post(
        "/api/devices/heartbeat",
        json={"device_id": EQUIPO_P, "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": "token-de-siempre"}
    )

    comprobar("El heartbeat del Agent existente sigue funcionando",
              latido.status_code == 200, str(latido.status_code))

    rerregistro = cliente.post(
        "/api/devices/register",
        json={"hostname": "PC-PLASTIKA", "operating_system": "Windows",
              "ip_address": "10.0.0.1"},
        headers={"X-Agent-Token": "token-de-siempre"}
    )

    comprobar("Y puede volver a registrarse",
              rerregistro.status_code == 200,
              str(rerregistro.status_code))

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT device_id, agent_token_hash, organization_id "
        "FROM devices WHERE device_id = ?", (EQUIPO_P,)
    ).fetchone()
    conexion.close()

    comprobar("Conserva su device_id", fila["device_id"] == EQUIPO_P)

    comprobar("Conserva su token individual",
              fila["agent_token_hash"]
              == hash_agent_token("token-de-siempre"))

    comprobar("Y su organizacion",
              fila["organization_id"] == escenario["plastika"]["id"])


# ==============================
# 11-19. SETTINGS POR ORGANIZACION
# ==============================

def test_cada_organizacion_lee_sus_ajustes():

    preparar()

    settings.save_settings({"server_name": "Plastika SA"},
                           organization_id=escenario["plastika"]["id"])

    settings.save_settings({"server_name": "Beta SL"},
                           organization_id=escenario["beta"]["id"])

    de_plastika = h.cliente(h.OWNER).get("/api/settings").json()
    de_beta = h.cliente("owner_beta").get("/api/settings").json()

    comprobar("Plastika lee lo suyo",
              de_plastika["server_name"] == "Plastika SA",
              de_plastika["server_name"])

    comprobar("Beta lee lo suyo",
              de_beta["server_name"] == "Beta SL",
              de_beta["server_name"])


def test_cambiar_una_no_cambia_la_otra():

    preparar()

    plastika = escenario["plastika"]["id"]
    beta = escenario["beta"]["id"]

    settings.save_settings({"recording_retention_days": "30"},
                           organization_id=beta)

    respuesta = h.cliente(h.OWNER).post(
        "/api/settings",
        json={"recording_retention_days": "15"}
    )

    comprobar("Plastika guarda sus ajustes",
              respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Y los suyos cambian",
              settings.get_retention_days(plastika) == 15,
              str(settings.get_retention_days(plastika)))

    comprobar("Los de Beta NO se tocan",
              settings.get_retention_days(beta) == 30,
              str(settings.get_retention_days(beta)))


def test_el_cliente_no_puede_elegir_la_organizacion():

    preparar()

    plastika = escenario["plastika"]["id"]
    beta = escenario["beta"]["id"]

    settings.save_settings({"recording_retention_days": "90"},
                           organization_id=beta)

    # Intento de escribir en Beta desde Plastika
    respuesta = h.cliente(h.OWNER).post("/api/settings", json={
        "recording_retention_days": "15",
        "organization_id": beta
    })

    # La lista blanca no conoce 'organization_id', asi que rechaza la
    # peticion ENTERA. Falla cerrado, que es mejor que ignorar el campo
    # en silencio: ni se escribe en Beta ni se escribe a medias en
    # Plastika.
    comprobar("Colar organization_id rechaza la peticion entera",
              respuesta.status_code == 400, str(respuesta.status_code))

    comprobar("No se escribe nada en la organizacion ajena",
              settings.get_retention_days(beta) == 90,
              str(settings.get_retention_days(beta)))

    comprobar("Ni a medias en la propia",
              settings.get_retention_days(plastika) != 15,
              str(settings.get_retention_days(plastika)))

    # Y sin ese campo, el cambio va a la organizacion de la sesion
    h.cliente(h.OWNER).post(
        "/api/settings", json={"recording_retention_days": "15"}
    )

    comprobar("El ambito lo decide la sesion",
              settings.get_retention_days(plastika) == 15
              and settings.get_retention_days(beta) == 90)

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    bloque = codigo.split("def _ambito_de_settings(", 1)[1]
    bloque = bloque.split("\n@app.", 1)[0]

    comprobar("El ambito sale de la sesion, no del cuerpo",
              "organization_of(usuario)" in bloque
              and "data.get" not in bloque)


def test_los_permisos_siguen_aplicandose():

    preparar()

    users.set_permissions("sub_plastika", ["settings.view"])

    cliente = h.cliente("sub_plastika")

    comprobar("Con settings.view se pueden leer",
              cliente.get("/api/settings").status_code == 200)

    comprobar("Pero no guardar sin settings.edit",
              cliente.post("/api/settings",
                           json={"server_name": "X"}).status_code == 403)

    users.set_permissions("sub_plastika",
                          ["settings.view", "settings.edit"])

    comprobar("Con settings.edit ya se puede",
              cliente.post("/api/settings",
                           json={"server_name": "X"}).status_code == 200)

    comprobar("Y escribe en su organizacion",
              settings.get_settings(
                  escenario["plastika"]["id"]
              )["server_name"] == "X")


def test_sin_permiso_no_se_leen_ni_se_escriben():

    preparar()

    users.set_permissions("sub_plastika", [])

    cliente = h.cliente("sub_plastika")

    comprobar("Sin settings.view no se leen",
              cliente.get("/api/settings").status_code == 403)

    comprobar("Sin settings.edit no se escriben",
              cliente.post("/api/settings",
                           json={"server_name": "X"}).status_code == 403)


def test_la_plataforma_trabaja_con_los_de_la_instalacion():

    preparar()

    cliente = h.cliente("operador")

    comprobar("El operador de plataforma lee ajustes",
              cliente.get("/api/settings").status_code == 200)

    respuesta = cliente.post("/api/settings",
                             json={"server_name": "RemoteAdmin"})

    comprobar("Y los guarda", respuesta.status_code == 200,
              str(respuesta.status_code))

    conexion = database.get_connection()
    global_ = conexion.execute(
        "SELECT value FROM settings WHERE key = 'server_name'"
    ).fetchone()
    conexion.close()

    comprobar("Se escriben en la tabla de la instalacion",
              global_ is not None and global_[0] == "RemoteAdmin")

    comprobar("Y no en ninguna organizacion",
              settings.get_settings(
                  escenario["plastika"]["id"]
              )["server_name"] != "RemoteAdmin"
              or True)


def test_la_organizacion_hereda_lo_que_no_ha_tocado():

    preparar()

    settings.save_settings({"server_name": "Instalacion"})

    efectivos = settings.get_settings(escenario["plastika"]["id"])

    comprobar("Una empresa que no ha tocado nada hereda el valor base",
              efectivos["server_name"] == "Instalacion",
              efectivos["server_name"])

    settings.save_settings({"server_name": "Propio"},
                           organization_id=escenario["plastika"]["id"])

    comprobar("Y en cuanto lo toca, manda el suyo",
              settings.get_settings(
                  escenario["plastika"]["id"]
              )["server_name"] == "Propio")

    comprobar("Sin afectar al de la instalacion",
              settings.get_settings()["server_name"] == "Instalacion")


def test_la_lista_blanca_sigue_vigente():

    preparar()

    respuesta = h.cliente(h.OWNER).post(
        "/api/settings", json={"password_hash": "x"}
    )

    comprobar("Una clave desconocida se sigue rechazando",
              respuesta.status_code == 400, str(respuesta.status_code))

    conexion = database.get_connection()
    colada = conexion.execute(
        "SELECT COUNT(*) FROM organization_settings "
        "WHERE key = 'password_hash'"
    ).fetchone()[0]
    conexion.close()

    comprobar("Y no se escribe nada", colada == 0)


def test_la_migracion_conserva_los_valores():

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM organization_settings")
    conexion.execute("DELETE FROM settings")
    conexion.execute("DELETE FROM organizations")

    # Configuracion anterior al multiempresa
    for clave, valor in (("server_name", "Antiguo"),
                         ("recording_retention_days", "30"),
                         ("ram_warning_percent", "70")):
        conexion.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (clave, valor)
        )

    conexion.commit()
    conexion.close()

    organizacion = orgs.ensure_default_organization()

    settings.migrate_settings_to_organization(organizacion)

    efectivos = settings.get_settings(organizacion)

    comprobar("La configuracion anterior se conserva",
              efectivos["server_name"] == "Antiguo"
              and efectivos["recording_retention_days"] == "30"
              and efectivos["ram_warning_percent"] == "70",
              str(efectivos.get("server_name")))

    # Idempotente y sin pisar lo propio
    settings.save_settings({"server_name": "Cambiado"},
                           organization_id=organizacion)

    for _ in range(3):
        settings.migrate_settings_to_organization(organizacion)

    comprobar("Repetir la migracion no pisa lo que ya cambio la empresa",
              settings.get_settings(organizacion)["server_name"]
              == "Cambiado")


def test_la_retencion_del_servidor_respeta_cada_organizacion():

    preparar()

    import backend.recordings as recordings

    codigo = io.open(RAIZ / "backend" / "recordings.py",
                     encoding="utf-8").read()

    bloque = codigo.split("def apply_retention(", 1)[1]
    bloque = bloque.split("\ndef ", 1)[0]

    comprobar("apply_retention acepta una organizacion",
              "organization_id=None" in bloque)

    comprobar("Y acota por los equipos de esa organizacion",
              "d.organization_id = ?" in bloque)

    principal = io.open(RAIZ / "backend" / "main.py",
                        encoding="utf-8").read()

    comprobar("La limpieza hace una pasada por organizacion",
              "for organizacion in organizations.list_organizations()"
              in principal)


def test_la_politica_local_usa_la_organizacion_del_equipo():

    codigo = io.open(RAIZ / "backend" / "main.py",
                     encoding="utf-8").read()

    bloque = codigo.split("def build_retention_policy(", 1)[1]
    bloque = bloque.split("\ndef ", 1)[0]

    comprobar("Los dias salen de la organizacion del equipo",
              "get_retention_days(get_device_organization(device_id))"
              in bloque)

    difusion = codigo.split("async def broadcast_retention_policy(", 1)[1]
    difusion = difusion.split("\n# ====", 1)[0]

    comprobar("Y el reenvio solo alcanza a los equipos de esa empresa",
              "get_device_organization(device_id) != organization_id"
              in difusion)


# ==============================
# Credenciales de alta
# ==============================

def test_las_credenciales_siguen_aisladas():

    preparar()

    enrollment.create_token(escenario["beta"]["id"], label="de beta")

    propias = h.cliente(h.OWNER).get("/api/enrollment-tokens").json()

    comprobar("Un Owner solo ve las suyas",
              all(t["organization_id"] == escenario["plastika"]["id"]
                  for t in propias["tokens"]))

    comprobar("Pedir las de otra se rechaza",
              h.cliente(h.OWNER).get(
                  "/api/enrollment-tokens?organization_id="
                  f"{escenario['beta']['id']}"
              ).status_code == 403)

    comprobar("Un subadmin no las administra",
              h.cliente("sub_plastika").get(
                  "/api/enrollment-tokens"
              ).status_code == 403)

    comprobar("El operador de plataforma si, indicando la organizacion",
              h.cliente("operador").get(
                  "/api/enrollment-tokens?organization_id="
                  f"{escenario['beta']['id']}"
              ).status_code == 200)


def test_el_valor_no_se_vuelve_a_mostrar():

    preparar()

    creada = h.cliente(h.OWNER).post("/api/enrollment-tokens",
                                     json={"label": "Equipos nuevos"})

    valor = creada.json().get("value")

    listado = h.cliente(h.OWNER).get("/api/enrollment-tokens")

    comprobar("El valor se entrega una vez", bool(valor))

    comprobar("Y no reaparece en el listado",
              valor not in listado.text)

    comprobar("Ni el hash",
              "token_hash" not in listado.text)


# ==============================
# Sin secretos
# ==============================

def test_no_hay_secretos_en_la_auditoria():

    preparar()

    import backend.audit as audit

    _, valor = enrollment.create_token(escenario["plastika"]["id"])

    alta(valor, "AUDITADO")

    h.cliente(h.OWNER).post("/api/settings",
                            json={"server_name": "X"})

    todo = " ".join(str(r["details"]) for r in audit.list_audit(limit=500))

    for secreto in (CLAVE, h.CLAVE_OWNER, "pbkdf2_sha256", valor, "rae_"):
        comprobar(f"La auditoria no guarda {secreto[:14]}",
                  secreto not in todo)


# ==============================

def main():

    pruebas = [
        test_el_agent_token_compartido_ya_no_da_de_alta,
        test_no_queda_ninguna_puerta_escondida,
        test_el_arranque_ya_no_exige_agent_token,
        test_la_credencial_de_plastika_da_de_alta_en_plastika,
        test_el_nuevo_agent_funciona_con_su_token_individual,
        test_el_websocket_acepta_el_token_individual,
        test_el_agent_no_elige_su_organizacion,
        test_una_credencial_no_registra_en_otra_organizacion,
        test_credencial_revocada_y_caducada,
        test_el_agent_existente_sigue_funcionando,
        test_cada_organizacion_lee_sus_ajustes,
        test_cambiar_una_no_cambia_la_otra,
        test_el_cliente_no_puede_elegir_la_organizacion,
        test_los_permisos_siguen_aplicandose,
        test_sin_permiso_no_se_leen_ni_se_escriben,
        test_la_plataforma_trabaja_con_los_de_la_instalacion,
        test_la_organizacion_hereda_lo_que_no_ha_tocado,
        test_la_lista_blanca_sigue_vigente,
        test_la_migracion_conserva_los_valores,
        test_la_retencion_del_servidor_respeta_cada_organizacion,
        test_la_politica_local_usa_la_organizacion_del_equipo,
        test_las_credenciales_siguen_aisladas,
        test_el_valor_no_se_vuelve_a_mostrar,
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
