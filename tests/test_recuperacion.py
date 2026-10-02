"""
Pruebas de la recuperacion de contrasena por correo.

    .venv\\Scripts\\python tests/test_recuperacion.py

Con transporte de correo falso: no se abre ninguna conexion y no sale ni
un mensaje de la maquina. Base temporal y contrasenas inventadas.
"""

import os
import sys
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

# El transporte falso debe estar puesto antes de importar nada de correo
os.environ["REMOTEADMIN_MAIL_TRANSPORT"] = "mock"
os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo/"

import users_harness as h

import backend.auth as auth
import backend.database as database
import backend.mailer as mailer
import backend.ratelimit as ratelimit
import backend.recovery as recovery
import backend.users as users


CORREO = "ana@ejemplo.com"


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def preparar():
    """Owner + una subadmin con correo, sin tokens ni correos pendientes."""

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM password_reset_tokens")
    conexion.commit()
    conexion.close()

    mailer.reset_mock()
    ratelimit._failures.clear()

    h.crear_subadmin("ana", permisos=["devices.view"], email=CORREO)


def pedir_enlace(cliente=None, correo=CORREO):

    cliente = cliente or h.cliente()

    return cliente.post("/api/auth/forgot", json={"email": correo})


def token_del_ultimo_correo():
    """Extrae el token del enlace, como haria quien abre el correo."""

    if not mailer.sent_messages:
        return None

    cuerpo = mailer.sent_messages[-1]["body"]

    for trozo in cuerpo.split():
        if "#reset=" in trozo:
            return trozo.split("#reset=", 1)[1]

    return None


# ==============================
# 1. Respuesta indistinguible
# ==============================

def test_misma_respuesta_exista_o_no():

    preparar()

    existe = pedir_enlace()

    ratelimit._failures.clear()

    no_existe = pedir_enlace(correo="nadie@ejemplo.com")

    comprobar("Las dos responden 200",
              existe.status_code == no_existe.status_code == 200,
              f"{existe.status_code}/{no_existe.status_code}")

    comprobar("Con el mismo cuerpo, palabra por palabra",
              existe.json() == no_existe.json(),
              str(existe.json()))

    comprobar("La respuesta no dice si la cuenta existe",
              "no existe" not in existe.text.lower()
              and "no encontrad" not in existe.text.lower())


def test_un_correo_sin_cuenta_no_envia_nada():

    preparar()

    pedir_enlace(correo="nadie@ejemplo.com")

    comprobar("No se envia correo a una direccion sin cuenta",
              len(mailer.sent_messages) == 0)


def test_un_usuario_desactivado_no_recibe_enlace():

    preparar()

    users.set_active("ana", False)

    pedir_enlace()

    comprobar("Un usuario desactivado no recibe enlace",
              len(mailer.sent_messages) == 0)


def test_correo_vacio_o_ausente():

    preparar()

    respuesta = h.cliente().post("/api/auth/forgot", json={})

    comprobar("Sin correo responde igual que siempre",
              respuesta.status_code == 200
              and respuesta.json()["status"] == "ok")

    comprobar("Y no envia nada", len(mailer.sent_messages) == 0)


# ==============================
# 2. El correo y el token
# ==============================

def test_se_envia_el_enlace():

    preparar()

    pedir_enlace()

    comprobar("Se envia un correo", len(mailer.sent_messages) == 1)

    if mailer.sent_messages:

        mensaje = mailer.sent_messages[0]

        comprobar("A la direccion de la cuenta", mensaje["to"] == CORREO)

        comprobar("Con el enlace del panel configurado",
                  "https://panel.ejemplo/" in mensaje["body"])

        comprobar("El correo NO contiene ninguna contrasena",
                  h.CLAVE_SUBADMIN not in mensaje["body"]
                  and h.CLAVE_OWNER not in mensaje["body"])

        comprobar("Avisa de que caduca y es de un solo uso",
                  "caduca" in mensaje["body"]
                  and "una vez" in mensaje["body"])


