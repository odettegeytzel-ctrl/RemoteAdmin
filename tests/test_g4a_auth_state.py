"""
Pruebas del bloque G4a: almacen de la contrasena e invalidacion global.

    .venv\\Scripts\\python tests/test_g4a_auth_state.py

Nunca se toca la base real ni la contrasena real: todo ocurre sobre una base
de datos temporal con una contrasena inventada para la prueba.

Ninguna comprobacion imprime hashes, contrasenas, cookies ni tokens. Cuando
hace falta enseñar algo de un token, se muestra solo el nombre de sus campos.
"""

import os
import sys
import tempfile
import time

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Antes de importar backend.auth: el modulo lee la configuracion al cargarse.
os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-pruebas-g4a")
os.environ.setdefault("AUTH_USERNAME", "odette")

import backend.database as database

BASE_TEMPORAL = Path(tempfile.mkdtemp(prefix="g4a_")) / "prueba.db"
database.DATABASE_PATH = BASE_TEMPORAL
database.init_db()

import backend.auth as auth
import backend.settings as settings


# Contrasena inventada SOLO para estas pruebas. No es la real de nadie.
CLAVE_SEMILLA = "SemillaDePrueba-G4a!"
CLAVE_NUEVA = "OtraDistinta-G4a!"


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def vaciar_auth_state():

    # Tambien los usuarios: desde el bloque de usuarios, la cuenta unica se
    # migra a una fila owner. Si esa fila sobreviviera, la siembra desde el
    # .env ya no volveria a reproducirse y la prueba no probaria nada.
    conexion = database.get_connection()
    conexion.execute("DELETE FROM auth_state")
    conexion.execute("DELETE FROM user_permissions")
    conexion.execute("DELETE FROM users")
    conexion.commit()
    conexion.close()


def escribir_clave_del_owner(clave):
    """
    Cambia la contrasena en la fuente de verdad actual: la fila del owner.

    Antes se escribia en auth_state. Desde el bloque de usuarios, auth_state
    conserva solo el corte global de sesiones y el hash historico; quien
    manda en el login es la fila del usuario.
    """

    from backend import users

    users.ensure_owner_migrated()
    users.set_user_password("odette", clave, invalidate_sessions=False)


def fila_auth_state():

    conexion = database.get_connection()

    fila = conexion.execute(
        "SELECT * FROM auth_state WHERE id = 1"
    ).fetchone()

    conexion.close()

    return fila


def poner_semilla(clave):
    """Cambia la semilla del .env simulada, sin tocar ningun archivo."""

    auth.AUTH_PASSWORD_HASH = auth.generate_password_hash(clave)


# ==============================
# 1. Esquema y migracion
# ==============================

def test_tabla_creada():

    conexion = database.get_connection()

    columnas = {
        fila["name"]
        for fila in conexion.execute("PRAGMA table_info(auth_state)")
    }

    conexion.close()

    comprobar(
        "auth_state tiene las columnas previstas",
        columnas == {"id", "password_hash", "password_changed_at",
                     "sessions_valid_from"},
        f"encontradas: {sorted(columnas)}"
    )


def test_una_sola_fila_logica():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    conexion = database.get_connection()

    try:
        conexion.execute(
            "INSERT INTO auth_state (id, password_hash, sessions_valid_from) "
            "VALUES (2, 'x', 0)"
        )
        conexion.commit()
        acepto_segunda = True

    except Exception:
        acepto_segunda = False

    finally:
        conexion.close()

    comprobar("La tabla no admite una segunda fila", not acepto_segunda)


