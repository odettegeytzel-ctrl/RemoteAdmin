"""
TLS del WebSocket del Agent: ws:// y wss:// en los dos entornos.

    .venv\\Scripts\\python tests/test_tls.py

El fallo que origina esta suite: el Agent pasaba ssl=None cuando no
habia CA propia configurada, contando con que la libreria pusiera su
contexto por defecto. Pero pasar ssl=None junto a una URL wss:// es un
error explicito —"ssl=None is incompatible with a wss:// URI"—, asi
que contra el servidor publico no llegaba a conectar nunca. Omitir el
argumento y pasarlo en None no son lo mismo.

Lo que se comprueba aqui es la decision real: se ejecuta el codigo de
agent.py con cada configuracion y se mira el contexto que sale. No se
importa el modulo entero porque arrastra wmi, mss y pyautogui, que
necesitan Windows con escritorio.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import os
import ssl
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.database as database
import backend.enrollment as enrollment
import backend.main as servidor
import backend.organizations as orgs
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

CA_DESARROLLO = RAIZ / "certs" / "dev-ca-cert.pem"

escenario = {}


# ==============================
# La decision sobre TLS
# ==============================

def _codigo_de_la_decision():
    """El trozo de agent.py que decide el contexto, tal cual esta."""

    desde = AGENTE.split("def usa_tls():", 1)[1]

    cuerpo = desde.split(chr(10) + chr(10) + "# =====", 1)[0]

    return "def usa_tls():" + cuerpo


def construir(server_url, ca_cert=""):
    """Ejecuta esa decision con una configuracion concreta."""

    espacio = {"ssl": ssl, "SERVER_URL": server_url, "CA_CERT": ca_cert}

    exec(_codigo_de_la_decision(), espacio)

    return espacio["build_ssl_context"](), espacio["usa_tls"]()


def test_ws_no_fuerza_ssl():

    for url in ("http://127.0.0.1:8000", "http://192.168.1.50:8000",
                "http://equipo.local"):

        contexto, tls = construir(url)

        comprobar(f"'{url}' no usa TLS", not tls)

        comprobar(f"'{url}' no trae contexto", contexto is None)


def test_ws_con_ca_configurada_sigue_sin_ssl():
    """Una CA puesta por error no debe convertir http en https."""

    contexto, tls = construir(
        "http://127.0.0.1:8000", str(CA_DESARROLLO)
    )

    comprobar("Con ws:// la CA se ignora", contexto is None)

    comprobar("Y sigue sin TLS", not tls)


def test_wss_con_ca_propia_usa_esa_autoridad():
    """El caso del entorno local con la CA del proyecto."""

    if not CA_DESARROLLO.is_file():
        comprobar("Existe la CA de desarrollo", False, str(CA_DESARROLLO))
        return

    contexto, tls = construir(
        "https://127.0.0.1:8000", str(CA_DESARROLLO)
    )

    comprobar("Con wss:// y CA propia se usa TLS", tls)

    comprobar("Y se devuelve un contexto de verdad",
              isinstance(contexto, ssl.SSLContext))

    comprobar("Nunca None", contexto is not None)

    comprobar("El certificado se verifica",
              contexto.verify_mode == ssl.CERT_REQUIRED)

    comprobar("Y el nombre de host tambien",
              contexto.check_hostname is True)

    # Con CA propia se confia SOLO en ella: una sola autoridad
    # cargada, no las del sistema.
    comprobar("Se confia unicamente en esa autoridad",
              len(contexto.get_ca_certs()) == 1,
              str(len(contexto.get_ca_certs())))


def test_wss_sin_ca_usa_la_validacion_del_sistema():
    """El caso de produccion detras de Cloudflare."""

    contexto, tls = construir("https://panel.ejemplo.com", "")

    comprobar("Con wss:// y sin CA se usa TLS", tls)

    comprobar("Se devuelve un contexto, NO None",
              isinstance(contexto, ssl.SSLContext))

    comprobar("El certificado se verifica",
              contexto.verify_mode == ssl.CERT_REQUIRED)

    comprobar("El nombre de host se comprueba",
              contexto.check_hostname is True)

    # Las autoridades estandar: muchas mas que una sola CA propia
    comprobar("Se confia en las autoridades del sistema",
              len(contexto.get_ca_certs()) > 1,
              str(len(contexto.get_ca_certs())))


def test_nunca_se_pasaria_ssl_none_con_wss():
    """La comprobacion que resume el fallo corregido."""

    urls = (
        "https://panel.ejemplo.com",
        "https://remoteadmin.ejemplo.com",
        "https://127.0.0.1:8000",
        "https://equipo.local:8443"
    )

    for url in urls:

        for ca in ("", str(CA_DESARROLLO) if CA_DESARROLLO.is_file() else ""):

            contexto, _ = construir(url, ca)

            etiqueta = "con CA" if ca else "sin CA"

            comprobar(f"'{url}' {etiqueta}: contexto presente",
                      contexto is not None)


def test_el_contexto_solo_se_pasa_cuando_hay_tls():
    """Con ws:// no se pasa el argumento, ni siquiera en None."""

    bloque = AGENTE.split("async def websocket_connection", 1)[1]

    trozo = bloque.split("websockets.connect(", 1)[0]

    comprobar("Se calcula el contexto antes de conectar",
              "contexto = build_ssl_context()" in trozo)

    comprobar("Y solo se anade si existe",
              "if contexto is not None:" in trozo
              and 'parametros["ssl"] = contexto' in trozo)

    llamada = bloque.split("websockets.connect(", 1)[1].split(")", 1)[0]

    comprobar("La llamada no lleva ssl= fijo",
              "ssl=" not in llamada, llamada.strip())

    comprobar("El token sigue viajando en la cabecera",
              "X-Agent-Token" in trozo)


