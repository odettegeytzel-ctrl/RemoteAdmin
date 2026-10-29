"""
Paginacion del listado, catalogo local y reproduccion bajo demanda.

    .venv\\Scripts\\python tests/test_grabaciones_paginadas.py

Lo que origina esta suite: el listado de Grabaciones traia el
historial COMPLETO en cada consulta y lo pintaba entero. Con 173
filas en la base de desarrollo ya costaba encontrar nada, y el numero
no tiene techo: cada segmento grabado anade una.

Y tres defectos que salieron al auditar ese camino:

  - Elegir una fecha sin poner horas no filtraba nada. El frontend
    solo mandaba 'start'/'end' si habia hora, asi que el dia
    seleccionado se ignoraba en silencio.

  - El orden era 'started_at DESC' a secas. Con fechas repetidas
    —el catalogo local fecha por la hora de modificacion del
    archivo— SQLite puede devolverlas en distinto orden en dos
    consultas, y al paginar eso repite una fila y se salta otra.

  - Una grabacion que solo estaba en el equipo no se podia ver. No
    era un error: no existia ninguna via para leer el video sin
    subirlo antes al servidor.

Base temporal y datos inventados. No se toca produccion ni se
reproduce ningun video real: la transmision desde el equipo se
comprueba con un Agent simulado, y eso se dice en el informe.
"""

import asyncio
import io
import json
import os
import sys

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import users_harness as h

import backend.database as database
import backend.main as servidor
import backend.organizations as orgs
import backend.recordings as grabaciones
import backend.users as users


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


CLAVE = "ClaveDePaginacion2026!"

JS = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
HTML = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
AGENTE = io.open(RAIZ / "agent" / "agent.py", encoding="utf-8").read()

escenario = {}


# Cuantas grabaciones se siembran. 25 son dos paginas completas y una
# tercera a medias: suficiente para que un fallo de desplazamiento se
# note y para que el boton "Ver mas" tenga que desaparecer al final.
SEMBRADAS = 25


def preparar():
    """Dos empresas, un equipo cada una, 25 grabaciones en la propia."""

    h.reiniciar()

    conexion = database.get_connection()

    for tabla in ("recordings", "devices", "alerts", "audit_log",
                  "enrollment_tokens", "organizations"):
        conexion.execute(f"DELETE FROM {tabla}")

    conexion.commit()
    conexion.close()

    propia = orgs.create_organization(
        "Propia", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    ajena = orgs.create_organization(
        "Ajena", plan=orgs.PLAN_PRO,
        subscription_status=orgs.STATUS_ACTIVE
    )

    users.set_organization(h.OWNER, propia["id"])

    conexion = database.get_connection()

    for equipo, nombre, organizacion in (
        ("equipo-propio", "PC-PROPIA", propia["id"]),
        ("equipo-ajeno", "PC-AJENA", ajena["id"])
    ):
        conexion.execute(
            "INSERT INTO devices (device_id, hostname, organization_id) "
            "VALUES (?, ?, ?)", (equipo, nombre, organizacion)
        )

    # Una por hora del 10 al 14 de octubre, todas en el equipo.
    for indice in range(SEMBRADAS):

        dia = 10 + (indice // 6)
        hora = 8 + (indice % 6)

        conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, "
            "storage_state, keep) VALUES (?, ?, ?, ?, 900, 4000000, "
            "'local', 'local_only', 0)",
            ("equipo-propio",
             f"equipo-propio/2026/10/{dia}/rec_{indice:03d}.mp4",
             f"2026-10-{dia}T{hora:02d}:00:00+00:00",
             f"2026-10-{dia}T{hora:02d}:15:00+00:00")
        )

    # Dos con el MISMO started_at. Es el caso que rompe la paginacion
    # cuando el orden no tiene desempate: existe de verdad en la base
    # de desarrollo, porque el catalogo local fecha los archivos por
    # su hora de modificacion.
    for sufijo in ("gemela_a", "gemela_b"):
        conexion.execute(
            "INSERT INTO recordings (device_id, path, started_at, "
            "ended_at, duration_sec, size_bytes, status, "
            "storage_state, keep) VALUES (?, ?, "
            "'2026-10-09T07:00:00+00:00', "
            "'2026-10-09T07:15:00+00:00', 900, 4000000, 'local', "
            "'local_only', 0)",
            ("equipo-propio", f"equipo-propio/2026/10/09/{sufijo}.mp4")
        )

    # Una archivada en el servidor y una de la otra empresa.
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, ended_at, "
        "duration_sec, size_bytes, status, storage_state, keep) "
        "VALUES ('equipo-propio', "
        "'equipo-propio/2026/10/09/rec_guardada.mp4', "
        "'2026-10-09T06:00:00+00:00', '2026-10-09T06:15:00+00:00', "
        "900, 5000000, 'stored', 'server_stored', 0)"
    )

    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, ended_at, "
        "duration_sec, size_bytes, status, storage_state, keep) "
        "VALUES ('equipo-ajeno', 'equipo-ajeno/2026/10/11/rec_x.mp4', "
        "'2026-10-11T10:00:00+00:00', '2026-10-11T10:15:00+00:00', "
        "900, 100, 'local', 'local_only', 0)"
    )

    # En cuarentena: no debe contarse ni aparecer en ninguna pagina.
    conexion.execute(
        "INSERT INTO recordings (device_id, path, started_at, ended_at, "
        "duration_sec, size_bytes, status, storage_state, keep) "
        "VALUES ('equipo-propio', "
        "'equipo-propio/2026/10/11/rec_mala.mp4', "
        "'2026-10-11T11:00:00+00:00', '2026-10-11T11:15:00+00:00', "
        "900, 100, 'invalid', 'local_only', 0)"
    )

    conexion.commit()
    conexion.close()

    escenario.update({"propia": propia["id"], "ajena": ajena["id"]})

    return escenario