def test_migracion_aditiva():
    """Reinicializar no borra ni la credencial ni el resto de datos."""

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    conexion = database.get_connection()
    conexion.execute(
        "INSERT OR IGNORE INTO devices (device_id, hostname) "
        "VALUES ('equipo-g4a', 'PRUEBA')"
    )
    conexion.commit()
    conexion.close()

    hash_antes = fila_auth_state()["password_hash"]

    database.init_db()

    fila = fila_auth_state()

    conexion = database.get_connection()
    equipos = conexion.execute(
        "SELECT COUNT(*) FROM devices WHERE device_id = 'equipo-g4a'"
    ).fetchone()[0]
    conexion.close()

    comprobar("init_db() repetido conserva la credencial",
              fila is not None and fila["password_hash"] == hash_antes)

    comprobar("init_db() repetido no toca el resto de tablas", equipos == 1)


# ==============================
# 2. Semilla desde .env
# ==============================

def test_semilla_cuando_la_tabla_esta_vacia():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)

    comprobar("Con la tabla vacia no hay fila todavia",
              fila_auth_state() is None)

    estado = auth.get_auth_state()

    comprobar("La primera consulta siembra la fila",
              estado is not None and fila_auth_state() is not None)

    comprobar(
        "La semilla copiada sirve para validar la contrasena",
        auth.verify_password(CLAVE_SEMILLA, estado["password_hash"])
    )

    comprobar("La fecha de corte arranca en 0",
              estado["sessions_valid_from"] == 0)

    comprobar("Sin cambios, password_changed_at queda vacio",
              estado["password_changed_at"] is None)


def test_la_tabla_manda_sobre_el_env():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    # Alguien edita el .env despues de la siembra
    poner_semilla("ClaveDelEnvQueNoDebeUsarse-G4a!")

    comprobar(
        "Tras sembrar, el .env deja de mandar",
        auth.verify_password(CLAVE_SEMILLA, auth.get_stored_password_hash())
    )

    comprobar(
        "La contrasena del .env editado NO vale",
        not auth.verify_password(
            "ClaveDelEnvQueNoDebeUsarse-G4a!",
            auth.get_stored_password_hash()
        )
    )


def test_no_siembra_dos_veces():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    # Se cambia la contrasena en el almacen, como hara G4b
    escribir_clave_del_owner(CLAVE_NUEVA)

    auth.get_auth_state()

    comprobar(
        "Una consulta posterior no vuelve a copiar la semilla",
        auth.verify_password(CLAVE_NUEVA, auth.get_stored_password_hash())
    )


def test_sin_credencial_por_ninguna_via():

    vaciar_auth_state()
    auth.AUTH_PASSWORD_HASH = ""

    comprobar("Sin semilla y sin fila no se siembra nada",
              auth.get_auth_state() is None)

    comprobar("Sin credencial, el login niega el acceso",
              auth.authenticate("odette", "loquesea") is None)

    poner_semilla(CLAVE_SEMILLA)


# ==============================
# 3. Login contra el almacen
# ==============================

def test_login_usa_el_hash_almacenado():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    comprobar("Se inicia sesion con la contrasena sembrada",
              auth.authenticate("odette", CLAVE_SEMILLA) is not None)

    comprobar("Una contrasena equivocada no entra",
              auth.authenticate("odette", CLAVE_NUEVA) is None)

    comprobar("Un usuario equivocado no entra",
              auth.authenticate("otro", CLAVE_SEMILLA) is None)


def test_cambio_en_caliente_sin_reiniciar():
    """El objetivo del bloque: cambiar la contrasena sin reiniciar."""

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    escribir_clave_del_owner(CLAVE_NUEVA)

    comprobar("La contrasena nueva vale de inmediato",
              auth.authenticate("odette", CLAVE_NUEVA) is not None)

    comprobar("La anterior deja de valer en el acto",
              auth.authenticate("odette", CLAVE_SEMILLA) is None)


