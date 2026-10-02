"""
Pruebas de la retencion LOCAL del Agent.

    .venv\\Scripts\\python tests/test_retencion_local.py

Cambio de politica: una subida correcta ya no borra la copia local. El
equipo conserva sus grabaciones y es la retencion quien las retira cuando
cumplen los dias configurados.

Todo ocurre en una carpeta temporal con archivos reales creados aqui.
Nunca se toca C:\\ProgramData\\RemoteAdmin ni las grabaciones de produccion.
"""

import io
import os
import shutil
import sys
import tempfile
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

import paths
import storage


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


TEMPORAL = Path(tempfile.mkdtemp(prefix="retencion_local_"))
BASE = TEMPORAL / "recordings"
BASE.mkdir(parents=True, exist_ok=True)


def _base_de_prueba():
    BASE.mkdir(parents=True, exist_ok=True)
    return str(BASE)


paths.get_recordings_dir = _base_de_prueba
storage.get_recordings_dir = _base_de_prueba


DEVICE = "774f538e82d2537d"

DIA = 86400


def limpiar():

    if BASE.exists():
        shutil.rmtree(BASE)

    BASE.mkdir(parents=True, exist_ok=True)


_contador = [1700000000]


def crear(etiqueta, dias_de_antiguedad=0, device_id=DEVICE,
          nombre_literal=None):
    """
    Crea un segmento real con la antiguedad que se pida.

    El nombre sigue el patron del grabador, rec_<marca de tiempo>.mp4: es
    el que exige is_managed_recording(), y usar otro haria pasar las
    pruebas por el motivo equivocado. 'etiqueta' solo sirve para leer el
    codigo.
    """

    carpeta = Path(paths.get_device_recordings_dir(device_id)) / "2026" / "10" / "02"
    carpeta.mkdir(parents=True, exist_ok=True)

    if nombre_literal:
        nombre = nombre_literal
    else:
        _contador[0] += 1
        nombre = f"rec_{_contador[0]}.mp4"

    ruta = carpeta / nombre
    ruta.write_bytes(b"contenido de prueba")

    cuando = time.time() - dias_de_antiguedad * DIA
    os.utime(ruta, (cuando, cuando))

    return ruta


# ==============================
# 1. La copia local se conserva tras subir
# ==============================

def test_la_subida_no_borra_la_copia_local():
    """El invariante central de esta correccion."""

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    proceso = codigo.split("def _process_pending(", 1)[1].split(
        "def on_segment_complete(", 1)[0]

    comprobar("Confirmar la subida no borra nada",
              "delete_recording_file" not in proceso
              and "_delete_uploaded_recording" not in proceso)

    comprobar("Solo se retira de la cola de pendientes",
              "_remove_pending(segment" in proceso)

    comprobar("Ya no existe el borrado posterior a la subida",
              "def _delete_uploaded_recording" not in codigo)


def test_la_grabacion_queda_en_la_carpeta_del_equipo():

    limpiar()

    ruta = crear("1")

    comprobar("La grabacion vive en la carpeta del equipo",
              DEVICE in str(ruta))

    comprobar("Con la estructura de fecha",
              str(ruta).replace("\\", "/").endswith(
                  f"{DEVICE}/2026/10/02/{ruta.name}"))

    comprobar("Y sigue ahi despues de crearse", ruta.exists())


# ==============================
# 2. Retencion por dias
# ==============================

def test_retencion_de_15_dias():

    limpiar()

    vieja = crear("vieja", 20)
    nueva = crear("nueva", 10)

    resumen = storage.apply_local_retention(15)

    comprobar("Con 15 dias se retira la de 20", not vieja.exists())
    comprobar("Y se conserva la de 10", nueva.exists())
    comprobar("El resumen lo refleja", resumen["eliminados"] == 1,
              str(resumen))


def test_retencion_de_30_dias():

    limpiar()

    vieja = crear("vieja", 40)
    media = crear("media", 20)

    storage.apply_local_retention(30)

    comprobar("Con 30 dias se retira la de 40", not vieja.exists())
    comprobar("Y sobrevive la de 20", media.exists())


