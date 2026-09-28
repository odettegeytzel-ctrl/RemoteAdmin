"""
Política de reintentos del Agent ante errores de subida (bloque F3).

Ejecuta el _upload_one REAL del Agent contra respuestas simuladas, para
comprobar que:
  - un error reintentable (red, 408, 425, 429, 5xx) se vuelve a intentar;
  - un error permanente (4xx) deja de reintentarse;
  - en NINGÚN caso se borra el archivo local.

Se simula la respuesta en lugar de levantar un servidor: lo que se está
probando es la decisión del Agent, no el transporte HTTP, y así la prueba es
determinista y rápida.

Uso, desde la raíz del proyecto:

    python tests/test_f3_retry_policy.py
"""

import os
import shutil
import sys
import tempfile
import threading

import requests

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

resultados = []


def comprobar(descripcion, condicion):
    resultados.append((descripcion, bool(condicion)))
    print(f"  [{'OK  ' if condicion else 'FALLA'}] {descripcion}")


class RespuestaSimulada:
    """Lo mínimo de requests.Response que usa _upload_one."""

    def __init__(self, status_code, cuerpo=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._cuerpo = cuerpo if cuerpo is not None else {"status": "stored"}

    def json(self):
        # _upload_one lee el cuerpo para distinguir una subida aceptada de
        # una grabación rechazada por F4 (status="invalid").
        return self._cuerpo


def cargar_agente(peticiones, respuesta):
    """
    Carga las piezas de agent.py que deciden la política de reintentos, sin
    importar el módulo entero (arrastra wmi, pyautogui y mss).
    """

    with open(os.path.join(RAIZ, "agent", "agent.py"), encoding="utf-8") as h:
        codigo = h.read()

    def extraer(desde, hasta):
        return codigo[codigo.index(desde):codigo.index(hasta)]

    def post_simulado(*args, **kwargs):
        peticiones.append(kwargs.get("params", {}).get("filename"))
        if isinstance(respuesta["valor"], Exception):
            raise respuesta["valor"]
        return RespuestaSimulada(respuesta["valor"])

    requests_falso = type("R", (), {
        "post": staticmethod(post_simulado),
        "RequestException": requests.RequestException
    })

    ambito = {
        "os": os,
        "threading": threading,
        "requests": requests_falso,
        "SERVER_URL": "https://servidor-de-prueba",
        "REQUESTS_VERIFY": True,
        "get_device_id": lambda: "dispositivo-de-prueba",
        "device_auth_headers": lambda: {"X-Agent-Token": "token"},
    }

    exec(extraer("RETRYABLE_STATUS = {", "def _upload_one("), ambito)
    exec(extraer("def _upload_one(", "def _process_pending("), ambito)

    return ambito


def main():

    temporal = tempfile.mkdtemp(prefix="remoteadmin_f3_retry_")

    peticiones = []
    respuesta = {"valor": 200}

    agente = cargar_agente(peticiones, respuesta)

    subir = agente["_upload_one"]
    marcado = agente["is_permanently_failed"]
    marcar = agente["mark_permanent_failure"]

    def crear(nombre):
        ruta = os.path.join(temporal, nombre)
        with open(ruta, "wb") as h:
            h.write(b"\x00" * 4096)
        return ruta

    try:

        print("\n=== 1. Subida correcta ===")
        respuesta["valor"] = 200
        a = crear("rec_8000000001.mp4")
        comprobar("devuelve True", subir({"path": a}) is True)
        comprobar("el archivo sigue ahí (lo borra F2, no la subida)",
                  os.path.exists(a))
        comprobar("no se marca como permanente", not marcado(a))

        print("\n=== 2. Errores REINTENTABLES: se siguen intentando ===")
        for codigo in (500, 502, 503, 504, 429, 408, 425):
            respuesta["valor"] = codigo
            b = crear(f"rec_8100{codigo}.mp4")
            peticiones.clear()

            primero = subir({"path": b})
            segundo = subir({"path": b})

            comprobar(f"{codigo}: devuelve False las dos veces",
                      primero is False and segundo is False)
            comprobar(f"{codigo}: SE REINTENTA (2 envíos)", len(peticiones) == 2)
            comprobar(f"{codigo}: no se marca como permanente", not marcado(b))
            comprobar(f"{codigo}: el archivo se conserva", os.path.exists(b))

        print("\n=== 3. Errores PERMANENTES: dejan de reintentarse ===")
        for codigo in (400, 401, 403, 404, 413, 415, 422, 418, 451):
            respuesta["valor"] = codigo
            c = crear(f"rec_8200{codigo}.mp4")

            comprobar(f"{codigo}: devuelve False", subir({"path": c}) is False)
            comprobar(f"{codigo}: queda MARCADO como permanente", marcado(c))
            comprobar(f"{codigo}: el archivo local se CONSERVA", os.path.exists(c))

        print("\n=== 4. Un marcado permanente ya no se envía ===")
        respuesta["valor"] = 401
        d = crear("rec_8300000001.mp4")
        subir({"path": d})                 # primer intento: lo marca
        peticiones.clear()

        # Así lo trata el bucle de _process_pending: lo salta sin enviar
        saltado = marcado(d)
        if not saltado:
            subir({"path": d})

        comprobar("el bucle lo salta", saltado)
        comprobar("no se envía ninguna petición más", len(peticiones) == 0)
        comprobar("el archivo sigue en disco", os.path.exists(d))

        print("\n=== 5. El aviso del log sale una sola vez ===")
        respuesta["valor"] = 403
        e = crear("rec_8400000001.mp4")
        subir({"path": e})
        comprobar("el segundo marcado devuelve False (no repite el aviso)",
                  marcar(e) is False)

        print("\n=== 6. Errores de red: siempre reintentables ===")
        for excepcion, etiqueta in [
            (requests.ConnectionError("red caída"), "conexión"),
            (requests.Timeout("agotado"), "timeout"),
            (requests.TooManyRedirects("bucle"), "redirecciones")
        ]:
            respuesta["valor"] = excepcion
            f = crear(f"rec_85000{etiqueta[:4]}.mp4")

            comprobar(f"{etiqueta}: devuelve False", subir({"path": f}) is False)
            comprobar(f"{etiqueta}: NO se marca como permanente", not marcado(f))
            comprobar(f"{etiqueta}: el archivo se conserva", os.path.exists(f))

        print("\n=== 7. Archivo inexistente: se da por subido ===")
        respuesta["valor"] = 200
        comprobar("una ruta que ya no existe devuelve True",
                  subir({"path": os.path.join(temporal, "no_existe.mp4")}) is True)

        print("\n=== 8. Ningún error borra archivos ===")
        restantes = [f for f in os.listdir(temporal) if f.endswith(".mp4")]
        comprobar(f"siguen los {len(restantes)} archivos creados",
                  len(restantes) >= 20)

    finally:
        shutil.rmtree(temporal, ignore_errors=True)

    fallos = [d for d, ok in resultados if not ok]

    print("\n" + "=" * 62)
    print(f"  {len(resultados) - len(fallos)} de {len(resultados)} comprobaciones correctas")

    if fallos:
        print("  FALLOS:")
        for f in fallos:
            print("   -", f)

    print("=" * 62)

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