def test_no_se_desactiva_la_verificacion_en_ningun_sitio():

    sospechosos = (
        "_create_unverified_context",
        "CERT_NONE",
        "check_hostname = False",
        "check_hostname=False",
        "verify=False",
        "verify = False",
        "SSLContext(ssl.PROTOCOL_TLS)"
    )

    encontrados = []

    for archivo in (RAIZ / "agent").glob("*.py"):

        texto = io.open(archivo, encoding="utf-8").read()

        for patron in sospechosos:
            if patron in texto:
                encontrados.append(f"{archivo.name}: {patron}")

    comprobar("Nada desactiva la verificacion TLS",
              not encontrados, str(encontrados))

    comprobar("El contexto se crea con create_default_context",
              AGENTE.count("ssl.create_default_context") == 2,
              str(AGENTE.count("ssl.create_default_context")))

    comprobar("Y requests nunca va con verify=False",
              "REQUESTS_VERIFY = CA_CERT or True" in AGENTE)


def test_la_url_del_websocket_sale_del_esquema():

    comprobar("https lleva a wss",
              'WEBSOCKET_URL = "wss://" + SERVER_URL[len("https://"):]'
              in AGENTE)

    comprobar("y http a ws",
              'WEBSOCKET_URL = "ws://" + SERVER_URL[len("http://"):]'
              in AGENTE)

    comprobar("Con la ruta del canal del Agent",
              '"/ws/agent"' in AGENTE)


def test_con_https_la_ca_que_falta_se_dice_a_tiempo():
    """Sin esto, el fallo aparece como un error TLS opaco."""

    comprobar("Se comprueba que el archivo de CA exista",
              "if CA_CERT and not os.path.isfile(CA_CERT):" in AGENTE)

    comprobar("Y con https se para en el arranque",
              'if SERVER_URL.startswith("https://"):' in AGENTE
              and "raise SystemExit(message)" in AGENTE)


# ==============================
# Regresiones pedidas
# ==============================

def preparar():

    h.reiniciar()

    servidor.connected_agents.clear()

    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    conexion = database.get_connection()
    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organization_settings",
                  "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    organizacion = orgs.create_organization(
        "Empresa", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, organizacion["id"])

    escenario["organizacion"] = organizacion["id"]

    return organizacion["id"]


def dar_de_alta(hostname="PC-TLS"):

    ficha, credencial = enrollment.create_token(escenario["organizacion"])

    datos = h.cliente().post(
        "/api/devices/register",
        json={"hostname": hostname, "operating_system": "Windows",
              "ip_address": "10.0.0.5"},
        headers={"X-Agent-Token": credencial}
    ).json()

    return ficha, credencial, datos


