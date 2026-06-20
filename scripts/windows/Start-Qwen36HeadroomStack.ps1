[CmdletBinding()]
param(
    [switch]$EnableOracle,
    [switch]$SkipLlamaStart,
    [switch]$RouteHermesThroughHeadroom,
    [int]$HeadroomPort = 8787,
    [switch]$ForceHeadroomCompression
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$stack = Join-Path $scriptDir "Start-Qwen36ZeroTierStack.ps1"

$params = @{
    EnableHeadroom = $true
}
if ($EnableOracle) { $params.EnableOracle = $true }
if ($SkipLlamaStart) { $params.SkipLlamaStart = $true }
if ($RouteHermesThroughHeadroom) { $params.RouteHermesThroughHeadroom = $true }
if ($HeadroomPort -ne 8787) { $params.HeadroomPort = $HeadroomPort }
if ($ForceHeadroomCompression) { $params.ForceHeadroomCompression = $true }

& $stack @params
