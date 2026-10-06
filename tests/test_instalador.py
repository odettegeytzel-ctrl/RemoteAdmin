"""
Descarga del instalador del Agent y credencial de instalacion.

    .venv\\Scripts\\python tests/test_instalador.py

Lo que mas se comprueba aqui es lo que el paquete NO lleva. Un ZIP
descargable se reenvia por correo y se queda en la carpeta de
Descargas: si dentro va un secreto, el secreto se escapa.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import os
import sys
import zipfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.database as database
import backend.enrollment as enrollment
import backend.main as servidor
import backend.organizations as orgs
import backend.packaging as packaging
import backend.users as users

from backend.auth import hash_agent_token


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDelInstalador1!"

PUBLICA = "https://panel.ejemplo.com"

EQUIPO_P = "equipo-plastika-inst"
EQUIPO_B = "equipo-beta-inst"

TOKEN_AGENT = "token-individual-instalador"

escenario = {}


def preparar():
    """Dos organizaciones con sus usuarios y equipos."""

    h.reiniciar()

    servidor.connected_agents.clear()

    os.environ["REMOTEADMIN_PUBLIC_URL"] = PUBLICA

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organization_settings",
                  "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    plastika = orgs.create_organization(
        "Plastika", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    beta = orgs.create_organization(
        "Beta", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, plastika["id"])

    todos = list(users.PERMISSIONS)

    users.create_user("sub_plastika", CLAVE, permissions=todos,
                      organization_id=plastika["id"])

    users.create_user("owner_beta", CLAVE, permissions=todos,
                      organization_id=beta["id"])

    conexion = database.get_connection()
    conexion.execute(
        "UPDATE users SET role = 'owner' WHERE username = 'owner_beta'"
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id, "
        "agent_token_hash, agent_token_active) "
        "VALUES (?, 'PC-PLASTIKA', ?, ?, 1)",
        (EQUIPO_P, plastika["id"], hash_agent_token(TOKEN_AGENT))
    )
    conexion.execute(
        "INSERT INTO devices (device_id, hostname, organization_id) "
        "VALUES (?, 'PC-BETA', ?)", (EQUIPO_B, beta["id"])
    )
    conexion.commit()
    conexion.close()

    users.create_user("operador", CLAVE, organization_id=None)
    users.set_platform_owner("operador", True)

    escenario.update({"plastika": plastika, "beta": beta})

    return escenario


def descargar(quien, **parametros):
    return h.cliente(quien).get("/api/agent-package", params=parametros)


def abrir(contenido):
    return zipfile.ZipFile(io.BytesIO(contenido))


# ==============================
# 1. Autenticacion
# ==============================

def test_la_descarga_exige_sesion():

    preparar()

    comprobar("Sin sesion responde 401",
              h.cliente().get("/api/agent-package").status_code == 401)


def test_quien_puede_descargar():

    preparar()

    comprobar("El Owner descarga el de su organizacion",
              descargar(h.OWNER).status_code == 200)

    # La descarga es el primer paso de un alta, y dar de alta equipos
    # es cosa del Owner: el subadmin no administra credenciales.
    comprobar("El subadmin no descarga",
              descargar("sub_plastika").status_code == 403)

    comprobar("El operador de plataforma debe indicar organizacion",
              descargar("operador").status_code == 400)

    comprobar("Y con ella si puede",
              descargar("operador",
                        organization_id=escenario["plastika"]["id"]
                        ).status_code == 200)


# ==============================
# 2. Aislamiento entre organizaciones
# ==============================

def test_un_owner_no_descarga_el_de_otra_organizacion():

    preparar()

    beta = escenario["beta"]["id"]

    orgs.update_organization(beta, server_url="https://beta.ejemplo.com")

    # Pedir otra organizacion se RECHAZA, no se ignora en silencio: asi
    # el intento queda claro en vez de parecer que funciono.
    respuesta = descargar(h.OWNER, organization_id=beta)

    comprobar("Pedir la de otra organizacion se rechaza con 403",
              respuesta.status_code == 403, str(respuesta.status_code))

    comprobar("Y no se entrega ningun ZIP",
              respuesta.headers.get("content-type") != "application/zip")

    comprobar("Sin filtrar la direccion de la otra",
              "beta.ejemplo.com" not in respuesta.text)

    # Sin el parametro sigue recibiendo el suyo
    configuracion = abrir(
        descargar(h.OWNER).content
    ).read(".env").decode("utf-8")

    comprobar("El suyo apunta a donde corresponde",
              PUBLICA in configuracion
              and "beta.ejemplo.com" not in configuracion)


def test_cada_organizacion_recibe_su_direccion():

    preparar()

    plastika = escenario["plastika"]["id"]

    orgs.update_organization(plastika,
                             server_url="https://plastika.ejemplo.com")

    configuracion = abrir(
        descargar(h.OWNER).content
    ).read(".env").decode("utf-8")

    comprobar("El server_url de la organizacion manda sobre el general",
              "REMOTEADMIN_SERVER=https://plastika.ejemplo.com"
              in configuracion, configuracion)


def test_sin_direccion_publica_no_se_entrega_un_paquete_roto():

    preparar()

    os.environ.pop("REMOTEADMIN_PUBLIC_URL", None)

    respuesta = descargar(h.OWNER)

    comprobar("Se rechaza con 503 en vez de apuntar a localhost",
              respuesta.status_code == 503, str(respuesta.status_code))

    comprobar("Y se explica que hay que configurar",
              "direccion publica" in respuesta.json()["message"].lower())

    os.environ["REMOTEADMIN_PUBLIC_URL"] = PUBLICA


# ==============================
# 3. El paquete
# ==============================

def test_el_paquete_es_un_zip_con_lo_necesario():

    preparar()

    respuesta = descargar(h.OWNER)

    comprobar("Se sirve como ZIP",
              respuesta.headers["content-type"] == "application/zip")

    comprobar("Con nombre de archivo fijo",
              'filename="RemoteAdmin-Agent-Windows.zip"'
              in respuesta.headers["content-disposition"])

    paquete = abrir(respuesta.content)

    comprobar("El ZIP es valido", paquete.testzip() is None)

    dentro = set(paquete.namelist())

    imprescindibles = {
        "agent/agent.py", "agent/paths.py", "agent/recorder.py",
        "requirements-agent-windows.txt",
        "installer/install-agent.ps1", "installer/README.md",
        ".env"
    }

    comprobar("Lleva el Agent, sus dependencias y el instalador",
              imprescindibles <= dentro,
              str(sorted(imprescindibles - dentro)))


def test_el_paquete_no_lleva_nada_del_servidor():
    """Lo importante de este bloque."""

    preparar()

    dentro = abrir(descargar(h.OWNER).content).namelist()

    prohibidos = []

    for nombre in dentro:

        minuscula = nombre.lower()

        if minuscula.startswith("backend/"):
            prohibidos.append(nombre)

        if minuscula.startswith("certs/"):
            prohibidos.append(nombre)

        if minuscula.endswith((".db", ".pem", ".key", ".crt", ".pfx")):
            prohibidos.append(nombre)

    comprobar("Ni backend, ni base de datos, ni certificados",
              not prohibidos, str(prohibidos))

    comprobar("Ni las pruebas ni el frontend",
              not [n for n in dentro
                   if n.startswith(("tests/", "frontend/"))])


def test_la_lista_de_archivos_es_fija():
    """Sin parametro de ruta no hay recorrido de directorios."""

    codigo = io.open(RAIZ / "backend" / "packaging.py",
                     encoding="utf-8").read()

    comprobar("El nombre del archivo es una constante",
              'PACKAGE_FILENAME = "RemoteAdmin-Agent-Windows.zip"'
              in codigo)

    comprobar("Los archivos salen de una tupla fija",
              "PACKAGE_FILES = (" in codigo)

    # build_agent_package recibe una organizacion, no una ruta
    comprobar("La funcion no acepta ninguna ruta",
              "def build_agent_package(organizacion=None)" in codigo)

    endpoint = io.open(RAIZ / "backend" / "main.py",
                       encoding="utf-8").read()

    trozo = endpoint.split('@app.get("/api/agent-package")', 1)[1]
    trozo = trozo.split(chr(10) + "@app.", 1)[0]

    comprobar("El endpoint no lee ningun nombre de archivo",
              "filename" not in trozo.replace(
                  'filename="{packaging.PACKAGE_FILENAME}"', ""
              ))


def test_no_se_cuela_un_salto_de_carpeta():

    preparar()

    nombres = abrir(descargar(h.OWNER).content).namelist()

    comprobar("Ningun nombre sube de carpeta",
              not [n for n in nombres if ".." in n], str(nombres))

    comprobar("Ni es una ruta absoluta",
              not [n for n in nombres
                   if n.startswith("/") or n.startswith("\\")
                   or (len(n) > 1 and n[1] == ":")])


# ==============================
# 4. Secretos
# ==============================

def test_el_paquete_no_lleva_credenciales():

    preparar()

    ficha, valor = enrollment.create_token(
        escenario["plastika"]["id"], label="PC nueva"
    )

    contenido = descargar(h.OWNER).content

    paquete = abrir(contenido)

    todo = b""

    for nombre in paquete.namelist():
        todo += paquete.read(nombre)

    texto = todo.decode("utf-8", errors="ignore")

    comprobar("No va la credencial de alta recien creada",
              valor not in texto)

    comprobar("Ni ninguna credencial: AGENT_TOKEN va vacio",
              "AGENT_TOKEN=\n" in paquete.read(".env").decode("utf-8"))

    comprobar("No va el token individual de ningun equipo",
              TOKEN_AGENT not in texto)

    comprobar("Ni ninguna huella de token",
              hash_agent_token(TOKEN_AGENT) not in texto)

    for secreto in ("AUTH_SECRET_KEY", "AUTH_PASSWORD_HASH",
                    "agent_token_hash"):
        comprobar(f"Ni rastro de {secreto}", secreto not in texto)


def test_la_descarga_no_deja_secretos_en_la_auditoria():

    preparar()

    descargar(h.OWNER)

    conexion = database.get_connection()

    filas = [
        dict(r) for r in conexion.execute(
            "SELECT * FROM audit_log WHERE action = "
            "'agent_package.download'"
        )
    ]

    conexion.close()

    comprobar("La descarga queda registrada", len(filas) == 1,
              str(len(filas)))

    texto = str(filas)

    comprobar("Sin ningun token en el registro",
              "rae_" not in texto and TOKEN_AGENT not in texto)

    comprobar("Con la organizacion correcta",
              filas and filas[0]["organization_id"]
              == escenario["plastika"]["id"])


def test_la_credencial_se_entrega_una_sola_vez():

    preparar()

    cliente = h.cliente(h.OWNER)

    respuesta = cliente.post("/api/enrollment-tokens",
                             json={"label": "PC recepcion",
                                   "expires_in_days": 1})

    comprobar("Se crea", respuesta.status_code == 200,
              str(respuesta.status_code))

    datos = respuesta.json()

    valor = datos["value"]

    comprobar("Empieza por rae_", valor.startswith("rae_"))

    listado = cliente.get("/api/enrollment-tokens").json()

    comprobar("Al listarlas ya no aparece el valor",
              valor not in str(listado), "el valor se repite")

    conexion = database.get_connection()

    guardado = conexion.execute(
        "SELECT * FROM enrollment_tokens"
    ).fetchall()

    conexion.close()

    comprobar("En la base solo esta la huella",
              valor not in str([dict(r) for r in guardado]))


def test_la_credencial_sirve_para_dar_de_alta_y_el_token_individual_despues():
    """El flujo completo que se va a seguir en el equipo nuevo."""

    preparar()

    ficha, credencial = enrollment.create_token(
        escenario["plastika"]["id"], label="PC nueva"
    )

    # 1. Alta con la credencial de la organizacion
    alta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-NUEVA", "operating_system": "Windows",
              "ip_address": "10.0.0.9"},
        headers={"X-Agent-Token": credencial}
    )

    comprobar("El alta se acepta", alta.status_code == 200,
              str(alta.status_code))

    datos = alta.json()

    comprobar("Devuelve device_id y token individual",
              datos.get("device_id") and datos.get("agent_token"))

    comprobar("El token individual NO es la credencial de alta",
              datos["agent_token"] != credencial)

    comprobar("Y entra en la organizacion de la credencial",
              servidor.organization_of_device(datos["device_id"])
              == escenario["plastika"]["id"])

    # 2. A partir de aqui, el token individual
    latido = h.cliente().post(
        "/api/devices/heartbeat",
        json={"device_id": datos["device_id"], "ip_address": "10.0.0.9"},
        headers={"X-Agent-Token": datos["agent_token"]}
    )

    comprobar("El heartbeat funciona con el token individual",
              latido.status_code == 200)

    # 3. La credencial de alta ya no hace falta: se revoca y el equipo
    #    sigue funcionando.
    enrollment.revoke_token(ficha["id"])

    comprobar("Revocada la credencial, el equipo sigue operando",
              h.cliente().post(
                  "/api/devices/heartbeat",
                  json={"device_id": datos["device_id"],
                        "ip_address": "10.0.0.9"},
                  headers={"X-Agent-Token": datos["agent_token"]}
              ).status_code == 200)

    comprobar("Pero ya no da de alta a nadie mas",
              h.cliente().post(
                  "/api/devices/register",
                  json={"hostname": "PC-OTRA",
                        "operating_system": "Windows",
                        "ip_address": "10.0.0.10"},
                  headers={"X-Agent-Token": credencial}
              ).status_code == 401)


def test_el_alta_no_deja_elegir_organizacion():

    preparar()

    ficha, credencial = enrollment.create_token(
        escenario["plastika"]["id"]
    )

    alta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-COLADA", "operating_system": "Windows",
              "ip_address": "10.0.0.11",
              # Un Agent que intenta entrar en otra empresa
              "organization_id": escenario["beta"]["id"]},
        headers={"X-Agent-Token": credencial}
    )

    comprobar("El organization_id del cuerpo se ignora",
              servidor.organization_of_device(alta.json()["device_id"])
              == escenario["plastika"]["id"])


# ==============================
# 5. El instalador
# ==============================

def test_el_instalador_tiene_lo_que_hace_falta():

    guion = io.open(RAIZ / "installer" / "install-agent.ps1",
                    encoding="utf-8").read()

    comprobar("Acepta -Server", "[string]$Server" in guion)

    comprobar("Acepta -EnrollmentToken",
              "[string]$EnrollmentToken" in guion)

    comprobar("Instala en C:\\ProgramData\\RemoteAdmin",
              '$env:ProgramData "RemoteAdmin"' in guion)

    comprobar("Escribe REMOTEADMIN_SERVER",
              "REMOTEADMIN_SERVER=$Server" in guion)

    comprobar("Escribe AGENT_TOKEN",
              "AGENT_TOKEN=$EnrollmentToken" in guion)

    comprobar("Instala las dependencias",
              "requirements-agent-windows.txt" in guion
              and "pip install" in guion)

    comprobar("Registra la tarea programada",
              "Register-ScheduledTask" in guion
              and "RemoteAdminAgent" in guion)

    comprobar("Y la arranca",
              "Start-ScheduledTask" in guion)

    comprobar("Arranca al iniciar sesion",
              "-AtLogOn" in guion)


def test_el_instalador_cuida_la_credencial():

    guion = io.open(RAIZ / "installer" / "install-agent.ps1",
                    encoding="utf-8").read()

    comprobar("La pide oculta si no se pasa por parametro",
              "Read-Host" in guion and "-AsSecureString" in guion)

    comprobar("Avisa de que el parametro queda en el historial",
              "historial" in guion.lower())

    comprobar("Restringe quien puede leer el .env",
              "icacls" in guion)

    comprobar("No vuelve a pedirla si el equipo ya esta dado de alta",
              "identity.json" in guion and "$yaEnrolado" in guion)

    comprobar("Comprueba que la direccion sea http o https",
              'StartsWith("https://")' in guion)

    # 'rae_' aparece en la ayuda y al validar el prefijo, que es
    # correcto. Lo que no puede haber es una credencial de verdad:
    # son 'rae_' seguido de muchos caracteres de base64.
    import re

    comprobar("No hay ninguna credencial de verdad en el guion",
              not re.findall(r"rae_[A-Za-z0-9_-]{12,}", guion))


def test_no_hay_dos_instaladores():

    comprobar("El instalador vive en installer/",
              (RAIZ / "installer" / "install-agent.ps1").is_file())

    comprobar("Y no quedo una copia en agent/service",
              not (RAIZ / "agent" / "service"
                   / "install-agent.ps1").is_file())


def test_no_se_hardcodea_ninguna_direccion_privada():

    for archivo, comentario in (("backend/packaging.py", "#"),
                                ("installer/install-agent.ps1", "#")):

        texto = io.open(RAIZ / archivo, encoding="utf-8").read()

        # Solo las lineas de CODIGO: los comentarios mencionan
        # 127.0.0.1 justamente para explicar por que no se apunta ahi.
        codigo = chr(10).join(
            linea for linea in texto.split(chr(10))
            if not linea.strip().startswith(comentario)
        )

        comprobar(f"{archivo} no fija ninguna IP privada",
                  "192.168." not in codigo and "10.0.0." not in codigo
                  and "127.0.0.1" not in codigo)

    comprobar("Ni se nombra a ninguna organizacion concreta",
              "plastika" not in io.open(
                  RAIZ / "backend" / "packaging.py", encoding="utf-8"
              ).read().lower())


# ==============================
# 6. La interfaz
# ==============================

def test_el_panel_ofrece_la_descarga():

    html = io.open(RAIZ / "frontend" / "index.html",
                   encoding="utf-8").read()

    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()

    comprobar("Hay una entrada de menu",
              'id="nav-install"' in html and "Instalar equipo" in html)

    comprobar("Con el titulo de la pantalla",
              "Conecta un nuevo equipo" in html)

    comprobar("Y el boton de descarga",
              "Descargar Agent para Windows" in html)

    comprobar("Que llama al endpoint",
              '"/api/agent-package"' in js)

    comprobar("Se puede crear la credencial desde ahi",
              'id="install-create-token"' in html
              and '"/api/enrollment-tokens"' in js)

    comprobar("Con boton de copiar",
              'id="install-copy-token"' in html)

    comprobar("Avisa de que solo se ve una vez",
              "No se puede volver a" in html)

    # Solo el codigo del bloque nuevo, sin sus comentarios: ahi se
    # nombran localStorage e innerHTML precisamente para decir que no
    # se usan.
    bloque = js.split("INSTALAR UN EQUIPO", 1)[1]

    codigo = chr(10).join(
        linea for linea in bloque.split(chr(10))
        if not linea.strip().startswith("//")
    )

    comprobar("El valor no se guarda en el navegador",
              "localStorage" not in codigo
              and "sessionStorage" not in codigo)

    comprobar("Y la etiqueta se pinta sin innerHTML",
              "innerHTML" not in codigo)

    comprobar("Sino con textContent",
              "textContent" in codigo and "replaceChildren" in codigo)


# ==============================
# 7. Nada de lo anterior se rompe
# ==============================

def test_los_endpoints_de_siempre_siguen_igual():

    preparar()

    cliente = h.cliente(h.OWNER)

    for ruta, esperado in (("/api/devices", 200),
                           ("/api/alerts", 200),
                           ("/api/settings", 200),
                           ("/api/users/me", 200),
                           ("/api/recordings", 200),
                           ("/api/organizations", 403)):

        comprobar(f"{ruta} responde {esperado}",
                  cliente.get(ruta).status_code == esperado,
                  str(cliente.get(ruta).status_code))

    comprobar("El operador sigue administrando organizaciones",
              h.cliente("operador").get(
                  "/api/organizations"
              ).status_code == 200)

    comprobar("El aislamiento sigue en pie",
              cliente.get(f"/api/devices/{EQUIPO_B}/software"
                          ).status_code == 404)


def test_la_suspension_alcanza_a_la_descarga():

    preparar()

    orgs.update_organization(escenario["plastika"]["id"],
                             subscription_status=orgs.STATUS_SUSPENDED)

    comprobar("Una organizacion suspendida no descarga el instalador",
              descargar(h.OWNER).status_code == 403)

    comprobar("Pero sus Agents siguen funcionando",
              h.cliente().post(
                  "/api/devices/heartbeat",
                  json={"device_id": EQUIPO_P, "ip_address": "10.0.0.1"},
                  headers={"X-Agent-Token": TOKEN_AGENT}
              ).status_code == 200)


# ==============================

def main():

    pruebas = [
        test_la_descarga_exige_sesion,
        test_quien_puede_descargar,
        test_un_owner_no_descarga_el_de_otra_organizacion,
        test_cada_organizacion_recibe_su_direccion,
        test_sin_direccion_publica_no_se_entrega_un_paquete_roto,
        test_el_paquete_es_un_zip_con_lo_necesario,
        test_el_paquete_no_lleva_nada_del_servidor,
        test_la_lista_de_archivos_es_fija,
        test_no_se_cuela_un_salto_de_carpeta,
        test_el_paquete_no_lleva_credenciales,
        test_la_descarga_no_deja_secretos_en_la_auditoria,
        test_la_credencial_se_entrega_una_sola_vez,
        test_la_credencial_sirve_para_dar_de_alta_y_el_token_individual_despues,
        test_el_alta_no_deja_elegir_organizacion,
        test_el_instalador_tiene_lo_que_hace_falta,
        test_el_instalador_cuida_la_credencial,
        test_no_hay_dos_instaladores,
        test_no_se_hardcodea_ninguna_direccion_privada,
        test_el_panel_ofrece_la_descarga,
        test_los_endpoints_de_siempre_siguen_igual,
        test_la_suspension_alcanza_a_la_descarga
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:90])

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
