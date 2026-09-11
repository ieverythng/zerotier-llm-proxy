param(
    [string]$AdapterName = "WiFi",
    [string]$InternetTarget = "1.1.1.1",
    [string]$HttpProbeUrl = "http://www.msftconnecttest.com/connecttest.txt",
    [int]$SampleIntervalMilliseconds = 1000,
    [int]$HttpProbeIntervalSeconds = 30,
    [int]$EventProbeIntervalSeconds = 2,
    [int]$DurationSeconds = 0,
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Net.Http

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $env:LOCALAPPDATA "Codex\wifi-monitor"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$now = Get-Date
$bootTime = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$sessionId = "{0}-{1}" -f $bootTime.ToString("yyyyMMdd_HHmmss"), $now.ToString("yyyyMMdd_HHmmss")
$sessionDirectory = Join-Path $OutputRoot $sessionId
New-Item -ItemType Directory -Force -Path $sessionDirectory | Out-Null

$samplesPath = Join-Path $sessionDirectory "samples.jsonl"
$eventsPath = Join-Path $sessionDirectory "events.jsonl"
$statusPath = Join-Path $OutputRoot "latest-status.json"
$sessionPath = Join-Path $sessionDirectory "session.json"
$monitorPidPath = Join-Path $OutputRoot "monitor.pid"

$utf8 = [Text.UTF8Encoding]::new($false)
$sampleWriter = [IO.StreamWriter]::new($samplesPath, $true, $utf8)
$eventWriter = [IO.StreamWriter]::new($eventsPath, $true, $utf8)
$sampleWriter.AutoFlush = $true
$eventWriter.AutoFlush = $true

function Write-JsonLine {
    param(
        [IO.StreamWriter]$Writer,
        [object]$Value
    )

    $Writer.WriteLine(($Value | ConvertTo-Json -Depth 8 -Compress))
}

function Write-JsonFile {
    param(
        [string]$Path,
        [object]$Value
    )

    [IO.File]::WriteAllText(
        $Path,
        ($Value | ConvertTo-Json -Depth 8),
        [Text.UTF8Encoding]::new($false)
    )
}

function Invoke-Ping {
    param(
        [string]$Target,
        [int]$TimeoutMilliseconds = 700
    )

    if (-not $Target) {
        return [pscustomobject]@{ ok = $false; latency_ms = $null; status = "no_target" }
    }

    $ping = [Net.NetworkInformation.Ping]::new()
    try {
        $reply = $ping.Send($Target, $TimeoutMilliseconds)
        return [pscustomobject]@{
            ok = ($reply.Status -eq [Net.NetworkInformation.IPStatus]::Success)
            latency_ms = if ($reply.Status -eq [Net.NetworkInformation.IPStatus]::Success) {
                [int]$reply.RoundtripTime
            } else {
                $null
            }
            status = [string]$reply.Status
        }
    }
    catch {
        return [pscustomobject]@{
            ok = $false
            latency_ms = $null
            status = $_.Exception.GetType().Name
        }
    }
    finally {
        $ping.Dispose()
    }
}

function Invoke-HttpProbe {
    param([string]$Url)

    $handler = [Net.Http.HttpClientHandler]::new()
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(3)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $response = $null
    try {
        $response = $client.GetAsync(
            $Url,
            [Net.Http.HttpCompletionOption]::ResponseHeadersRead
        ).GetAwaiter().GetResult()
        $timer.Stop()
        return [pscustomobject]@{
            ok = $response.IsSuccessStatusCode
            latency_ms = [Math]::Round($timer.Elapsed.TotalMilliseconds, 3)
            status_code = [int]$response.StatusCode
            error = $null
        }
    }
    catch {
        $timer.Stop()
        return [pscustomobject]@{
            ok = $false
            latency_ms = [Math]::Round($timer.Elapsed.TotalMilliseconds, 3)
            status_code = $null
            error = $_.Exception.Message
        }
    }
    finally {
        if ($response) { $response.Dispose() }
        $client.Dispose()
        $handler.Dispose()
    }
}

function Get-WlanSnapshot {
    $text = @(netsh wlan show interfaces 2>&1)
    $block = $text -join "`n"

    function Get-MatchValue {
        param([string]$Pattern)
        $match = [regex]::Match($block, $Pattern)
        if ($match.Success) { return $match.Groups[1].Value.Trim() }
        return $null
    }

    return [pscustomobject]@{
        raw = $block
        name = Get-MatchValue '(?m)^\s*Name\s*:\s*(.+)$'
        state = Get-MatchValue '(?m)^\s*State\s*:\s*(.+)$'
        ssid = Get-MatchValue '(?m)^\s*SSID\s*:\s*(.+)$'
        bssid = Get-MatchValue '(?m)^\s*BSSID\s*:\s*(.+)$'
        radio_type = Get-MatchValue '(?m)^\s*Radio type\s*:\s*(.+)$'
        channel = Get-MatchValue '(?m)^\s*Channel\s*:\s*(.+)$'
        signal_pct = Get-MatchValue '(?m)^\s*Signal\s*:\s*(\d+)%'
        receive_rate_mbps = Get-MatchValue '(?m)^\s*Receive rate \(Mbps\)\s*:\s*([\d.]+)'
        transmit_rate_mbps = Get-MatchValue '(?m)^\s*Transmit rate \(Mbps\)\s*:\s*([\d.]+)'
        profile = Get-MatchValue '(?m)^\s*Profile\s*:\s*(.+)$'
    }
}

function Get-DefaultGateway {
    param([string]$Name)

    $route = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Where-Object InterfaceAlias -eq $Name |
        Sort-Object RouteMetric |
        Select-Object -First 1
    if ($route) { return [string]$route.NextHop }
    return $null
}

function Get-EventClassification {
    param(
        [string]$Source,
        [int]$Id,
        [string]$Message
    )

    if ($Message -match 'disconnected by the user') { return "user_disconnect" }
    if ($Message -match 'network is disconnected by the driver') { return "driver_disconnect" }
    if ($Message -match 'driver disconnected while associating') { return "driver_association_failure" }
    if ($Message -match 'specific network is not available') { return "network_not_available" }
    if ($Message -match 'operation was cancelled') { return "connection_cancelled" }
    if ($Id -eq 8001) { return "connected" }
    if ($Id -eq 8000 -or $Id -eq 11000) { return "connection_started" }
    if ($Id -eq 11001 -or $Id -eq 11005) { return "association_or_security_succeeded" }
    if ($Id -eq 11004) { return "wireless_security_stopped" }
    if ($Source -eq "System" -and $Message -match 'removed|deleted|uninstall') {
        return "device_removed"
    }
    if ($Source -eq "System" -and $Message -match 'reset|restart|configured|started') {
        return "device_reset_or_started"
    }
    return "diagnostic_event"
}

function Write-EventRecord {
    param(
        [string]$Source,
        $Event
    )

    $message = [string]$Event.Message
    $record = [pscustomobject]@{
        captured_at = (Get-Date).ToString("o")
        source = $Source
        event_time = $Event.TimeCreated.ToString("o")
        record_id = [long]$Event.RecordId
        provider = [string]$Event.ProviderName
        id = [int]$Event.Id
        level = [string]$Event.LevelDisplayName
        classification = Get-EventClassification -Source $Source -Id $Event.Id -Message $message
        message = $message
    }
    Write-JsonLine -Writer $eventWriter -Value $record
    return $record
}

function Write-IncidentSnapshot {
    param(
        [string]$Reason,
        [string]$InstanceId
    )

    $snapshotStamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
    $safeReason = $Reason -replace '[^A-Za-z0-9_-]', '_'
    $snapshotPath = Join-Path $sessionDirectory "snapshot-$snapshotStamp-$safeReason.txt"
    $lines = [Collections.Generic.List[string]]::new()
    $lines.Add("captured_at: $((Get-Date).ToString('o'))")
    $lines.Add("reason: $Reason")
    $lines.Add("")
    $lines.Add("=== netsh wlan show interfaces ===")
    $lines.AddRange([string[]]@(netsh wlan show interfaces 2>&1))
    $lines.Add("")
    $lines.Add("=== netsh wlan show drivers ===")
    $lines.AddRange([string[]]@(netsh wlan show drivers 2>&1))
    $lines.Add("")
    $lines.Add("=== netsh wlan show networks mode=bssid ===")
    $lines.AddRange([string[]]@(netsh wlan show networks mode=bssid 2>&1))
    $lines.Add("")
    $lines.Add("=== Get-NetAdapter ===")
    $lines.AddRange([string[]]@(
        Get-NetAdapter -Name $AdapterName -ErrorAction SilentlyContinue |
            Format-List * |
            Out-String -Width 240
    ))
    if ($InstanceId) {
        $lines.Add("")
        $lines.Add("=== PnP device and properties ===")
        $lines.AddRange([string[]]@(
            Get-PnpDevice -InstanceId $InstanceId -ErrorAction SilentlyContinue |
                Format-List * |
                Out-String -Width 240
        ))
        $lines.AddRange([string[]]@(
            Get-PnpDeviceProperty -InstanceId $InstanceId -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.KeyName -match 'Driver|Parent|Location|Problem|Removal|Power|BusReported'
                } |
                Format-Table KeyName, Data -Wrap |
                Out-String -Width 240
        ))
    }
    [IO.File]::WriteAllLines($snapshotPath, $lines, $utf8)
    return $snapshotPath
}

