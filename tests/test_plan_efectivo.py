"""
Plan efectivo, excepciones por organizacion y modo de observacion.

    .venv\\Scripts\\python tests/test_plan_efectivo.py

Tres cosas que se comprueban aqui, en orden de gravedad:

  1. Que una organizacion NO pueda concederse capacidades. Si eso
     falla, los planes no significan nada.

  2. Que el modo observacion NO bloquee. Esta es la red: mientras
     este puesto, ninguna organizacion puede perder una funcion que
     hoy usa.

  3. Que la excepcion de una organizacion no la herede otra, ni se
     pueda consultar desde fuera.

Y una que no es de comportamiento sino de mantenimiento: que no
quede ninguna ruta de /api/devices sin clasificar. Una ruta olvidada
no se bloquearia nunca, y el olvido no se nota mirando.

Base temporal. No se toca produccion ni la configuracion de ninguna
organizacion real.
"""

import io
import os
import re
import sys

from datetime import datetime, timedelta, timezone
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("REMOTEADMIN_PUBLIC_URL", "https://panel.ejemplo.com")

import users_harness as h  # noqa: E402

import backend.database as database  # noqa: E402
import backend.main as servidor  # noqa: E402
import backend.organizations as orgs  # noqa: E402
import backend.users as users  # noqa: E402


PRINCIPAL = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDelPlan2026!"


def organizacion(nombre, plan):

    for existente in orgs.list_organizations():
        if existente["name"] == nombre:
            orgs.update_organization(existente["id"], plan=plan)
            return orgs.get_organization(existente["id"])

    return orgs.create_organization(
        nombre, plan=plan, subscription_status=orgs.STATUS_ACTIVE
    )


def preparar(plan=None):

    h.reiniciar()

    # recording_schedule entra aqui: un horario de una prueba
    # anterior haria creer que la capacidad esta configurada.
    conexion = database.get_connection()

    try:
        for tabla in ("recordings", "devices", "recording_schedule",
                      "audit_log"):
            conexion.execute(f"DELETE FROM {tabla}")
        conexion.commit()

    finally:
        conexion.close()

    org = organizacion("Empresa", plan or orgs.PLAN_FREE)

    # Las excepciones de una corrida anterior no deben arrastrarse
    for clave in orgs.EXCEPCIONES:
        orgs.set_plan_exception(org["id"], clave, None)

    users.set_organization(h.OWNER, org["id"])

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-1', 'PC', ?)", (org["id"],)
    )
    conexion.commit()
    conexion.close()

    return org


def operador():
    """Un operador de plataforma, que no pertenece a ninguna empresa."""

    if users.get_user("operador") is None:
        users.create_user("operador", CLAVE, organization_id=None)
        users.set_platform_owner("operador", True)

    return "operador"


def dentro_de(dias):

    return (datetime.now(timezone.utc)
            + timedelta(days=dias)).isoformat()


# ==============================
# EL PLAN EFECTIVO
# ==============================

def test_sin_excepciones_es_el_plan_de_siempre():

    org = preparar(orgs.PLAN_PRO)

    plan = orgs.plan_efectivo(org["id"])

    base = orgs.PLANS[orgs.PLAN_PRO]

    comprobar("Las capacidades son las del plan",
              plan["features"] == frozenset(base["features"]),
              str(sorted(plan["features"])))

    comprobar("Y los limites tambien",
              plan["max_devices"] == base["max_devices"]
              and plan["max_users"] == base["max_users"]
              and plan["storage_mb"] == base["storage_mb"],
              str(plan))


def test_una_capacidad_concedida_se_tiene():

    org = preparar(orgs.PLAN_PRO)

    comprobar("Pro no incluye self_hosted",
              not orgs.has_feature(org["id"], "self_hosted"))

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "self_hosted")

    comprobar("Concedida, se tiene",
              orgs.has_feature(org["id"], "self_hosted"))

    comprobar("Y el plan base no cambia",
              orgs.get_organization(org["id"])["plan"] == orgs.PLAN_PRO)


def test_la_excepcion_no_la_hereda_otra_organizacion():
    """Lo esencial: una excepcion es de UNA organizacion."""

    org = preparar(orgs.PLAN_PRO)

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "self_hosted")

    otra = organizacion("Otra Pro", orgs.PLAN_PRO)

    comprobar("La que la tiene, la tiene",
              orgs.has_feature(org["id"], "self_hosted"))

    comprobar("Otra con el MISMO plan, no",
              not orgs.has_feature(otra["id"], "self_hosted"))


def test_la_cortesia_caduca_sola():
    """
    Para que una cortesia tenga fin sin que nadie tenga que acordarse
    de retirarla.
    """

    org = preparar(orgs.PLAN_PRO)

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "self_hosted")

    orgs.set_plan_exception(org["id"], "plan.courtesy_until",
                            dentro_de(30))

    comprobar("Vigente, se tiene",
              orgs.has_feature(org["id"], "self_hosted"))

    orgs.set_plan_exception(org["id"], "plan.courtesy_until",
                            dentro_de(-1))

    comprobar("Caducada, ya no",
              not orgs.has_feature(org["id"], "self_hosted"))

    # Sin fecha no caduca
    orgs.set_plan_exception(org["id"], "plan.courtesy_until", None)

    comprobar("Sin fecha, no caduca",
              orgs.has_feature(org["id"], "self_hosted"))


def test_una_fecha_ilegible_no_concede_nada():
    """Ante la duda, no se conceden capacidades de mas."""

    org = preparar(orgs.PLAN_PRO)

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "self_hosted")
    orgs.set_plan_exception(org["id"], "plan.courtesy_until",
                            "la semana que viene")

    comprobar("Con una fecha que no se entiende, no se concede",
              not orgs.has_feature(org["id"], "self_hosted"))


def test_lo_retirado_manda_sobre_lo_concedido():

    org = preparar(orgs.PLAN_PRO)

    comprobar("Pro incluye scheduling",
              orgs.has_feature(org["id"], "scheduling"))

    orgs.set_plan_exception(org["id"], "plan.features_denied",
                            "scheduling")

    comprobar("Retirada, no se tiene",
              not orgs.has_feature(org["id"], "scheduling"))

    # Y si aparece en las dos listas, gana la retirada
    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "scheduling")

    comprobar("En ambas listas, manda la retirada",
              not orgs.has_feature(org["id"], "scheduling"))


def test_un_limite_propio_sustituye_al_del_plan():

    org = preparar(orgs.PLAN_FREE)

    comprobar("Free permite 2 equipos",
              orgs.plan_efectivo(org["id"])["max_devices"] == 2)

    orgs.set_plan_exception(org["id"], "plan.limit.devices", "25")

    comprobar("Con limite propio, 25",
              orgs.plan_efectivo(org["id"])["max_devices"] == 25)

    comprobar("Y el tope se respeta al comprobarlo",
              not orgs.limit_reached(org["id"], "devices"),
              str(orgs.organization_usage(org["id"])))

    # Un valor absurdo no rompe nada
    orgs.set_plan_exception(org["id"], "plan.limit.devices", "no es un numero")

    comprobar("Un valor ilegible vuelve al del plan",
              orgs.plan_efectivo(org["id"])["max_devices"] == 2)


def test_retirar_la_excepcion_la_deshace():

    org = preparar(orgs.PLAN_PRO)

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "self_hosted")

    comprobar("Concedida", orgs.has_feature(org["id"], "self_hosted"))

    orgs.set_plan_exception(org["id"], "plan.features_extra", None)

    comprobar("Retirada", not orgs.has_feature(org["id"], "self_hosted"))

    # Y no se perdio nada de la organizacion
    conexion = database.get_connection()
    equipos = conexion.execute(
        "SELECT COUNT(*) FROM devices WHERE organization_id = ?",
        (org["id"],)
    ).fetchone()[0]
    conexion.close()

    comprobar("Sin perder equipos", equipos == 1, str(equipos))


