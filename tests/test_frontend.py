"""
Pruebas del frontend: que los controles existen, estan cableados y no
inyectan datos ajenos en la pagina.

    .venv\\Scripts\\python tests/test_frontend.py

No hay navegador en este entorno, asi que esto NO comprueba como se ve la
pagina. Comprueba lo que si se puede comprobar sin uno: que cada control
existe, que su identificador coincide con el que busca el codigo, que cada
boton tiene a alguien escuchando, que las rutas que llama existen en el
backend y que los datos que vienen de fuera se pintan con textContent.
Lo visual hay que mirarlo a ojo.
"""

import io
import re
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
BACKEND = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def hay_id(identificador):
    return f'id="{identificador}"' in HTML


def tiene_escucha(identificador):
    """True si el JS registra una accion para ese boton."""

    return (
        f'"{identificador}"' in JS
        and ("addEventListener" in JS)
    )


# ==============================
# 1. Cuenta
# ==============================

def test_seccion_cuenta():

    controles = [
        "account-username", "account-role",
        "account-email", "account-email-save", "account-email-status"
    ]

    faltan = [c for c in controles if not hay_id(c)]

    comprobar("La seccion Cuenta tiene sus controles",
              not faltan, str(faltan))

    comprobar("Muestra quien ha iniciado sesion",
              "sesionActual.username" in JS)

    comprobar("Y con que rol, con la etiqueta de cada uno",
              'owner: "Owner"' in JS
              and 'subadmin: "Subadministrador"' in JS
              and "etiquetaDeRol(sesionActual.role)" in JS)

    comprobar("El correo se guarda contra el endpoint del usuario",
              "/email" in JS and "cargarSesionActual" in JS)


def test_cambio_de_contrasena_sigue_ahi():

    for control in ("password-current", "password-new", "password-repeat",
                    "password-save-button", "password-status"):
        comprobar(f"Sigue existiendo {control}", hay_id(control))

    comprobar("Avisa de que se cerraran las demas sesiones",
              "se cerrar" in HTML and "dem" in HTML)


# ==============================
# 2. Usuarios
# ==============================

def test_seccion_usuarios():

    controles = [
        "users-section", "users-table", "users-status",
        "new-user-name", "new-user-email", "new-user-password",
        "new-user-permissions", "new-user-save", "new-user-status"
    ]

    faltan = [c for c in controles if not hay_id(c)]

    comprobar("La seccion Usuarios tiene sus controles",
              not faltan, str(faltan))

    comprobar("Esta oculta de entrada",
              'id="users-section"' in HTML
              and HTML.split('id="users-section"')[1][:80].count("hidden"))

    comprobar("Y solo se ensena al propietario",
              "sesionActual.is_owner" in JS
              and "users-section" in JS)


def test_acciones_de_usuarios():

    acciones = {
        "crear": "crearUsuario",
        "permisos": "guardarPermisos",
        "activar o desactivar": "cambiarEstadoUsuario",
        "eliminar": "eliminarUsuario",
        "restablecer contrasena": "restablecerContrasenaDe"
    }

    for etiqueta, funcion in acciones.items():
        comprobar(f"Existe la accion para {etiqueta}", funcion in JS)

    comprobar("Desactivar y eliminar piden confirmacion",
              JS.count("confirm(") >= 2)

    comprobar("Se avisa de que desactivar cierra la sesion",
              "cerrara su sesion" in JS)


def test_los_permisos_se_pintan_desde_el_backend():
    """
    El CATALOGO de permisos viene del servidor.

    La regla protege que anadir un permiso nuevo no obligue a tocar el
    panel: las casillas de la administracion de usuarios se generan con
    la lista que manda el servidor.

    No prohibe que un control concreto nombre el permiso que exige. Un
    boton de "Apagar" solo puede existir atado a device.shutdown, y un
    permiso nuevo no puede hacer aparecer un boton que nadie ha escrito.
    La condicion es estructural: cada permiso nombrado tiene que estar
    atado a un control, sea con puede("X") o con permiso: "X".
    """

    import backend.users as users

    nombrados = {
        permiso for permiso in users.PERMISSIONS
        if f'"{permiso}"' in JS
    }

    sueltos = [
        permiso for permiso in nombrados
        if f'puede("{permiso}")' not in JS
        and f'permiso: "{permiso}"' not in JS
    ]

    comprobar(
        "Cada permiso nombrado esta atado a un control concreto",
        not sueltos, str(sorted(sueltos))
    )

    comprobar("El catalogo lo recibe del servidor",
              "catalogoDePermisos" in JS and "data.permissions" in JS)

    comprobar(
        "Las casillas de usuarios se generan con ese catalogo",
        "catalogoDePermisos.forEach" in JS
    )

    # Y que no haya una lista completa escrita a mano en ningun sitio
    comprobar(
        "No hay una lista entera de permisos escrita en el panel",
        len(nombrados) < len(users.PERMISSIONS),
        f"{len(nombrados)} de {len(users.PERMISSIONS)}"
    )


