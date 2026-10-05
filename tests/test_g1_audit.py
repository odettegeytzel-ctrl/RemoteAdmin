"""
Pruebas del bloque G1: registro de auditoría de acciones.

Se ejecutan con python directamente, sin pytest ni dependencias nuevas:

    python tests/test_g1_audit.py

Nunca tocan la base real: se crea una base temporal y se apunta a ella.
"""

import io
import os
import re
import sys
import tempfile

from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import backend.database as database


# La base real no se abre en ningún momento de estas pruebas.
BASE_TEMPORAL = Path(tempfile.mkdtemp(prefix="g1_audit_")) / "prueba.db"
database.DATABASE_PATH = BASE_TEMPORAL

from backend import audit


resultados = []


def comprobar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion), detalle))


def limpiar():
    conexion = database.get_connection()
    conexion.execute("DELETE FROM audit_log")
    conexion.commit()
    conexion.close()


# ==============================
# 1. Esquema
# ==============================

def test_tabla_y_columnas():

    database.init_db()

    conexion = database.get_connection()

    columnas = {
        fila["name"]
        for fila in conexion.execute("PRAGMA table_info(audit_log)")
    }

    indices = {
        fila["name"]
        for fila in conexion.execute("PRAGMA index_list(audit_log)")
    }

    conexion.close()

    # Las ocho originales. La comprobacion es de inclusion y no de
    # igualdad a proposito: las migraciones posteriores son ADITIVAS, y
    # lo que hay que vigilar es que no desaparezca ninguna, no que no
    # aparezcan nuevas.
    esperadas = {
        "id", "timestamp", "action", "device_id",
        "username", "source_ip", "status", "details"
    }

    comprobar(
        "La tabla audit_log conserva sus ocho columnas originales",
        esperadas <= columnas,
        f"faltan: {sorted(esperadas - columnas)}"
    )

    comprobar(
        "Y lleva la organizacion que anadio el bloque multiempresa",
        "organization_id" in columnas,
        f"encontradas: {sorted(columnas)}"
    )

    comprobar(
        "Existen los índices de timestamp, device_id y action",
        {"idx_audit_timestamp", "idx_audit_device",
         "idx_audit_action"} <= indices,
        f"índices: {sorted(indices)}"
    )


def test_migracion_es_aditiva():
    """Volver a inicializar no borra lo que ya había."""

    limpiar()

    audit.log_audit("device.ping", device_id="equipo-1")

    database.init_db()

    comprobar(
        "init_db() repetido no borra registros existentes",
        audit.count_audit() == 1
    )


# ==============================
# 2. Escritura
# ==============================

def test_registro_basico():

    limpiar()

    ok = audit.log_audit(
        "device.ping",
        device_id="equipo-1",
        status=audit.STATUS_REQUESTED,
        details="Comprobación de conexión",
        username="odette",
        source_ip="192.168.88.10"
    )

    filas = audit.list_audit()

    comprobar("log_audit() devuelve True al escribir", ok)
    comprobar("Se guarda exactamente una fila", len(filas) == 1)

    if filas:
        fila = filas[0]
        comprobar("Se guarda la acción", fila["action"] == "device.ping")
        comprobar("Se guarda el equipo", fila["device_id"] == "equipo-1")
        comprobar("Se guarda el usuario", fila["username"] == "odette")
        comprobar("Se guarda la IP", fila["source_ip"] == "192.168.88.10")
        comprobar("Se guarda el estado", fila["status"] == "requested")
        comprobar(
            "El timestamp es ISO-8601 con zona horaria",
            bool(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
                          fila["timestamp"] or ""))
            and ("+" in (fila["timestamp"] or "")
                 or (fila["timestamp"] or "").endswith("Z")),
            fila["timestamp"]
        )


def test_estado_por_defecto_es_requested():
    """Una acción sin confirmación del Agent no puede figurar como exitosa."""

    limpiar()

    audit.log_audit("device.ping", device_id="equipo-1")

    comprobar(
        "El estado por defecto es 'requested', no 'success'",
        audit.list_audit()[0]["status"] == audit.STATUS_REQUESTED
    )


def test_log_audit_nunca_lanza():
    """Si la auditoría falla, la acción del usuario no debe romperse."""

    original = database.DATABASE_PATH
    database.DATABASE_PATH = Path("Z:/ruta/que/no/existe/x.db")

    try:
        devuelto = audit.log_audit("device.ping", device_id="equipo-1")
        lanzo = False
    except Exception:
        devuelto = None
        lanzo = True
    finally:
        database.DATABASE_PATH = original

    comprobar("log_audit() no propaga errores de base de datos", not lanzo)
    comprobar("log_audit() devuelve False cuando no puede escribir",
              devuelto is False)


