param(
    [int]$Years = 30,
    [int]$BatchDates = 25,
    [int]$ProgressEvery = 10,
    [switch]$Once,
    [string]$ProviderConfig = "config/providers.local.yaml",
    [string]$StorageConfig = "config/storage.local.yaml"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = (Resolve-Path (Join-Path $projectRoot "..\stock-picker\.venv\Scripts\python.exe")).Path
$env:PYTHONPATH = Join-Path $projectRoot "src"

if ($BatchDates -le 0) {
    throw "BatchDates must be positive"
}

$batchNumber = 0
do {
    $batchNumber += 1
    $batchOutput = @(& $python -m stock_harness.cli `
        --provider-config $ProviderConfig `
        --storage-config $StorageConfig `
        backfill-stocks `
        --years $Years `
        --max-dates $BatchDates `
        --progress-every $ProgressEvery)
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    $batchOutput | Write-Output
    $summary = $batchOutput | Where-Object { $_ -like "backfill_summary *" } | Select-Object -Last 1
    if (-not $summary) {
        throw "backfill command did not emit a summary"
    }
    $match = [regex]::Match($summary, "completed_dates=(\d+).*empty_dates=(\d+)")
    if (-not $match.Success) {
        throw "unable to parse backfill summary: $summary"
    }
    $processedDates = [int]$match.Groups[1].Value + [int]$match.Groups[2].Value
    Write-Output "backfill_batch batch=$batchNumber processed_dates=$processedDates batch_limit=$BatchDates"
} while (-not $Once -and $processedDates -ge $BatchDates)
