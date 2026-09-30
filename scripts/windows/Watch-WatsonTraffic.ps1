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
    [ValidateRange(0, 300)]
    [int]$FrameWidth = 0,
    [string]$WslDistribution = 'Ubuntu',
    [string]$LogPath = '',
    [switch]$Once,
    [switch]$Json,
    [switch]$NoClear,
    [switch]$NoWslResolution,
    [switch]$Demo
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$taskHistory = New-Object Collections.Generic.List[object]
$lastTaskId = $null
$sampleCount = 0
$processLabelCache = @{}
$wslAddresses = @()
if (-not $Demo -and -not $NoWslResolution -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
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

function Get-DemoSnapshot {
    return [ordered]@{
        timestamp = (Get-Date).ToString('o')
        gpu = [ordered]@{
            utilization_percent = 73
            memory_used_mib = 15742
            memory_total_mib = 16303
            power_watts = 238.4
        }
        llama_slot = [ordered]@{
            id = 0
            context_tokens = 100096
            is_processing = $true
            task_id = 4202
            prompt_tokens = 38124
            prompt_tokens_processed = 256
            prompt_tokens_cached = 37682
            max_output_tokens = -1
            decoded_tokens = 144
        }
        services = @(
            [ordered]@{ name = 'Headroom'; port = 8787; listening = $true; process_id = 120; process = 'Headroom' },
            [ordered]@{ name = 'LiteLLM'; port = 4000; listening = $true; process_id = 220; process = 'LiteLLM' },
            [ordered]@{ name = 'llama.cpp'; port = 8080; listening = $true; process_id = 320; process = 'llama-server.exe' }
        )
        active_routes = @(
            [ordered]@{ target = 'Headroom'; target_port = 8787; source = 'hermes (WSL pid 17876)'; source_address = '172.24.31.12'; source_port = 50123; kind = 'remote-client' },
            [ordered]@{ target = 'LiteLLM'; target_port = 4000; source = 'Headroom'; source_address = '127.0.0.1'; source_port = 50124; kind = 'local-client' },
            [ordered]@{ target = 'llama.cpp'; target_port = 8080; source = 'LiteLLM'; source_address = '127.0.0.1'; source_port = 50125; kind = 'local-client' }
        )
    }
}

function New-DashboardLine {
    param([string]$Text = '', [string]$Color = 'Default')
    return [pscustomobject]@{ Text = $Text; Color = $Color }
}

function Get-DashboardWidth {
    if ($FrameWidth -gt 0) { return [Math]::Max(48, [Math]::Min(300, $FrameWidth)) }
    $candidate = 100
    try {
        if ([Console]::WindowWidth -gt 0) { $candidate = [Console]::WindowWidth }
    } catch {
        try { $candidate = $Host.UI.RawUI.WindowSize.Width } catch { }
    }
    return [Math]::Max(48, [Math]::Min(160, $candidate))
}

function Get-CompactSourceLabel {
    param([string]$Source)
    return ($Source -replace ' \(WSL pid (\d+)\)', '[wsl:$1]')
}

function Get-DashboardLines {
    param($Snapshot, [object[]]$RecentTasks, [int]$Width)

    $lines = New-Object Collections.Generic.List[object]
    $slot = $Snapshot.llama_slot
    $slotState = if (-not $slot) { 'OFFLINE' } elseif ($slot.is_processing) { 'ACTIVE' } else { 'IDLE' }
    $slotColor = if (-not $slot) { 'Red' } elseif ($slot.is_processing) { 'Green' } else { 'Gray' }
    $timestamp = Get-Date $Snapshot.timestamp -Format 'HH:mm:ss'
    [void]$lines.Add((New-DashboardLine -Text ("WATSON TRAFFIC  {0}  [{1}]" -f $timestamp, $slotState) -Color 'Cyan'))
    [void]$lines.Add((New-DashboardLine -Text ('-' * [Math]::Min(72, $Width - 1)) -Color 'Gray'))

    if ($Snapshot.gpu) {
        $barWidth = [Math]::Max(8, [Math]::Min(16, $Width - 48))
        $filled = [Math]::Min($barWidth, [Math]::Floor($Snapshot.gpu.utilization_percent * $barWidth / 100))
        $bar = ('#' * $filled).PadRight($barWidth, '.')
        $usedGb = $Snapshot.gpu.memory_used_mib / 1024
        $totalGb = $Snapshot.gpu.memory_total_mib / 1024
        [void]$lines.Add((New-DashboardLine -Text ("GPU [{0}] {1,3}% | VRAM {2:N1}/{3:N1} GB | {4:N0} W" -f `
            $bar, $Snapshot.gpu.utilization_percent, $usedGb, $totalGb, $Snapshot.gpu.power_watts)))
    } else {
        [void]$lines.Add((New-DashboardLine -Text 'GPU unavailable' -Color 'Yellow'))
    }

    if ($slot) {
        if ($Width -lt 64) {
            [void]$lines.Add((New-DashboardLine -Text ("MODEL task {0} | {1}" -f $slot.task_id, $slotState) -Color $slotColor))
            [void]$lines.Add((New-DashboardLine -Text ("prompt {0:N0} | out {1:N0} | ctx {2:N0}" -f `
                $slot.prompt_tokens, $slot.decoded_tokens, $slot.context_tokens) -Color $slotColor))
        } else {
            [void]$lines.Add((New-DashboardLine -Text ("MODEL task {0} | prompt {1:N0} | out {2:N0} | ctx {3:N0}" -f `
                $slot.task_id, $slot.prompt_tokens, $slot.decoded_tokens, $slot.context_tokens) -Color $slotColor))
        }
        if ($slot.is_processing) {
            [void]$lines.Add((New-DashboardLine -Text ("CACHE {0:N0} reused | {1:N0} processed" -f `
                $slot.prompt_tokens_cached, $slot.prompt_tokens_processed) -Color 'Gray'))
        }
    } else {
        [void]$lines.Add((New-DashboardLine -Text 'MODEL llama.cpp slots unavailable' -Color 'Red'))
    }

    [void]$lines.Add((New-DashboardLine))
    [void]$lines.Add((New-DashboardLine -Text 'PIPELINE' -Color 'Cyan'))
    $serviceParts = foreach ($service in $Snapshot.services) {
        $state = if ($service.listening) { 'UP' } else { 'DOWN' }
        '{0}:{1} {2}' -f $service.name, $service.port, $state
    }
    $servicesColor = if (@($Snapshot.services | Where-Object { -not $_.listening }).Count -gt 0) { 'Red' } else { 'Green' }
    if ($Width -lt 64) {
        foreach ($servicePart in $serviceParts) {
            [void]$lines.Add((New-DashboardLine -Text $servicePart -Color $servicesColor))
        }
    } else {
        [void]$lines.Add((New-DashboardLine -Text ($serviceParts -join ' | ') -Color $servicesColor))
    }

    if ($Snapshot.active_routes.Count -eq 0) {
        [void]$lines.Add((New-DashboardLine -Text 'No active request path.' -Color 'Gray'))
    } else {
        foreach ($route in $Snapshot.active_routes) {
            $source = Get-CompactSourceLabel -Source $route.source
            $address = if ($Width -ge 64 -and $route.kind -eq 'remote-client') { " [$($route.source_address)]" } else { '' }
            [void]$lines.Add((New-DashboardLine -Text ("  {0}{1} -> {2}:{3}" -f `
                $source, $address, $route.target, $route.target_port)))
        }
    }
    if ($slot -and -not $slot.is_processing -and $Snapshot.active_routes.Count -gt 0) {
        [void]$lines.Add((New-DashboardLine -Text 'Slot idle: connections are keep-alive or passive probes.' -Color 'Gray'))
    }

    [void]$lines.Add((New-DashboardLine))
    [void]$lines.Add((New-DashboardLine -Text 'RECENT TASKS' -Color 'Cyan'))
    $shownTasks = @($RecentTasks | Select-Object -Last 4)
    if ($shownTasks.Count -eq 0) {
        [void]$lines.Add((New-DashboardLine -Text 'No task changes since monitor start.' -Color 'Gray'))
    } else {
        foreach ($task in $shownTasks) {
            $state = if ($task.is_processing) { 'ACTIVE' } else { 'IDLE' }
            [void]$lines.Add((New-DashboardLine -Text ("{0}  task {1}  prompt {2:N0}  {3}" -f `
                $task.timestamp, $task.task_id, $task.prompt_tokens, $state)))
        }
    }

    [void]$lines.Add((New-DashboardLine))
    $passiveNote = if ($Width -lt 64) {
        'Passive monitor; LiteLLM /health is avoided.'
    } else {
        'Passive: /slots + TCP. LiteLLM /health is never called.'
    }
    [void]$lines.Add((New-DashboardLine -Text $passiveNote -Color 'Yellow'))
    [void]$lines.Add((New-DashboardLine -Text 'Ctrl+C to stop.' -Color 'Gray'))
    return $lines.ToArray()
}

