"""
La informacion del equipo: por que decia "Sin informacion".

    .venv\\Scripts\\python tests/test_info_equipo.py

Lo que se encontro
------------------

El panel mostraba bien el nombre del equipo, el sistema operativo y
la IP, y en blanco todo lo demas. No era casualidad: esos tres son
EXACTAMENTE los que escriben el alta y el latido.

    register_device   -> hostname, operating_system, ip_address
    update_heartbeat  -> ip_address, status, last_seen
    update_system_info -> todo el hardware

Y la tercera solo se ejecutaba al recibir 'get_system_info', que
dispara el boton "Actualizar informacion" del panel. Un equipo donde
nunca se pulsaba se quedaba sin procesador, sin memoria, sin
fabricante y sin modelo para siempre.

El Agent SI recopilaba todo. Nadie se lo pedia.

Dos fallos mas, encontrados al probar
-------------------------------------

1. El UPDATE estaba DENTRO de 'if disks:'. Una ficha sin discos no
   guardaba nada y despues reventaba leyendo cursor.rowcount, que no
   existia. Una consulta de discos que falle no puede impedir que se
   guarde el procesador.

2. El UPDATE escribia todos los campos siempre. Una ficha parcial
   —un Agent antiguo, un WMI que fallo— borraba datos buenos y el
   panel empezaba a decir "Sin informacion" sobre algo que si se
   sabia.
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-info")

import backend.database as database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="info-equipo-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

import backend.devices as devices  # noqa: E402

from backend.models import DeviceRegister  # noqa: E402


AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# Todos los campos que el panel enseña
CAMPOS = (
    "hostname", "username", "operating_system", "ip_address",
    "processor", "cpu_count", "ram_total", "windows_version",
    "architecture", "manufacturer", "model", "storage_total"
)


FICHA_COMPLETA = {
    "hostname": "PC-CONTABILIDAD",
    "username": "ana",
    "operating_system": "Windows-11-10.0.26200-SP0",
    "ip_address": "10.0.0.1",
    "processor": "Intel64 Family 6 Model 142",
    "cpu_count": 8,
    "ram_total": 16885047296,
    "ram_available": 8000000000,
    "ram_used": 8885047296,
    "ram_percent": 52.6,
    "windows_version": "10.0.26200",
    "architecture": "AMD64",
    "manufacturer": "LENOVO",
    "model": "20S20004US",
    "disks": [
        {"mountpoint": "C:" + chr(92), "total": 510994149376,
         "free": 200000000000, "used": 310994149376}
    ]
}


def nuevo_equipo(identificador):

    devices.register_device(DeviceRegister(
        device_id=identificador,
        hostname="PC",
        operating_system="Windows-11",
        ip_address="10.0.0.1"
    ))

    return identificador


def fila(identificador):

    conexion = database.get_connection()
    conexion.row_factory = sqlite3.Row

    try:
        encontrada = conexion.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (identificador,)
        ).fetchone()

        return dict(encontrada) if encontrada else None

    finally:
        conexion.close()


# ==================================================================
# 1. LA CAUSA: solo tres campos llegaban
# ==================================================================

def test_el_alta_solo_escribe_tres_campos():
    """Reproduce el estado en que se quedaba un equipo sin fichar."""

    nuevo_equipo("eq-solo-alta")

    datos = fila("eq-solo-alta")

    for campo in ("hostname", "operating_system", "ip_address"):
        comprobar("El alta escribe %s" % campo,
                  datos.get(campo) is not None)

    vacios = [c for c in CAMPOS if datos.get(c) is None]

    comprobar("Y deja el resto en blanco",
              set(vacios) == {"username", "processor", "cpu_count",
                              "ram_total", "windows_version",
                              "architecture", "manufacturer", "model",
                              "storage_total"},
              str(sorted(vacios)))


def test_con_la_ficha_se_llena_todo():

    nuevo_equipo("eq-con-ficha")

    devices.update_system_info("eq-con-ficha", FICHA_COMPLETA)

    datos = fila("eq-con-ficha")

    vacios = [c for c in CAMPOS if datos.get(c) is None]

    comprobar("No queda ningun campo sin dato",
              not vacios, "siguen vacios: %s" % sorted(vacios))

    comprobar("El modelo es el real",
              datos["model"] == "20S20004US", repr(datos["model"]))

    comprobar("Y el almacenamiento sale del disco del sistema",
              datos["storage_total"] == 510994149376,
              repr(datos["storage_total"]))


# ==================================================================
# 2. UNA FICHA PARCIAL NO BORRA LO QUE YA SE SABIA
# ==================================================================

def test_una_ficha_parcial_no_borra_datos_buenos():
    """
    Un Agent antiguo manda menos campos. Un WMI puede fallar y dejar
    fuera el fabricante. Eso no puede vaciar lo que ya constaba.
    """

    nuevo_equipo("eq-parcial")

    devices.update_system_info("eq-parcial", FICHA_COMPLETA)

    # Lo que mandaria un Agent viejo
    devices.update_system_info("eq-parcial", {
        "hostname": "PC-CONTABILIDAD",
        "ip_address": "10.0.0.99"
    })

    datos = fila("eq-parcial")

    for campo, esperado in (("model", "20S20004US"),
                            ("manufacturer", "LENOVO"),
                            ("processor", "Intel64 Family 6 Model 142"),
                            ("cpu_count", 8),
                            ("ram_total", 16885047296),
                            ("storage_total", 510994149376)):

        comprobar("Se conserva %s" % campo,
                  datos.get(campo) == esperado,
                  "quedo en %r" % datos.get(campo))

    comprobar("Pero la IP SI se actualiza",
              datos["ip_address"] == "10.0.0.99",
              repr(datos["ip_address"]))


def test_una_ficha_sin_discos_guarda_el_resto():
    """
    El UPDATE estaba dentro de 'if disks:'. Sin discos no guardaba
    nada y reventaba despues leyendo cursor.rowcount.
    """

    nuevo_equipo("eq-sin-discos")

    sin_discos = {k: v for k, v in FICHA_COMPLETA.items() if k != "disks"}

    try:
        devices.update_system_info("eq-sin-discos", sin_discos)
        reventó = False

    except Exception as error:
        reventó = True
        comprobar("No revienta sin discos", False, str(error)[:120])

    if reventó:
        return

    comprobar("No revienta sin discos", True)

    datos = fila("eq-sin-discos")

    comprobar("Y guarda el procesador igualmente",
              datos["processor"] == "Intel64 Family 6 Model 142",
              repr(datos["processor"]))

    comprobar("Y el modelo",
              datos["model"] == "20S20004US", repr(datos["model"]))

    comprobar("El almacenamiento se queda sin dato, no inventado",
              datos["storage_total"] is None,
              repr(datos["storage_total"]))


def test_una_ficha_vacia_no_destruye_nada():
    """El caso extremo: llega un diccionario sin nada util."""

    nuevo_equipo("eq-vacia")

    devices.update_system_info("eq-vacia", FICHA_COMPLETA)

    devices.update_system_info("eq-vacia", {})

    datos = fila("eq-vacia")

    vacios = [c for c in CAMPOS if datos.get(c) is None]

    comprobar("Nada se pierde con una ficha vacia",
              not vacios, "se perdieron: %s" % sorted(vacios))


# ==================================================================
# 3. CUANDO SE SUPO
# ==================================================================

def test_se_registra_cuando_llego_la_ficha():
    """
    Para poder decir "estos datos son de hace tres semanas" en vez de
    presentarlos como si fueran de ahora.
    """

    nuevo_equipo("eq-fecha")

    antes = fila("eq-fecha")

    comprobar("Un equipo recien dado de alta no tiene fecha de ficha",
              antes.get("system_info_at") is None,
              repr(antes.get("system_info_at")))

    devices.update_system_info("eq-fecha", FICHA_COMPLETA)

    despues = fila("eq-fecha")

    comprobar("Tras la ficha, si la tiene",
              bool(despues.get("system_info_at")),
              repr(despues.get("system_info_at")))

    comprobar("Y es una fecha legible",
              "T" in str(despues.get("system_info_at")),
              repr(despues.get("system_info_at")))


def test_los_equipos_historicos_siguen_funcionando():
    """
    La columna es nueva. Las filas de antes quedan en NULL, que se
    lee como "nunca se supo", y no se inventa una fecha.
    """

    nuevo_equipo("eq-historico")

    datos = fila("eq-historico")

    comprobar("La columna existe", "system_info_at" in datos)

    comprobar("Y vale NULL, no una fecha inventada",
              datos["system_info_at"] is None)

    # Y el equipo se sigue pudiendo listar
    listado = devices.get_devices()

    comprobar("El equipo aparece en el inventario",
              any(d["device_id"] == "eq-historico" for d in listado))


# ==================================================================
# 4. EL AGENT LA MANDA SIN QUE NADIE LA PIDA
# ==================================================================

def test_el_agent_manda_su_ficha_al_conectar():
    """La correccion de la causa, no del sintoma."""

    comprobar("El Agent manda system_info al conectar",
              '"system_info:" + json.dumps(ficha)' in AGENTE)

    comprobar("Y la recopila en un hilo, sin bloquear el bucle",
              "await asyncio.to_thread(get_system_info)" in AGENTE)

    # El camino antiguo, a peticion, se conserva
    comprobar("Sigue respondiendo a get_system_info",
              'elif message == "get_system_info":' in AGENTE)

    # Y un fallo recopilando no puede tumbar la conexion
    trozo = AGENTE.split("await asyncio.to_thread(get_system_info)", 1)[1]
    trozo = trozo[:400]

    comprobar("Un fallo al recopilar no tumba la conexion",
              "except Exception" in trozo, trozo[:160])


# ==================================================================

def principal():

    pruebas = [
        test_el_alta_solo_escribe_tres_campos,
        test_con_la_ficha_se_llena_todo,
        test_una_ficha_parcial_no_borra_datos_buenos,
        test_una_ficha_sin_discos_guarda_el_resto,
        test_una_ficha_vacia_no_destruye_nada,
        test_se_registra_cuando_llego_la_ficha,
        test_los_equipos_historicos_siguen_funcionando,
        test_el_agent_manda_su_ficha_al_conectar
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("INFORMACION DEL EQUIPO")
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
