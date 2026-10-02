"""
Visibilidad de roles y acceso a la administracion de usuarios.

    .venv\\Scripts\\python tests/test_visibilidad_roles.py

Dos preguntas:

  - el Owner, ¿ve quien es Owner y quien Subadministrador?
  - el Subadmin, ¿puede llegar a la administracion de usuarios por algun
    camino, aunque el boton no aparezca?

Lo segundo se comprueba llamando a los endpoints REALES, no mirando si el
frontend esconde la seccion: esconder un boton no impide nada a quien
sepa escribir una peticion a mano.
"""

import io
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.users as users


JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# Todo lo que es administrar usuarios. Si manana se anade un endpoint mas
# y no se mete aqui, la ultima prueba del archivo lo delata.
OPERACIONES = [
    ("GET", "/api/users", None),
    ("POST", "/api/users",
     {"username": "colado", "password": "ClaveLargaDePrueba1!"}),
    ("POST", "/api/users/maria/permissions", {"permissions": ["devices.view"]}),
    ("POST", "/api/users/maria/active", {"active": False}),
    ("POST", "/api/users/maria/email", {"email": "otra@ejemplo.com"}),
    ("POST", "/api/users/maria/password",
     {"new_password": "ClaveLargaDePrueba1!"}),
    ("DELETE", "/api/users/maria", None)
]


def pedir(cliente, metodo, ruta, cuerpo=None):

    if metodo == "GET":
        return cliente.get(ruta)

    if metodo == "DELETE":
        return cliente.delete(ruta)

    return cliente.post(ruta, json=cuerpo or {})


def preparar():
    """Owner (odette) + dos subadministradores, como en el ejemplo."""

    h.reiniciar()

    h.crear_subadmin("juan", permisos=["devices.view"])
    h.crear_subadmin("maria", permisos=["devices.view", "recordings.view"])


# ==============================
# OWNER
# ==============================

def test_el_owner_ve_la_seccion():

    preparar()

    respuesta = h.cliente(h.OWNER).get("/api/users")

    comprobar("El Owner accede a la lista de usuarios",
              respuesta.status_code == 200, str(respuesta.status_code))

    comprobar("Y recibe a los tres",
              len(respuesta.json().get("users", [])) == 3)


def test_el_owner_ve_el_rol_de_cada_uno():

    preparar()

    usuarios = h.cliente(h.OWNER).get("/api/users").json()["users"]

    por_nombre = {u["username"]: u["role"] for u in usuarios}

    comprobar("Odette aparece como owner",
              por_nombre.get(h.OWNER) == "owner", str(por_nombre))

    comprobar("Juan aparece como subadmin",
              por_nombre.get("juan") == "subadmin")

    comprobar("Maria aparece como subadmin",
              por_nombre.get("maria") == "subadmin")

    comprobar("Todos los usuarios traen su rol",
              all(u.get("role") for u in usuarios))


def test_las_etiquetas_que_se_ensenan():
    """El rol se escribe igual en la tabla y en la ficha de la cuenta."""

    comprobar("Las etiquetas estan definidas en un solo sitio",
              "ETIQUETA_DE_ROL" in JS)

    comprobar("El owner se ensena como 'Owner'",
              'owner: "Owner"' in JS)

    comprobar("Y el subadmin como 'Subadministrador'",
              'subadmin: "Subadministrador"' in JS)

    comprobar("La tabla usa esa etiqueta",
              "etiquetaDeRol(usuario.role)" in JS)

    comprobar("Y la ficha de la cuenta tambien, cuando procede",
              "etiquetaDeRol(sesionActual.role)" in JS)