function Limit-DashboardText {
    param([string]$Text, [int]$Width)
    $singleLine = ([string]$Text) -replace '[\r\n]+', ' '
    if ($singleLine.Length -le $Width) { return $singleLine }
    return $singleLine.Substring(0, [Math]::Max(0, $Width - 1)) + '~'
}

function Get-AnsiColorCode {
    param([string]$Color)
    switch ($Color) {
        'Cyan' { return '96' }
        'Green' { return '92' }
        'Yellow' { return '93' }
        'Red' { return '91' }
        'Gray' { return '90' }
        default { return '0' }
    }
}

function Write-DashboardFrame {
    param([object[]]$Lines, [int]$Width)

    $contentWidth = [Math]::Max(47, $Width - 1)
    $plainLines = @($Lines | ForEach-Object { Limit-DashboardText -Text $_.Text -Width $contentWidth })
    if ($NoClear -or [Console]::IsOutputRedirected) {
        Write-Output ($plainLines -join [Environment]::NewLine)
        return
    }

    $escape = [char]27
    $rendered = for ($index = 0; $index -lt $Lines.Count; $index++) {
        $code = Get-AnsiColorCode -Color $Lines[$index].Color
        $text = $plainLines[$index].PadRight($contentWidth)
        "$escape[$($code)m$text$escape[0m"
    }
    $frame = "$escape[?25l$escape[2J$escape[H" + ($rendered -join "`r`n") + "$escape[?25h"
    [Console]::Write($frame)
}

if ($Demo) {
    [void]$taskHistory.Add([ordered]@{ timestamp = '11:18:04'; task_id = 4200; prompt_tokens = 36214; is_processing = $true })
    [void]$taskHistory.Add([ordered]@{ timestamp = '11:18:31'; task_id = 4201; prompt_tokens = 37482; is_processing = $true })
    $lastTaskId = 4201
}

do {
    $snapshot = if ($Demo) { Get-DemoSnapshot } else { Get-WatsonSnapshot }
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
        $width = Get-DashboardWidth
        $lines = Get-DashboardLines -Snapshot $snapshot -RecentTasks $taskHistory.ToArray() -Width $width
        Write-DashboardFrame -Lines $lines -Width $width
    }
    $sampleCount++
    $finished = $Once -or ($MaxSamples -gt 0 -and $sampleCount -ge $MaxSamples)
    if (-not $finished) { Start-Sleep -Milliseconds $IntervalMs }
} while (-not $finished)
