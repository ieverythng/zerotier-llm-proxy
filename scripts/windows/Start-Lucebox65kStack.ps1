param(
    [switch]$IncludeOracle,
    [switch]$NoSpecDecode,
    [switch]$AllowExperimentalLowVram
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $scriptDir "Start-LuceboxZeroTierStack.ps1"

$params = @{
    ContextSize = 65536
    Budget = 22
    FaWindow = 0
    KvFlash = "4096"
    PrefillCompression = "off"
    DraftResidency = "request-scoped"
}

if (-not $IncludeOracle) {
    $params.NoOracle = $true
}
if ($NoSpecDecode) {
    $params.NoSpecDecode = $true
}
if ($AllowExperimentalLowVram) {
    $params.AllowExperimentalLowVram = $true
}

& $launcher @params
