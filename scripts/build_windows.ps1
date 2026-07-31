[CmdletBinding()]
param(
    [string]$Python = $env:EPA_BUILD_PYTHON,
    [switch]$CreateInstaller
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
        $Python = Get-Command python -CommandType Application -ErrorAction Stop |
            Select-Object -First 1 -ExpandProperty Path
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
$InstallerName = "econ-paper-analyzer-windows-x64-v$Version-setup.exe"
$ChecksumName = "econ-paper-analyzer-windows-x64-v$Version.sha256"
$ZipPath = Join-Path $ReleaseStage $ZipName
$InstallerPath = Join-Path $ReleaseStage $InstallerName
$ChecksumPath = Join-Path $ReleaseStage $ChecksumName
$InstallerScript = Join-Path $ProjectRoot "scripts\EconPaperAnalyzer.iss"

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

    $ReleaseArtifacts = @($ZipPath)
    if ($CreateInstaller) {
        if (-not (Test-Path -LiteralPath $InstallerScript)) {
            throw "Inno Setup script not found: $InstallerScript"
        }
        $InnoSetupCompiler = Get-Command ISCC.exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty Path
        if ([string]::IsNullOrWhiteSpace($InnoSetupCompiler)) {
            $InnoSetupCompiler = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
        }
        if (-not (Test-Path -LiteralPath $InnoSetupCompiler)) {
            throw "Inno Setup 6 was not found. Install it or run without -CreateInstaller."
        }

        & $InnoSetupCompiler "/DAppVersion=$Version" "/DSourceDir=$ApplicationPath" "/DOutputDir=$ReleaseStage" "/DSetupIcon=$IconPath" $InstallerScript
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
        if (-not (Test-Path -LiteralPath $InstallerPath)) {
            throw "Expected Windows installer was not created: $InstallerPath"
        }
        $ReleaseArtifacts += $InstallerPath
    }

    $ChecksumLines = foreach ($Artifact in $ReleaseArtifacts) {
        $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact).Hash.ToLowerInvariant()
        "$Hash  $(Split-Path -Leaf $Artifact)"
    }
    $ChecksumLines | Set-Content -LiteralPath $ChecksumPath -Encoding ascii
    foreach ($Line in Get-Content -LiteralPath $ChecksumPath) {
        if ($Line -notmatch '^[0-9a-f]{64}\s{2}.+$') {
            throw "SHA-256 verification file is malformed."
        }
    }

    Move-Item -LiteralPath $ReleaseStage -Destination $ReleaseRoot
    $ReleaseStage = $null
    Write-Host "Release: $(Join-Path $ReleaseRoot $ZipName)"
    if ($CreateInstaller) {
        Write-Host "Installer: $(Join-Path $ReleaseRoot $InstallerName)"
    }
    Write-Host "Checksum: $(Join-Path $ReleaseRoot $ChecksumName)"
}
finally {
    if ($null -ne $ReleaseStage -and (Test-Path -LiteralPath $ReleaseStage)) {
        Remove-Item -LiteralPath $ReleaseStage -Recurse -Force
    }
}