def test_el_heartbeat_sigue_funcionando():

    preparar()

    ficha, credencial, alta = dar_de_alta()

    device_id = alta["device_id"]
    token = alta["agent_token"]

    for vuelta in range(3):

        respuesta = h.cliente().post(
            "/api/devices/heartbeat",
            json={"device_id": device_id, "ip_address": "10.0.0.5"},
            headers={"X-Agent-Token": token}
        )

        comprobar(f"Latido {vuelta + 1} aceptado",
                  respuesta.status_code == 200,
                  str(respuesta.status_code))

    conexion = database.get_connection()

    visto = conexion.execute(
        "SELECT last_seen FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()[0]

    conexion.close()

    comprobar("Y queda registrado cuando se vio", visto is not None)

    comprobar("Sin token no se acepta",
              h.cliente().post(
                  "/api/devices/heartbeat",
                  json={"device_id": device_id, "ip_address": "10.0.0.5"}
              ).status_code == 401)


def test_la_autenticacion_del_agent_sigue_igual():

    preparar()

    ficha, credencial, alta = dar_de_alta("PC-AUTENTICA")

    device_id = alta["device_id"]
    token = alta["agent_token"]

    comprobar("El token individual no es la credencial de alta",
              token != credencial)

    comprobar("La identidad se deriva del token",
              servidor.get_device_id_for_token(token) == device_id)

    # Un token inventado no identifica a nadie
    comprobar("Un token falso no vale",
              servidor.get_device_id_for_token("inventado") is None)

    # El device_id del cuerpo no manda
    otro = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-SUPLANTA", "operating_system": "Windows",
              "ip_address": "10.0.0.9", "device_id": "otro-cualquiera"},
        headers={"X-Agent-Token": token}
    )

    comprobar("No se puede reclamar otro device_id",
              otro.json().get("device_id") == device_id,
              str(otro.json().get("device_id")))

    conexion = database.get_connection()
    total = conexion.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    conexion.close()

    comprobar("Y no se creo ningun equipo nuevo", total == 1, str(total))

    comprobar("La organizacion sigue siendo la de la credencial",
              servidor.organization_of_device(device_id)
              == escenario["organizacion"])


def test_el_canal_del_agent_exige_el_token_en_cabecera():

    servidor_py = io.open(RAIZ / "backend" / "main.py",
                          encoding="utf-8").read()

    bloque = servidor_py.split('@app.websocket("/ws/agent")', 1)[1]
    bloque = bloque.split(chr(10) + "@app.", 1)[0]

    comprobar("El servidor lee el token de la cabecera",
              'websocket.headers.get("x-agent-token")' in bloque)

    comprobar("Y rechaza antes de aceptar la conexion",
              "await websocket.close(code=1008)" in bloque)

    comprobar("La identidad sale del token, no del cliente",
              "get_device_id_for_token(token)" in bloque)

    # 'query' sale en el comentario que explica por que no va en la
    # URL, y en resolve_query, que es la correlacion de consultas de
    # procesos y servicios: nada que ver con el token.
    comprobar("El token no se lee de los parametros de la URL",
              "query_params" not in bloque
              and "websocket.url" not in bloque)


def test_el_cambio_no_toco_nada_mas():

    preparar()

    cliente = h.cliente(h.OWNER)

    for ruta in ("/api/devices", "/api/alerts", "/api/settings",
                 "/api/users/me", "/api/recordings", "/api/audit",
                 "/api/agent-package"):

        comprobar(f"{ruta} responde",
                  cliente.get(ruta).status_code == 200,
                  str(cliente.get(ruta).status_code))

    comprobar("El backoff sigue en su sitio",
              "def siguiente_espera" in AGENTE
              and "RETRY_MAX_SECONDS" in AGENTE)

    comprobar("Y el contador se reinicia al reconectar",
              "intentos = 0" in AGENTE.split(
                  "async def websocket_connection", 1
              )[1])

    comprobar("Los papeles del Agent no cambiaron",
              'ROLE_SERVICE = "service"' in AGENTE
              and 'ROLE_STANDALONE = "standalone"' in AGENTE)


# ==============================

def main():

    pruebas = [
        test_ws_no_fuerza_ssl,
        test_ws_con_ca_configurada_sigue_sin_ssl,
        test_wss_con_ca_propia_usa_esa_autoridad,
        test_wss_sin_ca_usa_la_validacion_del_sistema,
        test_nunca_se_pasaria_ssl_none_con_wss,
        test_el_contexto_solo_se_pasa_cuando_hay_tls,
        test_no_se_desactiva_la_verificacion_en_ningun_sitio,
        test_la_url_del_websocket_sale_del_esquema,
        test_con_https_la_ca_que_falta_se_dice_a_tiempo,
        test_el_heartbeat_sigue_funcionando,
        test_la_autenticacion_del_agent_sigue_igual,
        test_el_canal_del_agent_exige_el_token_en_cabecera,
        test_el_cambio_no_toco_nada_mas
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
