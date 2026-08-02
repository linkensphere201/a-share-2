param(
    [int]$PollSeconds = 60,
    [int]$LimitDates = 1,
    [switch]$Once,
    [string]$ProviderConfig = "config/providers.local.yaml",
    [string]$StorageConfig = "config/storage.local.yaml"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = (Resolve-Path (Join-Path $projectRoot "..\stock-picker\.venv\Scripts\python.exe")).Path
$env:PYTHONPATH = Join-Path $projectRoot "src"

do {
    & $python -m stock_harness.cli `
        --provider-config $ProviderConfig `
        --storage-config $StorageConfig `
        repair-gaps `
        --limit-dates $LimitDates
    if ($LASTEXITCODE -ne 0) {
        throw "gap repair command failed with exit code $LASTEXITCODE"
    }
    if (-not $Once) {
        Start-Sleep -Seconds $PollSeconds
    }
} while (-not $Once)