def test_arranque_detecta_la_contrasena_por_defecto():

    vaciar_auth_state()

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO auth_state (id, password_hash, sessions_valid_from) "
        "VALUES (1, ?, 0)",
        (auth.generate_password_hash(auth.DEFAULT_PASSWORD),)
    )
    conexion.commit()
    conexion.close()

    # El .env tiene una buena, pero la vigente es la de por defecto
    poner_semilla(CLAVE_SEMILLA)

    # La cuenta unica se migra al owner arrastrando esa contrasena mala
    from backend import users
    users.ensure_owner_migrated()

    try:
        auth.require_security_config()
        fallo = False
    except RuntimeError:
        fallo = True

    comprobar(
        "El arranque se bloquea si la contrasena VIGENTE es la de por defecto",
        fallo
    )

    vaciar_auth_state()
    auth.get_auth_state()


# ==============================
# 4. Tokens: iat y corte global
# ==============================

def campos_del_token(token):
    """Devuelve solo los NOMBRES de los campos, nunca sus valores."""

    import json

    payload_b64 = token.split(".", 1)[0]

    return set(json.loads(auth._b64decode(payload_b64)))


def token_con_iat(momento, username="odette"):
    """Construye un token firmado con un iat concreto, para las pruebas."""

    import hashlib
    import hmac
    import json

    payload = {"sub": username, "exp": int(time.time()) + 3600}

    if momento is not None:
        payload["iat"] = int(momento)

    payload_b64 = auth._b64encode(json.dumps(payload).encode("utf-8"))

    firma = hmac.new(
        auth.SECRET_KEY.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256
    ).digest()

    return f"{payload_b64}.{auth._b64encode(firma)}"


def preparar_corte(corte):

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()
    auth.invalidate_all_sessions(corte)


def test_token_nuevo_lleva_iat():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    token = auth.create_token("odette")

    comprobar("El token nuevo incluye iat",
              "iat" in campos_del_token(token),
              str(sorted(campos_del_token(token))))

    comprobar("Sigue llevando sub y exp",
              {"sub", "exp"} <= campos_del_token(token))

    comprobar("Un token recien emitido es valido",
              auth.verify_token(token) == "odette")


def test_token_anterior_al_corte_es_rechazado():

    ahora = int(time.time())

    preparar_corte(ahora)

    antiguo = token_con_iat(ahora - 3600)

    comprobar("Un token emitido antes del corte se rechaza",
              auth.verify_token(antiguo) is None)


def test_token_sin_iat_es_rechazado_tras_el_corte():

    ahora = int(time.time())

    preparar_corte(ahora)

    comprobar(
        "Un token antiguo sin iat cae con la invalidacion global",
        auth.verify_token(token_con_iat(None)) is None
    )


def test_token_sin_iat_vale_mientras_no_haya_corte():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    comprobar(
        "Sin ningun corte, las sesiones anteriores a G4a siguen vivas",
        auth.verify_token(token_con_iat(None)) == "odette"
    )


def test_token_posterior_al_corte_sigue_valido():

    ahora = int(time.time())

    preparar_corte(ahora)

    comprobar("Un token emitido despues del corte sigue valiendo",
              auth.verify_token(token_con_iat(ahora + 5)) == "odette")


def test_invalidacion_global_solo_toca_una_columna():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    antes = fila_auth_state()

    vivo = auth.create_token("odette")

    comprobar("Antes del corte, la sesion esta viva",
              auth.verify_token(vivo) == "odette")

    corte = auth.invalidate_all_sessions(int(time.time()) + 1)

    despues = fila_auth_state()

    comprobar("Cerrar todas las sesiones mata la sesion abierta",
              auth.verify_token(vivo) is None)

    comprobar("Solo cambia sessions_valid_from",
              despues["password_hash"] == antes["password_hash"]
              and despues["password_changed_at"]
              == antes["password_changed_at"]
              and despues["sessions_valid_from"] == corte)

    # El corte alcanza a todo lo emitido ANTES. Un token creado despues de
    # la llamada nace ya por encima de la fecha de corte: emitirlo exige
    # haberse autenticado, asi que es una sesion nueva y legitima.
    comprobar("Una sesion abierta despues del corte si funciona",
              auth.verify_token(auth.create_token("odette")) == "odette")

    comprobar("Y tambien una con iat muy posterior",
              auth.verify_token(token_con_iat(corte + 5)) == "odette")


