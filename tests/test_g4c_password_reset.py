"""
Pruebas del bloque G4c: restablecimiento desde la consola del servidor.

    .venv\\Scripts\\python tests/test_g4c_password_reset.py

Se prueba reset_password(), la funcion que hace el trabajo, separada a
proposito de la parte que teclea el operador. Todo sobre una base temporal
con contrasenas inventadas: reset_password.py NUNCA se ejecuta contra
produccion en estas pruebas.
"""

import io
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import g4_harness as h

import backend.auth as auth
import backend.database as database

import reset_password


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==============================
# 1. Reset correcto
# ==============================

def test_reset_correcto():

    h.reiniciar()

    ok, mensaje = reset_password.reset_password(h.CLAVE_NUEVA)

    comprobar("El reset se completa", ok is True, mensaje)

    comprobar("El mensaje no contiene la contrasena ni el hash",
              h.CLAVE_NUEVA not in mensaje
              and "pbkdf2_sha256$" not in mensaje)


def test_la_nueva_permite_entrar_y_la_anterior_no():

    h.reiniciar()

    reset_password.reset_password(h.CLAVE_NUEVA)

    comprobar("Se inicia sesion con la contrasena restablecida",
              auth.authenticate("odette", h.CLAVE_NUEVA) is not None)

    comprobar("La contrasena anterior deja de funcionar",
              auth.authenticate("odette", h.CLAVE_INICIAL) is None)


def test_no_hace_falta_reiniciar():
    """
    El proceso que atiende las peticiones es el mismo de antes del reset:
    si la contrasena nueva funciona sin volver a importar nada, es que se
    lee de la base en cada intento.
    """

    h.reiniciar()

    cliente = h.cliente()

    comprobar("El servidor esta atendiendo antes del reset",
              cliente.get("/api/auth/me").status_code == 200)

    reset_password.reset_password(h.CLAVE_NUEVA)

    comprobar(
        "El login con la nueva funciona en el mismo proceso, sin reiniciar",
        h.cliente(con_sesion=False).post(
            "/api/auth/login",
            json={"username": "odette", "password": h.CLAVE_NUEVA}
        ).status_code == 200
    )

    comprobar(
        "Y el login con la anterior ya no",
        h.cliente(con_sesion=False).post(
            "/api/auth/login",
            json={"username": "odette", "password": h.CLAVE_INICIAL}
        ).status_code == 401
    )


def test_se_guardan_hash_y_fecha():

    h.reiniciar()

    antes = h.fila_auth_state()

    reset_password.reset_password(h.CLAVE_NUEVA)

    despues = h.fila_auth_state()

    comprobar("El hash cambia",
              despues["password_hash"] != antes["password_hash"])

    comprobar("Se mantiene el formato PBKDF2-SHA256",
              despues["password_hash"].startswith("pbkdf2_sha256$"))

    comprobar("Se actualiza password_changed_at",
              bool(despues["password_changed_at"]))

    comprobar("Se actualiza sessions_valid_from",
              despues["sessions_valid_from"] > antes["sessions_valid_from"])


# ==============================
# 2. Sesiones
# ==============================

def test_todas_las_sesiones_quedan_invalidadas():

    h.reiniciar()

    una = h.cliente()
    otra = h.cliente()

    comprobar("Las dos sesiones funcionan antes del reset",
              una.get("/api/auth/me").status_code == 200
              and otra.get("/api/auth/me").status_code == 200)

    reset_password.reset_password(h.CLAVE_NUEVA)

    comprobar("Ninguna sesion sobrevive al reset",
              una.get("/api/auth/me").status_code == 401
              and otra.get("/api/auth/me").status_code == 401)


