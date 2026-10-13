"""
Vincular un equipo reinstalado con su historial anterior.

    .venv\\Scripts\\python tests/test_vinculo_historial.py

Como se representa la vinculacion, y por que importa para estas
pruebas:

    NO se reasignan las grabaciones. El equipo NUEVO anota a quien
    sucede, y cada grabacion conserva su device_id, su ruta en disco y
    su nombre. La vinculacion es un dato nuevo, no una enmienda al
    pasado.

De ahi que la mitad de estas comprobaciones sean sobre lo que NO
cambia: si alguna grabacion se moviera de sitio, el historial habria
quedado alterado aunque el panel se viera bien.

La otra mitad son los casos que deben RECHAZARSE. Un historial
vinculado a la maquina equivocada es peor que uno partido en dos.

Base temporal. No se toca produccion ni se borra nada.
"""

import io
import os
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("REMOTEADMIN_PUBLIC_URL", "https://panel.ejemplo.com")

import users_harness as h  # noqa: E402

import backend.database as database  # noqa: E402
import backend.devices as devices  # noqa: E402
import backend.organizations as orgs  # noqa: E402
import backend.users as users  # noqa: E402


HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDelVinculo2026!"


def organizacion(nombre):

    for existente in orgs.list_organizations():
        if existente["name"] == nombre:
            return existente

    return orgs.create_organization(
        nombre, plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )


def equipo(device_id, hostname, organization_id, grabaciones=0):

    conexion = database.get_connection()

    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id, "
        "created_at) VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00')",
        (device_id, hostname, organization_id)
    )

    for i in range(grabaciones):
        conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, "
            "storage_state, keep) VALUES (?, ?, "
            "'2026-10-09T10:00:00+00:00', '2026-10-09T10:02:00+00:00', "
            "120, 1000000, 'stored', 'server_stored', 0)",
            (device_id, f"{device_id}/g{i}.mp4")
        )

    conexion.commit()
    conexion.close()


def preparar():
    """Un equipo viejo con historial, dado de baja, y uno nuevo."""

    h.reiniciar()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    org = organizacion("Empresa")
    users.set_organization(h.OWNER, org["id"])

    equipo("eq-viejo", "PC-CONTABILIDAD", org["id"], grabaciones=3)
    equipo("eq-nuevo", "PC-CONTABILIDAD", org["id"], grabaciones=1)

    token_nuevo = devices.issue_agent_token("eq-nuevo")

    # El viejo se da de baja por la via normal
    h.cliente(h.OWNER).post("/api/devices/eq-viejo/decommission")

    return org, token_nuevo


def vincular(nuevo="eq-nuevo", anterior="eq-viejo", quien=None):

    return h.cliente(quien or h.OWNER).post(
        f"/api/devices/{nuevo}/link-history",
        json={"previous_device_id": anterior}
    )


# ==============================
# LA VINCULACION FUNCIONA
# ==============================

def test_se_puede_vincular_un_equipo_reinstalado():

    preparar()

    respuesta = vincular()

    comprobar("La vinculacion responde bien",
              respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Y dice a quien sucede",
              respuesta.json().get("previous_device_id") == "eq-viejo")

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT succeeds_device_id FROM devices "
        "WHERE device_id = 'eq-nuevo'"
    ).fetchone()
    conexion.close()

    comprobar("Queda anotado en el equipo nuevo",
              fila["succeeds_device_id"] == "eq-viejo",
              str(dict(fila)))


def test_la_cadena_lleva_al_historial_anterior():

    preparar()
    vincular()

    cadena = devices.device_history_chain("eq-nuevo")

    comprobar("La cadena incluye los dos",
              cadena == ["eq-nuevo", "eq-viejo"], str(cadena))

    comprobar("Y el viejo por si solo no arrastra al nuevo",
              devices.device_history_chain("eq-viejo") == ["eq-viejo"],
              str(devices.device_history_chain("eq-viejo")))


# ==============================
# EL HISTORIAL NO SE TOCA
# ==============================