def test_retencion_de_90_dias():

    limpiar()

    vieja = crear("vieja", 100)
    media = crear("media", 40)

    storage.apply_local_retention(90)

    comprobar("Con 90 dias se retira la de 100", not vieja.exists())
    comprobar("Y sobrevive la de 40", media.exists())


def test_no_se_borra_nada_dentro_del_periodo():

    limpiar()

    recientes = [crear(f"rec_{i}.mp4", d)
                 for i, d in enumerate((0, 1, 7, 14))]

    storage.apply_local_retention(15)

    comprobar("Nada dentro del periodo se borra",
              all(r.exists() for r in recientes))


def test_sin_politica_no_se_borra_nada():
    """Fallo seguro: sin dias configurados, no se toca nada."""

    limpiar()

    vieja = crear("vieja", 1000)

    for valor in (None, 0, -30, "", "abc"):
        storage.apply_local_retention(valor)

    comprobar("Sin una retencion valida no se borra nada",
              vieja.exists())


# ==============================
# 3. Conservar
# ==============================

def test_keep_protege_la_copia_local():

    limpiar()

    protegida = crear("protegida", 500)
    normal = crear("normal", 500)

    storage.apply_local_retention(15, keep_names={protegida.name})

    comprobar("Una grabacion marcada no se borra nunca",
              protegida.exists())

    comprobar("Aunque tenga 500 dias y la retencion sea de 15",
              protegida.exists())

    comprobar("La no marcada si se retira", not normal.exists())


def test_quitar_keep_la_devuelve_a_la_retencion():

    limpiar()

    archivo = crear("x", 500)

    storage.apply_local_retention(15, keep_names={archivo.name})

    comprobar("Mientras esta marcada, sobrevive", archivo.exists())

    # El servidor manda la lista nueva, ya sin ella
    storage.apply_local_retention(15, keep_names=set())

    comprobar("Al quitar la marca, la siguiente pasada se la lleva",
              not archivo.exists())


def test_la_marca_se_compara_por_nombre_de_archivo():

    limpiar()

    archivo = crear("abc", 500)

    # El servidor envia la ruta completa; el Agent se queda con el nombre
    storage.apply_local_retention(
        15,
        keep_names={os.path.basename(f"equipo/2026/10/02/{archivo.name}")}
    )

    comprobar("La correspondencia se hace por el nombre del archivo",
              archivo.exists())


# ==============================
# 4. Archivos que no se pueden tocar
# ==============================

def test_no_se_borra_lo_que_se_esta_grabando():

    limpiar()

    grabando = crear("en_curso", 500)
    otra = crear("otra", 500)

    def en_curso(ruta):
        return os.path.normcase(os.path.abspath(ruta)) == \
            os.path.normcase(os.path.abspath(str(grabando)))

    resumen = storage.apply_local_retention(15, is_protected=en_curso)

    comprobar("El segmento que se esta escribiendo no se borra",
              grabando.exists())

    comprobar("Se contabiliza aparte", resumen["en_uso"] == 1, str(resumen))

    comprobar("Las demas si se retiran", not otra.exists())


def test_no_se_borra_lo_que_esta_pendiente_de_subir():

    limpiar()

    pendiente = crear("pendiente", 500)
    subida = crear("subida", 500)

    def en_cola(ruta):
        return os.path.normcase(os.path.abspath(ruta)) == \
            os.path.normcase(os.path.abspath(str(pendiente)))

    resumen = storage.apply_local_retention(15, is_pending=en_cola)

    comprobar("Un archivo pendiente de subir NO se borra",
              pendiente.exists())

    comprobar("Se contabiliza aparte", resumen["pendientes"] == 1,
              str(resumen))

    comprobar("Una ya confirmada y vencida si se retira",
              not subida.exists())


def test_un_fallo_de_subida_no_pierde_la_grabacion():
    """
    Si la subida falla, el archivo sigue en la cola y la retencion no lo
    toca, por antiguo que sea: es la unica copia que queda.
    """

    limpiar()

    fallida = crear("fallida", 5000)

    storage.apply_local_retention(
        15, is_pending=lambda r: True
    )

    comprobar("Una grabacion que no se pudo subir se conserva",
              fallida.exists())


