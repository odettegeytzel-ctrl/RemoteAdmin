"""
Consultas con respuesta al Agent: procesos (G2) y servicios (G3).

Hasta ahora las órdenes al Agent eran de ida ("get_system_info") y el
resultado llegaba más tarde por su cuenta. Una consulta necesita respuesta:
quien pregunta espera la lista, y la auditoría necesita saber si salió bien
o mal. Este módulo es la centralita que empareja cada pregunta con su
respuesta.

Cada consulta lleva un identificador aleatorio. Una respuesta solo se
acepta si llega por el WebSocket del MISMO equipo al que se preguntó, y ese
equipo se deriva del token del Agent, no de lo que diga el mensaje. Así un
Agent no puede contestar por otro ni colar resultados en la consulta ajena.

El resultado que llega del Agent se normaliza antes de salir: solo campos
conocidos, tipos forzados y tope de filas. El Agent es de confianza, pero
una lista sin límite bastaría para dejar el panel inutilizable.
"""

import uuid


# Tiempo máximo de espera. Listar procesos en un equipo cargado puede pasar
# de un segundo; media hora colgado no le sirve a nadie.
QUERY_TIMEOUT_SECONDS = 30

# Topes de tamaño. Un Windows normal ronda los 250 procesos y 300
# servicios; estos márgenes sobran y evitan respuestas desmesuradas.
MAX_PROCESSES = 2000
MAX_SERVICES = 1000

# Longitud máxima de cualquier texto devuelto (nombre, usuario...)
MAX_TEXT_LENGTH = 200

KIND_PROCESSES = "processes"
KIND_SERVICES = "services"

# Acciones de energia y sesion (G5). Reutilizan esta misma centralita en
# vez de abrir otro canal: el emparejamiento pregunta-respuesta y la
# comprobacion de identidad ya estan resueltos aqui.
KIND_POWER = "power"

# Prefijo del mensaje que envía el Agent con la respuesta
RESPONSE_PREFIXES = {
    "processes_info:": KIND_PROCESSES,
    "services_info:": KIND_SERVICES,
    "power_result:": KIND_POWER
}


# consulta_id -> {"device_id", "kind", "future"}
pending_queries = {}


def create_query(device_id, kind, future):
    """Registra una consulta pendiente y devuelve su identificador."""

    query_id = uuid.uuid4().hex

    pending_queries[query_id] = {
        "device_id": device_id,
        "kind": kind,
        "future": future
    }

    return query_id


def discard_query(query_id):
    """Olvida una consulta (terminada, caducada o abortada)."""

    return pending_queries.pop(query_id, None)


def _entregar(pendiente, resultado):

    future = pendiente["future"]

    if future.done():
        return False

    future.set_result(resultado)

    return True


def resolve_query(device_id, kind, payload):
    """
    Entrega la respuesta del Agent a quien la esperaba.

    'device_id' es el del Agent AUTENTICADO (derivado de su token), no el
    que venga dentro del mensaje. Si no coincide con el de la consulta, la
    respuesta se descarta: es exactamente el caso de un Agent contestando
    por un equipo que no es el suyo.
    """

    if not isinstance(payload, dict):
        return False

    query_id = payload.get("query_id")

    if not query_id:
        return False

    pendiente = pending_queries.get(query_id)

    if pendiente is None:
        # Llegó tarde: la consulta ya caducó o se abortó
        return False

    if pendiente["device_id"] != device_id:
        # Un Agent no contesta por otro. No se resuelve ni se borra: la
        # consulta legítima sigue esperando su respuesta.
        return False

    if pendiente["kind"] != kind:
        return False

    pending_queries.pop(query_id, None)

    return _entregar(pendiente, payload)


def fail_device_queries(device_id, reason="El Agent se desconectó"):
    """
    Corta las consultas pendientes de un equipo que se ha desconectado.

    Sin esto, quien preguntó se quedaría esperando hasta el tiempo máximo
    para recibir un error que ya se conoce.
    """

    cortadas = 0

    for query_id, pendiente in list(pending_queries.items()):

        if pendiente["device_id"] != device_id:
            continue

        pending_queries.pop(query_id, None)

        if _entregar(pendiente, {"error": reason}):
            cortadas += 1

    return cortadas


# ==============================
# Normalización de la respuesta
# ==============================

def _texto(valor):

    if valor is None:
        return None

    texto = valor if isinstance(valor, str) else str(valor)

    return texto[:MAX_TEXT_LENGTH]


def _entero(valor, por_defecto=0):

    try:
        return int(valor)

    except (TypeError, ValueError):
        return por_defecto


def normalize_processes(payload):
    """
    Deja la respuesta de procesos en una forma conocida.

    Solo pasan los campos previstos: si algún día el Agent enviara datos de
    más, no acabarían en el panel por accidente.
    """

    if not isinstance(payload, dict):
        return {"error": "Respuesta no válida del Agent"}

    if payload.get("error"):
        return {"error": _texto(payload["error"])}

    crudos = payload.get("processes")

    if not isinstance(crudos, list):
        return {"error": "Respuesta no válida del Agent"}

    procesos = []

    for elemento in crudos[:MAX_PROCESSES]:

        if not isinstance(elemento, dict):
            continue

        procesos.append({
            "pid": _entero(elemento.get("pid"), 0),
            "name": _texto(elemento.get("name")) or "(desconocido)",
            "username": _texto(elemento.get("username")),
            "memory_bytes": _entero(elemento.get("memory_bytes"), 0),
            "status": _texto(elemento.get("status")),
            "started_at": elemento.get("started_at")
                if isinstance(elemento.get("started_at"), (int, float))
                else None
        })

    return {
        "processes": procesos,
        "count": len(procesos),
        "truncated": len(crudos) > MAX_PROCESSES,
        "skipped_gone": _entero(payload.get("skipped_gone"), 0),
        "skipped_denied": _entero(payload.get("skipped_denied"), 0)
    }


def normalize_services(payload):
    """Igual que normalize_processes, para servicios."""

    if not isinstance(payload, dict):
        return {"error": "Respuesta no válida del Agent"}

    if payload.get("error"):
        return {"error": _texto(payload["error"])}

    crudos = payload.get("services")

    if not isinstance(crudos, list):
        return {"error": "Respuesta no válida del Agent"}

    servicios = []

    for elemento in crudos[:MAX_SERVICES]:

        if not isinstance(elemento, dict):
            continue

        servicios.append({
            "name": _texto(elemento.get("name")) or "(desconocido)",
            "display_name": _texto(elemento.get("display_name")),
            "status": _texto(elemento.get("status")),
            "start_type": _texto(elemento.get("start_type")),
            "pid": _entero(elemento.get("pid"), 0) or None
        })

    return {
        "services": servicios,
        "count": len(servicios),
        "truncated": len(crudos) > MAX_SERVICES,
        "unavailable": _entero(payload.get("unavailable"), 0)
    }
