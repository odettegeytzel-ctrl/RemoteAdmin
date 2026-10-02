"""
Pruebas del bloque G4b: cambio de contrasena autenticado.

    .venv\\Scripts\\python tests/test_g4b_password_change.py

Todo ocurre sobre una base temporal con contrasenas inventadas. La
contrasena real de produccion no se lee ni se modifica en ningun momento.

La ultima comprobacion recorre el texto de todas las demas y falla si se
hubiera colado una contrasena, un hash, un token o una cookie.
"""

import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import g4_harness as h

import backend.auth as auth
import backend.database as database
import backend.settings as settings


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def cambiar(cliente, actual, nueva):
    return cliente.post(
        "/api/auth/password",
        json={"current_password": actual, "new_password": nueva}
    )


# ==============================
# 1. Cambio correcto
# ==============================

def test_cambio_correcto():

    h.reiniciar()

    respuesta = cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    comprobar("El cambio responde 200", respuesta.status_code == 200,
              str(respuesta.status_code))

    comprobar("La respuesta confirma el cambio",
              respuesta.json().get("status") == "ok")

    comprobar(
        "La respuesta NO devuelve la contrasena ni el hash",
        h.CLAVE_NUEVA not in respuesta.text
        and "pbkdf2_sha256$" not in respuesta.text
    )


def test_login_con_la_nueva_y_no_con_la_anterior():

    h.reiniciar()

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    comprobar("Se puede iniciar sesion con la contrasena nueva",
              auth.authenticate("odette", h.CLAVE_NUEVA) is not None)

    comprobar("La contrasena anterior deja de funcionar",
              auth.authenticate("odette", h.CLAVE_INICIAL) is None)


def test_se_guarda_hash_y_fecha():

    h.reiniciar()

    antes = h.fila_auth_state()

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    despues = h.fila_auth_state()

    comprobar("El hash almacenado cambia",
              despues["password_hash"] != antes["password_hash"])

    comprobar("Se guarda en formato PBKDF2-SHA256",
              despues["password_hash"].startswith("pbkdf2_sha256$"))

    comprobar("Se actualiza password_changed_at",
              bool(despues["password_changed_at"]))

    comprobar("Se actualiza sessions_valid_from",
              despues["sessions_valid_from"] > antes["sessions_valid_from"])

    comprobar(
        "La contrasena en claro no aparece en la base",
        h.CLAVE_NUEVA not in str(tuple(despues))
    )


# ==============================
# 2. Rechazos
# ==============================

def test_contrasena_actual_incorrecta():

    h.reiniciar()

    respuesta = cambiar(h.cliente(), "NoEsLaActual-G4!", h.CLAVE_NUEVA)

    comprobar("Una contrasena actual incorrecta da 403",
              respuesta.status_code == 403, str(respuesta.status_code))

    comprobar("No se cambia nada",
              auth.authenticate("odette", h.CLAVE_INICIAL) is not None)

    comprobar("La contrasena propuesta no queda activa",
              auth.authenticate("odette", h.CLAVE_NUEVA) is None)


def test_nueva_demasiado_corta():

    h.reiniciar()

    respuesta = cambiar(h.cliente(), h.CLAVE_INICIAL, "corta1!")

    comprobar("Una contrasena demasiado corta da 400",
              respuesta.status_code == 400, str(respuesta.status_code))

    comprobar("El mensaje explica la regla, no la contrasena",
              "caracteres" in respuesta.json().get("message", ""))

    comprobar("La contrasena sigue siendo la de antes",
              auth.authenticate("odette", h.CLAVE_INICIAL) is not None)


def test_nueva_demasiado_larga():

    h.reiniciar()

    larga = "A" * (auth.PASSWORD_MAX_LENGTH + 1)

    respuesta = cambiar(h.cliente(), h.CLAVE_INICIAL, larga)

    comprobar("Una contrasena desmesurada da 400",
              respuesta.status_code == 400, str(respuesta.status_code))

    comprobar("Justo en el limite si se acepta",
              cambiar(
                  h.cliente(), h.CLAVE_INICIAL,
                  "A" * auth.PASSWORD_MAX_LENGTH
              ).status_code == 200)