# Lo que debe ver el Owner de la empresa propia: 25 + 2 gemelas + 1
# archivada. Ni la de la otra empresa ni la que esta en cuarentena.
VISIBLES = SEMBRADAS + 3


def pagina(cliente, desde=0, limite=10, extra=""):
    """Una pagina tal y como la pide el panel."""

    return cliente.get(
        f"/api/recordings?limit={limite}&offset={desde}{extra}"
    ).json()


# ==============================
# PAGINACION
# ==============================

def test_la_primera_consulta_trae_diez():

    preparar()

    cuerpo = pagina(h.cliente(h.OWNER))

    comprobar("La primera pagina trae 10 grabaciones",
              len(cuerpo.get("recordings") or []) == 10,
              str(len(cuerpo.get("recordings") or [])))

    comprobar("Y dice cuantas hay en total",
              cuerpo.get("total") == VISIBLES,
              f"{cuerpo.get('total')} (se esperaban {VISIBLES})")

    comprobar("Y que todavia quedan mas",
              cuerpo.get("has_more") is True,
              str(cuerpo.get("has_more")))


def test_ver_mas_trae_las_siguientes_diez():

    preparar()

    cliente = h.cliente(h.OWNER)

    primera = pagina(cliente, 0)
    segunda = pagina(cliente, 10)

    comprobar("La segunda pagina trae otras 10",
              len(segunda.get("recordings") or []) == 10,
              str(len(segunda.get("recordings") or [])))

    ids_primera = [r["id"] for r in primera["recordings"]]
    ids_segunda = [r["id"] for r in segunda["recordings"]]

    comprobar("Sin repetir ninguna de la primera",
              not (set(ids_primera) & set(ids_segunda)),
              str(sorted(set(ids_primera) & set(ids_segunda))))


def test_recorrer_todo_no_omite_ni_repite():
    """Lo que de verdad importa: pagina a pagina sale el total exacto."""

    preparar()

    cliente = h.cliente(h.OWNER)

    vistas = []
    desde = 0

    while True:

        cuerpo = pagina(cliente, desde)
        filas = cuerpo.get("recordings") or []

        if not filas:
            break

        vistas.extend(r["id"] for r in filas)
        desde += len(filas)

        if not cuerpo.get("has_more"):
            break

    comprobar("Recorriendo las paginas salen todas",
              len(vistas) == VISIBLES,
              f"{len(vistas)} de {VISIBLES}")

    comprobar("Y ninguna repetida",
              len(vistas) == len(set(vistas)),
              f"{len(vistas) - len(set(vistas))} repetidas")


