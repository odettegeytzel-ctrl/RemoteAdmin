"""
Recuperacion por correo e instalador grafico.

    .venv\\Scripts\\python tests/test_correo_e_instalador.py

Dos funciones que comparten una idea: el fallo mas probable no es que
el codigo este mal, sino que la configuracion parezca correcta sin
serlo. La recuperacion puede descartar correos en silencio, y el
instalador puede dejar un equipo a medias si el servidor no responde.

Base temporal y datos inventados. No se toca produccion.
"""

import io
import json
import os
import shutil
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))
sys.path.insert(0, str(RAIZ / "installer"))

import users_harness as h

import backend.database as database
import backend.mailer as mailer
import backend.main as servidor
import backend.organizations as orgs
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDeCorreo1!"

CONSOLA = io.open(RAIZ / "platform_admin.py", encoding="utf-8").read()
GUION = io.open(RAIZ / "installer" / "install-agent.ps1",
                encoding="utf-8").read()
CONSTRUCTOR = io.open(RAIZ / "installer" / "build-installer.ps1",
                      encoding="utf-8").read()
INTERFAZ = io.open(RAIZ / "installer" / "veyra_installer.py",
                   encoding="utf-8").read()


def preparar():

    h.reiniciar()

    mailer.reset_mock()

    os.environ["REMOTEADMIN_MAIL_TRANSPORT"] = "mock"
    os.environ["REMOTEADMIN_PUBLIC_URL"] = "https://panel.ejemplo.com"

    conexion = database.get_connection()
    for tabla in ("devices", "alerts", "audit_log", "enrollment_tokens",
                  "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")
    conexion.commit()
    conexion.close()

    organizacion = orgs.create_organization(
        "Empresa", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, organizacion["id"])

    return organizacion["id"]


# ==============================
# RECUPERACION POR CORREO
# ==============================

def test_el_flujo_completo_funciona():

    preparar()

    users.set_email(h.OWNER, "duena@ejemplo.com")

    cliente = h.cliente()

    # 1. Se pide el enlace
    peticion = cliente.post("/api/auth/forgot",
                            json={"email": "duena@ejemplo.com"})

    comprobar("Se acepta la solicitud", peticion.status_code == 200)

    comprobar("Se envio un correo", len(mailer.sent_messages) == 1,
              str(len(mailer.sent_messages)))

    mensaje = mailer.sent_messages[0]

    # 2. El enlace lleva el token
    import re

    # El enlace lleva el token en el fragmento: #reset=...
    encaje = re.search(r"#reset=([A-Za-z0-9_\-]+)", mensaje["body"])

    comprobar("El correo lleva un enlace con token", encaje is not None)

    if not encaje:
        return

    token = encaje.group(1)

    # 3. Se comprueba el token antes de pedir la contrasena
    revision = cliente.post("/api/auth/reset/check", json={"token": token})

    comprobar("El token se valida antes de usarlo",
              revision.status_code == 200
              and revision.json().get("status") == "ok",
              str(revision.json()))

    # 4. Se establece la contrasena nueva
    nueva = "ClaveNuevaDeVerdad9!"

    cambio = cliente.post("/api/auth/reset",
                          json={"token": token, "new_password": nueva})

    comprobar("Se establece la contrasena nueva",
              cambio.status_code == 200, str(cambio.status_code))

    # 5. Ya sirve para entrar
    entrada = h.cliente().post("/api/auth/login",
                               json={"username": h.OWNER,
                                     "password": nueva})

    comprobar("La contrasena nueva permite entrar",
              entrada.status_code == 200, str(entrada.status_code))

    # 6. El enlace no se puede reutilizar
    repetido = h.cliente().post("/api/auth/reset",
                                json={"token": token,
                                      "new_password": "OtraMas9!"})

    comprobar("El enlace no se puede usar dos veces",
              repetido.status_code != 200, str(repetido.status_code))