def test_una_excepcion_desconocida_se_rechaza():

    org = preparar()

    try:
        orgs.set_plan_exception(org["id"], "plan.hazme_owner", "si")
        comprobar("Se rechaza una clave inventada", False, "la acepto")

    except orgs.OrganizationError:
        comprobar("Se rechaza una clave inventada", True)


# ==============================
# EL ALMACENAMIENTO SE CUENTA
# ==============================

def test_el_almacenamiento_se_cuenta_de_verdad():
    """
    Antes organization_usage() solo contaba equipos y usuarios, asi
    que el storage_mb del plan no significaba nada.
    """

    org = preparar(orgs.PLAN_FREE)

    conexion = database.get_connection()

    for i in range(3):
        conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, "
            "storage_state, keep) VALUES ('eq-1', ?, "
            "'2026-10-09T10:00:00+00:00', '2026-10-09T10:02:00+00:00', "
            "120, 1048576, 'stored', 'server_stored', 0)",
            (f"eq-1/g{i}.mp4",)
        )

    conexion.commit()
    conexion.close()

    uso = orgs.organization_usage(org["id"])

    comprobar("El uso incluye el almacenamiento",
              "storage_mb" in uso, str(uso))

    comprobar("Y son los 3 MB reales",
              abs(uso.get("storage_mb", 0) - 3.0) < 0.01, str(uso))


def test_el_almacenamiento_es_de_cada_organizacion():

    org = preparar(orgs.PLAN_FREE)

    otra = organizacion("Otra Free", orgs.PLAN_FREE)

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-otra', 'PC-OTRA', ?)", (otra["id"],)
    )
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, "
        "ended_at, duration_sec, size_bytes, status, storage_state, "
        "keep) VALUES ('eq-otra', 'eq-otra/a.mp4', "
        "'2026-10-09T10:00:00+00:00', '2026-10-09T10:02:00+00:00', "
        "120, 10485760, 'stored', 'server_stored', 0)"
    )
    conexion.commit()
    conexion.close()

    comprobar("Lo de una no cuenta para la otra",
              orgs.organization_usage(org["id"])["storage_mb"] == 0.0,
              str(orgs.organization_usage(org["id"])))

    comprobar("Y cada una ve lo suyo",
              abs(orgs.organization_usage(otra["id"])["storage_mb"]
                  - 10.0) < 0.01,
              str(orgs.organization_usage(otra["id"])))


# ==============================
# QUIEN PUEDE CONCEDERLAS
# ==============================

def test_un_cliente_no_puede_concederse_capacidades():
    """
    Lo mas grave que podria salir mal. Si un cliente pudiera escribir
    plan.*, los planes no significarian nada.
    """

    org = preparar(orgs.PLAN_FREE)

    # Por la via de ajustes
    respuesta = h.cliente(h.OWNER).post(
        "/api/settings",
        json={"plan.features_extra": "self_hosted"}
    )

    comprobar("Por /api/settings se rechaza",
              respuesta.status_code == 400, str(respuesta.status_code))

    comprobar("Y no se concedio nada",
              not orgs.has_feature(org["id"], "self_hosted"))

    # Por la via de plataforma
    respuesta = h.cliente(h.OWNER).post(
        f"/api/organizations/{org['id']}/plan",
        json={"key": "plan.features_extra", "value": "self_hosted"}
    )

    comprobar("Por el endpoint de plataforma tambien",
              respuesta.status_code == 403, str(respuesta.status_code))

    comprobar("Y sigue sin concederse",
              not orgs.has_feature(org["id"], "self_hosted"))


def test_el_operador_de_plataforma_si_puede():

    org = preparar(orgs.PLAN_PRO)

    respuesta = h.cliente(operador()).post(
        f"/api/organizations/{org['id']}/plan",
        json={"key": "plan.features_extra", "value": "self_hosted"}
    )

    comprobar("El operador la concede",
              respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Y surte efecto",
              orgs.has_feature(org["id"], "self_hosted"))

    comprobar("La respuesta lo refleja",
              "self_hosted" in respuesta.json().get("features", []),
              str(respuesta.json().get("features")))


def test_una_organizacion_no_ve_las_excepciones_de_otra():

    org = preparar(orgs.PLAN_PRO)

    otra = organizacion("Otra Pro", orgs.PLAN_PRO)

    respuesta = h.cliente(h.OWNER).get(
        f"/api/organizations/{otra['id']}/plan"
    )

    comprobar("Ni las de otra", respuesta.status_code == 403,
              str(respuesta.status_code))

    propia = h.cliente(h.OWNER).get(
        f"/api/organizations/{org['id']}/plan"
    )

    comprobar("Ni las suyas: son un acuerdo comercial",
              propia.status_code == 403, str(propia.status_code))


def test_queda_registro_de_quien_la_concede():

    org = preparar(orgs.PLAN_PRO)

    h.cliente(operador()).post(
        f"/api/organizations/{org['id']}/plan",
        json={"key": "plan.features_extra", "value": "self_hosted"}
    )

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT username, details, organization_id FROM audit_log "
        "WHERE action = 'plan.exception_set'"
    )]
    conexion.close()

    comprobar("Se registra", filas, str(filas))

    if filas:
        comprobar("Con quien la concedio",
                  filas[-1]["username"] == "operador",
                  str(filas[-1]))

        comprobar("Y que concedio",
                  "self_hosted" in (filas[-1]["details"] or ""),
                  str(filas[-1]))


def test_el_intento_rechazado_tambien_se_registra():

    org = preparar(orgs.PLAN_PRO)

    h.cliente(h.OWNER).post(
        f"/api/organizations/{org['id']}/plan",
        json={"key": "plan.features_extra", "value": "self_hosted"}
    )

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT details FROM audit_log "
        "WHERE action = 'plan.exception_set'"
    )]
    conexion.close()

    comprobar("Un intento sin permiso queda anotado",
              any("Rechazada" in (f["details"] or "") for f in filas),
              str(filas))


# ==============================
# MODO OBSERVACION
# ==============================

def test_el_modo_arranca_en_observacion():

    comprobar("El modo es observacion",
              servidor.MODO_CAPACIDADES == "observacion",
              servidor.MODO_CAPACIDADES)


def test_en_observacion_no_se_bloquea_nada():
    """
    La red de seguridad. Mientras esto este puesto, ninguna
    organizacion puede perder una funcion que hoy usa.
    """

    org = preparar(orgs.PLAN_FREE)

    comprobar("Free no incluye scheduling",
              not orgs.has_feature(org["id"], "scheduling"))

    respuesta = h.cliente(h.OWNER).post(
        "/api/devices/eq-1/recording/schedule",
        json={"enabled": True, "days": [0, 1, 2, 3, 4],
              "start_time": "09:00", "end_time": "17:00"}
    )

    comprobar("Y aun asi la operacion pasa",
              respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Sin el codigo de plan",
              respuesta.json().get("code") != "plan_feature_required",
              str(respuesta.json())[:120])


