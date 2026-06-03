param(
    [string]$Workspace = ".",
    [string]$BaseUrl = "http://10.88.140.94:4000/v1",
    [string]$SessionCodexHome = (Join-Path $env:TEMP "codex-qwen36-home")
)

$ErrorActionPreference = "Stop"

$profileHome = & (Join-Path $PSScriptRoot "New-Qwen36CodexHome.ps1") -TargetCodexHome $SessionCodexHome -BaseUrl $BaseUrl
$env:CODEX_HOME = $profileHome

codex app $Workspace
