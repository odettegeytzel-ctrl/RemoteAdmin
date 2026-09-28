# ==============================================================
# RemoteAdmin - Confianza del certificado TLS de DESARROLLO
# ==============================================================
#
# Instala certs\dev-ca-cert.pem en el almacén de raíces de confianza del
# USUARIO ACTUAL (Cert:\CurrentUser\Root), para que el navegador acepte
# https://127.0.0.1:8000 sin advertencias durante el desarrollo local.
#
# No requiere administrador: el alcance es solo tu usuario en esta máquina.
# Solo para desarrollo local. Nunca instales así un certificado de producción.
#
# Uso (PowerShell normal, desde la raíz del proyecto):
#   .\certs\trust-dev-cert.ps1              # instalar
#   .\certs\trust-dev-cert.ps1 -Uninstall   # retirar
#   .\certs\trust-dev-cert.ps1 -Force       # instalar sin preguntar
#
# Nota: esto afecta al navegador, no al Agent. Python (requests/websockets)
# no usa el almacén de Windows, sino su propio paquete de autoridades.

param(
    [switch]$Uninstall,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$CertPath = Join-Path $PSScriptRoot "dev-ca-cert.pem"
$StorePath = "Cert:\CurrentUser\Root"

# Marcas que identifican el certificado de desarrollo de RemoteAdmin
$ExpectedSubjectPart = "O=RemoteAdmin Desarrollo"
$ExpectedCommonName = "CN=RemoteAdmin Dev CA"


# --------------------------------------------------------------
# Carga del certificado desde el archivo .pem
# --------------------------------------------------------------

if (-not (Test-Path $CertPath)) {
    throw "No se encontró $CertPath. Genéralo con: bash certs/generate-dev-cert.sh"
}

$Cert = Get-PfxCertificate -FilePath $CertPath

Write-Host ""
Write-Host "Certificado leído de: $CertPath"
Write-Host "  Asunto     : $($Cert.Subject)"
Write-Host "  Emisor     : $($Cert.Issuer)"
Write-Host "  Huella     : $($Cert.Thumbprint)"
Write-Host "  Válido desde: $($Cert.NotBefore)"
Write-Host "  Válido hasta: $($Cert.NotAfter)"
Write-Host ""


# --------------------------------------------------------------
# Modo -Uninstall: retira el certificado por su huella digital
# --------------------------------------------------------------

if ($Uninstall) {

    $Installed = Get-ChildItem $StorePath | Where-Object { $_.Thumbprint -eq $Cert.Thumbprint }

    if (-not $Installed) {
        Write-Host "El certificado no está en $StorePath. No hay nada que retirar."
        return
    }

    # Se borra por huella digital: nunca por nombre, para no tocar otro certificado
    $Installed | Remove-Item

    Write-Host "Certificado retirado de $StorePath (huella $($Cert.Thumbprint))."
    Write-Host "Cierra y reabre el navegador para que deje de confiar en él."
    return
}


# --------------------------------------------------------------
# Verificaciones de seguridad antes de instalar
# --------------------------------------------------------------

# 1. Debe ser la CA de desarrollo de RemoteAdmin
if ($Cert.Subject -notlike "*$ExpectedSubjectPart*" -or $Cert.Subject -notlike "*$ExpectedCommonName*") {
    throw ("Este certificado no parece el de desarrollo de RemoteAdmin " +
           "($ExpectedCommonName, $ExpectedSubjectPart). Asunto encontrado: $($Cert.Subject). " +
           "Por seguridad no se instala.")
}

# 2. Debe ser autofirmado: un certificado emitido por un tercero no debe
#    instalarse jamás como raíz de confianza.
if ($Cert.Subject -ne $Cert.Issuer) {
    throw ("El certificado no es autofirmado (emisor: $($Cert.Issuer)). " +
           "Instalar como raíz de confianza un certificado ajeno es peligroso. No se instala.")
}

# 3. Debe ser una CA (Basic Constraints CA=true). Un certificado de entidad
#    final (el del servidor) no puede actuar como raíz: Windows lo rechazaría
#    con CERT_E_UNTRUSTEDROOT aunque estuviera en el almacén.
$BasicConstraints = $Cert.Extensions | Where-Object {
    $_ -is [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]
}

if (-not $BasicConstraints -or -not $BasicConstraints.CertificateAuthority) {
    throw ("Este certificado NO es una CA (Basic Constraints CA=true). " +
           "Instala certs\dev-ca-cert.pem, no el certificado del servidor. " +
           "Si falta, genéralo con: bash certs/generate-dev-cert.sh")
}

# 4. Debe estar vigente
$Now = Get-Date

if ($Now -lt $Cert.NotBefore -or $Now -gt $Cert.NotAfter) {
    throw ("El certificado no está vigente (válido de $($Cert.NotBefore) a $($Cert.NotAfter)). " +
           "Regenéralo con: bash certs/generate-dev-cert.sh")
}


# --------------------------------------------------------------
# Si ya está instalado, no se duplica
# --------------------------------------------------------------

$Existing = Get-ChildItem $StorePath | Where-Object { $_.Thumbprint -eq $Cert.Thumbprint }

if ($Existing) {
    Write-Host "El certificado YA está instalado en $StorePath. No se hace nada."
    Write-Host "Para retirarlo:  .\certs\trust-dev-cert.ps1 -Uninstall"
    return
}


# --------------------------------------------------------------
# Confirmación e instalación
# --------------------------------------------------------------

Write-Host "Se instalará en $StorePath (solo el usuario actual, sin admin)."
Write-Host "Windows confiará en este certificado para el desarrollo local."
Write-Host ""

if (-not $Force) {

    $Answer = Read-Host "¿Continuar? (s/N)"

    if ($Answer -ne "s" -and $Answer -ne "S") {
        Write-Host "Cancelado. No se instaló nada."
        return
    }
}

Import-Certificate -FilePath $CertPath -CertStoreLocation $StorePath | Out-Null

Write-Host ""
Write-Host "Instalado. Huella: $($Cert.Thumbprint)"
Write-Host "Cierra y reabre el navegador y entra en https://127.0.0.1:8000"
Write-Host "Para revertirlo:  .\certs\trust-dev-cert.ps1 -Uninstall"