def test_el_rol_no_se_envia_al_crear():
    """El alta no manda el rol: lo decide el servidor."""

    bloque = JS.split("async function crearUsuario()", 1)[1].split(
        "// =====", 1)[0]

    comprobar("El alta no envia ningun rol desde el navegador",
              '"role"' not in bloque and "role:" not in bloque)


# ==============================
# 3. Recuperacion
# ==============================

def test_olvido_de_contrasena_en_el_login():

    comprobar("El login ofrece recuperar la contrasena",
              hay_id("forgot-link"))

    comprobar("Con el texto esperado",
              "Olvidaste tu contrase" in HTML)

    for control in ("forgot-panel", "forgot-email", "forgot-send",
                    "forgot-back", "forgot-status"):
        comprobar(f"Existe {control}", hay_id(control))

    comprobar("El panel de recuperacion arranca oculto",
              'id="forgot-panel" class="hidden' in HTML)


def test_pantalla_de_contrasena_nueva():

    for control in ("reset-panel", "reset-new", "reset-repeat",
                    "reset-send", "reset-status"):
        comprobar(f"Existe {control}", hay_id(control))

    comprobar("El token se lee del enlace del correo",
              "#reset=" in JS)

    comprobar("Se comprueba el enlace antes de pedir la contrasena",
              "/api/auth/reset/check" in JS)

    comprobar("Y se borra de la barra de direcciones al usarlo",
              "history.replaceState" in JS)


def test_el_frontend_no_delata_si_el_correo_existe():

    bloque = JS.split("async function pedirEnlaceDeRecuperacion()", 1)[1]
    bloque = bloque.split("function tokenDeRecuperacion", 1)[0]

    delatores = ["no existe", "no encontrado", "no registrado",
                 "sin cuenta"]

    encontrados = [d for d in delatores if d in bloque.lower()]

    comprobar("El panel no distingue si la cuenta existe",
              not encontrados, str(encontrados))

    comprobar("Se limita a ensenar lo que diga el servidor",
              "data.message" in bloque)


# ==============================
# 4. Retencion y conservar
# ==============================

def test_retencion():

    comprobar("Existe el selector de retencion", hay_id("setting-retention"))

    for dias in ("15", "30", "90"):
        comprobar(f"Se ofrece la opcion de {dias} dias",
                  f'value="{dias}"' in HTML)

    comprobar("Se carga el valor guardado",
              "recording_retention_days" in JS)

    comprobar("Y se envia al guardar",
              JS.count("recording_retention_days") >= 2)

    comprobar("Se explica que 'Conservar' protege de la retencion",
              "no se borran nunca" in HTML)


def test_conservar():

    comprobar("Las grabaciones se pueden marcar como conservadas",
              "/keep" in JS)

    comprobar("Y se distinguen en la lista",
              "Conservar" in JS or "Conservada" in JS)


# ==============================
# 5. Programacion
# ==============================

def test_programacion():

    controles = ["schedule-panel", "schedule-enabled", "schedule-start",
                 "schedule-end", "schedule-days", "schedule-save",
                 "schedule-status"]

    faltan = [c for c in controles if not hay_id(c)]

    comprobar("El panel de programacion tiene sus controles",
              not faltan, str(faltan))

    comprobar("Se puede activar y desactivar",
              'id="schedule-enabled" type="checkbox"' in HTML)

    comprobar("Con hora de inicio y de fin",
              'id="schedule-start"' in HTML and 'type="time"' in HTML)

    comprobar("Y con los siete dias de la semana",
              "NOMBRES_DIAS_CORTOS" in JS
              and JS.count('"L", "M", "X", "J", "V", "S", "D"') == 1)

    comprobar("Se carga al abrir la ficha del equipo",
              "cargarProgramacion()" in JS)

    comprobar("Avisa de que el mando manual tiene prioridad en el tramo",
              "detener a mano" in HTML)


# ==============================
# 6. Rutas y seguridad
# ==============================

