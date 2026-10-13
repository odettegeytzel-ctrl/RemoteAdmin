"""
Funcionamiento desatendido: identidad, reconexion y arranque solo.

    .venv\\Scripts\\python tests/test_produccion.py

La pregunta que contesta esta suite es una sola: si nadie toca nada
durante un mes, y por el medio se cae internet, se reinicia el
servidor y se reinicia Windows, el equipo sigue siendo el mismo y
vuelve solo.

Lo que depende de infraestructura real (systemd en el servidor, tareas
programadas de Windows, el tunel de Cloudflare) no se puede ejecutar
aqui. Esas comprobaciones miran la configuracion que se va a aplicar,
no el resultado de aplicarla, y se dicen como tales.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import json
import os
import re
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
import backend.packaging as packaging
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeProduccion1!"

URL_A = "https://panel-a.ejemplo.com"
URL_B = "https://panel-b.ejemplo.com"

AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
GUION = io.open(RAIZ / "installer" / "install-agent.ps1",
                encoding="utf-8").read()

escenario = {}


def preparar():

    h.reiniciar()

    servidor.connected_agents.clear()

    os.environ["REMOTEADMIN_PUBLIC_URL"] = URL_A

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


def dar_de_alta(hostname="PC-UNO"):
    """Un alta real, como la que hace el Agent recien instalado."""

    ficha, credencial = enrollment.create_token(escenario["organizacion"])

    respuesta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": hostname, "operating_system": "Windows",
              "ip_address": "10.0.0.5"},
        headers={"X-Agent-Token": credencial}
    )

    datos = respuesta.json()

    return ficha, credencial, datos


def latir(device_id, token):
    return h.cliente().post(
        "/api/devices/heartbeat",
        json={"device_id": device_id, "ip_address": "10.0.0.5"},
        headers={"X-Agent-Token": token}
    )


# ==============================
# 11. CAMBIO DE URL
# ==============================

def test_cambiar_la_url_no_cambia_el_dispositivo():
    """
    URL A -> alta -> device_id X, token Y
    URL B -> el mismo equipo -> device_id X, token Y
    """

    preparar()

    # --- Con la URL A ---
    ficha, credencial, alta = dar_de_alta("PC-MIGRACION")

    device_id = alta["device_id"]
    token = alta["agent_token"]

    comprobar("Con la URL A el equipo se da de alta",
              alta.get("status") != "error" and device_id and token)

    conexion = database.get_connection()
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, "
        "ended_at, duration_sec, size_bytes) "
        "VALUES (?, ?, '2026-10-07T10:00:00+00:00', "
        "'2026-10-07T10:01:00+00:00', 60, 1000)",
        (device_id, f"{device_id}/2026/10/07/rec_1.mp4")
    )
    conexion.commit()
    conexion.close()

    antes = {
        "organizacion": servidor.organization_of_device(device_id),
        "grabaciones": [
            r["path"] for r in _grabaciones_de(device_id)
        ]
    }

    # --- Cambia la direccion publica del servidor ---
    os.environ["REMOTEADMIN_PUBLIC_URL"] = URL_B

    comprobar("La direccion publica cambia en un solo sitio",
              packaging.public_server_url() == URL_B)

    # --- El mismo Agent, con su identidad de siempre ---
    latido = latir(device_id, token)

    comprobar("El mismo token sigue valiendo tras el cambio",
              latido.status_code == 200, str(latido.status_code))

    rerregistro = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-MIGRACION",
              "operating_system": "Windows",
              "ip_address": "10.0.0.5"},
        headers={"X-Agent-Token": token}
    )

    comprobar("El re-registro NO crea un equipo nuevo",
              rerregistro.json().get("device_id") == device_id,
              str(rerregistro.json().get("device_id")))

    conexion = database.get_connection()
    total = conexion.execute(
        "SELECT COUNT(*) FROM devices"
    ).fetchone()[0]
    conexion.close()

    comprobar("Sigue habiendo un solo equipo", total == 1, str(total))

    comprobar("Conserva su organizacion",
              servidor.organization_of_device(device_id)
              == antes["organizacion"])

    comprobar("Conserva sus grabaciones",
              [r["path"] for r in _grabaciones_de(device_id)]
              == antes["grabaciones"])

    comprobar("Y no hace falta ninguna credencial de alta nueva",
              enrollment.organization_for_token(token) is None)


def _grabaciones_de(device_id):

    conexion = database.get_connection()

    filas = [
        dict(r) for r in conexion.execute(
            "SELECT * FROM recordings WHERE device_id = ? ORDER BY id",
            (device_id,)
        )
    ]

    conexion.close()

    return filas


def test_la_url_esta_en_un_solo_sitio():

    comprobar("El servidor la lee de REMOTEADMIN_PUBLIC_URL",
              "REMOTEADMIN_PUBLIC_URL" in io.open(
                  RAIZ / "backend" / "packaging.py", encoding="utf-8"
              ).read())

    # Puede leerla mas de un modulo (mailer.py la usa para los
    # enlaces de recuperacion). Lo que no puede haber es una SEGUNDA
    # variable que diga lo mismo: ahi es donde empiezan a
    # contradecirse.
    alternativas = set()

    for archivo in (RAIZ / "backend").glob("*.py"):

        texto = io.open(archivo, encoding="utf-8").read()

        for nombre in re.findall(
            r'getenv\(\s*"(REMOTEADMIN_[A-Z_]*URL[A-Z_]*)"', texto
        ):
            alternativas.add(nombre)

    comprobar("Hay una sola variable para la direccion publica",
              alternativas == {"REMOTEADMIN_PUBLIC_URL"},
              str(sorted(alternativas)))

    comprobar("Y nadie se inventa una direccion en el codigo",
              not [
                  a for a in (RAIZ / "backend").glob("*.py")
                  if "trycloudflare" in io.open(a, encoding="utf-8").read()
              ])

    comprobar("El Agent la toma de su propia configuracion",
              'os.getenv(' in AGENTE and "REMOTEADMIN_SERVER" in AGENTE)

    comprobar("Y no conoce la del servidor",
              "REMOTEADMIN_PUBLIC_URL" not in AGENTE)


def test_la_identidad_no_depende_de_la_direccion():

    # identity.json guarda device_id y token. Si guardara tambien la
    # direccion, cambiarla invalidaria la identidad.
    bloque = AGENTE.split("def save_identity", 1)[1].split("def ", 1)[0]

    comprobar("identity.json guarda solo identificador y token",
              '"device_id": device_id' in bloque
              and '"agent_token": agent_token' in bloque)

    comprobar("No guarda la direccion del servidor",
              "SERVER_URL" not in bloque and "server" not in bloque.lower())

    comprobar("Y vive fuera del programa, en la carpeta de datos",
              "get_config_dir()" in AGENTE
              and 'IDENTITY_FILE = os.path.join' in AGENTE)


# ==============================
# 5. IDENTIDAD
# ==============================

def test_el_segundo_arranque_reutiliza_la_identidad():

    preparar()

    ficha, credencial, alta = dar_de_alta("PC-REINICIO")

    device_id = alta["device_id"]
    token = alta["agent_token"]

    # Un segundo arranque: el Agent ya tiene identity.json, asi que se
    # presenta con su token individual y no pide alta.
    for vuelta in range(3):

        respuesta = h.cliente().post(
            "/api/devices/register",
            json={"hostname": "PC-REINICIO",
                  "operating_system": "Windows",
                  "ip_address": "10.0.0.5"},
            headers={"X-Agent-Token": token}
        )

        comprobar(f"Arranque {vuelta + 2}: mismo device_id",
                  respuesta.json().get("device_id") == device_id)

    conexion = database.get_connection()
    total = conexion.execute(
        "SELECT COUNT(*) FROM devices"
    ).fetchone()[0]
    conexion.close()

    comprobar("Ningun dispositivo duplicado", total == 1, str(total))


def test_la_credencial_de_alta_se_retira_tras_usarla():

    comprobar("El Agent borra AGENT_TOKEN del .env al enrolarse",
              "def retirar_credencial_de_alta" in AGENTE)

    bloque = AGENTE.split(
        "def retirar_credencial_de_alta", 1
    )[1].split("\\ndef ", 1)[0]

    comprobar("Escribiendo el archivo de forma atomica",
              "os.replace" in bloque)

    comprobar("Y se llama justo despues del alta",
              "if retirar_credencial_de_alta():" in AGENTE)

    comprobar("Un fallo al borrarla no tumba el Agent",
              "except OSError" in bloque)


def test_el_instalador_protege_la_identidad():

    comprobar("Restringe la carpeta config",
              "icacls $ConfigDir" in GUION)

    comprobar("Solo SYSTEM y administradores",
              '"SYSTEM:(F)" "Administrators:(F)"' in GUION)

    comprobar("Y tambien el .env, que lleva la credencial",
              "icacls $EnvFile" in GUION)

    comprobar("La crea antes de arrancar el Agent",
              GUION.index("icacls $ConfigDir")
              < GUION.index("Start-ScheduledTask -TaskName $TaskName"))


def test_actualizar_no_borra_la_identidad():

    comprobar("La reinstalacion solo copia el programa",
              'Copy-Item -Path (Join-Path $SourceDir "agent")' in GUION)

    # Antes se prohibia cualquier Remove-Item. Ahora hay uno
    # legitimo: el archivo temporal de la credencial. Lo que no puede
    # borrarse son los DATOS del equipo, asi que se comprueba QUE se
    # borra y no si la orden aparece.
    borrados = [
        linea.strip() for linea in GUION.split(chr(10))
        if ("Remove-Item" in linea or "rmdir" in linea.lower())
        and not linea.strip().startswith("#")
    ]

    indebidos = [b for b in borrados
                 if "$EnrollmentTokenFile" not in b]

    comprobar("Solo borra el temporal de la credencial",
              not indebidos, str(indebidos))

    for dato in ("config", "identity", "recordings", "logs",
                 "InstallDir"):
        comprobar(f"Ningun borrado alcanza '{dato}'",
                  not [b for b in borrados if dato in b])


    comprobar("Para el Agent antes de sobrescribirlo",
              GUION.index("Stop-ScheduledTask") < GUION.index("Copy-Item"))

    comprobar("Y no vuelve a pedir credencial si ya esta enrolado",
              "$yaEnrolado = Test-Path $IdentityFile" in GUION)


# ==============================
# 9. RECUPERACION
# ==============================

def test_la_espera_entre_reintentos_crece():

    comprobar("Hay una politica de espera creciente",
              "def siguiente_espera" in AGENTE)

    comprobar("Con un tope", "RETRY_MAX_SECONDS" in AGENTE)

    # Se reproduce la formula tal y como esta en el Agent
    base = int(re.search(r"RETRY_BASE_SECONDS = (\d+)", AGENTE).group(1))
    tope = int(re.search(r"RETRY_MAX_SECONDS = (\d+)", AGENTE).group(1))

    def espera(intento):
        return min(base * (2 ** max(0, intento - 1)), tope)

    comprobar("El primer reintento es rapido", espera(1) <= 10,
              str(espera(1)))

    comprobar("Crece al repetirse", espera(4) > espera(2))

    comprobar("Y nunca pasa del tope",
              all(espera(i) <= tope for i in range(1, 40)))

    comprobar("El tope es razonable: no deja el equipo descolgado",
              60 <= tope <= 600, str(tope))

    comprobar("Lleva algo de azar, para que no vuelvan todos a la vez",
              "random.uniform" in AGENTE)


def test_el_contador_se_reinicia_al_reconectar():

    bloque = AGENTE.split("async def websocket_connection", 1)[1]

    comprobar("Al conectar, la cuenta vuelve a cero",
              "intentos = 0" in bloque)

    comprobar("Y sube con cada fallo", "intentos += 1" in bloque)

    comprobar("El WebSocket reintenta para siempre",
              bloque.count("while True:") >= 1)


def test_la_grabacion_sobrevive_a_la_caida_de_la_conexion():

    comprobar("El grabador corre en su propio hilo",
              "threading.Thread" in AGENTE)

    comprobar("Perder el WebSocket no para la grabacion",
              "la grabación LOCAL continúa" in AGENTE
              or "la grabacion LOCAL continua" in AGENTE)

    comprobar("Al reconectar se reintentan las subidas pendientes",
              "_process_pending()" in AGENTE)


def test_un_alta_rechazada_se_explica():
    """Sin esto, una credencial mal copiada reintenta en silencio."""

    comprobar("Se distingue un rechazo de una caida del servidor",
              "respuesta.status_code in (401, 403)" in AGENTE)

    comprobar("Y se dice que revise la credencial",
              "Revisa la credencial" in AGENTE)


# ==============================
# 4 y 6. ARRANQUE DESATENDIDO
# ==============================

def test_el_agent_arranca_solo_y_sin_ventana():

    comprobar("Se registra una tarea programada",
              "Register-ScheduledTask" in GUION)

    comprobar("Que arranca al iniciar sesion",
              "-AtLogOn" in GUION)

    comprobar("Sin ventana de consola",
              "pythonw.exe" in GUION)

    comprobar("Se reinicia sola si el proceso muere",
              "-RestartCount 999" in GUION
              and "-RestartInterval" in GUION)

    comprobar("Sin limite de tiempo de ejecucion",
              "-ExecutionTimeLimit ([TimeSpan]::Zero)" in GUION)

    comprobar("Y no se duplica si ya esta corriendo",
              "-MultipleInstances IgnoreNew" in GUION)

    comprobar("La instalacion la arranca ella misma",
              "Start-ScheduledTask -TaskName $TaskName" in GUION)


def test_la_instalacion_no_deja_pasos_pendientes():

    comprobar("Instala las dependencias",
              "pip install" in GUION)

    comprobar("Espera a confirmar el alta",
              "Esperando el alta en el servidor" in GUION)

    comprobar("Y lo dice cuando termina",
              "No hace falta ningun otro comando" in GUION)

    comprobar("Si no se confirma, dice donde mirar",
              "$LogFile" in GUION)

    comprobar("Nunca imprime el token individual",
              "identidad.agent_token" not in GUION
              and "$identidad.device_id" in GUION)


def test_el_agent_deja_registro_sin_consola():
    """Con pythonw no hay consola: sin archivo, no hay diagnostico."""

    comprobar("Escribe a un archivo de registro",
              "def _abrir_registro" in AGENTE)

    comprobar("Que rota por tamano",
              "RotatingFileHandler" in AGENTE
              and "maxBytes" in AGENTE)

    comprobar("En la carpeta de datos, no en la del programa",
              "get_logs_dir()" in AGENTE)

    comprobar("Y si no se puede, el Agent arranca igual",
              "except Exception:" in AGENTE.split(
                  "def _abrir_registro", 1
              )[1].split("\\nHEARTBEAT", 1)[0])


def test_el_registro_no_guarda_secretos():

    # Ninguna linea que imprima algo puede llevar un token
    sospechosas = []

    for numero, linea in enumerate(AGENTE.split(chr(10)), 1):

        if "print(" not in linea:
            continue

        minuscula = linea.lower()

        for peligro in ("agent_token", "get_agent_device_token",
                        "credencial}", "token}", "identity[",
                        "AGENT_TOKEN"):

            if peligro.lower() in minuscula:
                sospechosas.append(f"{numero}: {linea.strip()[:70]}")

    comprobar("Ninguna linea imprime un token",
              not sospechosas, str(sospechosas))

    comprobar("El token viaja en cabecera, nunca en la URL",
              "additional_headers" in AGENTE
              and "X-Agent-Token" in AGENTE)

    comprobar("Y el servidor tampoco lo acepta por la URL",
              "websocket.headers.get" in io.open(
                  RAIZ / "backend" / "main.py", encoding="utf-8"
              ).read())


# ==============================
# 1, 2 y 13. EL SERVIDOR
# ==============================

def test_la_unidad_del_backend_arranca_sola():

    unidad = io.open(RAIZ / "deploy" / "remoteadmin.service",
                     encoding="utf-8").read()

    comprobar("Arranca con el sistema",
              "WantedBy=multi-user.target" in unidad)

    comprobar("Se reinicia siempre que termine",
              "Restart=always" in unidad)

    comprobar("Con una pausa entre intentos",
              "RestartSec=" in unidad)

    comprobar("Y un limite para no dar vueltas eternamente",
              "StartLimitBurst=" in unidad)

    comprobar("Conserva el EnvironmentFile",
              "EnvironmentFile=" in unidad)

    comprobar("Y el puerto interno actual",
              "--port 8000" in unidad)

    comprobar("Escucha solo en loopback: se entra por Nginx",
              "--host 127.0.0.1" in unidad)

    comprobar("Corre como usuario sin privilegios",
              "User=remoteadmin" in unidad
              and "NoNewPrivileges=true" in unidad)


def test_nginx_deja_pasar_el_websocket():

    configuracion = io.open(RAIZ / "deploy" / "nginx-remoteadmin.conf",
                            encoding="utf-8").read()

    comprobar("HTTP 1.1, que es lo que exige el WebSocket",
              "proxy_http_version 1.1" in configuracion)

    comprobar("Con la cabecera Upgrade",
              "proxy_set_header Upgrade $http_upgrade" in configuracion)

    comprobar("Y Connection: upgrade",
              'proxy_set_header Connection "upgrade"' in configuracion)

    comprobar("Timeout largo: la conexion del Agent esta callada",
              "proxy_read_timeout 3600s" in configuracion)

    comprobar("Y admite subir grabaciones grandes",
              "client_max_body_size" in configuracion)


def test_el_tunel_queda_preparado_sin_inventar_nada():

    plantilla = io.open(RAIZ / "deploy" / "cloudflared-config.yml",
                        encoding="utf-8").read()

    comprobar("Es una plantilla, no una configuracion falsa",
              "<HOSTNAME>" in plantilla and "<ID-DEL-TUNEL>" in plantilla)

    # Solo las lineas de configuracion: los comentarios nombran
    # *.trycloudflare.com justamente para decir que no se usa.
    ajustes = chr(10).join(
        linea for linea in plantilla.split(chr(10))
        if linea.strip() and not linea.strip().startswith("#")
    )

    comprobar("No se inventa ningun dominio en la configuracion",
              ".com" not in ajustes, ajustes)

    comprobar("Apunta a Nginx, no al backend directamente",
              "service: http://127.0.0.1:80" in plantilla)

    comprobar("Con una regla final obligatoria",
              "http_status:404" in plantilla)

    guia = io.open(RAIZ / "deploy" / "README.md",
                   encoding="utf-8").read()

    comprobar("Se dice que el Quick Tunnel no sirve para produccion",
              "no sirve para produccion" in guia.lower())

    comprobar("Y se explica exactamente que falta",
              "Lo que falta, exactamente" in guia)

    comprobar("El tunel queda como servicio, no como comando a mano",
              "cloudflared service install" in guia
              and "systemctl enable --now cloudflared" in guia)


def test_los_tres_servicios_son_independientes():

    unidad = io.open(RAIZ / "deploy" / "remoteadmin.service",
                     encoding="utf-8").read()

    # Encadenarlos haria que la caida de uno arrastrase a los demas.
    # Se miran las directivas, no los comentarios, que los nombran
    # precisamente para explicar por que no estan encadenados.
    directivas = chr(10).join(
        linea for linea in unidad.split(chr(10))
        if linea.strip() and not linea.strip().startswith("#")
    )

    comprobar("El backend no depende de nginx ni de cloudflared",
              "nginx" not in directivas
              and "cloudflared" not in directivas,
              directivas)

    comprobar("Solo espera a que haya red",
              "After=network-online.target" in unidad)


# ==============================
# 14. NADA DE LO ANTERIOR SE ROMPE
# ==============================

def test_lo_de_siempre_sigue_funcionando():

    preparar()

    cliente = h.cliente(h.OWNER)

    for ruta in ("/api/devices", "/api/alerts", "/api/settings",
                 "/api/users/me", "/api/recordings", "/api/audit"):

        comprobar(f"{ruta} responde",
                  cliente.get(ruta).status_code == 200,
                  str(cliente.get(ruta).status_code))

    ficha, credencial, alta = dar_de_alta("PC-REGRESION")

    comprobar("El alta sigue funcionando",
              alta.get("device_id") is not None)

    comprobar("El heartbeat sigue funcionando",
              latir(alta["device_id"], alta["agent_token"]
                    ).status_code == 200)

    comprobar("Y la descarga del instalador tambien",
              cliente.get("/api/agent-package").status_code == 200)

    orgs.update_organization(escenario["organizacion"],
                             subscription_status=orgs.STATUS_SUSPENDED)

    comprobar("La suspension sigue bloqueando el panel",
              cliente.get("/api/devices").status_code == 403)

    comprobar("Pero no al Agent",
              latir(alta["device_id"], alta["agent_token"]
                    ).status_code == 200)


def test_un_agent_no_puede_cambiarse_de_organizacion():

    preparar()

    otra = orgs.create_organization("Otra", plan=orgs.PLAN_PRO,
                                    subscription_status=orgs.STATUS_ACTIVE)

    ficha, credencial = enrollment.create_token(
        escenario["organizacion"]
    )

    alta = h.cliente().post(
        "/api/devices/register",
        json={"hostname": "PC-COLADA", "operating_system": "Windows",
              "ip_address": "10.0.0.7",
              "organization_id": otra["id"]},
        headers={"X-Agent-Token": credencial}
    )

    comprobar("El organization_id del cuerpo se ignora",
              servidor.organization_of_device(alta.json()["device_id"])
              == escenario["organizacion"])


# ==============================

def main():

    pruebas = [
        test_cambiar_la_url_no_cambia_el_dispositivo,
        test_la_url_esta_en_un_solo_sitio,
        test_la_identidad_no_depende_de_la_direccion,
        test_el_segundo_arranque_reutiliza_la_identidad,
        test_la_credencial_de_alta_se_retira_tras_usarla,
        test_el_instalador_protege_la_identidad,
        test_actualizar_no_borra_la_identidad,
        test_la_espera_entre_reintentos_crece,
        test_el_contador_se_reinicia_al_reconectar,
        test_la_grabacion_sobrevive_a_la_caida_de_la_conexion,
        test_un_alta_rechazada_se_explica,
        test_el_agent_arranca_solo_y_sin_ventana,
        test_la_instalacion_no_deja_pasos_pendientes,
        test_el_agent_deja_registro_sin_consola,
        test_el_registro_no_guarda_secretos,
        test_la_unidad_del_backend_arranca_sola,
        test_nginx_deja_pasar_el_websocket,
        test_el_tunel_queda_preparado_sin_inventar_nada,
        test_los_tres_servicios_son_independientes,
        test_lo_de_siempre_sigue_funcionando,
        test_un_agent_no_puede_cambiarse_de_organizacion
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
