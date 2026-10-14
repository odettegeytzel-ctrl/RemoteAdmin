"""
Quien puede ver y manejar la pantalla de otro equipo.

    .venv\\Scripts\\python tests/test_remoto_permisos.py

El agujero que esto cierra
--------------------------

El control remoto NO exigia ningun permiso de usuario. Lo que habia
era esto, en backend/main.py:

    CAPACIDAD_POR_RUTA = (
        ("/screen", ("POST",), "remote.actions"),
        ...

pero eso es la capacidad del PLAN: una restriccion comercial, de la
empresa contratante. En users.PERMISSIONS no existia nada parecido, y
ni POST /screen ni GET /screen-stream ni /keyboard ni /mouse/* se
molestaban en llamar a require_permission.

Dentro de una organizacion, cualquiera con sesion podia abrir la
pantalla de cualquier equipo con solo conocer su identificador. El
aislamiento ENTRE empresas si funcionaba —lo hace el middleware para
/api/devices/{id}/...— pero dentro no habia separacion ninguna.

Ver no es menos grave que actuar: en esa pantalla aparece el correo
de la persona, sus contraseñas mientras las teclea y todo lo que
tenga abierto.

Ahora existe "remote.control" y se exige en las nueve rutas.
"""

import io
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h  # noqa: E402

import backend.users as users  # noqa: E402
import backend.organizations as organizations  # noqa: E402
import backend.devices as devices  # noqa: E402


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# Las nueve rutas por las que se ve o se manda sobre una pantalla
RUTAS = (
    ("GET", "/api/devices/%s/screen-stream", None),
    ("GET", "/api/devices/%s/screen", None),
    ("POST", "/api/devices/%s/screen", None),
    ("POST", "/api/devices/%s/screen/stop", None),
    ("POST", "/api/devices/%s/mouse/move", {"x": 10, "y": 10}),
    ("POST", "/api/devices/%s/mouse/click", {"button": "left"}),
    ("POST", "/api/devices/%s/mouse/down", {"button": "left"}),
    ("POST", "/api/devices/%s/mouse/up", {"button": "left"}),
    ("POST", "/api/devices/%s/keyboard", {"action": "press", "key": "a"}),
)


def pedir(cliente, metodo, ruta, cuerpo):

    if metodo == "GET":
        return cliente.get(ruta)

    return cliente.post(ruta, json=cuerpo or {})


def equipo_en(organizacion, identificador):
    """Da de alta un equipo de prueba en esa organizacion."""

    conexion = __import__("backend.database", fromlist=["x"]).get_connection()

    try:
        conexion.execute(
            "INSERT OR REPLACE INTO devices "
            "(device_id, hostname, organization_id) VALUES (?, ?, ?)",
            (identificador, "PC-" + identificador, organizacion)
        )
        conexion.commit()

    finally:
        conexion.close()

    return identificador


# ==================================================================
# 1. SIN EL PERMISO
# ==================================================================

def test_sin_el_permiso_no_se_ve_la_pantalla():
    """El caso que estaba abierto."""

    h.reiniciar()

    organizacion = organizations.ensure_default_organization()
    propietario = users.get_user(h.OWNER)

    if propietario and propietario.get("organization_id"):
        organizacion = propietario["organization_id"]

    equipo = equipo_en(organizacion, "equipo-remoto")

    # Un subadmin con permisos de sobra, PERO sin remote.control
    h.crear_subadmin(
        "miron",
        permisos=["dashboard.view", "devices.view", "recordings.view",
                  "processes.view", "services.view"]
    )

    cliente = h.cliente("miron")

    for metodo, patron, cuerpo in RUTAS:

        respuesta = pedir(cliente, metodo, patron % equipo, cuerpo)

        comprobar(
            "Sin remote.control: %s %s" % (metodo, patron.split("/")[-1]),
            respuesta.status_code == 403,
            "respondio %d" % respuesta.status_code
        )


# ==================================================================
# 2. CON EL PERMISO
# ==================================================================

def test_con_el_permiso_si_puede():
    """
    La correccion no sirve de nada si deja fuera a quien debe entrar.

    El Agent no esta conectado en las pruebas, asi que la respuesta
    correcta NO es 200: es el error de "Agent no conectado". Lo que
    importa es que YA NO sea 403, que es lo que distingue "no tienes
    permiso" de "el equipo no esta".
    """

    h.reiniciar()

    organizacion = organizations.ensure_default_organization()
    propietario = users.get_user(h.OWNER)

    if propietario and propietario.get("organization_id"):
        organizacion = propietario["organization_id"]

    equipo = equipo_en(organizacion, "equipo-remoto")

    h.crear_subadmin(
        "tecnico",
        permisos=["devices.view", "remote.control"]
    )

    cliente = h.cliente("tecnico")

    for metodo, patron, cuerpo in RUTAS:

        respuesta = pedir(cliente, metodo, patron % equipo, cuerpo)

        comprobar(
            "Con remote.control: %s %s" % (metodo, patron.split("/")[-1]),
            respuesta.status_code != 403,
            "respondio 403 a quien SI tiene permiso"
        )


