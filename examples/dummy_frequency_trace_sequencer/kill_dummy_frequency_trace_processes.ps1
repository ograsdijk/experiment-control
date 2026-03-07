param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$instanceId = "dummy-frequency-trace-sequencer"
$exampleMarker = "dummy_frequency_trace_sequencer"

$rows = Get-CimInstance Win32_Process |
    Select-Object ProcessId, ParentProcessId, CommandLine

$byPid = @{}
foreach ($row in $rows) {
    $procId = [int]$row.ProcessId
    $byPid[$procId] = $row
}

function Test-SeedMatch {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    $cmd = $CommandLine
    if ($cmd -like "*run_dummy_frequency_trace_stack.py*") { return $true }
    if ($cmd -like "*run_dummy_frequency_trace_fastapi.py*") { return $true }
    if (
        $cmd -like "*experiment_control.cli.run_stack*" -and
        $cmd -like "*$exampleMarker*" -and
        $cmd -like "*stack.yaml*"
    ) { return $true }
    if (
        $cmd -like "*experiment_control.cli.start_driver*" -and
        $cmd -like "*--instance-id*" -and
        $cmd -like "*$instanceId*"
    ) { return $true }
    if (
        $cmd -like "*experiment_control.cli.start_process*" -and
        $cmd -like "*--instance-id*" -and
        $cmd -like "*$instanceId*"
    ) { return $true }
    return $false
}

$targetPids = New-Object "System.Collections.Generic.HashSet[int]"

foreach ($row in $rows) {
    $cmd = [string]$row.CommandLine
    if (Test-SeedMatch -CommandLine $cmd) {
        [void]$targetPids.Add([int]$row.ProcessId)
    }
}

# Add descendants of matched roots (helps when child cmdlines are not unique).
$expanded = $true
while ($expanded) {
    $expanded = $false
    foreach ($row in $rows) {
        $procId = [int]$row.ProcessId
        $ppid = [int]$row.ParentProcessId
        if ($targetPids.Contains($procId)) { continue }
        if ($targetPids.Contains($ppid)) {
            [void]$targetPids.Add($procId)
            $expanded = $true
        }
    }
}

if ($targetPids.Count -eq 0) {
    Write-Output "[cleanup] No matching dummy-frequency-trace processes found."
    exit 0
}

$targets = @()
foreach ($procId in $targetPids) {
    if ($byPid.ContainsKey($procId)) {
        $row = $byPid[$procId]
        $targets += [pscustomobject]@{
            ProcessId      = [int]$row.ProcessId
            ParentProcessId = [int]$row.ParentProcessId
            CommandLine    = [string]$row.CommandLine
        }
    }
}

$targets = $targets | Sort-Object ProcessId -Descending

Write-Output "[cleanup] Matched $($targets.Count) process(es):"
$targets | Select-Object ProcessId, ParentProcessId, CommandLine | Format-Table -AutoSize

if ($DryRun) {
    Write-Output "[cleanup] Dry run only. No processes were terminated."
    exit 0
}

$failed = @()
foreach ($target in $targets) {
    try {
        Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop
        Write-Output "[cleanup] Killed PID $($target.ProcessId)"
    } catch {
        $failed += $target.ProcessId
        Write-Output "[cleanup] Failed to kill PID $($target.ProcessId): $($_.Exception.Message)"
    }
}

if ($failed.Count -gt 0) {
    Write-Output "[cleanup] Failed PIDs: $($failed -join ', ')"
    exit 1
}

Write-Output "[cleanup] Done."
