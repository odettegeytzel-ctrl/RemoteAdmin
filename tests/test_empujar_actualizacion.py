r"""
Una actualizacion se puede empujar, y no abre ninguna puerta.

    .venv\Scripts\python tests/test_empujar_actualizacion.py

El Agent comprobaba su version cada seis horas y nada mas. Eso basta
para el dia a dia y no basta para una correccion urgente: la ultima
maquina tarda hasta seis horas en recibirla y no hay nada que hacer
mientras.

No es hipotetico. Paso: una semana de grabaciones perdidas por un
ayudante caducado, la correccion publicada y firmada en el servidor a
mediodia, y una tarde entera mirando como cada equipo la recogia a su
hora, de uno en uno, sin poder acelerarlo. Con treinta equipos eso ya
no es una tarde incomoda, es inaceptable.

Ahora hay dos cosas:

  una orden 'check_update'    el panel dice "comprueba ahora" por el
                              canal que ya esta abierto

  el intervalo baja a 1 h     seis horas eran demasiado para un
                              chequeo que es una peticion diminuta

Lo que esta prueba vigila de verdad
-----------------------------------

Que la orden NO sea una via para instalar cosas. Dice "comprueba
ahora" y nada mas: no lleva version, ni ruta, ni URL. El Agent hace
exactamente lo que ya hacia solo -pedir el manifiesto al servidor,
verificar su firma, decidir-. Si la orden pudiera llevar un origen o
una version, seria una forma de instalar software en todos los
equipos administrados, que es lo mas valioso que hay aqui.

Y que no se solapen dos comprobaciones: el reloj y el panel pueden
coincidir, y dos actualizaciones descomprimiendo sobre la misma
carpeta es justo lo que no se puede permitir.
"""

import io
import os
import re
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "agent"))

os.environ.setdefault(
    "AUTH_SECRET_KEY", "clave-solo-para-empujar-" + "x" * 20
)

import commands  # noqa: E402
import updater  # noqa: E402


AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

SERVIDOR = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), str(detalle)))


def prueba_la_orden_no_lleva_nada():
    """
    'check_update' es una orden sin argumentos, y eso es deliberado.

    Se comprueba en los dos lados: el servidor la manda tal cual y el
    Agent la compara por igualdad, no por prefijo. Una orden con
    prefijo -'check_update:...'- aceptaria una carga, y esa carga
    seria el sitio natural donde colar una version o un origen.
    """

    comprobar(
        "el servidor la manda sin argumentos",
        'send_text("check_update")' in SERVIDOR
    )

    comprobar(
        "el Agent la compara por igualdad, no por prefijo",
        'if message == "check_update":' in AGENTE
    )

    comprobar(
        "y no existe ninguna variante con dos puntos",
        '"check_update:"' not in AGENTE
        and '"check_update:"' not in SERVIDOR
    )

    # En la lista de ordenes va SIN dos puntos: las que llevan carga
    # se declaran con el prefijo y ':' al final.
    comprobar(
        "en la lista de ordenes figura sin carga",
        '"check_update",' in io.open(
            RAIZ / "agent" / "commands.py", encoding="utf-8"
        ).read()
    )


def prueba_la_cumple_el_servicio():
    """
    El que se actualiza es el servicio. El ayudante no tiene nada que
    ver, y ademas moriria en medio de la actualizacion, porque es a el
    a quien se reinicia.
    """

    comprobar(
        "check_update no necesita escritorio",
        commands.requires_desktop("check_update") is False
    )

    comprobar(
        "y el ayudante no la atiende",
        commands.el_ayudante_debe_atender("check_update") is False
    )

    comprobar(
        "el Agent la descarta en el papel de ayudante",
        'if AGENT_ROLE != ROLE_HELPER:' in AGENTE
    )


def prueba_el_intervalo_bajo():

    comprobar(
        "el intervalo es de una hora",
        updater.CHECK_INTERVAL_SECONDS == 3600,
        updater.CHECK_INTERVAL_SECONDS
    )

    # Que siga siendo un intervalo y no un bucle sin pausa: eso
    # convertiria treinta equipos en una tormenta sobre un servidor
    # de un solo nucleo.
    comprobar(
        "y sigue habiendo una espera entre vueltas",
        "time.sleep(updater.CHECK_INTERVAL_SECONDS)" in AGENTE
    )