def test_el_token_solo_se_guarda_hasheado():

    preparar()
    pedir_enlace()

    token = token_del_ultimo_correo()

    conexion = database.get_connection()

    filas = conexion.execute(
        "SELECT token_hash FROM password_reset_tokens"
    ).fetchall()

    conexion.close()

    comprobar("Se guarda una fila", len(filas) == 1)

    comprobar("El token en claro NO esta en la base",
              all(token not in f["token_hash"] for f in filas))

    comprobar("Lo guardado es un SHA-256",
              len(filas[0]["token_hash"]) == 64)

    comprobar("Y corresponde al token enviado",
              filas[0]["token_hash"] == recovery.hash_token(token))


def test_el_token_es_largo_y_aleatorio():

    preparar()

    tokens = set()

    for _ in range(10):
        tokens.add(recovery.create_token(users.get_user("ana")["id"]))

    comprobar("Diez tokens seguidos son todos distintos", len(tokens) == 10)

    comprobar("Y suficientemente largos",
              all(len(t) >= 40 for t in tokens),
              str(min(len(t) for t in tokens)))


def test_el_token_no_aparece_en_la_auditoria():

    preparar()
    pedir_enlace()

    token = token_del_ultimo_correo()

    todo = " ".join(
        f"{r['details']} {r['username']} {r['source_ip']}"
        for r in h.registros()
    )

    comprobar("El token no esta en el registro de auditoria",
              token not in todo)


def test_la_api_nunca_devuelve_el_token():

    preparar()

    respuesta = pedir_enlace()

    token = token_del_ultimo_correo()

    comprobar("La respuesta HTTP no incluye el token",
              token not in respuesta.text)


# ==============================
# 3. Uso del enlace
# ==============================

NUEVA = "ClaveRecuperadaDePrueba!"


def restablecer(token, nueva=NUEVA):

    return h.cliente().post(
        "/api/auth/reset",
        json={"token": token, "new_password": nueva}
    )


def test_reset_correcto():

    preparar()
    pedir_enlace()

    respuesta = restablecer(token_del_ultimo_correo())

    comprobar("El reset responde 200", respuesta.status_code == 200,
              str(respuesta.status_code))

    comprobar("Se puede entrar con la contrasena nueva",
              auth.authenticate("ana", NUEVA) is not None)

    comprobar("La anterior deja de servir",
              auth.authenticate("ana", h.CLAVE_SUBADMIN) is None)


def test_el_enlace_es_de_un_solo_uso():

    preparar()
    pedir_enlace()

    token = token_del_ultimo_correo()

    primera = restablecer(token)
    segunda = restablecer(token, "OtraClaveDistinta123!")

    comprobar("El primer uso funciona", primera.status_code == 200)

    comprobar("El segundo se rechaza", segunda.status_code == 400,
              str(segunda.status_code))

    comprobar("Y lo explica",
              "usado" in segunda.json().get("message", "").lower())

    comprobar("La segunda contrasena no se ha aplicado",
              auth.authenticate("ana", "OtraClaveDistinta123!") is None)


def test_enlace_caducado():

    preparar()
    pedir_enlace()

    token = token_del_ultimo_correo()

    # Se envejece el token en la base, sin esperar una hora
    conexion = database.get_connection()
    conexion.execute(
        "UPDATE password_reset_tokens SET expires_at = ?",
        (int(time.time()) - 10,)
    )
    conexion.commit()
    conexion.close()

    respuesta = restablecer(token)

    comprobar("Un enlace caducado se rechaza",
              respuesta.status_code == 400)

    comprobar("Y lo explica",
              "caducado" in respuesta.json().get("message", "").lower())

    comprobar("La contrasena no cambia",
              auth.authenticate("ana", h.CLAVE_SUBADMIN) is not None)


def test_token_inventado():

    preparar()

    respuesta = restablecer("token-que-nadie-ha-emitido-jamas")

    comprobar("Un token inventado se rechaza",
              respuesta.status_code == 400)

    comprobar("La contrasena no cambia",
              auth.authenticate("ana", h.CLAVE_SUBADMIN) is not None)


def test_la_politica_se_aplica_sin_quemar_el_enlace():

    preparar()
    pedir_enlace()

    token = token_del_ultimo_correo()

    debil = restablecer(token, "corta")

    comprobar("Una contrasena debil se rechaza",
              debil.status_code == 400)

    # El enlace no debe haberse gastado por un error del usuario
    buena = restablecer(token)

    comprobar("El enlace sigue sirviendo despues del rechazo",
              buena.status_code == 200, str(buena.status_code))


