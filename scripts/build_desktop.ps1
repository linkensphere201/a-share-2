param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$buildRoot = Join-Path $root "build\desktop"
$distRoot = Join-Path $root "dist"
$packageRoot = Join-Path $distRoot "StockHarness"
$packagePrefix = [IO.Path]::GetFullPath($packageRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

$runningPackageProcesses = @(Get-Process -Name "StockHarness" -ErrorAction SilentlyContinue | Where-Object {
    try {
        $processPath = [IO.Path]::GetFullPath($_.Path)
        $processPath.StartsWith($packagePrefix, [StringComparison]::OrdinalIgnoreCase)
    }
    catch {
        $false
    }
})

foreach ($process in $runningPackageProcesses) {
    Write-Host "Stopping running package process: $($process.Id) $($process.Path)"
    Stop-Process -Id $process.Id -Force
}

foreach ($process in $runningPackageProcesses) {
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((Get-Process -Id $process.Id -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
    }
    if (Get-Process -Id $process.Id -ErrorAction SilentlyContinue) {
        throw "Running package process did not stop: $($process.Id)"
    }
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
