[CmdletBinding()]
param(
    [string]$LlamaBaseUrl = "http://127.0.0.1:8080/v1",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ServedAlias = "qwen3.8",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$CanonicalModel = "qwen3.8",
    [string]$HermesBaseUrl = "http://172.24.16.1:8787/v1",
    [string]$FallbackBaseUrl = "http://172.24.16.1:4000/v1",
    [ValidateRange(0.5, 0.95)]
    [double]$CompressionThreshold = 0.80,
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$hermes = "/home/juanbeck/.local/bin/hermes"

function Invoke-Hermes {
    param([string[]]$Arguments)

    & wsl.exe -d Ubuntu -- $hermes @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Invoke-HermesJsonSetting {
    param(
        [string]$Key,
        [string]$Value
    )

    # wsl.exe strips JSON quote characters when they are forwarded as a
    # direct argv element. Decode inside WSL and invoke Hermes with Python so
    # the structured value reaches the CLI as exactly one argument.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
    $pythonCode = 'import base64,subprocess,sys; value=base64.b64decode(sys.argv[1]).decode(); subprocess.run(sys.argv[2:]+[value],check=True)'
    $bashCommand = "python3 -c '$pythonCode' $encoded /home/juanbeck/.local/bin/hermes config set --force $Key"
    & wsl.exe -d Ubuntu -- bash -lc $bashCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes JSON setting failed with exit code ${LASTEXITCODE}: $Key"
    }
}

function Remove-HermesKeyIfPresent {
    param([string]$Key)

    & wsl.exe -d Ubuntu -- $hermes config unset $Key
    # Hermes returns 1 when the key is already absent; that is the desired
    # idempotent outcome for a synchronizer.
    if ($LASTEXITCODE -gt 1) {
        throw "Hermes key removal failed with exit code ${LASTEXITCODE}: $Key"
    }
}

function Get-ModelEntries {
    param($Payload)

    $entries = @()
    if ($Payload.data) { $entries += @($Payload.data) }
    if ($Payload.models) { $entries += @($Payload.models) }
    return $entries
}

$models = Invoke-RestMethod -Uri "$($LlamaBaseUrl.TrimEnd('/'))/models" -Method Get -TimeoutSec 10
$entry = Get-ModelEntries $models |
    Where-Object { $_.id -eq $ServedAlias -or $_.name -eq $ServedAlias -or $_.model -eq $ServedAlias } |
    Select-Object -First 1
if (-not $entry) {
    throw "llama.cpp did not expose served alias '$ServedAlias' at $LlamaBaseUrl/models."
}

$context = 0
if ($entry.meta -and $entry.meta.n_ctx) { $context = [int]$entry.meta.n_ctx }
if ($context -lt 8192) {
    throw "llama.cpp metadata did not contain a usable meta.n_ctx for '$ServedAlias'."
}

# Hermes computes the ratio against (context_length - max_tokens), then floors
# small windows at 65,536 tokens. At the current 100,096/16,384 allocation,
# 0.80 therefore triggers at ~66,969 tokens: approximately 33% remains in the
# full window, which is the requested behavior.
$fallbackChain = '[{"provider":"custom","model":"' + $CanonicalModel + '","base_url":"' + $FallbackBaseUrl.TrimEnd('/') + '","api_mode":"chat_completions","timeout":300}]'
$settings = @(
    @("model.context_length", [string]$context),
    @("model.default", $CanonicalModel),
    @("model.provider", "watson-llama"),
    @("model.base_url", $HermesBaseUrl.TrimEnd('/')),
    @("providers.watson-llama.api", $HermesBaseUrl.TrimEnd('/')),
    @("providers.watson-llama.default_model", $CanonicalModel),
    # Summaries must not acquire Headroom memory tools or trigger an agent
    # tool round-trip: the compressor expects a plain-text response.
    @("auxiliary.compression.provider", "custom"),
    @("auxiliary.compression.model", $CanonicalModel),
    @("auxiliary.compression.base_url", $FallbackBaseUrl.TrimEnd('/')),
    @("auxiliary.compression.fallback_chain", $fallbackChain),
    @("compression.threshold", $CompressionThreshold.ToString("0.##", [Globalization.CultureInfo]::InvariantCulture)),
    @("streaming.enabled", "true"),
    @("display.platforms.discord.streaming", "true"),
    @("display.platforms.discord.runtime_footer.enabled", "true"),
    @("display.platforms.discord.runtime_footer.fields", '["model", "context_pct", "latency"]')
)

foreach ($setting in $settings) {
    if ($setting[0] -eq "auxiliary.compression.fallback_chain") {
        # This optional per-task key is intentionally extensible in Hermes;
        # --force suppresses the advisory for versions that omit it from the
        # static schema even though the runtime consumes it.
        Invoke-HermesJsonSetting -Key $setting[0] -Value $setting[1]
    } else {
        Invoke-Hermes -Arguments @("config", "set", $setting[0], $setting[1])
    }
}
# Remove the pre-existing unsupported key if an older config had it. The
# supported fallback_chain above is the only local-compression fallback now.
Remove-HermesKeyIfPresent -Key "auxiliary.compression.fallback_model"

Invoke-Hermes -Arguments @("config", "check")
if (-not $NoRestart) {
    Invoke-Hermes -Arguments @("gateway", "restart")
    & wsl.exe -d Ubuntu -- timeout 15 systemctl --user is-active hermes-gateway
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes gateway did not become active after metadata synchronization."
    }
}

[pscustomobject]@{
    canonical_model = $CanonicalModel
    served_alias = $ServedAlias
    llama_context_window = $context
    compression_threshold = $CompressionThreshold
    discord_streaming = $true
    discord_runtime_footer = @("model", "context_pct", "latency")
} | ConvertTo-Json -Compress