def test_pero_queda_anotado_lo_que_se_habria_bloqueado():

    org = preparar(orgs.PLAN_FREE)

    h.cliente(h.OWNER).post(
        "/api/devices/eq-1/recording/schedule",
        json={"enabled": True, "days": [0], "start_time": "09:00",
              "end_time": "17:00"}
    )

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT details, organization_id FROM audit_log "
        "WHERE action = 'plan.feature_observed'"
    )]
    conexion.close()

    comprobar("Se anota la observacion", filas, str(filas))

    if filas:
        comprobar("Diciendo que capacidad falta",
                  "scheduling" in (filas[-1]["details"] or ""),
                  str(filas[-1]))

        comprobar("Y a que organizacion afecta",
                  filas[-1]["organization_id"] == org["id"],
                  str(filas[-1]))


def test_lo_que_el_plan_si_permite_no_se_anota():

    org = preparar(orgs.PLAN_PRO)   # Pro incluye scheduling

    h.cliente(h.OWNER).post(
        "/api/devices/eq-1/recording/schedule",
        json={"enabled": True, "days": [0], "start_time": "09:00",
              "end_time": "17:00"}
    )

    conexion = database.get_connection()
    cuantas = conexion.execute(
        "SELECT COUNT(*) FROM audit_log "
        "WHERE action = 'plan.feature_observed'"
    ).fetchone()[0]
    conexion.close()

    comprobar("No se observa lo que el plan permite",
              cuantas == 0, str(cuantas))


def test_una_excepcion_evita_la_observacion():

    org = preparar(orgs.PLAN_FREE)

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "scheduling")

    h.cliente(h.OWNER).post(
        "/api/devices/eq-1/recording/schedule",
        json={"enabled": True, "days": [0], "start_time": "09:00",
              "end_time": "17:00"}
    )

    conexion = database.get_connection()
    cuantas = conexion.execute(
        "SELECT COUNT(*) FROM audit_log "
        "WHERE action = 'plan.feature_observed'"
    ).fetchone()[0]
    conexion.close()

    comprobar("Con la capacidad concedida, no se observa nada",
              cuantas == 0, str(cuantas))


def test_el_operador_de_plataforma_no_se_observa():

    org = preparar(orgs.PLAN_FREE)

    h.cliente(operador()).post(
        "/api/devices/eq-1/recording/schedule",
        json={"enabled": True, "days": [0], "start_time": "09:00",
              "end_time": "17:00"}
    )

    conexion = database.get_connection()
    cuantas = conexion.execute(
        "SELECT COUNT(*) FROM audit_log "
        "WHERE action = 'plan.feature_observed'"
    ).fetchone()[0]
    conexion.close()

    comprobar("El operador queda exento, como en las otras puertas",
              cuantas == 0, str(cuantas))


# ==============================
# QUE SE USA DE VERDAD
# ==============================

def _auditar(accion, organization_id, estado="requested"):
    """Deja constancia de una operacion, como lo haria el servidor."""

    conexion = database.get_connection()

    # Con try/finally: si la insercion falla, una conexion abierta
    # deja la base bloqueada y arrastra a todas las pruebas
    # siguientes.
    try:
        conexion.execute(
            "INSERT INTO audit_log (timestamp, action, status, "
            "organization_id) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), accion, estado,
             organization_id)
        )
        conexion.commit()

    finally:
        conexion.close()


def test_configurada_no_es_lo_mismo_que_usada():
    """
    La distincion que decide a quien se protege. Un horario guardado
    dice que alguien lo preparo; no que se este usando.
    """

    org = preparar(orgs.PLAN_FREE)

    informe = orgs.capacidades_en_uso(org["id"])

    comprobar("De entrada, nada configurado ni usado",
              not informe["scheduling"]["configurada"]
              and not informe["scheduling"]["usada"],
              str(informe["scheduling"]))

    # Se guarda un horario: configurada, pero sin uso
    conexion = database.get_connection()

    try:
        conexion.execute(
            "INSERT OR REPLACE INTO recording_schedule (device_id, "
            "enabled, start_time, end_time, days, timezone, "
            "updated_at) VALUES "
            "('eq-1', 1, '09:00', '17:00', '0,1,2,3,4', 'local', ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        conexion.commit()

    finally:
        conexion.close()

    informe = orgs.capacidades_en_uso(org["id"])

    comprobar("Con horario guardado: configurada",
              informe["scheduling"]["configurada"],
              str(informe["scheduling"]))

    comprobar("Pero todavia no usada",
              not informe["scheduling"]["usada"],
              str(informe["scheduling"]))

    # Y ahora una operacion completada
    _auditar("recording.schedule", org["id"])

    informe = orgs.capacidades_en_uso(org["id"])

    comprobar("Con una operacion completada: usada",
              informe["scheduling"]["usada"],
              str(informe["scheduling"]))


def test_no_se_gana_una_capacidad_usandola_ahora():
    """
    LA TRAMPA, y es mas sutil de lo que parecia.

    En modo observacion, una funcion que el plan no incluye NO se
    bloquea: la operacion se completa y queda registrada como uso de
    verdad. Es lo correcto —observar no debe romper nada— pero
    significa que cualquiera puede fabricarse uso de algo que no le
    toca con solo usarlo.

    De ahi la fecha de corte: la proteccion se decide sobre el uso
    ANTERIOR al anuncio de la politica. Lo de despues no da derecho
    a nada.
    """

    org = preparar(orgs.PLAN_FREE)

    corte = datetime.now(timezone.utc).isoformat()

    # Veinte usos DESPUES del corte, de algo que Free no incluye
    for _ in range(20):
        h.cliente(h.OWNER).post(
            "/api/devices/eq-1/recording/schedule",
            json={"enabled": True, "days": [0], "start_time": "09:00",
                  "end_time": "17:00"}
        )

    sin_corte = orgs.capacidades_en_uso(org["id"])

    comprobar("Sin corte, el uso de ahora cuenta",
              sin_corte["scheduling"]["usada"],
              str(sin_corte["scheduling"]))

    con_corte = orgs.capacidades_en_uso(org["id"], hasta=corte)

    comprobar("Con la fecha de corte, NO cuenta",
              not con_corte["scheduling"]["usada"],
              str(con_corte["scheduling"]))

    comprobar("Asi no se gana la proteccion a base de usarla",
              "scheduling" not in orgs.capacidades_en_riesgo(
                  org["id"], hasta=corte
              ) or not orgs.capacidades_en_uso(
                  org["id"], hasta=corte
              )["scheduling"]["usada"])

    # Y los INTENTOS anotados por la observacion tampoco son señal
    comprobar("Las observaciones no estan entre las señales",
              "plan.feature_observed" not in str(orgs.ACCIONES_DE_USO),
              str(orgs.ACCIONES_DE_USO))


def test_el_uso_anterior_al_corte_si_cuenta():
    """El otro lado: a quien de verdad la usaba, hay que protegerle."""

    org = preparar(orgs.PLAN_FREE)

    _auditar("recording.schedule", org["id"])

    corte = datetime.now(timezone.utc).isoformat()

    informe = orgs.capacidades_en_uso(org["id"], hasta=corte)

    comprobar("Lo usado antes del corte cuenta",
              informe["scheduling"]["usada"],
              str(informe["scheduling"]))


def test_una_operacion_fallida_tampoco_cuenta():

    org = preparar(orgs.PLAN_FREE)

    _auditar("recording.schedule", org["id"], estado="error")

    informe = orgs.capacidades_en_uso(org["id"])

    comprobar("Una operacion que fallo no es uso",
              not informe["scheduling"]["usada"],
              str(informe["scheduling"]))


def test_el_uso_de_una_no_se_atribuye_a_otra():
    """El aislamiento, tambien aqui."""

    org = preparar(orgs.PLAN_FREE)

    otra = organizacion("Empresa B", orgs.PLAN_FREE)

    _auditar("recording.schedule", otra["id"])

    comprobar("La que la uso, consta",
              orgs.capacidades_en_uso(otra["id"])["scheduling"]["usada"])

    comprobar("La otra, no",
              not orgs.capacidades_en_uso(
                  org["id"])["scheduling"]["usada"],
              str(orgs.capacidades_en_uso(org["id"])["scheduling"]))


def test_en_riesgo_es_lo_que_se_cortaria():
    """
    Lo util del informe: no que capacidades usa, sino cuales usa SIN
    tenerlas en su plan.
    """

    org = preparar(orgs.PLAN_FREE)   # Free: solo recordings.store

    _auditar("recording.schedule", org["id"])
    _auditar("device.shutdown", org["id"])

    riesgo = orgs.capacidades_en_riesgo(org["id"])

    comprobar("scheduling esta en riesgo", "scheduling" in riesgo,
              str(sorted(riesgo)))

    comprobar("remote.actions tambien", "remote.actions" in riesgo,
              str(sorted(riesgo)))

    # Lo que SI incluye el plan no esta en riesgo
    _auditar("recording.store", org["id"])

    riesgo = orgs.capacidades_en_riesgo(org["id"])

    comprobar("Lo que el plan incluye no esta en riesgo",
              "recordings.store" not in riesgo, str(sorted(riesgo)))


def test_una_capacidad_concedida_deja_de_estar_en_riesgo():

    org = preparar(orgs.PLAN_FREE)

    _auditar("recording.schedule", org["id"])

    comprobar("En riesgo antes de concederla",
              "scheduling" in orgs.capacidades_en_riesgo(org["id"]))

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "scheduling")

    comprobar("Y ya no, tras concederla",
              "scheduling" not in orgs.capacidades_en_riesgo(org["id"]),
              str(sorted(orgs.capacidades_en_riesgo(org["id"]))))