# ==============================
# 3. Secretos
# ==============================

def test_no_guarda_secretos_por_nombre_de_clave():

    limpiar()

    audit.log_audit(
        "auth.test",
        details={
            "password": "remoteadmin123",
            "token": "abc",
            "agent_token": "xyz",
            "cookie": "remoteadmin_token=...",
            "authorization": "Bearer algo",
            "private_key": "-----BEGIN",
            "filename": "informe.pdf"
        }
    )

    texto = audit.list_audit()[0]["details"]

    for secreto in ("remoteadmin123", "Bearer algo", "BEGIN",
                    "remoteadmin_token="):
        comprobar(
            f"No aparece el valor sensible ({secreto[:14]})",
            secreto not in texto,
            texto
        )

    comprobar("Sí se conserva el dato inocuo (filename)",
              "informe.pdf" in texto, texto)


def test_no_guarda_valores_con_pinta_de_token():

    limpiar()

    token = "a" * 40

    audit.log_audit("auth.test", details={"nota": token})

    texto = audit.list_audit()[0]["details"]

    comprobar(
        "Un valor largo tipo token se oculta aunque la clave sea inocente",
        token not in texto,
        texto
    )


def test_oculta_secretos_anidados():

    limpiar()

    audit.log_audit(
        "auth.test",
        details={"peticion": {"cabeceras": {"Authorization": "Bearer S3CR3T"}}}
    )

    texto = audit.list_audit()[0]["details"]

    comprobar("Los secretos anidados también se ocultan",
              "S3CR3T" not in texto, texto)


def test_detalles_truncados():

    limpiar()

    audit.log_audit("device.ping", details="x" * 5000)

    texto = audit.list_audit()[0]["details"]

    comprobar(
        "Los detalles se recortan a la longitud máxima",
        len(texto) <= audit.MAX_DETAILS_LENGTH,
        f"longitud {len(texto)}"
    )


# ==============================
# 4. Contexto de la petición (no falsificable)
# ==============================

class PeticionFalsa:
    """Imita lo justo de Request para probar get_request_context()."""

    class _Cliente:
        def __init__(self, host):
            self.host = host

    def __init__(self, host="10.0.0.5", cookies=None, headers=None):
        self.client = self._Cliente(host) if host else None
        self.cookies = cookies or {}
        self.headers = headers or {}


def test_usuario_e_ip_salen_del_contexto_real():

    limpiar()

    peticion = PeticionFalsa(host="10.0.0.5")

    audit.log_audit(
        "device.ping",
        request=peticion,
        device_id="equipo-1",
        # Valores que un cliente malicioso intentaría imponer
        username="administrador_falso",
        source_ip="1.2.3.4"
    )

    fila = audit.list_audit()[0]

    comprobar(
        "Con request, el username pasado por parámetro se descarta",
        fila["username"] != "administrador_falso",
        str(fila["username"])
    )

    comprobar(
        "Con request, la IP se toma de la conexión, no del parámetro",
        fila["source_ip"] == "10.0.0.5",
        str(fila["source_ip"])
    )


def test_token_invalido_no_da_usuario():

    peticion = PeticionFalsa(
        cookies={"remoteadmin_token": "token-inventado"},
        headers={"Authorization": "Bearer token-inventado"}
    )

    usuario, ip = audit.get_request_context(peticion)

    comprobar("Un token no válido no produce usuario", usuario is None,
              str(usuario))
    comprobar("La IP se obtiene igualmente", ip == "10.0.0.5")


def test_sin_request_se_aceptan_los_parametros():
    """Las tareas internas no tienen petición; ahí sí se pasan a mano."""

    limpiar()

    audit.log_audit("retention.cleanup", username="sistema",
                    source_ip=None, status=audit.STATUS_SUCCESS)

    fila = audit.list_audit()[0]

    comprobar("Sin request se conserva el usuario indicado",
              fila["username"] == "sistema")


# ==============================
# 5. Consulta
# ==============================

def sembrar():

    limpiar()

    audit.log_audit("device.ping", device_id="equipo-1")
    audit.log_audit("device.ping", device_id="equipo-2")
    audit.log_audit("file.upload", device_id="equipo-1",
                    status=audit.STATUS_SUCCESS)
    audit.log_audit("file.download", device_id="equipo-2",
                    status=audit.STATUS_ERROR)


