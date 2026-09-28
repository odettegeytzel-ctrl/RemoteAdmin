"""
Integración de G2 + G3: procesos y servicios conviviendo.

    .venv\\Scripts\\python tests/test_g23_integration.py

Lo que aquí se comprueba no sale en las pruebas de cada bloque: que las dos
consultas no se pisan entre sí, que una consulta colgada no deja bloqueado
el WebSocket para lo demás, y que la desconexión del Agent se resuelve de
inmediato en vez de esperar al tiempo máximo.
"""

import asyncio
import io
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import g23_harness as h

import backend.queries as queries


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


PROCESOS = {"processes": [{"pid": 1, "name": "a.exe"}]}
SERVICIOS = {"services": [{"name": "Spooler", "display_name": "Cola"}]}


class WebSocketDoble(h.WebSocketFalso):
    """Responde a las dos consultas, cada una con sus datos."""

    async def send_text(self, texto):

        import json

        self.enviados.append(texto)

        if ":" not in texto:
            # Ordenes de siempre ("ping", "get_system_info"): el Agent las
            # atiende igual, aqui no hay nada que responder.
            return

        comando, cuerpo = texto.split(":", 1)

        if comando not in ("get_processes", "get_services"):
            return

        peticion = json.loads(cuerpo)

        if comando == "get_processes":
            kind = queries.KIND_PROCESSES
            carga = dict(PROCESOS)
        else:
            kind = queries.KIND_SERVICES
            carga = dict(SERVICIOS)

        carga["query_id"] = peticion.get("query_id")

        queries.resolve_query(self.responder_como, kind, carga)


# ==============================
# Agent conectado
# ==============================

def test_agent_conectado_ambas_consultas():

    h.desconectar_todos()
    h.limpiar_auditoria()

    agente = WebSocketDoble(h.DEVICE_OK)
    h.conectar(agente)

    cliente = h.cliente()

    procesos = cliente.get(f"/api/devices/{h.DEVICE_OK}/processes").json()
    servicios = cliente.get(f"/api/devices/{h.DEVICE_OK}/services").json()

    comprobar("Con el Agent conectado se obtienen los procesos",
              procesos.get("count") == 1)

    comprobar("Con el Agent conectado se obtienen los servicios",
              servicios.get("count") == 1)

    comprobar(
        "Cada consulta recibe sus propios datos, no los de la otra",
        procesos["processes"][0]["name"] == "a.exe"
        and servicios["services"][0]["name"] == "Spooler"
    )

    comprobar("Se auditan las dos consultas por separado",
              len({r["action"] for r in h.registros_auditoria()})
              == 2)


def test_cada_consulta_lleva_identificador_propio():

    h.desconectar_todos()

    agente = WebSocketDoble(h.DEVICE_OK)
    h.conectar(agente)

    cliente = h.cliente()

    cliente.get(f"/api/devices/{h.DEVICE_OK}/processes")
    cliente.get(f"/api/devices/{h.DEVICE_OK}/processes")

    identificadores = {texto.split(":", 1)[1] for texto in agente.enviados}

    comprobar("Dos consultas seguidas no comparten identificador",
              len(identificadores) == 2, str(agente.enviados))


# ==============================
# Agent desconectado
# ==============================

def test_agent_desconectado():

    h.desconectar_todos()
    h.limpiar_auditoria()

    cliente = h.cliente()

    procesos = cliente.get(f"/api/devices/{h.DEVICE_OK}/processes")
    servicios = cliente.get(f"/api/devices/{h.DEVICE_OK}/services")

    comprobar("Sin Agent conectado, procesos responde 404",
              procesos.status_code == 404, str(procesos.status_code))

    comprobar("Sin Agent conectado, servicios responde 404",
              servicios.status_code == 404, str(servicios.status_code))

    comprobar(
        "Ambos intentos quedan auditados como error",
        len([r for r in h.registros_auditoria()
             if r["status"] == "error"]) == 2
    )


def test_desconexion_corta_la_espera():
    """fail_device_queries no deja a nadie esperando 30 segundos."""

    async def escenario():

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        queries.create_query(h.DEVICE_OK, queries.KIND_PROCESSES, future)

        cortadas = queries.fail_device_queries(h.DEVICE_OK)

        return cortadas, await asyncio.wait_for(future, 1)

    queries.pending_queries.clear()

    cortadas, resultado = asyncio.run(escenario())

    comprobar("La desconexion corta la consulta pendiente", cortadas == 1)

    comprobar("Quien esperaba recibe un error inmediato",
              "error" in resultado)

    comprobar("No quedan consultas pendientes colgadas",
              not queries.pending_queries)


def test_desconexion_no_afecta_a_otros_equipos():

    async def escenario():

        loop = asyncio.get_running_loop()

        mio = loop.create_future()
        ajeno = loop.create_future()

        queries.create_query(h.DEVICE_OK, queries.KIND_PROCESSES, mio)
        queries.create_query(h.DEVICE_OTRO, queries.KIND_PROCESSES, ajeno)

        queries.fail_device_queries(h.DEVICE_OK)

        return mio.done(), ajeno.done()

    queries.pending_queries.clear()

    mio_listo, ajeno_listo = asyncio.run(escenario())

    comprobar("Se corta la consulta del equipo desconectado", mio_listo)

    comprobar("La consulta de otro equipo sigue viva", not ajeno_listo)

    queries.pending_queries.clear()


