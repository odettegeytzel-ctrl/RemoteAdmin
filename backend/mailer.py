"""
Envio de correo de RemoteAdmin.

Una capa fina sobre SMTP con dos fines: que la configuracion real viva
SOLO en variables de entorno —nunca en el codigo ni en el repositorio— y
que las pruebas puedan enviar sin abrir una conexion a ninguna parte.

Transportes
-----------
  smtp   envio real. Necesita al menos REMOTEADMIN_SMTP_HOST.
  mock   no envia nada: guarda los mensajes en memoria. Es el que usan las
         pruebas y tambien el predeterminado, de modo que una instalacion
         sin configurar no intenta hablar con un servidor que no existe.

Variables de entorno
--------------------
  REMOTEADMIN_MAIL_TRANSPORT   smtp | mock   (por defecto: mock)
  REMOTEADMIN_SMTP_HOST
  REMOTEADMIN_SMTP_PORT        (por defecto: 587)
  REMOTEADMIN_SMTP_USER
  REMOTEADMIN_SMTP_PASSWORD
  REMOTEADMIN_SMTP_TLS         1 | 0        (por defecto: 1)
  REMOTEADMIN_MAIL_FROM
  REMOTEADMIN_PUBLIC_URL       base de los enlaces que se envian

Lo que este modulo NO hace: registrar el contenido de los mensajes. Un
correo de recuperacion lleva un enlace que vale tanto como una contrasena,
asi que ni el cuerpo ni el enlace aparecen en ningun log.
"""

import os
import smtplib

from email.message import EmailMessage


TRANSPORT_SMTP = "smtp"
TRANSPORT_MOCK = "mock"


class MailError(Exception):
    """No se pudo entregar el mensaje al servidor de correo."""


# Mensajes enviados con el transporte falso. Solo para pruebas: en
# produccion el transporte es smtp y esta lista queda vacia.
sent_messages = []


def _entorno(nombre, por_defecto=""):
    return (os.environ.get(nombre) or por_defecto).strip()


def get_transport():
    """Transporte configurado. 'mock' mientras no se diga otra cosa."""

    elegido = _entorno("REMOTEADMIN_MAIL_TRANSPORT", TRANSPORT_MOCK).lower()

    return elegido if elegido in (TRANSPORT_SMTP, TRANSPORT_MOCK) else TRANSPORT_MOCK


def get_public_url():
    """
    Base publica del panel, para construir los enlaces del correo.

    Se toma de la configuracion del servidor y NUNCA de la cabecera Host de
    la peticion: si no, bastaria con pedir la recuperacion falsificando esa
    cabecera para que el enlace apuntara al sitio del atacante.
    """

    return _entorno("REMOTEADMIN_PUBLIC_URL", "https://localhost:8000").rstrip("/")


def get_sender():
    return _entorno("REMOTEADMIN_MAIL_FROM", "remoteadmin@localhost")


def is_configured():
    """True si se puede enviar de verdad."""

    if get_transport() == TRANSPORT_MOCK:
        return True

    return bool(_entorno("REMOTEADMIN_SMTP_HOST"))


def reset_mock():
    sent_messages.clear()


def _enviar_por_smtp(mensaje):

    host = _entorno("REMOTEADMIN_SMTP_HOST")

    if not host:
        raise MailError("No hay servidor de correo configurado")

    try:
        puerto = int(_entorno("REMOTEADMIN_SMTP_PORT", "587"))
    except ValueError:
        puerto = 587

    usuario = _entorno("REMOTEADMIN_SMTP_USER")
    contrasena = _entorno("REMOTEADMIN_SMTP_PASSWORD")
    usar_tls = _entorno("REMOTEADMIN_SMTP_TLS", "1") not in ("0", "false", "no")

    try:

        with smtplib.SMTP(host, puerto, timeout=20) as servidor:

            if usar_tls:
                servidor.starttls()

            if usuario:
                servidor.login(usuario, contrasena)

            servidor.send_message(mensaje)

    except Exception as error:
        # El motivo sube para poder registrarlo, pero quien llame no debe
        # ensenarselo al usuario: delataria si la direccion existe.
        raise MailError(f"{type(error).__name__}: {error}") from error


def send_mail(to, subject, body):
    """
    Envia un mensaje de texto. Devuelve True si se entrego.

    Lanza MailError si falla. El cuerpo nunca se imprime ni se registra.
    """

    if not to:
        raise MailError("Falta el destinatario")

    mensaje = EmailMessage()
    mensaje["From"] = get_sender()
    mensaje["To"] = to
    mensaje["Subject"] = subject
    mensaje.set_content(body)

    if get_transport() == TRANSPORT_MOCK:

        sent_messages.append({
            "to": to,
            "subject": subject,
            "body": body
        })

        return True

    _enviar_por_smtp(mensaje)

    return True


def send_password_reset(to, token):
    """
    Correo de recuperacion con el enlace temporal.

    El token viaja en el enlace y en ningun otro sitio: no se registra, no
    se devuelve por la API y no se guarda en claro. Nunca se envia una
    contrasena por correo.
    """

    enlace = f"{get_public_url()}/frontend/index.html#reset={token}"

    cuerpo = (
        "Has pedido restablecer la contrasena de RemoteAdmin.\n\n"
        "Abre este enlace para elegir una nueva:\n\n"
        f"{enlace}\n\n"
        "El enlace caduca en una hora y solo se puede usar una vez.\n\n"
        "Si no has sido tu, no hace falta que hagas nada: la contrasena\n"
        "actual sigue siendo valida.\n"
    )

    return send_mail(to, "RemoteAdmin: restablecer contrasena", cuerpo)
