"""
La descarga del instalador desde el panel.

    .venv\\Scripts\\python tests/test_descarga_instalador.py

Sobre el estado "removed"
-------------------------

La palabra NO aparece en ninguna parte del repositorio: ni en el
backend, ni en el frontend, ni en la configuracion de nginx. VEYRA no
la produce. Viene de fuera, y estas pruebas no pueden decir de donde.

Lo que si pueden hacer es descartar que el fallo este en VEYRA, y eso
es lo que comprueban: que el endpoint sirva el archivo correcto,
entero, con las cabeceras que un navegador necesita para guardarlo, y
solo a quien corresponde.

El defecto que si estaba en VEYRA
---------------------------------

El panel descargaba con fetch, construia un Blob de 76 MB en memoria
de la pestaña y pinchaba un enlace a blob:. Y llamaba a
revokeObjectURL JUSTO despues del click, antes de que el navegador
hubiera confirmado la descarga: revocar el blob ahi la deja a medias.

Ahora es una navegacion normal, que escribe en disco segun llega.
"""

import hashlib
import io
import os
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-descarga")

import users_harness as h  # noqa: E402

import backend.agent_releases as releases  # noqa: E402


JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def hay_instalador():
    return os.path.isfile(releases.installer_path())


def hay_version_publicada():
    try:
        releases.load_published()
        return True
    except Exception:
        return False


# ==================================================================
# 1. LA PALABRA NO SALE DE AQUI
# ==================================================================

def test_removed_no_esta_en_el_codigo():
    """
    Si apareciera, habria que mirar aqui. No aparece: el estado viene
    de un componente externo —el navegador, el antivirus o el
    sistema— y no de VEYRA.
    """

    sospechosos = []

    for carpeta in ("backend", "frontend", "agent", "deploy"):

        base = RAIZ / carpeta

        if not base.is_dir():
            continue

        for ruta in base.rglob("*"):

            if not ruta.is_file():
                continue

            if ruta.suffix not in (".py", ".js", ".html", ".conf",
                                   ".ps1", ".css"):
                continue

            if "runtime" in ruta.parts or "dist" in ruta.parts:
                continue

            try:
                texto = io.open(ruta, encoding="utf-8",
                                errors="ignore").read()

            except OSError:
                continue

            if "removed" in texto.lower():
                sospechosos.append(str(ruta.relative_to(RAIZ)))

    comprobar("VEYRA no produce la palabra 'removed'",
              not sospechosos, str(sospechosos[:5]))


# ==================================================================
# 2. DISPONIBILIDAD
# ==================================================================

def test_sin_instalador_publicado_se_dice():
    """Y el boton se deshabilita, en vez de fallar al pulsarlo."""

    if hay_instalador():
        # Se aparta temporalmente para probar el camino
        ruta = releases.installer_path()
        aparte = ruta + ".apartado-prueba"
        os.rename(ruta, aparte)

    else:
        aparte = None

    try:
        h.reiniciar()

        respuesta = h.cliente(h.OWNER).get("/api/agent-installer/info")

        comprobar("Responde 200 aunque no haya instalador",
                  respuesta.status_code == 200,
                  str(respuesta.status_code))

        datos = respuesta.json()

        comprobar("Y dice que no esta disponible",
                  datos.get("available") is False, str(datos))

        descarga = h.cliente(h.OWNER).get("/api/agent-installer")

        comprobar("Descargarlo da 503, no un archivo vacio",
                  descarga.status_code == 503,
                  str(descarga.status_code))

    finally:
        if aparte:
            os.rename(aparte, releases.installer_path())


def test_con_instalador_se_informa_de_su_version():

    if not hay_instalador() or not hay_version_publicada():
        comprobar("(sin instalador o sin version firmada)", True)
        return

    h.reiniciar()

    datos = h.cliente(h.OWNER).get("/api/agent-installer/info").json()

    comprobar("Esta disponible", datos.get("available") is True,
              str(datos))

    ficha = datos.get("installer") or {}

    comprobar("Con nombre", ficha.get("filename") == "VEYRA-Instalador.exe",
              repr(ficha.get("filename")))

    comprobar("Con version, del manifiesto FIRMADO",
              bool(ficha.get("version")), repr(ficha.get("version")))

    comprobar("Con tamano", (ficha.get("size") or 0) > 1000000,
              str(ficha.get("size")))

    comprobar("Y con huella",
              len(str(ficha.get("sha256") or "")) == 64,
              repr(ficha.get("sha256")))