def test_el_orden_es_descendente_y_estable():

    preparar()

    cliente = h.cliente(h.OWNER)

    primera = [
        r["started_at"] for r in pagina(cliente, 0)["recordings"]
    ]

    comprobar("Las mas recientes van primero",
              primera == sorted(primera, reverse=True),
              str(primera[:3]))

    # Dos consultas identicas deben dar exactamente lo mismo. Con las
    # dos gemelas en la base, un orden ambiguo se delata aqui.
    uno = [r["id"] for r in pagina(cliente, 20)["recordings"]]
    dos = [r["id"] for r in pagina(cliente, 20)["recordings"]]

    comprobar("Y el orden no cambia entre consultas iguales",
              uno == dos, f"{uno} vs {dos}")

    # Las gemelas comparten started_at: el desempate por id las
    # coloca siempre igual, asi que ninguna se cae entre paginas.
    todas = []
    for desde in range(0, VISIBLES, 10):
        todas.extend(r["id"] for r in pagina(cliente, desde)["recordings"])

    comprobar("Las dos grabaciones con la misma fecha salen las dos",
              len(todas) == len(set(todas)) == VISIBLES,
              f"{len(todas)} filas, {len(set(todas))} distintas")


def test_el_ultimo_bloque_cierra_el_boton():

    preparar()

    cliente = h.cliente(h.OWNER)

    ultima = pagina(cliente, VISIBLES - 3)

    comprobar("La ultima pagina trae las que quedan",
              len(ultima.get("recordings") or []) == 3,
              str(len(ultima.get("recordings") or [])))

    comprobar("Y dice que ya no hay mas",
              ultima.get("has_more") is False,
              str(ultima.get("has_more")))

    vacia = pagina(cliente, VISIBLES + 50)

    comprobar("Pedir mas alla del final devuelve vacio, no un error",
              vacia.get("recordings") == [] and vacia.get("status") == "ok",
              str(vacia)[:90])

    comprobar("Y tampoco ofrece seguir",
              vacia.get("has_more") is False,
              str(vacia.get("has_more")))


def test_los_filtros_se_mantienen_al_paginar():

    preparar()

    cliente = h.cliente(h.OWNER)

    filtro = "&device_id=equipo-propio"

    primera = pagina(cliente, 0, extra=filtro)
    segunda = pagina(cliente, 10, extra=filtro)

    comprobar("El filtro de equipo se aplica en la primera pagina",
              all(r["device_id"] == "equipo-propio"
                  for r in primera["recordings"]),
              "hay filas de otro equipo")

    comprobar("Y tambien en la segunda",
              all(r["device_id"] == "equipo-propio"
                  for r in segunda["recordings"]),
              "hay filas de otro equipo")

    comprobar("El total es el del filtro, no el de todo",
              primera.get("total") == VISIBLES,
              str(primera.get("total")))


def test_el_rango_de_un_dia_se_cuenta_bien():

    preparar()

    cliente = h.cliente(h.OWNER)

    rango = (
        "&start=2026-10-10T00:00:00%2B00:00"
        "&end=2026-10-10T23:59:59%2B00:00"
    )

    cuerpo = pagina(cliente, 0, extra=rango)

    # El dia 10 lleva 6 grabaciones sembradas.
    comprobar("Un dia completo trae solo las de ese dia",
              cuerpo.get("total") == 6,
              str(cuerpo.get("total")))

    comprobar("Y todas caen dentro del dia pedido",
              all(r["started_at"].startswith("2026-10-10")
                  for r in cuerpo["recordings"]),
              str([r["started_at"] for r in cuerpo["recordings"]][:3]))

    comprobar("Con el filtro puesto ya no quedan mas paginas",
              cuerpo.get("has_more") is False,
              str(cuerpo.get("has_more")))


def test_un_rango_imposible_se_explica():
    """Devolver una lista vacia parecia perdida de datos."""

    preparar()

    cuerpo = pagina(
        h.cliente(h.OWNER), 0,
        extra=("&start=2026-10-10T20:00:00%2B00:00"
               "&end=2026-10-10T08:00:00%2B00:00")
    )

    comprobar("Un rango invertido no se contesta con una lista vacia",
              cuerpo.get("status") == "error",
              str(cuerpo)[:90])

    comprobar("Y el motivo se puede leer",
              "anterior" in (cuerpo.get("message") or "").lower(),
              str(cuerpo.get("message"))[:90])


def test_sin_limite_responde_como_siempre():
    """Hay llamadas que necesitan el historial entero."""

    preparar()

    cuerpo = h.cliente(h.OWNER).get("/api/recordings").json()

    comprobar("Sin 'limit' siguen llegando todas",
              len(cuerpo.get("recordings") or []) == VISIBLES,
              str(len(cuerpo.get("recordings") or [])))

    comprobar("Y la respuesta conserva su forma de siempre",
              isinstance(cuerpo, dict) and "recordings" in cuerpo,
              str(sorted(cuerpo))[:90])


