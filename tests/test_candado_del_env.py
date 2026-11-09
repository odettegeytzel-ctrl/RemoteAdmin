r"""
El candado del .env no sobrevive al secreto que protegia.

    .venv\Scripts\python tests/test_candado_del_env.py

El instalador cierra C:\ProgramData\RemoteAdmin\.env a SYSTEM y a los
administradores, y hace bien: al nacer el archivo lleva la credencial
de ALTA de la organizacion, con la que cualquiera podria dar de alta
mas equipos.

Pero en cuanto el equipo se enrola, esa credencial se borra y lo que
queda en el .env es la direccion del servidor. El candado se quedaba
puesto para siempre, y el ayudante -que corre como el usuario de la
sesion, no como administrador- reventaba al importar el modulo:

    agent/agent.py:66  load_dotenv(ENV_PATH)
    PermissionError: [Errno 13] Permission denied: '...\.env'

Moria antes de poder escribir en su propio registro de arranque, asi
que no dejaba rastro. Y explica el sintoma que costo tres dias: en
unas maquinas si y en otras no, segun si quien usa el equipo es
administrador. El equipo se quedaba sin escritorio -sin pantalla y
sin grabacion- por un archivo que el ayudante ni necesita.

Dos mitades, y las dos se prueban aqui:

  se suelta el candado     cuando el .env ya no guarda la credencial,
                           y SOLO entonces

  no mata el arranque      un .env ilegible se anota y se sigue, en
                           vez de tumbar el proceso

La segunda es la red de seguridad: si la primera falla en algun
equipo, el ayudante arranca igual.

Las funciones se sacan del fuente con ast en vez de importar
agent.agent, que arrastra wmi, pyautogui y mss. Es el codigo de
verdad el que se ejecuta, no una copia.
"""

import ast
import io
import os
import subprocess
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

FUENTE_DEL_AGENTE = RAIZ / "agent" / "agent.py"


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def sacar_del_fuente(*nombres):
    """
    Devuelve un espacio de nombres con esas funciones del agente, ya
    ejecutables, sin importar el modulo entero.
    """

    texto = io.open(FUENTE_DEL_AGENTE, encoding="utf-8").read()
    arbol = ast.parse(texto)

    quedan = set(nombres)
    piezas = []

    for nodo in arbol.body:

        if isinstance(nodo, ast.FunctionDef) and nodo.name in quedan:
            piezas.append(nodo)
            quedan.discard(nodo.name)

        # El SID vive como constante del modulo y las funciones lo usan
        elif isinstance(nodo, ast.Assign):

            for destino in nodo.targets:

                if (isinstance(destino, ast.Name)
                        and destino.id == "SID_DE_LA_SESION_INTERACTIVA"):
                    piezas.append(nodo)

    if quedan:
        raise AssertionError(
            "no estan en agent/agent.py: %s" % ", ".join(sorted(quedan))
        )

    espacio = {
        "os": os,
        "subprocess": subprocess,
        "print": lambda *a, **k: None,
        "ENV_PATH": None,
    }

    molde = ast.Module(body=piezas, type_ignores=[])

    exec(compile(molde, str(FUENTE_DEL_AGENTE), "exec"), espacio)

    return espacio


def env_de_prueba(contenido):
    carpeta = tempfile.mkdtemp(prefix="candado-")
    ruta = os.path.join(carpeta, ".env")

    with io.open(ruta, "w", encoding="utf-8") as mano:
        mano.write(contenido)

    return ruta


def lo_que_dice_icacls(ruta):
    consulta = subprocess.run(
        ["icacls", ruta], capture_output=True, text=True, timeout=30
    )

    return (consulta.stdout or "").upper()


def prueba_mientras_el_token_este_el_candado_se_queda(agente):
    """
    Un .env recien instalado, con credencial, no se toca.

    Esta es la mitad que NO debe moverse. Si se abriera aqui, se
    estaria regalando la credencial de alta de la organizacion a
    cualquiera que use el equipo, que es exactamente lo que el
    instalador queria evitar.
    """

    agente["ENV_PATH"] = env_de_prueba(
        "REMOTEADMIN_SERVER=https://ejemplo\n"
        "AGENT_TOKEN=rae_credencial_viva\n"
        "REMOTEADMIN_CA_CERT=\n"
    )

    comprobar(
        "con credencial, se reconoce que hay secreto",
        agente["el_env_guarda_la_credencial_de_alta"]()
    )

    antes = lo_que_dice_icacls(agente["ENV_PATH"])

    comprobar(
        "con credencial, soltar_el_candado_del_env no hace nada",
        agente["soltar_el_candado_del_env"]() is False
    )

    comprobar(
        "con credencial, los permisos quedan intactos",
        lo_que_dice_icacls(agente["ENV_PATH"]) == antes
    )


def prueba_sin_token_se_abre_a_la_sesion(agente):
    """
    Un .env ya enrolado se abre a quien tiene la sesion abierta.
    """

    agente["ENV_PATH"] = env_de_prueba(
        "REMOTEADMIN_SERVER=https://ejemplo\n"
        "AGENT_TOKEN=\n"
        "REMOTEADMIN_CA_CERT=\n"
    )

    comprobar(
        "sin credencial, se reconoce que no hay secreto",
        agente["el_env_guarda_la_credencial_de_alta"]() is False
    )

    comprobar(
        "antes de soltarlo, el permiso no esta puesto",
        agente["el_usuario_de_la_sesion_ya_puede_leer_el_env"]() is False
    )

    comprobar(
        "sin credencial, el candado se suelta",
        agente["soltar_el_candado_del_env"]()
    )

    comprobar(
        "el permiso de la sesion interactiva queda escrito",
        "S-1-5-4" in lo_que_dice_icacls(agente["ENV_PATH"])
        or "INTERACTIVE" in lo_que_dice_icacls(agente["ENV_PATH"])
    )

    # Idempotencia: cada escritura de ACL es una oportunidad de dejar
    # el archivo peor de como estaba, y esto corre en CADA arranque
    # del servicio.
    comprobar(
        "a la segunda vez ya no reescribe nada",
        agente["soltar_el_candado_del_env"]() is False
    )


