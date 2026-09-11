param(
    [string]$AdapterName = "WiFi",
    [string]$InternetTarget = "1.1.1.1",
    [int]$ConcurrentStreams = 2,
    [int]$DurationSeconds = 90,
    [int]$RequestBytes = 25000000,
    [int]$FailureThreshold = 5,
    [string]$DownloadUrl = "https://speed.cloudflare.com/__down?bytes={0}",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"

if ($ConcurrentStreams -lt 1) { throw "ConcurrentStreams must be at least 1." }
if ($DurationSeconds -lt 10) { throw "DurationSeconds must be at least 10." }
if ($FailureThreshold -lt 2) { throw "FailureThreshold must be at least 2." }
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $env:LOCALAPPDATA "Codex\wifi-monitor\load-tests"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$samplesPath = Join-Path $OutputRoot "concurrent-load-$stamp.samples.jsonl"
$summaryPath = Join-Path $OutputRoot "concurrent-load-$stamp.summary.json"
$utf8 = [Text.UTF8Encoding]::new($false)
$writer = [IO.StreamWriter]::new($samplesPath, $true, $utf8)
$writer.AutoFlush = $true

function Invoke-PingSample {
    param([string]$Target)

    if (-not $Target) {
        return [pscustomobject]@{ ok = $false; latency_ms = $null; status = "no_target" }
    }

    $ping = [Net.NetworkInformation.Ping]::new()
    try {
        $reply = $ping.Send($Target, 700)
        return [pscustomobject]@{
            ok = $reply.Status -eq [Net.NetworkInformation.IPStatus]::Success
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

function Get-WlanValue {
    param(
        [string]$Block,
        [string]$Pattern
    )

    $match = [regex]::Match($Block, $Pattern)
    if ($match.Success) { return $match.Groups[1].Value.Trim() }
    return $null
}

$adapter = Get-NetAdapter -Name $AdapterName -ErrorAction Stop
$instanceId = [string]$adapter.PnPDeviceID
$route = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" -ErrorAction Stop |
    Where-Object InterfaceAlias -eq $AdapterName |
    Sort-Object RouteMetric |
    Select-Object -First 1
if (-not $route) { throw "No IPv4 default route found for '$AdapterName'." }
$gateway = [string]$route.NextHop

$jobs = @(
    for ($stream = 1; $stream -le $ConcurrentStreams; $stream++) {
        Start-Job -Name "wifi-load-$stream" -ArgumentList $stream, $DownloadUrl, $RequestBytes -ScriptBlock {
            param($StreamId, $UrlTemplate, $BytesPerRequest)

            Add-Type -AssemblyName System.Net.Http
            $client = [Net.Http.HttpClient]::new()
            $client.Timeout = [TimeSpan]::FromSeconds(30)
            $attempts = 0L
            $failures = 0L
            $bytes = 0L
            try {
                while ($true) {
                    $attempts++
                    try {
                        $url = $UrlTemplate -f $BytesPerRequest
                        $payload = $client.GetByteArrayAsync($url).GetAwaiter().GetResult()
                        $bytes += $payload.LongLength
                    }
                    catch {
                        $failures++
                        Start-Sleep -Milliseconds 250
                    }
                }
            }
            finally {
                $client.Dispose()
                [pscustomobject]@{
                    stream = $StreamId
                    attempts = $attempts
                    failures = $failures
                    bytes = $bytes
                }
            }
        }
    }
)

$startedAt = Get-Date
$deadline = $startedAt.AddSeconds($DurationSeconds)
$lastStatistics = Get-NetAdapterStatistics -Name $AdapterName -ErrorAction SilentlyContinue
$lastSampleAt = $startedAt
$consecutivePathFailures = 0
$stopReason = "duration_complete"
$samples = 0
$peakMbps = 0.0

try {
    while ((Get-Date) -lt $deadline) {
        $sampleAt = Get-Date
        $currentAdapter = Get-NetAdapter -Name $AdapterName -ErrorAction SilentlyContinue
        $pnp = Get-PnpDevice -InstanceId $instanceId -ErrorAction SilentlyContinue
        $statistics = Get-NetAdapterStatistics -Name $AdapterName -ErrorAction SilentlyContinue
        $processor = Get-CimInstance `
            -ClassName Win32_PerfFormattedData_PerfOS_Processor `
            -Filter "Name='_Total'" `
            -ErrorAction SilentlyContinue
        $wlanBlock = (netsh wlan show interfaces 2>&1) -join "`n"
        $gatewayPing = Invoke-PingSample -Target $gateway
        $internetPing = Invoke-PingSample -Target $InternetTarget
        $pathFailed = -not $gatewayPing.ok -and -not $internetPing.ok

        if ($pathFailed) {
            $consecutivePathFailures++
        }
        else {
            $consecutivePathFailures = 0
        }

        $intervalSeconds = [Math]::Max(0.001, ($sampleAt - $lastSampleAt).TotalSeconds)
        $receivedDelta = if ($statistics -and $lastStatistics) {
            [long]$statistics.ReceivedBytes - [long]$lastStatistics.ReceivedBytes
        } else {
            0L
        }
        if ($receivedDelta -lt 0 -or $intervalSeconds -lt 0.5) {
            $observedMbps = 0.0
        }
        else {
            $observedMbps = [Math]::Round(
                ($receivedDelta * 8 / 1MB) / $intervalSeconds,
                3
            )
        }
        $peakMbps = [Math]::Max($peakMbps, $observedMbps)

        $record = [pscustomobject]@{
            timestamp = $sampleAt.ToString("o")
            elapsed_s = [Math]::Round(($sampleAt - $startedAt).TotalSeconds, 3)
            concurrent_streams = $ConcurrentStreams
            adapter_present = $null -ne $currentAdapter
            adapter_status = if ($currentAdapter) { [string]$currentAdapter.Status } else { "Missing" }
            pnp_status = if ($pnp) { [string]$pnp.Status } else { "Missing" }
            wlan_state = Get-WlanValue -Block $wlanBlock -Pattern '(?m)^\s*State\s*:\s*(.+)$'
            ssid = Get-WlanValue -Block $wlanBlock -Pattern '(?m)^\s*SSID\s*:\s*(.+)$'
            bssid = Get-WlanValue -Block $wlanBlock -Pattern '(?m)^\s*BSSID\s*:\s*(.+)$'
            radio_type = Get-WlanValue -Block $wlanBlock -Pattern '(?m)^\s*Radio type\s*:\s*(.+)$'
            channel = Get-WlanValue -Block $wlanBlock -Pattern '(?m)^\s*Channel\s*:\s*(.+)$'
            signal_pct = Get-WlanValue -Block $wlanBlock -Pattern '(?m)^\s*Signal\s*:\s*(\d+)%'
            link_receive_mbps = Get-WlanValue -Block $wlanBlock -Pattern '(?m)^\s*Receive rate \(Mbps\)\s*:\s*([\d.]+)'
            observed_receive_mbps = $observedMbps
            gateway_ok = $gatewayPing.ok
            gateway_latency_ms = $gatewayPing.latency_ms
            gateway_status = $gatewayPing.status
            internet_ok = $internetPing.ok
            internet_latency_ms = $internetPing.latency_ms
            internet_status = $internetPing.status
            consecutive_path_failures = $consecutivePathFailures
            received_bytes = if ($statistics) { [long]$statistics.ReceivedBytes } else { $null }
            sent_bytes = if ($statistics) { [long]$statistics.SentBytes } else { $null }
            received_errors = if ($statistics) { [long]$statistics.ReceivedPacketErrors } else { $null }
            received_discards = if ($statistics) { [long]$statistics.ReceivedDiscardedPackets } else { $null }
            cpu_pct = if ($processor) { [int]$processor.PercentProcessorTime } else { $null }
            dpc_pct = if ($processor) { [int]$processor.PercentDPCTime } else { $null }
            interrupt_pct = if ($processor) { [int]$processor.PercentInterruptTime } else { $null }
            interrupts_per_s = if ($processor) { [long]$processor.InterruptsPersec } else { $null }
            dpcs_queued_per_s = if ($processor) { [long]$processor.DPCsQueuedPersec } else { $null }
            dpc_rate = if ($processor) { [long]$processor.DPCRate } else { $null }
        }

        $writer.WriteLine(($record | ConvertTo-Json -Compress))
        $samples++

        if ($consecutivePathFailures -ge $FailureThreshold) {
            $stopReason = "sustained_path_failure"
            break
        }

        $lastStatistics = $statistics
        $lastSampleAt = $sampleAt
        $sleepMilliseconds = [Math]::Max(
            0,
            1000 - [int]((Get-Date) - $sampleAt).TotalMilliseconds
        )
        if ($sleepMilliseconds -gt 0) {
            Start-Sleep -Milliseconds $sleepMilliseconds
        }
    }
}
finally {
    foreach ($job in $jobs) {
        if ($job.State -eq "Running") {
            Stop-Job -Job $job -ErrorAction SilentlyContinue
        }
    }
    $jobResults = @(
        foreach ($job in $jobs) {
            Receive-Job -Job $job -ErrorAction SilentlyContinue | Select-Object -Last 1
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        }
    )
    $writer.Dispose()
}

$endedAt = Get-Date
$summary = [pscustomobject]@{
    adapter_name = $AdapterName
    pnp_instance_id = $instanceId
    started_at = $startedAt.ToString("o")
    ended_at = $endedAt.ToString("o")
    duration_s = [Math]::Round(($endedAt - $startedAt).TotalSeconds, 3)
    concurrent_streams = $ConcurrentStreams
    request_bytes = $RequestBytes
    samples = $samples
    stop_reason = $stopReason
    peak_observed_receive_mbps = [Math]::Round($peakMbps, 3)
    worker_results = $jobResults
    samples_path = $samplesPath
}

[IO.File]::WriteAllText(
    $summaryPath,
    ($summary | ConvertTo-Json -Depth 6),
    $utf8
)

$summary | Format-List
Write-Host "Samples: $samplesPath"
Write-Host "Summary: $summaryPath"

if ($stopReason -eq "sustained_path_failure") {
    exit 2
}