def test_el_limite_tiene_tope():
    """Un 'limit' de la URL no puede pedir el historial entero."""

    preparar()

    cuerpo = pagina(h.cliente(h.OWNER), 0, limite=100000)

    comprobar("Un limite enorme se recorta al tope del servidor",
              len(cuerpo.get("recordings") or []) <= grabaciones.MAX_PAGE_SIZE,
              str(len(cuerpo.get("recordings") or [])))


# ==============================
# AISLAMIENTO ENTRE EMPRESAS
# ==============================

def test_la_paginacion_no_filtra_grabaciones_ajenas():

    preparar()

    cliente = h.cliente(h.OWNER)

    todas = []
    desde = 0

    while True:

        cuerpo = pagina(cliente, desde)
        filas = cuerpo.get("recordings") or []

        if not filas:
            break

        todas.extend(filas)
        desde += len(filas)

        if not cuerpo.get("has_more"):
            break

    comprobar("Ninguna pagina trae grabaciones de la otra empresa",
              all(r["device_id"] != "equipo-ajeno" for r in todas),
              "hay filas ajenas")

    comprobar("Y el total tampoco las cuenta",
              len(todas) == VISIBLES, str(len(todas)))


def test_pedir_un_equipo_ajeno_devuelve_vacio():

    preparar()

    cuerpo = pagina(
        h.cliente(h.OWNER), 0, extra="&device_id=equipo-ajeno"
    )

    comprobar("Filtrar por un equipo ajeno no trae nada",
              (cuerpo.get("recordings") or []) == [],
              str(cuerpo.get("recordings"))[:90])

    comprobar("Y el total es cero, no el de la otra empresa",
              cuerpo.get("total") == 0, str(cuerpo.get("total")))

    comprobar("Sin ofrecer mas paginas",
              cuerpo.get("has_more") is False,
              str(cuerpo.get("has_more")))


def test_la_cuarentena_sigue_fuera():

    preparar()

    cuerpo = pagina(h.cliente(h.OWNER), 0, limite=200)

    rutas = [r["path"] for r in cuerpo["recordings"]]

    comprobar("Una grabacion en cuarentena no se lista",
              not any("rec_mala" in ruta for ruta in rutas),
              "aparece rec_mala.mp4")


# ==============================
# REPRODUCCION LOCAL
# ==============================

class AgenteSimulado:
    """
    Un Agent que contesta a 'recording_stream_start'.

    No es un equipo real: no hay Windows, ni disco, ni WebSocket. Lo
    que comprueba es el camino del backend —permisos, cancelacion,
    Range y cabeceras—, no que el Agent lea bien un MP4.
    """

    def __init__(self, contenido=b"", fallo=None):
        self.contenido = contenido
        self.fallo = fallo
        self.enviados = []
        self.abortos = []

    async def send_text(self, mensaje):

        self.enviados.append(mensaje)

        orden, _, carga = mensaje.partition(":")
        datos = json.loads(carga) if carga else {}

        transfer_id = datos.get("transfer_id")

        if orden == "recording_stream_abort":
            self.abortos.append(transfer_id)
            return

        if orden != "recording_stream_start":
            return

        transmision = servidor.recording_streams.get(transfer_id)

        if not transmision:
            return

        if self.fallo:
            transmision["meta"].set_result({"error": self.fallo})
            return

        total = len(self.contenido)

        # 'suffix' son los ultimos N bytes: el Agent real lo resuelve
        # aqui, que es donde se conoce el tamano del archivo.
        sufijo = datos.get("suffix")

        if sufijo is not None:
            offset = max(0, total - int(sufijo))
            largo = total - offset

        else:

            offset = int(datos.get("offset") or 0)

            # Un trozo que empieza mas alla del final no es una
            # averia: es un Range imposible, y se marca como tal para
            # que el servidor conteste 416 y no 502.
            if offset >= total and total > 0:
                transmision["meta"].set_result({
                    "error": "El trozo pedido esta fuera del archivo",
                    "code": "range",
                    "size": total
                })
                return

            pedido = datos.get("length")

            largo = (
                total - offset if pedido is None
                else max(0, min(int(pedido), total - offset))
            )

        transmision["meta"].set_result({
            "transfer_id": transfer_id,
            "filename": "rec.mp4",
            "size": total,
            "offset": offset,
            "length": largo
        })

        if largo:
            transmision["queue"].put_nowait(
                self.contenido[offset:offset + largo]
            )

        transmision["queue"].put_nowait(None)