def prueba_no_se_solapan_dos_comprobaciones():
    """
    El reloj y el panel pueden coincidir.

    Se usa un cerrojo sin espera: la segunda no hace cola, se
    descarta. Lo que importa es no descomprimir dos veces sobre la
    misma carpeta, no atender las dos ordenes.
    """

    comprobar(
        "hay un cerrojo para la comprobacion",
        "_comprobando_version = threading.Lock()" in AGENTE
    )

    comprobar(
        "y se toma sin bloquear",
        "_comprobando_version.acquire(blocking=False)" in AGENTE
    )

    comprobar(
        "se suelta siempre, en un finally",
        re.search(
            r"finally:\s*\n\s*_comprobando_version\.release\(\)", AGENTE
        ) is not None
    )

    comprobar(
        "el reloj y la orden usan la MISMA funcion",
        AGENTE.count("comprobar_version_ahora(") >= 3,
        AGENTE.count("comprobar_version_ahora(")
    )


def prueba_la_orden_va_en_su_propio_hilo():
    """
    Descargar y descomprimir bloquea. Si eso corriera en el bucle del
    WebSocket, el equipo dejaria de atender todo lo demas mientras se
    actualiza: ni latido, ni pantalla, ni ordenes.
    """

    comprobar(
        "la orden se atiende en un hilo aparte",
        "def atender_orden_de_actualizar():" in AGENTE
        and "CheckUpdateOnDemand" in AGENTE
    )

    comprobar(
        "y un fallo ahi no tumba nada",
        "La orden de actualizar fallo" in AGENTE
    )


def prueba_el_endpoint_pide_permiso():

    for ruta in ('@app.post("/api/devices/{device_id}/check-update")',
                 '@app.post("/api/agent/check-update")'):
        comprobar("existe %s" % ruta.split('"')[1], ruta in SERVIDOR)

    # Las dos exigen permiso, y el mismo.
    trozo = SERVIDOR.split("# EMPUJAR UNA ACTUALIZACION", 1)[1]
    trozo = trozo.split("# ACCIONES SOBRE EL EQUIPO", 1)[0]

    comprobar(
        "las dos exigen devices.manage",
        trozo.count('require_permission(request, "devices.manage")') == 2,
        trozo.count('require_permission(request, "devices.manage")')
    )

    comprobar(
        "las dos quedan en la auditoria",
        trozo.count('log_audit(') >= 3,
        trozo.count('log_audit(')
    )

    comprobar(
        "un equipo desconectado da 409 y no se calla",
        "status_code=409" in trozo
        and "El equipo no esta conectado" in trozo
    )


def prueba_el_envio_a_todos_no_recorre_el_diccionario_vivo():
    """
    Mandar mensajes puede cerrar conexiones, y connected_agents
    cambia de tamano cuando eso pasa. Recorrerlo mientras revienta.
    """

    trozo = SERVIDOR.split("async def agent_check_update_all", 1)[1]
    trozo = trozo.split("# ACCIONES SOBRE EL EQUIPO", 1)[0]

    comprobar(
        "se recorre una copia de las claves",
        "list(connected_agents.keys())" in trozo
    )


def prueba_el_servidor_no_manda_version_ni_origen():
    """
    La prueba de fondo: lo que viaja es la palabra, nada mas.

    Si por el canal pudiera viajar una version o una URL de origen,
    cualquiera con acceso al servidor podria instalar software en
    todos los equipos administrados. La firma del manifiesto es lo
    que lo impide, y solo sirve si el origen no es negociable.
    """

    trozo = SERVIDOR.split("# EMPUJAR UNA ACTUALIZACION", 1)[1]
    trozo = trozo.split("# ACCIONES SOBRE EL EQUIPO", 1)[0]

    # Sin escapatorias en la condicion: o no aparece, o falla. Una
    # comprobacion que puede pasar por el motivo equivocado es peor
    # que no tenerla, y hoy ya ha pasado una vez en este proyecto.
    for sospechoso in ("version=", "http", "origen", "sha256", "json.dumps"):
        comprobar(
            "la orden no lleva '%s'" % sospechoso,
            sospechoso not in trozo.lower(),
            trozo.lower().count(sospechoso)
        )

    comprobar(
        "lo unico que se envia es la palabra",
        len(re.findall(r'send_text\(([^)]*)\)', trozo)) == 1
        and re.findall(r'send_text\(([^)]*)\)', trozo)[0]
        == '"check_update"',
        re.findall(r'send_text\(([^)]*)\)', trozo)
    )


def principal():

    prueba_la_orden_no_lleva_nada()
    prueba_la_cumple_el_servicio()
    prueba_el_intervalo_bajo()
    prueba_no_se_solapan_dos_comprobaciones()
    prueba_la_orden_va_en_su_propio_hilo()
    prueba_el_endpoint_pide_permiso()
    prueba_el_envio_a_todos_no_recorre_el_diccionario_vivo()
    prueba_el_servidor_no_manda_version_ni_origen()

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
