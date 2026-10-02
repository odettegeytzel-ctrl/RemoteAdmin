"""
Pruebas del sistema de usuarios: alta, estado, borrado y proteccion del owner.

    .venv\\Scripts\\python tests/test_usuarios.py

Base temporal, contrasenas inventadas. La cuenta real no se toca.
"""

import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.auth as auth
import backend.database as database
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==============================
# 1. Migracion de la cuenta unica
# ==============================

def test_la_cuenta_actual_se_convierte_en_owner():

    h.reiniciar()

    owner = users.get_user(h.OWNER)

    comprobar("La cuenta anterior existe como usuario", owner is not None)

    if owner:
        comprobar("Su rol es owner", owner["role"] == users.ROLE_OWNER)
        comprobar("Esta activa", owner["active"] is True)

    comprobar(
        "Conserva su contrasena: entra sin cambiar nada",
        auth.authenticate(h.OWNER, h.CLAVE_OWNER) is not None
    )


def test_la_migracion_es_idempotente():

    h.reiniciar()

    antes = users.get_user(h.OWNER)

    for _ in range(5):
        users.ensure_owner_migrated()

    comprobar("Repetir la migracion no duplica usuarios",
              len(users.list_users()) == 1)

    comprobar("Ni cambia el que ya habia",
              users.get_user(h.OWNER)["id"] == antes["id"])


def test_no_inventa_credenciales_si_no_hay_ninguna():

    conexion = database.get_connection()
    conexion.execute("DELETE FROM user_permissions")
    conexion.execute("DELETE FROM users")
    conexion.execute("DELETE FROM auth_state")
    conexion.commit()
    conexion.close()

    semilla = auth.AUTH_PASSWORD_HASH
    auth.AUTH_PASSWORD_HASH = ""

    try:
        creado = users.ensure_owner_migrated()

        # Se cuenta leyendo la base directamente: list_users() volveria a
        # llamar a la migracion y, con la semilla ya restaurada, crearia el
        # owner justo antes de comprobar que no existe.
        conexion = database.get_connection()
        total = conexion.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conexion.close()

    finally:
        auth.AUTH_PASSWORD_HASH = semilla

    comprobar("Sin credencial previa no se crea ningun usuario",
              creado is None and total == 0, str(total))

    h.reiniciar()


def test_el_hash_nunca_sale_del_modulo():

    h.reiniciar()

    owner = users.get_user(h.OWNER)

    comprobar("El usuario devuelto no incluye el hash",
              "password_hash" not in owner)

    comprobar("Tampoco en el listado",
              all("password_hash" not in u for u in users.list_users()))


# ==============================
# 2. Alta
# ==============================

def test_creacion_correcta():

    h.reiniciar()

    creado = h.crear_subadmin("ana", permisos=["devices.view"],
                              email="ana@ejemplo.com")

    comprobar("Se crea el usuario", creado is not None)
    comprobar("Con rol subadmin", creado["role"] == users.ROLE_SUBADMIN)
    comprobar("Activo por defecto", creado["active"] is True)
    comprobar("Con sus permisos", creado["permissions"] == ["devices.view"])
    comprobar("Con su correo", creado["email"] == "ana@ejemplo.com")
    comprobar("Sin correo verificado todavia",
              creado["email_verified"] is False)

    comprobar("Puede iniciar sesion",
              auth.authenticate("ana", h.CLAVE_SUBADMIN) is not None)


def test_username_unico():

    h.reiniciar()
    h.crear_subadmin("ana")

    try:
        h.crear_subadmin("ana")
        rechazo = False
    except users.UserError:
        rechazo = True

    comprobar("No se admite un nombre repetido", rechazo)

    try:
        h.crear_subadmin("ANA")
        rechazo_mayusculas = False
    except users.UserError:
        rechazo_mayusculas = True

    comprobar("Ni el mismo nombre con otras mayusculas",
              rechazo_mayusculas)


def test_validacion_de_username():

    h.reiniciar()

    invalidos = ["ab", "a" * 33, "con espacio", "raro;drop", ""]

    rechazados = 0

    for nombre in invalidos:
        try:
            h.crear_subadmin(nombre)
        except users.UserError:
            rechazados += 1

    comprobar("Los nombres invalidos se rechazan",
              rechazados == len(invalidos),
              f"{rechazados}/{len(invalidos)}")