def con_agente(device_id, agente):
    """Conecta el Agent simulado y lo retira al terminar."""

    class Contexto:

        def __enter__(self):

            servidor.connected_agents[device_id] = agente

            # El Agent declara lo que sabe hacer al conectarse. Sin
            # esto el servidor lo toma por una version anterior, que
            # es justo lo que comprueba
            # test_un_agente_antiguo_se_distingue().
            servidor.agent_capabilities[device_id] = {
                "version": "1.4.0",
                "capabilities": ["recording_stream"]
            }

            return agente

        def __exit__(self, *_):
            servidor.connected_agents.pop(device_id, None)
            servidor.agent_capabilities.pop(device_id, None)
            servidor.recording_streams.clear()

    return Contexto()


def una_local():
    """El id de una grabacion que solo esta en el equipo."""

    conexion = database.get_connection()

    fila = conexion.execute(
        "SELECT id FROM recordings WHERE device_id = 'equipo-propio' "
        "AND status = 'local' ORDER BY id LIMIT 1"
    ).fetchone()

    conexion.close()

    return fila["id"]


VIDEO = b"VIDEOFALSO" * 500


def test_una_grabacion_local_se_puede_ver_sin_subirla():

    preparar()

    recording_id = una_local()

    with con_agente("equipo-propio", AgenteSimulado(VIDEO)):

        respuesta = h.cliente(h.OWNER).get(
            f"/api/recordings/{recording_id}/local-video"
        )

    comprobar("El video local se entrega", respuesta.status_code == 200,
              str(respuesta.status_code))

    comprobar("Y es el contenido del equipo",
              respuesta.content == VIDEO,
              f"{len(respuesta.content)} de {len(VIDEO)} bytes")

    comprobar("Se anuncia como video",
              respuesta.headers.get("content-type", "").startswith("video/"),
              respuesta.headers.get("content-type", ""))

    comprobar("Y admite saltos en el reproductor",
              respuesta.headers.get("accept-ranges") == "bytes",
              str(respuesta.headers.get("accept-ranges")))

    # Lo mas importante de todo: verla no la ha subido.
    fila = grabaciones.get_recording(recording_id)

    comprobar("Reproducirla NO la pasa a guardada en el servidor",
              fila["status"] == "local"
              and fila["storage_state"] == "local_only",
              f"{fila['status']} / {fila['storage_state']}")


def test_el_reproductor_puede_saltar_a_un_punto():

    preparar()

    recording_id = una_local()

    with con_agente("equipo-propio", AgenteSimulado(VIDEO)):

        respuesta = h.cliente(h.OWNER).get(
            f"/api/recordings/{recording_id}/local-video",
            headers={"Range": "bytes=100-199"}
        )

    comprobar("Un salto se contesta con 206",
              respuesta.status_code == 206,
              str(respuesta.status_code))

    comprobar("Y llega solo el trozo pedido",
              respuesta.content == VIDEO[100:200],
              f"{len(respuesta.content)} bytes")

    comprobar("Con el rango bien declarado",
              respuesta.headers.get("content-range")
              == f"bytes 100-199/{len(VIDEO)}",
              str(respuesta.headers.get("content-range")))


def test_un_rango_fuera_del_archivo_se_rechaza():

    preparar()

    recording_id = una_local()

    with con_agente("equipo-propio", AgenteSimulado(VIDEO)):

        respuesta = h.cliente(h.OWNER).get(
            f"/api/recordings/{recording_id}/local-video",
            headers={"Range": "bytes=999999-1000000"}
        )

    comprobar("Pedir un trozo que no existe da 416",
              respuesta.status_code == 416,
              str(respuesta.status_code))


def test_el_equipo_desconectado_se_dice_con_claridad():

    preparar()

    recording_id = una_local()

    servidor.connected_agents.pop("equipo-propio", None)

    respuesta = h.cliente(h.OWNER).get(
        f"/api/recordings/{recording_id}/local-video"
    )

    comprobar("Sin equipo conectado no se inventa un video",
              respuesta.status_code == 409,
              str(respuesta.status_code))

    comprobar("Y se explica por que",
              "desconectado" in respuesta.text.lower(),
              respuesta.text[:90])