# ==============================
# 5. Lo que ya existia sigue funcionando
# ==============================

def test_logout_individual_sigue_funcionando():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    uno = auth.create_token("odette")
    otro = auth.create_token("odette")

    comprobar("Las dos sesiones empiezan validas",
              auth.verify_token(uno) == "odette"
              and auth.verify_token(otro) == "odette")

    comprobar("revoke_session_token() acepta el token",
              auth.revoke_session_token(uno) is True)

    comprobar("La sesion cerrada deja de valer",
              auth.verify_token(uno) is None)

    comprobar("La OTRA sesion sigue viva: el logout es individual",
              auth.verify_token(otro) == "odette")


def test_cada_sesion_tiene_su_propio_token():
    """
    Dos inicios de sesion seguidos no pueden producir el mismo token.

    Antes de G4a el payload era solo {sub, exp}: dos logins dentro del mismo
    segundo daban tokens identicos, asi que cerrar sesion en un sitio
    cerraba tambien la del otro. Lo arregla el campo jti.
    """

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    tokens = {auth.create_token("odette") for _ in range(20)}

    comprobar("Veinte sesiones seguidas dan veinte tokens distintos",
              len(tokens) == 20, f"distintos: {len(tokens)}")


def test_revocacion_registrada_en_la_tabla():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    token = auth.create_token("odette")
    auth.revoke_session_token(token)

    conexion = database.get_connection()
    filas = conexion.execute(
        "SELECT token_hash FROM revoked_sessions"
    ).fetchall()
    conexion.close()

    comprobar("La revocacion deja constancia en revoked_sessions",
              len(filas) >= 1)

    comprobar("El token en claro NUNCA se guarda",
              all(token not in fila["token_hash"] for fila in filas))

    comprobar("is_session_revoked() lo reconoce",
              auth.is_session_revoked(token) is True)


def test_expiracion_normal_sigue_funcionando():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    import hashlib
    import hmac
    import json

    payload = {
        "sub": "odette",
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600
    }

    payload_b64 = auth._b64encode(json.dumps(payload).encode("utf-8"))

    firma = hmac.new(
        auth.SECRET_KEY.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256
    ).digest()

    caducado = f"{payload_b64}.{auth._b64encode(firma)}"

    comprobar("Un token caducado se sigue rechazando",
              auth.verify_token(caducado) is None)


def test_firma_invalida_se_sigue_rechazando():

    token = auth.create_token("odette")

    manipulado = token[:-4] + ("aaaa" if not token.endswith("aaaa")
                               else "bbbb")

    comprobar("Un token con la firma tocada se rechaza",
              auth.verify_token(manipulado) is None)


# ==============================
# 6. /api/settings
# ==============================

def test_settings_acepta_las_claves_de_siempre():

    legitimos = {
        "server_name": "RemoteAdmin",
        "ram_warning_percent": "70",
        "disk_critical_percent": "95"
    }

    settings.save_settings(legitimos)

    guardados = settings.get_settings()

    comprobar(
        "Las claves legitimas se guardan igual que antes",
        guardados["ram_warning_percent"] == "70"
        and guardados["disk_critical_percent"] == "95"
        and guardados["server_name"] == "RemoteAdmin"
    )


def test_settings_rechaza_clave_desconocida():

    try:
        settings.save_settings({"clave_inventada": "x"})
        rechazo = False
    except settings.UnknownSettingError:
        rechazo = True

    comprobar("Una clave desconocida se rechaza", rechazo)


def test_settings_no_escribe_nada_si_hay_una_clave_mala():

    antes = settings.get_settings()["ram_warning_percent"]

    try:
        settings.save_settings({
            "ram_warning_percent": "11",
            "clave_inventada": "x"
        })
    except settings.UnknownSettingError:
        pass

    comprobar(
        "Una peticion con una clave mala no guarda ni las buenas",
        settings.get_settings()["ram_warning_percent"] == antes
    )


