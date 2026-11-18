param(
    [switch]$BuildUI,
    [switch]$Test,
    [switch]$Seed,
    [int]$Port = 8000,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
function Run-Native {
    $executablePath = $args[0]
    $nativeArgs = @($args | Select-Object -Skip 1)
    & $executablePath @nativeArgs
    if ($LASTEXITCODE -ne 0) { throw "$executablePath exited with status $LASTEXITCODE" }
}
Push-Location $root
try {
    $venv = Join-Path $root ".venv/Scripts/python.exe"
    if (!(Test-Path -LiteralPath $venv)) {
        Run-Native $Python -m venv .venv
        Run-Native $venv -m pip install -e "core[dev]"
    }
    if ($BuildUI -or (!(Test-Path web/dist/index.html) -and !$Test -and !$Seed)) {
        Push-Location web
        try { Run-Native npm.cmd ci; Run-Native npm.cmd run build }
        finally { Pop-Location }
    }
    if ($BuildUI -and !$Test -and !$Seed) { return }
    Push-Location core
    try {
        if ($Test) { Run-Native $venv -m pytest; return }
        if ($Seed -and ($env:ZADDESH_DEMO_MODE -ne "true" -or $env:ZADDESH_DATABASE__URL -notmatch "^mongodb(?:\+srv)?://.+/zaddesh_demo(?:\?|$)")) {
            throw "Set ZADDESH_DEMO_MODE=true and use a separate MongoDB database named zaddesh_demo before seeding"
        }
        Run-Native $venv -m zaddesh_core.storage_init
        if ($Seed) { Run-Native $venv -m zaddesh_core.seed; return }
        Run-Native $venv -m uvicorn zaddesh_core.main:app --host 127.0.0.1 --port "$Port" --no-proxy-headers
    } finally { Pop-Location }
} finally { Pop-Location }
