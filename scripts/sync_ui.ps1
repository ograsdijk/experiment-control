$ErrorActionPreference = "Stop"

param(
  [string]$UiPath = "web/react_ui",
  [switch]$Optional
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$candidate = if ([System.IO.Path]::IsPathRooted($UiPath)) {
  $UiPath
}
else {
  Join-Path $repoRoot $UiPath
}

try {
  $uiRoot = (Resolve-Path $candidate -ErrorAction Stop).Path
}
catch {
  if ($Optional) {
    Write-Host "sync_ui: UI path not found, skipping: $candidate"
    return
  }
  throw
}

$packageJson = Join-Path $uiRoot "package.json"
if (-not (Test-Path $packageJson)) {
  $msg = "sync_ui: package.json not found at $packageJson"
  if ($Optional) {
    Write-Host "$msg (skipping)"
    return
  }
  throw $msg
}

Push-Location $repoRoot
try {
  npm ci --prefix $uiRoot
  if ($LASTEXITCODE -ne 0) {
    throw "npm ci failed with exit code $LASTEXITCODE"
  }
}
finally {
  Pop-Location
}
