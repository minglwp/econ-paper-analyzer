[CmdletBinding()]
param(
    [string]$Python = $env:EPA_BUILD_PYTHON
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if ([string]::IsNullOrWhiteSpace($Python)) {
    $ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $ProjectPython) {
        $Python = $ProjectPython
    }
    else {
        $Python = (Get-Command python -CommandType Application -ErrorAction Stop).Path
    }
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Build Python not found: $Python"
}

$Version = & $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the project version."
}
$PythonArchitecture = (& $Python -c "import platform; print(platform.machine())").ToLowerInvariant()
if ($PythonArchitecture -notmatch "amd64|x86_64") {
    throw "Windows packaging requires an x64 Python interpreter; found $PythonArchitecture."
}

$BuildRoot = Join-Path $ProjectRoot ".build\windows-x64"
$ReleaseParent = Join-Path $ProjectRoot "dist\releases"
$ReleaseRoot = Join-Path $ReleaseParent "v$Version"
if (Test-Path -LiteralPath $ReleaseRoot) {
    throw "Release directory already exists: $ReleaseRoot"
}

$PackageRoot = Join-Path ([System.IO.Path]::GetTempPath()) "econ-paper-analyzer-package-$Version-windows-x64-$([guid]::NewGuid().ToString('N'))"
$ReleaseStage = Join-Path $ReleaseParent ".v$Version-windows-x64-$([guid]::NewGuid().ToString('N'))"
$PackageDist = Join-Path $PackageRoot "windows-x64"
$IconPath = Join-Path $BuildRoot "EconPaperAnalyzer.ico"
$IconsetPath = Join-Path $BuildRoot "AppIcon.iconset"
$PngPath = Join-Path $ProjectRoot "assets\app-icon.png"
$ApplicationPath = Join-Path $PackageDist "econ-paper-analyzer"
$ZipName = "econ-paper-analyzer-windows-x64-v$Version.zip"
$ZipPath = Join-Path $ReleaseStage $ZipName
$ChecksumPath = Join-Path $ReleaseStage "SHA256SUMS.txt"

New-Item -ItemType Directory -Force -Path $BuildRoot, $ReleaseParent, $PackageDist, $ReleaseStage | Out-Null

try {
    & $Python scripts/generate_icon.py --png $PngPath --iconset $IconsetPath --ico $IconPath
    if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }

    $env:EPA_APP_VERSION = $Version
    $env:EPA_ICON_PATH = $IconPath
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildRoot "config"
    $env:MPLCONFIGDIR = Join-Path $BuildRoot "matplotlib"
    $env:XDG_CACHE_HOME = Join-Path $BuildRoot "cache"

    & $Python -m PyInstaller --clean --noconfirm --workpath (Join-Path $BuildRoot "pyinstaller") --distpath $PackageDist EconPaperAnalyzer.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

    $ExecutablePath = Join-Path $ApplicationPath "econ-paper-analyzer.exe"
    if (-not (Test-Path -LiteralPath $ExecutablePath)) {
        throw "Expected Windows executable was not created: $ExecutablePath"
    }

    Compress-Archive -Path $ApplicationPath -DestinationPath $ZipPath -CompressionLevel Optimal
    $VerificationRoot = Join-Path $PackageRoot "verify"
    Expand-Archive -Path $ZipPath -DestinationPath $VerificationRoot -Force
    $VerifiedExecutable = Join-Path $VerificationRoot "econ-paper-analyzer\econ-paper-analyzer.exe"
    if (-not (Test-Path -LiteralPath $VerifiedExecutable)) {
        throw "The release ZIP does not contain the expected executable."
    }

    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
    "$Hash  $ZipName" | Set-Content -LiteralPath $ChecksumPath -Encoding ascii
    $ExpectedHash = ((Get-Content -LiteralPath $ChecksumPath) -split '\s+' | Where-Object { $_ } | Select-Object -First 1)
    if ($ExpectedHash -ne $Hash) {
        throw "SHA-256 verification failed."
    }

    Move-Item -LiteralPath $ReleaseStage -Destination $ReleaseRoot
    $ReleaseStage = $null
    Write-Host "Release: $(Join-Path $ReleaseRoot $ZipName)"
    Write-Host "Checksums: $(Join-Path $ReleaseRoot 'SHA256SUMS.txt')"
}
finally {
    if ($null -ne $ReleaseStage -and (Test-Path -LiteralPath $ReleaseStage)) {
        Remove-Item -LiteralPath $ReleaseStage -Recurse -Force
    }
}
