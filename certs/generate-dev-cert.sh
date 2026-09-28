#!/usr/bin/env bash
#
# Regenera la infraestructura de certificados de DESARROLLO de RemoteAdmin.
#
# Crea dos niveles, como exige una cadena de confianza válida:
#
#   1. Una CA local de desarrollo (Basic Constraints CA=true).
#      Es la que se instala en el almacén de raíces de confianza de Windows.
#         dev-ca-cert.pem / dev-ca-key.pem
#
#   2. Un certificado de servidor para localhost, 127.0.0.1 y ::1,
#      firmado por esa CA. Es el que usa Uvicorn.
#         dev-server-cert.pem / dev-server-key.pem
#
# La clave de la CA (dev-ca-key.pem) es el elemento más sensible: quien la
# tenga puede emitir certificados en los que tu equipo confiará. Nunca sale
# de esta máquina y está ignorada por Git.
#
# Solo para desarrollo local. Nunca en producción.
#
# Uso (Git Bash, desde la raíz del proyecto):
#   bash certs/generate-dev-cert.sh
#
# Requiere openssl (incluido con Git para Windows).

set -e

cd "$(dirname "$0")"

export MSYS_NO_PATHCONV=1

CA_DAYS=1825      # 5 años: la CA dura más que los certificados que emite
SERVER_DAYS=825   # máximo que los navegadores aceptan para un certificado de servidor


# --------------------------------------------------------------
# 1. CA local de desarrollo
# --------------------------------------------------------------

echo "[1/2] Generando la CA local de desarrollo..."

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout dev-ca-key.pem -out dev-ca-cert.pem \
  -days "$CA_DAYS" -sha256 \
  -subj "/CN=RemoteAdmin Dev CA/O=RemoteAdmin Desarrollo" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"


# --------------------------------------------------------------
# 2. Certificado de servidor, firmado por la CA
# --------------------------------------------------------------

echo "[2/2] Generando el certificado de servidor firmado por la CA..."

openssl req -newkey rsa:2048 -nodes \
  -keyout dev-server-key.pem -out dev-server.csr \
  -subj "/CN=localhost/O=RemoteAdmin Desarrollo"

cat > dev-server-ext.cnf <<'EOF'
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost,IP:127.0.0.1,IP:::1
EOF

openssl x509 -req \
  -in dev-server.csr \
  -CA dev-ca-cert.pem -CAkey dev-ca-key.pem -CAcreateserial \
  -out dev-server-cert.pem \
  -days "$SERVER_DAYS" -sha256 \
  -extfile dev-server-ext.cnf

# Archivos intermedios que ya no hacen falta
rm -f dev-server.csr dev-server-ext.cnf dev-ca-cert.srl


# --------------------------------------------------------------
# Comprobación de la cadena
# --------------------------------------------------------------

echo
echo "Verificando la cadena..."
openssl verify -CAfile dev-ca-cert.pem dev-server-cert.pem

echo
echo "Generados:"
echo "  dev-ca-cert.pem      <- instalar en Windows (certs/trust-dev-cert.ps1)"
echo "  dev-ca-key.pem       <- clave de la CA: NO compartir"
echo "  dev-server-cert.pem  <- lo usa Uvicorn (run_server.py)"
echo "  dev-server-key.pem   <- clave del servidor"
