"""
Pruebas del bloque G2: consulta de procesos (solo lectura).

    .venv\\Scripts\\python tests/test_g2_processes.py

Dos frentes:
  - agent/inventory.py con un psutil falso, para provocar a voluntad los
    casos que en un equipo real no se pueden reproducir (un proceso que
    desaparece a mitad del recorrido, otro sin permisos).
  - los endpoints reales de la app, con un WebSocket de Agent falso, contra
    una base de datos temporal. Nunca se toca la base real.
"""

import io
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))
sys.path.insert(0, str(RAIZ / "agent"))

import g23_harness as h

import inventory


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


# ==============================
# psutil falso
# ==============================

class ProcesoDesaparecido(Exception):
    pass


class PermisoDenegado(Exception):
    pass


class MemoriaFalsa:
    def __init__(self, rss):
        self.rss = rss


class ProcesoFalso:
    def __init__(self, info=None, error=None):
        self._info = info
        self._error = error

    @property
    def info(self):
        if self._error:
            raise self._error
        return self._info


class PsutilFalso:

    NoSuchProcess = ProcesoDesaparecido
    AccessDenied = PermisoDenegado

    def __init__(self, procesos=None, error_al_iterar=None):
        self._procesos = procesos or []
        self._error_al_iterar = error_al_iterar

    def process_iter(self, attrs=None):
        if self._error_al_iterar:
            raise self._error_al_iterar
        return iter(self._procesos)


def usar_psutil(falso):
    inventory.psutil = falso


def proceso(pid, name, username="EQUIPO\\odette", rss=1024, status="running"):
    return ProcesoFalso({
        "pid": pid,
        "name": name,
        "username": username,
        "memory_info": MemoriaFalsa(rss),
        "status": status,
        "create_time": 1700000000.0
    })


# ==============================
# 1. Agent: recogida de procesos
# ==============================

def test_consulta_correcta():

    usar_psutil(PsutilFalso([
        proceso(4, "System", username=None, rss=110592),
        proceso(1234, "chrome.exe", rss=524288000)
    ]))

    resultado = inventory.list_processes()

    comprobar("Se devuelven los procesos encontrados",
              len(resultado["processes"]) == 2)

    primero = resultado["processes"][0]

    comprobar("Se incluye el PID", primero["pid"] == 4)
    comprobar("Se incluye el nombre", primero["name"] == "System")
    comprobar("Se incluye la memoria usada",
              primero["memory_bytes"] == 110592)
    comprobar("Se incluye el estado", primero["status"] == "running")
    comprobar("Un proceso sin usuario visible no rompe: queda a None",
              primero["username"] is None)
    comprobar("El usuario se incluye cuando esta disponible",
              resultado["processes"][1]["username"] == "EQUIPO\\odette")


def test_respuesta_vacia():

    usar_psutil(PsutilFalso([]))

    resultado = inventory.list_processes()

    comprobar("Una lista vacia es una respuesta valida, no un error",
              resultado["processes"] == [] and "error" not in resultado)


def test_proceso_desaparece_durante_la_consulta():

    usar_psutil(PsutilFalso([
        proceso(1, "bueno.exe"),
        ProcesoFalso(error=ProcesoDesaparecido()),
        proceso(2, "otro.exe")
    ]))

    resultado = inventory.list_processes()

    comprobar("Un proceso que muere a mitad no tumba la consulta",
              len(resultado["processes"]) == 2)

    comprobar("Se cuenta el proceso desaparecido",
              resultado["skipped_gone"] == 1)


def test_error_de_permisos():

    usar_psutil(PsutilFalso([
        proceso(1, "bueno.exe"),
        ProcesoFalso(error=PermisoDenegado())
    ]))

    resultado = inventory.list_processes()

    comprobar("Un proceso sin permisos se omite, no rompe",
              len(resultado["processes"]) == 1)

    comprobar("Se cuenta el proceso sin permisos",
              resultado["skipped_denied"] == 1)


def test_error_general_al_listar():

    usar_psutil(PsutilFalso(error_al_iterar=OSError("acceso denegado")))

    resultado = inventory.list_processes()

    comprobar("Si no se puede ni empezar, se devuelve error explicito",
              "error" in resultado and not resultado.get("processes"))

    comprobar("list_processes() nunca lanza",
              isinstance(resultado, dict))


def test_no_permite_modificar_procesos():
    """El modulo no debe tener forma alguna de matar o cambiar un proceso."""

    codigo = io.open(RAIZ / "agent" / "inventory.py",
                     encoding="utf-8").read()

    # Se buscan LLAMADAS, no palabras: los comentarios del modulo dicen en
    # castellano que no suspende ni termina nada, y eso no es una llamada.
    prohibido = [
        ".kill(", ".terminate(", ".suspend(", ".resume(", ".nice(",
        "subprocess", "os.system", "taskkill", "Popen", "win32serviceutil"
    ]

    encontrados = [p for p in prohibido if p in codigo]

    comprobar("agent/inventory.py no puede terminar ni alterar procesos",
              not encontrados, f"encontrado: {encontrados}")

    backend_codigo = io.open(RAIZ / "backend" / "main.py",
                             encoding="utf-8").read()

    comprobar(
        "No existe ningun endpoint para matar procesos",
        "/processes/kill" not in backend_codigo
        and "kill_process" not in backend_codigo
        and 'delete("/api/devices/{device_id}/processes' not in backend_codigo
    )

    comprobar(
        "El endpoint de procesos es GET (solo lectura)",
        '@app.get("/api/devices/{device_id}/processes")' in backend_codigo
        and '@app.post("/api/devices/{device_id}/processes"' not in backend_codigo
    )