def test_no_se_revela_que_correos_existen():
    """
    La respuesta es la misma exista o no la direccion.

    Si no, el formulario serviria para averiguar quien tiene cuenta.
    """

    preparar()

    users.set_email(h.OWNER, "duena@ejemplo.com")

    cliente = h.cliente()

    conocido = cliente.post("/api/auth/forgot",
                            json={"email": "duena@ejemplo.com"})

    mailer.reset_mock()

    desconocido = cliente.post("/api/auth/forgot",
                               json={"email": "nadie@ejemplo.com"})

    comprobar("Mismo codigo de estado",
              conocido.status_code == desconocido.status_code)

    comprobar("Mismo cuerpo de respuesta",
              conocido.json() == desconocido.json(),
              f"{conocido.json()} vs {desconocido.json()}")

    comprobar("Y con una direccion desconocida no se envia nada",
              len(mailer.sent_messages) == 0)


def test_un_usuario_sin_correo_no_puede_recuperar():
    """Y el flujo no lo avisa: de ahi que haga falta el diagnostico."""

    preparar()

    sin_correo = users.get_user(h.OWNER)

    comprobar("El usuario de prueba no tiene correo",
              not sin_correo.get("email"))

    mailer.reset_mock()

    respuesta = h.cliente().post("/api/auth/forgot",
                                 json={"email": "cualquiera@ejemplo.com"})

    comprobar("La respuesta es de exito igualmente",
              respuesta.status_code == 200)

    comprobar("Pero no se envio ningun correo",
              len(mailer.sent_messages) == 0)


def test_el_correo_no_lleva_ni_registra_secretos():

    preparar()

    users.set_email(h.OWNER, "duena@ejemplo.com")

    h.cliente().post("/api/auth/forgot",
                     json={"email": "duena@ejemplo.com"})

    mensaje = mailer.sent_messages[0]

    comprobar("El correo no lleva la contrasena ni su huella",
              "pbkdf2" not in mensaje["body"]
              and "password_hash" not in mensaje["body"])

    # En la auditoria queda que se pidio, nunca el enlace
    conexion = database.get_connection()

    filas = [
        dict(r) for r in conexion.execute(
            "SELECT * FROM audit_log WHERE action LIKE '%recover%' "
            "OR action LIKE '%forgot%' OR action LIKE '%reset%'"
        )
    ]

    conexion.close()

    texto = str(filas)

    comprobar("La auditoria no guarda el token del enlace",
              "token=" not in texto)

    comprobar("Ni el cuerpo del mensaje",
              "restablecer" not in texto.lower()
              or "token" not in texto.lower())

    modulo = io.open(RAIZ / "backend" / "mailer.py",
                     encoding="utf-8").read()

    comprobar("El modulo documenta que no registra el contenido",
              "ni el cuerpo ni el enlace aparecen en ningun log"
              in modulo.lower())


def test_el_enlace_no_usa_la_cabecera_host():
    """
    Si la usara, bastaria con falsificarla para que el enlace
    apuntara al sitio de quien ataca.
    """

    modulo = io.open(RAIZ / "backend" / "mailer.py",
                     encoding="utf-8").read()

    comprobar("La base del enlace sale de la configuracion",
              'REMOTEADMIN_PUBLIC_URL' in modulo)

    comprobar("Y se documenta por que no se usa la cabecera Host",
              "NUNCA de la cabecera Host" in modulo)

    bloque = modulo.split("def get_public_url", 1)[1].split(
        chr(10) + "def ", 1
    )[0]

    comprobar("La funcion no mira ninguna peticion",
              "request" not in bloque and "headers" not in bloque)


# ==============================
# EL DIAGNOSTICO DE CORREO
# ==============================

def test_la_consola_permite_asignar_correo():
    """Sin esto no habia forma de que un usuario pudiera recuperar."""

    comprobar("Existe la orden 'correo'",
              'if orden == "correo" and len(argumentos) == 3:' in CONSOLA)

    comprobar("Y llama a users.set_email",
              "users.set_email(username, direccion)" in CONSOLA)

    comprobar("El listado muestra quien no tiene correo",
              '"SIN CORREO"' in CONSOLA)

    comprobar("Avisa si el envio esta en modo mock",
              "los enlaces de recuperacion NO saldran" in CONSOLA)

    comprobar("Y queda en la auditoria",
              '"user.email_set"' in CONSOLA)


