param(
    [string]$AdapterName = "WiFi",
    [string]$ProfileName = "MOVISTAR_3A60",
    [string]$HardwareId = "USB\VID_2357&PID_0120",
    [string]$InternetTarget = "1.1.1.1",
    [int]$ProbeSeconds = 15,
    [int]$DeviceReturnSeconds = 150,
    [switch]$ReconnectOnly,
    [switch]$Force,
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Continue"

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $env:LOCALAPPDATA "Codex\wifi-monitor\recovery"
}
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$logPath = Join-Path $OutputRoot "recovery-actions.jsonl"
$utf8 = [Text.UTF8Encoding]::new($false)
$writer = [IO.StreamWriter]::new($logPath, $true, $utf8)
$writer.AutoFlush = $true

function Write-RecoveryEvent {
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

function Get-TargetAdapter {
    $byName = Get-NetAdapter -Name $AdapterName -ErrorAction SilentlyContinue
    if ($byName -and $byName.PnPDeviceID -like "$HardwareId*") {
        return $byName
    }

    return Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
        Where-Object PnPDeviceID -Like "$HardwareId*" |
        Select-Object -First 1
}

function Invoke-Probe {
    $adapter = Get-TargetAdapter
    if (-not $adapter -or $adapter.Status -ne "Up") {
        return [pscustomobject]@{
            healthy = $false
            adapter_name = if ($adapter) { [string]$adapter.Name } else { $null }
            adapter_status = if ($adapter) { [string]$adapter.Status } else { "Missing" }
            wlan_state = $null
            gateway = $null
            gateway_ok = $false
            internet_ok = $false
        }
    }

    $wlanBlock = (netsh wlan show interfaces 2>&1) -join "`n"
    $stateMatch = [regex]::Match($wlanBlock, '(?m)^\s*State\s*:\s*(.+)$')
    $wlanState = if ($stateMatch.Success) { $stateMatch.Groups[1].Value.Trim() } else { $null }
    $route = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" `
        -ErrorAction SilentlyContinue |
        Where-Object InterfaceAlias -eq $adapter.Name |
        Sort-Object RouteMetric |
        Select-Object -First 1
    $gateway = if ($route) { [string]$route.NextHop } else { $null }

    $gatewayOk = if ($gateway) {
        Test-Connection -ComputerName $gateway -Count 1 -Quiet -ErrorAction SilentlyContinue
    } else {
        $false
    }
    $internetOk = Test-Connection -ComputerName $InternetTarget -Count 1 -Quiet `
        -ErrorAction SilentlyContinue

    return [pscustomobject]@{
        healthy = (
            $adapter.Status -eq "Up" -and
            $wlanState -eq "connected" -and
            ($gatewayOk -or $internetOk)
        )
        adapter_name = [string]$adapter.Name
        adapter_status = [string]$adapter.Status
        wlan_state = $wlanState
        gateway = $gateway
        gateway_ok = [bool]$gatewayOk
        internet_ok = [bool]$internetOk
    }
}

function Wait-ForHealthyPath {
    param([int]$Seconds)

    $deadline = (Get-Date).AddSeconds($Seconds)
    $lastProbe = $null
    while ((Get-Date) -lt $deadline) {
        $lastProbe = Invoke-Probe
        if ($lastProbe.healthy) {
            return $lastProbe
        }
        Start-Sleep -Seconds 1
    }
    return $lastProbe
}

function Connect-Profile {
    $adapter = Get-TargetAdapter
    if (-not $adapter) { return }
    netsh wlan connect name="$ProfileName" interface="$($adapter.Name)" 2>&1 |
        Out-Null
}

try {
    $initial = Invoke-Probe
    $initialResult = if ($initial.healthy) { "healthy" } else { "failed" }
    Write-RecoveryEvent -Action "probe_initial" -Result $initialResult -Details $initial

    if ($initial.healthy -and -not $Force) {
        Write-RecoveryEvent -Action "recovery" -Result "skipped_already_healthy"
        Write-Host "Wi-Fi path is already healthy; no recovery action was taken."
        exit 0
    }

    $adapter = Get-TargetAdapter
    $adapterMissingInitially = ($null -eq $adapter)
    if ($adapter) {
        netsh wlan disconnect interface="$($adapter.Name)" 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        Connect-Profile
        $probe = Wait-ForHealthyPath -Seconds $ProbeSeconds
        $reconnectResult = if ($probe.healthy) { "recovered" } else { "failed" }
        Write-RecoveryEvent -Action "profile_reconnect" -Result $reconnectResult -Details $probe
        if ($probe.healthy) {
            Write-Host "Wi-Fi recovered after profile reconnect."
            exit 0
        }
    }

    if ($ReconnectOnly -and $adapterMissingInitially) {
        $returnDeadline = (Get-Date).AddSeconds($DeviceReturnSeconds)
        while ((Get-Date) -lt $returnDeadline) {
            $adapter = Get-TargetAdapter
            if ($adapter) {
                Write-RecoveryEvent -Action "device_return_wait" -Result "adapter_present" `
                    -Details @{
                        adapter_name = [string]$adapter.Name
                        adapter_status = [string]$adapter.Status
                    }
                Connect-Profile
                $probe = Wait-ForHealthyPath -Seconds $ProbeSeconds
                $returnResult = if ($probe.healthy) { "recovered" } else { "failed" }
                Write-RecoveryEvent -Action "post_return_reconnect" `
                    -Result $returnResult -Details $probe
                if ($probe.healthy) {
                    Write-Host "Wi-Fi recovered after the adapter returned."
                    exit 0
                }
            }
            Start-Sleep -Seconds 1
        }
        Write-RecoveryEvent -Action "recovery" -Result "reconnect_only_exhausted"
        Write-Error "Profile reconnect did not restore the Wi-Fi path; privileged recovery is required."
        exit 1
    }
    elseif ($ReconnectOnly) {
        Write-RecoveryEvent -Action "recovery" -Result "reconnect_only_exhausted"
        Write-Error "Profile reconnect did not restore the Wi-Fi path; privileged recovery is required."
        exit 1
    }

    $adapter = Get-TargetAdapter
    $instanceId = if ($adapter) { [string]$adapter.PnPDeviceID } else {
        [string](
            Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
                Where-Object InstanceId -Like "$HardwareId*" |
                Select-Object -First 1 -ExpandProperty InstanceId
        )
    }

    if ($instanceId) {
        & pnputil.exe /restart-device "$instanceId" | Out-Null
        Start-Sleep -Seconds 5
        Connect-Profile
        $probe = Wait-ForHealthyPath -Seconds $ProbeSeconds
        $restartResult = if ($probe.healthy) { "recovered" } else { "failed" }
        Write-RecoveryEvent -Action "pnp_restart" -Result $restartResult -Details $probe
        if ($probe.healthy) {
            Write-Host "Wi-Fi recovered after PnP device restart."
            exit 0
        }

        & pnputil.exe /remove-device "$instanceId" | Out-Null
        Start-Sleep -Seconds 3
        & pnputil.exe /scan-devices | Out-Null
        Start-Sleep -Seconds 7
        Connect-Profile
        $probe = Wait-ForHealthyPath -Seconds $ProbeSeconds
        $reenumerateResult = if ($probe.healthy) { "recovered" } else { "failed" }
        Write-RecoveryEvent -Action "pnp_reenumerate" -Result $reenumerateResult -Details $probe
        if ($probe.healthy) {
            Write-Host "Wi-Fi recovered after PnP device re-enumeration."
            exit 0
        }
    }

    Write-RecoveryEvent -Action "recovery" -Result "exhausted"
    throw "Wi-Fi recovery actions were exhausted without restoring the path."
}
finally {
    $writer.Dispose()
}