# ==============================
# 2. Endpoint real
# ==============================

RESPUESTA_OK = {
    "processes": [
        {"pid": 4, "name": "System", "username": None,
         "memory_bytes": 110592, "status": "running",
         "started_at": 1700000000.0},
        {"pid": 1234, "name": "chrome.exe", "username": "EQUIPO\\odette",
         "memory_bytes": 524288000, "status": "running",
         "started_at": 1700000100.0}
    ],
    "skipped_gone": 1,
    "skipped_denied": 3
}


def pedir(cliente, device_id=None):
    return cliente.get(
        f"/api/devices/{device_id or h.DEVICE_OK}/processes"
    )


def test_endpoint_devuelve_la_lista():

    h.desconectar_todos()
    h.limpiar_auditoria()

    h.conectar(h.WebSocketFalso(h.DEVICE_OK, RESPUESTA_OK))

    respuesta = pedir(h.cliente())
    datos = respuesta.json()

    comprobar("La consulta responde 200", respuesta.status_code == 200,
              str(respuesta.status_code))

    comprobar("Se devuelven los dos procesos",
              len(datos.get("processes", [])) == 2)

    comprobar("Se informa del recuento", datos.get("count") == 2)

    comprobar("Se informan los procesos omitidos",
              datos.get("skipped_gone") == 1
              and datos.get("skipped_denied") == 3)

    comprobar(
        "La auditoria registra la consulta como exitosa",
        any(r["action"] == "device.processes" and r["status"] == "success"
            and "2" in (r["details"] or "")
            for r in h.registros_auditoria())
    )

    comprobar(
        "La auditoria NO guarda la lista de procesos",
        all("chrome.exe" not in (r["details"] or "")
            for r in h.registros_auditoria())
    )


def test_endpoint_respuesta_vacia():

    h.desconectar_todos()
    h.limpiar_auditoria()

    h.conectar(h.WebSocketFalso(h.DEVICE_OK, {"processes": []}))

    datos = pedir(h.cliente()).json()

    comprobar("Una lista vacia se devuelve como consulta correcta",
              datos.get("status") == "ok" and datos.get("count") == 0)

    comprobar("Una lista vacia se audita como success",
              any(r["action"] == "device.processes"
                  and r["status"] == "success"
                  for r in h.registros_auditoria()))


def test_endpoint_error_del_agent():

    h.desconectar_todos()
    h.limpiar_auditoria()

    h.conectar(h.WebSocketFalso(
        h.DEVICE_OK, {"error": "Acceso denegado al listar procesos"}
    ))

    respuesta = pedir(h.cliente())

    comprobar("Un error del Agent devuelve 502",
              respuesta.status_code == 502, str(respuesta.status_code))

    registros = h.registros_auditoria()

    comprobar(
        "El error queda auditado como error, no como exito",
        any(r["action"] == "device.processes" and r["status"] == "error"
            for r in registros)
    )

    comprobar(
        "La auditoria conserva el motivo del error",
        any("denegado" in (r["details"] or "") for r in registros)
    )


def test_autenticacion_requerida():

    h.desconectar_todos()

    h.conectar(h.WebSocketFalso(h.DEVICE_OK, RESPUESTA_OK))

    respuesta = pedir(h.cliente(autenticado=False))

    comprobar("Sin sesion no se pueden consultar procesos",
              respuesta.status_code == 401, str(respuesta.status_code))


def test_device_id_inventado():

    h.desconectar_todos()
    h.limpiar_auditoria()

    respuesta = pedir(h.cliente(), h.DEVICE_INEXISTENTE)

    comprobar("Un device_id inventado se rechaza con 404",
              respuesta.status_code == 404, str(respuesta.status_code))

    comprobar("El intento queda auditado como error",
              any(r["device_id"] == h.DEVICE_INEXISTENTE
                  and r["status"] == "error"
                  for r in h.registros_auditoria()))


def test_otro_agent_no_puede_contestar():
    """Un Agent no puede responder por un equipo que no es el suyo."""

    h.desconectar_todos()
    h.limpiar_auditoria()

    anterior = h.acelerar_timeout(1)

    try:
        # El Agent de DEVICE_OK contesta diciendo ser DEVICE_OTRO
        h.conectar(h.WebSocketFalso(
            h.DEVICE_OK, RESPUESTA_OK, responder_como=h.DEVICE_OTRO
        ))

        respuesta = pedir(h.cliente())

    finally:
        h.acelerar_timeout(anterior)

    comprobar(
        "Una respuesta con identidad ajena se descarta (caduca la consulta)",
        respuesta.status_code == 504, str(respuesta.status_code)
    )

    comprobar(
        "No se cuela ningun resultado en la respuesta",
        "processes" not in respuesta.json()
    )

    comprobar("El intento fallido queda auditado como error",
              any(r["action"] == "device.processes" and r["status"] == "error"
                  for r in h.registros_auditoria()))


# ==============================

def main():

    pruebas = [
        test_consulta_correcta,
        test_respuesta_vacia,
        test_proceso_desaparece_durante_la_consulta,
        test_error_de_permisos,
        test_error_general_al_listar,
        test_no_permite_modificar_procesos,
        test_endpoint_devuelve_la_lista,
        test_endpoint_respuesta_vacia,
        test_endpoint_error_del_agent,
        test_autenticacion_requerida,
        test_device_id_inventado,
        test_otro_agent_no_puede_contestar
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False, repr(error))

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
