"""
Administracion de la PLATAFORMA desde la consola del servidor.

RemoteAdmin distingue dos niveles: quien opera la plataforma y quien
administra una empresa cliente. Este script sirve para designar al
primero.

    python platform_admin.py listar
    python platform_admin.py crear <usuario>
    python platform_admin.py promover <usuario>
    python platform_admin.py degradar <usuario>
    python platform_admin.py asignar <usuario> <id-organizacion>
    python platform_admin.py organizaciones

Por que desde consola y no desde el panel
-----------------------------------------
Ascender a operador de la plataforma da acceso a TODAS las
organizaciones. Si existiera un endpoint para hacerlo, el camino mas
corto para que el Owner de una empresa se ascendiera a si mismo seria
ese endpoint. Al vivir solo aqui, hace falta acceso al servidor, que es
una barrera real y no una comprobacion mas de permisos.

Quien pueda ejecutar este script controla toda la plataforma. Protege el
acceso al servidor en consecuencia.
"""

import sys

from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.database import init_db
from backend.audit import log_audit, STATUS_SUCCESS, STATUS_ERROR
from backend import organizations
from backend import users


# Quien ejecuta el script no tiene sesion ni IP: se identifica el canal
# y no se inventa una direccion que no existe.
USERNAME_CONSOLA = "consola"


def listar_usuarios():

    filas = users.list_users()

    if not filas:
        print("No hay usuarios todavia.")
        return 0

    nombres = {o["id"]: o["name"]
               for o in organizations.list_organizations()}

    print()
    print(f"{'USUARIO':<24} {'ROL':<16} {'ORGANIZACION':<24} ESTADO")
    print("-" * 78)

    for usuario in filas:

        organizacion = (
            nombres.get(usuario["organization_id"], "(desconocida)")
            if usuario["organization_id"] else "(plataforma)"
        )

        print(
            f"{usuario['username']:<24} "
            f"{usuario['role']:<16} "
            f"{organizacion:<24} "
            f"{'activo' if usuario['active'] else 'desactivado'}"
        )

    print()

    return 0


def listar_organizaciones():

    filas = organizations.list_organizations()

    if not filas:
        print("No hay organizaciones todavia.")
        return 0

    print()
    print(f"{'ORGANIZACION':<24} {'PLAN':<12} {'ESTADO':<12} "
          f"{'FACTURACION':<12} {'EQUIPOS':>8} {'USUARIOS':>9}")
    print("-" * 82)

    for organizacion in filas:

        uso = organizacion["usage"]

        print(
            f"{organizacion['name']:<24} "
            f"{organizacion['plan_label']:<12} "
            f"{organizacion['subscription_status']:<12} "
            f"{organizacion['billing_mode']:<12} "
            f"{uso['devices']:>8} {uso['users']:>9}"
            + ("" if organizacion["usable"] else "   [SIN ACCESO]")
        )

    print()

    return 0


def crear(username):
    """
    Crea una cuenta NUEVA de operador de la plataforma.

    Es la via recomendada: promover al Owner de una empresa lo desliga
    de ella, y si era el unico la organizacion se queda sin quien la
    administre. Crear una cuenta aparte deja cada cosa en su sitio.
    """

    import getpass

    from backend.auth import validate_new_password

    if users.get_user(username) is not None:
        print(f"Ya existe un usuario '{username}'.")
        print("Si quieres ascenderlo, usa: platform_admin.py promover")
        return 1

    print()
    print(f"Creando el operador de la plataforma '{username}'.")
    print("Esta cuenta podra administrar TODAS las organizaciones.")
    print()

    try:
        clave = getpass.getpass("Contrasena: ")
        repetida = getpass.getpass("Repite la contrasena: ")

    except (KeyboardInterrupt, EOFError):
        print(chr(10) + "Cancelado. No se ha creado nada.")
        return 1

    if clave != repetida:
        print(chr(10) + "Las contrasenas no coinciden. No se ha creado nada.")
        return 1

    problema = validate_new_password(clave)

    if problema:
        print(chr(10) + str(problema))
        return 1

    try:
        # Se crea como usuario normal y despues se asciende: asi pasa
        # por las mismas comprobaciones de nombre y contrasena que
        # cualquier otro, y acaba sin organizacion.
        users.create_user(username, clave, organization_id=None)
        usuario = users.set_platform_owner(username, True)

    except users.UserError as problema:
        print(chr(10) + f"No se pudo crear: {problema}")
        return 1

    log_audit(
        "platform.owner_created", status=STATUS_SUCCESS,
        username=USERNAME_CONSOLA,
        details={"objetivo": usuario["username"], "scope": "platform"}
    )

    print()
    print(f"'{usuario['username']}' es operador de la plataforma.")
    print("No pertenece a ninguna organizacion.")
    print("Las organizaciones conservan sus propios Owner.")

    return 0


