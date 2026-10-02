"""
Pruebas de la carpeta de grabaciones por equipo.

    .venv\\Scripts\\python tests/test_carpetas.py

Con carpetas REALES en una ruta temporal. Nunca se toca
C:\\ProgramData\\RemoteAdmin ni las grabaciones del equipo.
"""

import io
import os
import shutil
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

import paths
import storage


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# Carpeta temporal que sustituye a la de datos del Agent
TEMPORAL = Path(tempfile.mkdtemp(prefix="carpetas_"))
BASE = TEMPORAL / "recordings"
BASE.mkdir(parents=True, exist_ok=True)


def _base_de_prueba():
    BASE.mkdir(parents=True, exist_ok=True)
    return str(BASE)


paths.get_recordings_dir = _base_de_prueba
storage.get_recordings_dir = _base_de_prueba


DEVICE = "774f538e82d2537d"


def limpiar():

    if BASE.exists():
        shutil.rmtree(BASE)

    BASE.mkdir(parents=True, exist_ok=True)


# ==============================
# 1. Creacion
# ==============================

def test_se_crea_la_carpeta_del_equipo():

    limpiar()

    carpeta = Path(paths.get_device_recordings_dir(DEVICE))

    comprobar("La carpeta se crea sola", carpeta.is_dir())

    comprobar("Con el nombre del equipo", carpeta.name == DEVICE)

    comprobar("Dentro de la carpeta de grabaciones",
              carpeta.parent == BASE)


def test_cada_equipo_la_suya():

    limpiar()

    uno = Path(paths.get_device_recordings_dir("equipo-uno"))
    otro = Path(paths.get_device_recordings_dir("equipo-dos"))

    comprobar("Dos equipos, dos carpetas distintas", uno != otro)

    comprobar("Las dos existen", uno.is_dir() and otro.is_dir())

    (uno / "rec_1.mp4").write_bytes(b"x")

    comprobar("Lo que graba uno no aparece en el otro",
              not (otro / "rec_1.mp4").exists())


def test_llamarla_dos_veces_no_falla():

    limpiar()

    primera = paths.get_device_recordings_dir(DEVICE)
    segunda = paths.get_device_recordings_dir(DEVICE)

    comprobar("Pedirla dos veces devuelve la misma y no rompe",
              primera == segunda and Path(primera).is_dir())


# ==============================
# 2. Sanitizacion y path traversal
# ==============================

def test_identificadores_validos():

    validos = ["774f538e82d2537d", "equipo-1", "equipo_2", "ABC123"]

    comprobar("Los identificadores normales pasan tal cual",
              all(paths.safe_device_folder(v) == v for v in validos))


def test_identificadores_peligrosos():

    peligrosos = [
        "..",
        "../../Windows",
        "..\\..\\Windows",
        "C:\\Windows",
        "/etc/passwd",
        "equipo/../../otro",
        "equipo\\sub",
        "con espacio",
        "punto.punto",
        "nombre;rm",
        "",
        None,
        "a" * 65
    ]

    descartados = [
        p for p in peligrosos
        if paths.safe_device_folder(p) == paths.UNKNOWN_DEVICE_FOLDER
    ]

    comprobar("Todo lo que no encaja en el patron se descarta entero",
              len(descartados) == len(peligrosos),
              f"{len(descartados)}/{len(peligrosos)}")


def test_el_traversal_no_sale_de_la_carpeta():

    limpiar()

    victima = TEMPORAL / "no_deberia_existir"

    for malicioso in ["../no_deberia_existir", "..\\no_deberia_existir",
                      "../../no_deberia_existir", ".."]:

        carpeta = Path(paths.get_device_recordings_dir(malicioso))

        comprobar(
            f"'{malicioso}' no saca la carpeta de su sitio",
            str(carpeta).startswith(str(BASE)),
            str(carpeta)
        )

    comprobar("No se ha creado nada fuera de la carpeta de grabaciones",
              not victima.exists())


def test_lo_descartado_va_a_una_carpeta_neutra():

    limpiar()

    carpeta = Path(paths.get_device_recordings_dir("../../malicioso"))

    comprobar("Un identificador rechazado acaba en la carpeta neutra",
              carpeta.name == paths.UNKNOWN_DEVICE_FOLDER)

    comprobar("Que tambien esta dentro de la de grabaciones",
              carpeta.parent == BASE)

    comprobar("Y no se pierde la grabacion: la carpeta existe",
              carpeta.is_dir())


# ==============================
# 3. Compatibilidad con el borrado de F2
# ==============================

def test_f2_reconoce_las_grabaciones_en_subcarpeta():

    limpiar()

    carpeta = Path(paths.get_device_recordings_dir(DEVICE))

    archivo = carpeta / "rec_1700000000.mp4"
    archivo.write_bytes(b"contenido")

    comprobar("F2 reconoce una grabacion dentro de la carpeta del equipo",
              storage.is_managed_recording(str(archivo)) is True)

    comprobar("Y la puede borrar",
              storage.delete_recording_file(str(archivo)) is True)

    comprobar("El archivo desaparece", not archivo.exists())