def test_el_owner_sigue_pudiendo():
    """El owner no usa la tabla de permisos: los tiene todos."""

    h.reiniciar()

    organizacion = organizations.ensure_default_organization()
    propietario = users.get_user(h.OWNER)

    if propietario and propietario.get("organization_id"):
        organizacion = propietario["organization_id"]

    equipo = equipo_en(organizacion, "equipo-remoto")

    cliente = h.cliente(h.OWNER)

    for metodo, patron, cuerpo in RUTAS:

        respuesta = pedir(cliente, metodo, patron % equipo, cuerpo)

        comprobar(
            "El owner puede: %s %s" % (metodo, patron.split("/")[-1]),
            respuesta.status_code != 403,
            "respondio %d" % respuesta.status_code
        )


# ==================================================================
# 3. OTRA ORGANIZACION
# ==================================================================

def test_de_otra_empresa_no_ve_nada_aunque_tenga_el_permiso():
    """
    El permiso no vale fuera de su empresa.

    Es la comprobacion que no puede fallar nunca: un tecnico con
    remote.control en su organizacion no puede asomarse a los equipos
    de otra, ni conociendo el identificador.
    """

    h.reiniciar()

    propia = organizations.ensure_default_organization()
    propietario = users.get_user(h.OWNER)

    if propietario and propietario.get("organization_id"):
        propia = propietario["organization_id"]

    ajena = organizations.create_organization("Otra empresa")

    if isinstance(ajena, dict):
        ajena = ajena["id"]

    equipo_ajeno = equipo_en(ajena, "equipo-de-otros")

    h.crear_subadmin(
        "tecnico-de-aqui",
        permisos=["devices.view", "remote.control"],
        organization_id=propia
    )

    cliente = h.cliente("tecnico-de-aqui")

    for metodo, patron, cuerpo in RUTAS:

        respuesta = pedir(cliente, metodo, patron % equipo_ajeno, cuerpo)

        comprobar(
            "De otra empresa, bloqueado: %s %s"
            % (metodo, patron.split("/")[-1]),
            respuesta.status_code in (403, 404),
            "respondio %d" % respuesta.status_code
        )


# ==================================================================
# 4. SIN SESION
# ==================================================================

def test_sin_sesion_no_se_llega_a_ninguna():

    h.reiniciar()

    cliente = h.cliente()

    for metodo, patron, cuerpo in RUTAS:

        respuesta = pedir(cliente, metodo, patron % "equipo-x", cuerpo)

        comprobar(
            "Sin sesion: %s %s" % (metodo, patron.split("/")[-1]),
            respuesta.status_code in (401, 403),
            "respondio %d" % respuesta.status_code
        )


# ==================================================================
# 5. LA MIGRACION
# ==================================================================

def test_a_quien_ya_daba_soporte_no_se_le_quita():
    """
    Exigir un permiso que antes no existia deja sin servicio a quien
    hoy lo usa. La migracion se lo concede a los que YA estan.
    """

    h.reiniciar()

    antiguo = h.crear_subadmin(
        "de-siempre", permisos=["devices.view"]
    )

    identificador = (antiguo["id"] if isinstance(antiguo, dict)
                     else antiguo)

    nuevos = users.grant_remote_control_to_existing_subadmins()

    comprobar("La migracion alcanza a los subadmins existentes",
              nuevos >= 1, "alcanzo a %s" % nuevos)

    usuario = users.get_user_by_id(identificador)

    comprobar("Y ahora lo tiene",
              "remote.control" in set(usuario.get("permissions") or ()),
              str(sorted(usuario.get("permissions") or ())))

    # Y no se repite
    segunda = users.grant_remote_control_to_existing_subadmins()

    comprobar("Ejecutarla dos veces no hace nada",
              segunda == 0, "la segunda alcanzo a %s" % segunda)


# ==================================================================

def principal():

    pruebas = [
        test_sin_el_permiso_no_se_ve_la_pantalla,
        test_con_el_permiso_si_puede,
        test_el_owner_sigue_pudiendo,
        test_de_otra_empresa_no_ve_nada_aunque_tenga_el_permiso,
        test_sin_sesion_no_se_llega_a_ninguna,
        test_a_quien_ya_daba_soporte_no_se_le_quita
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      type(error).__name__ + ": " + str(error)[:160])

    print()
    print("=" * 66)
    print("CONTROL REMOTO: QUIEN PUEDE")
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

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
