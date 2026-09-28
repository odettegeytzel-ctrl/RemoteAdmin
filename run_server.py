"""
Arranque del backend de RemoteAdmin con TLS (HTTPS/WSS).

Uso, desde la raíz del proyecto:

    python run_server.py

Sirve la API, el frontend y el WebSocket del Agent en https://127.0.0.1:8000
usando el certificado de desarrollo de certs/ (ver certs/generate-dev-cert.sh).

Las rutas estáticas de backend/main.py son relativas a la raíz del proyecto,
por eso este script fija ahí el directorio de trabajo antes de arrancar.
"""

import os
import sys

import uvicorn


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Certificado de servidor emitido por la CA local de desarrollo
# (certs/dev-ca-cert.pem), no autofirmado. Ver certs/generate-dev-cert.sh.
CERT_FILE = os.path.join(BASE_DIR, "certs", "dev-server-cert.pem")
KEY_FILE = os.path.join(BASE_DIR, "certs", "dev-server-key.pem")

# Interfaz en la que escucha el servidor. Por defecto solo loopback: el
# backend no es accesible desde la red salvo que se pida explícitamente.
#
# Para exponerlo temporalmente en la red local (por ejemplo, para probar desde
# otra máquina) se arranca así:
#
#     $env:REMOTEADMIN_BIND_HOST = "0.0.0.0"; python run_server.py
#
# 0.0.0.0 escucha en TODAS las interfaces, loopback incluido, así que el Agent
# local sigue conectando por 127.0.0.1. Enlazar solo a la IP de red rompería
# ese acceso.
#
# Al exponerlo, el certificado debe incluir esa IP en su SAN y conviene
# limitar el acceso con una regla de firewall.
HOST = os.getenv("REMOTEADMIN_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = 8000


def main():

    os.chdir(BASE_DIR)

    missing = [
        path for path in (CERT_FILE, KEY_FILE)
        if not os.path.isfile(path)
    ]

    if missing:
        print(
            "ERROR: falta(n) el certificado o la clave TLS:\n  "
            + "\n  ".join(missing)
            + "\n\nGenéralos con:  bash certs/generate-dev-cert.sh"
        )
        sys.exit(1)

    print(f"[TLS] Arrancando en https://{HOST}:{PORT}")

    uvicorn.run(
        "backend.main:app",
        host=HOST,
        port=PORT,
        ssl_certfile=CERT_FILE,
        ssl_keyfile=KEY_FILE
    )


if __name__ == "__main__":
    main()