def test_el_diagnostico_detecta_lo_que_falta():

    comprobar("Existe la orden de diagnostico",
              "def correo_diagnostico" in CONSOLA)

    for problema in ("El transporte es 'mock'",
                     "Falta ",
                     "apunta al propio servidor",
                     "usuarios activos sin correo"):

        comprobar(f"Detecta: {problema.strip()}", problema in CONSOLA)

    comprobar("No imprime la contrasena del SMTP",
              "print" not in CONSOLA.split(
                  "REMOTEADMIN_SMTP_PASSWORD", 1
              )[1].split(chr(10))[0])

    comprobar("Solo dice si cada variable esta definida",
              "'definida' if puesta else 'sin definir'" in CONSOLA)


def test_el_modo_mock_no_envia_nada_de_verdad():

    os.environ["REMOTEADMIN_MAIL_TRANSPORT"] = "mock"

    comprobar("El transporte por defecto es mock",
              mailer.get_transport() == mailer.TRANSPORT_MOCK)

    # is_configured() dice True en mock: el diagnostico existe
    # precisamente porque esa funcion no distingue los dos casos.
    comprobar("is_configured() no distingue mock de smtp",
              mailer.is_configured() is True)

    comprobar("Por eso el diagnostico lo comprueba aparte",
              "transporte == mailer.TRANSPORT_MOCK" in CONSOLA)


def test_la_documentacion_de_smtp_existe():

    ruta = RAIZ / "docs" / "configurar-correo.md"

    comprobar("Hay documentacion de la configuracion", ruta.is_file())

    if not ruta.is_file():
        return

    texto = io.open(ruta, encoding="utf-8").read()

    for variable in ("REMOTEADMIN_MAIL_TRANSPORT",
                     "REMOTEADMIN_SMTP_HOST",
                     "REMOTEADMIN_SMTP_PORT",
                     "REMOTEADMIN_SMTP_USER",
                     "REMOTEADMIN_SMTP_PASSWORD",
                     "REMOTEADMIN_MAIL_FROM",
                     "REMOTEADMIN_PUBLIC_URL"):

        comprobar(f"Documenta {variable}", variable in texto)

    comprobar("Explica que en mock no se envia nada",
              "descarta el correo" in texto.lower()
              or "no se envía nada" in texto.lower())

    comprobar("Y como comprobarlo",
              "correo-diagnostico" in texto)

    comprobar("No lleva ninguna contrasena de ejemplo real",
              "REMOTEADMIN_SMTP_PASSWORD=..." in texto)


# ==============================
# EL INSTALADOR GRAFICO
# ==============================

def test_la_interfaz_no_lleva_credenciales():

    comprobar("No hay ninguna credencial de alta dentro",
              "rae_" not in INTERFAZ.replace(
                  'PREFIJO_CREDENCIAL = "rae_"', ""
              ).replace("PREFIJO_CREDENCIAL", ""))

    comprobar("Ni contrasenas ni tokens",
              "password" not in INTERFAZ.lower()
              and "agent_token" not in INTERFAZ)

    comprobar("No lee el token individual del equipo",
              '"agent_token"' not in INTERFAZ)

    bloque = INTERFAZ.split("def identidad_existente", 1)[1].split(
        chr(10) + "def ", 1
    )[0]

    comprobar("De identity.json solo toma el identificador",
              'datos.get("device_id")' in bloque
              and "agent_token" not in bloque)

    comprobar("Y se documenta por que",
              "NO se lee" in bloque)


def test_la_credencial_no_pasa_por_la_linea_de_ordenes():
    """Un argumento se ve en la lista de procesos de todo el equipo."""

    comprobar("Se pasa en un archivo, no como argumento",
              "-EnrollmentTokenFile" in INTERFAZ)

    comprobar("Nunca con -EnrollmentToken",
              '"-EnrollmentToken",' not in INTERFAZ)

    comprobar("El archivo se restringe con icacls",
              "icacls" in INTERFAZ)

    comprobar("Se sobrescribe antes de borrarlo",
              "def _borrar" in INTERFAZ
              and 'b"0" * largo' in INTERFAZ)

    # Y el script lo lee y lo destruye
    comprobar("El script acepta el archivo",
              "$EnrollmentTokenFile" in GUION)

    comprobar("Lo sobrescribe y lo borra al leerlo",
              '"0" * 256' in GUION
              and "Remove-Item -Path $EnrollmentTokenFile" in GUION)

    comprobar("Y se explica el motivo",
              "lista de procesos" in GUION)


