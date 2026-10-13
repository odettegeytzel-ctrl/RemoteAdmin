"""
Baja reversible de equipos.

    .venv\\Scripts\\python tests/test_baja_equipos.py

Dar de baja NO es borrar. Lo que se retira es el acceso: la
credencial queda revocada y el equipo deja de contar para el plan.
Lo que se queda es todo lo demas, y eso es justo lo que hay que
demostrar, porque es lo facil de romper sin darse cuenta.

Base temporal: no se toca la real, ni se borra ningun equipo ni
ninguna grabacion de verdad.
"""

import atexit
import io
import os
import shutil
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("REMOTEADMIN_PUBLIC_URL", "https://panel.ejemplo.com")

# El andamiaje primero: apunta la base a una temporal. Si un modulo de
# backend se carga antes, queda enlazado a la base REAL.
import users_harness as h  # noqa: E402

import backend.audit as audit  # noqa: E402
import backend.database as database  # noqa: E402

# El respaldo de auditoria se escribe en un archivo. Sin esto iria al
# del proyecto, al lado de los datos de verdad: la base ya era
# temporal, el archivo no lo era.
_CARPETA_RESPALDO = tempfile.mkdtemp(prefix="auditoria-pruebas-")

audit.RESPALDO_AUDITORIA = os.path.join(
    _CARPETA_RESPALDO, "auditoria_pendiente.jsonl"
)

atexit.register(shutil.rmtree, _CARPETA_RESPALDO, True)
import backend.devices as devices  # noqa: E402
import backend.organizations as orgs  # noqa: E402
import backend.users as users  # noqa: E402


HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeLaBaja2026!"


def organizacion(nombre, plan=None):

    for existente in orgs.list_organizations():
        if existente["name"] == nombre:
            return existente

    return orgs.create_organization(
        nombre, plan=plan or orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )


def preparar(plan=None):
    """Una organizacion con un equipo, su credencial y sus grabaciones."""

    h.reiniciar()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    org = organizacion("Empresa", plan)

    if plan:
        orgs.update_organization(org["id"], plan=plan)

    users.set_organization(h.OWNER, org["id"])

    conexion = database.get_connection()

    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id, "
        "status, created_at) VALUES ('eq-1', 'PC-CONTABILIDAD', ?, "
        "'online', '2026-01-01T00:00:00+00:00')", (org["id"],)
    )

    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, ended_at, "
        "duration_sec, size_bytes, status, storage_state, keep) VALUES "
        "('eq-1', 'eq-1/a.mp4', '2026-10-09T10:00:00+00:00', "
        "'2026-10-09T10:02:18+00:00', 138, 3145776, 'stored', "
        "'server_stored', 0)"
    )

    conexion.execute(
        "INSERT INTO alerts (device_id, type, message, created_at) "
        "VALUES ('eq-1', 'disk', 'Disco al limite', "
        "'2026-10-09T10:00:00+00:00')"
    )

    conexion.commit()
    conexion.close()

    token = devices.issue_agent_token("eq-1")

    return org, token


# ==============================
# LA BAJA RETIRA EL ACCESO
# ==============================

def test_la_credencial_deja_de_valer():
    """
    Lo esencial. Si el Agent pudiera seguir autenticandose, la baja
    seria solo un cambio de aspecto.
    """

    org, token = preparar()

    comprobar("Antes de la baja, la credencial vale",
              devices.get_device_id_for_token(token) == "eq-1")

    respuesta = h.cliente(h.OWNER).post(
        "/api/devices/eq-1/decommission"
    )

    comprobar("La baja responde bien", respuesta.status_code == 200,
              str(respuesta.status_code))

    comprobar("Y dice que no estaba ya de baja",
              respuesta.json().get("already_decommissioned") is False)

    comprobar("Despues, la credencial NO vale",
              devices.get_device_id_for_token(token) is None)


