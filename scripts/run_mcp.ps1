$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$executable = Join-Path $projectRoot ".venv\Scripts\stock-harness-mcp.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    throw "StockHarness MCP executable not found. Create the project virtual environment first."
}

if (-not $env:STOCK_HARNESS_API_URL) {
    $env:STOCK_HARNESS_API_URL = "http://127.0.0.1:8765"
}

& $executable
exit $LASTEXITCODE