# ==================================================================
# 3. LA DESCARGA
# ==================================================================

def test_se_sirve_el_archivo_correcto_y_entero():

    if not hay_instalador() or not hay_version_publicada():
        comprobar("(sin instalador publicado)", True)
        return

    h.reiniciar()

    respuesta = h.cliente(h.OWNER).get("/api/agent-installer")

    comprobar("Responde 200", respuesta.status_code == 200,
              str(respuesta.status_code))

    disco = io.open(releases.installer_path(), "rb").read()

    comprobar("Llega entero",
              len(respuesta.content) == len(disco),
              "%d de %d bytes" % (len(respuesta.content), len(disco)))

    comprobar("Y es el mismo archivo, byte a byte",
              hashlib.sha256(respuesta.content).hexdigest()
              == hashlib.sha256(disco).hexdigest())

    # Y coincide con lo que anuncia /info
    ficha = h.cliente(h.OWNER).get(
        "/api/agent-installer/info").json().get("installer") or {}

    comprobar("La huella servida es la anunciada",
              hashlib.sha256(respuesta.content).hexdigest()
              == ficha.get("sha256"),
              "anunciada %s" % str(ficha.get("sha256"))[:16])


def test_las_cabeceras_que_necesita_un_navegador():

    if not hay_instalador() or not hay_version_publicada():
        comprobar("(sin instalador publicado)", True)
        return

    h.reiniciar()

    respuesta = h.cliente(h.OWNER).get("/api/agent-installer")

    cabeceras = {k.lower(): v for k, v in respuesta.headers.items()}

    comprobar("Se fuerza la descarga, no se abre",
              "attachment" in cabeceras.get("content-disposition", ""),
              cabeceras.get("content-disposition"))

    comprobar("Con el nombre correcto",
              "VEYRA-Instalador.exe"
              in cabeceras.get("content-disposition", ""),
              cabeceras.get("content-disposition"))

    comprobar("Con Content-Length, para que se sepa cuanto falta",
              cabeceras.get("content-length") == str(
                  os.path.getsize(releases.installer_path())),
              cabeceras.get("content-length"))

    comprobar("Y se puede reanudar si se corta",
              cabeceras.get("accept-ranges") == "bytes",
              cabeceras.get("accept-ranges"))

    # Tipo estandar de descarga: un tipo propio de ejecutable invita
    # a que proxies y antivirus le den un trato distinto
    comprobar("El tipo es application/octet-stream",
              cabeceras.get("content-type") == "application/octet-stream",
              cabeceras.get("content-type"))


# ==================================================================
# 4. QUIEN PUEDE
# ==================================================================

def test_la_descarga_sigue_protegida():

    h.reiniciar()

    for ruta in ("/api/agent-installer", "/api/agent-installer/info"):

        respuesta = h.cliente().get(ruta)

        comprobar("Sin sesion, %s no entrega nada" % ruta,
                  respuesta.status_code in (401, 403),
                  str(respuesta.status_code))

    # Un subadmin no es propietario de la organizacion
    h.crear_subadmin("tecnico", permisos=["devices.view"])

    respuesta = h.cliente("tecnico").get("/api/agent-installer")

    comprobar("Un subadministrador no puede descargarlo",
              respuesta.status_code == 403,
              str(respuesta.status_code))

    comprobar("Y el owner si",
              h.cliente(h.OWNER).get(
                  "/api/agent-installer/info").status_code == 200)


# ==================================================================
# 5. EL PANEL
# ==================================================================

