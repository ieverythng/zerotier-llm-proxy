param(
    [string]$AdapterName = "WiFi",
    [int]$SampleIntervalMilliseconds = 1000,
    [string]$OutputRoot = "",
    [switch]$InstallAutoStart,
    [switch]$Stop,
    [switch]$RemoveAutoStart
)

$ErrorActionPreference = "Stop"

$monitorScript = (Resolve-Path (Join-Path $PSScriptRoot "Monitor-UsbWifiContinuously.ps1")).Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $env:LOCALAPPDATA "Codex\wifi-monitor"
}

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$runName = "CodexUsbWifiMonitor"

function Get-MonitorProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match '(?i)-File\s+"?[^"]*Monitor-UsbWifiContinuously\.ps1(?:\s|"|$)'
        }
}

if ($Stop) {
    $processes = @(Get-MonitorProcesses)
    foreach ($process in $processes) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Stopped $($processes.Count) Wi-Fi monitor process(es)."
}

if ($RemoveAutoStart) {
    Remove-ItemProperty -Path $runKey -Name $runName -ErrorAction SilentlyContinue
    Write-Host "Removed Wi-Fi monitor auto-start entry."
}

if ($Stop -or $RemoveAutoStart) {
    exit 0
}

New-Item -Path $runKey -Force | Out-Null

$monitorArguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", ('"{0}"' -f $monitorScript),
    "-AdapterName", ('"{0}"' -f $AdapterName),
    "-SampleIntervalMilliseconds", $SampleIntervalMilliseconds,
    "-OutputRoot", ('"{0}"' -f $OutputRoot)
)

if ($InstallAutoStart) {
    $command = "powershell.exe " + ($monitorArguments -join " ")
    New-ItemProperty -Path $runKey -Name $runName -Value $command -PropertyType String -Force |
        Out-Null
    Write-Host "Installed Wi-Fi monitor auto-start entry for this user."
}

$existing = @(Get-MonitorProcesses)
if ($existing.Count -gt 0) {
    Write-Host "Wi-Fi monitor is already running. PID(s): $($existing.ProcessId -join ', ')"
    Write-Host "Output: $OutputRoot"
    exit 0
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$process = Start-Process -FilePath "powershell.exe" `
    -ArgumentList $monitorArguments `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 3
if ($process.HasExited) {
    throw "Wi-Fi monitor exited immediately with code $($process.ExitCode)."
}

Write-Host "Wi-Fi monitor started."
Write-Host "PID: $($process.Id)"
Write-Host "Output: $OutputRoot"
Write-Host "Latest status: $(Join-Path $OutputRoot 'latest-status.json')"