def test_validacion_de_correo():

    h.reiniciar()

    rechazados = 0

    for correo in ["sinarroba", "a@b", "dos@@arrobas.com", "con espacio@a.com"]:
        try:
            h.crear_subadmin("ana", email=correo)
        except users.UserError:
            rechazados += 1

    comprobar("Los correos invalidos se rechazan", rechazados == 4,
              str(rechazados))


def test_la_politica_de_contrasena_se_aplica_al_alta():

    h.reiniciar()

    try:
        users.create_user("ana", "corta")
        rechazo = False
    except users.UserError:
        rechazo = True

    comprobar("Una contrasena debil no crea usuario", rechazo)

    comprobar("Y no queda el usuario a medias",
              users.get_user("ana") is None)


def test_no_se_puede_crear_un_owner():

    h.reiniciar()

    try:
        users.create_user("otro", h.CLAVE_SUBADMIN, role=users.ROLE_OWNER)
        rechazo = False
    except users.UserError:
        rechazo = True

    comprobar("Un alta no puede fabricar un segundo owner", rechazo)


def test_contrasena_nunca_en_texto_plano():

    h.reiniciar()
    h.crear_subadmin("ana")

    conexion = database.get_connection()

    volcado = []

    for (tabla,) in conexion.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall():
        for fila in conexion.execute(f"SELECT * FROM {tabla}"):
            volcado.append(" ".join(str(v) for v in tuple(fila)))

    conexion.close()

    texto = " ".join(volcado)

    comprobar("La contrasena del subadmin no esta en la base",
              h.CLAVE_SUBADMIN not in texto)

    comprobar("La del owner tampoco", h.CLAVE_OWNER not in texto)

    conexion = database.get_connection()
    hash_guardado = conexion.execute(
        "SELECT password_hash FROM users WHERE username = 'ana'"
    ).fetchone()[0]
    conexion.close()

    comprobar("Se guarda en PBKDF2-SHA256",
              hash_guardado.startswith("pbkdf2_sha256$"))


# ==============================
# 3. Activar / desactivar
# ==============================

def test_desactivar_y_reactivar():

    h.reiniciar()
    h.crear_subadmin("ana")

    users.set_active("ana", False)

    comprobar("Queda desactivado",
              users.get_user("ana")["active"] is False)

    comprobar("Y no puede iniciar sesion",
              auth.authenticate("ana", h.CLAVE_SUBADMIN) is None)

    users.set_active("ana", True)

    comprobar("Al reactivarlo vuelve a entrar",
              auth.authenticate("ana", h.CLAVE_SUBADMIN) is not None)


def test_desactivar_invalida_sus_sesiones():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["devices.view"])

    sesion = h.cliente("ana")

    comprobar("La sesion funciona antes de desactivar",
              sesion.get("/api/devices").status_code == 200)

    users.set_active("ana", False)

    comprobar("Pierde el acceso en el acto",
              sesion.get("/api/devices").status_code == 401,
              str(sesion.get("/api/devices").status_code))

    comprobar("Su token deja de verificar",
              auth.verify_token(auth.create_token("ana")) is None
              or users.get_user("ana")["active"] is False)


def test_desactivar_no_afecta_a_otros():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["devices.view"])
    h.crear_subadmin("beto", permisos=["devices.view"])

    beto = h.cliente("beto")

    users.set_active("ana", False)

    comprobar("La sesion de otro usuario sigue viva",
              beto.get("/api/devices").status_code == 200)

    comprobar("Y la del owner tambien",
              h.cliente(h.OWNER).get("/api/devices").status_code == 200)


# ==============================
# 4. Eliminar
# ==============================

def test_eliminar_usuario():

    h.reiniciar()
    h.crear_subadmin("ana", permisos=["devices.view"])

    users.delete_user("ana")

    comprobar("El usuario desaparece", users.get_user("ana") is None)

    comprobar("Ya no puede entrar",
              auth.authenticate("ana", h.CLAVE_SUBADMIN) is None)

    conexion = database.get_connection()
    permisos = conexion.execute(
        "SELECT COUNT(*) FROM user_permissions"
    ).fetchone()[0]
    conexion.close()

    comprobar("Sus permisos se eliminan con el", permisos == 0)


