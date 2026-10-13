# ==============================================================
# VEYRA - Construye el instalador grafico (.exe)
# ==============================================================
#
# Produce installer\dist\VEYRA-Instalador.exe, un unico archivo que se
# copia junto al paquete del Agent y se ejecuta con doble clic.
#
# Uso, desde la raiz del proyecto:
#
#   .\installer\build-installer.ps1
#
# Requiere PyInstaller en el entorno de desarrollo:
#
#   .venv\Scripts\python -m pip install -r requirements-dev.txt
#
# El .exe NO lleva ninguna credencial dentro: solo la ventana. La
# credencial de alta se teclea al instalar.
#
# Tampoco lleva el Agent: el .exe acompana al paquete descargado del
# panel y ejecuta installer\install-agent.ps1 de esa misma carpeta. Asi
# el Agent que se instala es siempre el del paquete firmado, y no una
# copia congelada dentro del ejecutable que envejeceria por su cuenta.

[CmdletBinding()]
param(
    [switch]$Limpiar
)

$ErrorActionPreference = "Stop"

$Raiz = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Raiz ".venv\Scripts\python.exe"
$Guion = Join-Path $Raiz "installer\veyra_installer.py"
$Salida = Join-Path $Raiz "installer\dist"
$Trabajo = Join-Path $Raiz "installer\build"

Write-Host ""
Write-Host "VEYRA - construccion del instalador"
Write-Host "-----------------------------------"

if (-not (Test-Path $Python)) {
    throw "No se encontro el entorno virtual en $Python"
}

& $Python -c "import PyInstaller" 2>$null

if ($LASTEXITCODE -ne 0) {
    throw "Falta PyInstaller. Instalalo con: .venv\Scripts\python -m pip install -r requirements-dev.txt"
}

if ($Limpiar) {
    foreach ($d in @($Salida, $Trabajo)) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d }
    }
    Write-Host "Limpiado."
}

Write-Host "Empaquetando..."

# --onefile      un solo .exe, para que no haya que copiar carpetas
# --noconsole    sin ventana negra detras de la interfaz
# --uac-admin    NO: la elevacion se pide al instalar, con el dialogo
#                del sistema. Marcar el .exe como "requiere
#                administrador" obligaria a elevar solo para abrir la
#                ventana y comprobar la conexion, que no lo necesitan.
& $Python -m PyInstaller `
    --onefile `
    --noconsole `
    --name "VEYRA-Instalador" `
    --distpath $Salida `
    --workpath $Trabajo `
    --specpath $Trabajo `
    --noconfirm `
    $Guion

if ($LASTEXITCODE -ne 0) {
    throw "Fallo el empaquetado."
}

$Exe = Join-Path $Salida "VEYRA-Instalador.exe"

if (-not (Test-Path $Exe)) {
    throw "El empaquetado termino pero no se encontro $Exe"
}

$Tamano = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
$Huella = (Get-FileHash $Exe -Algorithm SHA256).Hash
$HuellaFuente = (Get-FileHash $Guion -Algorithm SHA256).Hash

# Se deja constancia de QUE fuente produjo QUE ejecutable. Un .exe no
# permite comprobar por dentro de que codigo salio, asi que sin este
# registro no habria forma de saber si corresponde al fuente actual.
$Constancia = Join-Path $Salida "VEYRA-Instalador.sha256.txt"

@(
    "VEYRA-Instalador.exe",
    "  construido   : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "  exe SHA-256  : $Huella",
    "  fuente       : installer/veyra_installer.py",
    "  fuente SHA256: $HuellaFuente"
) | Set-Content -Encoding UTF8 -Path $Constancia

Write-Host ""
Write-Host "Listo."
Write-Host "  archivo        : $Exe"
Write-Host "  tamano         : $Tamano MB"
Write-Host "  SHA-256        : $Huella"
Write-Host "  fuente SHA-256 : $HuellaFuente"
Write-Host "  constancia     : $Constancia"
Write-Host ""
Write-Host "Copialo dentro de la carpeta del paquete descargado del"
Write-Host "panel, junto a installer\ y agent\, y comprimelo todo."
Write-Host ""