def test_un_archivo_que_ya_no_esta_se_informa():

    preparar()

    recording_id = una_local()

    agente = AgenteSimulado(
        fallo="La grabacion ya no esta en este equipo"
    )

    with con_agente("equipo-propio", agente):

        respuesta = h.cliente(h.OWNER).get(
            f"/api/recordings/{recording_id}/local-video"
        )

    comprobar("Un archivo borrado en el equipo no da 200",
              respuesta.status_code == 502,
              str(respuesta.status_code))

    comprobar("Y el motivo llega hasta el panel",
              "ya no esta" in respuesta.text.lower(),
              respuesta.text[:90])


def test_no_se_puede_ver_la_grabacion_de_otra_empresa():

    preparar()

    conexion = database.get_connection()

    ajena = conexion.execute(
        "SELECT id FROM recordings WHERE device_id = 'equipo-ajeno' "
        "LIMIT 1"
    ).fetchone()["id"]

    conexion.close()

    with con_agente("equipo-ajeno", AgenteSimulado(VIDEO)):

        respuesta = h.cliente(h.OWNER).get(
            f"/api/recordings/{ajena}/local-video"
        )

    comprobar("Un equipo ajeno no se puede reproducir",
              respuesta.status_code == 404,
              str(respuesta.status_code))

    comprobar("Y no se filtra ni un byte del video",
              VIDEO[:20] not in respuesta.content,
              "el contenido se ha filtrado")


def test_un_agente_antiguo_se_distingue():
    """
    Un Agent anterior no declara saber transmitir.

    Lo que NO debe pasar: que se presente como un error de archivo,
    ni que haya que agotar el tiempo de espera para contestar.
    """

    preparar()

    recording_id = una_local()

    servidor.connected_agents["equipo-propio"] = AgenteSimulado(VIDEO)
    servidor.agent_capabilities.pop("equipo-propio", None)

    try:
        respuesta = h.cliente(h.OWNER).get(
            f"/api/recordings/{recording_id}/local-video"
        )

    finally:
        servidor.connected_agents.pop("equipo-propio", None)
        servidor.recording_streams.clear()

    comprobar("Un Agent antiguo no da un error de archivo",
              respuesta.status_code == 409,
              str(respuesta.status_code))

    cuerpo = respuesta.json()

    comprobar("Se reconoce por un codigo, no por el texto",
              cuerpo.get("code") == "agent_sin_transmision",
              str(cuerpo.get("code")))

    comprobar("Y se ofrece guardarla en el servidor",
              "servidor" in (cuerpo.get("message") or "").lower(),
              str(cuerpo.get("message"))[:90])


def test_sin_sesion_no_hay_video():

    preparar()

    recording_id = una_local()

    with con_agente("equipo-propio", AgenteSimulado(VIDEO)):

        respuesta = h.cliente(None).get(
            f"/api/recordings/{recording_id}/local-video"
        )

    comprobar("Sin sesion no se entrega el video",
              respuesta.status_code in (401, 403),
              str(respuesta.status_code))


def test_al_terminar_no_queda_la_transmision_colgada():

    preparar()

    recording_id = una_local()

    with con_agente("equipo-propio", AgenteSimulado(VIDEO)) as agente:

        h.cliente(h.OWNER).get(
            f"/api/recordings/{recording_id}/local-video"
        )

        comprobar("Al acabar no queda ninguna transmision abierta",
                  not servidor.recording_streams,
                  str(list(servidor.recording_streams)))

        comprobar("Y se le dice al equipo que deje de leer el archivo",
                  len(agente.abortos) >= 1,
                  str(agente.abortos))


def test_el_equipo_se_desconecta_a_media_transmision():

    preparar()

    transfer_id = "prueba"
    bucle = asyncio.new_event_loop()

    try:

        servidor.recording_streams[transfer_id] = {
            "device_id": "equipo-propio",
            "meta": bucle.create_future(),
            "queue": asyncio.Queue()
        }

        servidor.fail_device_recording_streams("equipo-propio")

        meta = servidor.recording_streams[transfer_id]["meta"]

        comprobar("La desconexion corta la transmision en curso",
                  meta.done() and meta.result().get("error"),
                  str(meta.done()))

    finally:
        servidor.recording_streams.clear()
        bucle.close()


# ==============================
# LO QUE NO DEBE CAMBIAR
# ==============================