def test_ninguna_grabacion_cambia_de_equipo():
    """
    Lo esencial del diseño. Si al vincular se reasignaran las
    grabaciones, el historial habria quedado reescrito: las rutas en
    disco llevan el device_id antiguo.
    """

    preparar()

    conexion = database.get_connection()
    antes = sorted(
        (f["device_id"], f["path"])
        for f in conexion.execute(
            "SELECT device_id, path FROM recordings"
        )
    )
    conexion.close()

    vincular()

    conexion = database.get_connection()
    despues = sorted(
        (f["device_id"], f["path"])
        for f in conexion.execute(
            "SELECT device_id, path FROM recordings"
        )
    )
    conexion.close()

    comprobar("Ni una grabacion cambia de equipo ni de ruta",
              antes == despues,
              f"{len(antes)} antes, {len(despues)} despues")

    comprobar("Las del equipo viejo siguen siendo suyas",
              sum(1 for d, _ in despues if d == "eq-viejo") == 3,
              str(despues))


def test_el_equipo_viejo_no_resucita():
    """Vincular no puede devolverle la vida al equipo anterior."""

    preparar()
    vincular()

    comprobar("Sigue dado de baja",
              devices.is_decommissioned("eq-viejo"))

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT agent_token_active, hostname FROM devices "
        "WHERE device_id = 'eq-viejo'"
    ).fetchone()
    conexion.close()

    comprobar("Con su credencial revocada",
              not fila["agent_token_active"],
              str(fila["agent_token_active"]))

    comprobar("Y conservando su nombre",
              fila["hostname"] == "PC-CONTABILIDAD")


def test_el_equipo_nuevo_conserva_su_credencial():
    """
    La credencial del nuevo es la que obtuvo al darse de alta. Nunca
    se recupera la del viejo.
    """

    org, token_nuevo = preparar()

    comprobar("Antes de vincular, la del nuevo vale",
              devices.get_device_id_for_token(token_nuevo) == "eq-nuevo")

    vincular()

    comprobar("Despues, sigue valiendo",
              devices.get_device_id_for_token(token_nuevo) == "eq-nuevo")


def test_deshacer_no_pierde_nada():

    preparar()
    vincular()

    conexion = database.get_connection()
    antes = conexion.execute(
        "SELECT COUNT(*) FROM recordings"
    ).fetchone()[0]
    conexion.close()

    respuesta = h.cliente(h.OWNER).post(
        "/api/devices/eq-nuevo/unlink-history"
    )

    comprobar("Deshacer responde bien", respuesta.status_code == 200)

    comprobar("Y dice que estaba vinculado",
              respuesta.json().get("was_linked") is True)

    conexion = database.get_connection()
    despues = conexion.execute(
        "SELECT COUNT(*) FROM recordings"
    ).fetchone()[0]
    fila = conexion.execute(
        "SELECT succeeds_device_id FROM devices "
        "WHERE device_id = 'eq-nuevo'"
    ).fetchone()
    conexion.close()

    comprobar("No se perdio ninguna grabacion",
              despues == antes == 4, f"{antes} -> {despues}")

    comprobar("Y los historiales vuelven a estar separados",
              fila["succeeds_device_id"] is None)


# ==============================
# LO QUE DEBE RECHAZARSE
# ==============================

def test_no_se_hereda_de_un_equipo_en_servicio():
    """
    Heredar de uno activo dejaria el historial asociado a dos
    maquinas a la vez.
    """

    org, _ = preparar()

    equipo("eq-activo", "PC-ACTIVO", org["id"], grabaciones=2)

    respuesta = vincular(anterior="eq-activo")

    comprobar("Se rechaza", respuesta.status_code == 409,
              str(respuesta.status_code))

    comprobar("Diciendo por que",
              "dado de baja" in respuesta.json().get("message", ""),
              str(respuesta.json()))


def test_un_historial_solo_tiene_un_heredero():

    org, _ = preparar()

    equipo("eq-otro", "PC-OTRO", org["id"])

    comprobar("El primero se vincula",
              vincular().status_code == 200)

    segunda = vincular(nuevo="eq-otro")

    comprobar("El segundo se rechaza",
              segunda.status_code == 409, str(segunda.status_code))

    comprobar("Diciendo que ya esta vinculado",
              "ya esta vinculado" in segunda.json().get("message", ""),
              str(segunda.json()))


def test_un_equipo_no_hereda_dos_historiales():

    org, _ = preparar()

    equipo("eq-viejo2", "PC-VIEJO-2", org["id"], grabaciones=1)
    h.cliente(h.OWNER).post("/api/devices/eq-viejo2/decommission")

    comprobar("El primero se vincula",
              vincular().status_code == 200)

    segunda = vincular(anterior="eq-viejo2")

    comprobar("El segundo se rechaza",
              segunda.status_code == 409, str(segunda.status_code))