def test_nueva_igual_a_la_actual():

    h.reiniciar()

    respuesta = cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_INICIAL)

    comprobar("Repetir la misma contrasena da 400",
              respuesta.status_code == 400, str(respuesta.status_code))

    comprobar("El mensaje lo explica",
              "distinta" in respuesta.json().get("message", "").lower())


def test_contrasena_por_defecto_rechazada():

    h.reiniciar()

    respuesta = cambiar(h.cliente(), h.CLAVE_INICIAL, auth.DEFAULT_PASSWORD)

    comprobar("La contrasena por defecto se rechaza",
              respuesta.status_code == 400, str(respuesta.status_code))

    comprobar("La contrasena por defecto no queda activa",
              auth.authenticate("odette", auth.DEFAULT_PASSWORD) is None)


def test_cuerpo_vacio_o_raro():

    h.reiniciar()

    comprobar("Sin campos, no se cambia nada",
              h.cliente().post("/api/auth/password", json={}).status_code
              in (400, 403))

    comprobar("La contrasena sigue intacta",
              auth.authenticate("odette", h.CLAVE_INICIAL) is not None)


# ==============================
# 3. Sesion
# ==============================

def test_sin_sesion_no_se_puede():

    h.reiniciar()

    respuesta = cambiar(
        h.cliente(con_sesion=False), h.CLAVE_INICIAL, h.CLAVE_NUEVA
    )

    comprobar("Sin sesion responde 401",
              respuesta.status_code == 401, str(respuesta.status_code))

    comprobar("Y no cambia la contrasena",
              auth.authenticate("odette", h.CLAVE_INICIAL) is not None)


