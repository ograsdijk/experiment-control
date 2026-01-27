$ErrorActionPreference = "Stop"

param(
  [string]$UiPath = "web/react_ui"
)

& (Join-Path $PSScriptRoot "sync.ps1")
& (Join-Path $PSScriptRoot "sync_ui.ps1") -UiPath $UiPath -Optional
