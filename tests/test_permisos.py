"""
Pruebas de roles y permisos: que puede hacer un subadmin y que no.

    .venv\\Scripts\\python tests/test_permisos.py

Lo que se comprueba aqui es que la autorizacion la decide el SERVIDOR. Las
peticiones se hacen a los endpoints reales, no a las funciones internas:
que una funcion devuelva False no sirve de nada si el endpoint no la llama.
"""

import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.auth as auth
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# Cada permiso con un endpoint que lo exige. Si manana se anade un permiso
# nuevo y nadie lo comprueba en ningun endpoint, esta tabla lo delata.
ENDPOINTS = {
    "dashboard.view": ("GET", "/api/alerts"),
    "devices.view": ("GET", "/api/devices"),
    "recordings.view": ("GET", "/api/recordings"),
    "settings.view": ("GET", "/api/settings"),
    "processes.view": ("GET", "/api/devices/equipo-x/processes"),
    "services.view": ("GET", "/api/devices/equipo-x/services")
}


def pedir(cliente, metodo, ruta, cuerpo=None):

    if metodo == "GET":
        return cliente.get(ruta)

    if metodo == "DELETE":
        return cliente.delete(ruta)

    return cliente.post(ruta, json=cuerpo or {})


# ==============================
# 1. Permiso concedido / denegado
# ==============================

def test_sin_permisos_no_puede_nada():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=[])

    cliente = h.cliente("ana")

    denegados = 0

    for permiso, (metodo, ruta) in ENDPOINTS.items():

        respuesta = pedir(cliente, metodo, ruta)

        if respuesta.status_code == 403:
            denegados += 1
        else:
            comprobar(f"Sin permiso, {permiso} deberia dar 403",
                      False, str(respuesta.status_code))

    comprobar("Un subadmin sin permisos no accede a nada",
              denegados == len(ENDPOINTS),
              f"{denegados}/{len(ENDPOINTS)}")


def test_con_permiso_si_puede():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=list(ENDPOINTS))

    cliente = h.cliente("ana")

    for permiso, (metodo, ruta) in ENDPOINTS.items():

        respuesta = pedir(cliente, metodo, ruta)

        # 404 vale: el equipo de prueba no existe, pero el permiso paso
        comprobar(f"Con {permiso} ya no se rechaza por permisos",
                  respuesta.status_code != 403,
                  str(respuesta.status_code))


def test_el_owner_puede_siempre():

    h.reiniciar()

    cliente = h.cliente(h.OWNER)

    for permiso, (metodo, ruta) in ENDPOINTS.items():

        comprobar(f"El owner no necesita {permiso}",
                  pedir(cliente, metodo, ruta).status_code != 403)


def test_quitar_un_permiso_quita_el_acceso():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["devices.view", "recordings.view"])

    cliente = h.cliente("ana")

    comprobar("Con el permiso, accede",
              cliente.get("/api/devices").status_code == 200)

    users.set_permissions("ana", ["recordings.view"])

    comprobar("Al quitarlo, deja de acceder en el acto",
              cliente.get("/api/devices").status_code == 403)

    comprobar("Y conserva el que si tiene",
              cliente.get("/api/recordings").status_code == 200)


def test_permisos_de_escritura_separados_de_lectura():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["settings.view"])

    cliente = h.cliente("ana")

    comprobar("Puede ver los ajustes",
              cliente.get("/api/settings").status_code == 200)

    comprobar("Pero no guardarlos",
              cliente.post("/api/settings",
                           json={"server_name": "X"}).status_code == 403)

    users.set_permissions("ana", ["settings.view", "settings.edit"])

    comprobar("Con settings.edit ya puede guardar",
              cliente.post("/api/settings",
                           json={"server_name": "RemoteAdmin"}).status_code
              == 200)


def test_descarga_separada_de_visualizacion():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["recordings.view"])

    cliente = h.cliente("ana")

    comprobar("Ver la lista no da derecho a descargar",
              cliente.get("/api/recordings/1/download").status_code == 403)

    comprobar("Ni a marcar como conservada",
              cliente.post("/api/recordings/1/keep",
                           json={"keep": True}).status_code == 403)


