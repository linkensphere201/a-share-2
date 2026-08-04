param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$buildRoot = Join-Path $root "build\desktop"
$distRoot = Join-Path $root "dist"
$packageRoot = Join-Path $distRoot "StockHarness"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

Push-Location (Join-Path $root "web")
try {
    if (-not $SkipInstall) {
        npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed with exit code $LASTEXITCODE"
        }
    }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

if (-not $SkipInstall) {
    & $python -m pip install -e "${root}[desktop]"
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop dependency installation failed with exit code $LASTEXITCODE"
    }
}

Push-Location $root
try {
    & $python -m PyInstaller `
        --noconfirm `
        --workpath $buildRoot `
        --distpath $distRoot `
        packaging\stock-harness.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $configTarget = Join-Path $packageRoot "config"
    New-Item -ItemType Directory -Force -Path $configTarget | Out-Null
    Copy-Item -Force -LiteralPath "config\providers.example.yaml" -Destination (Join-Path $configTarget "providers.local.yaml")
    Copy-Item -Force -LiteralPath "config\storage.example.yaml" -Destination (Join-Path $configTarget "storage.local.yaml")
}
finally {
    Pop-Location
}

Write-Host "Desktop package: $packageRoot\StockHarness.exe"