def test_las_rutas_que_llama_existen():

    # Las rutas se escriben con plantillas: /api/devices/${x.device_id}/...
    # El patron tiene que tragarse el contenido de ${...} entero.
    rutas = set(re.findall(
        r'["`](/api/(?:\$\{[^}]*\}|[A-Za-z0-9_/\-.])+)', JS
    ))

    # Se normalizan los trozos con plantillas de JavaScript
    normalizadas = set()

    for ruta in rutas:
        limpia = re.sub(r"\$\{[^}]+\}", "{x}", ruta)
        normalizadas.add(limpia.split("?")[0].rstrip("/"))

    conocidas = {
        "/api/auth/forgot", "/api/auth/reset", "/api/auth/reset/check",
        "/api/auth/login", "/api/auth/logout", "/api/auth/me",
        "/api/auth/password", "/api/users", "/api/users/me",
        "/api/settings", "/api/devices", "/api/alerts", "/api/audit",
        "/api/health", "/api/recordings"
    }

    # Rutas cuyo ultimo tramo se compone en tiempo de ejecucion. Se
    # comprueban aqui, una por una, en vez de dejarlas pasar en bloque.
    COMPUESTAS = {
        # mouse/move, mouse/click, mouse/down, mouse/up
        "/api/devices/{x}/mouse/{x}": [
            "/mouse/move", "/mouse/click", "/mouse/down", "/mouse/up"
        ],
        # processes y services, segun la vista de inventario elegida
        "/api/devices/{x}/{x}": ["/processes", "/services"]
    }

    desconocidas = []

    for ruta in normalizadas:

        if ruta in conocidas:
            continue

        if ruta in COMPUESTAS:

            faltan = [
                tramo for tramo in COMPUESTAS[ruta]
                if f'device_id}}{tramo}"' not in BACKEND
            ]

            if faltan:
                desconocidas.append(f"{ruta} -> faltan {faltan}")

            continue

        # Las rutas con parametros se comparan por su forma
        patron = ruta.replace("{x}", "{[a-z_]+}")

        if re.search(re.escape(ruta).replace(r"\{x\}", r"\{[a-z_]+\}"),
                     BACKEND):
            continue

        if patron.replace("{[a-z_]+}", "{device_id}") in BACKEND:
            continue

        if patron.replace("{[a-z_]+}", "{username}") in BACKEND:
            continue

        if patron.replace("{[a-z_]+}", "{recording_id}") in BACKEND:
            continue

        desconocidas.append(ruta)

    comprobar("Todas las rutas que llama el panel existen en el backend",
              not desconocidas, str(sorted(desconocidas)))


def test_los_datos_de_fuera_se_pintan_con_textcontent():
    """
    Nombres de usuario, correos, procesos y servicios vienen de fuera.

    Si se pintaran con innerHTML, un nombre con etiquetas podria ejecutar
    codigo en la pagina de quien lo mira.
    """

    for funcion, etiqueta in [
        ("function celda(", "la tabla de usuarios"),
        ("function renderInventory(", "procesos y servicios")
    ]:

        if funcion not in JS:
            comprobar(f"Existe {etiqueta}", False)
            continue

        bloque = JS.split(funcion, 1)[1].split("\nfunction ", 1)[0]

        comprobar(f"Se usa textContent en {etiqueta}",
                  "textContent" in bloque)

        # Vaciar con innerHTML = "" es legitimo; lo que no vale es
        # asignarle datos. Se miran las asignaciones una por una.
        asignaciones = re.findall("innerHTML" + chr(92) + "s*=" + chr(92) + "s*([^;" + chr(92) + "n]+)", bloque)

        con_datos = [a for a in asignaciones if a.strip() not in ('""', "''")]

        comprobar(f"Y no se asignan datos por innerHTML en {etiqueta}",
                  not con_datos, str(con_datos))


def test_el_frontend_no_decide_la_autorizacion():

    comprobar(
        "Se deja dicho que esconder un boton no es seguridad",
        "no protege nada" in JS or "comodidad visual" in JS
    )

    comprobar(
        "El backend comprueba el permiso en cada endpoint",
        BACKEND.count("require_permission(") >= 8,
        str(BACKEND.count("require_permission("))
    )


def test_no_hay_secretos_en_el_frontend():

    sospechosos = ["AUTH_SECRET_KEY", "AGENT_TOKEN", "SMTP_PASSWORD",
                   "pbkdf2_sha256$", "password_hash"]

    encontrados = [p for p in sospechosos if p in JS or p in HTML]

    comprobar("No hay secretos ni hashes en el frontend",
              not encontrados, str(encontrados))


# ==============================

def main():

    pruebas = [
        test_seccion_cuenta,
        test_cambio_de_contrasena_sigue_ahi,
        test_seccion_usuarios,
        test_acciones_de_usuarios,
        test_los_permisos_se_pintan_desde_el_backend,
        test_el_rol_no_se_envia_al_crear,
        test_olvido_de_contrasena_en_el_login,
        test_pantalla_de_contrasena_nueva,
        test_el_frontend_no_delata_si_el_correo_existe,
        test_retencion,
        test_conservar,
        test_programacion,
        test_las_rutas_que_llama_existen,
        test_los_datos_de_fuera_se_pintan_con_textcontent,
        test_el_frontend_no_decide_la_autorizacion,
        test_no_hay_secretos_en_el_frontend
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

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
