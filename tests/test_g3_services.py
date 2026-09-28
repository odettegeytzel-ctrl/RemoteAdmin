"""
Pruebas del bloque G3: consulta de servicios de Windows (solo lectura).

    .venv\\Scripts\\python tests/test_g3_services.py

Mismo planteamiento que G2: agent/inventory.py con un psutil falso para los
casos límite, y los endpoints reales con un Agent simulado sobre una base de
datos temporal.
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

class ServicioFalso:

    def __init__(self, datos=None, error=None):
        self._datos = datos
        self._error = error

    def as_dict(self):
        if self._error:
            raise self._error
        return self._datos


class PsutilServiciosFalso:

    def __init__(self, servicios=None, error_al_iterar=None,
                 con_servicios=True):

        self._servicios = servicios or []
        self._error_al_iterar = error_al_iterar

        if not con_servicios:
            # Simula una plataforma sin servicios (psutil no expone
            # win_service_iter fuera de Windows)
            return

        self.win_service_iter = self._iterar

    def _iterar(self):
        if self._error_al_iterar:
            raise self._error_al_iterar
        return iter(self._servicios)


def usar_psutil(falso):
    inventory.psutil = falso


def servicio(name, display_name, status="running", start_type="automatic",
             pid=100):
    return ServicioFalso({
        "name": name,
        "display_name": display_name,
        "status": status,
        "start_type": start_type,
        "pid": pid
    })


# ==============================
# 1. Agent: recogida de servicios
# ==============================

def test_consulta_correcta():

    usar_psutil(PsutilServiciosFalso([
        servicio("Spooler", "Cola de impresion"),
        servicio("wuauserv", "Windows Update",
                 status="stopped", start_type="manual", pid=None)
    ]))

    resultado = inventory.list_services()

    comprobar("Se devuelven los servicios encontrados",
              len(resultado["services"]) == 2)

    primero = resultado["services"][0]

    comprobar("Se incluye el nombre", primero["name"] == "Spooler")
    comprobar("Se incluye el nombre visible",
              primero["display_name"] == "Cola de impresion")
    comprobar("Se incluye el estado", primero["status"] == "running")
    comprobar("Se incluye el tipo de inicio",
              primero["start_type"] == "automatic")


def test_servicio_detenido():

    usar_psutil(PsutilServiciosFalso([
        servicio("wuauserv", "Windows Update",
                 status="stopped", start_type="disabled", pid=None)
    ]))

    servicio_detenido = inventory.list_services()["services"][0]

    comprobar("Un servicio detenido se informa como tal",
              servicio_detenido["status"] == "stopped")

    comprobar("Se conserva su tipo de inicio",
              servicio_detenido["start_type"] == "disabled")


def test_respuesta_vacia():

    usar_psutil(PsutilServiciosFalso([]))

    resultado = inventory.list_services()

    comprobar("Una lista vacia es una respuesta valida, no un error",
              resultado["services"] == [] and "error" not in resultado)


def test_servicio_no_consultable():

    usar_psutil(PsutilServiciosFalso([
        servicio("Spooler", "Cola de impresion"),
        ServicioFalso(error=PermissionError("acceso denegado"))
    ]))

    resultado = inventory.list_services()

    comprobar("Un servicio que no se deja leer no tumba la consulta",
              len(resultado["services"]) == 1)

    comprobar("Se cuentan los servicios no consultables",
              resultado["unavailable"] == 1)


def test_error_general_al_listar():

    usar_psutil(PsutilServiciosFalso(
        error_al_iterar=OSError("no se pudo abrir el gestor de servicios")
    ))

    resultado = inventory.list_services()

    comprobar("Si no se puede ni empezar, se devuelve error explicito",
              "error" in resultado and not resultado.get("services"))


def test_plataforma_sin_servicios():

    usar_psutil(PsutilServiciosFalso(con_servicios=False))

    resultado = inventory.list_services()

    comprobar("Sin soporte de servicios se devuelve error, no una excepcion",
              "error" in resultado)


def test_no_permite_modificar_servicios():

    codigo = io.open(RAIZ / "agent" / "inventory.py",
                     encoding="utf-8").read()

    prohibido = [
        ".start(", ".stop(", ".restart(", "sc ", "net start", "net stop",
        "subprocess", "os.system", "Popen", "win32serviceutil"
    ]

    encontrados = [p for p in prohibido if p in codigo]

    comprobar("agent/inventory.py no puede iniciar ni detener servicios",
              not encontrados, f"encontrado: {encontrados}")

    backend_codigo = io.open(RAIZ / "backend" / "main.py",
                             encoding="utf-8").read()

    comprobar(
        "El endpoint de servicios es GET (solo lectura)",
        '@app.get("/api/devices/{device_id}/services")' in backend_codigo
        and '@app.post("/api/devices/{device_id}/services"' not in backend_codigo
    )

    comprobar(
        "No existe ningun endpoint para arrancar o parar servicios",
        "/services/start" not in backend_codigo
        and "/services/stop" not in backend_codigo
        and "/services/restart" not in backend_codigo
    )


# ==============================
# 2. Endpoint real
# ==============================

RESPUESTA_OK = {
    "services": [
        {"name": "Spooler", "display_name": "Cola de impresion",
         "status": "running", "start_type": "automatic", "pid": 100},
        {"name": "wuauserv", "display_name": "Windows Update",
         "status": "stopped", "start_type": "manual", "pid": None}
    ],
    "unavailable": 2
}


def pedir(cliente, device_id=None):
    return cliente.get(
        f"/api/devices/{device_id or h.DEVICE_OK}/services"
    )


def test_endpoint_devuelve_la_lista():

    h.desconectar_todos()
    h.limpiar_auditoria()

    h.conectar(h.WebSocketFalso(h.DEVICE_OK, RESPUESTA_OK))

    respuesta = pedir(h.cliente())
    datos = respuesta.json()

    comprobar("La consulta responde 200", respuesta.status_code == 200,
              str(respuesta.status_code))

    comprobar("Se devuelven los dos servicios",
              len(datos.get("services", [])) == 2)

    comprobar("Se informa del recuento", datos.get("count") == 2)

    comprobar("Se informan los servicios no consultables",
              datos.get("unavailable") == 2)

    comprobar(
        "El servicio detenido llega al panel como detenido",
        datos["services"][1]["status"] == "stopped"
    )

    comprobar(
        "La auditoria registra la consulta como exitosa",
        any(r["action"] == "device.services" and r["status"] == "success"
            for r in h.registros_auditoria())
    )

    comprobar(
        "La auditoria NO guarda la lista de servicios",
        all("Spooler" not in (r["details"] or "")
            for r in h.registros_auditoria())
    )


def test_endpoint_respuesta_vacia():

    h.desconectar_todos()
    h.limpiar_auditoria()

    h.conectar(h.WebSocketFalso(h.DEVICE_OK, {"services": []}))

    datos = pedir(h.cliente()).json()

    comprobar("Una lista vacia se devuelve como consulta correcta",
              datos.get("status") == "ok" and datos.get("count") == 0)


def test_endpoint_error_del_agent():

    h.desconectar_todos()
    h.limpiar_auditoria()

    h.conectar(h.WebSocketFalso(
        h.DEVICE_OK,
        {"error": "No se pudo abrir el gestor de servicios"}
    ))

    respuesta = pedir(h.cliente())

    comprobar("Un error del Agent devuelve 502",
              respuesta.status_code == 502, str(respuesta.status_code))

    comprobar(
        "El error queda auditado como error, no como exito",
        any(r["action"] == "device.services" and r["status"] == "error"
            for r in h.registros_auditoria())
    )


def test_autenticacion_requerida():

    h.desconectar_todos()
    h.conectar(h.WebSocketFalso(h.DEVICE_OK, RESPUESTA_OK))

    comprobar("Sin sesion no se pueden consultar servicios",
              pedir(h.cliente(autenticado=False)).status_code == 401)


def test_device_id_inventado():

    h.desconectar_todos()
    h.limpiar_auditoria()

    respuesta = pedir(h.cliente(), h.DEVICE_INEXISTENTE)

    comprobar("Un device_id inventado se rechaza con 404",
              respuesta.status_code == 404, str(respuesta.status_code))

    comprobar("El intento queda auditado como error",
              any(r["device_id"] == h.DEVICE_INEXISTENTE
                  and r["action"] == "device.services"
                  and r["status"] == "error"
                  for r in h.registros_auditoria()))


def test_otro_agent_no_puede_contestar():

    h.desconectar_todos()
    h.limpiar_auditoria()

    anterior = h.acelerar_timeout(1)

    try:
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

    comprobar("El intento fallido queda auditado como error",
              any(r["action"] == "device.services" and r["status"] == "error"
                  for r in h.registros_auditoria()))


# ==============================

def main():

    pruebas = [
        test_consulta_correcta,
        test_servicio_detenido,
        test_respuesta_vacia,
        test_servicio_no_consultable,
        test_error_general_al_listar,
        test_plataforma_sin_servicios,
        test_no_permite_modificar_servicios,
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