def test_no_se_elude_el_aviso_de_windows():

    comprobar("La elevacion se pide con el verbo runas",
              'info.lpVerb = "runas"' in INTERFAZ)

    comprobar("Si se rechaza, no se instala",
              "Windows no concedio los permisos" in INTERFAZ)

    for truco in ("schtasks /create /ru SYSTEM", "ms-settings:",
                  "fodhelper", "ComputerDefaults",
                  "EnableLUA", "ConsentPromptBehavior"):
        comprobar(f"No usa '{truco}'", truco not in INTERFAZ)

    comprobar("Y se documenta que no se elude",
              "No elude el aviso de Windows" in INTERFAZ)

    comprobar("El .exe no se marca como requiere-administrador",
              "--uac-admin" not in CONSTRUCTOR.replace(
                  "# --uac-admin", ""
              ))


def test_se_comprueba_el_servidor_antes_de_instalar():

    import veyra_installer as vi

    # Casos que deben rechazarse sin tocar el equipo
    for url, razon in (
        ("", "vacia"),
        ("ftp://servidor", "esquema invalido"),
        ("servidor.ejemplo.com", "sin esquema"),
    ):
        correcto, mensaje = vi.comprobar_servidor(url)

        comprobar(f"Se rechaza una direccion {razon}",
                  not correcto, mensaje)

    comprobar("La validacion del certificado queda ACTIVADA",
              "ssl.create_default_context()" in INTERFAZ)

    for insegura in ("verify=False", "CERT_NONE",
                     "_create_unverified_context",
                     "check_hostname = False"):
        comprobar(f"No usa {insegura}", insegura not in INTERFAZ)

    comprobar("Un certificado invalido se explica, no se ignora",
              "SSLCertVerificationError" in INTERFAZ
              and "no seria de fiar" in INTERFAZ)

    comprobar("Y si el servidor no responde, no se instala nada",
              "No se instalo nada." in INTERFAZ)


def test_se_conserva_la_identidad_de_un_equipo_ya_dado_de_alta():

    comprobar("Se detecta si ya hay identidad",
              "self.ya_instalado = identidad_existente()" in INTERFAZ)

    comprobar("Y entonces no se pide credencial",
              "if not self.ya_instalado:" in INTERFAZ)

    plano = " ".join(INTERFAZ.split())

    comprobar("Se dice que conservara su identidad",
              "conserva su" in plano and "identidad" in plano
              and "no hace falta credencial" in plano)

    comprobar("El boton cambia a 'Actualizar'",
              '"Actualizar" if self.ya_instalado else "Instalar"'
              in INTERFAZ)

    # Y el script, por su parte, tampoco la vuelve a pedir
    comprobar("El script conserva identity.json",
              "$yaEnrolado = Test-Path $IdentityFile" in GUION)

    # Lo que SI puede borrar el instalador, y por que:
    #
    #   $EnrollmentTokenFile  el temporal de la credencial, que no
    #                         debe quedar recuperable en disco
    #   $RuntimeDestino       el interprete de una instalacion
    #                         anterior, antes de copiar el nuevo. Es
    #                         una carpeta propia, hermana de config,
    #                         recordings, logs y .env: no toca
    #                         ninguna
    #
    # La lista es explicita a proposito: cualquier otro borrado en el
    # instalador tiene que pasar por aqui y justificarse.
    borrados = [
        linea.strip() for linea in GUION.split(chr(10))
        if "Remove-Item" in linea and not linea.strip().startswith("#")
    ]

    PERMITIDOS = ("$EnrollmentTokenFile", "$RuntimeDestino")

    indebidos = [b for b in borrados
                 if not any(x in b for x in PERMITIDOS)]

    comprobar("Y no borra la carpeta de datos",
              not indebidos, str(indebidos))