def test_un_equipo_de_baja_no_puede_volver_a_latir():
    """Con el token revocado, ninguna via del Agent debe funcionar."""

    org, token = preparar()

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    sin_sesion = h.cliente()

    # El cuerpo va completo a proposito: lo que se mide es el
    # rechazo por credencial, no un error de validacion.
    cuerpo = {"device_id": "eq-1", "ip_address": "10.0.0.5"}

    respuesta = sin_sesion.post(
        "/api/devices/heartbeat",
        json=cuerpo,
        headers={"X-Agent-Token": token}
    )

    comprobar("El heartbeat con la credencial vieja se rechaza",
              respuesta.status_code in (401, 403),
              str(respuesta.status_code))

    # Y antes de la baja SI funcionaba, para que la comprobacion
    # anterior no pase por un motivo equivocado.
    otro_org, otro_token = preparar()

    previo = h.cliente().post(
        "/api/devices/heartbeat",
        json=cuerpo,
        headers={"X-Agent-Token": otro_token}
    )

    comprobar("Con la credencial viva, el heartbeat se acepta",
              previo.status_code == 200, str(previo.status_code))


# ==============================
# LA BAJA NO BORRA NADA
# ==============================

def test_la_fila_del_equipo_se_conserva():

    org, _ = preparar()

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT device_id, hostname, created_at, organization_id, "
        "decommissioned_at FROM devices WHERE device_id = 'eq-1'"
    ).fetchone()
    conexion.close()

    comprobar("El equipo sigue en la base", fila is not None)

    if not fila:
        return

    comprobar("Con su nombre", fila["hostname"] == "PC-CONTABILIDAD")

    comprobar("Con su fecha de alta",
              fila["created_at"] == "2026-01-01T00:00:00+00:00")

    comprobar("Con su organizacion",
              fila["organization_id"] == org["id"])

    comprobar("Y con la fecha de baja anotada",
              bool(fila["decommissioned_at"]), str(fila["decommissioned_at"]))


def test_las_grabaciones_se_conservan_con_su_nombre():
    """
    El listado saca el hostname de la fila del equipo con un LEFT
    JOIN. Si la baja borrara la fila, las grabaciones historicas se
    quedarian sin nombre.
    """

    org, _ = preparar()

    antes = h.cliente(h.OWNER).get("/api/recordings").json()["recordings"]

    comprobar("Hay una grabacion antes", len(antes) == 1)

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    despues = h.cliente(h.OWNER).get(
        "/api/recordings"
    ).json()["recordings"]

    comprobar("Sigue habiendo una despues", len(despues) == 1,
              str(len(despues)))

    if not despues:
        return

    comprobar("Y conserva el nombre del equipo",
              despues[0].get("hostname") == "PC-CONTABILIDAD",
              str(despues[0].get("hostname")))

    comprobar("Con su tamaño y duracion intactos",
              despues[0]["size_bytes"] == 3145776
              and despues[0]["duration_sec"] == 138)


def test_las_alertas_y_el_archivo_se_conservan():

    org, _ = preparar()

    conexion = database.get_connection()
    antes = conexion.execute(
        "SELECT COUNT(*) FROM alerts WHERE device_id = 'eq-1'"
    ).fetchone()[0]
    conexion.close()

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    conexion = database.get_connection()
    despues = conexion.execute(
        "SELECT COUNT(*) FROM alerts WHERE device_id = 'eq-1'"
    ).fetchone()[0]
    grabaciones = conexion.execute(
        "SELECT COUNT(*) FROM recordings WHERE device_id = 'eq-1'"
    ).fetchone()[0]
    conexion.close()

    comprobar("Las alertas se conservan",
              despues == antes == 1, f"{antes} -> {despues}")

    comprobar("Y las grabaciones en la base tambien",
              grabaciones == 1, str(grabaciones))


def test_queda_registro_en_la_auditoria():

    org, _ = preparar()

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT action, device_id, details FROM audit_log "
        "WHERE action = 'device.decommissioned'"
    )]
    conexion.close()

    comprobar("La baja se registra", len(filas) == 1, str(filas))

    if filas:
        comprobar("Con el equipo al que afecta",
                  filas[0]["device_id"] == "eq-1")


# ==============================
# EL CUPO DEL PLAN
# ==============================