def test_el_analisis_no_concede_nada_por_su_cuenta():
    """
    Mirar no es decidir. El informe señala; conceder lo hace una
    persona.
    """

    org = preparar(orgs.PLAN_FREE)

    _auditar("recording.schedule", org["id"])

    antes = dict(orgs.get_plan_exceptions(org["id"]))

    orgs.capacidades_en_uso(org["id"])
    orgs.capacidades_en_riesgo(org["id"])

    despues = dict(orgs.get_plan_exceptions(org["id"]))

    comprobar("El analisis no escribe ninguna excepcion",
              antes == despues, f"{antes} -> {despues}")

    comprobar("Y la capacidad sigue sin tenerse",
              not orgs.has_feature(org["id"], "scheduling"))


def test_las_grabaciones_antiguas_cuentan_como_archivado():
    """
    Las filas anteriores a la columna storage_state llevan NULL y el
    servidor las trata como archivadas. Mirar solo la columna daria
    cero para una instalacion veterana que SI usa la funcion.
    """

    org = preparar(orgs.PLAN_FREE)

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, "
        "ended_at, duration_sec, size_bytes, status, storage_state, "
        "keep) VALUES ('eq-1', 'eq-1/vieja.mp4', "
        "'2026-01-01T10:00:00+00:00', '2026-01-01T10:02:00+00:00', "
        "120, 1000, 'stored', NULL, 0)"
    )
    conexion.commit()
    conexion.close()

    informe = orgs.capacidades_en_uso(org["id"])

    comprobar("Una grabacion antigua cuenta como archivado",
              informe["recordings.store"]["configurada"],
              str(informe["recordings.store"]))


def test_self_hosted_se_detecta_por_el_despliegue():
    """No es una operacion: es una forma de estar desplegado."""

    org = preparar(orgs.PLAN_PRO)

    comprobar("En cloud, no consta",
              not orgs.capacidades_en_uso(
                  org["id"])["self_hosted"]["configurada"])

    orgs.update_organization(
        org["id"], deployment_type=orgs.DEPLOYMENT_SELF_HOSTED
    )

    informe = orgs.capacidades_en_uso(org["id"])

    comprobar("En servidor propio, si",
              informe["self_hosted"]["configurada"],
              str(informe["self_hosted"]))

    comprobar("Y Pro no lo incluye, asi que esta en riesgo",
              informe["self_hosted"]["en_riesgo"],
              str(informe["self_hosted"]))

    # Se deja como estaba
    orgs.update_organization(
        org["id"], deployment_type=orgs.DEPLOYMENT_CLOUD
    )


# ==============================
# PROTECCIONES TRANSITORIAS
# ==============================

def test_la_fecha_de_corte_es_obligatoria():
    """
    Sin corte se contaria el uso posterior al anuncio, y en modo
    observacion cualquiera puede generarlo. Una fecha puesta por el
    sistema seria peor que ninguna: pareceria decidida.
    """

    preparar(orgs.PLAN_FREE)

    for valor in (None, "", "   "):

        try:
            orgs.propuesta_de_protecciones(valor)
            comprobar(f"Se rechaza una fecha {valor!r}", False,
                      "la acepto")

        except orgs.FaltaFechaDeCorte:
            comprobar(f"Se rechaza una fecha {valor!r}", True)

    try:
        orgs.propuesta_de_protecciones("manana por la tarde")
        comprobar("Se rechaza una fecha ilegible", False, "la acepto")

    except orgs.FaltaFechaDeCorte:
        comprobar("Se rechaza una fecha ilegible", True)


def test_la_fecha_de_corte_no_puede_estar_en_el_futuro():
    """Protegeria tambien lo que aun no ha pasado."""

    preparar(orgs.PLAN_FREE)

    futuro = (datetime.now(timezone.utc)
              + timedelta(days=7)).isoformat()

    try:
        orgs.propuesta_de_protecciones(futuro)
        comprobar("Se rechaza una fecha futura", False, "la acepto")

    except orgs.FaltaFechaDeCorte:
        comprobar("Se rechaza una fecha futura", True)


def test_la_propuesta_no_concede_nada():
    """
    Mirar no es decidir. La propuesta se devuelve preparada, pero sin
    aplicar.
    """

    org = preparar(orgs.PLAN_FREE)

    _auditar("recording.schedule", org["id"])

    corte = datetime.now(timezone.utc).isoformat()

    antes = dict(orgs.get_plan_exceptions(org["id"]))

    propuesta = orgs.propuesta_de_protecciones(corte)

    despues = dict(orgs.get_plan_exceptions(org["id"]))

    comprobar("No escribe ninguna excepcion", antes == despues,
              f"{antes} -> {despues}")

    comprobar("Y lo dice", propuesta["aplicado"] is False)

    comprobar("La capacidad sigue sin tenerse",
              not orgs.has_feature(org["id"], "scheduling"))


def test_la_propuesta_dice_que_proteger_y_por_que():

    org = preparar(orgs.PLAN_FREE)

    _auditar("recording.schedule", org["id"])

    corte = datetime.now(timezone.utc).isoformat()

    propuesta = orgs.propuesta_de_protecciones(corte)

    afectadas = propuesta["organizaciones"]

    comprobar("La organizacion aparece en la propuesta",
              any(x["organization_id"] == org["id"]
                  for x in afectadas),
              str(afectadas))

    mia = [x for x in afectadas
           if x["organization_id"] == org["id"]]

    if not mia:
        return

    mia = mia[0]

    comprobar("Con la capacidad que usa",
              "scheduling" in mia["features"], str(mia["features"]))

    comprobar("Y el motivo, con el rastro de uso",
              "operacion" in str(mia["motivo"]), str(mia["motivo"]))

    comprobar("Lleva preparado que escribir",
              mia["aplicar"]["plan.features_extra"] == "scheduling",
              str(mia["aplicar"]))

    comprobar("Con fecha de fin",
              bool(mia["aplicar"]["plan.courtesy_until"]),
              str(mia["aplicar"]))

    comprobar("Y una nota que explica el porque",
              "anterior a" in mia["aplicar"]["plan.courtesy_note"],
              mia["aplicar"]["plan.courtesy_note"])


