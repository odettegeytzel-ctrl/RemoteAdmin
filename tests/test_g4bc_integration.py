"""
Integracion G4b + G4c: el panel y la consola sobre la misma credencial.

    .venv\\Scripts\\python tests/test_g4bc_integration.py

Recorre la secuencia completa que haria una persona: cambia la contrasena
desde el panel, entra con la nueva, la olvida, la restablece desde la
consola y vuelve a entrar. Todo en el mismo proceso, sin reiniciar nada.
"""

import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import g4_harness as h

import backend.auth as auth
import backend.main as main

import reset_password


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def login(clave):
    return h.cliente(con_sesion=False).post(
        "/api/auth/login",
        json={"username": "odette", "password": clave}
    )


def cambiar(cliente, actual, nueva):
    return cliente.post(
        "/api/auth/password",
        json={"current_password": actual, "new_password": nueva}
    )


# ==============================
# Secuencia completa
# ==============================

def test_secuencia_panel_y_consola():

    h.reiniciar()

    # 1. Se entra con la contrasena inicial
    comprobar("Se entra con la contrasena inicial",
              login(h.CLAVE_INICIAL).status_code == 200)

    # 2. Se cambia desde el panel
    panel = h.cliente()

    comprobar("El cambio desde el panel funciona",
              cambiar(panel, h.CLAVE_INICIAL, h.CLAVE_NUEVA).status_code
              == 200)

    comprobar("La sesion que hizo el cambio sigue dentro",
              panel.get("/api/auth/me").status_code == 200)

    comprobar("Se entra con la contrasena nueva",
              login(h.CLAVE_NUEVA).status_code == 200)

    comprobar("La inicial ya no sirve",
              login(h.CLAVE_INICIAL).status_code == 401)

    # 3. Se olvida la nueva y se restablece desde la consola
    ok, _ = reset_password.reset_password(h.CLAVE_TERCERA)

    comprobar("El reset desde consola se completa", ok is True)

    comprobar("Se entra con la contrasena restablecida",
              login(h.CLAVE_TERCERA).status_code == 200)

    comprobar("La contrasena anterior deja de funcionar",
              login(h.CLAVE_NUEVA).status_code == 401)

    comprobar("Y la inicial tampoco vuelve",
              login(h.CLAVE_INICIAL).status_code == 401)


def test_cada_operacion_cierra_las_sesiones():

    h.reiniciar()

    # Dos sesiones abiertas
    una = h.cliente()
    otra = h.cliente()

    panel = h.cliente()

    cambiar(panel, h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    comprobar("Tras el cambio, las otras sesiones estan cerradas",
              una.get("/api/auth/me").status_code == 401
              and otra.get("/api/auth/me").status_code == 401)

    comprobar("Solo sobrevive la que hizo el cambio",
              panel.get("/api/auth/me").status_code == 200)

    reset_password.reset_password(h.CLAVE_TERCERA)

    comprobar("Tras el reset no sobrevive ninguna, ni siquiera esa",
              panel.get("/api/auth/me").status_code == 401)


def test_el_corte_avanza_en_cada_operacion():

    h.reiniciar()

    inicial = h.fila_auth_state()["sessions_valid_from"]

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    tras_cambio = h.fila_auth_state()["sessions_valid_from"]

    reset_password.reset_password(h.CLAVE_TERCERA)

    tras_reset = h.fila_auth_state()["sessions_valid_from"]

    comprobar("El cambio adelanta la fecha de corte",
              tras_cambio > inicial)

    comprobar("El reset la adelanta de nuevo, aunque sea el mismo segundo",
              tras_reset > tras_cambio,
              f"{tras_cambio} -> {tras_reset}")


def test_no_hace_falta_reiniciar_en_ningun_caso():
    """
    Las dos operaciones ocurren en el mismo proceso que atiende las
    peticiones, y surten efecto de inmediato. Si hubiera que reiniciar,
    los logins de arriba fallarian.
    """

    h.reiniciar()

    modulo_antes = id(main.app)

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)
    reset_password.reset_password(h.CLAVE_TERCERA)

    comprobar("La aplicacion es la misma instancia todo el rato",
              id(main.app) == modulo_antes)

    comprobar("Y responde con la ultima contrasena",
              login(h.CLAVE_TERCERA).status_code == 200)