def test_el_reset_no_deja_sesion_abierta():
    """A diferencia del cambio de G4b, aqui no hay sesion que conservar."""

    h.reiniciar()

    # Emitido ANTES del reset: es el caso que debe morir
    anterior = auth.create_token("odette")

    reset_password.reset_password(h.CLAVE_NUEVA)

    comprobar(
        "Un token emitido antes del reset deja de valer",
        auth.verify_token(anterior) is None
    )

    comprobar(
        "Una sesion iniciada despues del reset si vale",
        auth.verify_token(auth.create_token("odette")) == "odette"
    )


# ==============================
# 3. Politica
# ==============================

def test_politica_demasiado_corta():

    h.reiniciar()

    ok, mensaje = reset_password.reset_password("corta1!")

    comprobar("Una contrasena corta se rechaza", ok is False)
    comprobar("Se explica la regla", "caracteres" in mensaje)
    comprobar("No se ha cambiado nada",
              auth.authenticate("odette", h.CLAVE_INICIAL) is not None)


def test_politica_demasiado_larga():

    h.reiniciar()

    ok, _ = reset_password.reset_password(
        "A" * (auth.PASSWORD_MAX_LENGTH + 1)
    )

    comprobar("Una contrasena desmesurada se rechaza", ok is False)


def test_politica_igual_a_la_actual():

    h.reiniciar()

    ok, mensaje = reset_password.reset_password(h.CLAVE_INICIAL)

    comprobar("Repetir la contrasena vigente se rechaza", ok is False)
    comprobar("Se explica el motivo", "distinta" in mensaje.lower())


def test_politica_contrasena_por_defecto():

    h.reiniciar()

    ok, _ = reset_password.reset_password(auth.DEFAULT_PASSWORD)

    comprobar("La contrasena por defecto se rechaza", ok is False)

    comprobar("Y no queda activa",
              auth.authenticate("odette", auth.DEFAULT_PASSWORD) is None)


def test_politica_vacia():

    h.reiniciar()

    ok, _ = reset_password.reset_password("")

    comprobar("Una contrasena vacia se rechaza", ok is False)


# ==============================
# 4. Auditoria
# ==============================

def test_auditoria_del_reset():

    h.reiniciar()

    reset_password.reset_password(h.CLAVE_NUEVA)

    registros = h.registros_auditoria(action="auth.password_reset")

    comprobar("El reset queda auditado", len(registros) == 1,
              str(len(registros)))

    if registros:

        fila = registros[0]

        comprobar("Se registra como success", fila["status"] == "success")

        comprobar("El usuario es 'consola'", fila["username"] == "consola")

        comprobar("No se inventa una IP", fila["source_ip"] is None,
                  str(fila["source_ip"]))

        comprobar("Los detalles no contienen secretos",
                  h.CLAVE_NUEVA not in (fila["details"] or "")
                  and "pbkdf2_sha256$" not in (fila["details"] or ""))


def test_auditoria_del_rechazo():

    h.reiniciar()

    reset_password.reset_password("corta")

    registros = h.registros_auditoria(action="auth.password_reset")

    comprobar("Un reset rechazado tambien queda auditado",
              any(r["status"] == "error" for r in registros))

    comprobar("Con el motivo, no la contrasena",
              all("corta" != (r["details"] or "") for r in registros))


# ==============================
# 5. Fallo de persistencia
# ==============================

def test_fallo_al_guardar():
    """Si la base no se puede escribir, no puede parecer que fue bien."""

    h.reiniciar()

    original = database.DATABASE_PATH
    database.DATABASE_PATH = Path("Z:/ruta/que/no/existe/x.db")

    try:
        ok, mensaje = reset_password.reset_password(h.CLAVE_NUEVA)
        lanzo = False

    except Exception:
        ok, mensaje, lanzo = None, "", True

    finally:
        database.DATABASE_PATH = original

    comprobar("No se propaga la excepcion al operador", not lanzo)

    comprobar("Se informa del fallo", ok is False)

    comprobar("El mensaje explica que no se pudo guardar",
              "no se pudo guardar" in (mensaje or "").lower(), mensaje)

    comprobar("La contrasena anterior sigue siendo valida",
              auth.authenticate("odette", h.CLAVE_INICIAL) is not None)

    comprobar("La nueva no quedo a medias",
              auth.authenticate("odette", h.CLAVE_NUEVA) is None)