# ==============================
# Dispositivo incorrecto
# ==============================

def test_dispositivo_incorrecto():

    h.desconectar_todos()

    cliente = h.cliente()

    for recurso in ("processes", "services"):

        respuesta = cliente.get(
            f"/api/devices/{h.DEVICE_INEXISTENTE}/{recurso}"
        )

        comprobar(f"Un equipo inexistente da 404 en {recurso}",
                  respuesta.status_code == 404)


def test_respuesta_tardia_no_contamina():
    """Una respuesta que llega cuando la consulta ya caduco se descarta."""

    queries.pending_queries.clear()

    entregada = queries.resolve_query(
        h.DEVICE_OK,
        queries.KIND_PROCESSES,
        {"query_id": "identificador-que-ya-no-existe", "processes": []}
    )

    comprobar("Una respuesta sin consulta pendiente no se entrega",
              entregada is False)


def test_respuesta_de_otro_tipo_no_se_entrega():
    """Contestar servicios a una pregunta de procesos no cuela."""

    async def escenario():

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        query_id = queries.create_query(
            h.DEVICE_OK, queries.KIND_PROCESSES, future
        )

        entregada = queries.resolve_query(
            h.DEVICE_OK,
            queries.KIND_SERVICES,
            {"query_id": query_id, "services": []}
        )

        return entregada, future.done()

    queries.pending_queries.clear()

    entregada, resuelta = asyncio.run(escenario())

    comprobar("Una respuesta del tipo equivocado se descarta",
              entregada is False and not resuelta)

    queries.pending_queries.clear()


# ==============================
# Consultas consecutivas y bloqueo
# ==============================

def test_multiples_consultas_consecutivas():

    h.desconectar_todos()
    h.limpiar_auditoria()

    agente = WebSocketDoble(h.DEVICE_OK)
    h.conectar(agente)

    cliente = h.cliente()

    codigos = []

    for _ in range(5):
        codigos.append(
            cliente.get(f"/api/devices/{h.DEVICE_OK}/processes").status_code
        )
        codigos.append(
            cliente.get(f"/api/devices/{h.DEVICE_OK}/services").status_code
        )

    comprobar("Diez consultas consecutivas responden todas 200",
              set(codigos) == {200}, str(codigos))

    comprobar("No queda ninguna consulta pendiente al terminar",
              not queries.pending_queries,
              str(queries.pending_queries))

    comprobar("Las diez consultas quedan auditadas",
              len(h.registros_auditoria()) == 10,
              str(len(h.registros_auditoria())))


def test_otras_funciones_siguen_disponibles():
    """Una consulta no debe dejar bloqueadas las funciones existentes."""

    h.desconectar_todos()

    agente = WebSocketDoble(h.DEVICE_OK)
    h.conectar(agente)

    cliente = h.cliente()

    cliente.get(f"/api/devices/{h.DEVICE_OK}/processes")

    # Un endpoint que no tiene nada que ver debe seguir respondiendo
    respuesta = cliente.get("/api/devices")

    comprobar("La lista de equipos sigue respondiendo tras una consulta",
              respuesta.status_code == 200, str(respuesta.status_code))

    # Y una orden de las de siempre por el mismo WebSocket
    ping = cliente.post(f"/api/devices/{h.DEVICE_OK}/ping")

    comprobar("El ping por el mismo WebSocket sigue funcionando",
              ping.status_code == 200, str(ping.status_code))


def test_consulta_no_bloquea_el_bucle_del_agent():
    """
    En el Agent, recorrer procesos o servicios va en un hilo aparte.

    Sin esto, listar cientos de procesos congelaria pantalla, teclado y
    grabacion durante toda la consulta. Se comprueba en el codigo porque el
    efecto solo se ve con el Agent real corriendo.
    """

    codigo = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

    bloque = codigo.split('get_processes:', 1)[-1].split("elif message ==", 1)[0]

    comprobar(
        "El Agent consulta en un hilo aparte (asyncio.to_thread)",
        "asyncio.to_thread(consultar)" in bloque
    )

    comprobar(
        "El Agent responde siempre, tambien si la consulta falla",
        'resultado = {"error"' in bloque
    )


# ==============================

def main():

    pruebas = [
        test_agent_conectado_ambas_consultas,
        test_cada_consulta_lleva_identificador_propio,
        test_agent_desconectado,
        test_desconexion_corta_la_espera,
        test_desconexion_no_afecta_a_otros_equipos,
        test_dispositivo_incorrecto,
        test_respuesta_tardia_no_contamina,
        test_respuesta_de_otro_tipo_no_se_entrega,
        test_multiples_consultas_consecutivas,
        test_otras_funciones_siguen_disponibles,
        test_consulta_no_bloquea_el_bucle_del_agent
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