def test_permisos_desconocidos_se_rechazan():

    h.reiniciar()
    h.crear_subadmin("ana")

    try:
        users.set_permissions("ana", ["devices.view", "inventado.total"])
        rechazo = False
    except users.UserError:
        rechazo = True

    comprobar("Un permiso inventado se rechaza", rechazo)

    comprobar("Y no se guarda nada de esa peticion",
              users.get_user("ana")["permissions"] == [])


# ==============================
# 2. Suplantacion
# ==============================

def test_no_se_puede_falsear_el_rol():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["devices.view"])

    cliente = h.cliente("ana")

    intentos = [
        ("cabecera X-Role", {"X-Role": "owner"}),
        ("cabecera X-User", {"X-User": h.OWNER}),
        ("cabecera X-Permissions", {"X-Permissions": "settings.edit"})
    ]

    for etiqueta, cabeceras in intentos:
        comprobar(
            f"No sirve suplantar el rol con una {etiqueta}",
            cliente.get("/api/users", headers=cabeceras).status_code == 403
        )

    comprobar(
        "Ni enviar el rol en el cuerpo de la peticion",
        cliente.post("/api/settings",
                     json={"server_name": "X", "role": "owner"}).status_code
        in (400, 403)
    )


def test_no_se_puede_falsear_el_usuario_al_crear():

    h.reiniciar()

    owner = h.cliente(h.OWNER)

    respuesta = owner.post("/api/users", json={
        "username": "colado",
        "password": h.CLAVE_SUBADMIN,
        "role": "owner"
    })

    comprobar("El alta ignora el rol que venga en el cuerpo",
              respuesta.status_code == 200)

    if respuesta.status_code == 200:
        comprobar("Y crea un subadmin, no un owner",
                  respuesta.json()["user"]["role"] == users.ROLE_SUBADMIN)


def test_un_subadmin_no_administra_usuarios():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=list(users.PERMISSIONS))
    h.crear_subadmin("beto")

    cliente = h.cliente("ana")

    operaciones = [
        ("GET", "/api/users", None),
        ("POST", "/api/users",
         {"username": "nuevo", "password": h.CLAVE_SUBADMIN}),
        ("POST", "/api/users/beto/permissions",
         {"permissions": ["devices.view"]}),
        ("POST", "/api/users/beto/active", {"active": False}),
        ("POST", "/api/users/beto/password",
         {"new_password": "OtraClaveLarga123!"}),
        ("DELETE", "/api/users/beto", None)
    ]

    denegadas = 0

    for metodo, ruta, cuerpo in operaciones:

        respuesta = pedir(cliente, metodo, ruta, cuerpo)

        if respuesta.status_code == 403:
            denegadas += 1
        else:
            comprobar(f"{metodo} {ruta} deberia dar 403",
                      False, str(respuesta.status_code))

    comprobar(
        "Ni con TODOS los permisos puede un subadmin administrar usuarios",
        denegadas == len(operaciones), f"{denegadas}/{len(operaciones)}"
    )

    comprobar("Y el otro usuario sigue intacto",
              users.get_user("beto") is not None
              and users.get_user("beto")["active"] is True)


def test_un_subadmin_no_lee_la_auditoria():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=list(users.PERMISSIONS))

    comprobar("La auditoria es solo del owner",
              h.cliente("ana").get("/api/audit").status_code == 403)

    comprobar("El owner si la lee",
              h.cliente(h.OWNER).get("/api/audit").status_code == 200)


def test_usuario_desactivado_no_pasa_aunque_tenga_permisos():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=list(users.PERMISSIONS))

    sesion = h.cliente("ana")

    users.set_active("ana", False)

    rechazos = 0

    for metodo, ruta in ENDPOINTS.values():
        if pedir(sesion, metodo, ruta).status_code in (401, 403):
            rechazos += 1

    comprobar("Un usuario desactivado no accede a nada",
              rechazos == len(ENDPOINTS), f"{rechazos}/{len(ENDPOINTS)}")