def test_la_baja_libera_la_plaza_del_plan():
    """
    Si un equipo de baja siguiera contando, dar de baja no serviria de
    nada: el plan quedaria lleno igual.
    """

    org, _ = preparar(plan=orgs.PLAN_FREE)   # Free: 2 equipos

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-2', 'PC-DOS', ?)", (org["id"],)
    )
    conexion.commit()
    conexion.close()

    comprobar("Con dos equipos, el plan Free esta lleno",
              orgs.limit_reached(org["id"], "devices"),
              str(orgs.organization_usage(org["id"])))

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    comprobar("Tras la baja, hay sitio otra vez",
              not orgs.limit_reached(org["id"], "devices"),
              str(orgs.organization_usage(org["id"])))

    comprobar("Y el uso cuenta solo uno",
              orgs.organization_usage(org["id"])["devices"] == 1,
              str(orgs.organization_usage(org["id"])))


# ==============================
# PERMISOS Y AISLAMIENTO
# ==============================

def test_hace_falta_el_permiso():
    """
    El boton del panel es comodidad. Quien lo quite en el navegador
    tiene que seguir sin poder dar de baja.
    """

    org, _ = preparar()

    sin_permiso = [p for p in users.PERMISSIONS if p != "devices.manage"]

    users.create_user("sub_sin_baja", CLAVE, permissions=sin_permiso,
                      organization_id=org["id"])

    respuesta = h.cliente("sub_sin_baja").post(
        "/api/devices/eq-1/decommission"
    )

    comprobar("Sin devices.manage se rechaza",
              respuesta.status_code == 403, str(respuesta.status_code))

    comprobar("Y el equipo sigue en servicio",
              not devices.is_decommissioned("eq-1"))


def test_una_organizacion_no_da_de_baja_equipos_de_otra():
    """Lo mas grave que podria salir mal."""

    org, _ = preparar()

    otra = organizacion("Ajena")

    users.create_user("ajeno", CLAVE,
                      permissions=list(users.PERMISSIONS),
                      organization_id=otra["id"])

    respuesta = h.cliente("ajeno").post(
        "/api/devices/eq-1/decommission"
    )

    comprobar("Se rechaza la baja de un equipo ajeno",
              respuesta.status_code in (403, 404),
              str(respuesta.status_code))

    comprobar("Y el equipo sigue en servicio",
              not devices.is_decommissioned("eq-1"))


def test_un_equipo_de_baja_no_desaparece_del_listado_ajeno():
    """Ni aparece en el de otra organizacion."""

    org, _ = preparar()

    otra = organizacion("Ajena")

    users.create_user("ajeno2", CLAVE,
                      permissions=list(users.PERMISSIONS),
                      organization_id=otra["id"])

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    lista = h.cliente("ajeno2").get("/api/devices").json()

    comprobar("La otra organizacion no lo ve",
              not any(e.get("device_id") == "eq-1" for e in lista),
              str(lista))


# ==============================
# REINCORPORACION
# ==============================

def test_reincorporar_devuelve_el_historial_pero_no_el_acceso():
    """
    La parte delicada: reincorporar NO puede resucitar una credencial
    revocada. Si lo hiciera, dar de baja no protegeria de un equipo
    robado.
    """

    org, token = preparar()

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    respuesta = h.cliente(h.OWNER).post("/api/devices/eq-1/reinstate")

    comprobar("La reincorporacion responde bien",
              respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Y dice que estaba de baja",
              respuesta.json().get("was_decommissioned") is True)

    comprobar("El equipo vuelve a estar en servicio",
              not devices.is_decommissioned("eq-1"))

    comprobar("Con su historial intacto",
              len(h.cliente(h.OWNER).get(
                  "/api/recordings").json()["recordings"]) == 1)

    # Lo que NO vuelve
    comprobar("La credencial vieja SIGUE revocada",
              devices.get_device_id_for_token(token) is None)


def test_reincorporar_vuelve_a_ocupar_plaza():

    org, _ = preparar(plan=orgs.PLAN_FREE)

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    libre = orgs.organization_usage(org["id"])["devices"]

    h.cliente(h.OWNER).post("/api/devices/eq-1/reinstate")

    ocupado = orgs.organization_usage(org["id"])["devices"]

    comprobar("Reincorporar vuelve a contar para el plan",
              ocupado == libre + 1, f"{libre} -> {ocupado}")