def promover(username):

    try:
        usuario = users.set_platform_owner(username, True)

    except users.UserError as problema:

        print(f"No se pudo promover: {problema}")

        log_audit("platform.owner_granted", status=STATUS_ERROR,
                  username=USERNAME_CONSOLA,
                  details={"objetivo": username, "error": str(problema)})

        return 1

    log_audit(
        "platform.owner_granted", status=STATUS_SUCCESS,
        username=USERNAME_CONSOLA,
        details={"objetivo": usuario["username"], "scope": "platform"}
    )

    print(f"'{usuario['username']}' es ahora operador de la plataforma.")
    print("Puede administrar todas las organizaciones.")
    print("Ya no pertenece a ninguna organizacion concreta.")

    return 0


def degradar(username):

    try:
        usuario = users.set_platform_owner(username, False)

    except users.UserError as problema:
        print(f"No se pudo degradar: {problema}")
        return 1

    log_audit(
        "platform.owner_revoked", status=STATUS_SUCCESS,
        username=USERNAME_CONSOLA,
        details={"objetivo": usuario["username"], "scope": "platform"}
    )

    print(f"'{usuario['username']}' vuelve a ser Owner de organizacion.")
    print()
    print("AVISO: no pertenece a ninguna organizacion. Asignale una con:")
    print(f"    python platform_admin.py asignar {usuario['username']} "
          "<id-organizacion>")

    return 0


def asignar(username, organization_id):

    try:
        identificador = int(organization_id)

    except (TypeError, ValueError):
        print("El identificador de organizacion debe ser un numero.")
        return 1

    if organizations.get_organization(identificador) is None:
        print("Esa organizacion no existe.")
        return 1

    try:
        usuario = users.set_organization(username, identificador)

    except users.UserError as problema:
        print(f"No se pudo asignar: {problema}")
        return 1

    organizacion = organizations.get_organization(identificador)

    log_audit(
        "user.organization_changed", status=STATUS_SUCCESS,
        username=USERNAME_CONSOLA,
        details={"objetivo": usuario["username"],
                 "organization": organizacion["name"]}
    )

    print(f"'{usuario['username']}' pertenece ahora a "
          f"'{organizacion['name']}'.")

    return 0


def ayuda():

    print(__doc__)

    return 1


def main():

    init_db()
    users.ensure_owner_migrated()
    organizations.ensure_default_organization()

    argumentos = sys.argv[1:]

    if not argumentos:
        return ayuda()

    orden = argumentos[0].lower()

    if orden in ("listar", "usuarios"):
        return listar_usuarios()

    if orden in ("organizaciones", "orgs"):
        return listar_organizaciones()

    if orden == "crear" and len(argumentos) == 2:
        return crear(argumentos[1])

    if orden == "promover" and len(argumentos) == 2:
        return promover(argumentos[1])

    if orden == "degradar" and len(argumentos) == 2:
        return degradar(argumentos[1])

    if orden == "asignar" and len(argumentos) == 3:
        return asignar(argumentos[1], argumentos[2])

    return ayuda()


if __name__ == "__main__":
    sys.exit(main())
