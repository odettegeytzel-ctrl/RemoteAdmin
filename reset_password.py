"""
Restablece la contrasena del panel desde la CONSOLA DEL SERVIDOR.

    python reset_password.py

Esta es la unica via de recuperacion. Si nadie recuerda la contrasena, el
panel no se puede abrir de ninguna otra forma: no hay correo, ni codigos, ni
preguntas, ni endpoint HTTP. Es deliberado. Anadir un canal de recuperacion
por red significaria credenciales SMTP, una dependencia mas y una superficie
de ataque nueva, para servir a un operador que ya tiene acceso fisico a esta
maquina.

ADVERTENCIA DE SEGURIDAD
------------------------
Quien pueda ejecutar este script se apodera de la cuenta del panel, y con
ella del control remoto de todos los equipos administrados. No pide la
contrasena anterior: no serviria de nada si el motivo de ejecutarlo es
justamente haberla olvidado.

Por tanto, EL ACCESO AL SERVIDOR ES EL MECANISMO DE RECUPERACION. Protegelo
en consecuencia: cuenta de Windows con contrasena, sin sesiones abiertas sin
vigilancia, y acceso remoto al servidor restringido.

Cada ejecucion queda registrada en audit_log como 'auth.password_reset', con
username 'consola' y sin IP, para que un restablecimiento fuera de banda sea
tan visible como cualquier otra accion del panel.

No hace falta reiniciar el servidor: la contrasena vive en la base de datos
y se lee en cada intento de inicio de sesion.
"""

import getpass
import sys

from pathlib import Path


# Permite ejecutarlo desde cualquier carpeta
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.database import init_db
from backend.audit import log_audit, STATUS_SUCCESS, STATUS_ERROR
from backend.auth import (
    get_stored_password_hash,
    invalidate_all_sessions,
    set_password,
    validate_new_password,
    PASSWORD_MIN_LENGTH
)


# Quien ejecuta el script no tiene sesion ni IP: se identifica el canal, y
# no se inventa una direccion que no existe.
USERNAME_CONSOLA = "consola"


def reset_password(nueva_contrasena):
    """
    Aplica el restablecimiento. Separado de la interaccion por consola para
    poder probarlo sin teclear nada.

    Devuelve (ok, mensaje). El mensaje es apto para ensenar por pantalla:
    nunca contiene la contrasena ni el hash.
    """

    # La actual se usa solo para impedir repetir la misma, no para
    # autorizar: si se recuerda, no hace falta este script.
    #
    # Leer tambien puede fallar (base bloqueada, permisos, disco): si la
    # comprobacion se dejara fuera del try, el operador recibiria un
    # volcado de pila en vez de una explicacion.
    try:
        hash_actual = get_stored_password_hash()

    except Exception as error:

        log_audit(
            "auth.password_reset", status=STATUS_ERROR,
            username=USERNAME_CONSOLA,
            details=f"No se pudo leer la credencial: {type(error).__name__}"
        )

        return False, f"No se pudo guardar la contrasena: {error}"

    problema = validate_new_password(nueva_contrasena, hash_actual)

    if problema:

        log_audit(
            "auth.password_reset", status=STATUS_ERROR,
            username=USERNAME_CONSOLA,
            details=f"Contrasena rechazada: {problema}"
        )

        return False, problema

    try:
        corte = set_password(nueva_contrasena)

        # Un restablecimiento de emergencia cierra las sesiones de TODO el
        # mundo, no solo las del owner: si se llega aqui es porque algo va
        # mal, y dejar abiertas las sesiones de otros usuarios seria
        # justamente lo contrario de lo que se pretende.
        corte = max(corte, invalidate_all_sessions())

    except Exception as error:

        # Un fallo al escribir (base bloqueada, disco lleno, permisos) no
        # puede quedar como un exito silencioso ni soltar un volcado por
        # pantalla: se informa en una linea y se registra.
        log_audit(
            "auth.password_reset", status=STATUS_ERROR,
            username=USERNAME_CONSOLA,
            details=f"No se pudo guardar: {type(error).__name__}"
        )

        return False, f"No se pudo guardar la contrasena: {error}"

    log_audit(
        "auth.password_reset", status=STATUS_SUCCESS,
        username=USERNAME_CONSOLA,
        details="Contrasena restablecida desde consola; "
                "se cerraron todas las sesiones"
    )

    return True, (
        "Contrasena restablecida. Todas las sesiones abiertas se han "
        f"cerrado (corte: {corte})."
    )


def main():

    print()
    print("RemoteAdmin - restablecer la contrasena del panel")
    print("=" * 52)
    print()
    print("Quien ejecuta este script toma el control de la cuenta del")
    print("panel. Si no eres tu quien administra este servidor, cancela")
    print("con Ctrl+C.")
    print()
    print(f"La contrasena debe tener al menos {PASSWORD_MIN_LENGTH} "
          "caracteres.")
    print()

    # Crea las tablas si aun no existen; es aditivo y no borra nada
    init_db()

    try:
        nueva = getpass.getpass("Nueva contrasena: ")
        repetida = getpass.getpass("Repite la contrasena: ")

    except (KeyboardInterrupt, EOFError):
        print("\nCancelado. No se ha cambiado nada.")
        return 1

    if nueva != repetida:
        print("\nLas contrasenas no coinciden. No se ha cambiado nada.")
        return 1

    ok, mensaje = reset_password(nueva)

    print()
    print(mensaje)

    if ok:
        print()
        print("No hace falta reiniciar el servidor.")
        print("Los Agents conectados no se ven afectados.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
