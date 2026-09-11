param(
    [string]$StatusPath = "",
    [string]$RecoveryTaskName = "CodexUsbWifiRecovery",
    [string]$AdapterName = "WiFi",
    [string]$ProfileName = "MOVISTAR_3A60",
    [int]$FailureThresholdSeconds = 12,
    [int]$CooldownSeconds = 180,
    [int]$PollMilliseconds = 1000,
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Continue"

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $env:LOCALAPPDATA "Codex\wifi-monitor\recovery"
}
if (-not $StatusPath) {
    $StatusPath = Join-Path (Split-Path $OutputRoot) "latest-status.json"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$logPath = Join-Path $OutputRoot "watchdog.jsonl"
$pidPath = Join-Path $OutputRoot "watchdog.pid"
$utf8 = [Text.UTF8Encoding]::new($false)
$writer = [IO.StreamWriter]::new($logPath, $true, $utf8)
$writer.AutoFlush = $true
[IO.File]::WriteAllText($pidPath, [string]$PID, $utf8)

function Write-WatchdogEvent {
    param(
        [string]$Action,
        [string]$Result,
        [object]$Details = $null
    )

    $writer.WriteLine((
        [pscustomobject]@{
            timestamp = (Get-Date).ToString("o")
            action = $Action
            result = $Result
            details = $Details
        } | ConvertTo-Json -Depth 5 -Compress
    ))
}

function Get-LatestStatus {
    if (-not (Test-Path -LiteralPath $StatusPath)) { return $null }
    try {
        return [IO.File]::ReadAllText($StatusPath) | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

$seriousClassifications = @(
    "wifi_or_lan_path_failed",
    "wifi_disconnected",
    "pnp_error",
    "adapter_missing"
)
$failureStartedAt = $null
$lastRecoveryAt = [datetime]::MinValue
$lastTaskMissingLogAt = [datetime]::MinValue
$recoveryScript = Join-Path $PSScriptRoot "Invoke-UsbWifiRecovery.ps1"

Write-WatchdogEvent -Action "watchdog" -Result "started" -Details @{
    pid = $PID
    status_path = $StatusPath
    threshold_seconds = $FailureThresholdSeconds
    cooldown_seconds = $CooldownSeconds
}

try {
    while ($true) {
        $now = Get-Date
        $status = Get-LatestStatus
        $statusTimestamp = if ($status -and $status.timestamp) {
            try { [datetime]$status.timestamp } catch { $null }
        } else {
            $null
        }
        $statusFresh = (
            $statusTimestamp -and
            ($now - $statusTimestamp).TotalSeconds -le 10
        )
        $seriousFailure = (
            $statusFresh -and
            $status.classification -in $seriousClassifications
        )

        if (-not $seriousFailure) {
            $failureStartedAt = $null
            Start-Sleep -Milliseconds $PollMilliseconds
            continue
        }

        if (-not $failureStartedAt) {
            $failureStartedAt = $now
            Write-WatchdogEvent -Action "failure_window" -Result "started" -Details @{
                classification = $status.classification
                status_timestamp = $status.timestamp
            }
        }

        $failureSeconds = ($now - $failureStartedAt).TotalSeconds
        $cooldownElapsed = ($now - $lastRecoveryAt).TotalSeconds
        if (
            $failureSeconds -ge $FailureThresholdSeconds -and
            $cooldownElapsed -ge $CooldownSeconds
        ) {
            $task = Get-ScheduledTask -TaskName $RecoveryTaskName -ErrorAction SilentlyContinue
            if ($task) {
                try {
                    Start-ScheduledTask -TaskName $RecoveryTaskName -ErrorAction Stop
                    $lastRecoveryAt = $now
                    $failureStartedAt = $null
                    Write-WatchdogEvent -Action "recovery_task" -Result "started" -Details @{
                        task_name = $RecoveryTaskName
                        classification = $status.classification
                        failure_seconds = [Math]::Round($failureSeconds, 3)
                    }
                }
                catch {
                    Write-WatchdogEvent -Action "recovery_task" -Result "start_failed" `
                        -Details $_.Exception.Message
                    $lastRecoveryAt = $now
                }
            }
            else {
                try {
                    $fallbackArguments = @(
                        "-NoProfile",
                        "-ExecutionPolicy", "Bypass",
                        "-WindowStyle", "Hidden",
                        "-File", ('"{0}"' -f $recoveryScript),
                        "-AdapterName", ('"{0}"' -f $AdapterName),
                        "-ProfileName", ('"{0}"' -f $ProfileName),
                        "-ReconnectOnly"
                    )
                    Start-Process -FilePath "powershell.exe" `
                        -ArgumentList $fallbackArguments `
                        -WindowStyle Hidden |
                        Out-Null
                    $lastRecoveryAt = $now
                    $failureStartedAt = $null
                    Write-WatchdogEvent -Action "recovery_fallback" `
                        -Result "profile_reconnect_started" -Details @{
                            task_name = $RecoveryTaskName
                            classification = $status.classification
                            failure_seconds = [Math]::Round($failureSeconds, 3)
                        }
                }
                catch {
                    Write-WatchdogEvent -Action "recovery_fallback" `
                        -Result "start_failed" -Details $_.Exception.Message
                    $lastRecoveryAt = $now
                }
                if (($now - $lastTaskMissingLogAt).TotalSeconds -ge 300) {
                    Write-WatchdogEvent -Action "recovery_task" -Result "not_installed" `
                        -Details @{ task_name = $RecoveryTaskName }
                    $lastTaskMissingLogAt = $now
                }
            }
        }

        Start-Sleep -Milliseconds $PollMilliseconds
    }
}
finally {
    Write-WatchdogEvent -Action "watchdog" -Result "stopped"
    $writer.Dispose()
}