def test_usuario_eliminado_no_pasa():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["devices.view"])

    sesion = h.cliente("ana")

    comprobar("Antes de eliminarlo accede",
              sesion.get("/api/devices").status_code == 200)

    users.delete_user("ana")

    comprobar("Eliminado, su sesion ya no sirve",
              sesion.get("/api/devices").status_code in (401, 403),
              str(sesion.get("/api/devices").status_code))


def test_sin_sesion_no_pasa_nada():

    h.reiniciar()

    anonimo = h.cliente()

    rechazos = 0

    for metodo, ruta in ENDPOINTS.values():
        if pedir(anonimo, metodo, ruta).status_code == 401:
            rechazos += 1

    comprobar("Sin sesion, todo responde 401",
              rechazos == len(ENDPOINTS), f"{rechazos}/{len(ENDPOINTS)}")


# ==============================
# 3. users/me
# ==============================

def test_users_me_describe_lo_que_puede_hacer():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["devices.view", "recordings.view"])

    datos = h.cliente("ana").get("/api/users/me").json()

    comprobar("Informa del usuario", datos.get("username") == "ana")
    comprobar("Y de su rol", datos.get("role") == users.ROLE_SUBADMIN)
    comprobar("No es owner", datos.get("is_owner") is False)
    comprobar("Lista solo sus permisos",
              sorted(datos.get("permissions", []))
              == ["devices.view", "recordings.view"])

    del_owner = h.cliente(h.OWNER).get("/api/users/me").json()

    comprobar("El owner se identifica como tal",
              del_owner.get("is_owner") is True)

    comprobar("Y recibe la lista completa de permisos",
              len(del_owner.get("permissions", [])) == len(users.PERMISSIONS))


def test_la_cobertura_de_permisos_esta_comprobada():
    """
    Avisa si se anade un permiso sin ninguna prueba que lo cubra.

    Los de device.* son de acciones que todavia no existen; cuando se
    implementen, hay que anadirlos a ENDPOINTS.
    """

    pendientes = {
        "recordings.download", "recordings.manage",
        "device.lock", "device.logoff", "device.restart", "device.shutdown"
    }

    # settings.edit se comprueba aparte, en la prueba que separa ver de
    # guardar, porque exige enviar un cuerpo valido.
    cubiertos = set(ENDPOINTS) | pendientes | {"settings.edit"}

    comprobar("Todos los permisos definidos estan contemplados",
              set(users.PERMISSIONS) <= cubiertos,
              str(sorted(set(users.PERMISSIONS) - cubiertos)))


def test_el_informe_no_expone_secretos():

    texto = " ".join(f"{n} {d}" for n, _, d in resultados)

    comprobar("Ninguna comprobacion expone contrasenas ni hashes",
              h.CLAVE_OWNER not in texto and h.CLAVE_SUBADMIN not in texto)


# ==============================

def main():

    pruebas = [
        test_sin_permisos_no_puede_nada,
        test_con_permiso_si_puede,
        test_el_owner_puede_siempre,
        test_quitar_un_permiso_quita_el_acceso,
        test_permisos_de_escritura_separados_de_lectura,
        test_descarga_separada_de_visualizacion,
        test_permisos_desconocidos_se_rechazan,
        test_no_se_puede_falsear_el_rol,
        test_no_se_puede_falsear_el_usuario_al_crear,
        test_un_subadmin_no_administra_usuarios,
        test_un_subadmin_no_lee_la_auditoria,
        test_usuario_desactivado_no_pasa_aunque_tenga_permisos,
        test_usuario_eliminado_no_pasa,
        test_sin_sesion_no_pasa_nada,
        test_users_me_describe_lo_que_puede_hacer,
        test_la_cobertura_de_permisos_esta_comprobada,
        test_el_informe_no_expone_secretos
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__)

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
