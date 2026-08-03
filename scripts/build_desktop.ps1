param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

Push-Location (Join-Path $root "web")
try {
    if (-not $SkipInstall) { npm.cmd install }
    npm.cmd run build
}
finally {
    Pop-Location
}

if (-not $SkipInstall) {
    & $python -m pip install -e "${root}[desktop]"
}

Push-Location $root
try {
    & $python -m PyInstaller packaging\stock-harness.spec
    $configTarget = Join-Path $root "dist\StockHarness\config"
    New-Item -ItemType Directory -Force -Path $configTarget | Out-Null
    Copy-Item -LiteralPath "config\providers.example.yaml" -Destination (Join-Path $configTarget "providers.local.yaml")
    Copy-Item -LiteralPath "config\storage.example.yaml" -Destination (Join-Path $configTarget "storage.local.yaml")
}
finally {
    Pop-Location
}

Write-Host "Desktop package: $root\dist\StockHarness\StockHarness.exe"