$initialAdapter = Get-NetAdapter -Name $AdapterName -ErrorAction SilentlyContinue
$instanceId = if ($initialAdapter) { [string]$initialAdapter.PnPDeviceID } else { $null }
$driver = if ($instanceId) {
    Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue |
        Where-Object DeviceID -eq $instanceId |
        Select-Object -First 1
} else {
    $null
}

$session = [pscustomobject]@{
    schema_version = 3
    session_id = $sessionId
    monitor_pid = $PID
    machine = $env:COMPUTERNAME
    user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    started_at = $now.ToString("o")
    boot_time = $bootTime.ToString("o")
    adapter_name = $AdapterName
    interface_description = if ($initialAdapter) { [string]$initialAdapter.InterfaceDescription } else { $null }
    pnp_instance_id = $instanceId
    driver_version = if ($driver) { [string]$driver.DriverVersion } else { $null }
    driver_provider = if ($driver) { [string]$driver.DriverProviderName } else { $null }
    sample_interval_ms = $SampleIntervalMilliseconds
    internet_target = $InternetTarget
    samples_path = $samplesPath
    events_path = $eventsPath
}
Write-JsonFile -Path $sessionPath -Value $session
[IO.File]::WriteAllText($monitorPidPath, [string]$PID, $utf8)