def test_la_interfaz_usa_el_script_probado():
    """Dos implementaciones de la instalacion se desincronizarian."""

    comprobar("Invoca install-agent.ps1",
              '"install-agent.ps1"' in INTERFAZ
              and "carpeta_del_paquete()" in INTERFAZ)

    comprobar("No reimplementa el registro de tareas",
              "Register-ScheduledTask" not in INTERFAZ
              and "schtasks /create" not in INTERFAZ.lower())

    comprobar("Ni la instalacion de dependencias",
              "pip install" not in INTERFAZ)

    comprobar("Avisa si falta el script",
              "No se encontro el instalador" in INTERFAZ)

    comprobar("Y se documenta la razon",
              "daria dos versiones de la misma" in INTERFAZ)


def _instalar_con(resultado, credencial="rae_PRUEBA_NO_REAL_abcdef"):
    """
    Recorre instalar() sustituyendo la elevacion de permisos.

    Asi se prueban todos los caminos de error sin instalar nada en
    este equipo: `resultado` es lo que devolveria la elevacion (0, un
    codigo de error, None para UAC cancelado) o una excepcion.
    """

    import veyra_installer as vi

    visto = {}

    original = vi._lanzar_elevado

    def sustituto(programa, argumentos):

        visto["argumentos"] = argumentos

        if "-EnrollmentTokenFile" in argumentos:
            ruta = argumentos[
                argumentos.index("-EnrollmentTokenFile") + 1
            ]
            visto["ruta"] = ruta
            visto["contenido"] = io.open(ruta, encoding="utf-8").read()

        if isinstance(resultado, Exception):
            raise resultado

        return resultado

    vi._lanzar_elevado = sustituto

    try:
        correcto, mensaje = vi.instalar(
            "https://remoteadmin.tail37c528.ts.net", credencial,
            lambda t: None
        )

    finally:
        vi._lanzar_elevado = original

    visto["correcto"] = correcto
    visto["mensaje"] = mensaje

    return visto


def test_el_temporal_se_limpia_en_todos_los_caminos():
    """
    Lo importante no es el camino feliz, sino los que no lo son: si un
    camino de error no limpia, la credencial se queda en disco.
    """

    import veyra_installer as vi

    escenarios = (
        ("instalacion correcta", 0),
        ("UAC cancelado", None),
        ("la instalacion falla", 1),
        ("excepcion al elevar", RuntimeError("fallo simulado")),
    )

    for etiqueta, resultado in escenarios:

        visto = _instalar_con(resultado)

        comprobar(f"{etiqueta}: el script habria leido la credencial",
                  visto.get("contenido") == "rae_PRUEBA_NO_REAL_abcdef")

        comprobar(f"{etiqueta}: el temporal queda borrado",
                  not os.path.isfile(visto["ruta"]), visto["ruta"])

        comprobar(f"{etiqueta}: no deja pendientes",
                  len(vi._pendientes) == 0, str(vi._pendientes))


def test_la_credencial_no_aparece_en_la_linea_de_ordenes():

    import subprocess

    visto = _instalar_con(0)

    argumentos = visto["argumentos"]

    comprobar("Ningun argumento es la credencial",
              not any("rae_PRUEBA" in str(a) for a in argumentos),
              str(argumentos))

    comprobar("Ni aparece al montar la linea completa",
              "rae_PRUEBA" not in subprocess.list2cmdline(argumentos))

    comprobar("Lo que se pasa es una ruta",
              "-EnrollmentTokenFile" in argumentos)


def test_cancelar_uac_no_cambia_nada():

    visto = _instalar_con(None)

    comprobar("No se da por instalado", visto["correcto"] is False)

    comprobar("Se explica que faltan permisos",
              "no concedio los permisos" in visto["mensaje"])

    comprobar("Y se dice que el equipo quedo intacto",
              "no se ha cambiado nada" in visto["mensaje"].lower())


def test_una_instalacion_fallida_remite_al_registro():

    visto = _instalar_con(1)

    comprobar("No se da por instalado", visto["correcto"] is False)

    comprobar("Remite al registro del Agent",
              "agent-service.log" in visto["mensaje"])