def test_orden_y_filtros():

    sembrar()

    todos = audit.list_audit()

    comprobar("Se devuelven los 4 registros", len(todos) == 4)
    comprobar(
        "El más reciente va primero",
        todos[0]["action"] == "file.download",
        todos[0]["action"]
    )

    por_equipo = audit.list_audit(device_id="equipo-1")

    comprobar(
        "El filtro por device_id devuelve solo ese equipo",
        len(por_equipo) == 2
        and all(f["device_id"] == "equipo-1" for f in por_equipo)
    )

    por_accion = audit.list_audit(action="device.ping")

    comprobar(
        "El filtro por action devuelve solo esa acción",
        len(por_accion) == 2
        and all(f["action"] == "device.ping" for f in por_accion)
    )

    combinado = audit.list_audit(device_id="equipo-2", action="device.ping")

    comprobar("Los dos filtros se combinan", len(combinado) == 1)

    comprobar("count_audit() cuenta el total", audit.count_audit() == 4)
    comprobar("count_audit() respeta el filtro",
              audit.count_audit(device_id="equipo-1") == 2)


def test_limite_acotado():

    sembrar()

    comprobar("El límite recorta el resultado",
              len(audit.list_audit(limit=2)) == 2)

    comprobar(
        "Un límite disparatado no arrastra el historial entero",
        len(audit.list_audit(limit=999999)) <= 500
    )

    comprobar("Un límite de 0 o negativo no rompe",
              len(audit.list_audit(limit=0)) >= 1)

    comprobar("El desplazamiento salta registros",
              audit.list_audit(limit=1, offset=1)[0]["action"]
              == "file.upload")


def test_filtro_no_es_inyectable():

    sembrar()

    filas = audit.list_audit(device_id="equipo-1' OR '1'='1")

    comprobar(
        "Un filtro con comillas no devuelve filas de más (consulta parametrizada)",
        len(filas) == 0
    )


# ==============================
# 6. Integración en el código del servidor (revisión estática)
# ==============================

def test_endpoints_instrumentados():

    codigo = io.open(RAIZ / "backend" / "main.py", encoding="utf-8").read()

    acciones = [
        "device.ping", "device.system_info", "device.software",
        "remote.session_start", "remote.session_stop",
        "recording.start", "recording.stop", "recording.continuous",
        "file.upload", "file.download",
        "auth.login", "auth.logout"
    ]

    faltan = [a for a in acciones if f'"{a}"' not in codigo]

    comprobar("Todas las acciones previstas se auditan en main.py",
              not faltan, f"faltan: {faltan}")

    comprobar(
        "Existe el endpoint de consulta de solo lectura",
        '@app.get("/api/audit")' in codigo
    )

    comprobar(
        "No hay ningún endpoint que escriba o borre auditoría",
        '@app.post("/api/audit' not in codigo
        and '@app.delete("/api/audit' not in codigo
        and '@app.put("/api/audit' not in codigo
    )

    comprobar(
        "El endpoint de auditoría no está en las rutas abiertas",
        "/api/audit" not in codigo.split("OPEN_API_PATHS")[1].split(")")[0]
        if "OPEN_API_PATHS" in codigo else False
    )

    comprobar(
        "No se auditan los eventos individuales de ratón y teclado",
        '"input.mouse"' not in codigo and '"input.keyboard"' not in codigo
    )

    comprobar(
        "El login fallido no registra el usuario tecleado",
        'data.get("username", "")' not in codigo.split(
            "Credenciales incorrectas")[0].split("register_failure")[-1]
    )


# ==============================

def main():

    pruebas = [
        test_tabla_y_columnas,
        test_migracion_es_aditiva,
        test_registro_basico,
        test_estado_por_defecto_es_requested,
        test_log_audit_nunca_lanza,
        test_no_guarda_secretos_por_nombre_de_clave,
        test_no_guarda_valores_con_pinta_de_token,
        test_oculta_secretos_anidados,
        test_detalles_truncados,
        test_usuario_e_ip_salen_del_contexto_real,
        test_token_invalido_no_da_usuario,
        test_sin_request_se_aceptan_los_parametros,
        test_orden_y_filtros,
        test_limite_acotado,
        test_filtro_no_es_inyectable,
        test_endpoints_instrumentados
    ]

    for prueba in pruebas:
        try:
            prueba()
        except Exception as error:
            comprobar(f"{prueba.__name__} (excepción)", False, repr(error))

    fallos = 0

    for nombre, ok, detalle in resultados:
        marca = "OK  " if ok else "FALLO"
        print(f"[{marca}] {nombre}" + (f"  -> {detalle}" if not ok else ""))
        if not ok:
            fallos += 1

    print(f"\n{len(resultados) - fallos}/{len(resultados)} comprobaciones "
          "correctas")

    try:
        os.remove(BASE_TEMPORAL)
    except OSError:
        pass

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