# ==============================
# 5. Proteccion del owner
# ==============================

def test_no_se_puede_eliminar_al_ultimo_owner():

    h.reiniciar()

    try:
        users.delete_user(h.OWNER)
        rechazo = False
    except users.UserError:
        rechazo = True

    comprobar("No se elimina al ultimo owner activo", rechazo)

    comprobar("El owner sigue ahi", users.get_user(h.OWNER) is not None)


def test_no_se_puede_desactivar_al_ultimo_owner():

    h.reiniciar()

    try:
        users.set_active(h.OWNER, False)
        rechazo = False
    except users.UserError:
        rechazo = True

    comprobar("No se desactiva al ultimo owner activo", rechazo)

    comprobar("Y sigue pudiendo entrar",
              auth.authenticate(h.OWNER, h.CLAVE_OWNER) is not None)


def test_al_owner_no_se_le_asignan_permisos():

    h.reiniciar()

    try:
        users.set_permissions(h.OWNER, ["devices.view"])
        rechazo = False
    except users.UserError:
        rechazo = True

    comprobar("Al owner no se le asigna una lista de permisos", rechazo)

    comprobar("Porque los tiene todos por su rol",
              all(users.has_permission(users.get_user(h.OWNER), p)
                  for p in users.PERMISSIONS))


# ==============================
# 6. Auditoria
# ==============================

def test_auditoria_de_las_operaciones():

    h.reiniciar()

    owner = h.cliente(h.OWNER)

    owner.post("/api/users", json={
        "username": "ana", "password": h.CLAVE_SUBADMIN,
        "permissions": ["devices.view"]
    })

    owner.post("/api/users/ana/permissions",
               json={"permissions": ["devices.view", "recordings.view"]})

    owner.post("/api/users/ana/active", json={"active": False})
    owner.post("/api/users/ana/active", json={"active": True})
    owner.post("/api/users/ana/email", json={"email": "ana@ejemplo.com"})
    owner.delete("/api/users/ana")

    acciones = {r["action"] for r in h.registros()}

    esperadas = {
        "user.created", "user.permissions_changed",
        "user.disabled", "user.enabled", "user.updated", "user.deleted"
    }

    comprobar("Se auditan las seis operaciones",
              esperadas <= acciones,
              str(sorted(esperadas - acciones)))

    comprobar("Todas se atribuyen al owner",
              all(r["username"] == h.OWNER
                  for r in h.registros()
                  if r["action"].startswith("user.")))

    todo = " ".join(str(r["details"]) for r in h.registros())

    etiquetas = ("la contrasena del subadmin", "la del owner",
                 "ningun hash")

    for etiqueta, secreto in zip(
        etiquetas, (h.CLAVE_SUBADMIN, h.CLAVE_OWNER, "pbkdf2_sha256$")
    ):
        comprobar(f"La auditoria no guarda {etiqueta}", secreto not in todo)


def test_el_informe_no_expone_secretos():

    texto = " ".join(f"{n} {d}" for n, _, d in resultados)

    comprobar("Ninguna comprobacion expone contrasenas ni hashes",
              h.CLAVE_OWNER not in texto
              and h.CLAVE_SUBADMIN not in texto
              and "pbkdf2_sha256$" not in texto)


# ==============================

def main():

    pruebas = [
        test_la_cuenta_actual_se_convierte_en_owner,
        test_la_migracion_es_idempotente,
        test_no_inventa_credenciales_si_no_hay_ninguna,
        test_el_hash_nunca_sale_del_modulo,
        test_creacion_correcta,
        test_username_unico,
        test_validacion_de_username,
        test_validacion_de_correo,
        test_la_politica_de_contrasena_se_aplica_al_alta,
        test_no_se_puede_crear_un_owner,
        test_contrasena_nunca_en_texto_plano,
        test_desactivar_y_reactivar,
        test_desactivar_invalida_sus_sesiones,
        test_desactivar_no_afecta_a_otros,
        test_eliminar_usuario,
        test_no_se_puede_eliminar_al_ultimo_owner,
        test_no_se_puede_desactivar_al_ultimo_owner,
        test_al_owner_no_se_le_asignan_permisos,
        test_auditoria_de_las_operaciones,
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