def test_actualizar_no_crea_ningun_temporal():
    """Sin credencial no hay nada que traspasar."""

    import veyra_installer as vi

    visto = _instalar_con(0, credencial="")

    comprobar("La instalacion se da por correcta",
              visto["correcto"] is True)

    comprobar("No se pasa ninguna ruta de credencial",
              "-EnrollmentTokenFile" not in visto["argumentos"],
              str(visto["argumentos"]))

    comprobar("Y no queda ningun temporal pendiente",
              len(vi._pendientes) == 0)


def test_no_se_entrega_un_enlace_al_proceso_con_permisos():
    """
    La ruta se la damos a un proceso mas privilegiado que nosotros.

    Si ahi hubiera un enlace o una union de directorios, leeria lo que
    apunte el enlace en vez del archivo que se escribio.
    """

    import subprocess
    import veyra_installer as vi

    carpeta = tempfile.mkdtemp(prefix="veyra-enlaces-")

    try:

        normal = os.path.join(carpeta, "normal.tmp")
        io.open(normal, "w", encoding="utf-8").write("rae_corta")

        comprobar("Un archivo normal se acepta",
                  vi._es_archivo_normal(normal))

        grande = os.path.join(carpeta, "grande.tmp")
        io.open(grande, "w", encoding="utf-8").write(
            "A" * (vi.MAX_CREDENCIAL_BYTES + 10)
        )

        comprobar("Un archivo demasiado grande se rechaza",
                  not vi._es_archivo_normal(grande))

        comprobar("Una carpeta se rechaza",
                  not vi._es_archivo_normal(carpeta))

        comprobar("Una ruta inexistente se rechaza",
                  not vi._es_archivo_normal(
                      os.path.join(carpeta, "no-existe")
                  ))

        # Union de directorios: no exige permisos de administrador
        destino = os.path.join(carpeta, "destino")
        os.makedirs(destino)

        union = os.path.join(carpeta, "union")

        hecho = subprocess.run(
            ["cmd", "/c", "mklink", "/J", union, destino],
            capture_output=True, text=True
        )

        if hecho.returncode == 0:
            comprobar("Una union de directorios se rechaza",
                      not vi._es_archivo_normal(union))
        else:
            comprobar("Una union de directorios se rechaza", False,
                      "no se pudo crear la union para probarlo")

    finally:
        shutil.rmtree(carpeta, ignore_errors=True)

    # Y el script tambien lo comprueba por su cuenta
    comprobar("El script rechaza un enlace",
              "ReparsePoint" in GUION
              and "es un enlace" in GUION)

    comprobar("Rechaza una carpeta",
              "PSIsContainer" in GUION)

    comprobar("Y limita el tamano",
              "$archivo.Length -gt 4096" in GUION)

    comprobar("Lee por ruta literal, sin expandir comodines",
              "-LiteralPath $EnrollmentTokenFile" in GUION)


def test_la_limpieza_cubre_la_salida_del_programa():

    import veyra_installer as vi

    descriptor, huerfano = tempfile.mkstemp(prefix="veyra-huerfano-")
    os.write(descriptor, b"rae_QUEDA_SUELTO")
    os.close(descriptor)

    vi._pendientes.add(huerfano)

    comprobar("Se registra como pendiente",
              huerfano in vi._pendientes)

    vi._limpiar_pendientes()

    comprobar("La limpieza lo borra", not os.path.isfile(huerfano))

    comprobar("Y queda registrada al salir del programa",
              "atexit.register(_limpiar_pendientes)" in INTERFAZ)

    comprobar("Y al cerrar la ventana",
              'raiz.protocol("WM_DELETE_WINDOW", self.cerrar)' in INTERFAZ)

    comprobar("instalar() limpia en un finally",
              "finally:" in INTERFAZ.split("def instalar(", 1)[1].split(
                  chr(10) + "def ", 1
              )[0])


def test_no_se_promete_un_borrado_fisico_garantizado():
    """
    Sobrescribir reduce la ventana, no garantiza la destruccion: en
    SSD y con journaling los bytes pueden sobrevivir. Decir lo
    contrario daria una seguridad falsa.
    """

    comprobar("La interfaz lo advierte",
              "NO garantiza un borrado fisico" in INTERFAZ)

    comprobar("Y menciona SSD y journaling",
              "SSD" in INTERFAZ and "journaling" in INTERFAZ)

    comprobar("Y dice donde esta la garantia de verdad",
              "caduca" in INTERFAZ and "revocar" in INTERFAZ)

    comprobar("El script tambien lo advierte",
              "NO garantiza un borrado fisico" in GUION)