def prueba_un_env_que_no_existe_no_revienta(agente):
    agente["ENV_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="candado-vacio-"), ".env"
    )

    comprobar(
        "sin archivo, soltar_el_candado_del_env devuelve False",
        agente["soltar_el_candado_del_env"]() is False
    )


def prueba_un_env_ilegible_se_asume_con_secreto(agente):
    """
    Si no se puede leer, se asume lo peor y no se abre nada.

    Es la decision conservadora: ante la duda, el candado se queda.
    """

    ruta = env_de_prueba("AGENT_TOKEN=rae_algo\n")

    subprocess.run(
        ["icacls", ruta, "/deny", "%s:(R)" % os.environ.get("USERNAME", "")],
        capture_output=True, text=True, timeout=30
    )

    agente["ENV_PATH"] = ruta

    try:
        veredicto = agente["el_env_guarda_la_credencial_de_alta"]()
    finally:
        subprocess.run(
            ["icacls", ruta, "/remove:d",
             os.environ.get("USERNAME", "")],
            capture_output=True, text=True, timeout=30
        )

    comprobar(
        "un .env ilegible se trata como si guardara la credencial",
        veredicto
    )


def prueba_el_arranque_no_muere_con_un_env_ilegible():
    """
    La red de seguridad: load_dotenv va dentro de un try.

    Se comprueban las dos cosas que tienen que cumplirse para que esa
    red funcione, porque una sin la otra no sirve de nada:

      1. el fuente envuelve load_dotenv y recoge OSError
      2. load_dotenv sobre un archivo sin permiso de lectura lanza de
         verdad un OSError, y no otra cosa que el except no pillaria

    La segunda es la que importa de verdad: si dotenv se tragara el
    error o lanzara algo distinto, el guardia seria decorativo.
    """

    texto = io.open(FUENTE_DEL_AGENTE, encoding="utf-8").read()

    arbol = ast.parse(texto)

    envuelto = False

    for nodo in ast.walk(arbol):

        if not isinstance(nodo, ast.Try):
            continue

        llama = any(
            isinstance(hijo, ast.Call)
            and isinstance(hijo.func, ast.Name)
            and hijo.func.id == "load_dotenv"
            for hijo in ast.walk(nodo)
        )

        if not llama:
            continue

        for manejador in nodo.handlers:

            tipo = manejador.type

            if isinstance(tipo, ast.Name) and tipo.id == "OSError":
                envuelto = True

    comprobar(
        "load_dotenv va dentro de un try que recoge OSError",
        envuelto
    )

    # Y que lo que lanza sea de esa familia.
    ruta = env_de_prueba("REMOTEADMIN_SERVER=https://ejemplo\n")

    subprocess.run(
        ["icacls", ruta, "/deny", "%s:(R)" % os.environ.get("USERNAME", "")],
        capture_output=True, text=True, timeout=30
    )

    hijo = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys\n"
         "from dotenv import load_dotenv\n"
         "try:\n"
         "    load_dotenv(sys.argv[1])\n"
         "except OSError as fallo:\n"
         "    print('OSERROR ' + type(fallo).__name__)\n"
         "except BaseException as fallo:\n"
         "    print('OTRA ' + type(fallo).__name__)\n"
         "else:\n"
         "    print('SIN ERROR')\n",
         ruta],
        capture_output=True, text=True, timeout=120,
        cwd=tempfile.gettempdir()
    )

    subprocess.run(
        ["icacls", ruta, "/remove:d", os.environ.get("USERNAME", "")],
        capture_output=True, text=True, timeout=30
    )

    dice = (hijo.stdout or "").strip()

    # 'SIN ERROR' tambien vale: significa que dotenv ni se queja, asi
    # que el arranque no corria peligro por esta via. Lo unico
    # inaceptable es que lance algo que el except no recoge.
    comprobar(
        "lo que lanza dotenv sin permiso es OSError (o nada)",
        dice.startswith("OSERROR") or dice == "SIN ERROR",
        dice or (hijo.stderr or "").strip()
    )


def principal():

    agente = sacar_del_fuente(
        "el_env_guarda_la_credencial_de_alta",
        "el_usuario_de_la_sesion_ya_puede_leer_el_env",
        "soltar_el_candado_del_env",
    )

    prueba_mientras_el_token_este_el_candado_se_queda(agente)
    prueba_sin_token_se_abre_a_la_sesion(agente)
    prueba_un_env_que_no_existe_no_revienta(agente)
    prueba_un_env_ilegible_se_asume_con_secreto(agente)
    prueba_el_arranque_no_muere_con_un_env_ilegible()

    print()

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  bien   %s" % nombre)
        else:
            fallos += 1
            print("  FALLA  %s" % nombre)

            if detalle:
                print("         %s" % detalle)

    print()
    print("%d/%d comprobaciones" % (len(resultados) - fallos,
                                    len(resultados)))

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(principal())
