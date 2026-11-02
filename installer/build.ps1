# Construye AcademicManager-Setup-<versión>.exe en Windows (PowerShell 5.1 o 7). Ver docs/39.
# Descarga solo lo fijado en installer/pins.json y comprueba su SHA-256 antes de usarlo.
#   .\installer\build.ps1                      usa el Inno Setup 6 ya instalado en el PC
#   .\installer\build.ps1 -InstallInnoSetup    instala el Inno Setup fijado en la carpeta de construcción
[CmdletBinding()]
param(
    [string]$BuildDir = (Join-Path ([IO.Path]::GetTempPath()) "AcademicManager-build"),
    [string]$Iscc = "",
    [switch]$InstallInnoSetup
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$repo = Split-Path -Parent $PSScriptRoot
$pins = Get-Content -LiteralPath (Join-Path $PSScriptRoot "pins.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$version = (Get-Content -LiteralPath (Join-Path $repo "academic_app\VERSION") -Raw).Trim()

function Get-Pinned($pin, [string]$folder) {
    if ($pin.sha256 -notmatch '^[0-9a-f]{64}$') {
        throw "Falta el SHA-256 verificado de $($pin.url): la construcción se detiene."
    }
    $file = Join-Path $folder ([IO.Path]::GetFileName(([Uri]$pin.url).AbsolutePath))
    if (-not (Test-Path -LiteralPath $file)) {
        Invoke-WebRequest -Uri $pin.url -OutFile $file -UseBasicParsing
    }
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $pin.sha256) {
        Remove-Item -LiteralPath $file -Force
        throw "SHA-256 distinto para $($pin.url): esperado $($pin.sha256), obtenido $actual."
    }
    return $file
}

function Invoke-Checked([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Falló ($LASTEXITCODE): $exe $($arguments -join ' ')" }
}

$downloads = Join-Path $BuildDir "downloads"
$stage = Join-Path $BuildDir "stage"
$out = Join-Path $BuildDir "out"
New-Item -ItemType Directory -Force -Path $downloads | Out-Null
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
$pythonDir = Join-Path $stage "python"
New-Item -ItemType Directory -Force -Path $pythonDir | Out-Null

# 1. Python portátil (embeddable) con site-packages y ..\app en su ._pth.
$zip = Get-Pinned $pins.python $downloads
Expand-Archive -LiteralPath $zip -DestinationPath $pythonDir
$py = Join-Path $pythonDir "python.exe"
$stagePy = Join-Path $PSScriptRoot "stage.py"
Invoke-Checked $py @($stagePy, "pth", $pythonDir)

# 2. pip desde su wheel fijado (en lugar de get-pip.py, que no tiene versión) y las dependencias fijadas.
$pipWheel = Get-Pinned $pins.pip $downloads
# Se lanza con -c y no como "$pipWheel\pip": en Windows pip se niega a modificarse si el script se llama pip.
$pipFromWheel = "import sys;sys.path.insert(0,sys.argv.pop(1));from pip._internal.cli.main import main;sys.exit(main(sys.argv[1:]))"
Invoke-Checked $py @("-c", $pipFromWheel, $pipWheel, "install", "--no-index", "--no-warn-script-location", $pipWheel)
Invoke-Checked $py @("-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
    "--prefer-binary", "-r", (Join-Path $repo "requirements-app.txt"))

# 3. Código de Academic Manager (sin pruebas, odd, docs ni .git) y comprobación de que arranca.
Invoke-Checked $py @($stagePy, "app", $repo, (Join-Path $stage "app"))
Invoke-Checked $py @("-c", "import academic_app, academic_mcp, academic_mcp.server, academic_setup, canvas_sync, capture, googleapiclient, google_auth_oauthlib, mcp, pypdf, yt_dlp")
# Sin tzdata el Python portátil no tiene zonas horarias y fallan today y canvas_sync al importarse.
Invoke-Checked $py @("-c", "from zoneinfo import ZoneInfo; ZoneInfo('America/Bogota')")
Invoke-Checked $py @("-m", "academic_app", "--help")

# 4. Inno Setup fijado (opcional) y compilación.
if ($InstallInnoSetup) {
    $innoInstaller = Get-Pinned $pins.innosetup $downloads
    $innoDir = Join-Path $BuildDir "innosetup"
    $process = Start-Process -FilePath $innoInstaller -Wait -PassThru -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CURRENTUSER", "/DIR=`"$innoDir`"")
    if ($process.ExitCode -ne 0) { throw "La instalación de Inno Setup falló ($($process.ExitCode))." }
    $Iscc = Join-Path $innoDir "ISCC.exe"
}
if (-not $Iscc) {
    $Iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Iscc -or -not (Test-Path -LiteralPath $Iscc)) {
    throw "No se encontró ISCC.exe de Inno Setup 6.3 o posterior. Use -InstallInnoSetup o -Iscc <ruta>."
}
# La versión mínima (6.3) la comprueba el propio .iss con Ver: ISCC.exe declara FileVersion 0.0.0.0.
Invoke-Checked $Iscc @("/Q", "/DStageDir=$stage", "/O$out", (Join-Path $PSScriptRoot "academic-manager.iss"))
$setup = Join-Path $out "AcademicManager-Setup-$version.exe"
if (-not (Test-Path -LiteralPath $setup)) { throw "Inno Setup no generó $setup." }
Write-Output $setup