# ==============================
# 6. No existe reset por HTTP
# ==============================

def test_no_hay_endpoint_de_reset():

    codigo = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()

    comprobar(
        "No hay ninguna ruta de reset en el backend",
        "password/reset" not in codigo
        and "auth/reset" not in codigo
        and "forgot" not in codigo.lower()
    )

    comprobar(
        "El backend no importa reset_password",
        "reset_password" not in codigo
    )

    cliente = h.cliente(con_sesion=False)

    for ruta in ("/api/auth/reset", "/api/auth/password/reset",
                 "/api/auth/forgot"):

        comprobar(
            f"{ruta} no existe",
            cliente.post(ruta, json={}).status_code in (401, 404, 405)
        )


def test_el_script_no_imprime_secretos():

    codigo = io.open(RAIZ / "reset_password.py", encoding="utf-8").read()

    impresiones = [
        linea.strip()
        for linea in codigo.splitlines()
        if linea.strip().startswith("print(")
    ]

    sospechosas = [
        linea for linea in impresiones
        if "hash" in linea.lower() or "token" in linea.lower()
        or "nueva)" in linea or "contrasena)" in linea
    ]

    comprobar("Ninguna linea del script imprime hashes ni tokens",
              not sospechosas, str(sospechosas))

    comprobar(
        "El script avisa de que da control sobre la cuenta",
        "ADVERTENCIA DE SEGURIDAD" in codigo
        and "ACCESO AL SERVIDOR ES EL MECANISMO DE RECUPERACION" in codigo
    )


def test_auth_py_ya_no_imprime_hashes():
    """
    backend/auth.py tenia un modo consola que imprimia el hash por pantalla
    para pegarlo a mano en el .env. Con reset_password.py esa via sobra, y
    un hash en el historial de la terminal es un hash de mas.
    """

    codigo = io.open(RAIZ / "backend" / "auth.py", encoding="utf-8").read()

    comprobar("backend/auth.py ya no tiene modo consola",
              '__main__' not in codigo)

    comprobar("Y no imprime nada en absoluto",
              'print(' not in codigo.replace('print(secrets.token_hex', ''))

    entorno = io.open(RAIZ / ".env.example", encoding="utf-8").read()

    comprobar("El .env.example ya no manda ejecutar backend.auth",
              'backend.auth' not in entorno and 'backend/auth.py' not in entorno)

    comprobar("Y apunta al script de consola",
              'reset_password.py' in entorno)


def test_el_informe_no_expone_secretos():

    texto = " ".join(
        f"{nombre} {detalle}" for nombre, _, detalle in resultados
    )

    prohibidos = [h.CLAVE_INICIAL, h.CLAVE_NUEVA,
                  h.fila_auth_state()["password_hash"]]

    comprobar("Ninguna comprobacion expone contrasenas, hashes ni tokens",
              not [p for p in prohibidos if p and p in texto])


# ==============================

def main():

    pruebas = [
        test_reset_correcto,
        test_la_nueva_permite_entrar_y_la_anterior_no,
        test_no_hace_falta_reiniciar,
        test_se_guardan_hash_y_fecha,
        test_todas_las_sesiones_quedan_invalidadas,
        test_el_reset_no_deja_sesion_abierta,
        test_politica_demasiado_corta,
        test_politica_demasiado_larga,
        test_politica_igual_a_la_actual,
        test_politica_contrasena_por_defecto,
        test_politica_vacia,
        test_auditoria_del_reset,
        test_auditoria_del_rechazo,
        test_fallo_al_guardar,
        test_no_hay_endpoint_de_reset,
        test_el_script_no_imprime_secretos,
        test_auth_py_ya_no_imprime_hashes,
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
