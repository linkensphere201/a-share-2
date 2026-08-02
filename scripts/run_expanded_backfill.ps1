param(
    [string]$EndDate = (Get-Date -Format "yyyy-MM-dd"),
    [int]$Years = 30,
    [switch]$SkipMembers
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "..\stock-picker\.venv\Scripts\python.exe"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
Set-Location $ProjectRoot

function Invoke-StockHarness {
    param([string[]]$Arguments)
    & $Python -m stock_harness.cli @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "StockHarness command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

Write-Output "stage=catalog status=started end_date=$EndDate"
Invoke-StockHarness @("sync-catalog", "--scope", "all", "--observed-on", $EndDate)

Write-Output "stage=history status=started end_date=$EndDate years=$Years"
Invoke-StockHarness @(
    "backfill-expanded", "--scope", "all", "--years", "$Years", "--end-date", $EndDate
)

if (-not $SkipMembers) {
    Write-Output "stage=dc_members status=started observed_on=$EndDate"
    Invoke-StockHarness @("sync-board-members", "--source", "dc", "--observed-on", $EndDate)
    Write-Output "stage=ths_members status=started observed_on=$EndDate"
    Invoke-StockHarness @("sync-board-members", "--source", "ths", "--observed-on", $EndDate)
}

Write-Output "stage=report status=started"
Invoke-StockHarness @("expanded-report")
Write-Output "expanded_backfill status=completed end_date=$EndDate years=$Years"