def test_mi_cuenta_solo_ensena_el_rol_al_owner():
    """
    La ficha de la cuenta dice el rol al Owner y no al subadministrador.

    A un subadministrador la pantalla le dice quien es, nada mas. La
    condicion es el rol real que manda el servidor, no el nombre del
    usuario ni el texto de la etiqueta.
    """

    comprobar("El parentesis del rol vive en un envoltorio propio",
              'id="account-role-wrapper"' in HTML)

    envoltorio = HTML.split('id="account-role-wrapper"', 1)[1][:120]

    comprobar("Que arranca oculto", "hidden" in envoltorio)

    comprobar("El rol sigue dentro de ese envoltorio",
              'id="account-role"' in envoltorio)

    comprobar(
        "Solo se ensena si quien mira es Owner",
        'envoltorioRol.classList.toggle("hidden", !sesionActual.is_owner)'
        in JS
    )

    comprobar("Y el texto del rol solo se rellena siendo Owner",
              "if (rol && sesionActual.is_owner) {" in JS)

    comprobar(
        "La decision usa el rol del servidor, no el nombre del usuario",
        "sesionActual.username ===" not in JS
        and 'username === "admin"' not in JS
    )

    # La tabla de usuarios no se ve afectada
    comprobar("La tabla de usuarios sigue mostrando el rol de cada uno",
              "etiquetaDeRol(usuario.role)" in JS)

    comprobar("Con su insignia", "insignia" in JS)

    comprobar("Y ETIQUETA_DE_ROL sigue existiendo",
              "ETIQUETA_DE_ROL" in JS
              and 'owner: "Owner"' in JS
              and 'subadmin: "Subadministrador"' in JS)

    comprobar("El Owner se distingue a simple vista",
              "bg-amber-100" in JS and "insignia" in JS)


def test_el_owner_conserva_todas_sus_funciones():

    preparar()

    owner = h.cliente(h.OWNER)

    permitidas = 0

    for metodo, ruta, cuerpo in OPERACIONES:

        respuesta = pedir(owner, metodo, ruta, cuerpo)

        if respuesta.status_code in (200, 400, 404):
            permitidas += 1
        else:
            comprobar(f"El Owner deberia poder {metodo} {ruta}",
                      False, str(respuesta.status_code))

    comprobar("El Owner no recibe ningun 403 administrando usuarios",
              permitidas == len(OPERACIONES),
              f"{permitidas}/{len(OPERACIONES)}")


def test_el_owner_crea_y_administra():

    preparar()

    owner = h.cliente(h.OWNER)

    alta = owner.post("/api/users", json={
        "username": "nuevo",
        "password": "ClaveLargaDePrueba1!",
        "permissions": ["devices.view"]
    })

    comprobar("El Owner crea un subadministrador",
              alta.status_code == 200, str(alta.status_code))

    if alta.status_code == 200:
        comprobar("Y nace con rol subadmin",
                  alta.json()["user"]["role"] == "subadmin")

    comprobar("Le cambia los permisos",
              owner.post("/api/users/nuevo/permissions",
                         json={"permissions": ["recordings.view"]}
                         ).status_code == 200)

    comprobar("Lo desactiva",
              owner.post("/api/users/nuevo/active",
                         json={"active": False}).status_code == 200)

    comprobar("Lo vuelve a activar",
              owner.post("/api/users/nuevo/active",
                         json={"active": True}).status_code == 200)

    comprobar("Y lo elimina",
              owner.delete("/api/users/nuevo").status_code == 200)


# ==============================
# SUBADMIN
# ==============================

def test_el_subadmin_no_llega_a_ninguna_operacion():

    preparar()

    cliente = h.cliente("juan")

    denegadas = 0

    for metodo, ruta, cuerpo in OPERACIONES:

        respuesta = pedir(cliente, metodo, ruta, cuerpo)

        if respuesta.status_code == 403:
            denegadas += 1
        else:
            comprobar(f"{metodo} {ruta} deberia dar 403 a un subadmin",
                      False, str(respuesta.status_code))

    comprobar("Un subadministrador recibe 403 en las siete operaciones",
              denegadas == len(OPERACIONES),
              f"{denegadas}/{len(OPERACIONES)}")


def test_ni_con_todos_los_permisos_operativos():
    """
    Tener todos los permisos de trabajo no da acceso a las cuentas.

    Administrar usuarios no es un permiso que se pueda conceder: es
    exclusivo del Owner por su rol.
    """

    preparar()

    users.set_permissions("juan", list(users.PERMISSIONS))

    cliente = h.cliente("juan")

    denegadas = sum(
        1 for metodo, ruta, cuerpo in OPERACIONES
        if pedir(cliente, metodo, ruta, cuerpo).status_code == 403
    )

    comprobar("Con los 13 permisos sigue sin poder administrar usuarios",
              denegadas == len(OPERACIONES),
              f"{denegadas}/{len(OPERACIONES)}")

    comprobar("Y sus permisos operativos siguen funcionando",
              cliente.get("/api/devices").status_code == 200)