def test_nadie_se_sucede_a_si_mismo():

    preparar()

    respuesta = vincular(nuevo="eq-nuevo", anterior="eq-nuevo")

    comprobar("Se rechaza", respuesta.status_code == 409,
              str(respuesta.status_code))


def test_un_equipo_de_baja_no_hereda():

    preparar()

    respuesta = vincular(nuevo="eq-viejo", anterior="eq-viejo")

    comprobar("Un equipo de baja no puede heredar",
              respuesta.status_code == 409, str(respuesta.status_code))


def test_un_equipo_que_no_existe():

    preparar()

    respuesta = vincular(anterior="eq-inventado")

    comprobar("Se rechaza sin confirmar que exista",
              respuesta.status_code in (404, 409),
              str(respuesta.status_code))


# ==============================
# AISLAMIENTO Y PERMISOS
# ==============================

def test_no_se_hereda_el_historial_de_otra_organizacion():
    """Lo mas grave que podria salir mal."""

    org, _ = preparar()

    otra = organizacion("Ajena")

    equipo("eq-ajeno", "PC-AJENO", otra["id"], grabaciones=5)

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET decommissioned_at = "
        "'2026-10-01T00:00:00+00:00' WHERE device_id = 'eq-ajeno'"
    )
    conexion.commit()
    conexion.close()

    respuesta = vincular(anterior="eq-ajeno")

    comprobar("Se rechaza heredar de otra organizacion",
              respuesta.status_code in (404, 409),
              str(respuesta.status_code))

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT succeeds_device_id FROM devices "
        "WHERE device_id = 'eq-nuevo'"
    ).fetchone()
    conexion.close()

    comprobar("Y no queda vinculado",
              fila["succeeds_device_id"] is None,
              str(fila["succeeds_device_id"]))


def test_el_listado_solo_muestra_los_propios():

    org, _ = preparar()

    otra = organizacion("Ajena")
    equipo("eq-ajeno2", "PC-AJENO-2", otra["id"])

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET decommissioned_at = "
        "'2026-10-01T00:00:00+00:00' WHERE device_id = 'eq-ajeno2'"
    )
    conexion.commit()
    conexion.close()

    lista = h.cliente(h.OWNER).get(
        "/api/devices/decommissioned"
    ).json()["devices"]

    identificadores = [e["device_id"] for e in lista]

    comprobar("Sale el propio", "eq-viejo" in identificadores,
              str(identificadores))

    comprobar("Y NO el de la otra organizacion",
              "eq-ajeno2" not in identificadores,
              str(identificadores))


def test_los_ya_heredados_no_vuelven_a_ofrecerse():

    preparar()

    vincular()

    lista = h.cliente(h.OWNER).get(
        "/api/devices/decommissioned"
    ).json()["devices"]

    comprobar("El historial ya heredado desaparece de la lista",
              "eq-viejo" not in [e["device_id"] for e in lista],
              str([e["device_id"] for e in lista]))


def test_hace_falta_el_permiso():

    org, _ = preparar()

    sin_permiso = [p for p in users.PERMISSIONS if p != "devices.manage"]

    users.create_user("sub_sin_vinculo", CLAVE,
                      permissions=sin_permiso,
                      organization_id=org["id"])

    respuesta = vincular(quien="sub_sin_vinculo")

    comprobar("Sin devices.manage se rechaza",
              respuesta.status_code == 403, str(respuesta.status_code))

    listado = h.cliente("sub_sin_vinculo").get(
        "/api/devices/decommissioned"
    )

    comprobar("Y tampoco puede ver la lista",
              listado.status_code == 403, str(listado.status_code))


def test_otra_organizacion_no_vincula_equipos_ajenos():

    org, _ = preparar()

    otra = organizacion("Ajena")

    users.create_user("ajeno", CLAVE,
                      permissions=list(users.PERMISSIONS),
                      organization_id=otra["id"])

    respuesta = vincular(quien="ajeno")

    comprobar("Se rechaza", respuesta.status_code in (403, 404),
              str(respuesta.status_code))


# ==============================
# AUDITORIA
# ==============================

def test_queda_registro_de_la_vinculacion():

    preparar()
    vincular()

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT action, device_id, details FROM audit_log "
        "WHERE action = 'device.history_linked'"
    )]
    conexion.close()

    comprobar("Se registra", len(filas) == 1, str(filas))

    if filas:
        comprobar("Diciendo de quien hereda",
                  "eq-viejo" in (filas[0]["details"] or ""),
                  str(filas[0]))