def test_f2_sigue_sin_borrar_fuera():

    limpiar()

    intruso = TEMPORAL / "ajeno.mp4"
    intruso.write_bytes(b"no tocar")

    comprobar("Un archivo fuera no se considera gestionado",
              storage.is_managed_recording(str(intruso)) is False)

    comprobar("Y no se borra",
              storage.delete_recording_file(str(intruso)) is False)

    comprobar("Sigue ahi", intruso.exists())

    intruso.unlink()


def test_f2_no_borra_por_traversal_desde_la_carpeta_del_equipo():

    limpiar()

    victima = TEMPORAL / "rec_9999.mp4"
    victima.write_bytes(b"no tocar")

    carpeta = Path(paths.get_device_recordings_dir(DEVICE))

    trampa = str(carpeta / ".." / ".." / "rec_9999.mp4")

    comprobar("Una ruta con '..' no se considera gestionada",
              storage.is_managed_recording(trampa) is False)

    comprobar("Y no se borra",
              storage.delete_recording_file(trampa) is False)

    comprobar("El archivo de fuera sigue intacto", victima.exists())

    victima.unlink()


def test_f2_solo_borra_archivos_con_nombre_de_grabacion():

    limpiar()

    carpeta = Path(paths.get_device_recordings_dir(DEVICE))

    otro = carpeta / "importante.txt"
    otro.write_bytes(b"no soy una grabacion")

    comprobar("Un archivo que no es una grabacion no se toca",
              storage.delete_recording_file(str(otro)) is False)

    comprobar("Sigue ahi", otro.exists())


def test_el_recuento_de_espacio_ve_las_subcarpetas():

    limpiar()

    for equipo in ("equipo-uno", "equipo-dos"):
        carpeta = Path(paths.get_device_recordings_dir(equipo))
        (carpeta / "rec_1.mp4").write_bytes(b"x" * 1000)

    total = storage.recordings_size_bytes()

    comprobar(
        "El control de espacio de F2 suma lo de todas las carpetas",
        total >= 2000, str(total)
    )


# ==============================
# 4. Integracion con el grabador
# ==============================

def test_el_grabador_usa_la_carpeta_del_equipo():

    codigo = io.open(RAIZ / "agent" / "recorder.py",
                     encoding="utf-8").read()

    comprobar("El grabador pide la carpeta del equipo",
              "get_device_recordings_dir" in codigo)

    comprobar("Y acepta el identificador en su constructor",
              "device_id=None" in codigo)

    agente = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    comprobar("El Agent se lo pasa cuando ya conoce su identidad",
              "screen_recorder.device_id = get_device_id()" in agente)


def test_el_nombre_del_equipo_nunca_se_usa_en_la_ruta():
    """
    El hostname no debe intervenir en ninguna ruta.

    Se comprueba por comportamiento y no buscando la palabra en el
    codigo: paths.py la menciona justamente para advertir que no se usa,
    y una prueba que lee comentarios no prueba nada.
    """

    limpiar()

    # Hostnames reales de Windows: llevan puntos, espacios o barras, que
    # es justo lo que el filtro no admite.
    hostnames = ["PC-OFICINA.local", "EQUIPO DE ANA", "DOMINIO\PC01",
                 "pc.casa.lan"]

    descartados = [
        n for n in hostnames
        if paths.safe_device_folder(n) == paths.UNKNOWN_DEVICE_FOLDER
    ]

    comprobar(
        "Un hostname no sirve como nombre de carpeta",
        len(descartados) == len(hostnames),
        f"{len(descartados)}/{len(hostnames)}"
    )

    recorder = io.open(RAIZ / "agent" / "recorder.py",
                       encoding="utf-8").read()

    comprobar("El grabador no menciona el hostname en ningun sitio",
              "hostname" not in recorder.lower())


# ==============================

def main():

    pruebas = [
        test_se_crea_la_carpeta_del_equipo,
        test_cada_equipo_la_suya,
        test_llamarla_dos_veces_no_falla,
        test_identificadores_validos,
        test_identificadores_peligrosos,
        test_el_traversal_no_sale_de_la_carpeta,
        test_lo_descartado_va_a_una_carpeta_neutra,
        test_f2_reconoce_las_grabaciones_en_subcarpeta,
        test_f2_sigue_sin_borrar_fuera,
        test_f2_no_borra_por_traversal_desde_la_carpeta_del_equipo,
        test_f2_solo_borra_archivos_con_nombre_de_grabacion,
        test_el_recuento_de_espacio_ve_las_subcarpetas,
        test_el_grabador_usa_la_carpeta_del_equipo,
        test_el_nombre_del_equipo_nunca_se_usa_en_la_ruta
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:70])

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLO"
        print(f"[{marca}] {nombre}" + (f"  -> {detalle}" if not ok else ""))
        if not ok:
            fallos += 1

    print(f"\n{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    try:
        shutil.rmtree(TEMPORAL)
    except OSError:
        pass

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
