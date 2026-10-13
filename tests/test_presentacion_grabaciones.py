"""
Como se presentan las grabaciones en el panel.

    .venv\\Scripts\\python tests/test_presentacion_grabaciones.py

Tres defectos con una causa cada uno, y ninguno era del dato:

  1. "[object Object]" en la fecha. formatRecordingDate() devuelve un
     OBJETO {date, time} porque la tabla las pinta en celdas
     distintas. En otros dos sitios se interpolaba entero dentro de
     una plantilla, y JavaScript lo convierte a texto sin avisar.

  2. "0.0 GB" en todos los tamaños. formatBytes() dividia siempre
     entre 1024^3: una grabacion de 3 MB da 0,003 GB, que redondeado
     es 0,0.

  3. "0s" en la duracion. Un cero significa DESCONOCIDA en este
     producto, no "dura cero".

Las comprobaciones de formato EJECUTAN las funciones de app.js en
Node, no buscan texto en el archivo: una copia del codigo aqui se
desincronizaria y acabaria probando algo que ya no existe.

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

os.environ.setdefault("REMOTEADMIN_PUBLIC_URL", "https://panel.ejemplo.com")

# El andamiaje primero: apunta la base de datos a una temporal. Si un
# modulo de backend se carga antes, queda enlazado a la base REAL.
import users_harness as h  # noqa: E402

import backend.database as database  # noqa: E402
import backend.organizations as orgs  # noqa: E402
import backend.users as users  # noqa: E402


JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==============================
# EJECUTAR LA LOGICA REAL
# ==============================

def hay_node():
    try:
        return subprocess.run(
            ["node", "--version"], capture_output=True, timeout=20
        ).returncode == 0
    except Exception:
        return False


NODE = hay_node()


def extraer(nombre):
    """
    Una funcion de app.js, contando llaves.

    Los cuerpos de las funciones de nivel superior no van sangrados en
    este archivo, asi que hay cierres en la columna 0 DENTRO de la
    funcion: buscar la primera "}" sola la cortaria por la mitad.
    """

    inicio = JS.index(f"function {nombre}(")

    nivel = 0
    visto = False

    for i in range(inicio, len(JS)):

        if JS[i] == "{":
            nivel += 1
            visto = True

        elif JS[i] == "}":
            nivel -= 1

            if visto and nivel == 0:
                return JS[inicio:i + 1]

    raise AssertionError(f"no se encontro {nombre}")


def constante(nombre):

    inicio = JS.index(f"const {nombre}")

    return JS[inicio:JS.index("];", inicio) + 2]


def piezas():

    return "\n\n".join([
        constante("UNIDADES_DE_TAMANO"),
        extraer("formatBytes"),
        extraer("formatRecordingDate"),
        extraer("textoDeFecha"),
        extraer("duracionConocida"),
        extraer("formatDuration"),
    ])


def evaluar(expresiones):
    """Devuelve el resultado de cada expresion, en orden."""

    guion = (
        piezas()
        + "\n\nconst salida = [];\n"
        + "".join(f"salida.push(String({e}));\n" for e in expresiones)
        + "console.log(JSON.stringify(salida));\n"
    )

    carpeta = tempfile.mkdtemp(prefix="presentacion-")

    try:
        ruta = os.path.join(carpeta, "p.mjs")

        with io.open(ruta, "w", encoding="utf-8") as archivo:
            archivo.write(guion)

        salida = subprocess.run(
            ["node", ruta], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=40
        )

        if salida.returncode != 0:
            return None, salida.stderr.strip()[:200]

        return json.loads(salida.stdout.strip()), None

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


# ==============================
# 1. LA FECHA
# ==============================

def test_la_fecha_no_sale_como_objeto():

    if not NODE:
        comprobar("Node disponible", False, "sin node")
        return

    valores, error = evaluar([
        'textoDeFecha("2026-10-06T14:12:33+00:00")',
        'textoDeFecha(null)',
        'textoDeFecha("no es una fecha")',
    ])

    comprobar("La logica se ejecuta", error is None, str(error))

    if error:
        return

    completa, vacia, invalida = valores

    comprobar("Una fecha real se convierte en texto",
              "[object Object]" not in completa, completa)

    comprobar("Y lleva fecha y hora",
              len(completa) > 8 and any(c.isdigit() for c in completa),
              completa)

    comprobar("Sin fecha se dice con un guion",
              vacia == "-", vacia)

    comprobar("Una fecha ilegible se enseña tal cual, no como objeto",
              "[object Object]" not in invalida, invalida)


def test_formatRecordingDate_sigue_devolviendo_sus_partes():
    """
    La tabla las pinta en celdas distintas y las usa por separado. El
    arreglo no podia cambiarle el contrato.
    """

    if not NODE:
        return

    valores, error = evaluar([
        'JSON.stringify(Object.keys('
        'formatRecordingDate("2026-10-06T14:12:33+00:00")))',
    ])

    if error:
        comprobar("Se puede leer el contrato", False, str(error))
        return

    claves = json.loads(valores[0])

    comprobar("Sigue devolviendo date y time",
              sorted(claves) == ["date", "time"], str(claves))


def test_nadie_interpola_el_objeto_de_fecha():
    """
    El defecto original. Si vuelve a aparecer en una plantilla sin
    .date ni .time, vuelve "[object Object]".
    """

    import re

    malos = []

    for encaje in re.finditer(r'\$\{formatRecordingDate\([^}]*\}', JS):

        trozo = encaje.group(0)

        if ".date" not in trozo and ".time" not in trozo:
            malos.append(trozo[:60])

    comprobar("Ninguna plantilla pinta el objeto entero",
              not malos, str(malos))


# ==============================
# 2. EL TAMAÑO
# ==============================

def test_el_tamano_usa_la_unidad_que_toca():

    if not NODE:
        return

    valores, error = evaluar([
        "formatBytes(261)",
        "formatBytes(161903)",
        "formatBytes(3145776)",
        "formatBytes(12322167)",
        "formatBytes(2147483648)",
        "formatBytes(1099511627776)",
    ])

    if error:
        comprobar("Se puede medir el tamaño", False, str(error))
        return

    chico, kb, mb, total, gb, tb = valores

    comprobar("261 bytes se dicen en bytes", chico == "261 B", chico)
    comprobar("158 KB en kilobytes", kb.endswith("KB"), kb)
    comprobar("3 MB en megabytes", mb == "3.0 MB", mb)
    comprobar("2 GB en gigabytes", gb == "2.0 GB", gb)
    comprobar("1 TB en terabytes", tb.endswith("TB"), tb)

    # El caso que motivo todo: once grabaciones reales suman 11,8 MB
    comprobar("El espacio ocupado ya no sale como 0.0 GB",
              total == "11.8 MB", total)


def test_cero_bytes_no_es_lo_mismo_que_sin_dato():
    """
    Un archivo de cero bytes es un problema y hay que poder verlo. No
    saber el tamaño todavia es otra cosa.
    """

    if not NODE:
        return

    valores, error = evaluar([
        "formatBytes(0)",
        "formatBytes(null)",
        "formatBytes(undefined)",
        "formatBytes(-5)",
        'formatBytes("")',
    ])

    if error:
        comprobar("Se pueden distinguir", False, str(error))
        return

    cero, nulo, indefinido, negativo, vacio = valores

    comprobar("Cero bytes se dice que es cero", cero == "0 B", cero)

    for nombre, valor in (("null", nulo), ("undefined", indefinido),
                          ("vacio", vacio)):

        comprobar(f"Sin dato ({nombre}) se dice que no se sabe",
                  valor == "Sin información", valor)

    comprobar("Un negativo no se presenta como tamaño",
              negativo == "Sin información", negativo)


# ==============================
# 3. LA DURACION
# ==============================

def test_cero_segundos_es_desconocida():
    """
    El Agent declara 0 cuando construye la ficha escaneando el disco:
    el sistema de archivos solo sabe tamaño y fecha. Enseñar "0s"
    afirmaria algo falso.
    """

    if not NODE:
        return

    valores, error = evaluar([
        "formatDuration(0)",
        "formatDuration(null)",
        "formatDuration(undefined)",
        "formatDuration(4)",
        "formatDuration(138)",
        "formatDuration(3600)",
        "formatDuration(7325)",
    ])

    if error:
        comprobar("Se puede medir la duracion", False, str(error))
        return

    cero, nulo, indef, corta, media, hora, larga = valores

    for nombre, valor in (("0", cero), ("null", nulo),
                          ("undefined", indef)):

        comprobar(f"Duracion {nombre} se dice desconocida",
                  valor == "Desconocida", valor)

    comprobar("4 segundos se dicen en segundos", corta == "4s", corta)
    comprobar("138 segundos en minutos", media == "2m 18s", media)
    comprobar("Una hora en horas", hora == "1h 00m", hora)
    comprobar("Dos horas largas tambien", larga == "2h 02m", larga)


def test_ya_nadie_pinta_la_duracion_a_mano():
    """
    El patron del defecto: ${rec.duration_sec || 0}s. Afirma cero
    cuando no se sabe.
    """

    import re

    malos = re.findall(r'\$\{[^}]*duration_sec\s*\|\|\s*0\}s', JS)

    comprobar("Ninguna plantilla escribe la duracion a mano",
              not malos, str(malos))


# ==============================
# LO QUE ENVIA EL SERVIDOR
# ==============================

def _organizacion(nombre, plan=None):
    """La organizacion con ese nombre, creandola si hace falta."""

    for existente in orgs.list_organizations():
        if existente["name"] == nombre:
            return existente

    return orgs.create_organization(
        nombre,
        plan=plan or orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )


def preparar():
    """Una organizacion con grabaciones de tamaños y estados variados."""

    h.reiniciar()

    conexion = database.get_connection()
    for tabla in ("recordings", "devices"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    # reiniciar() no borra las organizaciones, y borrarlas se llevaria
    # por delante al owner (users.organization_id va en cascada). Asi
    # que se reutiliza la que ya haya.
    org = _organizacion("Empresa")

    users.set_organization(h.OWNER, org["id"])

    conexion = database.get_connection()

    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES ('eq-1', 'PC-CONTABILIDAD', ?)", (org["id"],)
    )

    # Tamaños y duraciones como los reales, incluida una sin duracion
    filas = [
        ("eq-1/a.mp4", 138, 3145776, "stored", "server_stored"),
        ("eq-1/b.mp4", 6, 161903, "stored", "server_stored"),
        ("eq-1/c.mp4", 0, 4000000, "local", "local_only"),
    ]

    for ruta, dur, tam, estado, almacen in filas:
        conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, storage_state, "
            "keep) VALUES ('eq-1', ?, '2026-10-09T10:00:00+00:00', "
            "'2026-10-09T10:02:18+00:00', ?, ?, ?, ?, 0)",
            (ruta, dur, tam, estado, almacen)
        )

    conexion.commit()
    conexion.close()

    return org["id"]


def test_el_servidor_manda_los_campos_que_el_panel_pinta():

    preparar()

    respuesta = h.cliente(h.OWNER).get("/api/recordings")

    comprobar("El listado responde", respuesta.status_code == 200,
              str(respuesta.status_code))

    grabaciones = respuesta.json().get("recordings", [])

    comprobar("Vienen las tres", len(grabaciones) == 3,
              str(len(grabaciones)))

    for campo in ("id", "device_id", "hostname", "started_at",
                  "ended_at", "duration_sec", "size_bytes",
                  "storage_state"):

        comprobar(f"Cada grabacion trae {campo}",
                  all(campo in g for g in grabaciones))

    # El hostname sale del JOIN con devices, no del panel
    comprobar("Y el nombre del equipo es el real",
              all(g["hostname"] == "PC-CONTABILIDAD"
                  for g in grabaciones),
              str([g.get("hostname") for g in grabaciones]))


def test_los_totales_cuadran_con_la_base():
    """
    El contador y el espacio ocupado se calculan en el panel sumando
    lo que manda el servidor. Tienen que cuadrar con la base.
    """

    preparar()

    grabaciones = h.cliente(h.OWNER).get(
        "/api/recordings"
    ).json()["recordings"]

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT COUNT(*), SUM(size_bytes) FROM recordings "
        "WHERE status IN ('stored', 'local')"
    ).fetchone()
    conexion.close()

    comprobar("El numero de grabaciones cuadra",
              len(grabaciones) == fila[0],
              f"{len(grabaciones)} frente a {fila[0]}")

    suma = sum(g.get("size_bytes") or 0 for g in grabaciones)

    comprobar("El espacio ocupado cuadra",
              suma == fila[1], f"{suma} frente a {fila[1]}")

    # Y ese total, presentado, ya no es "0.0 GB"
    if NODE:

        valores, error = evaluar([f"formatBytes({suma})"])

        if not error:
            comprobar("Presentado con su unidad",
                      valores[0] != "0.0 GB", valores[0])


def test_la_duracion_desconocida_llega_como_cero_y_se_respeta():
    """
    El servidor no inventa una duracion: manda el cero tal cual. Es el
    panel quien lo traduce a "Desconocida".
    """

    preparar()

    grabaciones = h.cliente(h.OWNER).get(
        "/api/recordings"
    ).json()["recordings"]

    sin_duracion = [g for g in grabaciones if not g["duration_sec"]]

    comprobar("Hay una sin duracion conocida",
              len(sin_duracion) == 1, str(len(sin_duracion)))

    comprobar("Y el servidor no se la inventa",
              sin_duracion and sin_duracion[0]["duration_sec"] == 0,
              str(sin_duracion[:1]))

    comprobar("Pero si manda su tamaño, que si conoce",
              sin_duracion and sin_duracion[0]["size_bytes"] == 4000000)


def test_el_aislamiento_sigue_en_pie():
    """El arreglo era de presentacion; no podia tocar quien ve que."""

    preparar()

    otra = _organizacion("Ajena")

    users.create_user("ajeno", "ClaveDeOtraEmpresa2026!",
                      permissions=list(users.PERMISSIONS),
                      organization_id=otra["id"])

    grabaciones = h.cliente("ajeno").get(
        "/api/recordings"
    ).json()["recordings"]

    comprobar("Otra organizacion no ve estas grabaciones",
              grabaciones == [], str(grabaciones))


# ==============================

def main():

    pruebas = [
        test_la_fecha_no_sale_como_objeto,
        test_formatRecordingDate_sigue_devolviendo_sus_partes,
        test_nadie_interpola_el_objeto_de_fecha,
        test_el_tamano_usa_la_unidad_que_toca,
        test_cero_bytes_no_es_lo_mismo_que_sin_dato,
        test_cero_segundos_es_desconocida,
        test_ya_nadie_pinta_la_duracion_a_mano,
        test_el_servidor_manda_los_campos_que_el_panel_pinta,
        test_los_totales_cuadran_con_la_base,
        test_la_duracion_desconocida_llega_como_cero_y_se_respeta,
        test_el_aislamiento_sigue_en_pie,
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
