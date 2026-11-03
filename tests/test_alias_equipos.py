"""
El nombre que la empresa le pone a cada equipo.

    .venv\\Scripts\\python tests/test_alias_equipos.py

"DESKTOP-7XK92AB" no le dice nada a nadie; "Recepcion" si. El alias
es una ETIQUETA de VEYRA: va en su propia columna, no sustituye al
nombre de Windows en ningun sitio, y el identificador tecnico del
equipo sigue siendo device_id.

Lo que estas pruebas vigilan sobre todo es lo que NO debe cambiar.
Un alias es una comodidad; si para ganarla se tocara la identidad del
equipo, sus grabaciones o su organizacion, no valdria la pena.
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
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-alias")

import backend.database as database  # noqa: E402

CARPETA = tempfile.mkdtemp(prefix="alias-")
database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
database.init_db()

import backend.devices as devices  # noqa: E402

from backend.models import DeviceRegister  # noqa: E402


JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def alta(identificador, hostname="DESKTOP-7XK92AB"):

    devices.register_device(DeviceRegister(
        device_id=identificador, hostname=hostname,
        operating_system="Windows-11", ip_address="10.0.0.1"
    ))

    return identificador


def fila(identificador):

    conexion = database.get_connection()
    conexion.row_factory = sqlite3.Row

    try:
        f = conexion.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (identificador,)
        ).fetchone()

        return dict(f) if f else None

    finally:
        conexion.close()


# ==================================================================
# 1. SIN ALIAS Y CON ALIAS
# ==================================================================

def test_un_equipo_sin_alias_sigue_igual():
    """El registro debe funcionar sin alias, como siempre."""

    alta("eq-sin")

    datos = fila("eq-sin")

    comprobar("Se da de alta sin alias", datos is not None)

    comprobar("Y el alias queda vacio", datos["alias"] is None,
              repr(datos["alias"]))

    comprobar("Se le llama por su nombre de Windows",
              devices.nombre_visible(datos) == "DESKTOP-7XK92AB",
              devices.nombre_visible(datos))


def test_ponerle_un_nombre():

    alta("eq-con")

    devices.set_alias("eq-con", "Recepción")

    datos = fila("eq-con")

    comprobar("Se guarda el alias", datos["alias"] == "Recepción",
              repr(datos["alias"]))

    comprobar("Y pasa a ser el nombre visible",
              devices.nombre_visible(datos) == "Recepción")

    comprobar("Pero el nombre de Windows NO cambia",
              datos["hostname"] == "DESKTOP-7XK92AB",
              repr(datos["hostname"]))


def test_cambiarlo_y_quitarlo():

    alta("eq-cambio")

    devices.set_alias("eq-cambio", "Recepción")
    devices.set_alias("eq-cambio", "Contabilidad")

    comprobar("Se puede cambiar",
              fila("eq-cambio")["alias"] == "Contabilidad")

    devices.set_alias("eq-cambio", "")

    datos = fila("eq-cambio")

    comprobar("Un alias vacio lo QUITA",
              datos["alias"] is None, repr(datos["alias"]))

    comprobar("Y se vuelve al nombre de Windows",
              devices.nombre_visible(datos) == "DESKTOP-7XK92AB")

    comprobar("El equipo sigue existiendo", datos is not None)


# ==================================================================
# 2. VALIDACION
# ==================================================================

def test_se_aceptan_los_nombres_normales_en_espanol():

    for nombre in ("Recepción 1", "Almacén Norte", "PC de Contabilidad",
                   "Oficina — Dirección", "Ñoño", "Caja #3"):

        try:
            limpio = devices.limpiar_alias(nombre)
            bien = limpio == nombre

        except Exception as error:
            bien = False
            limpio = str(error)

        comprobar("Se acepta %r" % nombre, bien, repr(limpio))


def test_se_quitan_los_espacios_de_los_extremos():
    """
    " Recepcion " y "Recepcion" son el mismo sitio. Tenerlos
    distintos solo confunde al buscar.
    """

    comprobar("Se recortan los extremos",
              devices.limpiar_alias("  Recepción  ") == "Recepción")

    comprobar("Pero no los de dentro",
              devices.limpiar_alias("Almacén  Norte") == "Almacén  Norte")

    comprobar("Solo espacios equivale a sin alias",
              devices.limpiar_alias("     ") is None)


def test_se_rechaza_lo_que_no_vale():

    casos = [
        ("demasiado largo", "x" * 61),
        ("un nulo dentro", "hola\x00mundo"),
        ("un salto de linea", "linea\nrota"),
        ("un retorno de carro", "linea\rrota"),
        ("una tabulacion", "con\ttab"),
        ("un numero", 123),
        ("una lista", ["Recepción"])
    ]

    for nombre, valor in casos:

        try:
            devices.limpiar_alias(valor)
            rechazado = False

        except devices.AliasError:
            rechazado = True

        except Exception:
            rechazado = True

        comprobar("Se rechaza %s" % nombre, rechazado)

    # Justo en el limite si entra
    comprobar("60 caracteres entran",
              devices.limpiar_alias("x" * 60) == "x" * 60)


def test_un_intento_de_xss_se_guarda_como_texto():
    """
    No se escapa al GUARDAR: eso dejaria "&lt;b&gt;" dentro de la base
    para siempre. Se guarda el texto tal cual y quien pinta lo trata
    como texto.
    """

    alta("eq-xss")

    ataque = "<script>alert(1)</script>"

    devices.set_alias("eq-xss", ataque)

    comprobar("Se guarda tal cual, sin escapar en la base",
              fila("eq-xss")["alias"] == ataque,
              repr(fila("eq-xss")["alias"]))

    # Y el frontend lo pinta con textContent, no con innerHTML
    bloque = JS.split('"detail-hostname"\n).textContent', 1)

    comprobar("La ficha lo pinta con textContent",
              len(bloque) == 2)

    comprobar("Hay una sola funcion de presentacion",
              "function nombreDeEquipo(" in JS)

    # El LISTADO tambien, que antes no.
    #
    # La fila se arma con una plantilla y el nombre se interpolaba
    # dentro: un alias con etiquetas se ejecutaba en el panel de
    # cualquiera que abriera la lista. Que lo escriba un usuario
    # autenticado de la propia organizacion no lo hace marcado de
    # confianza, y el hostname lo manda el equipo.
    fila_lista = JS.split("visibles.forEach(device => {", 1)[1][:6000]

    comprobar("El listado NO interpola el nombre en la plantilla",
              "${nombreDeEquipo(device)}" not in fila_lista,
              "sigue interpolandose en innerHTML")

    comprobar("Ni el nombre de Windows",
              "${nombreRealSiHayAlias(device)}" not in fila_lista)

    comprobar("Los pone como texto",
              'celdaNombre.textContent = nombreDeEquipo(device)' in JS
              and "celdaNombreReal.textContent" in JS)


# ==================================================================
# 3. LO QUE NO DEBE CAMBIAR
# ==================================================================

def test_el_alias_no_toca_la_identidad_del_equipo():
    """
    Lo mas importante. Un alias es una comodidad; si para ganarla se
    tocara la identidad, el token o la organizacion, no valdria la
    pena.
    """

    alta("eq-identidad")

    devices.issue_agent_token("eq-identidad")

    antes = fila("eq-identidad")

    devices.set_alias("eq-identidad", "Recepción")

    despues = fila("eq-identidad")

    for campo in ("device_id", "hostname", "agent_token_hash",
                  "agent_token_active", "organization_id",
                  "created_at", "operating_system", "ip_address"):

        comprobar("No cambia %s" % campo,
                  antes.get(campo) == despues.get(campo),
                  "%r -> %r" % (antes.get(campo), despues.get(campo)))


def test_el_agente_actualiza_el_hostname_sin_borrar_el_alias():
    """
    Pasa de verdad: a un equipo se le cambia el nombre en Windows, o
    manda su ficha al reconectar. El alias no puede perderse por eso.
    """

    alta("eq-reconecta", hostname="W11")

    devices.set_alias("eq-reconecta", "Equipo de mantenimiento")

    # El Agent manda su ficha, como al conectar
    devices.update_system_info("eq-reconecta", {
        "hostname": "W11-RENOMBRADO",
        "username": "ana",
        "processor": "Intel i7",
        "ip_address": "10.0.0.5"
    })

    datos = fila("eq-reconecta")

    comprobar("El hostname se actualiza",
              datos["hostname"] == "W11-RENOMBRADO",
              repr(datos["hostname"]))

    comprobar("Y el alias SIGUE",
              datos["alias"] == "Equipo de mantenimiento",
              repr(datos["alias"]))

    comprobar("El nombre visible sigue siendo el alias",
              devices.nombre_visible(datos) == "Equipo de mantenimiento")


def test_persiste_tras_reabrir_la_base():
    """Persistencia de verdad: cerrar y volver a abrir."""

    alta("eq-persiste")

    devices.set_alias("eq-persiste", "Laptop de almacén")

    conexion = database.get_connection()
    conexion.close()

    comprobar("Sigue ahi tras reabrir",
              fila("eq-persiste")["alias"] == "Laptop de almacén",
              repr(fila("eq-persiste")["alias"]))


def test_un_equipo_que_no_existe():

    comprobar("Devuelve None, no revienta",
              devices.set_alias("no-existe", "Recepción") is None)


# ==================================================================
# 4. MIGRACION
# ==================================================================

def test_la_columna_es_aditiva_y_reversible():

    datos = fila("eq-sin") or {}

    comprobar("La columna existe", "alias" in datos)

    comprobar("Y los equipos anteriores quedan en NULL",
              datos.get("alias") is None)

    # init_db dos veces no puede romper nada
    database.init_db()

    comprobar("init_db es idempotente", fila("eq-sin") is not None)


# ==================================================================
# 5. API, PERMISOS Y ORGANIZACIONES
# ==================================================================

def test_la_api_y_sus_permisos():

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
        ("eq-api", "DESKTOP-API", propia)
    )
    conexion.commit()
    conexion.close()

    ruta = "/api/devices/eq-api/alias"

    # Sin sesion
    comprobar("Sin sesion no se renombra",
              h.cliente().put(ruta, json={"alias": "X"}).status_code
              in (401, 403))

    # Con devices.view NO basta: ver no es administrar
    h.crear_subadmin("solo-ver", permisos=["devices.view"])

    comprobar("Con devices.view no se puede renombrar",
              h.cliente("solo-ver").put(
                  ruta, json={"alias": "X"}).status_code == 403)

    # Con devices.manage si
    h.crear_subadmin("gestor", permisos=["devices.view", "devices.manage"])

    respuesta = h.cliente("gestor").put(ruta, json={"alias": "Recepción"})

    comprobar("Con devices.manage si",
              respuesta.status_code == 200,
              "respondio %d" % respuesta.status_code)

    if respuesta.status_code == 200:
        comprobar("Y devuelve el alias guardado",
                  respuesta.json().get("alias") == "Recepción",
                  str(respuesta.json()))

    # Un alias invalido se rechaza con un motivo
    malo = h.cliente("gestor").put(ruta, json={"alias": "x" * 200})

    comprobar("Un alias demasiado largo se rechaza",
              malo.status_code == 400, str(malo.status_code))

    comprobar("Con un motivo legible",
              "caracteres" in (malo.json().get("message") or "").lower(),
              str(malo.json()))

    # Un equipo que no existe
    comprobar("Un equipo inexistente da 404",
              h.cliente("gestor").put(
                  "/api/devices/no-existe/alias",
                  json={"alias": "X"}).status_code == 404)

    # Otra organizacion
    # Nombre propio de esta suite: el banco de pruebas comparte
    # base y reiniciar() no borra organizaciones, asi que dos
    # suites con el mismo nombre chocarian al ejecutarse juntas.
    ajena = organizations.create_organization("Otra empresa (alias)")
    ajena = ajena["id"] if isinstance(ajena, dict) else ajena

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR REPLACE INTO devices (device_id, hostname, "
        "organization_id) VALUES (?, ?, ?)",
        ("eq-ajeno", "DESKTOP-AJENO", ajena)
    )
    conexion.commit()
    conexion.close()

    respuesta = h.cliente("gestor").put(
        "/api/devices/eq-ajeno/alias", json={"alias": "Mio"}
    )

    comprobar("Un equipo de otra empresa no se renombra",
              respuesta.status_code == 404,
              "respondio %d" % respuesta.status_code)

    conexion = database.get_connection()
    conexion.row_factory = sqlite3.Row

    ajeno = dict(conexion.execute(
        "SELECT alias FROM devices WHERE device_id = ?",
        ("eq-ajeno",)
    ).fetchone())

    conexion.close()

    comprobar("Y su alias sigue vacio", ajeno["alias"] is None,
              repr(ajeno["alias"]))


# ==================================================================
# 6. EL PANEL
# ==================================================================

def test_el_panel_lo_enseña_en_todas_partes():

    comprobar("Hay una sola funcion de presentacion",
              "function nombreDeEquipo(" in JS)

    # El patron vive ahora en UN solo sitio: dentro de la propia
    # funcion de presentacion. Si apareciera fuera, seria una vista
    # que enseña otro nombre que las demas.
    fuera = JS.split("function nombreDeEquipo(", 1)
    fuera = fuera[0] + fuera[1].split(chr(10) + "}", 1)[1]

    comprobar("Y ya no queda el patron repetido fuera de ella",
              "hostname || equipo.device_id" not in fuera
              and "selectedDevice.hostname || selectedDevice.device_id"
              not in fuera
              and "rec.hostname || rec.device_id" not in fuera)

    comprobar("Se usa en muchas vistas",
              JS.count("nombreDeEquipo(") >= 10,
              str(JS.count("nombreDeEquipo(")))

    comprobar("El nombre real se conserva debajo",
              "function nombreRealSiHayAlias(" in JS)


def test_el_buscador_encuentra_por_los_dos_nombres():

    bloque = JS.split("const campos = [", 1)[1].split("]", 1)[0]

    comprobar("Busca por alias", "device.alias" in bloque, bloque)

    comprobar("Y por nombre de Windows",
              "device.hostname" in bloque, bloque)


def test_la_ficha_permite_editarlo():

    comprobar("Hay un boton de editar",
              'id="detail-alias-editar"' in HTML)

    comprobar("Un campo", 'id="detail-alias-input"' in HTML)

    comprobar("Guardar y cancelar",
              'id="detail-alias-guardar"' in HTML
              and 'id="detail-alias-cancelar"' in HTML)

    comprobar("Y quitar el nombre",
              'id="detail-alias-quitar"' in HTML)

    comprobar("Con limite en el propio campo",
              'maxlength="60"' in HTML)

    comprobar("Se ve el nombre real debajo",
              'id="detail-hostname-real"' in HTML)

    # Y no recarga la pagina entera
    cuerpo = JS.split("async function guardarNombreDelEquipo(", 1)[1][:2200]

    comprobar("Guardar no recarga la pagina",
              "location.reload" not in cuerpo)

    comprobar("Refresca la ficha y el listado",
              "renderDeviceInfoFields(selectedDevice)" in cuerpo
              and "loadDevices()" in cuerpo)


def test_el_listado_permite_renombrar_sin_entrar_a_la_ficha():
    """
    Doble clic sobre el nombre y a escribir.

    Lo que mas importa aqui no es que se pueda editar, sino que al
    hacerlo NO pase ninguna otra cosa: la fila entera abre la ficha
    del equipo, y si ese clic siguiera su camino, empezar a
    renombrar cambiaria de pantalla y se perderia lo escrito.
    """

    cuerpo = JS.split("function editarNombreEnLaLista(", 1)

    comprobar("Existe el editor de la lista", len(cuerpo) == 2)

    if len(cuerpo) != 2:
        return

    editor = cuerpo[1][:4200]

    comprobar("Se entra con doble clic, no con uno solo",
              'celdaNombre.addEventListener("dblclick"' in JS)

    comprobar("El clic sobre el nombre no abre la ficha",
              'celdaNombre.addEventListener(\n                "click",\n'
              '                (evento) => evento.stopPropagation()\n'
              '            );' in JS)

    comprobar("Ni el del propio campo",
              'campo.addEventListener("click", '
              '(evento) => evento.stopPropagation());' in editor)

    comprobar("Mientras se escribe, la fila no navega",
              'celdaNombre.dataset.editando === "si"' in JS)

    comprobar("Enter guarda", 'evento.key === "Enter"' in editor)

    comprobar("Escape cancela y repone lo que habia",
              'evento.key === "Escape"' in editor
              and "restaurar(anterior)" in editor)

    comprobar("El teclado no se escapa de la fila",
              "evento.stopPropagation();" in editor)

    # Parte del campo, no del nombre visible: si no hay alias el
    # campo tiene que salir VACIO, no relleno con el de Windows.
    comprobar("El campo parte del alias, no del nombre visible",
              'campo.value = device.alias || "";' in editor)

    comprobar("Y enseña el de Windows como pista",
              'campo.placeholder = device.hostname' in editor)

    comprobar("Mismo tope que el servidor",
              "campo.maxLength = %d;" % devices.ALIAS_MAXIMO in editor,
              "ALIAS_MAXIMO = %d" % devices.ALIAS_MAXIMO)

    comprobar("Sin cambios no se llama al servidor",
              'escrito.trim() === (device.alias || "").trim()' in editor)

    comprobar("Un nombre rechazado no se da por guardado",
              "if (resultado.error)" in editor
              and "queja.textContent = resultado.error;" in editor)

    comprobar("Y no se repite el intento al salir del campo",
              'if (celda.querySelector("[data-queja]"))' in editor)


def test_renombrar_desde_la_lista_pide_el_mismo_permiso():
    """
    devices.manage, igual que la ficha y que el servidor.

    Ver el inventario y renombrar lo que hay en el no son la misma
    responsabilidad.
    """

    comprobar("El listado consulta el permiso",
              'puede("devices.manage")' in JS)

    # Y la lista espera a la sesion antes de pintar: sin eso, la
    # primera carga -la unica que ve quien entra- saldria siempre
    # sin la opcion.
    cuerpo = JS.split("async function loadDevices(", 1)[1][:1200]

    comprobar("Y espera a tener la sesion antes de pintar",
              "await asegurarSesion()" in cuerpo)

    comprobar("Sin tumbar el listado si la sesion falla",
              ".catch(() => null)" in cuerpo)

    # El servidor es quien manda, no el panel
    comprobar("El servidor lo exige igual",
              'require_permission(request, "devices.manage")'
              in io.open(RAIZ / "backend" / "main.py",
                         encoding="utf-8").read())


def test_el_alias_no_se_usa_como_identificador():
    """
    En las peticiones siempre device_id, nunca el alias.
    """

    # La peticion la hace una sola funcion, que recibe el
    # identificador. Se mira ahi, no en quien la llama.
    cuerpo = JS.split("async function pedirCambioDeNombre(", 1)[1][:1200]

    comprobar("La peticion va por device_id",
              "${deviceId}/alias" in cuerpo,
              cuerpo[:260])

    comprobar("Y quien la llama le pasa el device_id, no el nombre",
              "pedirCambioDeNombre(selectedDevice.device_id, alias)" in JS
              and "pedirCambioDeNombre(device.device_id, escrito)" in JS)


# ==================================================================

def principal():

    # Esta suite reclama SU base al empezar, no solo al importarse.
    #
    # DATABASE_PATH es global. Con un lanzador unico, la ultima suite
    # importada se queda con el, y las demas acabarian leyendo y
    # escribiendo en una base que no es la suya. Reclamarla aqui es
    # lo que hace que el resultado sea el mismo sola que acompañada.
    database.DATABASE_PATH = os.path.join(CARPETA, "prueba.db")
    database.init_db()

    pruebas = [
        test_un_equipo_sin_alias_sigue_igual,
        test_ponerle_un_nombre,
        test_cambiarlo_y_quitarlo,
        test_se_aceptan_los_nombres_normales_en_espanol,
        test_se_quitan_los_espacios_de_los_extremos,
        test_se_rechaza_lo_que_no_vale,
        test_un_intento_de_xss_se_guarda_como_texto,
        test_el_alias_no_toca_la_identidad_del_equipo,
        test_el_agente_actualiza_el_hostname_sin_borrar_el_alias,
        test_persiste_tras_reabrir_la_base,
        test_un_equipo_que_no_existe,
        test_la_columna_es_aditiva_y_reversible,
        test_la_api_y_sus_permisos,
        test_el_panel_lo_enseña_en_todas_partes,
        test_el_buscador_encuentra_por_los_dos_nombres,
        test_la_ficha_permite_editarlo,
        test_el_listado_permite_renombrar_sin_entrar_a_la_ficha,
        test_renombrar_desde_la_lista_pide_el_mismo_permiso,
        test_el_alias_no_se_usa_como_identificador
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("NOMBRE PERSONALIZADO DE LOS EQUIPOS")
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

    # Se borran los temporales, pero ANTES se aparta DATABASE_PATH a
    # otro temporal vacio.
    #
    # Dos problemas se evitan con esto, y el segundo es grave:
    #
    #   1. Si la ruta se quedara dentro de la carpeta recien borrada,
    #      la siguiente suite moriria con "unable to open database
    #      file", en un sitio que no tiene nada que ver con la causa.
    #
    #   2. Si se devolviera a la ruta que habia ANTES, esa ruta puede
    #      ser la base de desarrollo real. La siguiente suite llamaria
    #      a reiniciar(), que hace DELETE FROM users y DELETE FROM
    #      audit_log, y se cargaria datos de verdad. Paso.
    #
    # Por eso no se restaura nada: se apunta a un temporal nuevo, que
    # no es la base de nadie.
    database.DATABASE_PATH = os.path.join(
        tempfile.mkdtemp(prefix="apartado-"), "vacia.db"
    )

    shutil.rmtree(CARPETA, ignore_errors=True)

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
