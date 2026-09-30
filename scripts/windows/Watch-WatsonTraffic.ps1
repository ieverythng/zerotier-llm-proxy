[CmdletBinding()]
param(
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [int]$HeadroomPort = 8787,
    [ValidateRange(250, 60000)]
    [int]$IntervalMs = 1000,
    [ValidateRange(1, 100)]
    [int]$HistorySize = 10,
    [ValidateRange(0, 1000000)]
    [int]$MaxSamples = 0,
    [string]$WslDistribution = 'Ubuntu',
    [string]$LogPath = '',
    [switch]$Once,
    [switch]$Json,
    [switch]$NoClear,
    [switch]$NoWslResolution
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$taskHistory = New-Object Collections.Generic.List[object]
$lastTaskId = $null
$sampleCount = 0
$processLabelCache = @{}
$wslAddresses = @()
if (-not $NoWslResolution -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    try {
        $runningDistributions = @(& wsl.exe --list --running --quiet 2>$null)
        if ($runningDistributions -contains $WslDistribution) {
            $wslAddresses = @((& wsl.exe -d $WslDistribution -- hostname -I 2>$null) -split '\s+' | Where-Object { $_ })
        }
    } catch { }
}

function Get-ProcessLabel {
    param([int]$ProcessId)

    if (-not $ProcessId) { return 'remote' }
    if ($processLabelCache.ContainsKey($ProcessId)) { return $processLabelCache[$ProcessId] }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { return "pid:$ProcessId" }
    $commandLine = [string]$process.CommandLine
    $label = if ($commandLine -match '(?i)Watch-WatsonTraffic\.ps1') { 'Watson traffic monitor' }
        elseif ($commandLine -match '(?i)litellm') { 'LiteLLM' }
        elseif ($commandLine -match '(?i)headroom') { 'Headroom' }
        elseif ($commandLine -match '(?i)codex_watson_router') { 'Codex Watson router' }
        elseif ($commandLine -match '(?i)hermes') { 'Hermes' }
        else { "$($process.Name) ($ProcessId)" }
    $processLabelCache[$ProcessId] = $label
    return $label
}

function Get-GpuSnapshot {
    $nvidia = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $nvidia) { return $null }
    try {
        $line = & $nvidia.Source --query-gpu=utilization.gpu,memory.used,memory.total,power.draw --format=csv,noheader,nounits 2>$null |
            Select-Object -First 1
        if (-not $line) { return $null }
        $values = @($line -split ',' | ForEach-Object { $_.Trim() })
        return [ordered]@{
            utilization_percent = [int]$values[0]
            memory_used_mib = [int]$values[1]
            memory_total_mib = [int]$values[2]
            power_watts = [double]$values[3]
        }
    } catch {
        return $null
    }
}

function Get-LlamaSlotSnapshot {
    param([int]$Port)

    try {
        $slots = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/slots" -Method Get -TimeoutSec 2)
        if (-not $slots) { return $null }
        $slot = $slots[0]
        return [ordered]@{
            id = $slot.id
            context_tokens = $slot.n_ctx
            is_processing = [bool]$slot.is_processing
            task_id = $slot.id_task
            prompt_tokens = $slot.n_prompt_tokens
            prompt_tokens_processed = $slot.n_prompt_tokens_processed
            prompt_tokens_cached = $slot.n_prompt_tokens_cache
            max_output_tokens = $slot.params.max_tokens
            decoded_tokens = $slot.next_token.n_decoded
        }
    } catch {
        return $null
    }
}

function Get-ServiceSnapshot {
    param([hashtable[]]$Definitions, [object[]]$Connections)

    $listeners = @($Connections | Where-Object State -eq 'Listen')
    $services = foreach ($definition in $Definitions) {
        $listener = $listeners | Where-Object LocalPort -eq $definition.Port | Select-Object -First 1
        [ordered]@{
            name = $definition.Name
            port = $definition.Port
            listening = $null -ne $listener
            process_id = if ($listener) { $listener.OwningProcess } else { $null }
            process = if ($listener) { Get-ProcessLabel -ProcessId $listener.OwningProcess } else { $null }
        }
    }
    return @($services)
}