def test_la_reincorporacion_queda_registrada():

    org, _ = preparar()

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")
    h.cliente(h.OWNER).post("/api/devices/eq-1/reinstate")

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT action FROM audit_log WHERE action = 'device.reinstated'"
    )]
    conexion.close()

    comprobar("Queda registro", len(filas) == 1, str(filas))


# ==============================
# ESTADOS RAROS
# ==============================

def test_dar_de_baja_dos_veces_no_rompe_nada():

    org, _ = preparar()

    primera = h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")
    segunda = h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    comprobar("La segunda responde bien",
              segunda.status_code == 200, str(segunda.status_code))

    comprobar("Y dice que ya estaba de baja",
              segunda.json().get("already_decommissioned") is True)

    # Un solo registro de auditoria, no dos
    conexion = database.get_connection()
    cuantos = conexion.execute(
        "SELECT COUNT(*) FROM audit_log "
        "WHERE action = 'device.decommissioned'"
    ).fetchone()[0]
    conexion.close()

    comprobar("Solo se registra la baja que de verdad ocurrio",
              cuantos == 1, str(cuantos))


def test_un_equipo_que_no_existe():

    preparar()

    respuesta = h.cliente(h.OWNER).post(
        "/api/devices/eq-inventado/decommission"
    )

    comprobar("Se responde que no existe",
              respuesta.status_code in (403, 404),
              str(respuesta.status_code))


def test_reincorporar_uno_que_estaba_en_servicio():

    preparar()

    respuesta = h.cliente(h.OWNER).post("/api/devices/eq-1/reinstate")

    comprobar("No falla", respuesta.status_code == 200)

    comprobar("Y dice que no estaba de baja",
              respuesta.json().get("was_decommissioned") is False)


# ==============================
# FALLOS A MITAD
# ==============================

class _ConexionQueFalla:
    """
    Deja pasar todo menos la sentencia que se quiere romper.

    Sirve para provocar un fallo REAL en mitad de la baja y mirar en
    que estado queda el equipo.
    """

    def __init__(self, real, marca):
        self._real = real
        self._marca = marca

    def execute(self, sql, *args, **kwargs):

        if self._marca in sql:
            raise RuntimeError("fallo simulado")

        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)


def _con_fallo_en(marca, funcion):
    """Ejecuta funcion() con esa sentencia rota."""

    original = devices.get_connection

    devices.get_connection = lambda: _ConexionQueFalla(original(), marca)

    try:
        funcion()

    except RuntimeError:
        pass

    finally:
        devices.get_connection = original


def test_la_baja_es_todo_o_nada():
    """
    Lo que no puede pasar: quedar marcado como dado de baja
    conservando el acceso. El panel diria que el equipo esta retirado
    mientras su Agent sigue conectandose.

    Las dos escrituras van en una transaccion con un solo commit, asi
    que un fallo en cualquiera de ellas lo deja todo como estaba.
    """

    for marca, descripcion in (
            ("agent_token_active = 0", "al revocar la credencial"),
            ("decommissioned_at = ?", "al anotar la fecha")):

        org, token = preparar()

        _con_fallo_en(marca,
                      lambda: devices.decommission_device("eq-1"))

        conexion = database.get_connection()
        fila = conexion.execute(
            "SELECT agent_token_active, decommissioned_at FROM devices "
            "WHERE device_id = 'eq-1'"
        ).fetchone()
        conexion.close()

        marcado = bool(fila["decommissioned_at"])
        con_acceso = devices.get_device_id_for_token(token) is not None

        # El estado prohibido
        comprobar(f"Fallo {descripcion}: no queda de baja CON acceso",
                  not (marcado and con_acceso),
                  f"marcado={marcado} acceso={con_acceso}")

        # Y ademas, nada cambio
        comprobar(f"Fallo {descripcion}: el equipo queda como estaba",
                  not marcado and con_acceso,
                  f"marcado={marcado} acceso={con_acceso}")


