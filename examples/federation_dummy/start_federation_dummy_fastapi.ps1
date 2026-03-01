param(
    [string]$Uv = "uv",
    [string]$WebHost = "127.0.0.1",
    [int]$Port = 8010
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $repoRoot

Push-Location $repoRoot
try {
    if (-not $env:UV_CACHE_DIR) {
        $env:UV_CACHE_DIR = Join-Path $repoRoot ".uv-cache"
    }
    & $Uv run python "examples/federation_dummy/run_hub_fastapi.py" --host $WebHost --port $Port
}
finally {
    Pop-Location
}
