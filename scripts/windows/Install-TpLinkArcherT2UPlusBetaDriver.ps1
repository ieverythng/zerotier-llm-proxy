[CmdletBinding()]
param(
    [string]$DriverRoot = "$env:LOCALAPPDATA\Codex\wifi-driver-packages\Archer_T2U_Plus_V1_20251231_beta\Archer_T2U_Plus_V1\T2U PLUS\Driver\Windows_11_64bit",
    [string]$AdapterInstanceId = "USB\VID_2357&PID_0120\00E04C000001",
    [string]$BackupRoot = "$env:LOCALAPPDATA\Codex\wifi-driver-backup-2026-08-04"
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window (Run as administrator)."
}

$infPath = Join-Path $DriverRoot "netrtwlanu.inf"
$catPath = Join-Path $DriverRoot "netrtwlanu.cat"
$sysPath = Join-Path $DriverRoot "rtwlanu.sys"
foreach ($path in @($infPath, $catPath, $sysPath)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required driver file is missing: $path"
    }
}

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$metadataPath = Join-Path $BackupRoot "preinstall-$stamp.json"
$classPath = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0001"

$before = Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue |
    Where-Object DeviceID -Like "$AdapterInstanceId*" |
    Select-Object DeviceID, DeviceName, DriverVersion, DriverDate, DriverProviderName, InfName, Manufacturer, IsSigned
$class = Get-ItemProperty -LiteralPath $classPath -ErrorAction SilentlyContinue |
    Select-Object DriverDesc, NetCfgInstanceId, InfPath, DriverVersion, UsbRxAggMode, UsbRxAggPageCount, UsbRxAggBlockCount, DynamicBatchEnable, TcpReorder, RxReorder, EnableUsbSS, InactivePs, bLeisurePs, @{'n'='WdiRscIPv4';'e'={$_.'*WdiRscIPv4'}}, @{'n'='WdiRscIPv6';'e'={$_.'*WdiRscIPv6'}}

[pscustomobject]@{
    timestamp = (Get-Date).ToString("o")
    adapter_instance_id = $AdapterInstanceId
    driver_root = $DriverRoot
    driver_inf = $infPath
    before_driver = $before
    before_class_values = $class
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $metadataPath -Encoding UTF8

$exportPath = Join-Path $BackupRoot "adapter-class-0001-preinstall-$stamp.reg"
& reg.exe export "HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0001" $exportPath /y | Out-Host

$catSignature = Get-AuthenticodeSignature -LiteralPath $catPath
if ($catSignature.Status -ne "Valid") {
    throw "The driver catalog signature is not valid: $($catSignature.Status)"
}

Write-Host "Adding TP-Link driver package: $infPath"
$installOutput = & pnputil.exe /add-driver $infPath /install 2>&1
$installOutput | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "pnputil failed with exit code $LASTEXITCODE. No driver switch was confirmed."
}

Write-Host "Restarting the adapter PnP device (no package deletion)."
& pnputil.exe /restart-device $AdapterInstanceId 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "pnputil /restart-device failed with exit code $LASTEXITCODE."
}

$deadline = (Get-Date).AddSeconds(30)
$after = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $after = Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue |
        Where-Object DeviceID -Like "$AdapterInstanceId*" |
        Select-Object DeviceID, DeviceName, DriverVersion, DriverDate, DriverProviderName, InfName, Manufacturer, IsSigned
    if ($after -and $after.DriverVersion -match "1030\.52\.1101\.2025") { break }
}

$after | Format-List | Out-Host
if (-not $after -or $after.DriverVersion -notmatch "1030\.52\.1101\.2025") {
    throw "The adapter did not report TP-Link driver 1030.52.1101.2025. Review $metadataPath and the pnputil output above before taking further action."
}

Write-Host "Verified TP-Link driver 1030.52.1101.2025 is active."
Write-Host "Backup metadata: $metadataPath"
Write-Host "Registry backup: $exportPath"