def test_el_exe_corresponde_al_fuente_actual():
    """
    Un .exe no permite comprobar por dentro de que codigo salio, asi
    que la construccion deja constancia de la huella del fuente.
    """

    import hashlib
    import re

    constancia = RAIZ / "installer" / "dist" / "VEYRA-Instalador.sha256.txt"

    comprobar("El constructor deja constancia de la huella del fuente",
              "HuellaFuente" in CONSTRUCTOR
              and "VEYRA-Instalador.sha256.txt" in CONSTRUCTOR)

    if not constancia.is_file():
        comprobar("Hay un .exe construido", False,
                  "falta installer/dist (se construye con "
                  "build-installer.ps1)")
        return

    texto = io.open(constancia, encoding="utf-8-sig").read()

    encaje = re.search(r"fuente SHA256:\s*([0-9A-Fa-f]+)", texto)

    comprobar("La constancia anota la huella del fuente",
              encaje is not None)

    if not encaje:
        return

    actual = hashlib.sha256(
        open(RAIZ / "installer" / "veyra_installer.py", "rb").read()
    ).hexdigest()

    comprobar("El .exe construido corresponde al fuente actual",
              encaje.group(1).lower() == actual,
              f"anotado {encaje.group(1)[:16]}... vs actual "
              f"{actual[:16]}...")

    exe = RAIZ / "installer" / "dist" / "VEYRA-Instalador.exe"

    if exe.is_file():
        comprobar("Y es posterior al fuente",
                  exe.stat().st_mtime >= (
                      RAIZ / "installer" / "veyra_installer.py"
                  ).stat().st_mtime)


def test_los_identificadores_tecnicos_no_cambiaron():
    """El cambio de marca no debe romper los equipos ya instalados."""

    comprobar("La carpeta de datos sigue siendo RemoteAdmin",
              '"RemoteAdmin"' in INTERFAZ
              and "ProgramData" in INTERFAZ)

    comprobar("La tarea sigue llamandose RemoteAdminAgent",
              'TAREA_SERVICIO = "RemoteAdminAgent"' in INTERFAZ)

    comprobar("Y se explica por que no cambian",
              "identificador tecnico" in INTERFAZ)

    # Lo visible si usa el nombre comercial
    comprobar("La ventana muestra VEYRA",
              'PRODUCTO = "VEYRA"' in INTERFAZ)

    comprobar("Y la empresa NYRA",
              'EMPRESA = "NYRA"' in INTERFAZ)

    # El Agent y sus rutas no se tocaron
    paths = io.open(RAIZ / "agent" / "paths.py", encoding="utf-8").read()

    comprobar("agent/paths.py conserva APP_NAME",
              'APP_NAME = "RemoteAdmin"' in paths)

    ipc = io.open(RAIZ / "agent" / "ipc.py", encoding="utf-8").read()

    comprobar("El canal local conserva su nombre",
              r"\\.\pipe\RemoteAdmin.Agent" in ipc)

    main = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()

    comprobar("La cookie de sesion no cambio",
              'AUTH_COOKIE_NAME = "remoteadmin_token"' in main)