# ==============================
# 5. Protecciones de F2 que siguen en pie
# ==============================

def test_no_se_borra_fuera_de_la_carpeta():

    limpiar()

    intruso = TEMPORAL / "rec_ajeno.mp4"
    intruso.write_bytes(b"no tocar")

    cuando = time.time() - 5000 * DIA
    os.utime(intruso, (cuando, cuando))

    storage.apply_local_retention(15)

    comprobar("Un archivo fuera de la carpeta administrada no se toca",
              intruso.exists())

    comprobar("Ni siquiera llamando al borrado directamente",
              storage.delete_recording_file(str(intruso)) is False)

    intruso.unlink()


def test_path_traversal():

    limpiar()

    victima = TEMPORAL / "rec_9999.mp4"
    victima.write_bytes(b"no tocar")

    carpeta = Path(paths.get_device_recordings_dir(DEVICE))

    trampa = str(carpeta / ".." / ".." / ".." / "rec_9999.mp4")

    comprobar("Una ruta con '..' no se considera gestionada",
              storage.is_managed_recording(trampa) is False)

    comprobar("Y no se borra",
              storage.delete_recording_file(trampa) is False)

    comprobar("El archivo de fuera sigue intacto", victima.exists())

    victima.unlink()


def test_solo_archivos_con_nombre_de_grabacion():

    limpiar()

    carpeta = Path(paths.get_device_recordings_dir(DEVICE))

    otro = carpeta / "notas.txt"
    otro.write_bytes(b"no soy una grabacion")

    cuando = time.time() - 5000 * DIA
    os.utime(otro, (cuando, cuando))

    storage.apply_local_retention(15)

    comprobar("Un archivo que no es una grabacion no se borra nunca",
              otro.exists())


def test_los_part_no_se_tocan():
    """Los .part son subidas a medias; no son grabaciones publicadas."""

    limpiar()

    carpeta = Path(paths.get_device_recordings_dir(DEVICE))

    parcial = carpeta / "rec_1700000001.mp4.abc123.part"
    parcial.write_bytes(b"a medias")

    cuando = time.time() - 5000 * DIA
    os.utime(parcial, (cuando, cuando))

    storage.apply_local_retention(15)

    comprobar("Un archivo .part no lo borra la retencion",
              parcial.exists())


def test_la_proteccion_de_espacio_sigue_funcionando():

    limpiar()

    comprobar("Siguen existiendo los limites de espacio",
              storage.STORAGE_LIMIT_BYTES > 0
              and storage.MIN_FREE_BYTES > 0)

    comprobar("Y la comprobacion previa a abrir un segmento",
              callable(storage.can_start_new_segment)
              and callable(storage.storage_blocked_reason))

    crear("1")

    comprobar("El recuento ve las grabaciones de las subcarpetas",
              storage.recordings_size_bytes() > 0)


def test_la_retencion_no_lanza_nunca():

    limpiar()

    try:
        storage.apply_local_retention(15, keep_names=None,
                                      is_protected=None, is_pending=None)
        storage.apply_local_retention("quince")
        lanzo = False

    except Exception:
        lanzo = True

    comprobar("La retencion no propaga errores", not lanzo)


# ==============================
# 6. Reinicio del Agent
# ==============================

def test_tras_un_reinicio_las_grabaciones_siguen_ahi():
    """
    Un reinicio no debe perder nada: los archivos estan en disco y la cola
    de pendientes es un archivo JSON, no memoria.
    """

    limpiar()

    reciente = crear("reciente", 1)
    pendiente = crear("pendiente", 500)

    # "Reinicio": se vuelve a aplicar la retencion desde cero, sin politica
    storage.apply_local_retention(None)

    comprobar("Sin politica tras el reinicio no se borra nada",
              reciente.exists() and pendiente.exists())

    # Llega la politica y la cola sigue reclamando el archivo viejo
    storage.apply_local_retention(
        15,
        is_pending=lambda r: os.path.basename(r) == pendiente.name
    )

    comprobar("La grabacion aun pendiente se conserva tras el reinicio",
              pendiente.exists())

    comprobar("Y la reciente tambien", reciente.exists())


