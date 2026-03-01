param(
    [string]$Uv = "uv"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $repoRoot

Push-Location $repoRoot
try {
    if (-not $env:UV_CACHE_DIR) {
        $env:UV_CACHE_DIR = Join-Path $repoRoot ".uv-cache"
    }
    & $Uv run python -m experiment_control.cli.run_stack "examples/federation_dummy/hub/stack.yaml"
}
finally {
    Pop-Location
}
