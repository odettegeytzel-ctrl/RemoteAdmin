r"""
Traer un archivo de un equipo remoto.

    .venv\Scripts\python tests/test_descarga_de_archivos.py

Antes solo se podia sacar lo que hubiera dentro de
C:\RemoteAdmin\Downloads. Eso no es administrar un equipo: es un
buzon. Servia para recoger algo que alguien hubiera dejado ahi a
mano, y cuando el equipo esta a cien kilometros no hay nadie que lo
deje.

El caso que lo puso en evidencia: dos equipos no mostraban la
pantalla y la unica forma de leer el registro que decia por que era
desplazarse hasta la maquina. El archivo estaba ahi, el equipo
conectado, y no habia manera de verlo.

Lo que sostiene que esto sea seguro
-----------------------------------

No es la ruta. Es quien pide:

  - hace falta sesion de panel: lo exige el middleware para toda
    ruta /api, no endpoint por endpoint;
  - hace falta que el equipo sea de tu empresa: el aislamiento se
    comprueba en el mismo sitio, para todas las rutas;
  - hace falta 'remote.control', el mismo permiso que ver la
    pantalla de alguien y moverle el raton;
  - y cada descarga queda en la auditoria con el usuario, la IP y
    la ruta exacta.

Lo que se conserva del Agent es lo que protege al equipo de un
disparate: que sea un archivo, que exista y que no pase del tope de
tamano.
"""

import io
import os
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))
sys.path.insert(0, str(RAIZ / "tests"))

os.environ.setdefault("AUTH_SECRET_KEY", "clave-solo-para-descargas")

SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()
AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


# ==================================================================
# 1. CUALQUIER RUTA
# ==================================================================

def test_el_agent_acepta_cualquier_ruta():

    import agent

    comprobar("Ya no se exige una carpeta concreta",
              "La ruta está fuera de la carpeta permitida" not in AGENTE)

    # Un archivo de verdad, fuera de C:\RemoteAdmin\Downloads
    ruta = os.path.abspath(__file__)

    destino, tamano = agent.resolve_download_path(ruta)

    comprobar("Se puede pedir un archivo de cualquier carpeta",
              os.path.normcase(destino) == os.path.normcase(ruta),
              destino)

    comprobar("Y se informa de su tamano",
              tamano == os.path.getsize(ruta), str(tamano))


def test_el_servidor_ya_no_filtra_por_carpeta():

    comprobar("Desaparece el rechazo por carpeta",
              "Solo se permiten archivos dentro de" not in SERVIDOR)

    comprobar("Y no queda el comprobador sin usar",
              "def path_within_downloads(" not in SERVIDOR)

    comprobar("El panel ya no propone esa carpeta",
              "C:\\RemoteAdmin\\Downloads\\archivo.txt" not in HTML)


# ==================================================================
# 2. LO QUE SIGUE PROTEGIDO
# ==================================================================

def test_hace_falta_poder_controlar_el_equipo():
    """
    El permiso NO estaba, y mientras solo se podia sacar de una
    carpeta concreta casi daba igual. Ahora no: quien mira el
    inventario no tiene por que poder llevarse los documentos de
    nadie.
    """

    cuerpo = SERVIDOR.split("async def download_file(", 1)[1][:2000]

    comprobar("La descarga exige remote.control",
              'require_permission(request, "remote.control")' in cuerpo)

    comprobar("Y se niega antes de pedir nada al equipo",
              cuerpo.index("require_permission")
              < cuerpo.index("connected_agents.get"),
              "se pide el archivo antes de mirar el permiso")

    comprobar("El intento denegado queda en la auditoria",
              '"error": "Sin permiso remote.control"' in cuerpo)


def test_queda_constancia_de_que_se_llevaron_y_quien():
    """
    Es lo que hace que esto se pueda auditar despues: no impide
    nada, pero deja dicho quien saco que de donde.
    """

    cuerpo = SERVIDOR.split("async def download_file(", 1)[1][:2000]

    comprobar("Se registra la accion",
              '"file.download"' in cuerpo)

    comprobar("Con el usuario y su IP",
              "username=usuario" in cuerpo and "source_ip=ip_origen" in cuerpo)

    comprobar("Y con la ruta exacta",
              '"path": path' in cuerpo)

    # El CONTENIDO no: la auditoria no es un almacen de archivos
    # ajenos.
    comprobar("Pero nunca el contenido del archivo",
              "contenido" not in cuerpo.lower().split('log_audit')[1][:400]
              if "log_audit" in cuerpo else True)


def test_el_agent_sigue_protegiendo_al_equipo():
    """
    Abrir la ruta no es abrirlo todo.
    """

    import agent

    def falla(ruta):

        try:
            agent.resolve_download_path(ruta)
            return False

        except (ValueError, FileNotFoundError, OSError):
            return True

    comprobar("Una ruta vacia se rechaza", falla(""))
    comprobar("Y None tambien", falla(None))

    comprobar("Un archivo que no existe se rechaza",
              falla(os.path.join(os.path.dirname(__file__), "no-existe.xyz")))

    comprobar("Una carpeta no es un archivo",
              falla(os.path.dirname(os.path.abspath(__file__))))

    comprobar("Y sigue habiendo un tope de tamano",
              "MAX_FILE_SIZE" in AGENTE
              and "supera el límite" in AGENTE)


def test_sigue_siendo_una_orden_de_fondo():
    """
    La atiende el SERVICIO, no el ayudante.

    Importa justo en el caso que motivo el cambio: cuando no hay
    nadie con la sesion iniciada, el ayudante no existe, y una
    descarga que dependiera de el no serviria para averiguar por que
    no hay ayudante.
    """

    import commands

    comprobar("Pedir un archivo no necesita escritorio",
              not commands.requires_desktop(
                  'file_download_start:{"path": "C:\\\\x.log"}'
              ))

    comprobar("Y esta escrito entre las de fondo",
              "file_download_ready:" in io.open(
                  RAIZ / "agent" / "commands.py", encoding="utf-8"
              ).read())


# ==================================================================

def principal():

    print()
    print("=" * 66)
    print("TRAER UN ARCHIVO DE UN EQUIPO REMOTO")
    print("=" * 66)
    print()

    pruebas = [
        test_el_agent_acepta_cualquier_ruta,
        test_el_servidor_ya_no_filtra_por_carpeta,
        test_hace_falta_poder_controlar_el_equipo,
        test_queda_constancia_de_que_se_llevaron_y_quien,
        test_el_agent_sigue_protegiendo_al_equipo,
        test_sigue_siendo_una_orden_de_fondo,
    ]

    for prueba in pruebas:

        try:
            prueba()

        except Exception as error:
            comprobar("%s (excepcion)" % prueba.__name__, False,
                      "%s: %s" % (type(error).__name__, error))

    fallos = 0

    for nombre, bien, detalle in resultados:

        if bien:
            print("  ok     %s" % nombre)

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