def test_tambien_se_registra_el_rechazo():
    """
    Un intento rechazado de heredar un historial ajeno es justo lo que
    se querria encontrar despues.
    """

    org, _ = preparar()

    otra = organizacion("Ajena")
    equipo("eq-ajeno3", "PC-AJENO-3", otra["id"])

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE devices SET decommissioned_at = "
        "'2026-10-01T00:00:00+00:00' WHERE device_id = 'eq-ajeno3'"
    )
    conexion.commit()
    conexion.close()

    vincular(anterior="eq-ajeno3")

    conexion = database.get_connection()
    filas = [dict(f) for f in conexion.execute(
        "SELECT status, details FROM audit_log "
        "WHERE action = 'device.history_linked'"
    )]
    conexion.close()

    comprobar("El rechazo queda registrado",
              any("Rechazada" in (f["details"] or "") for f in filas),
              str(filas))


def test_deshacer_tambien_se_registra():

    preparar()
    vincular()

    h.cliente(h.OWNER).post("/api/devices/eq-nuevo/unlink-history")

    conexion = database.get_connection()
    cuantos = conexion.execute(
        "SELECT COUNT(*) FROM audit_log "
        "WHERE action = 'device.history_unlinked'"
    ).fetchone()[0]
    conexion.close()

    comprobar("Queda registro", cuantos == 1, str(cuantos))


# ==============================
# EL PANEL
# ==============================

def test_el_panel_tiene_el_paso():

    comprobar("Hay bloque de vinculacion",
              'id="link-history-box"' in HTML)

    comprobar("Con lista de equipos de baja",
              'id="link-history-select"' in HTML)

    comprobar("Y boton",
              'id="link-history-button"' in HTML)

    comprobar("Mas el de deshacer",
              'id="unlink-history-button"' in HTML)


def test_la_confirmacion_explica_lo_que_pasa():

    bloque = JS.split("async function vincularHistorial", 1)[1].split(
        "\nasync function", 1
    )[0]

    comprobar("Hay confirmacion", "window.confirm" in bloque)

    comprobar("Nombra los dos equipos",
              "nombreActual" in bloque and "nombreAnterior" in bloque)

    comprobar("Dice que las grabaciones NO se mueven",
              "NO se mueven" in bloque)

    comprobar("Y avisa de que solo vale si es la misma maquina",
              "misma maquina" in bloque)


def test_el_panel_pinta_con_textContent():
    """Los nombres los pone el equipo; no pueden ir como marcado."""

    bloque = JS.split("function pintarVinculacion", 1)[1].split(
        "\nasync function", 1
    )[0]

    comprobar("Las opciones se rellenan con textContent",
              "opcion.textContent" in bloque
              and "innerHTML =" not in bloque.replace(
                  'lista.innerHTML = "";', ""
              ),
              bloque[-400:])


def test_el_boton_solo_para_quien_puede():

    bloque = JS.split("function pintarVinculacion", 1)[1].split(
        "\nasync function", 1
    )[0]

    comprobar("Se comprueba el permiso en el panel",
              'puede("devices.manage")' in bloque)


# ==============================

def main():

    pruebas = [
        test_se_puede_vincular_un_equipo_reinstalado,
        test_la_cadena_lleva_al_historial_anterior,
        test_ninguna_grabacion_cambia_de_equipo,
        test_el_equipo_viejo_no_resucita,
        test_el_equipo_nuevo_conserva_su_credencial,
        test_deshacer_no_pierde_nada,
        test_no_se_hereda_de_un_equipo_en_servicio,
        test_un_historial_solo_tiene_un_heredero,
        test_un_equipo_no_hereda_dos_historiales,
        test_nadie_se_sucede_a_si_mismo,
        test_un_equipo_de_baja_no_hereda,
        test_un_equipo_que_no_existe,
        test_no_se_hereda_el_historial_de_otra_organizacion,
        test_el_listado_solo_muestra_los_propios,
        test_los_ya_heredados_no_vuelven_a_ofrecerse,
        test_hace_falta_el_permiso,
        test_otra_organizacion_no_vincula_equipos_ajenos,
        test_queda_registro_de_la_vinculacion,
        test_tambien_se_registra_el_rechazo,
        test_deshacer_tambien_se_registra,
        test_el_panel_tiene_el_paso,
        test_la_confirmacion_explica_lo_que_pasa,
        test_el_panel_pinta_con_textContent,
        test_el_boton_solo_para_quien_puede,
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