def test_la_sesion_propia_sobrevive():

    h.reiniciar()

    cliente = h.cliente()

    respuesta = cambiar(cliente, h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    comprobar("El servidor reemite la cookie de sesion",
              COOKIE_EN(respuesta))

    # El TestClient guarda la cookie nueva; la siguiente peticion la usa
    siguiente = cliente.get("/api/auth/me")

    comprobar("La sesion que hizo el cambio sigue funcionando",
              siguiente.status_code == 200, str(siguiente.status_code))

    comprobar("Y sigue identificando al mismo usuario",
              siguiente.json().get("username") == "odette")


def COOKIE_EN(respuesta):
    """True si la respuesta trae la cookie de sesion, sin leer su valor."""

    return any(
        h.COOKIE in valor
        for clave, valor in respuesta.headers.items()
        if clave.lower() == "set-cookie"
    )


def test_la_cookie_conserva_sus_atributos():

    h.reiniciar()

    respuesta = cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    cabecera = " ".join(
        valor
        for clave, valor in respuesta.headers.items()
        if clave.lower() == "set-cookie"
    ).lower()

    comprobar("La cookie reemitida es HttpOnly", "httponly" in cabecera)
    comprobar("La cookie reemitida es Secure", "secure" in cabecera)
    comprobar("La cookie reemitida es SameSite=Strict",
              "samesite=strict" in cabecera)


def test_las_demas_sesiones_mueren():

    h.reiniciar()

    # Dos sesiones abiertas a la vez, de dos navegadores distintos
    token_otra = auth.create_token("odette")
    otra = h.cliente(token=token_otra)

    comprobar("La otra sesion funciona antes del cambio",
              otra.get("/api/auth/me").status_code == 200)

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    comprobar("La otra sesion queda invalidada al instante",
              otra.get("/api/auth/me").status_code == 401)

    comprobar("Y el token ya no verifica",
              auth.verify_token(token_otra) is None)


# ==============================
# 4. Lo que ya existia
# ==============================

def test_logout_individual_sigue_funcionando():

    h.reiniciar()

    cliente = h.cliente()
    cambiar(cliente, h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    # Tras el cambio hay una sesion (la reemitida) y se abre otra
    segunda = h.cliente(token=auth.create_token(
        "odette", issued_at=h.fila_auth_state()["sessions_valid_from"] + 1
    ))

    comprobar("La segunda sesion arranca valida",
              segunda.get("/api/auth/me").status_code == 200)

    comprobar("El logout responde correctamente",
              segunda.post("/api/auth/logout").status_code == 200)

    comprobar("La sesion cerrada deja de valer",
              segunda.get("/api/auth/me").status_code == 401)

    comprobar("La sesion que cambio la contrasena sigue viva",
              cliente.get("/api/auth/me").status_code == 200)


def test_revoked_sessions_sigue_registrando():

    h.reiniciar()

    conexion = database.get_connection()
    conexion.execute("DELETE FROM revoked_sessions")
    conexion.commit()
    conexion.close()

    cliente = h.cliente()
    cliente.post("/api/auth/logout")

    conexion = database.get_connection()
    total = conexion.execute(
        "SELECT COUNT(*) FROM revoked_sessions"
    ).fetchone()[0]
    conexion.close()

    comprobar("El logout sigue dejando constancia en revoked_sessions",
              total == 1, str(total))


def test_iat_y_jti_siguen_presentes():

    import json

    token = auth.create_token("odette")

    campos = set(json.loads(auth._b64decode(token.split(".", 1)[0])))

    comprobar("El token sigue llevando iat y jti",
              {"iat", "jti", "sub", "exp"} == campos,
              str(sorted(campos)))


def test_settings_sigue_sin_poder_tocar_auth_state():

    h.reiniciar()

    hash_antes = h.fila_auth_state()["password_hash"]

    respuesta = h.cliente().post(
        "/api/settings",
        json={"password_hash": "x", "sessions_valid_from": "0"}
    )

    comprobar("/api/settings rechaza claves de autenticacion",
              respuesta.status_code == 400, str(respuesta.status_code))

    comprobar("auth_state no se ha movido",
              h.fila_auth_state()["password_hash"] == hash_antes)


# ==============================
# 5. Rate limiting propio
# ==============================

def test_rate_limit_independiente():

    h.reiniciar()

    import backend.ratelimit as ratelimit

    maximo = ratelimit.PASSWORD_MAX_FAILURES

    cliente = h.cliente()

    codigos = []

    for _ in range(maximo):
        codigos.append(
            cambiar(cliente, "MalMalMal-G4!", h.CLAVE_NUEVA).status_code
        )

    bloqueado = cambiar(cliente, h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    comprobar(f"Los primeros {maximo} fallos dan 403",
              set(codigos) == {403}, str(codigos))

    comprobar("Pasado el limite se responde 429",
              bloqueado.status_code == 429, str(bloqueado.status_code))

    comprobar("Se indica cuanto hay que esperar",
              "retry-after" in
              {k.lower() for k in bloqueado.headers})

    comprobar(
        "Estando bloqueado, ni con la contrasena correcta se cambia",
        auth.authenticate("odette", h.CLAVE_INICIAL) is not None
    )


def test_el_bloqueo_del_cambio_no_bloquea_el_login():

    h.reiniciar()

    import backend.ratelimit as ratelimit

    cliente = h.cliente()

    for _ in range(ratelimit.PASSWORD_MAX_FAILURES):
        cambiar(cliente, "MalMalMal-G4!", h.CLAVE_NUEVA)

    comprobar(
        "El contador del cambio esta agotado",
        ratelimit.seconds_until_unblocked("testclient", scope="password") > 0
    )

    comprobar(
        "Pero el login no esta bloqueado: son contadores distintos",
        ratelimit.seconds_until_unblocked("testclient", scope="login") == 0
    )


def test_un_cambio_correcto_limpia_el_contador():

    h.reiniciar()

    import backend.ratelimit as ratelimit

    cliente = h.cliente()

    cambiar(cliente, "MalMalMal-G4!", h.CLAVE_NUEVA)

    cambiar(cliente, h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    comprobar(
        "Tras un cambio correcto se olvida el fallo anterior",
        ratelimit.seconds_until_unblocked("testclient", scope="password") == 0
    )


# ==============================
# 6. Auditoria
# ==============================

def test_auditoria_de_exito():

    h.reiniciar()

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    registros = h.registros_auditoria(action="auth.password_change")

    comprobar("El cambio queda auditado como success",
              any(r["status"] == "success" for r in registros))

    comprobar("Se registra el usuario del contexto real",
              all(r["username"] == "odette" for r in registros))

    comprobar("Se registra la IP de la conexion",
              all(r["source_ip"] for r in registros))


def test_auditoria_de_error():

    h.reiniciar()

    cambiar(h.cliente(), "NoEsLaActual-G4!", h.CLAVE_NUEVA)
    cambiar(h.cliente(), h.CLAVE_INICIAL, "corta")

    registros = h.registros_auditoria(action="auth.password_change")

    comprobar("Los dos fallos quedan auditados como error",
              len([r for r in registros if r["status"] == "error"]) == 2,
              str(len(registros)))

    motivos = " ".join(r["details"] or "" for r in registros)

    comprobar("Se distingue la contrasena actual incorrecta",
              "actual incorrecta" in motivos)

    comprobar("Se distingue el rechazo por politica",
              "rechazada" in motivos)


def test_la_auditoria_no_guarda_secretos():

    h.reiniciar()

    cliente = h.cliente()

    cambiar(cliente, "NoEsLaActual-G4!", h.CLAVE_NUEVA)
    cambiar(cliente, h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    todo = " ".join(
        f"{r['details']} {r['username']} {r['source_ip']}"
        for r in h.registros_auditoria()
    )

    for secreto in (h.CLAVE_INICIAL, h.CLAVE_NUEVA, "NoEsLaActual-G4!",
                    "pbkdf2_sha256$", h.COOKIE):

        comprobar(
            f"La auditoria no guarda {secreto[:18]}...",
            secreto not in todo
        )


def test_no_quedan_secretos_en_la_base():

    h.reiniciar()

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    conexion = database.get_connection()

    volcado = []

    for (tabla,) in conexion.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall():

        for fila in conexion.execute(f"SELECT * FROM {tabla}"):
            volcado.append(" ".join(str(v) for v in tuple(fila)))

    conexion.close()

    texto = " ".join(volcado)

    comprobar("La contrasena anterior no esta en ninguna tabla",
              h.CLAVE_INICIAL not in texto)

    comprobar("La contrasena nueva no esta en ninguna tabla",
              h.CLAVE_NUEVA not in texto)


# ==============================
# 7. Nada sensible en el informe
# ==============================

def test_el_informe_no_expone_secretos():

    texto = " ".join(
        f"{nombre} {detalle}" for nombre, _, detalle in resultados
    )

    prohibidos = [h.CLAVE_INICIAL, h.CLAVE_NUEVA,
                  h.fila_auth_state()["password_hash"]]

    comprobar(
        "Ninguna comprobacion expone contrasenas, hashes ni tokens",
        not [p for p in prohibidos if p and p in texto]
    )


# ==============================

def main():

    pruebas = [
        test_cambio_correcto,
        test_login_con_la_nueva_y_no_con_la_anterior,
        test_se_guarda_hash_y_fecha,
        test_contrasena_actual_incorrecta,
        test_nueva_demasiado_corta,
        test_nueva_demasiado_larga,
        test_nueva_igual_a_la_actual,
        test_contrasena_por_defecto_rechazada,
        test_cuerpo_vacio_o_raro,
        test_sin_sesion_no_se_puede,
        test_la_sesion_propia_sobrevive,
        test_la_cookie_conserva_sus_atributos,
        test_las_demas_sesiones_mueren,
        test_logout_individual_sigue_funcionando,
        test_revoked_sessions_sigue_registrando,
        test_iat_y_jti_siguen_presentes,
        test_settings_sigue_sin_poder_tocar_auth_state,
        test_rate_limit_independiente,
        test_el_bloqueo_del_cambio_no_bloquea_el_login,
        test_un_cambio_correcto_limpia_el_contador,
        test_auditoria_de_exito,
        test_auditoria_de_error,
        test_la_auditoria_no_guarda_secretos,
        test_no_quedan_secretos_en_la_base,
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