def test_el_subadmin_no_ve_los_roles_ajenos():

    preparar()

    cliente = h.cliente("juan")

    comprobar("No puede listar usuarios",
              cliente.get("/api/users").status_code == 403)

    propio = cliente.get("/api/users/me")

    comprobar("Solo puede consultar lo suyo",
              propio.status_code == 200)

    datos = propio.json()

    comprobar("Y ahi aparece su propio nombre",
              datos.get("username") == "juan")

    comprobar("Con su propio rol", datos.get("role") == "subadmin")

    comprobar("Marcado como no-owner", datos.get("is_owner") is False)

    comprobar("Sin rastro de los demas usuarios",
              "maria" not in propio.text and "users" not in datos)


def test_nada_cambia_tras_los_intentos():

    preparar()

    cliente = h.cliente("juan")

    for metodo, ruta, cuerpo in OPERACIONES:
        pedir(cliente, metodo, ruta, cuerpo)

    maria = users.get_user("maria")

    comprobar("Maria sigue existiendo", maria is not None)

    comprobar("Sigue activa", maria["active"] is True)

    comprobar("Con sus permisos intactos",
              maria["permissions"] == ["devices.view", "recordings.view"])

    comprobar("Y no se ha creado el usuario que intento colar",
              users.get_user("colado") is None)

    comprobar("Siguen siendo tres usuarios",
              len(users.list_users()) == 3)


def test_un_subadmin_desactivado_tampoco():

    preparar()

    sesion = h.cliente("juan")

    users.set_active("juan", False)

    comprobar("Un subadmin desactivado no llega ni a 403: no esta autenticado",
              sesion.get("/api/users").status_code in (401, 403))


# ==============================
# FRONTEND
# ==============================

def test_el_frontend_esconde_la_seccion():

    comprobar("La seccion de usuarios existe en la pagina",
              'id="users-section"' in HTML)

    comprobar("Y arranca oculta",
              'id="users-section"' in HTML
              and 'class="hidden' in HTML.split('id="users-section"')[1][:80])

    comprobar("Solo se ensena si quien entra es Owner",
              'seccion.classList.toggle("hidden", !sesionActual.is_owner)'
              in JS)

    comprobar("Y la tabla solo se pide siendo Owner",
              "if (sesionActual.is_owner) {" in JS
              and "await cargarUsuarios();" in JS)


def test_el_frontend_no_decide_nada_por_su_cuenta():

    comprobar("is_owner viene del servidor, no del navegador",
              "sesionActual = await response.json()" in JS)

    comprobar("No se calcula el rol en el cliente",
              'is_owner =' not in JS.replace("sesionActual.is_owner", ""))


# ==============================
# COBERTURA
# ==============================

def test_todos_los_endpoints_de_usuarios_estan_cubiertos():
    """
    Avisa si se anade un endpoint de administracion sin probarlo aqui.

    /api/users/me queda fuera a proposito: cualquiera consulta lo suyo.
    """

    backend = io.open(RAIZ / "backend" / "main.py",
                      encoding="utf-8").read()

    import re

    rutas = set(re.findall(r'@app\.\w+\("(/api/users[^"]*)"\)', backend))

    rutas.discard("/api/users/me")

    cubiertas = {ruta for _, ruta, _ in OPERACIONES}

    # Las rutas con parametro se comparan por su forma
    normalizadas = {
        re.sub(r"/(maria|colado)(/|$)", r"/{username}\2", r)
        for r in cubiertas
    }

    faltan = rutas - normalizadas

    comprobar("Todas las rutas de administracion estan probadas",
              not faltan, str(sorted(faltan)))

    comprobar("Y todas exigen Owner en el backend",
              backend.count("require_owner(request)") >= len(rutas),
              str(backend.count("require_owner(request)")))


# ==============================

def main():

    pruebas = [
        test_el_owner_ve_la_seccion,
        test_el_owner_ve_el_rol_de_cada_uno,
        test_las_etiquetas_que_se_ensenan,
        test_mi_cuenta_solo_ensena_el_rol_al_owner,
        test_el_owner_conserva_todas_sus_funciones,
        test_el_owner_crea_y_administra,
        test_el_subadmin_no_llega_a_ninguna_operacion,
        test_ni_con_todos_los_permisos_operativos,
        test_el_subadmin_no_ve_los_roles_ajenos,
        test_nada_cambia_tras_los_intentos,
        test_un_subadmin_desactivado_tampoco,
        test_el_frontend_esconde_la_seccion,
        test_el_frontend_no_decide_nada_por_su_cuenta,
        test_todos_los_endpoints_de_usuarios_estan_cubiertos
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
