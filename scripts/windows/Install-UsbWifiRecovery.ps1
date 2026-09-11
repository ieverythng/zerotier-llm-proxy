param(
    [string]$AdapterName = "WiFi",
    [string]$ProfileName = "MOVISTAR_3A60",
    [string]$RecoveryTaskName = "CodexUsbWifiRecovery",
    [int]$FailureThresholdSeconds = 12,
    [int]$CooldownSeconds = 180,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

$recoveryScript = (
    Resolve-Path (Join-Path $PSScriptRoot "Invoke-UsbWifiRecovery.ps1")
).Path
$watchdogScript = (
    Resolve-Path (Join-Path $PSScriptRoot "Watch-UsbWifiRecovery.ps1")
).Path
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$runName = "CodexUsbWifiRecoveryWatchdog"

if ($Uninstall) {
    if ($isAdministrator) {
        Unregister-ScheduledTask -TaskName $RecoveryTaskName -Confirm:$false `
            -ErrorAction SilentlyContinue
    }
    Remove-ItemProperty -Path $runKey -Name $runName -ErrorAction SilentlyContinue

    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match '(?i)-File\s+"?[^"]*Watch-UsbWifiRecovery\.ps1'
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Write-Host "Removed Wi-Fi recovery task, watchdog auto-start, and running watchdog."
    exit 0
}

if ($isAdministrator) {
    $recoveryArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", ('"{0}"' -f $recoveryScript),
        "-AdapterName", ('"{0}"' -f $AdapterName),
        "-ProfileName", ('"{0}"' -f $ProfileName)
    ) -join " "
    $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument $recoveryArguments
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity.Name `
        -LogonType Interactive -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::FromMinutes(5)) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $RecoveryTaskName `
        -Action $taskAction `
        -Principal $taskPrincipal `
        -Settings $taskSettings `
        -Description "Conservative recovery for sustained TP-Link USB Wi-Fi path failures." `
        -Force |
        Out-Null
}

New-Item -Path $runKey -Force | Out-Null
$watchdogArguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", ('"{0}"' -f $watchdogScript),
    "-RecoveryTaskName", ('"{0}"' -f $RecoveryTaskName),
    "-AdapterName", ('"{0}"' -f $AdapterName),
    "-ProfileName", ('"{0}"' -f $ProfileName),
    "-FailureThresholdSeconds", $FailureThresholdSeconds,
    "-CooldownSeconds", $CooldownSeconds
)
$watchdogCommand = "powershell.exe " + ($watchdogArguments -join " ")
New-ItemProperty -Path $runKey -Name $runName -Value $watchdogCommand `
    -PropertyType String -Force |
    Out-Null

$existing = @(
    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match '(?i)-File\s+"?[^"]*Watch-UsbWifiRecovery\.ps1'
        }
)
if ($existing.Count -eq 0) {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList $watchdogArguments `
        -WindowStyle Hidden |
        Out-Null
}

if ($isAdministrator) {
    Write-Host "Installed scheduled recovery task: $RecoveryTaskName"
} else {
    Write-Host "Installed user-mode reconnect fallback; elevated device recovery is not installed."
}
Write-Host "Installed watchdog auto-start: $runName"
Write-Host "Failure threshold: $FailureThresholdSeconds seconds"
Write-Host "Recovery cooldown: $CooldownSeconds seconds"