def test_pedir_otro_enlace_anula_el_anterior_al_usarlo():

    preparar()

    pedir_enlace()
    primero = token_del_ultimo_correo()

    ratelimit._failures.clear()

    pedir_enlace()
    segundo = token_del_ultimo_correo()

    comprobar("Son dos enlaces distintos", primero != segundo)

    comprobar("Usar el segundo funciona",
              restablecer(segundo).status_code == 200)

    comprobar("Y el primero deja de valer",
              restablecer(primero, "TerceraClaveDistinta1!").status_code
              == 400)


def test_comprobar_enlace_sin_gastarlo():

    preparar()
    pedir_enlace()

    token = token_del_ultimo_correo()

    cliente = h.cliente()

    comprobar("Se puede comprobar el enlace antes de usarlo",
              cliente.post("/api/auth/reset/check",
                           json={"token": token}).status_code == 200)

    comprobar("Comprobarlo no lo gasta",
              restablecer(token).status_code == 200)

    comprobar("Y despues ya no vale",
              cliente.post("/api/auth/reset/check",
                           json={"token": token}).status_code == 400)


# ==============================
# 4. Sesiones
# ==============================

def test_el_reset_invalida_las_sesiones():

    preparar()

    sesion = h.cliente("ana")

    comprobar("La sesion funciona antes del reset",
              sesion.get("/api/devices").status_code == 200)

    pedir_enlace()
    restablecer(token_del_ultimo_correo())

    comprobar("Tras el reset su sesion queda invalidada",
              sesion.get("/api/devices").status_code == 401,
              str(sesion.get("/api/devices").status_code))


def test_el_reset_no_toca_las_sesiones_de_otros():

    preparar()

    owner = h.cliente(h.OWNER)

    pedir_enlace()
    restablecer(token_del_ultimo_correo())

    comprobar("La sesion del owner sigue viva",
              owner.get("/api/devices").status_code == 200)

    comprobar("Y su contrasena no ha cambiado",
              auth.authenticate(h.OWNER, h.CLAVE_OWNER) is not None)


# ==============================
# 5. Rate limiting
# ==============================

def test_rate_limit_de_peticiones():

    preparar()

    cliente = h.cliente()

    codigos = [
        pedir_enlace(cliente).status_code
        for _ in range(ratelimit.RECOVERY_MAX_FAILURES)
    ]

    bloqueado = pedir_enlace(cliente)

    comprobar("Las primeras peticiones se atienden",
              set(codigos) == {200}, str(codigos))

    comprobar("Pasado el limite se responde 429",
              bloqueado.status_code == 429, str(bloqueado.status_code))

    comprobar("Indicando cuanto esperar",
              "retry-after" in {k.lower() for k in bloqueado.headers})


def test_el_limite_de_recuperacion_es_independiente():

    preparar()

    cliente = h.cliente()

    for _ in range(ratelimit.RECOVERY_MAX_FAILURES + 1):
        pedir_enlace(cliente)

    comprobar("El contador de recuperacion esta agotado",
              ratelimit.seconds_until_unblocked("testclient",
                                                scope="recovery") > 0)

    comprobar("Pero el del login no",
              ratelimit.seconds_until_unblocked("testclient",
                                                scope="login") == 0)

    comprobar("Ni el del cambio de contrasena",
              ratelimit.seconds_until_unblocked("testclient",
                                                scope="password") == 0)


# ==============================
# 6. Transporte de correo
# ==============================

def test_el_transporte_sale_del_entorno():

    anterior = os.environ.get("REMOTEADMIN_MAIL_TRANSPORT")

    try:
        os.environ["REMOTEADMIN_MAIL_TRANSPORT"] = "smtp"
        comprobar("Se puede elegir smtp por entorno",
                  mailer.get_transport() == "smtp")

        os.environ["REMOTEADMIN_MAIL_TRANSPORT"] = "inventado"
        comprobar("Un transporte desconocido cae en el falso",
                  mailer.get_transport() == "mock")

    finally:
        os.environ["REMOTEADMIN_MAIL_TRANSPORT"] = anterior or "mock"


