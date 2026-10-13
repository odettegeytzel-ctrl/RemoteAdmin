# -*- coding: utf-8 -*-
"""
Respaldo y restauracion de VEYRA.

    python scripts/respaldo.py crear   [--destino CARPETA] [--sin-grabaciones]
    python scripts/respaldo.py verificar PAQUETE
    python scripts/respaldo.py probar    PAQUETE
    python scripts/respaldo.py restaurar PAQUETE --destino CARPETA

Por que no se copia el archivo y ya:

    La base esta abierta mientras el servicio corre. Copiarla con cp
    o shutil puede dar un archivo a medias —SQLite escribe en varias
    paginas y mantiene un diario aparte— y el resultado abre
    perfectamente y falla meses despues, cuando hace falta.

    Aqui se usa el mecanismo de respaldo de la propia SQLite
    (Connection.backup), que coordina con los escritores y garantiza
    una copia coherente. Es lo mismo que hace el comando .backup de
    su consola.

Que NO entra en el paquete, a proposito:

    Ni .env, ni certificados, ni claves, ni el archivo de entorno del
    servicio. Un respaldo se copia, se envia y se guarda en sitios
    donde un secreto no deberia estar. Lo que haya que conservar de
    la configuracion se guarda aparte y por otra via.

    El paquete lleva una lista de lo que el sistema NECESITA para
    arrancar, para que al restaurar se sepa que falta reponer a mano.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

from datetime import datetime, timezone


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.join(RAIZ, "remoteadmin.db")
AUDITORIA_PENDIENTE = os.path.join(RAIZ, "auditoria_pendiente.jsonl")
GRABACIONES = os.path.join(RAIZ, "server_recordings")


# Tablas cuyo recuento se guarda y se compara al verificar. No son
# todas: son las que, si pierden una fila, hay que enterarse.
TABLAS_VIGILADAS = (
    "organizations",
    "users",
    "user_permissions",
    "devices",
    "recordings",
    "audit_log",
    "organization_settings",
    "recording_schedule",
    "settings",
    "alerts",
)


# Lo que el sistema necesita y NO viaja en el paquete, por llevar
# secretos. Se nombra para que al restaurar se sepa que reponer.
FUERA_DEL_PAQUETE = (
    "/etc/remoteadmin/remoteadmin.env  (o .env): clave de sesion, "
    "SMTP, URL publica",
    "certificados TLS, si los hubiera fuera de Let's Encrypt",
    "la clave privada de firma del Agent, que nunca ha estado aqui",
)


def _ahora():
    return datetime.now(timezone.utc)


def _marca():
    return _ahora().strftime("%Y%m%d-%H%M%S")


def sha256(ruta):
    """Huella de un archivo, leido por bloques."""

    resumen = hashlib.sha256()

    with open(ruta, "rb") as archivo:

        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            resumen.update(bloque)

    return resumen.hexdigest()


def recuentos(ruta_base):
    """Filas por tabla, de las vigiladas que existan."""

    conexion = sqlite3.connect(f"file:{ruta_base}?mode=ro", uri=True)

    try:
        presentes = {
            fila[0] for fila in conexion.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        return {
            tabla: conexion.execute(
                f"SELECT COUNT(*) FROM {tabla}"
            ).fetchone()[0]
            for tabla in TABLAS_VIGILADAS
            if tabla in presentes
        }

    finally:
        conexion.close()


def integridad(ruta_base):
    """
    Lo que dice SQLite de su propia base.

    integrity_check recorre la estructura entera; foreign_key_check
    busca referencias rotas. Una base que pasa las dos es utilizable.
    """

    conexion = sqlite3.connect(f"file:{ruta_base}?mode=ro", uri=True)

    try:
        estructura = conexion.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        huerfanas = conexion.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        return {
            "integrity_check": estructura,
            "foreign_key_violations": len(huerfanas),
        }

    finally:
        conexion.close()


# ==============================
# CREAR
# ==============================

def copiar_base_en_caliente(origen, destino):
    """
    Copia coherente con el servicio en marcha.

    Connection.backup() es el mecanismo de SQLite para esto: se
    coordina con los escritores y entrega una copia utilizable
    aunque la base este recibiendo escrituras.
    """

    # Solo lectura sobre la original: este proceso no puede escribir
    # en la base de produccion ni por error.
    fuente = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)

    try:
        copia = sqlite3.connect(destino)

        try:
            fuente.backup(copia)

        finally:
            copia.close()

    finally:
        fuente.close()


def crear(destino=None, con_grabaciones=True):

    if not os.path.exists(BASE):
        print(f"ERROR: no se encuentra la base en {BASE}")
        return 1

    carpeta = destino or os.path.join(RAIZ, "respaldos")

    paquete = os.path.join(carpeta, f"veyra-{_marca()}")

    os.makedirs(paquete, exist_ok=False)

    print(f"Paquete: {paquete}")
    print()

    manifiesto = {
        "creado": _ahora().isoformat(),
        "origen": RAIZ,
        "archivos": {},
        "no_incluido": list(FUERA_DEL_PAQUETE),
    }

    # --- 1. La base, en caliente ---

    print("Base de datos...")

    copia_base = os.path.join(paquete, "remoteadmin.db")

    copiar_base_en_caliente(BASE, copia_base)

    estado = integridad(copia_base)

    if estado["integrity_check"] != "ok":
        print(f"  ERROR: la copia no pasa integrity_check: "
              f"{estado['integrity_check']}")
        return 1

    manifiesto["base"] = {
        "sha256": sha256(copia_base),
        "bytes": os.path.getsize(copia_base),
        "recuentos": recuentos(copia_base),
        "integridad": estado,
    }

    print(f"  integrity_check: {estado['integrity_check']}")
    print(f"  referencias rotas: "
          f"{estado['foreign_key_violations']}")
    print(f"  {os.path.getsize(copia_base)} bytes")

    for tabla, cuantas in manifiesto["base"]["recuentos"].items():
        print(f"    {tabla:24} {cuantas}")

    # --- 2. Auditoria pendiente ---

    if os.path.exists(AUDITORIA_PENDIENTE):

        print()
        print("Auditoria pendiente...")

        copia = os.path.join(paquete, "auditoria_pendiente.jsonl")
        shutil.copy2(AUDITORIA_PENDIENTE, copia)

        manifiesto["archivos"]["auditoria_pendiente.jsonl"] = {
            "sha256": sha256(copia),
            "bytes": os.path.getsize(copia),
        }

        print(f"  {os.path.getsize(copia)} bytes")

    # --- 3. Grabaciones ---

    if con_grabaciones and os.path.isdir(GRABACIONES):

        print()
        print("Grabaciones...")

        copia = os.path.join(paquete, "server_recordings")

        shutil.copytree(GRABACIONES, copia)

        total = 0
        cuantos = 0
        huellas = {}

        for raiz, _, archivos in os.walk(copia):

            for nombre in archivos:

                ruta = os.path.join(raiz, nombre)
                relativa = os.path.relpath(ruta, copia).replace(
                    "\\", "/"
                )

                total += os.path.getsize(ruta)
                cuantos += 1
                huellas[relativa] = sha256(ruta)

        manifiesto["grabaciones"] = {
            "archivos": cuantos,
            "bytes": total,
            "sha256": huellas,
        }

        print(f"  {cuantos} archivos, {total / 1024 ** 2:.1f} MB")

    elif not con_grabaciones:
        print()
        print("Grabaciones: omitidas (--sin-grabaciones)")
        manifiesto["grabaciones"] = None

    # --- 4. Manifiesto ---

    ruta_manifiesto = os.path.join(paquete, "manifiesto.json")

    with io.open(ruta_manifiesto, "w", encoding="utf-8") as archivo:
        json.dump(manifiesto, archivo, indent=2, ensure_ascii=False)

    print()
    print("Hecho. Ahora comprueba la copia:")
    print(f"  python scripts/respaldo.py probar \"{paquete}\"")

    return 0


# ==============================
# VERIFICAR
# ==============================

def verificar(paquete):
    """
    Comprueba el paquete contra su manifiesto.

    Esto dice si el paquete esta INTACTO. Que ademas sirva para
    restaurar lo dice 'probar'.
    """

    ruta_manifiesto = os.path.join(paquete, "manifiesto.json")

    if not os.path.exists(ruta_manifiesto):
        print(f"ERROR: no hay manifiesto en {paquete}")
        return 1

    with io.open(ruta_manifiesto, encoding="utf-8") as archivo:
        manifiesto = json.load(archivo)

    fallos = []

    # --- La base ---

    copia_base = os.path.join(paquete, "remoteadmin.db")

    if not os.path.exists(copia_base):
        print("ERROR: falta la base en el paquete")
        return 1

    print("Base de datos:")

    huella = sha256(copia_base)

    if huella != manifiesto["base"]["sha256"]:
        fallos.append("la huella de la base no coincide")
        print(f"  sha256: NO COINCIDE")
    else:
        print(f"  sha256: coincide")

    estado = integridad(copia_base)

    print(f"  integrity_check: {estado['integrity_check']}")

    if estado["integrity_check"] != "ok":
        fallos.append("integrity_check no da 'ok'")

    print(f"  referencias rotas: "
          f"{estado['foreign_key_violations']}")

    if estado["foreign_key_violations"]:
        fallos.append("hay referencias rotas")

    actuales = recuentos(copia_base)
    guardados = manifiesto["base"]["recuentos"]

    for tabla, esperadas in guardados.items():

        tiene = actuales.get(tabla)

        marca = "ok" if tiene == esperadas else "NO COINCIDE"

        print(f"    {tabla:24} {tiene} / {esperadas}  {marca}")

        if tiene != esperadas:
            fallos.append(f"{tabla}: {tiene} en vez de {esperadas}")

    # --- Los demas archivos ---

    for nombre, datos in manifiesto.get("archivos", {}).items():

        ruta = os.path.join(paquete, nombre)

        if not os.path.exists(ruta):
            fallos.append(f"falta {nombre}")
            continue

        if sha256(ruta) != datos["sha256"]:
            fallos.append(f"{nombre}: la huella no coincide")

    # --- Grabaciones ---

    grabaciones = manifiesto.get("grabaciones")

    if grabaciones:

        print()
        print("Grabaciones:")

        carpeta = os.path.join(paquete, "server_recordings")

        malas = 0

        for relativa, esperada in grabaciones["sha256"].items():

            ruta = os.path.join(carpeta, relativa.replace("/", os.sep))

            if not os.path.exists(ruta) or sha256(ruta) != esperada:
                malas += 1

        print(f"  {grabaciones['archivos']} archivos, "
              f"{malas} con problemas")

        if malas:
            fallos.append(f"{malas} grabacion(es) no coinciden")

    print()

    if fallos:
        print("RESULTADO: el paquete NO es fiable")
        for fallo in fallos:
            print(f"  - {fallo}")
        return 1

    print("RESULTADO: el paquete esta intacto")
    print()
    print("Intacto no es lo mismo que restaurable. Para eso:")
    print(f"  python scripts/respaldo.py probar \"{paquete}\"")

    return 0


# ==============================
# PROBAR
# ==============================

def probar(paquete):
    """
    Restaura el paquete en una carpeta temporal y comprueba que el
    resultado sirve.

    Es la unica forma de saber que un respaldo vale: usarlo. No se
    toca nada real; la carpeta temporal se borra al terminar.
    """

    if verificar(paquete) != 0:
        print()
        print("No se prueba un paquete que ya falla la verificacion.")
        return 1

    print()
    print("=" * 60)
    print("PRUEBA DE RESTAURACION, en una carpeta aislada")
    print("=" * 60)

    temporal = tempfile.mkdtemp(prefix="veyra-prueba-")

    try:
        destino = os.path.join(temporal, "restaurado")

        restaurar(paquete, destino, confirmado=True, silencioso=True)

        copia_base = os.path.join(destino, "remoteadmin.db")

        print(f"Restaurado en: {destino}")
        print()

        # 1. Abre y pasa integridad
        estado = integridad(copia_base)

        print(f"  integrity_check: {estado['integrity_check']}")

        if estado["integrity_check"] != "ok":
            print("  FALLO: la base restaurada no pasa la comprobacion")
            return 1

        # 2. Los datos estan
        actuales = recuentos(copia_base)

        print("  recuentos:")

        for tabla, cuantas in actuales.items():
            print(f"    {tabla:24} {cuantas}")

        # 3. Se puede CONSULTAR de verdad, no solo abrir
        conexion = sqlite3.connect(
            f"file:{copia_base}?mode=ro", uri=True
        )

        try:
            conexion.row_factory = sqlite3.Row

            equipos = [
                dict(f) for f in conexion.execute(
                    "SELECT device_id, hostname FROM devices"
                )
            ]

            grabaciones = conexion.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) "
                "FROM recordings"
            ).fetchone()

        finally:
            conexion.close()

        print()
        print(f"  equipos legibles: {len(equipos)}")
        print(f"  grabaciones: {grabaciones[0]}, "
              f"{(grabaciones[1] or 0) / 1024 ** 2:.1f} MB")

        print()
        print("RESULTADO: el respaldo SIRVE para restaurar")

        return 0

    finally:
        shutil.rmtree(temporal, ignore_errors=True)


# ==============================
# RESTAURAR
# ==============================

def restaurar(paquete, destino, confirmado=False, silencioso=False):
    """
    Deja el contenido del paquete en una carpeta.

    NUNCA escribe sobre una instalacion existente: si la carpeta
    destino ya tiene una base, se niega. Mover lo restaurado a su
    sitio es un paso manual, deliberado, con el servicio parado.
    """

    copia_base = os.path.join(paquete, "remoteadmin.db")

    if not os.path.exists(copia_base):
        print(f"ERROR: no hay base en {paquete}")
        return 1

    if os.path.exists(os.path.join(destino, "remoteadmin.db")):
        print(f"ERROR: ya hay una base en {destino}. "
              "No se sobrescribe nada.")
        return 1

    os.makedirs(destino, exist_ok=True)

    shutil.copy2(copia_base, os.path.join(destino, "remoteadmin.db"))

    pendiente = os.path.join(paquete, "auditoria_pendiente.jsonl")

    if os.path.exists(pendiente):
        shutil.copy2(
            pendiente,
            os.path.join(destino, "auditoria_pendiente.jsonl")
        )

    grabaciones = os.path.join(paquete, "server_recordings")

    if os.path.isdir(grabaciones):
        shutil.copytree(
            grabaciones,
            os.path.join(destino, "server_recordings"),
            dirs_exist_ok=False
        )

    if not silencioso:

        print(f"Restaurado en: {destino}")
        print()
        print("Queda por reponer a mano, porque no viaja en el")
        print("paquete para no llevar secretos:")

        for cosa in FUERA_DEL_PAQUETE:
            print(f"  - {cosa}")

    return 0


# ==============================

def main():

    analizador = argparse.ArgumentParser(
        description="Respaldo y restauracion de VEYRA"
    )

    analizador.add_argument(
        "orden",
        choices=("crear", "verificar", "probar", "restaurar")
    )

    analizador.add_argument("paquete", nargs="?")
    analizador.add_argument("--destino")
    analizador.add_argument(
        "--sin-grabaciones", action="store_true",
        help="omite server_recordings (mucho mas rapido y pequeño)"
    )

    argumentos = analizador.parse_args()

    if argumentos.orden == "crear":
        return crear(
            argumentos.destino,
            con_grabaciones=not argumentos.sin_grabaciones
        )

    if not argumentos.paquete:
        print(f"ERROR: '{argumentos.orden}' necesita un paquete")
        return 2

    if argumentos.orden == "verificar":
        return verificar(argumentos.paquete)

    if argumentos.orden == "probar":
        return probar(argumentos.paquete)

    if not argumentos.destino:
        print("ERROR: 'restaurar' necesita --destino")
        return 2

    return restaurar(argumentos.paquete, argumentos.destino)


if __name__ == "__main__":
    sys.exit(main())
