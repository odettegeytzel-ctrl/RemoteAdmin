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
    python platform_admin.py correo <usuario> <direccion>
    python platform_admin.py correo-diagnostico

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
from backend import mailer
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
    print(f"{'USUARIO':<20} {'ROL':<15} {'ORGANIZACION':<20} "
          f"{'CORREO':<22} ESTADO")
    print("-" * 96)

    for usuario in filas:

        organizacion = (
            nombres.get(usuario["organization_id"], "(desconocida)")
            if usuario["organization_id"] else "(plataforma)"
        )

        # Sin correo no se puede recuperar la contrasena: se marca
        # para que se vea de un vistazo, porque el flujo de
        # recuperacion no avisa de esto (responde lo mismo siempre,
        # a proposito, para no revelar que direcciones existen).
        if not usuario.get("email"):
            correo = "SIN CORREO"
        elif usuario.get("email_verified"):
            correo = usuario["email"]
        else:
            correo = usuario["email"] + " (sin verificar)"

        print(
            f"{usuario['username']:<20} "
            f"{usuario['role']:<15} "
            f"{organizacion:<20} "
            f"{correo:<22} "
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


def correo(username, direccion):
    """
    Asigna la direccion a la que llegaran los enlaces de recuperacion.

    Hace falta porque sin correo el usuario NO puede recuperar su
    contrasena, y el propio flujo no lo avisa: responde siempre lo
    mismo para no revelar que direcciones existen en el sistema. Es
    decir, un usuario sin correo pide el enlace, ve un mensaje de
    exito y no recibe nada.
    """

    try:
        usuario = users.set_email(username, direccion)

    except users.UserError as problema:
        print(f"ERROR: {problema}")
        return 1

    log_audit(
        "user.email_set", username=USERNAME_CONSOLA,
        status=STATUS_SUCCESS, source_ip="consola",
        organization_id=usuario.get("organization_id"),
        details={"target": username}
    )

    print()
    print(f"Correo de '{username}' actualizado.")
    print()
    print("Queda SIN VERIFICAR a proposito: cambiar la direccion no")
    print("hereda la verificacion de la anterior.")
    print()

    if mailer.get_transport() == mailer.TRANSPORT_MOCK:
        print("AVISO: el envio de correo esta en modo 'mock', asi que")
        print("los enlaces de recuperacion NO saldran de este servidor.")
        print("Comprueba la configuracion con:")
        print("  python platform_admin.py correo-diagnostico")
        print()

    return 0


def correo_diagnostico():
    """
    Dice si la recuperacion por correo funcionaria de verdad.

    Existe porque el sistema puede parecer configurado sin estarlo: en
    modo 'mock' el envio se da por bueno y el mensaje se descarta en
    silencio. Nada en el panel lo delata.

    No se imprime ninguna contrasena ni usuario de SMTP: solo si estan
    definidos.
    """

    transporte = mailer.get_transport()

    print()
    print("CORREO DE RECUPERACION")
    print("-" * 60)
    print(f"  transporte           : {transporte}")

    problemas = []

    if transporte == mailer.TRANSPORT_MOCK:
        problemas.append(
            "El transporte es 'mock': los correos se descartan y NO "
            "llegan a nadie. Para enviar de verdad, pon "
            "REMOTEADMIN_MAIL_TRANSPORT=smtp"
        )

    else:

        for variable, obligatoria in (
            ("REMOTEADMIN_SMTP_HOST", True),
            ("REMOTEADMIN_SMTP_PORT", False),
            ("REMOTEADMIN_SMTP_USER", False),
            ("REMOTEADMIN_SMTP_PASSWORD", False),
            ("REMOTEADMIN_MAIL_FROM", False)
        ):
            puesta = bool(mailer._entorno(variable))

            print(f"  {variable:<21}: "
                  f"{'definida' if puesta else 'sin definir'}")

            if obligatoria and not puesta:
                problemas.append(f"Falta {variable}")

    publica = mailer.get_public_url()

    print(f"  direccion del panel  : {publica}")

    if "localhost" in publica or "127.0.0.1" in publica:
        problemas.append(
            "REMOTEADMIN_PUBLIC_URL apunta al propio servidor, asi que "
            "los enlaces del correo no se podran abrir desde fuera"
        )

    # Usuarios que no podrian recuperar su contrasena
    sin_correo = [
        u["username"] for u in users.list_users()
        if u["active"] and not u.get("email")
    ]

    print(f"  usuarios sin correo  : "
          f"{', '.join(sin_correo) if sin_correo else 'ninguno'}")

    if sin_correo:
        problemas.append(
            "Hay usuarios activos sin correo: no podrian recuperar su "
            "contrasena. Asignalo con: python platform_admin.py "
            "correo <usuario> <direccion>"
        )

    print()

    if problemas:
        print("NO esta listo para enviar:")
        for numero, problema in enumerate(problemas, 1):
            print(f"  {numero}. {problema}")
        print()
        return 1

    print("La recuperacion por correo esta configurada.")
    print()
    print("Para comprobarlo de verdad, pide un enlace desde el panel y")
    print("confirma que llega. Esta orden solo revisa la configuracion.")
    print()

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

    if orden == "correo" and len(argumentos) == 3:
        return correo(argumentos[1], argumentos[2])

    if orden in ("correo-diagnostico", "correo-diag"):
        return correo_diagnostico()

    return ayuda()


if __name__ == "__main__":
    sys.exit(main())