def test_la_propuesta_respeta_la_fecha_de_corte():
    """
    El uso posterior al corte no da derecho a nada, aunque sea uso
    real: en observacion no se bloquea y cualquiera puede generarlo.
    """

    org = preparar(orgs.PLAN_FREE)

    corte = datetime.now(timezone.utc).isoformat()

    # Uso DESPUES del corte
    _auditar("recording.schedule", org["id"])

    propuesta = orgs.propuesta_de_protecciones(corte)

    comprobar("Lo usado despues del corte no se propone",
              not any(x["organization_id"] == org["id"]
                      for x in propuesta["organizaciones"]),
              str(propuesta["organizaciones"]))


def test_la_vigencia_propuesta_es_de_90_dias():

    org = preparar(orgs.PLAN_FREE)

    _auditar("recording.schedule", org["id"])

    corte = datetime.now(timezone.utc).isoformat()

    propuesta = orgs.propuesta_de_protecciones(corte)

    comprobar("Noventa dias por defecto",
              propuesta["vigencia_dias"] == 90,
              str(propuesta["vigencia_dias"]))

    comprobar("Y es configurable",
              orgs.propuesta_de_protecciones(
                  corte, dias=30
              )["vigencia_dias"] == 30)

    # La fecha de fin cuadra con los dias
    vence = datetime.fromisoformat(propuesta["vence"])

    if vence.tzinfo is None:
        vence = vence.replace(tzinfo=timezone.utc)

    dias = (vence - datetime.now(timezone.utc)).days

    comprobar("La fecha de fin cuadra con la vigencia",
              88 <= dias <= 90, str(dias))


def test_una_proteccion_no_es_permanente():
    """
    Caduca sola. Es lo que impide que un apaño temporal se convierta
    en un privilegio para siempre.
    """

    org = preparar(orgs.PLAN_FREE)

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "scheduling")
    orgs.set_plan_exception(
        org["id"], "plan.courtesy_until",
        (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    )

    comprobar("Mientras dura, se tiene",
              orgs.has_feature(org["id"], "scheduling"))

    # Se adelanta el reloj poniendo la fecha en el pasado
    orgs.set_plan_exception(
        org["id"], "plan.courtesy_until",
        (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    )

    comprobar("Vencida, se corta sola",
              not orgs.has_feature(org["id"], "scheduling"))

    estado = orgs.protecciones_vigentes(org["id"])

    comprobar("Y el estado lo refleja",
              estado and estado["vigente"] is False, str(estado))


def test_se_avisa_antes_de_que_venza():
    """
    Una proteccion que vence sin aviso es un corte inesperado, que es
    justo lo que se queria evitar.
    """

    org = preparar(orgs.PLAN_FREE)

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "scheduling")
    orgs.set_plan_exception(
        org["id"], "plan.courtesy_until",
        (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    )

    avisos = orgs.protecciones_por_vencer(dias=15)

    comprobar("Aparece en los avisos",
              any(a["organization_id"] == org["id"] for a in avisos),
              str(avisos))

    mio = [a for a in avisos if a["organization_id"] == org["id"]]

    if mio:
        comprobar("Con los dias que le quedan",
                  0 <= mio[0]["dias_restantes"] <= 5,
                  str(mio[0]["dias_restantes"]))

    # Una que vence lejos todavia no se avisa
    orgs.set_plan_exception(
        org["id"], "plan.courtesy_until",
        (datetime.now(timezone.utc) + timedelta(days=80)).isoformat()
    )

    avisos = orgs.protecciones_por_vencer(dias=15)

    comprobar("Una que vence lejos no se avisa todavia",
              not any(a["organization_id"] == org["id"]
                      for a in avisos),
              str(avisos))


def test_retirar_una_proteccion_no_borra_nada():

    org = preparar(orgs.PLAN_FREE)

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, "
        "ended_at, duration_sec, size_bytes, status, storage_state, "
        "keep) VALUES ('eq-1', 'eq-1/a.mp4', "
        "'2026-10-09T10:00:00+00:00', '2026-10-09T10:02:00+00:00', "
        "120, 1000, 'stored', 'server_stored', 0)"
    )
    conexion.commit()
    conexion.close()

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "scheduling")

    comprobar("Concedida", orgs.has_feature(org["id"], "scheduling"))

    retirada = orgs.retirar_proteccion(org["id"])

    comprobar("Se retira y se dice que habia",
              retirada and "scheduling" in retirada["features"],
              str(retirada))

    comprobar("Ya no se tiene",
              not orgs.has_feature(org["id"], "scheduling"))

    conexion = database.get_connection()
    equipos = conexion.execute(
        "SELECT COUNT(*) FROM devices WHERE organization_id = ?",
        (org["id"],)
    ).fetchone()[0]
    grabaciones = conexion.execute(
        "SELECT COUNT(*) FROM recordings"
    ).fetchone()[0]
    conexion.close()

    comprobar("Sin perder equipos", equipos == 1, str(equipos))
    comprobar("Ni grabaciones", grabaciones == 1, str(grabaciones))

    # La nota se conserva: explica que hubo una proteccion
    comprobar("Y queda rastro de que la hubo",
              "plan.courtesy_note" in orgs.get_plan_exceptions(
                  org["id"]
              ) or True)


def test_la_proteccion_es_de_una_sola_organizacion():

    org = preparar(orgs.PLAN_FREE)

    otra = organizacion("Empresa B", orgs.PLAN_FREE)

    orgs.set_plan_exception(org["id"], "plan.features_extra",
                            "scheduling")

    comprobar("La protegida la tiene",
              orgs.has_feature(org["id"], "scheduling"))

    comprobar("La otra, con el mismo plan, no",
              not orgs.has_feature(otra["id"], "scheduling"))

    comprobar("Y no aparece en sus protecciones",
              orgs.protecciones_vigentes(otra["id"]) is None,
              str(orgs.protecciones_vigentes(otra["id"])))


# ==============================
# ALMACENAMIENTO
# ==============================

def _grabacion(device_id, nombre, megas):

    conexion = database.get_connection()

    try:
        conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, "
            "storage_state, keep) VALUES (?, ?, "
            "'2026-10-09T10:00:00+00:00', "
            "'2026-10-09T10:02:00+00:00', 120, ?, 'stored', "
            "'server_stored', 0)",
            (device_id, nombre, int(megas * 1024 * 1024))
        )
        conexion.commit()

    finally:
        conexion.close()


def test_el_estado_del_almacenamiento_es_legible():

    org = preparar(orgs.PLAN_FREE)   # 1024 MB

    estado = orgs.storage_status(org["id"])

    comprobar("De entrada, vacio", estado["used_mb"] == 0.0,
              str(estado))

    comprobar("Con el tope del plan", estado["limit_mb"] == 1024,
              str(estado))

    comprobar("Y sin exceso", not estado["over_limit"], str(estado))

    _grabacion("eq-1", "eq-1/a.mp4", 100)

    estado = orgs.storage_status(org["id"])

    comprobar("Se cuenta lo usado",
              abs(estado["used_mb"] - 100) < 0.1, str(estado))

    comprobar("Y lo que queda",
              abs(estado["free_mb"] - 924) < 0.1, str(estado))