def test_el_constructor_del_exe_es_correcto():

    comprobar("Produce un solo archivo", "--onefile" in CONSTRUCTOR)

    comprobar("Sin ventana de consola", "--noconsole" in CONSTRUCTOR)

    comprobar("Con el nombre comercial",
              '--name "VEYRA-Instalador"' in CONSTRUCTOR)

    comprobar("Comprueba que PyInstaller este instalado",
              "import PyInstaller" in CONSTRUCTOR)

    comprobar("E informa del SHA-256 de lo construido",
              "Get-FileHash" in CONSTRUCTOR)

    # Esta comprobacion decia lo contrario hasta que el instalador
    # paso a ser UN SOLO ARCHIVO. Antes el .exe se ponia junto al
    # paquete descomprimido; ahora lo lleva dentro, que es lo que
    # permite descargarlo del panel y ejecutarlo sin nada mas.
    comprobar("Empaqueta el Agent dentro del .exe",
              '--add-data "$Agente;agent"' in CONSTRUCTOR)

    comprobar("Y el interprete, para no exigir Python en el equipo",
              '--add-data "$Runtime;runtime"' in CONSTRUCTOR)

    comprobar("Se niega a construir sin el interprete",
              "Falta el Python embebido" in CONSTRUCTOR)

    # Lo que NO puede empaquetar
    comprobar("No mete el .env en el ejecutable",
              ".env;" not in CONSTRUCTOR)

    comprobar("Ni ninguna clave",
              ".pem" not in CONSTRUCTOR and ".key" not in CONSTRUCTOR)

    dev = io.open(RAIZ / "requirements-dev.txt", encoding="utf-8").read()

    comprobar("PyInstaller esta declarado como dependencia de desarrollo",
              "pyinstaller==" in dev)

    comprobar("Y solo ahi, no en el servidor ni en el Agent",
              "pyinstaller" not in io.open(
                  RAIZ / "requirements-server.txt", encoding="utf-8"
              ).read().lower()
              and "pyinstaller" not in io.open(
                  RAIZ / "requirements-agent-windows.txt",
                  encoding="utf-8"
              ).read().lower())


def test_las_pruebas_tienen_sus_dependencias():
    """
    Al mover cryptography fuera de requirements-base, las pruebas
    quedaron sin ella: test_actualizacion la importa.
    """

    dev = io.open(RAIZ / "requirements-dev.txt", encoding="utf-8").read()

    comprobar("cryptography esta en requirements-dev",
              "cryptography==" in dev)

    comprobar("Y se explica por que hace falta ahi",
              "no pasaria en una maquina limpia" in dev)


# ==============================
# NADA DE LO ANTERIOR SE ROMPE
# ==============================

def test_lo_de_siempre_sigue_en_pie():

    preparar()

    cliente = h.cliente(h.OWNER)

    for ruta in ("/api/devices", "/api/alerts", "/api/settings",
                 "/api/users/me", "/api/recordings", "/api/audit",
                 "/api/agent-package"):

        comprobar(f"{ruta} responde",
                  cliente.get(ruta).status_code == 200,
                  str(cliente.get(ruta).status_code))

    comprobar("El login sigue funcionando",
              h.cliente().post("/api/auth/login",
                               json={"username": h.OWNER,
                                     "password": h.CLAVE_OWNER}
                               ).status_code == 200)


def main():

    pruebas = [
        test_el_flujo_completo_funciona,
        test_no_se_revela_que_correos_existen,
        test_un_usuario_sin_correo_no_puede_recuperar,
        test_el_correo_no_lleva_ni_registra_secretos,
        test_el_enlace_no_usa_la_cabecera_host,
        test_la_consola_permite_asignar_correo,
        test_el_diagnostico_detecta_lo_que_falta,
        test_el_modo_mock_no_envia_nada_de_verdad,
        test_la_documentacion_de_smtp_existe,
        test_la_interfaz_no_lleva_credenciales,
        test_la_credencial_no_pasa_por_la_linea_de_ordenes,
        test_no_se_elude_el_aviso_de_windows,
        test_se_comprueba_el_servidor_antes_de_instalar,
        test_se_conserva_la_identidad_de_un_equipo_ya_dado_de_alta,
        test_la_interfaz_usa_el_script_probado,
        test_el_temporal_se_limpia_en_todos_los_caminos,
        test_la_credencial_no_aparece_en_la_linea_de_ordenes,
        test_cancelar_uac_no_cambia_nada,
        test_una_instalacion_fallida_remite_al_registro,
        test_actualizar_no_crea_ningun_temporal,
        test_no_se_entrega_un_enlace_al_proceso_con_permisos,
        test_la_limpieza_cubre_la_salida_del_programa,
        test_no_se_promete_un_borrado_fisico_garantizado,
        test_el_exe_corresponde_al_fuente_actual,
        test_los_identificadores_tecnicos_no_cambiaron,
        test_el_constructor_del_exe_es_correcto,
        test_las_pruebas_tienen_sus_dependencias,
        test_lo_de_siempre_sigue_en_pie
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