def test_el_video_del_servidor_sigue_funcionando():

    preparar()

    conexion = database.get_connection()

    guardada = conexion.execute(
        "SELECT id FROM recordings WHERE status = 'stored' LIMIT 1"
    ).fetchone()["id"]

    conexion.close()

    respuesta = h.cliente(h.OWNER).get(
        f"/api/recordings/{guardada}/video"
    )

    # El archivo no existe en disco en esta base de pruebas, pero lo
    # que importa es que NO se rechaza por estado: llega hasta el
    # punto de buscar el archivo.
    comprobar("Una grabacion guardada no se rechaza por su estado",
              respuesta.status_code != 409,
              str(respuesta.status_code))


def test_conservar_sigue_funcionando():

    preparar()

    recording_id = una_local()

    respuesta = h.cliente(h.OWNER).post(
        f"/api/recordings/{recording_id}/keep", json={"keep": True}
    )

    comprobar("Conservar sigue respondiendo",
              respuesta.status_code == 200,
              str(respuesta.status_code))

    fila = grabaciones.get_recording(recording_id)

    comprobar("Y marca la grabacion", fila["keep"] == 1,
              str(fila["keep"]))

    comprobar("Sin cambiar donde esta guardada",
              fila["storage_state"] == "local_only",
              str(fila["storage_state"]))


def test_el_catalogo_local_no_se_duplica():
    """Reenviar el catalogo al reconectar no puede crear filas."""

    preparar()

    antes = grabaciones.count_recordings()

    ficha = {
        "filename": "rec_000.mp4",
        "started_at": "2026-10-10T08:00:00+00:00",
        "ended_at": "2026-10-10T08:15:00+00:00",
        "duration_sec": 900,
        "size_bytes": 4000000
    }

    for _ in range(3):
        servidor.registrar_ficha_local("equipo-propio", ficha)

    comprobar("Reenviar la misma ficha tres veces no duplica nada",
              grabaciones.count_recordings() == antes,
              f"{grabaciones.count_recordings()} vs {antes}")


def test_una_grabacion_archivada_no_vuelve_a_local():

    preparar()

    conexion = database.get_connection()

    fila = conexion.execute(
        "SELECT id, path FROM recordings WHERE status = 'stored' LIMIT 1"
    ).fetchone()

    conexion.close()

    servidor.registrar_ficha_local("equipo-propio", {
        "filename": os.path.basename(fila["path"]),
        "started_at": "2026-10-09T06:00:00+00:00",
        "ended_at": "2026-10-09T06:15:00+00:00",
        "duration_sec": 900,
        "size_bytes": 5000000
    })

    actual = grabaciones.get_recording(fila["id"])

    comprobar("Una ficha repetida no degrada una grabacion ya guardada",
              actual["status"] == "stored",
              str(actual["status"]))


# ==============================
# EL PANEL
# ==============================

def test_el_panel_pagina_de_verdad():

    comprobar("El panel pide paginas de 10",
              "GRABACIONES_POR_PAGINA = 10" in JS,
              "no se encuentra el tamano de pagina")

    comprobar("Y manda limit y offset al servidor",
              'params.set("limit"' in JS and 'params.set("offset"' in JS,
              "falta algun parametro")

    comprobar("Existe el boton 'Ver mas'",
              'id="recordings-more-button"' in JS and "Ver más" in JS,
              "no se encuentra el boton")

    comprobar("Y su sitio esta en la pagina",
              'id="recordings-footer"' in HTML,
              "falta el pie de la tabla")

    comprobar("Se ensena cuantas se ven de cuantas",
              "de ${grabacionesTotales}" in JS,
              "no se muestra el total")

    comprobar("El boton se esconde cuando ya no quedan",
              "hayMas ?" in JS, "el boton no depende de que queden mas")

    comprobar("Y avisa mientras carga",
              'boton.textContent = "Cargando..."' in JS,
              "no hay indicador de carga")


def test_el_panel_no_repinta_con_respuestas_viejas():

    comprobar("Cada busqueda lleva numero",
              "consultaDeGrabaciones" in JS,
              "no hay control de consulta en curso")

    comprobar("Y una respuesta tardia se descarta",
              "mia !== consultaDeGrabaciones" in JS,
              "no se comparan los numeros")

    comprobar("Un segundo clic en 'Ver mas' no pide lo mismo dos veces",
              "cargandoMasGrabaciones" in JS,
              "no hay barrera contra el doble clic")

    comprobar("Y las filas repetidas se descartan por id",
              "yaEstan.has(rec.id)" in JS,
              "no se comprueban duplicados")


