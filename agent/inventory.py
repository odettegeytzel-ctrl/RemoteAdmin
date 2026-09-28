"""
Inventario de solo lectura del equipo: procesos (G2) y servicios (G3).

Este módulo SOLO consulta. No termina, suspende, inicia ni modifica nada:
no importa ni usa ninguna función de psutil que cambie el estado del
sistema, y no ejecuta comandos externos. El Agent corre con privilegios
elevados, así que la única defensa fiable es que la capacidad de modificar
directamente no exista aquí.

Se apoya en psutil, que ya es una dependencia del Agent (se usa para RAM,
disco y alertas). No hace falta nada nuevo.

Está separado de agent.py a propósito: sin WebSocket ni red se puede probar
con procesos y servicios falsos, incluidos los casos raros —un proceso que
desaparece a mitad de la consulta, un servicio que no se deja leer— que en
la máquina real no se pueden provocar a voluntad.
"""

import psutil


# Atributos que se piden a psutil de una vez. Pedirlos en bloque es mucho
# más rápido que atributo a atributo, y en Windows evita abrir un handle
# por cada dato.
PROCESS_ATTRS = [
    "pid", "name", "username", "memory_info", "status", "create_time"
]


def _texto(valor):
    """Devuelve una cadena o None; nunca un objeto suelto de psutil."""

    if valor is None:
        return None

    if isinstance(valor, str):
        return valor

    return str(valor)


def list_processes():
    """
    Lista los procesos activos.

    Nunca lanza. Los procesos que desaparecen durante el recorrido y los que
    no se dejan consultar se cuentan aparte en vez de romper la consulta
    entera: en un equipo real siempre hay de los dos.

    Devuelve un dict con 'processes' y los contadores de omitidos, o con
    'error' si ni siquiera se pudo empezar.
    """

    procesos = []
    desaparecidos = 0
    sin_permiso = 0

    try:
        iterador = psutil.process_iter(PROCESS_ATTRS)

    except Exception as error:
        return {"error": f"No se pudo listar procesos: {error}"}

    while True:

        # El propio recorrido puede fallar en un proceso concreto; se avanza
        # de uno en uno para que eso no tumbe la lista completa.
        try:
            proceso = next(iterador)

        except StopIteration:
            break

        except psutil.NoSuchProcess:
            desaparecidos += 1
            continue

        except psutil.AccessDenied:
            sin_permiso += 1
            continue

        except Exception:
            break

        try:
            info = dict(proceso.info)

            memoria = info.get("memory_info")

            # memory_info puede ser None si el proceso murió justo ahora
            memoria_bytes = getattr(memoria, "rss", None)

            creado = info.get("create_time")

            procesos.append({
                "pid": info.get("pid"),
                "name": _texto(info.get("name")),

                # El usuario no está disponible para los procesos del
                # sistema sin permisos suficientes: queda a None, no rompe.
                "username": _texto(info.get("username")),

                "memory_bytes": int(memoria_bytes) if memoria_bytes else 0,
                "status": _texto(info.get("status")),
                "started_at": float(creado) if creado is not None else None
            })

        except psutil.NoSuchProcess:
            desaparecidos += 1

        except psutil.AccessDenied:
            sin_permiso += 1

        except Exception:
            sin_permiso += 1

    return {
        "processes": procesos,
        "skipped_gone": desaparecidos,
        "skipped_denied": sin_permiso
    }


def list_services():
    """
    Lista los servicios de Windows.

    Igual que con los procesos: los servicios que no se dejan leer se
    cuentan y se sigue. Devuelve 'error' si la plataforma no ofrece
    servicios (psutil solo expone win_service_iter en Windows).
    """

    if not hasattr(psutil, "win_service_iter"):
        return {"error": "Los servicios solo están disponibles en Windows"}

    servicios = []
    no_disponibles = 0

    try:
        iterador = psutil.win_service_iter()

    except Exception as error:
        return {"error": f"No se pudo listar servicios: {error}"}

    while True:

        try:
            servicio = next(iterador)

        except StopIteration:
            break

        except Exception:
            no_disponibles += 1
            continue

        try:
            info = servicio.as_dict()

            servicios.append({
                "name": _texto(info.get("name")),
                "display_name": _texto(info.get("display_name")),

                # 'stopped', 'running', 'paused'...
                "status": _texto(info.get("status")),

                # 'automatic', 'manual', 'disabled'. No siempre disponible.
                "start_type": _texto(info.get("start_type")),

                "pid": info.get("pid")
            })

        except Exception:
            # Un servicio que se borra o que no deja consultarse no debe
            # invalidar la lista entera.
            no_disponibles += 1

    return {
        "services": servicios,
        "unavailable": no_disponibles
    }