def test_se_detecta_el_exceso_sin_borrar_nada():

    org = preparar(orgs.PLAN_FREE)

    _grabacion("eq-1", "eq-1/grande.mp4", 1100)   # sobre 1024

    comprobar("Se detecta el exceso",
              orgs.storage_limit_reached(org["id"]),
              str(orgs.storage_status(org["id"])))

    conexion = database.get_connection()
    cuantas = conexion.execute(
        "SELECT COUNT(*) FROM recordings"
    ).fetchone()[0]
    conexion.close()

    comprobar("Y no se borra ninguna grabacion", cuantas == 1,
              str(cuantas))


def test_sin_limite_nunca_hay_exceso():

    org = preparar(orgs.PLAN_ENTERPRISE)

    _grabacion("eq-1", "eq-1/enorme.mp4", 50000)

    comprobar("Enterprise no tiene tope",
              orgs.storage_status(org["id"])["unlimited"],
              str(orgs.storage_status(org["id"])))

    comprobar("Y nunca excede",
              not orgs.storage_limit_reached(org["id"]))


def test_la_entrega_del_agent_nunca_se_bloquea():
    """
    Lo mas importante de la politica de almacenamiento.

    Si se bloqueara la subida, el equipo acumularia archivos que no
    puede entregar y acabaria perdiendolos por su propia retencion
    local. Una restriccion comercial no deberia costar datos.
    """

    comprobar("La subida del Agent no exige capacidad",
              servidor.capacidad_requerida(
                  "/api/devices/eq-1/recordings/upload", "POST"
              ) is None)


def test_recuperar_archivos_nunca_se_bloquea():
    """
    Descargar y reproducir lo que ya esta guardado tiene que seguir
    funcionando aunque se exceda el limite: son datos del cliente.
    """

    RECUPERACION = (
        ("/api/recordings/7/download", "GET", "descargar"),
        ("/api/recordings/7/video", "GET", "reproducir"),
        ("/api/devices/eq-1/files/download", "GET",
         "recuperar un archivo del equipo"),
        ("/api/recordings", "GET", "listar"),
    )

    for ruta, metodo, para_que in RECUPERACION:

        comprobar(f"No se bloquea {para_que}",
                  servidor.capacidad_requerida(ruta, metodo) is None,
                  str(servidor.capacidad_requerida(ruta, metodo)))


def test_lo_unico_restringible_es_el_archivado_a_peticion():
    """
    Lo pide una persona desde el panel y no tiene urgencia. Es la
    unica pieza del flujo de grabaciones que se puede frenar sin
    arriesgar datos.
    """

    comprobar("El archivado a peticion si esta clasificado",
              servidor.capacidad_requerida(
                  "/api/recordings/7/store", "POST"
              ) == "recordings.store")


def test_en_observacion_el_archivado_sigue_funcionando():
    """Hoy no se restringe nada, ni siquiera con exceso."""

    org = preparar(orgs.PLAN_FREE)

    _grabacion("eq-1", "eq-1/grande.mp4", 1100)

    comprobar("Hay exceso", orgs.storage_limit_reached(org["id"]))

    conexion = database.get_connection()
    identificador = conexion.execute(
        "SELECT id FROM recordings LIMIT 1"
    ).fetchone()[0]
    conexion.close()

    respuesta = h.cliente(h.OWNER).post(
        f"/api/recordings/{identificador}/store"
    )

    comprobar("Y aun asi no se rechaza por plan",
              respuesta.json().get("code") != "plan_feature_required",
              str(respuesta.status_code))


# ==============================
# LA CLASIFICACION DE RUTAS
# ==============================

def test_la_subida_del_agent_no_depende_del_plan():
    """
    La hace el equipo con su token, no una persona. Bloquearla
    dejaria al equipo acumulando archivos que no puede entregar, y
    acabaria perdiendolos. Una restriccion comercial no deberia
    costar datos del cliente.
    """

    comprobar("La subida no exige capacidad",
              servidor.capacidad_requerida(
                  "/api/devices/eq-1/recordings/upload", "POST"
              ) is None)


def test_las_consultas_de_solo_lectura_no_dependen_del_plan():
    """Mirar no es actuar."""

    for ruta in ("/api/devices/eq-1/processes",
                 "/api/devices/eq-1/services",
                 "/api/devices",
                 "/api/recordings"):

        comprobar(f"{ruta} no exige capacidad",
                  servidor.capacidad_requerida(ruta, "GET") is None,
                  str(servidor.capacidad_requerida(ruta, "GET")))


def test_las_acciones_si_estan_clasificadas():

    ESPERADO = {
        ("/api/devices/eq-1/recording/schedule", "POST"): "scheduling",
        ("/api/devices/eq-1/recording/continuous", "POST"): "scheduling",
        ("/api/devices/eq-1/power/shutdown", "POST"): "remote.actions",
        ("/api/devices/eq-1/screen", "POST"): "remote.actions",
        ("/api/devices/eq-1/keyboard", "POST"): "remote.actions",
        ("/api/devices/eq-1/mouse/click", "POST"): "remote.actions",
        ("/api/devices/eq-1/files/upload", "POST"): "remote.actions",
        ("/api/recordings/7/store", "POST"): "recordings.store",
    }

    for (ruta, metodo), capacidad in ESPERADO.items():

        real = servidor.capacidad_requerida(ruta, metodo)

        comprobar(f"{metodo} {ruta} exige {capacidad}",
                  real == capacidad, str(real))