def test_el_panel_filtra_un_dia_completo():

    comprobar("Una fecha sin horas cubre el dia entero",
              'startTime || "00:00"' in JS and 'endTime || "23:59"' in JS,
              "la fecha sola sigue sin filtrar")

    comprobar("Y el ultimo minuto entra entero",
              "setSeconds(59, 999)" in JS,
              "el minuto final se corta")


def test_el_panel_distingue_donde_esta_cada_grabacion():

    comprobar("Hay un estado para el equipo desconectado",
              "local_desconectado" in JS,
              "no se distingue el equipo desconectado")

    comprobar("Y depende de que el equipo este en linea",
              "equiposConectados.has(rec.device_id)" in JS,
              "no se mira la conexion")

    comprobar("Una grabacion local se abre desde el equipo",
              "/local-video" in JS,
              "el panel no usa la transmision local")

    comprobar("Y se dice que no se sube al servidor",
              "No se sube al servidor" in JS,
              "no se aclara que ver no es subir")


def test_el_panel_suelta_el_video_anterior():

    comprobar("Cambiar de grabacion detiene la anterior",
              JS.count("video.removeAttribute(\"src\")") >= 2,
              "solo se suelta al cerrar")

    comprobar("Y se comprueba el origen antes de pintar el error generico",
              "mostrarErrorDeReproduccion" in JS,
              "no hay causa util")

    comprobar("El titulo se rehace con cada grabacion",
              "grabacionEnReproduccion = id" in JS,
              "el titulo puede quedarse del anterior")


def test_el_agente_solo_lee_su_carpeta():

    comprobar("La transmision busca por NOMBRE, no por ruta",
              "find_managed_recording(data.get(\"filename\"))" in AGENTE,
              "se acepta una ruta")

    comprobar("Y se puede cancelar",
              "recording_stream_abort" in AGENTE,
              "no hay forma de cancelar")

    comprobar("El envio no bloquea el bucle de mensajes",
              "asyncio.create_task(" in AGENTE
              and "_enviar_grabacion" in AGENTE,
              "el envio es sincrono y sordo a la cancelacion")

    comprobar("El endpoint manda solo el nombre del archivo",
              "_os.path.basename(str(grabacion.get(\"path\")" in
              io.open(RAIZ / "backend" / "main.py",
                      encoding="utf-8").read(),
              "el backend manda una ruta")


# ==============================

def main():

    pruebas = [
        test_la_primera_consulta_trae_diez,
        test_ver_mas_trae_las_siguientes_diez,
        test_recorrer_todo_no_omite_ni_repite,
        test_el_orden_es_descendente_y_estable,
        test_el_ultimo_bloque_cierra_el_boton,
        test_los_filtros_se_mantienen_al_paginar,
        test_el_rango_de_un_dia_se_cuenta_bien,
        test_un_rango_imposible_se_explica,
        test_sin_limite_responde_como_siempre,
        test_el_limite_tiene_tope,
        test_la_paginacion_no_filtra_grabaciones_ajenas,
        test_pedir_un_equipo_ajeno_devuelve_vacio,
        test_la_cuarentena_sigue_fuera,
        test_una_grabacion_local_se_puede_ver_sin_subirla,
        test_el_reproductor_puede_saltar_a_un_punto,
        test_un_rango_fuera_del_archivo_se_rechaza,
        test_el_equipo_desconectado_se_dice_con_claridad,
        test_un_archivo_que_ya_no_esta_se_informa,
        test_no_se_puede_ver_la_grabacion_de_otra_empresa,
        test_un_agente_antiguo_se_distingue,
        test_sin_sesion_no_hay_video,
        test_al_terminar_no_queda_la_transmision_colgada,
        test_el_equipo_se_desconecta_a_media_transmision,
        test_el_video_del_servidor_sigue_funcionando,
        test_conservar_sigue_funcionando,
        test_el_catalogo_local_no_se_duplica,
        test_una_grabacion_archivada_no_vuelve_a_local,
        test_el_panel_pagina_de_verdad,
        test_el_panel_no_repinta_con_respuestas_viejas,
        test_el_panel_filtra_un_dia_completo,
        test_el_panel_distingue_donde_esta_cada_grabacion,
        test_el_panel_suelta_el_video_anterior,
        test_el_agente_solo_lee_su_carpeta
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepcion)", False,
                      type(error).__name__ + ": " + str(error)[:120])

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
