"""
Las grabaciones llegan al panel.

    .venv\\Scripts\\python tests/test_grabaciones_panel.py

El fallo que origina esta suite: la seccion de Grabaciones salia
vacia para el Owner de una empresa y llena para el operador de la
plataforma, con las mismas grabaciones en la base.

La causa era la FORMA de la respuesta. /api/recordings devolvia un
objeto {"status","recordings"} en la rama general y una lista desnuda
en la rama que filtra por organizacion. El panel lee
`data.recordings`, que en una lista es undefined, asi que pintaba
cero.

Las pruebas que ya existian no lo vieron porque comprueban el
contenido —que esten las grabaciones que deben estar y no las de
otra empresa— y para eso da igual que venga envuelto o no. De ahi que
esta suite compruebe la forma, y que coincida con lo que el panel lee.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import json
import os
import re
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.database as database
import backend.main as servidor
import backend.organizations as orgs
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeGrabaciones2026!"

JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

escenario = {}


def preparar():
    """Dos empresas, cada una con un equipo y grabaciones locales."""

    h.reiniciar()

    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    propia = orgs.create_organization(
        "Propia", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    ajena = orgs.create_organization(
        "Ajena", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, propia["id"])

    users.create_user("operador", CLAVE, organization_id=None)
    users.set_platform_owner("operador", True)

    conexion = database.get_connection()

    for equipo, nombre, organizacion in (
        ("equipo-propio", "PC-PROPIA", propia["id"]),
        ("equipo-ajeno", "PC-AJENA", ajena["id"])
    ):
        conexion.execute(
            "INSERT INTO devices (device_id, hostname, organization_id) "
            "VALUES (?, ?, ?)", (equipo, nombre, organizacion)
        )

    # Tal y como estan en produccion: catalogadas pero no subidas.
    for indice in range(3):
        conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, "
            "storage_state, keep) VALUES (?, ?, ?, ?, 900, 4000000, "
            "'local', 'local_only', 0)",
            ("equipo-propio",
             f"equipo-propio/2026/10/09/rec_17915{indice}.mp4",
             f"2026-10-09T1{indice}:00:00+00:00",
             f"2026-10-09T1{indice}:15:00+00:00")
        )

    # Una ya archivada en el servidor, para cubrir los dos estados
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, ended_at, "
        "duration_sec, size_bytes, status, storage_state, keep) "
        "VALUES ('equipo-propio', "
        "'equipo-propio/2026/10/09/rec_guardada.mp4', "
        "'2026-10-09T09:00:00+00:00', '2026-10-09T09:15:00+00:00', "
        "900, 5000000, 'stored', 'server_stored', 0)"
    )

    # Y una de la otra empresa, que no debe verse
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, ended_at, "
        "duration_sec, size_bytes, status, storage_state, keep) "
        "VALUES ('equipo-ajeno', 'equipo-ajeno/2026/10/09/rec_x.mp4', "
        "'2026-10-09T10:00:00+00:00', '2026-10-09T10:15:00+00:00', "
        "900, 100, 'local', 'local_only', 0)"
    )

    conexion.commit()
    conexion.close()

    escenario.update({"propia": propia["id"], "ajena": ajena["id"]})

    return escenario


def leer(cuerpo):
    """
    Lo que el panel obtiene de la respuesta.

    Reproduce `data.recordings || []` tal cual: si la respuesta es una
    lista, el campo no existe y el panel pinta cero.
    """

    if isinstance(cuerpo, dict):
        return cuerpo.get("recordings") or []

    return []


# ==============================
# EL FALLO
# ==============================

def test_el_owner_de_una_empresa_ve_sus_grabaciones():
    """Es el caso que fallaba: la seccion salia vacia."""

    preparar()

    respuesta = h.cliente(h.OWNER).get("/api/recordings")

    comprobar("La peticion funciona", respuesta.status_code == 200,
              str(respuesta.status_code))

    cuerpo = respuesta.json()

    comprobar("La respuesta es un objeto, no una lista desnuda",
              isinstance(cuerpo, dict), type(cuerpo).__name__)

    comprobar("Y trae el campo que el panel lee",
              isinstance(cuerpo, dict) and "recordings" in cuerpo,
              str(sorted(cuerpo) if isinstance(cuerpo, dict) else cuerpo))

    pintadas = leer(cuerpo)

    comprobar("El panel pintaria las 4 grabaciones de la empresa",
              len(pintadas) == 4, str(len(pintadas)))


def test_la_forma_es_la_misma_para_los_dos_roles():
    """
    El origen del fallo: dos ramas del mismo endpoint con formas
    distintas, asi que lo que se veia dependia de quien entrara.
    """

    preparar()

    formas = {}

    for quien in (h.OWNER, "operador"):

        cuerpo = h.cliente(quien).get("/api/recordings").json()

        formas[quien] = (
            type(cuerpo).__name__,
            sorted(cuerpo) if isinstance(cuerpo, dict) else None
        )

    comprobar("Owner y operador reciben la misma forma",
              formas[h.OWNER] == formas["operador"],
              str(formas))

    comprobar("Y los dos ven grabaciones",
              leer(h.cliente(h.OWNER).get("/api/recordings").json())
              and leer(h.cliente("operador").get(
                  "/api/recordings"
              ).json()))


def test_la_forma_se_mantiene_en_todas_las_variantes():
    """
    Las ramas con filtro tambien devolvian listas desnudas. Se
    comprueban una por una porque cada `return` es una oportunidad de
    volver a romperlo.
    """

    preparar()

    cliente = h.cliente(h.OWNER)

    variantes = (
        ("sin filtros", ""),
        ("por equipo propio", "?device_id=equipo-propio"),
        ("por equipo ajeno", "?device_id=equipo-ajeno"),
        ("por equipo inexistente", "?device_id=no-existe"),
        ("por rango de fechas",
         "?start=2026-10-09T00:00:00%2B00:00&end=2026-10-10T00:00:00%2B00:00"),
        ("por equipo y rango",
         "?device_id=equipo-propio&start=2026-10-09T00:00:00%2B00:00"),
        ("con device_id vacio", "?device_id="),
    )

    for etiqueta, consulta in variantes:

        respuesta = cliente.get("/api/recordings" + consulta)

        cuerpo = respuesta.json()

        comprobar(f"{etiqueta}: responde 200",
                  respuesta.status_code == 200, str(respuesta.status_code))

        comprobar(f"{etiqueta}: es un objeto con 'recordings'",
                  isinstance(cuerpo, dict) and "recordings" in cuerpo,
                  type(cuerpo).__name__)


def test_una_grabacion_solo_local_aparece_en_el_listado():
    """
    Las grabaciones que estan solo en el equipo deben verse, para
    poder decidir si se archivan. Si no aparecieran, el panel seguiria
    vacio aunque la forma fuese correcta.
    """

    preparar()

    pintadas = leer(h.cliente(h.OWNER).get("/api/recordings").json())

    estados = {g.get("storage_state") for g in pintadas}

    comprobar("Se ven las que estan solo en el equipo",
              "local_only" in estados, str(estados))

    comprobar("Y tambien las archivadas en el servidor",
              "server_stored" in estados, str(estados))

    locales = [g for g in pintadas if g.get("status") == "local"]

    comprobar("Las locales llevan su estado de almacenamiento",
              all(g.get("storage_state") for g in locales))

    comprobar("Y el nombre del equipo, para distinguirlas",
              all(g.get("hostname") for g in pintadas),
              str([g.get("hostname") for g in pintadas]))


def test_el_aislamiento_sigue_en_pie():
    """La correccion no debe abrir las grabaciones de otra empresa."""

    preparar()

    pintadas = leer(h.cliente(h.OWNER).get("/api/recordings").json())

    equipos = {g["device_id"] for g in pintadas}

    comprobar("Solo aparecen equipos de la empresa propia",
              equipos == {"equipo-propio"}, str(equipos))

    # Pedir el equipo de otra empresa devuelve vacio, no error
    respuesta = h.cliente(h.OWNER).get(
        "/api/recordings?device_id=equipo-ajeno"
    )

    comprobar("Pedir un equipo ajeno no da error",
              respuesta.status_code == 200)

    comprobar("Y no devuelve nada",
              len(leer(respuesta.json())) == 0)

    # El operador de plataforma si las ve todas
    todas = leer(h.cliente("operador").get("/api/recordings").json())

    comprobar("El operador de la plataforma ve las dos empresas",
              {g["device_id"] for g in todas}
              == {"equipo-propio", "equipo-ajeno"},
              str({g["device_id"] for g in todas}))


# ==============================
# BACKEND Y FRONTEND DE ACUERDO
# ==============================

def test_el_panel_lee_el_campo_que_el_backend_manda():
    """
    Lo que fallaba no era el backend ni el frontend por separado, sino
    que no coincidian. Se comprueba el acuerdo, no cada lado.
    """

    comprobar("El panel lee data.recordings",
              "data.recordings" in JS)

    servidor_py = io.open(RAIZ / "backend" / "main.py",
                          encoding="utf-8").read()

    bloque = servidor_py.split(
        '@app.get("/api/recordings")', 1
    )[1].split(chr(10) + "@app.", 1)[0]

    # Todos los return del endpoint deben llevar el campo
    retornos = [
        linea.strip() for linea in bloque.split(chr(10))
        if linea.strip().startswith("return ")
        and "error" not in linea
    ]

    comprobar("Ningun return del endpoint devuelve una lista desnuda",
              not [r for r in retornos if r.startswith("return [")],
              str(retornos))

    comprobar("El endpoint devuelve 'recordings' en sus ramas",
              bloque.count('"recordings"') >= 2,
              str(bloque.count('"recordings"')))


def test_el_panel_avisa_cuando_de_verdad_no_hay_nada():
    """
    Distinguir 'no hay grabaciones' de 'no se pudieron leer' importa:
    el fallo original se veia igual que la ausencia de datos.
    """

    preparar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM recordings")
    conexion.commit()
    conexion.close()

    cuerpo = h.cliente(h.OWNER).get("/api/recordings").json()

    comprobar("Sin grabaciones sigue siendo un objeto",
              isinstance(cuerpo, dict) and "recordings" in cuerpo)

    comprobar("Con la lista vacia", leer(cuerpo) == [])

    comprobar("El panel tiene un mensaje para ese caso",
              "No hay grabaciones" in JS)

    comprobar("Y otro distinto para un error de carga",
              "Error al cargar las grabaciones" in JS)


# ==============================
# NI PERDIDA NI DUPLICADOS
# ==============================

def test_archivar_dos_veces_no_duplica_el_registro():
    """
    Un reintento de subida no puede crear una segunda fila: el panel
    mostraria la misma grabacion dos veces y la retencion la contaria
    dos veces.
    """

    preparar()

    conexion = database.get_connection()

    indices = [
        r["name"] for r in conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = 'recordings'"
        )
    ]

    unico = [
        r["sql"] for r in conexion.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = 'recordings' AND sql LIKE '%UNIQUE%'"
        )
    ]

    comprobar("Hay un indice unico en recordings", bool(unico), str(indices))

    comprobar("Y es sobre (device_id, path)",
              any("device_id" in (s or "") and "path" in (s or "")
                  for s in unico), str(unico))

    # Se intenta insertar la misma ruta otra vez
    fallo = None

    try:
        conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, storage_state) "
            "VALUES ('equipo-propio', "
            "'equipo-propio/2026/10/09/rec_179150.mp4', "
            "'2026-10-09T10:00:00+00:00', '2026-10-09T10:15:00+00:00', "
            "900, 100, 'local', 'local_only')"
        )
        conexion.commit()

    except Exception as error:
        fallo = type(error).__name__

    conexion.close()

    comprobar("La base rechaza una ruta repetida del mismo equipo",
              fallo is not None, str(fallo))

    pintadas = leer(h.cliente(h.OWNER).get("/api/recordings").json())

    rutas = [g["path"] for g in pintadas]

    comprobar("No hay rutas repetidas en el listado",
              len(rutas) == len(set(rutas)), str(rutas))


def test_el_listado_no_expone_rutas_absolutas_ni_secretos():

    preparar()

    respuesta = h.cliente(h.OWNER).get("/api/recordings")

    texto = respuesta.text

    comprobar("Las rutas son relativas al almacenamiento",
              "C:\\\\" not in texto and "/opt/" not in texto,
              texto[:120])

    for secreto in ("agent_token", "token_hash", "password",
                    "AUTH_SECRET"):
        comprobar(f"No aparece {secreto}", secreto not in texto)


# ==============================
# LO DE SIEMPRE
# ==============================

def test_lo_de_siempre_sigue_en_pie():

    preparar()

    cliente = h.cliente(h.OWNER)

    for ruta in ("/api/devices", "/api/alerts", "/api/settings",
                 "/api/users/me", "/api/audit"):

        comprobar(f"{ruta} responde",
                  cliente.get(ruta).status_code == 200,
                  str(cliente.get(ruta).status_code))

    comprobar("La suspension sigue alcanzando a las grabaciones",
              _suspendida_bloquea())


def _suspendida_bloquea():

    orgs.update_organization(escenario["propia"],
                             subscription_status=orgs.STATUS_SUSPENDED)

    codigo = h.cliente(h.OWNER).get("/api/recordings").status_code

    orgs.update_organization(escenario["propia"],
                             subscription_status=orgs.STATUS_ACTIVE)

    return codigo == 403


# ==============================

def main():

    pruebas = [
        test_el_owner_de_una_empresa_ve_sus_grabaciones,
        test_la_forma_es_la_misma_para_los_dos_roles,
        test_la_forma_se_mantiene_en_todas_las_variantes,
        test_una_grabacion_solo_local_aparece_en_el_listado,
        test_el_aislamiento_sigue_en_pie,
        test_el_panel_lee_el_campo_que_el_backend_manda,
        test_el_panel_avisa_cuando_de_verdad_no_hay_nada,
        test_archivar_dos_veces_no_duplica_el_registro,
        test_el_listado_no_expone_rutas_absolutas_ni_secretos,
        test_lo_de_siempre_sigue_en_pie
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