def test_la_cola_de_pendientes_vive_en_disco():

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    comprobar("pending_uploads se guarda en un archivo",
              "pending_uploads.json" in codigo)

    comprobar("Y se lee al arrancar, no solo en memoria",
              "_load_pending()" in codigo)

    comprobar("La retencion consulta la cola antes de borrar",
              "_is_pending_upload" in codigo
              and "is_pending=_is_pending_upload" in codigo)


# ==============================
# 7. El servidor ya la tiene
# ==============================

def test_estar_subida_no_es_motivo_para_borrar():

    limpiar()

    subida = crear("subida", 1)

    # Ya no esta en la cola: el servidor la confirmo
    storage.apply_local_retention(15, is_pending=lambda r: False)

    comprobar(
        "Que el servidor ya la tenga NO basta para borrar la copia local",
        subida.exists()
    )

    # Lo que si la retira es cumplir los dias
    antigua = crear("antigua", 500)

    storage.apply_local_retention(15, is_pending=lambda r: False)

    comprobar("Lo que la retira es la antiguedad, no estar subida",
              not antigua.exists() and subida.exists())


def test_resubir_una_grabacion_que_el_servidor_ya_tiene():
    """
    Si el Agent reintenta una que el servidor ya tiene, el indice unico
    del servidor la trata como duplicada y el Agent la saca de la cola.
    Nada de eso borra el archivo local.
    """

    backend = io.open(RAIZ / "backend" / "main.py",
                      encoding="utf-8").read()

    comprobar("El servidor detecta la grabacion repetida",
              "find_recording_by_path" in backend
              and "duplicate" in backend)

    agente = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    comprobar("Y el Agent no borra nada al recibir esa respuesta",
              "delete_recording_file" not in agente.split(
                  "def _upload_one(", 1)[1].split("def ", 1)[0])


# ==============================
# 8. Las grabaciones historicas no se tocan
# ==============================

def test_no_se_toco_nada_de_produccion():

    reales = RAIZ / "server_recordings"

    comprobar("Esta suite no usa la carpeta real de grabaciones",
              str(BASE) != str(reales) and str(TEMPORAL) not in str(reales))

    comprobar("Trabaja en una carpeta temporal",
              "retencion_local_" in str(TEMPORAL))

    if reales.exists():
        archivos = list(reales.rglob("*.mp4"))
        comprobar("Las grabaciones del servidor siguen donde estaban",
                  len(archivos) >= 1, str(len(archivos)))


# ==============================

def main():

    pruebas = [
        test_la_subida_no_borra_la_copia_local,
        test_la_grabacion_queda_en_la_carpeta_del_equipo,
        test_retencion_de_15_dias,
        test_retencion_de_30_dias,
        test_retencion_de_90_dias,
        test_no_se_borra_nada_dentro_del_periodo,
        test_sin_politica_no_se_borra_nada,
        test_keep_protege_la_copia_local,
        test_quitar_keep_la_devuelve_a_la_retencion,
        test_la_marca_se_compara_por_nombre_de_archivo,
        test_no_se_borra_lo_que_se_esta_grabando,
        test_no_se_borra_lo_que_esta_pendiente_de_subir,
        test_un_fallo_de_subida_no_pierde_la_grabacion,
        test_no_se_borra_fuera_de_la_carpeta,
        test_path_traversal,
        test_solo_archivos_con_nombre_de_grabacion,
        test_los_part_no_se_tocan,
        test_la_proteccion_de_espacio_sigue_funcionando,
        test_la_retencion_no_lanza_nunca,
        test_tras_un_reinicio_las_grabaciones_siguen_ahi,
        test_la_cola_de_pendientes_vive_en_disco,
        test_estar_subida_no_es_motivo_para_borrar,
        test_resubir_una_grabacion_que_el_servidor_ya_tiene,
        test_no_se_toco_nada_de_produccion
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
