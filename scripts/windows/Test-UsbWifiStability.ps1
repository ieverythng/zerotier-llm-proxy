param(
    [string]$AdapterName = "WiFi",
    [string]$Gateway = "",
    [string]$InternetTarget = "1.1.1.1",
    [string]$HttpProbeUrl = "https://www.cloudflare.com/cdn-cgi/trace",
    [int]$HttpProbeIntervalSeconds = 5,
    [int]$DurationSeconds = 600,
    [long]$LoadBytes = 1GB,
    [int]$ChunkBytes = 50000000,
    [string]$DownloadUrl = "https://speed.cloudflare.com/__down?bytes={0}",
    [string]$OutDirectory = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

function Invoke-Ping {
    param([string]$Target)

    $ping = [Net.NetworkInformation.Ping]::new()
    try {
        $reply = $ping.Send($Target, 800)
        return [pscustomobject]@{
            ok = ($reply.Status -eq [Net.NetworkInformation.IPStatus]::Success)
            latency_ms = if ($reply.Status -eq [Net.NetworkInformation.IPStatus]::Success) {
                [int]$reply.RoundtripTime
            } else { $null }
        }
    }
    catch {
        return [pscustomobject]@{ ok = $false; latency_ms = $null }
    }
    finally {
        $ping.Dispose()
    }
}

function Get-WlanSnapshot {
    param([string]$Name)

    $text = netsh wlan show interfaces
    $block = ($text -join "`n")
    $signal = if ($block -match '(?m)^\s*Signal\s*:\s*(\d+)%') { [int]$Matches[1] } else { $null }
    $rxRate = if ($block -match '(?m)^\s*Receive rate \(Mbps\)\s*:\s*([\d.]+)') { [double]$Matches[1] } else { $null }
    $txRate = if ($block -match '(?m)^\s*Transmit rate \(Mbps\)\s*:\s*([\d.]+)') { [double]$Matches[1] } else { $null }
    $state = if ($block -match '(?m)^\s*State\s*:\s*(.+)$') { $Matches[1].Trim() } else { "" }
    return [pscustomobject]@{
        state = $state
        signal_pct = $signal
        receive_rate_mbps = $rxRate
        transmit_rate_mbps = $txRate
    }
}

function Invoke-HttpProbe {
    param([string]$Url)

    $handler = [Net.Http.HttpClientHandler]::new()
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(3)
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $response = $client.GetAsync($Url, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $stopwatch.Stop()
        return [pscustomobject]@{
            ok = $response.IsSuccessStatusCode
            latency_ms = [Math]::Round($stopwatch.Elapsed.TotalMilliseconds, 3)
            status_code = [int]$response.StatusCode
        }
    }
    catch {
        $stopwatch.Stop()
        return [pscustomobject]@{
            ok = $false
            latency_ms = [Math]::Round($stopwatch.Elapsed.TotalMilliseconds, 3)
            status_code = $null
        }
    }
    finally {
        if ($response) { $response.Dispose() }
        $client.Dispose()
        $handler.Dispose()
    }
}

if (-not $Gateway) {
    $route = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" |
        Where-Object InterfaceAlias -eq $AdapterName |
        Sort-Object RouteMetric |
        Select-Object -First 1
    if (-not $route) { throw "No IPv4 default route found for adapter '$AdapterName'." }
    $Gateway = [string]$route.NextHop
}

$adapter = Get-NetAdapter -Name $AdapterName -ErrorAction Stop
$instanceId = [string]$adapter.PnPDeviceID
if (-not $OutDirectory) {
    $OutDirectory = Join-Path $env:TEMP "usb-wifi-stability"
}
New-Item -ItemType Directory -Force -Path $OutDirectory | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$csvPath = Join-Path $OutDirectory "usb-wifi-stability-$stamp.csv"
$jsonPath = Join-Path $OutDirectory "usb-wifi-stability-$stamp.json"
$startedAt = Get-Date

$downloadJob = Start-Job -ArgumentList $DownloadUrl, $LoadBytes, $ChunkBytes -ScriptBlock {
    param($UrlTemplate, $TargetBytes, $BytesPerChunk)

    Add-Type -AssemblyName System.Net.Http
    $client = [Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(60)
    $downloaded = 0L
    $attempts = 0
    $failures = 0
    $elapsed = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($downloaded -lt $TargetBytes) {
            $attempts++
            try {
                $remaining = $TargetBytes - $downloaded
                $requestBytes = [int][Math]::Min($BytesPerChunk, $remaining)
                $url = $UrlTemplate -f $requestBytes
                $payload = $client.GetByteArrayAsync($url).GetAwaiter().GetResult()
                $downloaded += $payload.LongLength
            }
            catch {
                $failures++
                Start-Sleep -Seconds 1
            }
        }
    }
    finally {
        $elapsed.Stop()
        $client.Dispose()
    }

    [pscustomobject]@{
        attempts = $attempts
        failures = $failures
        bytes = $downloaded
        elapsed_s = [Math]::Round($elapsed.Elapsed.TotalSeconds, 3)
        average_mbps = if ($elapsed.Elapsed.TotalSeconds -gt 0) {
            [Math]::Round(($downloaded * 8 / 1MB) / $elapsed.Elapsed.TotalSeconds, 3)
        } else { 0 }
    }
}

$rows = [Collections.Generic.List[object]]::new()
$lastWlan = Get-WlanSnapshot -Name $AdapterName
$lastHttpProbe = [pscustomobject]@{ ok = $null; latency_ms = $null; status_code = $null }
$deadline = $startedAt.AddSeconds($DurationSeconds)
$sampleNumber = 0

try {
    while ((Get-Date) -lt $deadline) {
        $sampleStarted = Get-Date
        $sampleNumber++
        $currentAdapter = Get-NetAdapter -Name $AdapterName -ErrorAction SilentlyContinue
        $statistics = Get-NetAdapterStatistics -Name $AdapterName -ErrorAction SilentlyContinue
        $pnp = Get-PnpDevice -InstanceId $instanceId -ErrorAction SilentlyContinue
        if ($sampleNumber -eq 1 -or $sampleNumber % 10 -eq 0) {
            $lastWlan = Get-WlanSnapshot -Name $AdapterName
        }
        $gatewayPing = Invoke-Ping -Target $Gateway
        $internetPing = Invoke-Ping -Target $InternetTarget
        $isHttpProbeSample = ($sampleNumber -eq 1 -or $sampleNumber % $HttpProbeIntervalSeconds -eq 0)
        if ($isHttpProbeSample) {
            $lastHttpProbe = Invoke-HttpProbe -Url $HttpProbeUrl
        }
        $phase = if ($downloadJob.State -eq "Running") { "load" } else { "observation" }

        $rows.Add([pscustomobject]@{
            timestamp = $sampleStarted.ToString("o")
            elapsed_s = [Math]::Round(($sampleStarted - $startedAt).TotalSeconds, 3)
            phase = $phase
            adapter_present = ($null -ne $currentAdapter)
            adapter_status = if ($currentAdapter) { [string]$currentAdapter.Status } else { "Missing" }
            pnp_status = if ($pnp) { [string]$pnp.Status } else { "Missing" }
            wlan_state = $lastWlan.state
            signal_pct = $lastWlan.signal_pct
            receive_rate_mbps = $lastWlan.receive_rate_mbps
            transmit_rate_mbps = $lastWlan.transmit_rate_mbps
            gateway_ok = $gatewayPing.ok
            gateway_latency_ms = $gatewayPing.latency_ms
            internet_ok = $internetPing.ok
            internet_latency_ms = $internetPing.latency_ms
            http_probe_sample = $isHttpProbeSample
            http_ok = $lastHttpProbe.ok
            http_latency_ms = $lastHttpProbe.latency_ms
            http_status_code = $lastHttpProbe.status_code
            received_bytes = if ($statistics) { [long]$statistics.ReceivedBytes } else { $null }
            sent_bytes = if ($statistics) { [long]$statistics.SentBytes } else { $null }
            received_errors = if ($statistics) { [long]$statistics.ReceivedPacketErrors } else { $null }
            outbound_errors = if ($statistics) { [long]$statistics.OutboundPacketErrors } else { $null }
            received_discards = if ($statistics) { [long]$statistics.ReceivedDiscardedPackets } else { $null }
            outbound_discards = if ($statistics) { [long]$statistics.OutboundDiscardedPackets } else { $null }
        })

        $sleepMs = [Math]::Max(0, 1000 - [int]((Get-Date) - $sampleStarted).TotalMilliseconds)
        if ($sleepMs -gt 0) { Start-Sleep -Milliseconds $sleepMs }
    }
}
finally {
    if ($downloadJob.State -eq "Running") {
        Stop-Job -Job $downloadJob
    }
    $downloadResult = Receive-Job -Job $downloadJob -ErrorAction SilentlyContinue | Select-Object -Last 1
    Remove-Job -Job $downloadJob -Force -ErrorAction SilentlyContinue
}

$rows | Export-Csv -NoTypeInformation -LiteralPath $csvPath
$endedAt = Get-Date
$newSystemEvents = @(
    Get-WinEvent -FilterHashtable @{ LogName = "System"; StartTime = $startedAt } -ErrorAction SilentlyContinue |
        Where-Object { $_.ProviderName -eq "RtlWlanu" }
)
$newWlanFailures = @(
    Get-WinEvent -FilterHashtable @{
        LogName = "Microsoft-Windows-WLAN-AutoConfig/Operational"
        StartTime = $startedAt
        Id = 8002
    } -ErrorAction SilentlyContinue
)

$gatewayFailures = @($rows | Where-Object { -not $_.gateway_ok }).Count
$internetFailures = @($rows | Where-Object { -not $_.internet_ok }).Count
$httpProbeRows = @($rows | Where-Object { $_.http_probe_sample -eq $true })
$httpFailures = @($httpProbeRows | Where-Object { -not $_.http_ok }).Count
$missingSamples = @($rows | Where-Object { -not $_.adapter_present -or $_.pnp_status -ne "OK" }).Count
$downSamples = @($rows | Where-Object { $_.adapter_status -ne "Up" -or $_.wlan_state -ne "connected" }).Count
$gatewayLatencies = @($rows | Where-Object { $null -ne $_.gateway_latency_ms } | ForEach-Object { $_.gateway_latency_ms })
$internetLatencies = @($rows | Where-Object { $null -ne $_.internet_latency_ms } | ForEach-Object { $_.internet_latency_ms })

$summary = [pscustomobject]@{
    adapter_name = $AdapterName
    interface_description = [string]$adapter.InterfaceDescription
    pnp_instance_id = $instanceId
    driver_version = [string]$adapter.DriverVersion
    gateway = $Gateway
    internet_target = $InternetTarget
    started_at = $startedAt.ToString("o")
    ended_at = $endedAt.ToString("o")
    duration_s = [Math]::Round(($endedAt - $startedAt).TotalSeconds, 3)
    samples = $rows.Count
    gateway_failures = $gatewayFailures
    internet_failures = $internetFailures
    http_probes = $httpProbeRows.Count
    http_failures = $httpFailures
    missing_or_pnp_error_samples = $missingSamples
    disconnected_samples = $downSamples
    gateway_latency_avg_ms = if ($gatewayLatencies.Count) {
        [Math]::Round(($gatewayLatencies | Measure-Object -Average).Average, 3)
    } else { $null }
    gateway_latency_max_ms = if ($gatewayLatencies.Count) {
        ($gatewayLatencies | Measure-Object -Maximum).Maximum
    } else { $null }
    internet_latency_avg_ms = if ($internetLatencies.Count) {
        [Math]::Round(($internetLatencies | Measure-Object -Average).Average, 3)
    } else { $null }
    internet_latency_max_ms = if ($internetLatencies.Count) {
        ($internetLatencies | Measure-Object -Maximum).Maximum
    } else { $null }
    download = $downloadResult
    new_rtwlanu_events = $newSystemEvents.Count
    new_wlan_connection_failures = $newWlanFailures.Count
    csv_path = $csvPath
}

$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $jsonPath -Encoding UTF8
$summary | Format-List
Write-Host "Samples: $csvPath"
Write-Host "Summary: $jsonPath"

if ($missingSamples -gt 0 -or $downSamples -gt 0 -or $gatewayFailures -gt 0 -or $newSystemEvents.Count -gt 0) {
    exit 1
}
