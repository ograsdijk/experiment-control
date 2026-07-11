$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$uiRoot = Join-Path $repoRoot "web\react_ui"
$uiDist = Join-Path $uiRoot "dist"
$packagedUi = Join-Path $repoRoot "src\experiment_control\_ui_dist"

if (-not (Test-Path (Join-Path $uiRoot "package.json"))) {
  throw "build_packaged_ui: UI source not found at $uiRoot"
}

# npm 11+ resolves package.json from the current working directory before
# honouring --prefix, so running from the repo root fails with ENOENT even
# though `--prefix web\react_ui` is supplied. Push into the UI source dir
# for all npm invocations and drop the now-redundant --prefix flag.
Push-Location $uiRoot
try {
  npm ci --prefer-offline --no-audit
  if ($LASTEXITCODE -ne 0) {
    throw "npm ci failed with exit code $LASTEXITCODE"
  }

  npm run build
  if ($LASTEXITCODE -ne 0) {
    throw "npm run build failed with exit code $LASTEXITCODE"
  }

  if (-not (Test-Path (Join-Path $uiDist "index.html"))) {
    throw "build_packaged_ui: build output missing at $uiDist"
  }

  New-Item -ItemType Directory -Path $packagedUi -Force | Out-Null
  Get-ChildItem -Path $packagedUi -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
  Copy-Item (Join-Path $uiDist "*") $packagedUi -Recurse -Force

  Write-Host "build_packaged_ui: copied $uiDist -> $packagedUi"
}
finally {
  Pop-Location
}
