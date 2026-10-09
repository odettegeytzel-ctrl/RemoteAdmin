"""
Las acciones de la tabla de Grabaciones.

    .venv\\Scripts\\python tests/test_panel_acciones.py

Dos fallos que compartian causa: la fila decidia que botones pintar
sin tener los datos para decidirlo.

El primero, el boton de guardar: dependia de `puede()`, que consulta
una sesion que solo se cargaba al abrir Configuracion. Quien entraba
directo a Grabaciones no lo veia nunca.

El segundo, al reves: "Ver grabacion" y "Descargar" se ofrecian
siempre, tambien en grabaciones que solo estan en el equipo. Esas dos
acciones leen del SERVIDOR, asi que no podian funcionar.

Las comprobaciones de interfaz ejecutan la logica de app.js en un
interprete de JavaScript, no buscan texto: un boton que aparece es
distinto de una cadena presente en el archivo.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.database as database
import backend.organizations as orgs
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

CLAVE = "ClaveDelPanel2026!"


# ==============================
# EJECUTAR LA LOGICA REAL DE app.js
# ==============================

def hay_node():
    try:
        return subprocess.run(
            ["node", "--version"], capture_output=True, timeout=20
        ).returncode == 0

    except Exception:
        return False


NODE = hay_node()


def _extraer(nombre, tipo="function"):
    """
    Saca una funcion o constante de app.js tal cual esta.

    Se ejecuta el codigo de verdad en vez de copiarlo aqui: una copia
    se desincroniza del original y acaba probando algo que ya no
    existe.
    """

    lineas = JS.split("\n")

    if tipo == "function":
        arranque = (f"function {nombre}(", f"async function {nombre}(")
    else:
        arranque = (f"const {nombre} = ",)

    inicio = None

    for indice, texto in enumerate(lineas):
        if texto.startswith(arranque):
            inicio = indice
            break

    if inicio is None:
        return None

    cierre = "}" if tipo == "function" else "};"

    for fin in range(inicio + 1, len(lineas)):
        if lineas[fin] == cierre:
            return "\n".join(lineas[inicio:fin + 1])

    return None


def ejecutar_js(preparacion, expresion):
    """Evalua una expresion con las piezas reales de app.js cargadas."""

    piezas = [
        _extraer("ESTADOS_DE_ALMACENAMIENTO", "const"),
        _extraer("estadoDeAlmacenamiento"),
        _extraer("insigniaDeAlmacenamiento"),
        _extraer("puede"),
        _extraer("botonGuardarEnServidor"),
        _extraer("accionesDeGrabacion"),
    ]

    faltan = [p for p in piezas if p is None]

    if faltan:
        return None, "no se pudieron extraer todas las piezas de app.js"

    guion = (
        "\n\n".join(piezas)
        + "\n\n" + preparacion
        + "\n\nconsole.log(JSON.stringify(" + expresion + "));\n"
    )

    carpeta = tempfile.mkdtemp(prefix="panel-")

    try:
        ruta = os.path.join(carpeta, "prueba.mjs")

        with io.open(ruta, "w", encoding="utf-8") as archivo:
            archivo.write(guion)

        salida = subprocess.run(
            ["node", ruta], capture_output=True, text=True, timeout=40,
            encoding="utf-8", errors="replace"
        )

        if salida.returncode != 0:
            return None, salida.stderr.strip()[:200]

        return json.loads(salida.stdout.strip()), None

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def acciones(storage_state, es_owner=True, permisos=None):
    """HTML de las acciones para una grabacion en ese estado."""

    sesion = json.dumps({
        "is_owner": es_owner,
        "permissions": permisos if permisos is not None else []
    })

    preparacion = (
        f"let sesionActual = {sesion};\n"
        f"const rec = {{ id: 7, storage_state: "
        f"{json.dumps(storage_state)} }};"
    )

    return ejecutar_js(preparacion, "accionesDeGrabacion(rec)")


# ==============================
# EL BOTON DE GUARDAR
# ==============================

def test_el_boton_aparece_para_quien_puede_y_en_estado_local():

    if not NODE:
        comprobar("Node disponible para ejecutar la logica", False,
                  "sin node: estas comprobaciones no se ejecutaron")
        return

    html, error = acciones("local_only", es_owner=True)

    comprobar("La logica se ejecuta", error is None, str(error))

    if error:
        return

    comprobar("El Owner ve el boton de guardar",
              "Guardar en servidor" in html, html[:120])

    # Un subadmin con el permiso explicito
    html, _ = acciones("local_only", es_owner=False,
                       permisos=["recordings.manage"])

    comprobar("Un subadmin con el permiso tambien lo ve",
              "Guardar en servidor" in html)


def test_sin_permiso_no_aparece_el_boton():

    if not NODE:
        return

    html, _ = acciones("local_only", es_owner=False, permisos=[])

    comprobar("Sin permiso no se ofrece guardar",
              "Guardar en servidor" not in html, html[:120])

    html, _ = acciones("local_only", es_owner=False,
                       permisos=["recordings.view"])

    comprobar("Ver grabaciones no basta para guardarlas",
              "Guardar en servidor" not in html)


def test_una_sesion_sin_cargar_no_concede_permisos():
    """
    El estado de partida de `sesionActual`. Si de aqui saliera el
    boton, el panel estaria concediendo permisos que no sabe que
    existen.
    """

    if not NODE:
        return

    html, _ = acciones("local_only", es_owner=False, permisos=[])

    comprobar("La sesion vacia no concede nada",
              "Guardar en servidor" not in html)

    comprobar("Y el valor inicial de sesionActual no tiene permisos",
              "let sesionActual = { permissions: [], is_owner: false }"
              in JS)


def test_no_se_ofrece_guardar_lo_ya_guardado():

    if not NODE:
        return

    html, _ = acciones("server_stored")

    comprobar("Una grabacion ya archivada no se vuelve a ofrecer",
              "Guardar en servidor" not in html, html[:140])

    html, _ = acciones("server_pending")

    comprobar("Ni una que se esta transfiriendo",
              "Guardar en servidor" not in html)

    html, _ = acciones("server_error")

    comprobar("Pero tras un error si se puede reintentar",
              "Guardar en servidor" in html)


# ==============================
# VER Y DESCARGAR
# ==============================

def test_ver_y_descargar_solo_si_el_archivo_esta_en_el_servidor():
    """Las dos acciones leen del servidor, no del equipo."""

    if not NODE:
        return

    html, _ = acciones("server_stored")

    comprobar("Archivada: se puede ver",
              "Ver grabaci" in html, html[:140])

    comprobar("Archivada: se puede descargar",
              "/download" in html)

    for estado in ("local_only", "server_pending", "server_error"):

        html, _ = acciones(estado)

        comprobar(f"{estado}: no se ofrece ver",
                  "playRecording" not in html, html[:120])

        comprobar(f"{estado}: no se ofrece descargar",
                  "/download" not in html)


def test_se_explica_por_que_no_se_puede_abrir():
    """Un boton que siempre falla confunde mas que su ausencia."""

    if not NODE:
        return

    html, _ = acciones("local_only")

    comprobar("Se dice que esta solo en el equipo",
              "solo esta en el equipo" in html, html[:160])

    comprobar("Y que hay que guardarla para verla",
              "Guardala en el servidor" in html)

    html, _ = acciones("server_pending")

    comprobar("Mientras se transfiere, se dice",
              "transfiriendo" in html)

    html, _ = acciones("server_error")

    comprobar("Tras un error se dice que la copia local sigue",
              "copia del equipo sigue intacta" in html)


# ==============================
# LA CARGA DE SESION
# ==============================

def test_la_sesion_se_carga_antes_de_pintar_las_acciones():

    bloque = JS.split("async function loadRecordings", 1)[1].split(
        "\n/* =", 1
    )[0]

    comprobar("loadRecordings espera a la sesion",
              "await asegurarSesion()" in bloque)

    comprobar("Y lo hace antes de pintar la tabla",
              bloque.index("await asegurarSesion()")
              < bloque.index("accionesDeGrabacion"))


def test_la_sesion_se_pide_una_sola_vez():

    comprobar("Hay una peticion compartida", "asegurarSesion" in JS)

    bloque = JS.split("async function asegurarSesion", 1)[1].split(
        "\n\nasync function", 1
    )[0]

    comprobar("Se guarda la promesa, no un booleano",
              "sesionPedida = cargarSesionActual()" in bloque)

    comprobar("Y se reutiliza si ya esta en vuelo",
              "if (!sesionPedida)" in bloque)


def test_un_fallo_de_sesion_se_puede_reintentar():
    """
    Si una promesa rechazada se quedara cacheada, la sesion estaria
    vacia el resto de la visita y el boton no volveria nunca.
    """

    bloque = JS.split("async function asegurarSesion", 1)[1].split(
        "\n\nasync function", 1
    )[0]

    comprobar("Un fallo descarta la peticion cacheada",
              bloque.count("sesionPedida = null") >= 2, bloque)

    comprobar("Se capturan los errores",
              ".catch(" in bloque)

    comprobar("Y una carga que no trajo datos tambien se descarta",
              "if (!cargada)" in bloque)

    # cargarSesionActual informa de si cargo
    cargar = JS.split("async function cargarSesionActual", 1)[1].split(
        "\n}", 1
    )[0]

    comprobar("cargarSesionActual dice si funciono",
              "return true" in cargar and "return false" in cargar)

    comprobar("Y no concede permisos cuando falla",
              "no se conceden permisos por defecto" in cargar.lower()
              or "sin ninguno" in cargar.lower())


# ==============================
# EL ESTADO NO SE ADELANTA
# ==============================

def test_el_estado_solo_cambia_cuando_el_servidor_confirma():

    bloque = JS.split("async function guardarEnServidor", 1)[1].split(
        "\n}", 1
    )[0]

    comprobar("Se espera la respuesta del servidor",
              "await fetch" in bloque)

    # No se escribe server_stored a mano antes de la respuesta
    antes = bloque.split("await fetch", 1)[0]

    comprobar("No se marca como guardada antes de pedirlo",
              "server_stored" not in antes, antes[-160:])

    comprobar("Se vuelve a leer el listado al terminar",
              "loadRecordings()" in bloque)


def test_no_se_puede_pulsar_dos_veces():

    comprobar("Hay un registro de guardados en curso",
              "guardadosEnCurso" in JS)

    bloque = JS.split("async function guardarEnServidor", 1)[1].split(
        "\n}", 1
    )[0]

    comprobar("Un segundo clic se descarta",
              "guardadosEnCurso.has(recordingId)" in bloque)

    comprobar("Se anota antes de llamar al servidor",
              bloque.index("guardadosEnCurso.add")
              < bloque.index("await fetch"))

    comprobar("Y se suelta al terminar, pase lo que pase",
              "finally" in bloque
              and "guardadosEnCurso.delete" in bloque)


# ==============================
# EL BACKEND NO CAMBIA
# ==============================

def preparar():

    h.reiniciar()

    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "organizations",
                  "enrollment_tokens"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    org = orgs.create_organization(
        "Empresa", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, org["id"])

    sin_manage = [p for p in users.PERMISSIONS if p != "recordings.manage"]

    users.create_user("sub_sin_manage", CLAVE, permissions=sin_manage,
                      organization_id=org["id"])

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq', 'PC', ?)", (org["id"],)
    )
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, ended_at, "
        "duration_sec, size_bytes, status, storage_state, keep) VALUES "
        "('eq','eq/a.mp4','2026-10-09T10:00:00+00:00',"
        "'2026-10-09T10:15:00+00:00',900,4000000,'local','local_only',0)"
    )
    conexion.commit()
    conexion.close()

    return org["id"]


def test_el_backend_sigue_exigiendo_el_permiso():
    """
    El boton es comodidad visual. Quien quite la condicion en el
    navegador sigue sin poder archivar.
    """

    preparar()

    conexion = database.get_connection()
    identificador = list(conexion.execute(
        "SELECT id FROM recordings LIMIT 1"
    ))[0][0]
    conexion.close()

    respuesta = h.cliente("sub_sin_manage").post(
        f"/api/recordings/{identificador}/store"
    )

    comprobar("Sin recordings.manage el servidor rechaza",
              respuesta.status_code == 403, str(respuesta.status_code))

    # Y el estado no cambio
    conexion = database.get_connection()
    estado = list(conexion.execute(
        "SELECT storage_state FROM recordings WHERE id = ?",
        (identificador,)
    ))[0][0]
    conexion.close()

    comprobar("Y la grabacion sigue como estaba",
              estado == "local_only", str(estado))


def test_el_listado_sigue_igual():

    preparar()

    respuesta = h.cliente(h.OWNER).get("/api/recordings")

    cuerpo = respuesta.json()

    comprobar("El listado responde", respuesta.status_code == 200)

    comprobar("Con la forma que el panel lee",
              isinstance(cuerpo, dict) and "recordings" in cuerpo)

    comprobar("Y trae el estado de almacenamiento",
              all("storage_state" in g for g in cuerpo["recordings"]))


# ==============================

def main():

    pruebas = [
        test_el_boton_aparece_para_quien_puede_y_en_estado_local,
        test_sin_permiso_no_aparece_el_boton,
        test_una_sesion_sin_cargar_no_concede_permisos,
        test_no_se_ofrece_guardar_lo_ya_guardado,
        test_ver_y_descargar_solo_si_el_archivo_esta_en_el_servidor,
        test_se_explica_por_que_no_se_puede_abrir,
        test_la_sesion_se_carga_antes_de_pintar_las_acciones,
        test_la_sesion_se_pide_una_sola_vez,
        test_un_fallo_de_sesion_se_puede_reintentar,
        test_el_estado_solo_cambia_cuando_el_servidor_confirma,
        test_no_se_puede_pulsar_dos_veces,
        test_el_backend_sigue_exigiendo_el_permiso,
        test_el_listado_sigue_igual
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
