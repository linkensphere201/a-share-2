param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ErrorActionPreference = "Stop"
if ($BackendOnly -and $FrontendOnly) {
    throw "BackendOnly and FrontendOnly cannot be used together"
}

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$testRoot = Join-Path $root ".tmp\test"
$pytestCache = Join-Path $testRoot "pytest-cache"
$pytestTemp = Join-Path $testRoot "pytest-tmp"
$pythonCache = Join-Path $testRoot "pycache"
$processTemp = Join-Path $testRoot "temp"
$npmCache = Join-Path $testRoot "npm-cache"

New-Item -ItemType Directory -Force -Path $testRoot, $pytestCache, $pythonCache, $processTemp, $npmCache | Out-Null

$env:PYTHONPYCACHEPREFIX = $pythonCache
$env:TEMP = $processTemp
$env:TMP = $processTemp
$env:npm_config_cache = $npmCache

if (-not $FrontendOnly) {
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python environment not found: $python"
    }
    Push-Location $root
    try {
        & $python -m pytest `
            --basetemp $pytestTemp `
            -o "cache_dir=$pytestCache"
        if ($LASTEXITCODE -ne 0) {
            throw "Backend tests failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

if (-not $BackendOnly) {
    Push-Location (Join-Path $root "web")
    try {
        npm.cmd test
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend tests failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host "Test workspace: $testRoot"