def test_un_fallo_no_se_anuncia_como_exito():
    """
    Si la baja no se completo, la respuesta no puede decir que si.
    """

    org, token = preparar()

    salida = {}

    def intentar():
        salida["hecho"] = devices.decommission_device("eq-1")

    _con_fallo_en("decommissioned_at = ?", intentar)

    comprobar("La funcion no devuelve que la baja se hizo",
              salida.get("hecho") is not True,
              str(salida))

    comprobar("Y el equipo sigue sin estar de baja",
              not devices.is_decommissioned("eq-1"))


# ==============================
# SI FALLA LA AUDITORIA
# ==============================

def _sin_tabla_de_auditoria(funcion):
    """
    Ejecuta funcion() con la tabla audit_log fuera de servicio.

    Se le cambia el nombre en vez de borrarla: el INSERT falla de
    verdad —que es lo que se quiere provocar— y al terminar se deja
    todo como estaba, sin perder una sola fila.
    """

    conexion = database.get_connection()
    conexion.execute("ALTER TABLE audit_log RENAME TO audit_log_guardada")
    conexion.commit()
    conexion.close()

    try:
        return funcion()

    finally:
        conexion = database.get_connection()
        conexion.execute(
            "ALTER TABLE audit_log_guardada RENAME TO audit_log"
        )
        conexion.commit()
        conexion.close()


def test_si_falla_la_auditoria_la_baja_se_completa_igual():
    """
    La baja ya ocurrio. Devolver un error haria creer que no se hizo
    nada: el equipo quedaria sin acceso mientras el panel dice que
    sigue en servicio, que es peor que no avisar.
    """

    org, token = preparar()

    respuesta = _sin_tabla_de_auditoria(
        lambda: h.cliente(h.OWNER).post(
            "/api/devices/eq-1/decommission"
        )
    )

    comprobar("La peticion no falla",
              respuesta.status_code == 200, str(respuesta.status_code))

    datos = respuesta.json()

    comprobar("La baja se completo de verdad",
              devices.is_decommissioned("eq-1"))

    comprobar("Y la credencial quedo revocada",
              devices.get_device_id_for_token(token) is None)


def test_no_se_oculta_que_no_quedo_anotada():

    org, _ = preparar()

    respuesta = _sin_tabla_de_auditoria(
        lambda: h.cliente(h.OWNER).post(
            "/api/devices/eq-1/decommission"
        )
    )

    datos = respuesta.json()

    comprobar("La respuesta dice que no se anoto",
              datos.get("audit_recorded") is False,
              str(datos.get("audit_recorded")))

    comprobar("Y lo explica por escrito",
              "auditoria" in datos.get("message", "").lower(),
              datos.get("message", ""))


def test_el_evento_no_se_pierde():
    """
    Una linea en la consola no es un registro de auditoria. El evento
    tiene que quedar en algun sitio del que se pueda recuperar.
    """

    from backend import audit

    respaldo = Path(audit.RESPALDO_AUDITORIA)

    antes = respaldo.read_text(encoding="utf-8") if respaldo.exists()         else ""

    org, _ = preparar()

    _sin_tabla_de_auditoria(
        lambda: h.cliente(h.OWNER).post(
            "/api/devices/eq-1/decommission"
        )
    )

    comprobar("Se escribio el respaldo", respaldo.exists())

    if not respaldo.exists():
        return

    despues = respaldo.read_text(encoding="utf-8")

    nuevas = [
        linea for linea in despues[len(antes):].splitlines()
        if linea.strip()
    ]

    comprobar("Con el evento que no cupo en la base",
              any("device.decommissioned" in n for n in nuevas),
              str(nuevas[-2:]))

    # Y se puede volver a leer, que es para lo que sirve
    import json as _json

    legibles = []

    for linea in nuevas:
        try:
            legibles.append(_json.loads(linea))
        except ValueError:
            pass

    comprobar("Cada linea se puede releer",
              len(legibles) == len(nuevas), str(nuevas[:1]))

    if legibles:
        comprobar("Y lleva el equipo al que afecta",
                  any(e.get("device_id") == "eq-1" for e in legibles),
                  str(legibles[-1]))