def test_sin_servidor_configurado_falla_con_claridad():

    anterior_t = os.environ.get("REMOTEADMIN_MAIL_TRANSPORT")
    anterior_h = os.environ.get("REMOTEADMIN_SMTP_HOST")

    try:
        os.environ["REMOTEADMIN_MAIL_TRANSPORT"] = "smtp"
        os.environ["REMOTEADMIN_SMTP_HOST"] = ""

        try:
            mailer.send_mail("a@b.com", "x", "y")
            lanzo = False
        except mailer.MailError:
            lanzo = True

    finally:
        os.environ["REMOTEADMIN_MAIL_TRANSPORT"] = anterior_t or "mock"
        if anterior_h is None:
            os.environ.pop("REMOTEADMIN_SMTP_HOST", None)
        else:
            os.environ["REMOTEADMIN_SMTP_HOST"] = anterior_h

    comprobar("Sin servidor configurado se avisa con un error claro", lanzo)


def test_un_fallo_de_envio_no_deja_el_enlace_vivo():

    preparar()

    original = mailer.send_password_reset

    def falla(*args, **kwargs):
        raise mailer.MailError("servidor caido")

    mailer.send_password_reset = falla

    try:
        respuesta = pedir_enlace()
    finally:
        mailer.send_password_reset = original

    comprobar("La respuesta sigue siendo la generica",
              respuesta.status_code == 200
              and respuesta.json()["status"] == "ok")

    comprobar("El enlace que no llego a salir se anula",
              recovery.pending_tokens(users.get_user("ana")["id"]) == 0)


def test_las_credenciales_de_correo_no_estan_en_el_codigo():

    import io

    codigo = io.open(RAIZ / "backend" / "mailer.py",
                     encoding="utf-8").read()

    sospechosas = [
        linea for linea in codigo.splitlines()
        if ("password" in linea.lower() or "smtp_user" in linea.lower())
        and "=" in linea
        and "_entorno(" not in linea
        and not linea.strip().startswith("#")
        and "REMOTEADMIN_SMTP" not in linea
    ]

    comprobar("Ninguna credencial de correo esta escrita en el modulo",
              not sospechosas, str(sospechosas))


# ==============================
# 7. Emergencia local
# ==============================

def test_reset_password_sigue_existiendo():

    comprobar("El script de emergencia local sigue ahi",
              (RAIZ / "reset_password.py").exists())

    import io

    codigo = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()

    comprobar("Y el backend sigue sin importarlo",
              "import reset_password" not in codigo)


def test_el_informe_no_expone_secretos():

    texto = " ".join(f"{n} {d}" for n, _, d in resultados)

    comprobar("Ninguna comprobacion expone contrasenas, tokens ni hashes",
              h.CLAVE_OWNER not in texto
              and h.CLAVE_SUBADMIN not in texto
              and NUEVA not in texto
              and "pbkdf2_sha256" not in texto)


# ==============================

def main():

    pruebas = [
        test_misma_respuesta_exista_o_no,
        test_un_correo_sin_cuenta_no_envia_nada,
        test_un_usuario_desactivado_no_recibe_enlace,
        test_correo_vacio_o_ausente,
        test_se_envia_el_enlace,
        test_el_token_solo_se_guarda_hasheado,
        test_el_token_es_largo_y_aleatorio,
        test_el_token_no_aparece_en_la_auditoria,
        test_la_api_nunca_devuelve_el_token,
        test_reset_correcto,
        test_el_enlace_es_de_un_solo_uso,
        test_enlace_caducado,
        test_token_inventado,
        test_la_politica_se_aplica_sin_quemar_el_enlace,
        test_pedir_otro_enlace_anula_el_anterior_al_usarlo,
        test_comprobar_enlace_sin_gastarlo,
        test_el_reset_invalida_las_sesiones,
        test_el_reset_no_toca_las_sesiones_de_otros,
        test_rate_limit_de_peticiones,
        test_el_limite_de_recuperacion_es_independiente,
        test_el_transporte_sale_del_entorno,
        test_sin_servidor_configurado_falla_con_claridad,
        test_un_fallo_de_envio_no_deja_el_enlace_vivo,
        test_las_credenciales_de_correo_no_estan_en_el_codigo,
        test_reset_password_sigue_existiendo,
        test_el_informe_no_expone_secretos
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