def test_los_agents_no_se_ven_afectados():
    """
    Los Agents se autentican con su token individual, no con la sesion del
    panel. Un cambio de contrasena no debe tocarlos.
    """

    h.reiniciar()

    import backend.database as database

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR IGNORE INTO devices "
        "(device_id, hostname, agent_token_hash, agent_token_active) "
        "VALUES ('equipo-g4bc', 'PRUEBA', 'hash-de-prueba', 1)"
    )
    conexion.commit()
    conexion.close()

    # Un Agent "conectado"
    class WebSocketFalso:
        async def send_text(self, texto):
            return None

    main.connected_agents["equipo-g4bc"] = WebSocketFalso()

    antes = dict(main.connected_agents)

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)
    reset_password.reset_password(h.CLAVE_TERCERA)

    conexion = database.get_connection()
    fila = conexion.execute(
        "SELECT agent_token_hash, agent_token_active FROM devices "
        "WHERE device_id = 'equipo-g4bc'"
    ).fetchone()
    conexion.close()

    comprobar("El Agent sigue conectado tras ambas operaciones",
              main.connected_agents.keys() == antes.keys())

    comprobar("Su token individual no se ha tocado",
              fila["agent_token_hash"] == "hash-de-prueba"
              and fila["agent_token_active"] == 1)

    main.connected_agents.pop("equipo-g4bc", None)


def test_autenticacion_y_logout_siguen_funcionando():

    h.reiniciar()

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)

    respuesta = login(h.CLAVE_NUEVA)

    comprobar("El login responde 200", respuesta.status_code == 200)

    cliente = h.cliente(con_sesion=False)
    cliente.post("/api/auth/login",
                 json={"username": "odette", "password": h.CLAVE_NUEVA})

    comprobar("La sesion recien abierta vale",
              cliente.get("/api/auth/me").status_code == 200)

    comprobar("El logout responde 200",
              cliente.post("/api/auth/logout").status_code == 200)

    comprobar("Y despues la sesion ya no vale",
              cliente.get("/api/auth/me").status_code == 401)


def test_auditoria_de_la_secuencia():

    h.reiniciar()

    cambiar(h.cliente(), h.CLAVE_INICIAL, h.CLAVE_NUEVA)
    reset_password.reset_password(h.CLAVE_TERCERA)

    acciones = [r["action"] for r in h.registros_auditoria()]

    comprobar("Queda registrado el cambio desde el panel",
              "auth.password_change" in acciones)

    comprobar("Queda registrado el reset desde consola",
              "auth.password_reset" in acciones)

    por_usuario = {
        r["action"]: r["username"]
        for r in h.registros_auditoria()
        if r["action"].startswith("auth.password")
    }

    comprobar("El cambio se atribuye al usuario del panel",
              por_usuario.get("auth.password_change") == "odette")

    comprobar("El reset se atribuye a la consola",
              por_usuario.get("auth.password_reset") == "consola")

    todo = " ".join(
        f"{r['details']}" for r in h.registros_auditoria()
    )

    for secreto in (h.CLAVE_INICIAL, h.CLAVE_NUEVA, h.CLAVE_TERCERA,
                    "pbkdf2_sha256$"):

        comprobar(f"La auditoria no guarda {secreto[:16]}...",
                  secreto not in todo)


def test_el_informe_no_expone_secretos():

    texto = " ".join(
        f"{nombre} {detalle}" for nombre, _, detalle in resultados
    )

    prohibidos = [h.CLAVE_INICIAL, h.CLAVE_NUEVA, h.CLAVE_TERCERA,
                  h.fila_auth_state()["password_hash"]]

    comprobar("Ninguna comprobacion expone contrasenas, hashes ni tokens",
              not [p for p in prohibidos if p and p in texto])


# ==============================

def main_():

    pruebas = [
        test_secuencia_panel_y_consola,
        test_cada_operacion_cierra_las_sesiones,
        test_el_corte_avanza_en_cada_operacion,
        test_no_hace_falta_reiniciar_en_ningun_caso,
        test_los_agents_no_se_ven_afectados,
        test_autenticacion_y_logout_siguen_funcionando,
        test_auditoria_de_la_secuencia,
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
    sys.exit(main_())