def test_settings_no_puede_tocar_la_autenticacion():

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    hash_antes = fila_auth_state()["password_hash"]

    intentos = [
        {"password_hash": auth.generate_password_hash("MiaAhora-G4a!")},
        {"auth_password_hash": "x"},
        {"sessions_valid_from": "0"},
        {"AUTH_PASSWORD_HASH": "x"}
    ]

    rechazados = 0

    for intento in intentos:
        try:
            settings.save_settings(intento)
        except settings.UnknownSettingError:
            rechazados += 1

    comprobar("Ningun intento de escribir credenciales pasa",
              rechazados == len(intentos), f"{rechazados}/{len(intentos)}")

    comprobar("La credencial almacenada no se ha movido",
              fila_auth_state()["password_hash"] == hash_antes)

    comprobar(
        "La contrasena inyectada por settings no sirve para entrar",
        auth.authenticate("odette", "MiaAhora-G4a!") is None
    )

    conexion = database.get_connection()
    colados = conexion.execute(
        "SELECT COUNT(*) FROM settings WHERE key NOT IN "
        "(SELECT key FROM settings WHERE key IN ({}))".format(
            ",".join("?" * len(settings.ALLOWED_SETTINGS))
        ),
        tuple(settings.ALLOWED_SETTINGS)
    ).fetchone()[0]
    conexion.close()

    comprobar("No queda ninguna clave ajena en la tabla settings",
              colados == 0)


# ==============================
# 7. Nada sensible a la vista
# ==============================

def test_las_pruebas_no_exponen_secretos():
    """
    Ninguna comprobacion de esta suite debe llevar un hash, una contrasena
    ni un token en su texto ni en su detalle.
    """

    vaciar_auth_state()
    poner_semilla(CLAVE_SEMILLA)
    auth.get_auth_state()

    token = auth.create_token("odette")
    hash_actual = auth.get_stored_password_hash()

    prohibidos = [CLAVE_SEMILLA, CLAVE_NUEVA, hash_actual, token,
                  "pbkdf2_sha256$"]

    texto = " ".join(
        f"{nombre} {detalle}" for nombre, _, detalle in resultados
    )

    filtrados = [p for p in prohibidos if p and p in texto]

    comprobar("Ninguna comprobacion expone contrasenas, hashes ni tokens",
              not filtrados,
              "se ha colado algo sensible" if filtrados else "")


# ==============================

def main():

    pruebas = [
        test_tabla_creada,
        test_una_sola_fila_logica,
        test_migracion_aditiva,
        test_semilla_cuando_la_tabla_esta_vacia,
        test_la_tabla_manda_sobre_el_env,
        test_no_siembra_dos_veces,
        test_sin_credencial_por_ninguna_via,
        test_login_usa_el_hash_almacenado,
        test_cambio_en_caliente_sin_reiniciar,
        test_arranque_detecta_la_contrasena_por_defecto,
        test_token_nuevo_lleva_iat,
        test_token_anterior_al_corte_es_rechazado,
        test_token_sin_iat_es_rechazado_tras_el_corte,
        test_token_sin_iat_vale_mientras_no_haya_corte,
        test_token_posterior_al_corte_sigue_valido,
        test_invalidacion_global_solo_toca_una_columna,
        test_cada_sesion_tiene_su_propio_token,
        test_logout_individual_sigue_funcionando,
        test_revocacion_registrada_en_la_tabla,
        test_expiracion_normal_sigue_funcionando,
        test_firma_invalida_se_sigue_rechazando,
        test_settings_acepta_las_claves_de_siempre,
        test_settings_rechaza_clave_desconocida,
        test_settings_no_escribe_nada_si_hay_una_clave_mala,
        test_settings_no_puede_tocar_la_autenticacion,
        test_las_pruebas_no_exponen_secretos
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

    try:
        os.remove(BASE_TEMPORAL)
    except OSError:
        pass

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