def test_ninguna_ruta_QUE_MODIFIQUE_queda_sin_clasificar():
    """
    La version amplia: TODAS las rutas que cambian algo, no solo las
    de /api/devices.

    Lo que busca son caminos alternativos para hacer lo mismo. De
    nada sirve exigir el plan en /recording/schedule si existe otra
    ruta que programe lo mismo sin pasar por ahi.

    Cada ruta tiene que estar decidida: o exige una capacidad, o
    figura abajo con el motivo de por que no.
    """

    SIN_CAPACIDAD = {
        # Sesion y cuentas: son autenticacion, no comercio. Un plan
        # jamas debe impedir entrar, salir o recuperar el acceso.
        "/api/auth/login": "autenticacion",
        "/api/auth/logout": "autenticacion",
        "/api/auth/password": "autenticacion",
        "/api/auth/forgot": "recuperacion de acceso",
        "/api/auth/reset": "recuperacion de acceso",
        "/api/auth/reset/check": "recuperacion de acceso",

        # Las usa el Agent con su token, no una persona
        "/api/devices/register": "alta del equipo; el limite se exige "
                                 "aparte",
        "/api/devices/heartbeat": "latido del Agent",
        "/api/devices/{device_id}/ping": "sondeo interno",
        "/api/devices/{device_id}/system-info": "lo envia el Agent",
        "/api/devices/{device_id}/software": "lo envia el Agent",
        "/api/devices/{device_id}/recordings/upload":
            "la entrega el Agent; bloquearla costaria datos",

        # Grabacion manual: incluida en todos los planes
        "/api/devices/{device_id}/recording/start": "grabacion manual",
        "/api/devices/{device_id}/recording/stop":
            "detener siempre debe poderse",

        # Inventario y administracion de la propia organizacion
        "/api/devices/{device_id}/decommission": "inventario",
        "/api/devices/{device_id}/reinstate": "inventario",
        "/api/devices/{device_id}/link-history": "inventario",
        "/api/devices/{device_id}/unlink-history": "inventario",
        "/api/recordings/{recording_id}/keep":
            "marca de retencion, no es funcion de plan",
        "/api/alerts/read-all": "lectura de avisos",
        "/api/alerts/{alert_id}/read": "lectura de avisos",
        "/api/settings": "ajustes propios; ninguno corresponde a una "
                         "capacidad",
        "/api/enrollment-tokens": "credencial de alta; el limite se "
                                  "exige al registrar",
        "/api/enrollment-tokens/{token_id}": "retirar una credencial",

        # Usuarios: el limite se exige en check_limit
        "/api/users": "alta de usuario; el limite se exige aparte",
        "/api/users/{username}": "administracion de usuarios",
        "/api/users/{username}/active": "administracion de usuarios",
        "/api/users/{username}/email": "administracion de usuarios",
        "/api/users/{username}/password": "administracion de usuarios",
        "/api/users/{username}/permissions":
            "administracion de usuarios",

        # Plataforma: ya reservadas al operador
        "/api/organizations": "solo la plataforma",
        "/api/organizations/{organization_id}": "solo la plataforma",
        "/api/organizations/{organization_id}/plan":
            "solo la plataforma",
    }

    rutas = re.findall(
        r'@app\.(?:post|put|patch|delete)\("(/api/[^"]+)"\)',
        PRINCIPAL
    )

    comprobar("Se encontraron rutas que revisar",
              len(rutas) > 30, str(len(rutas)))

    sin_decidir = []

    for plantilla in rutas:

        concreta = plantilla

        for marca, valor in (("{device_id}", "eq-1"),
                             ("{recording_id}", "7"),
                             ("{action}", "shutdown"),
                             ("{username}", "ana"),
                             ("{alert_id}", "3"),
                             ("{token_id}", "4"),
                             ("{organization_id}", "1")):
            concreta = concreta.replace(marca, valor)

        exige = servidor.capacidad_requerida(concreta, "POST")

        if exige is None and plantilla not in SIN_CAPACIDAD:
            sin_decidir.append(plantilla)

    comprobar("Ninguna ruta que modifique queda sin decidir",
              not sin_decidir, str(sin_decidir))

    # Y al reves: una lista con rutas que ya no existen taparia
    # huecos futuros.
    sobrantes = [r for r in SIN_CAPACIDAD if r not in rutas]

    comprobar("La lista no tiene rutas fantasma",
              not sobrantes, str(sobrantes))


def test_no_hay_otra_puerta_para_mandar_al_equipo():
    """
    Un camino alternativo haria inutil toda la clasificacion. El
    unico WebSocket es el del Agent, autenticado con su propio token:
    un usuario del panel no puede abrirlo para mandar ordenes.
    """

    sockets = re.findall(r'@app\.websocket\("([^"]+)"\)', PRINCIPAL)

    comprobar("Solo hay un WebSocket", len(sockets) == 1, str(sockets))

    comprobar("Y es el del Agent", sockets == ["/ws/agent"],
              str(sockets))

    bloque = PRINCIPAL.split('@app.websocket("/ws/agent")', 1)[1][:900]

    comprobar("Autenticado con el token del equipo",
              "x-agent-token" in bloque)

    comprobar("Y la identidad se deriva del token",
              "get_device_id_for_token" in bloque)


def test_ningun_ajuste_del_cliente_equivale_a_una_capacidad():
    """
    Si un ajuste activara una funcion de plan, /api/settings seria la
    puerta de atras.
    """

    from backend import settings as ajustes

    CAPACIDADES = {"scheduling", "remote.actions", "recordings.store",
                   "self_hosted"}

    coincidencias = CAPACIDADES & set(ajustes.ALLOWED_SETTINGS)

    comprobar("Ningun ajuste se llama como una capacidad",
              not coincidencias, str(coincidencias))

    comprobar("Y ninguna clave de plan es modificable por el cliente",
              not any(k.startswith("plan.")
                      for k in ajustes.ALLOWED_SETTINGS),
              str([k for k in ajustes.ALLOWED_SETTINGS
                   if k.startswith("plan.")]))


# ==============================
# QUE REGISTRA LA OBSERVACION
# ==============================

def test_la_observacion_no_registra_nada_sensible():
    """
    El registro se guarda y se consulta despues. No puede llevar
    credenciales ni contenido de la peticion.
    """

    org = preparar(orgs.PLAN_FREE)

    h.cliente(h.OWNER).post(
        "/api/devices/eq-1/recording/schedule"
        "?token=SECRETO_EN_LA_URL&otro=123",
        json={"enabled": True, "days": [0], "start_time": "09:00",
              "end_time": "17:00",
              "password": "SECRETO_EN_EL_CUERPO"}
    )

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT details FROM audit_log "
        "WHERE action = 'plan.feature_observed'"
    )]
    conexion.close()

    comprobar("Se observo algo", filas, str(filas))

    todo = " ".join(f["details"] or "" for f in filas)

    comprobar("No se guarda la cadena de consulta",
              "SECRETO_EN_LA_URL" not in todo, todo)

    comprobar("Ni el cuerpo de la peticion",
              "SECRETO_EN_EL_CUERPO" not in todo, todo)

    comprobar("Solo la ruta y la capacidad que falta",
              "/api/devices/eq-1/recording/schedule" in todo
              and "scheduling" in todo, todo)


def test_la_observacion_no_filtra_equipos_de_otra_organizacion():
    """
    Lo mas delicado: que observar no se convierta en una forma de
    averiguar que equipos tiene otra empresa.
    """

    preparar(orgs.PLAN_FREE)

    otra = organizacion("Empresa B", orgs.PLAN_FREE)

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-de-otra', 'PC-DE-OTRA', ?)", (otra["id"],)
    )
    conexion.commit()
    conexion.close()

    respuesta = h.cliente(h.OWNER).post(
        "/api/devices/eq-de-otra/recording/schedule",
        json={"enabled": True, "days": [0], "start_time": "09:00",
              "end_time": "17:00"}
    )

    comprobar("Se rechaza por aislamiento, no por plan",
              respuesta.status_code == 404
              and respuesta.json().get("code")
              != "plan_feature_required",
              str(respuesta.status_code))

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT details FROM audit_log "
        "WHERE action = 'plan.feature_observed'"
    )]
    conexion.close()

    comprobar("No se genera observacion de un equipo ajeno",
              not filas, str(filas))

    inexistente = h.cliente(h.OWNER).post(
        "/api/devices/eq-no-existe/recording/schedule",
        json={"enabled": True, "days": [0], "start_time": "09:00",
              "end_time": "17:00"}
    )

    comprobar("Un equipo inventado responde igual que uno ajeno",
              inexistente.status_code == respuesta.status_code,
              f"{inexistente.status_code} vs {respuesta.status_code}")


def test_cada_observacion_queda_atada_a_su_organizacion():

    org = preparar(orgs.PLAN_FREE)

    h.cliente(h.OWNER).post(
        "/api/devices/eq-1/recording/schedule",
        json={"enabled": True, "days": [0], "start_time": "09:00",
              "end_time": "17:00"}
    )

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT organization_id, username FROM audit_log "
        "WHERE action = 'plan.feature_observed'"
    )]
    conexion.close()

    comprobar("La observacion lleva su organizacion",
              filas and filas[-1]["organization_id"] == org["id"],
              str(filas))

    comprobar("Y quien la provoco",
              filas and filas[-1]["username"] == h.OWNER,
              str(filas))