$startEvent = [pscustomobject]@{
    captured_at = (Get-Date).ToString("o")
    source = "Monitor"
    event_time = (Get-Date).ToString("o")
    record_id = 0
    provider = "Monitor-UsbWifiContinuously"
    id = 1
    level = "Information"
    classification = "monitor_started"
    message = "Continuous Wi-Fi monitoring started."
}
Write-JsonLine -Writer $eventWriter -Value $startEvent

$wlanLog = "Microsoft-Windows-WLAN-AutoConfig/Operational"
$networkProfileLog = "Microsoft-Windows-NetworkProfile/Operational"
$eventCursor = @{}
foreach ($logName in @($wlanLog, "System", $networkProfileLog)) {
    $latest = Get-WinEvent -LogName $logName -MaxEvents 1 -ErrorAction SilentlyContinue
    $eventCursor[$logName] = if ($latest) { [long]$latest.RecordId } else { 0L }
}

$startedAt = Get-Date
$sequence = 0L
$lastClassification = $null
$consecutiveSeriousSamples = 0
$incidentSnapshotCaptured = $false
$seriousClassifications = @(
    "adapter_missing",
    "pnp_error",
    "wifi_disconnected",
    "wifi_or_lan_path_failed"
)
$lastHttpProbe = [pscustomobject]@{
    ok = $null
    latency_ms = $null
    status_code = $null
    error = $null
}
$lastHttpProbeAt = [datetime]::MinValue
$lastEventProbeAt = Get-Date

