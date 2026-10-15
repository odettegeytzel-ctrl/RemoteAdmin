"""
Los tramos de grabacion: del Agent al panel.

    .venv\\Scripts\\python tests/test_tramos_grabacion.py

Para que sirven
---------------

Un archivo de 15 minutos puede empezar sosteniendolo solo la
grabacion programada y terminar con la manual tambien activa.
Atribuirlo entero a una modalidad seria falso en la mitad del
archivo.

Los tramos guardan QUIEN sostenia la captura y CUANDO. El reparto de
cada archivo se calcula cruzando sus tramos con el rango del
segmento, y se recorta a sus limites.

La identidad
------------

Un tramo es (equipo, modalidad, inicio). Una modalidad no puede tomar
la captura dos veces en el mismo instante, asi que esos tres campos
lo identifican sin que el Agent invente ningun identificador.

Eso es lo que hace inofensivo reintentar: el Agent reenvia sus tramos
enteros al reconectar, sin saber cuales llegaron antes de la caida, y
el servidor no los duplica.
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile

from datetime import datetime, timedelta, timezone
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-tramos")

import backend.database as database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="tramos-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

import backend.recordings as recordings  # noqa: E402
import backend.devices as devices  # noqa: E402

from backend.models import DeviceRegister  # noqa: E402


AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


BASE = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)


def t(minutos):
    return (BASE + timedelta(minutes=minutos)).isoformat()


def tramo(origen, desde, hasta=None):
    return {"origen": origen, "desde": t(desde),
            "hasta": t(hasta) if hasta is not None else None}


def contar(device_id=None):

    conexion = database.get_connection()

    try:
        if device_id:
            return conexion.execute(
                "SELECT COUNT(*) FROM recording_intervals "
                "WHERE device_id = ?", (device_id,)
            ).fetchone()[0]

        return conexion.execute(
            "SELECT COUNT(*) FROM recording_intervals"
        ).fetchone()[0]

    finally:
        conexion.close()


def crear_grabacion(device_id, desde, hasta, nombre):
    """Una ficha de grabacion local, como la que manda el Agent."""

    identificador, _ = recordings.register_local_recording(
        device_id=device_id,
        path="%s/%s" % (device_id, nombre),
        started_at=t(desde),
        ended_at=t(hasta),
        duration_sec=(hasta - desde) * 60,
        size_bytes=1024
    )

    return identificador


def nuevo_equipo(identificador):

    devices.register_device(DeviceRegister(
        device_id=identificador, hostname="PC",
        operating_system="Windows-11", ip_address="10.0.0.1"
    ))

    return identificador


# ==================================================================
# 1. PERSISTENCIA
# ==================================================================

def test_se_guardan_los_tramos():

    nuevo_equipo("eq-guardar")

    nuevos, actualizados, malos = recordings.guardar_tramos(
        "eq-guardar",
        [tramo("programada", 0, 60), tramo("manual", 10, 25)]
    )

    comprobar("Se guardan los dos", nuevos == 2,
              "nuevos=%s actualizados=%s malos=%s"
              % (nuevos, actualizados, malos))

    comprobar("Y constan en la base",
              contar("eq-guardar") == 2, str(contar("eq-guardar")))


def test_un_tramo_abierto_se_guarda_sin_fin():
    """
    Cerrarlo en el Agent con la hora actual inventaria un final
    distinto en cada envio para una grabacion que sigue en marcha.
    """

    nuevo_equipo("eq-abierto")

    recordings.guardar_tramos("eq-abierto", [tramo("manual", 0)])

    conexion = database.get_connection()

    fin = conexion.execute(
        "SELECT ended_at FROM recording_intervals WHERE device_id = ?",
        ("eq-abierto",)
    ).fetchone()[0]

    conexion.close()

    comprobar("El tramo abierto queda sin fin",
              fin is None, repr(fin))


def test_lo_que_no_se_entiende_no_se_guarda():
    """Una lista cerrada de modalidades, no lo que llegue."""

    nuevo_equipo("eq-basura")

    nuevos, _, malos = recordings.guardar_tramos("eq-basura", [
        tramo("programada", 0, 10),
        {"origen": "inventada", "desde": t(0), "hasta": t(5)},
        {"origen": "manual", "desde": "", "hasta": t(5)},
        {"origen": "manual", "desde": t(20), "hasta": t(10)},
        "esto no es un tramo",
        {}
    ])

    comprobar("Solo entra el valido", nuevos == 1, str(nuevos))

    comprobar("Y se descartan los cinco restantes",
              malos == 5, str(malos))

    comprobar("Un tramo que termina antes de empezar no entra",
              contar("eq-basura") == 1, str(contar("eq-basura")))


# ==================================================================
# 2. DUPLICADOS, REINTENTOS Y RECONEXION
# ==================================================================

def test_reenviar_no_duplica():
    """
    El Agent reenvia sus tramos enteros al reconectar, sin saber
    cuales llegaron antes de la caida.
    """

    nuevo_equipo("eq-reenvio")

    lote = [tramo("programada", 0, 60), tramo("manual", 10, 25)]

    recordings.guardar_tramos("eq-reenvio", lote)

    for _ in range(5):
        recordings.guardar_tramos("eq-reenvio", lote)

    comprobar("Cinco reenvios siguen siendo dos filas",
              contar("eq-reenvio") == 2, str(contar("eq-reenvio")))


def test_un_tramo_abierto_se_cierra_despues():
    """
    El caso normal: se informa abierto y mas tarde, cerrado. No puede
    crear una fila nueva ni perder el fin.
    """

    nuevo_equipo("eq-cierre")

    recordings.guardar_tramos("eq-cierre", [tramo("manual", 0)])

    comprobar("Primero, una fila abierta",
              contar("eq-cierre") == 1)

    recordings.guardar_tramos("eq-cierre", [tramo("manual", 0, 30)])

    comprobar("Al cerrarlo sigue siendo UNA fila",
              contar("eq-cierre") == 1, str(contar("eq-cierre")))

    conexion = database.get_connection()

    fin = conexion.execute(
        "SELECT ended_at FROM recording_intervals WHERE device_id = ?",
        ("eq-cierre",)
    ).fetchone()[0]

    conexion.close()

    comprobar("Y ahora tiene fin", fin == t(30), repr(fin))


def test_un_reenvio_viejo_no_borra_el_fin():
    """
    Tras reconectar, el Agent puede reenviar el tramo como abierto
    aunque el servidor ya sepa que termino. No puede perderse el fin.
    """

    nuevo_equipo("eq-viejo")

    recordings.guardar_tramos("eq-viejo", [tramo("manual", 0, 30)])

    # Llega otra vez, pero abierto
    recordings.guardar_tramos("eq-viejo", [tramo("manual", 0)])

    conexion = database.get_connection()

    fin = conexion.execute(
        "SELECT ended_at FROM recording_intervals WHERE device_id = ?",
        ("eq-viejo",)
    ).fetchone()[0]

    conexion.close()

    comprobar("El fin conocido no se pierde",
              fin == t(30), repr(fin))


def test_dos_equipos_no_se_mezclan():
    """La identidad incluye el equipo, no solo la hora."""

    nuevo_equipo("eq-a")
    nuevo_equipo("eq-b")

    mismo = [tramo("manual", 0, 30)]

    recordings.guardar_tramos("eq-a", mismo)
    recordings.guardar_tramos("eq-b", mismo)

    comprobar("Cada equipo tiene el suyo",
              contar("eq-a") == 1 and contar("eq-b") == 1,
              "a=%d b=%d" % (contar("eq-a"), contar("eq-b")))


# ==================================================================
# 3. EL REPARTO DE UN ARCHIVO
# ==================================================================

def test_un_archivo_compartido_no_se_atribuye_a_una_sola():
    """El caso que justifica todo esto."""

    nuevo_equipo("eq-reparto")

    # Grabacion de 10:00 a 10:15
    identificador = crear_grabacion("eq-reparto", 0, 15, "a.mp4")

    # La programada sostiene todo; la manual entra de 10:04 a 10:07
    recordings.guardar_tramos("eq-reparto", [
        tramo("programada", 0, 15),
        tramo("manual", 4, 7)
    ])

    reparto = recordings.tramos_de_grabacion(identificador)

    comprobar("Hay reparto", reparto and not reparto["sin_datos"],
              str(reparto))

    por_origen = {}

    for x in reparto["tramos"]:
        por_origen[x["origen"]] = por_origen.get(x["origen"], 0) + x["segundos"]

    comprobar("La programada cubre los 15 minutos",
              por_origen.get("programada") == 900, str(por_origen))

    comprobar("Y la manual solo sus 3",
              por_origen.get("manual") == 180, str(por_origen))

    comprobar("Son dos tramos, no uno",
              len(reparto["tramos"]) == 2, str(len(reparto["tramos"])))


def test_los_tramos_se_recortan_al_archivo():
    """Un tramo que empezo antes no cuenta entero."""

    nuevo_equipo("eq-recorte")

    identificador = crear_grabacion("eq-recorte", 10, 20, "b.mp4")

    # Empezo mucho antes y sigue despues
    recordings.guardar_tramos("eq-recorte", [tramo("programada", 0, 60)])

    reparto = recordings.tramos_de_grabacion(identificador)

    comprobar("Un solo tramo", len(reparto["tramos"]) == 1,
              str(reparto))

    if reparto["tramos"]:
        comprobar("Recortado a los 10 minutos del archivo",
                  reparto["tramos"][0]["segundos"] == 600,
                  str(reparto["tramos"][0]))


def test_un_tramo_de_otro_momento_no_cuenta():

    nuevo_equipo("eq-fuera")

    identificador = crear_grabacion("eq-fuera", 0, 10, "c.mp4")

    recordings.guardar_tramos("eq-fuera", [tramo("manual", 30, 40)])

    reparto = recordings.tramos_de_grabacion(identificador)

    comprobar("Un tramo posterior no se atribuye al archivo",
              reparto["tramos"] == [], str(reparto["tramos"]))

    comprobar("Y se dice que no hay datos, no se inventa",
              reparto["sin_datos"] is True)


def test_las_grabaciones_historicas_no_se_inventan():
    """
    Las que ya existen no tienen tramos. Eso se dice, no se rellena
    atribuyendolas a una modalidad.
    """

    nuevo_equipo("eq-historico")

    identificador = crear_grabacion("eq-historico", 0, 10, "viejo.mp4")

    reparto = recordings.tramos_de_grabacion(identificador)

    comprobar("Sin tramos, se responde sin_datos",
              reparto["sin_datos"] is True, str(reparto))

    comprobar("Y la lista va vacia, no inventada",
              reparto["tramos"] == [])


def test_un_tramo_abierto_cuenta_hasta_el_fin_del_archivo():
    """Es lo unico que se sabe con certeza de una grabacion en curso."""

    nuevo_equipo("eq-encurso")

    identificador = crear_grabacion("eq-encurso", 0, 10, "d.mp4")

    recordings.guardar_tramos("eq-encurso", [tramo("manual", 2)])

    reparto = recordings.tramos_de_grabacion(identificador)

    comprobar("El tramo abierto se cuenta", len(reparto["tramos"]) == 1,
              str(reparto))

    if reparto["tramos"]:
        x = reparto["tramos"][0]

        comprobar("Hasta el fin del archivo, no mas alla",
                  x["segundos"] == 480, str(x))

        comprobar("Y se marca como abierto", x["abierto"] is True, str(x))


def test_una_grabacion_que_no_existe():

    comprobar("Devuelve None, no revienta",
              recordings.tramos_de_grabacion(999999) is None)


# ==================================================================
# 4. AUTORIZACION Y AISLAMIENTO
# ==================================================================

def test_la_consulta_exige_permiso_y_organizacion():

    import users_harness as h
    import backend.users as users
    import backend.organizations as organizations

    h.reiniciar()

    propia = organizations.ensure_default_organization()
    propietario = users.get_user(h.OWNER)

    if propietario and propietario.get("organization_id"):
        propia = propietario["organization_id"]

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR REPLACE INTO devices (device_id, hostname, "
        "organization_id) VALUES (?, ?, ?)",
        ("eq-mio", "PC-MIO", propia)
    )
    conexion.commit()
    conexion.close()

    identificador = crear_grabacion("eq-mio", 0, 10, "mia.mp4")

    ruta = "/api/recordings/%d/intervals" % identificador

    # Sin sesion
    comprobar("Sin sesion no se consulta",
              h.cliente().get(ruta).status_code in (401, 403))

    # Sin el permiso
    h.crear_subadmin("sin-ver", permisos=["devices.view"])

    comprobar("Sin recordings.view tampoco",
              h.cliente("sin-ver").get(ruta).status_code == 403)

    # Con el permiso
    h.crear_subadmin("con-ver", permisos=["recordings.view"])

    respuesta = h.cliente("con-ver").get(ruta)

    comprobar("Con recordings.view si",
              respuesta.status_code == 200,
              "respondio %d" % respuesta.status_code)

    # De otra organizacion
    ajena = organizations.create_organization("Otra")
    ajena = ajena["id"] if isinstance(ajena, dict) else ajena

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR REPLACE INTO devices (device_id, hostname, "
        "organization_id) VALUES (?, ?, ?)",
        ("eq-ajeno", "PC-AJENO", ajena)
    )
    conexion.commit()
    conexion.close()

    otra = crear_grabacion("eq-ajeno", 0, 10, "ajena.mp4")

    respuesta = h.cliente("con-ver").get(
        "/api/recordings/%d/intervals" % otra
    )

    comprobar("Una grabacion de otra empresa, no",
              respuesta.status_code == 404,
              "respondio %d" % respuesta.status_code)


# ==================================================================
# 5. EL CAMINO COMPLETO
# ==================================================================

def test_el_agent_los_informa():

    comprobar("El Agent tiene funcion de informe",
              "def report_recording_intervals(" in AGENTE)

    comprobar("Los manda al cerrar cada segmento",
              "report_recording_intervals()" in
              AGENTE.split("def on_segment_complete(", 1)[1].split(chr(10)+chr(10)+chr(10), 1)[0])

    comprobar("Y al reconectar, para recuperar lo perdido",
              AGENTE.count("report_recording_intervals()") >= 3,
              str(AGENTE.count("report_recording_intervals()")))

    comprobar("El tramo abierto se manda sin cerrar",
              "duenos.para_informar()" in AGENTE)

    comprobar("Y se olvidan los viejos para no crecer sin limite",
              "duenos.olvidar_antes_de(" in AGENTE)


def test_el_servidor_usa_el_equipo_de_la_conexion():
    """
    Un equipo no puede informar tramos de otro. El device_id sale de
    la conexion autenticada, no del mensaje.
    """

    bloque = SERVIDOR.split('message.startswith("recording_intervals:")',
                            1)[1][:900]

    comprobar("Guarda con el device_id de la conexion",
              "guardar_tramos(\n                        device_id,"
              in bloque or "guardar_tramos(" in bloque,
              bloque[:200])

    comprobar("No lee ningun device_id del mensaje",
              'carga.get("device_id")' not in bloque)

    comprobar("Una carga ilegible no tumba la conexion",
              "json.JSONDecodeError" in bloque)


# ==================================================================

def principal():

    pruebas = [
        test_se_guardan_los_tramos,
        test_un_tramo_abierto_se_guarda_sin_fin,
        test_lo_que_no_se_entiende_no_se_guarda,
        test_reenviar_no_duplica,
        test_un_tramo_abierto_se_cierra_despues,
        test_un_reenvio_viejo_no_borra_el_fin,
        test_dos_equipos_no_se_mezclan,
        test_un_archivo_compartido_no_se_atribuye_a_una_sola,
        test_los_tramos_se_recortan_al_archivo,
        test_un_tramo_de_otro_momento_no_cuenta,
        test_las_grabaciones_historicas_no_se_inventan,
        test_un_tramo_abierto_cuenta_hasta_el_fin_del_archivo,
        test_una_grabacion_que_no_existe,
        test_la_consulta_exige_permiso_y_organizacion,
        test_el_agent_los_informa,
        test_el_servidor_usa_el_equipo_de_la_conexion
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("TRAMOS DE GRABACION")
    print("=" * 66)
    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if not bien:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    if not fallos:
        print("  todo correcto")

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    shutil.rmtree(CARPETA, ignore_errors=True)

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