def test_cuando_la_auditoria_funciona_se_dice_que_si():

    org, _ = preparar()

    datos = h.cliente(h.OWNER).post(
        "/api/devices/eq-1/decommission"
    ).json()

    comprobar("Se informa de que quedo anotada",
              datos.get("audit_recorded") is True,
              str(datos.get("audit_recorded")))

    comprobar("Y el aviso no aparece",
              "Aviso" not in datos.get("message", ""),
              datos.get("message", ""))


# ==============================
# EL PANEL
# ==============================

def test_el_panel_tiene_la_accion():

    comprobar("Hay bloque de baja",
              'id="decommission-panel"' in HTML)

    comprobar("Con boton de dar de baja",
              'id="decommission-button"' in HTML)

    comprobar("Y de reincorporar",
              'id="reinstate-button"' in HTML)

    comprobar("Separado de las acciones sobre la maquina",
              HTML.index('id="power-panel"')
              < HTML.index('id="decommission-panel"'))


def test_la_confirmacion_dice_que_se_conserva():
    """
    Un "¿seguro?" a secas no deja decidir. Tiene que decir que se va y
    que se queda.
    """

    bloque = JS.split("async function darDeBajaEquipo", 1)[1].split(
        "async function", 1
    )[0]

    comprobar("Hay confirmacion", "window.confirm" in bloque)

    comprobar("Nombra el equipo", "nombre" in bloque)

    comprobar("Dice que se revoca la credencial",
              "revocara su credencial" in bloque)

    comprobar("Y que se conservan las grabaciones",
              "SE CONSERVAN" in bloque)


def test_el_boton_solo_para_quien_puede():

    bloque = JS.split("function pintarBajaDeEquipo", 1)[1].split(
        "\n}\n", 1
    )[0]

    comprobar("Se comprueba el permiso",
              'puede("devices.manage")' in bloque, bloque[:300])


def test_el_estado_de_baja_llega_al_panel():

    preparar()

    h.cliente(h.OWNER).post("/api/devices/eq-1/decommission")

    lista = h.cliente(h.OWNER).get("/api/devices").json()

    equipo = [e for e in lista if e["device_id"] == "eq-1"]

    comprobar("El equipo sigue en el listado propio", equipo,
              str(lista))

    if equipo:
        comprobar("Con su fecha de baja",
                  bool(equipo[0].get("decommissioned_at")),
                  str(equipo[0].get("decommissioned_at")))


# ==============================

def main():

    pruebas = [
        test_la_credencial_deja_de_valer,
        test_un_equipo_de_baja_no_puede_volver_a_latir,
        test_la_fila_del_equipo_se_conserva,
        test_las_grabaciones_se_conservan_con_su_nombre,
        test_las_alertas_y_el_archivo_se_conservan,
        test_queda_registro_en_la_auditoria,
        test_la_baja_libera_la_plaza_del_plan,
        test_hace_falta_el_permiso,
        test_una_organizacion_no_da_de_baja_equipos_de_otra,
        test_un_equipo_de_baja_no_desaparece_del_listado_ajeno,
        test_reincorporar_devuelve_el_historial_pero_no_el_acceso,
        test_reincorporar_vuelve_a_ocupar_plaza,
        test_la_reincorporacion_queda_registrada,
        test_dar_de_baja_dos_veces_no_rompe_nada,
        test_un_equipo_que_no_existe,
        test_reincorporar_uno_que_estaba_en_servicio,
        test_la_baja_es_todo_o_nada,
        test_un_fallo_no_se_anuncia_como_exito,
        test_si_falla_la_auditoria_la_baja_se_completa_igual,
        test_no_se_oculta_que_no_quedo_anotada,
        test_el_evento_no_se_pierde,
        test_cuando_la_auditoria_funciona_se_dice_que_si,
        test_el_panel_tiene_la_accion,
        test_la_confirmacion_dice_que_se_conserva,
        test_el_boton_solo_para_quien_puede,
        test_el_estado_de_baja_llega_al_panel,
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