try {
    while ($DurationSeconds -le 0 -or ((Get-Date) - $startedAt).TotalSeconds -lt $DurationSeconds) {
        $iterationStarted = Get-Date
        $sequence++
        try {
            $adapter = Get-NetAdapter -Name $AdapterName -ErrorAction SilentlyContinue
            if ($adapter -and -not $instanceId) {
                $instanceId = [string]$adapter.PnPDeviceID
            }
            $pnp = if ($instanceId) {
                Get-PnpDevice -InstanceId $instanceId -ErrorAction SilentlyContinue
            } else {
                $null
            }
            $statistics = Get-NetAdapterStatistics -Name $AdapterName -ErrorAction SilentlyContinue
            $processor = Get-CimInstance `
                -ClassName Win32_PerfFormattedData_PerfOS_Processor `
                -Filter "Name='_Total'" `
                -ErrorAction SilentlyContinue
            $wlan = Get-WlanSnapshot
            $gateway = Get-DefaultGateway -Name $AdapterName
            $gatewayPing = Invoke-Ping -Target $gateway
            $internetPing = Invoke-Ping -Target $InternetTarget

            $httpProbeSample = ((Get-Date) - $lastHttpProbeAt).TotalSeconds -ge $HttpProbeIntervalSeconds
            if ($httpProbeSample) {
                $lastHttpProbe = Invoke-HttpProbe -Url $HttpProbeUrl
                $lastHttpProbeAt = Get-Date
            }

            $classification = "healthy"
            if (-not $adapter -or -not $pnp) {
                $classification = "adapter_missing"
            }
            elseif ($pnp.Status -ne "OK") {
                $classification = "pnp_error"
            }
            elseif ($adapter.Status -ne "Up" -or $wlan.state -ne "connected") {
                $classification = "wifi_disconnected"
            }
            elseif (-not $gatewayPing.ok -and -not $internetPing.ok) {
                $classification = "wifi_or_lan_path_failed"
            }
            elseif ($gatewayPing.ok -and -not $internetPing.ok) {
                $classification = "internet_only_failed"
            }
            elseif (-not $gatewayPing.ok -and $internetPing.ok) {
                $classification = "gateway_icmp_unavailable"
            }

            $sample = [pscustomobject]@{
                timestamp = $iterationStarted.ToString("o")
                boot_time = $bootTime.ToString("o")
                sequence = $sequence
                elapsed_s = [Math]::Round(($iterationStarted - $startedAt).TotalSeconds, 3)
                classification = $classification
                adapter_present = ($null -ne $adapter)
                adapter_status = if ($adapter) { [string]$adapter.Status } else { "Missing" }
                media_connection_state = if ($adapter) { [string]$adapter.MediaConnectionState } else { "Missing" }
                link_speed = if ($adapter) { [string]$adapter.LinkSpeed } else { $null }
                interface_guid = if ($adapter) { [string]$adapter.InterfaceGuid } else { $null }
                pnp_present = ($null -ne $pnp)
                pnp_status = if ($pnp) { [string]$pnp.Status } else { "Missing" }
                pnp_problem = if ($pnp) { [string]$pnp.Problem } else { "Missing" }
                wlan_state = $wlan.state
                ssid = $wlan.ssid
                bssid = $wlan.bssid
                profile = $wlan.profile
                radio_type = $wlan.radio_type
                channel = $wlan.channel
                signal_pct = $wlan.signal_pct
                receive_rate_mbps = $wlan.receive_rate_mbps
                transmit_rate_mbps = $wlan.transmit_rate_mbps
                gateway = $gateway
                gateway_ok = $gatewayPing.ok
                gateway_latency_ms = $gatewayPing.latency_ms
                gateway_ping_status = $gatewayPing.status
                internet_target = $InternetTarget
                internet_ok = $internetPing.ok
                internet_latency_ms = $internetPing.latency_ms
                internet_ping_status = $internetPing.status
                http_probe_sample = $httpProbeSample
                http_ok = $lastHttpProbe.ok
                http_latency_ms = $lastHttpProbe.latency_ms
                http_status_code = $lastHttpProbe.status_code
                http_error = $lastHttpProbe.error
                received_bytes = if ($statistics) { [long]$statistics.ReceivedBytes } else { $null }
                sent_bytes = if ($statistics) { [long]$statistics.SentBytes } else { $null }
                received_errors = if ($statistics) { [long]$statistics.ReceivedPacketErrors } else { $null }
                outbound_errors = if ($statistics) { [long]$statistics.OutboundPacketErrors } else { $null }
                received_discards = if ($statistics) { [long]$statistics.ReceivedDiscardedPackets } else { $null }
                outbound_discards = if ($statistics) { [long]$statistics.OutboundDiscardedPackets } else { $null }
                cpu_pct = if ($processor) { [int]$processor.PercentProcessorTime } else { $null }
                dpc_pct = if ($processor) { [int]$processor.PercentDPCTime } else { $null }
                interrupt_pct = if ($processor) { [int]$processor.PercentInterruptTime } else { $null }
                interrupts_per_s = if ($processor) { [long]$processor.InterruptsPersec } else { $null }
                dpcs_queued_per_s = if ($processor) { [long]$processor.DPCsQueuedPersec } else { $null }
                dpc_rate = if ($processor) { [long]$processor.DPCRate } else { $null }
            }

            Write-JsonLine -Writer $sampleWriter -Value $sample
            Write-JsonFile -Path $statusPath -Value $sample

            $snapshotPath = $null
            if ($classification -eq "healthy") {
                $consecutiveSeriousSamples = 0
                $incidentSnapshotCaptured = $false
            }
            elseif ($classification -in $seriousClassifications) {
                $consecutiveSeriousSamples++
                $snapshotThreshold = if (
                    $classification -eq "wifi_or_lan_path_failed"
                ) {
                    5
                } else {
                    1
                }
                if (
                    -not $incidentSnapshotCaptured -and
                    $consecutiveSeriousSamples -ge $snapshotThreshold
                ) {
                    $snapshotPath = Write-IncidentSnapshot `
                        -Reason $classification `
                        -InstanceId $instanceId
                    $incidentSnapshotCaptured = $true
                }
            }
            else {
                $consecutiveSeriousSamples = 0
            }

            if ($classification -ne $lastClassification) {
                Write-JsonLine -Writer $eventWriter -Value ([pscustomobject]@{
                    captured_at = (Get-Date).ToString("o")
                    source = "Monitor"
                    event_time = (Get-Date).ToString("o")
                    record_id = 0
                    provider = "Monitor-UsbWifiContinuously"
                    id = 2
                    level = if ($classification -eq "healthy") { "Information" } else { "Warning" }
                    classification = "state_transition"
                    message = "$lastClassification -> $classification"
                    snapshot_path = $snapshotPath
                })
                $lastClassification = $classification
            }

            if (((Get-Date) - $lastEventProbeAt).TotalSeconds -ge $EventProbeIntervalSeconds) {
                $eventWindowStart = $lastEventProbeAt.AddSeconds(-1)

                $wlanEvents = @(
                    Get-WinEvent -FilterHashtable @{
                        LogName = $wlanLog
                        StartTime = $eventWindowStart
                    } -ErrorAction SilentlyContinue |
                        Where-Object RecordId -gt $eventCursor[$wlanLog] |
                        Sort-Object RecordId
                )
                foreach ($event in $wlanEvents) {
                    $null = Write-EventRecord -Source "WLAN-AutoConfig" -Event $event
                    $eventCursor[$wlanLog] = [Math]::Max($eventCursor[$wlanLog], [long]$event.RecordId)
                }

                $systemEvents = @(
                    Get-WinEvent -FilterHashtable @{
                        LogName = "System"
                        StartTime = $eventWindowStart
                    } -ErrorAction SilentlyContinue |
                        Where-Object {
                            $_.RecordId -gt $eventCursor["System"] -and
                            (
                                $_.ProviderName -match 'RtlWlanu|Kernel-PnP|UserPnp|USB|NDIS|Tcpip|WLAN' -or
                                $_.Message -match 'VID_2357|PID_0120|TP-Link Wireless USB Adapter'
                            )
                        } |
                        Sort-Object RecordId
                )
                foreach ($event in $systemEvents) {
                    $null = Write-EventRecord -Source "System" -Event $event
                    $eventCursor["System"] = [Math]::Max($eventCursor["System"], [long]$event.RecordId)
                }

                $profileEvents = @(
                    Get-WinEvent -FilterHashtable @{
                        LogName = $networkProfileLog
                        StartTime = $eventWindowStart
                    } -ErrorAction SilentlyContinue |
                        Where-Object RecordId -gt $eventCursor[$networkProfileLog] |
                        Sort-Object RecordId
                )
                foreach ($event in $profileEvents) {
                    $null = Write-EventRecord -Source "NetworkProfile" -Event $event
                    $eventCursor[$networkProfileLog] = [Math]::Max(
                        $eventCursor[$networkProfileLog],
                        [long]$event.RecordId
                    )
                }

                $lastEventProbeAt = Get-Date
            }
        }
        catch {
            Write-JsonLine -Writer $eventWriter -Value ([pscustomobject]@{
                captured_at = (Get-Date).ToString("o")
                source = "Monitor"
                event_time = (Get-Date).ToString("o")
                record_id = 0
                provider = "Monitor-UsbWifiContinuously"
                id = 500
                level = "Error"
                classification = "monitor_iteration_error"
                message = $_.Exception.ToString()
            })
        }

        $elapsedMilliseconds = ((Get-Date) - $iterationStarted).TotalMilliseconds
        $sleepMilliseconds = [Math]::Max(
            0,
            $SampleIntervalMilliseconds - [int]$elapsedMilliseconds
        )
        if ($sleepMilliseconds -gt 0) {
            Start-Sleep -Milliseconds $sleepMilliseconds
        }
    }
}
finally {
    Write-JsonLine -Writer $eventWriter -Value ([pscustomobject]@{
        captured_at = (Get-Date).ToString("o")
        source = "Monitor"
        event_time = (Get-Date).ToString("o")
        record_id = 0
        provider = "Monitor-UsbWifiContinuously"
        id = 3
        level = "Information"
        classification = "monitor_stopped"
        message = "Continuous Wi-Fi monitoring stopped."
    })
    $sampleWriter.Dispose()
    $eventWriter.Dispose()
}