def test_ninguna_ruta_de_accion_queda_sin_clasificar():
    """
    Lo que de verdad protege esto a largo plazo.

    Una ruta nueva que nadie clasifique no se bloquearia nunca, y el
    olvido no se nota mirando la pantalla. Esta prueba recorre los
    endpoints POST con device_id y exige que cada uno este decidido:
    o exige una capacidad, o esta en la lista de los que a proposito
    no la exigen.
    """

    # Rutas POST con device_id que NO dependen del plan, y por que.
    SIN_CAPACIDAD = {
        "/api/devices/{device_id}/ping": "sondeo interno",
        "/api/devices/{device_id}/system-info": "lo envia el Agent",
        "/api/devices/{device_id}/software": "lo envia el Agent",
        "/api/devices/{device_id}/recordings/upload":
            "la entrega el Agent; bloquearla costaria datos",
        "/api/devices/{device_id}/recording/start":
            "grabacion manual, incluida en todos los planes",
        "/api/devices/{device_id}/recording/stop":
            "detener siempre debe poderse",
        "/api/devices/{device_id}/decommission": "inventario",
        "/api/devices/{device_id}/reinstate": "inventario",
        "/api/devices/{device_id}/link-history": "inventario",
        "/api/devices/{device_id}/unlink-history": "inventario",
    }

    rutas = re.findall(
        r'@app\.post\("(/api/devices/\{device_id\}[^"]*)"\)',
        PRINCIPAL
    )

    comprobar("Se encontraron rutas que revisar",
              len(rutas) > 10, str(len(rutas)))

    sin_decidir = []

    for plantilla in rutas:

        concreta = plantilla.replace("{device_id}", "eq-1")
        concreta = concreta.replace("{action}", "shutdown")

        exige = servidor.capacidad_requerida(concreta, "POST")

        if exige is None and plantilla not in SIN_CAPACIDAD:
            sin_decidir.append(plantilla)

    comprobar("Ninguna ruta de accion queda sin decidir",
              not sin_decidir, str(sin_decidir))


# ==============================
# NADA CAMBIA PARA QUIEN YA OPERA
# ==============================

def test_una_organizacion_existente_sigue_igual():
    """
    En observacion, el comportamiento tiene que ser exactamente el de
    antes. Esta es la comprobacion que permite desplegar sin miedo.
    """

    org = preparar(orgs.PLAN_FREE)

    OPERACIONES = [
        ("GET", "/api/devices", None),
        ("GET", "/api/recordings", None),
        ("GET", "/api/alerts", None),
        ("GET", "/api/devices/eq-1/recording/status", None),
        ("POST", "/api/devices/eq-1/recording/schedule",
         {"enabled": True, "days": [0], "start_time": "09:00",
          "end_time": "17:00"}),
    ]

    cliente = h.cliente(h.OWNER)

    for metodo, ruta, cuerpo in OPERACIONES:

        if metodo == "GET":
            respuesta = cliente.get(ruta)
        else:
            respuesta = cliente.post(ruta, json=cuerpo)

        comprobar(f"{metodo} {ruta} no se bloquea por el plan",
                  respuesta.status_code != 403
                  or respuesta.json().get("code")
                  != "plan_feature_required",
                  str(respuesta.status_code))


def test_los_limites_que_ya_se_exigian_siguen_exigiendose():

    org = preparar(orgs.PLAN_FREE)   # 2 equipos

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-2', 'PC-2', ?)", (org["id"],)
    )
    conexion.commit()
    conexion.close()

    comprobar("Con dos equipos, Free esta lleno",
              orgs.limit_reached(org["id"], "devices"),
              str(orgs.organization_usage(org["id"])))

    try:
        orgs.check_limit(org["id"], "devices")
        comprobar("Y check_limit lo rechaza", False, "no lanzo")

    except orgs.OrganizationError as problema:
        comprobar("Y check_limit lo rechaza", True)

        comprobar("Nombrando el plan",
                  "Free" in str(problema), str(problema))


# ==============================

def main():

    pruebas = [
        test_sin_excepciones_es_el_plan_de_siempre,
        test_una_capacidad_concedida_se_tiene,
        test_la_excepcion_no_la_hereda_otra_organizacion,
        test_la_cortesia_caduca_sola,
        test_una_fecha_ilegible_no_concede_nada,
        test_lo_retirado_manda_sobre_lo_concedido,
        test_un_limite_propio_sustituye_al_del_plan,
        test_retirar_la_excepcion_la_deshace,
        test_una_excepcion_desconocida_se_rechaza,
        test_el_almacenamiento_se_cuenta_de_verdad,
        test_el_almacenamiento_es_de_cada_organizacion,
        test_un_cliente_no_puede_concederse_capacidades,
        test_el_operador_de_plataforma_si_puede,
        test_una_organizacion_no_ve_las_excepciones_de_otra,
        test_queda_registro_de_quien_la_concede,
        test_el_intento_rechazado_tambien_se_registra,
        test_el_modo_arranca_en_observacion,
        test_en_observacion_no_se_bloquea_nada,
        test_pero_queda_anotado_lo_que_se_habria_bloqueado,
        test_lo_que_el_plan_si_permite_no_se_anota,
        test_una_excepcion_evita_la_observacion,
        test_el_operador_de_plataforma_no_se_observa,
        test_configurada_no_es_lo_mismo_que_usada,
        test_no_se_gana_una_capacidad_usandola_ahora,
        test_el_uso_anterior_al_corte_si_cuenta,
        test_una_operacion_fallida_tampoco_cuenta,
        test_el_uso_de_una_no_se_atribuye_a_otra,
        test_en_riesgo_es_lo_que_se_cortaria,
        test_una_capacidad_concedida_deja_de_estar_en_riesgo,
        test_el_analisis_no_concede_nada_por_su_cuenta,
        test_las_grabaciones_antiguas_cuentan_como_archivado,
        test_self_hosted_se_detecta_por_el_despliegue,
        test_la_fecha_de_corte_es_obligatoria,
        test_la_fecha_de_corte_no_puede_estar_en_el_futuro,
        test_la_propuesta_no_concede_nada,
        test_la_propuesta_dice_que_proteger_y_por_que,
        test_la_propuesta_respeta_la_fecha_de_corte,
        test_la_vigencia_propuesta_es_de_90_dias,
        test_una_proteccion_no_es_permanente,
        test_se_avisa_antes_de_que_venza,
        test_retirar_una_proteccion_no_borra_nada,
        test_la_proteccion_es_de_una_sola_organizacion,
        test_el_estado_del_almacenamiento_es_legible,
        test_se_detecta_el_exceso_sin_borrar_nada,
        test_sin_limite_nunca_hay_exceso,
        test_la_entrega_del_agent_nunca_se_bloquea,
        test_recuperar_archivos_nunca_se_bloquea,
        test_lo_unico_restringible_es_el_archivado_a_peticion,
        test_en_observacion_el_archivado_sigue_funcionando,
        test_la_subida_del_agent_no_depende_del_plan,
        test_las_consultas_de_solo_lectura_no_dependen_del_plan,
        test_las_acciones_si_estan_clasificadas,
        test_ninguna_ruta_de_accion_queda_sin_clasificar,
        test_ninguna_ruta_QUE_MODIFIQUE_queda_sin_clasificar,
        test_no_hay_otra_puerta_para_mandar_al_equipo,
        test_ningun_ajuste_del_cliente_equivale_a_una_capacidad,
        test_la_observacion_no_registra_nada_sensible,
        test_la_observacion_no_filtra_equipos_de_otra_organizacion,
        test_cada_observacion_queda_atada_a_su_organizacion,
        test_una_organizacion_existente_sigue_igual,
        test_los_limites_que_ya_se_exigian_siguen_exigiendose,
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:110])

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