function Get-ActiveRoutes {
    param([hashtable[]]$Definitions, [object[]]$Connections)

    $connections = @($Connections | Where-Object State -eq 'Established')
    $routes = New-Object Collections.Generic.List[object]
    foreach ($definition in $Definitions) {
        foreach ($connection in @($connections | Where-Object RemotePort -eq $definition.Port)) {
            if ($connection.OwningProcess -eq $PID) { continue }
            $source = Get-ProcessLabel -ProcessId $connection.OwningProcess
            if ($source -eq 'Watson traffic monitor') { continue }
            $routes.Add([ordered]@{
                target = $definition.Name
                target_port = $definition.Port
                source = $source
                source_process_id = $connection.OwningProcess
                source_address = $connection.LocalAddress
                source_port = $connection.LocalPort
                kind = 'local-client'
            })
        }
        foreach ($connection in @($connections | Where-Object {
            $_.LocalPort -eq $definition.Port -and $_.RemoteAddress -notin @('127.0.0.1', '::1')
        })) {
            $source = "remote $($connection.RemoteAddress)"
            $sourceProcessId = $null
            if ($connection.RemoteAddress -in $wslAddresses) {
                try {
                    $socket = & wsl.exe -d $WslDistribution -- sh -lc `
                        "ss -Htnp state established '( sport = :$($connection.RemotePort) and dport = :$($definition.Port) )' 2>/dev/null" |
                        Select-Object -First 1
                    $owner = [regex]::Match([string]$socket, 'users:\(\(\"([^\"]+)\",pid=(\d+)')
                    if ($owner.Success) {
                        $source = "$($owner.Groups[1].Value) (WSL pid $($owner.Groups[2].Value))"
                        $sourceProcessId = [int]$owner.Groups[2].Value
                    } else {
                        $source = "WSL $($connection.RemoteAddress)"
                    }
                } catch {
                    $source = "WSL $($connection.RemoteAddress)"
                }
            }
            $routes.Add([ordered]@{
                target = $definition.Name
                target_port = $definition.Port
                source = $source
                source_process_id = $sourceProcessId
                source_address = $connection.RemoteAddress
                source_port = $connection.RemotePort
                kind = 'remote-client'
            })
        }
    }
    return $routes.ToArray()
}

function Get-WatsonSnapshot {
    $definitions = @(
        @{ Name = 'Headroom'; Port = $HeadroomPort },
        @{ Name = 'LiteLLM'; Port = $LiteLLMPort },
        @{ Name = 'llama.cpp'; Port = $LlamaPort }
    )
    $connections = @(Get-NetTCPConnection -ErrorAction SilentlyContinue)
    return [ordered]@{
        timestamp = (Get-Date).ToString('o')
        gpu = Get-GpuSnapshot
        llama_slot = Get-LlamaSlotSnapshot -Port $LlamaPort
        services = Get-ServiceSnapshot -Definitions $definitions -Connections $connections
        active_routes = @(Get-ActiveRoutes -Definitions $definitions -Connections $connections)
    }
}

function Write-WatsonDashboard {
    param($Snapshot, [object[]]$RecentTasks)

    if (-not $NoClear) { Clear-Host }
    Write-Host 'WATSON TRAFFIC MONITOR' -ForegroundColor Cyan
    Write-Host (Get-Date $Snapshot.timestamp -Format 'yyyy-MM-dd HH:mm:ss') -ForegroundColor DarkGray
    Write-Host ''

    if ($Snapshot.gpu) {
        $blocks = [Math]::Min(20, [Math]::Floor($Snapshot.gpu.utilization_percent / 5))
        $bar = ('#' * $blocks).PadRight(20, '.')
        Write-Host ("GPU  [{0}] {1,3}%   VRAM {2}/{3} MiB   {4:N1} W" -f `
            $bar, $Snapshot.gpu.utilization_percent, $Snapshot.gpu.memory_used_mib, `
            $Snapshot.gpu.memory_total_mib, $Snapshot.gpu.power_watts)
    } else {
        Write-Host 'GPU  unavailable' -ForegroundColor Yellow
    }

    $slot = $Snapshot.llama_slot
    if ($slot) {
        $state = if ($slot.is_processing) { 'GENERATING' } else { 'idle' }
        $color = if ($slot.is_processing) { 'Green' } else { 'DarkGray' }
        Write-Host ("Slot {0}: {1} | task {2} | prompt {3} tok | decoded {4} tok | context {5}" -f `
            $slot.id, $state, $slot.task_id, $slot.prompt_tokens, $slot.decoded_tokens, $slot.context_tokens) `
            -ForegroundColor $color
    } else {
        Write-Host 'llama.cpp slots endpoint unavailable' -ForegroundColor Red
    }

    Write-Host ''
    Write-Host 'SERVICES' -ForegroundColor Cyan
    foreach ($service in $Snapshot.services) {
        $state = if ($service.listening) { 'LISTENING' } else { 'DOWN' }
        $color = if ($service.listening) { 'Green' } else { 'Red' }
        Write-Host ("  {0,-10} :{1,-5} {2,-9} {3}" -f $service.name, $service.port, $state, $service.process) -ForegroundColor $color
    }

    Write-Host ''
    Write-Host 'ACTIVE CONNECTIONS' -ForegroundColor Cyan
    if ($Snapshot.active_routes.Count -eq 0) {
        Write-Host '  No active client connections.' -ForegroundColor DarkGray
    } else {
        foreach ($route in $Snapshot.active_routes) {
            Write-Host ("  {0} [{1}:{2}] -> {3}:{4}" -f `
                $route.source, $route.source_address, $route.source_port, $route.target, $route.target_port)
        }
    }
    if ($slot -and -not $slot.is_processing -and $Snapshot.active_routes.Count -gt 0) {
        Write-Host '  Slot is idle; listed sockets may be keep-alive or passive probes.' -ForegroundColor DarkGray
    }

    if ($RecentTasks.Count -gt 0) {
        Write-Host ''
        Write-Host 'RECENT LLAMA TASK CHANGES' -ForegroundColor Cyan
        foreach ($task in $RecentTasks) {
            Write-Host ("  {0} task={1} prompt={2} processing={3}" -f `
                $task.timestamp, $task.task_id, $task.prompt_tokens, $task.is_processing)
        }
    }

    Write-Host ''
    Write-Host 'Passive monitor: /slots + OS sockets only. Do not monitor LiteLLM with /health; it runs inference.' -ForegroundColor Yellow
    Write-Host 'Press Ctrl+C to stop.' -ForegroundColor DarkGray
}

do {
    $snapshot = Get-WatsonSnapshot
    if ($snapshot.llama_slot -and $snapshot.llama_slot.task_id -ne $lastTaskId) {
        if ($null -ne $lastTaskId) {
            $taskHistory.Add([ordered]@{
                timestamp = (Get-Date $snapshot.timestamp -Format 'HH:mm:ss')
                task_id = $snapshot.llama_slot.task_id
                prompt_tokens = $snapshot.llama_slot.prompt_tokens
                is_processing = $snapshot.llama_slot.is_processing
            })
            while ($taskHistory.Count -gt $HistorySize) { $taskHistory.RemoveAt(0) }
        }
        $lastTaskId = $snapshot.llama_slot.task_id
    }

    $jsonLine = $snapshot | ConvertTo-Json -Depth 8 -Compress
    if ($LogPath) {
        $logDirectory = Split-Path -Parent $LogPath
        if ($logDirectory -and -not (Test-Path -LiteralPath $logDirectory)) {
            New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        }
        [IO.File]::AppendAllText($LogPath, $jsonLine + [Environment]::NewLine, $utf8NoBom)
    }
    if ($Json) {
        Write-Output $jsonLine
    } else {
        Write-WatsonDashboard -Snapshot $snapshot -RecentTasks $taskHistory.ToArray()
    }
    $sampleCount++
    $finished = $Once -or ($MaxSamples -gt 0 -and $sampleCount -ge $MaxSamples)
    if (-not $finished) { Start-Sleep -Milliseconds $IntervalMs }
} while (-not $finished)
