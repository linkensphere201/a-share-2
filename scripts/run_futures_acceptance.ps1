param(
    [switch]$RunTests,
    [switch]$BuildFrontend,
    [string]$BaseUrl,
    [string]$RealSymbol,
    [string]$ContinuousSymbol,
    [string]$Output = ".tmp\test\futures-acceptance-report.json"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$checks = [System.Collections.Generic.List[object]]::new()

function Add-Check([string]$Id, [string]$Status, [string]$Evidence) {
    $checks.Add([ordered]@{ id = $Id; status = $Status; evidence = $Evidence })
}

function Invoke-NativeCheck([string]$Id, [scriptblock]$Command) {
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) {
            Add-Check $Id "failed" "exit_code=$LASTEXITCODE"
            return
        }
        Add-Check $Id "passed" "exit_code=0"
    }
    catch {
        Add-Check $Id "failed" $_.Exception.GetType().Name
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    if ($RunTests) {
        Invoke-NativeCheck "automated-tests" { & "$root\scripts\run_tests.ps1" }
    }
    else {
        Add-Check "automated-tests" "not-run" "Use -RunTests"
    }

    if ($BuildFrontend) {
        Invoke-NativeCheck "frontend-production-build" {
            Push-Location "$root\web"
            try { npm.cmd run build } finally { Pop-Location }
        }
    }
    else {
        Add-Check "frontend-production-build" "not-run" "Use -BuildFrontend"
    }

    $integrityOutput = Join-Path $root "data\reports\futures-integrity-acceptance.json"
    Invoke-NativeCheck "store-integrity" {
        & $python -m stock_harness.cli audit-futures-store --output $integrityOutput
    }

    if ($BaseUrl) {
        try {
            $health = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/api/health" -TimeoutSec 10
            Add-Check "app-health" ($(if ($health.status -eq "ok") { "passed" } else { "failed" })) "status=$($health.status)"
        }
        catch {
            Add-Check "app-health" "failed" $_.Exception.GetType().Name
        }
        try {
            $coverage = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/api/futures/coverage?limit=1&offset=0" -TimeoutSec 10
            Add-Check "futures-discovery" ($(if ($coverage.total -gt 0) { "passed" } else { "failed" })) "total=$($coverage.total)"
        }
        catch {
            Add-Check "futures-discovery" "failed" $_.Exception.GetType().Name
        }
        foreach ($sample in @(
            @{ id = "real-contract-history"; symbol = $RealSymbol },
            @{ id = "continuous-history"; symbol = $ContinuousSymbol }
        )) {
            if (-not $sample.symbol) {
                Add-Check $sample.id "not-run" "Provide the corresponding symbol parameter"
                continue
            }
            try {
                $encoded = [Uri]::EscapeDataString($sample.symbol)
                $history = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/api/instruments/$encoded/daily-bars" -TimeoutSec 30
                $count = @($history.items).Count
                Add-Check $sample.id ($(if ($count -gt 0) { "passed" } else { "failed" })) "symbol=$($sample.symbol); rows=$count"
            }
            catch {
                Add-Check $sample.id "failed" $_.Exception.GetType().Name
            }
        }
        if ($ContinuousSymbol) {
            try {
                $encoded = [Uri]::EscapeDataString($ContinuousSymbol)
                $detail = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/api/futures/continuous/$encoded" -TimeoutSec 30
                $mapped = @($detail.mappings).Count
                Add-Check "continuous-roll-evidence" ($(if ($mapped -gt 0) { "passed" } else { "failed" })) "mappings=$mapped; rolls=$(@($detail.rolls).Count)"
            }
            catch {
                Add-Check "continuous-roll-evidence" "failed" $_.Exception.GetType().Name
            }
        }
        else {
            Add-Check "continuous-roll-evidence" "not-run" "Provide -ContinuousSymbol"
        }
    }
    else {
        Add-Check "app-health" "not-run" "Provide -BaseUrl"
        Add-Check "futures-discovery" "not-run" "Provide -BaseUrl"
        Add-Check "real-contract-history" "not-run" "Provide -BaseUrl and -RealSymbol"
        Add-Check "continuous-history" "not-run" "Provide -BaseUrl and -ContinuousSymbol"
        Add-Check "continuous-roll-evidence" "not-run" "Provide -BaseUrl and -ContinuousSymbol"
    }

    $manual = @(
        "day-session provisional refresh and retained failure fallback",
        "night-session trading-day ownership and later canonical takeover",
        "real and continuous chart identity, settlement, volume, MACD, and open-interest panes",
        "30-year normal/log zoom, free-space pan, extrema, gaps, and rollover measurement warning",
        "drawing create/edit/reopen on real and continuous identities",
        "attached/detached lists, custom groups, and persisted multi-window layout",
        "trend analysis preview/official revision and rollover qualification",
        "bottom-bar WARN/ERROR visibility and rotating-log evidence",
        "read-only MCP futures tools and StockHarness analysis Skill",
        "packaged restart with state recovery and offline retained-history degradation",
        "four-chart and six-chart target-machine interaction performance"
    ) | ForEach-Object { [ordered]@{ item = $_; status = "manual-pending"; evidence = "" } }

    $failed = @($checks | Where-Object { $_.status -eq "failed" }).Count
    $notRun = @($checks | Where-Object { $_.status -eq "not-run" }).Count
    $report = [ordered]@{
        schema_version = "futures-acceptance-v1"
        generated_at = [DateTimeOffset]::Now.ToString("o")
        automated_disposition = $(if ($failed) { "failed" } elseif ($notRun) { "partial" } else { "passed" })
        m6_complete = $false
        checks = $checks
        manual_checks = $manual
    }
    $outputPath = [IO.Path]::GetFullPath((Join-Path $root $Output))
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPath) | Out-Null
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outputPath -Encoding UTF8
    Write-Host "Futures acceptance report: $outputPath"
    Write-Host "Automated disposition: $($report.automated_disposition); M6 complete: false"
    if ($failed) { exit 1 }
}
finally {
    Pop-Location
}