def test_el_panel_descarga_sin_blob():
    """
    El defecto que si estaba en VEYRA: 76 MB en memoria de la
    pestaña y revokeObjectURL justo despues del click.
    """

    cuerpo = JS.split("async function descargarInstaladorExe() {", 1)[1]
    cuerpo = cuerpo.split("\n}", 1)[0]

    comprobar("Ya no construye un Blob",
              "blob()" not in cuerpo and "createObjectURL" not in cuerpo,
              cuerpo[:200])

    # revokeObjectURL aparece en un COMENTARIO que explica por que ya
    # no se usa. Lo que no puede haber es la llamada de verdad: se
    # miran solo las lineas que no son comentario.
    activas = [l for l in cuerpo.splitlines()
               if not l.strip().startswith("//")]

    comprobar("Ni revoca nada justo tras el click",
              not any("revokeObjectURL" in l for l in activas),
              str([l.strip() for l in activas if "revokeObjectURL" in l]))

    comprobar("Y el motivo queda explicado",
              "revokeObjectURL" in cuerpo)

    comprobar("Navega directo al endpoint",
              'enlace.href = "/api/agent-installer"' in cuerpo)

    comprobar("Comprobando antes que hay algo que descargar",
              "/api/agent-installer/info" in cuerpo)

    comprobar("Y con un motivo claro si no lo hay",
              "no está publicado" in cuerpo)

    comprobar("Y si la cuenta no puede",
              "403" in cuerpo)


def test_solo_se_ofrece_el_exe_a_los_clientes():

    comprobar("Esta el boton del .exe",
              'id="install-download-exe"' in HTML)

    # El ZIP sigue existiendo, pero oculto
    bloque = HTML.split('id="install-zip-avanzado"', 1)

    comprobar("El ZIP esta en un bloque aparte", len(bloque) == 2)

    if len(bloque) == 2:
        comprobar("Y oculto por defecto",
                  "hidden" in bloque[1][:120], bloque[1][:120])

    comprobar("Solo lo ve el operador de la plataforma",
              "is_platform_owner" in
              JS.split("async function mostrarVersionDelInstalador(",
                       1)[1][:900])

    # Y su endpoint sigue vivo para la administracion tecnica
    comprobar("El endpoint del ZIP no se ha quitado",
              '"/api/agent-package"' in JS)


def test_el_actualizador_no_depende_del_exe():
    """
    El .exe es para instalar. Las actualizaciones van por el paquete
    firmado, que es otra cosa y no se toca aqui.
    """

    import backend.packaging as packaging

    comprobar("El paquete firmado no lleva el .exe",
              not any("Instalador" in f or ".exe" in f
                      for f in packaging.PACKAGE_FILES),
              str([f for f in packaging.PACKAGE_FILES if ".exe" in f]))

    comprobar("Y el instalador se sirve desde releases/",
              "releases" in releases.installer_path().replace("\\", "/"))


# ==================================================================

def principal():

    pruebas = [
        test_removed_no_esta_en_el_codigo,
        test_sin_instalador_publicado_se_dice,
        test_con_instalador_se_informa_de_su_version,
        test_se_sirve_el_archivo_correcto_y_entero,
        test_las_cabeceras_que_necesita_un_navegador,
        test_la_descarga_sigue_protegida,
        test_el_panel_descarga_sin_blob,
        test_solo_se_ofrece_el_exe_a_los_clientes,
        test_el_actualizador_no_depende_del_exe
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("DESCARGA DEL INSTALADOR")
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

    print()
    print("-" * 66)
    print("LO QUE ESTO NO PUEDE DECIR")
    print("-" * 66)
    print("""
De donde sale el estado 'removed'. No esta en el codigo de VEYRA, y
desde aqui no se ve el navegador, el antivirus ni el servidor Oracle.

Hace falta comprobarlo en el equipo donde pasa:

  - en Chrome, chrome://downloads  -> si dice "Eliminado" o
    "Archivo eliminado", lo borro algo del equipo, no VEYRA;
  - en Windows, Seguridad de Windows -> Proteccion antivirus ->
    Historial de proteccion, para ver si lo puso en cuarentena;
  - en Oracle, que el archivo siga ahi y con el dueño correcto.
""")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
