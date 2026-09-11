# Wi-Fi Desktop/driver audit — 2026-08-04

## Current verified state

- Adapter: TP-Link Archer T2U Plus V1 / Realtek RTL8811AU
- PnP ID: `USB\VID_2357&PID_0120\00E04C000001`
- Active driver: `oem23.inf`, TP-Link Technologies Co., Ltd., `1030.52.1101.2025` (2025-12-08)
- Driver catalog and `rtwlanu.sys` signatures: valid
- PnP status: `OK`; adapter is present but currently disconnected
- Parent root hub: `USB\ROOT_HUB30\5&23F8E3F5&0&0`, physical location `Port_#0004.Hub_#0002`
- Ethernet (`Ethernet 2`) is healthy through `192.168.0.1` and the Internet

The older Microsoft inbox driver (`1030.38.712.2019`) is no longer active. The newer
`1030.52.1216.2025` package is retained only in the prior backup; it was not reinstalled.
There is no second active Wi-Fi adapter. The extra `0001`/`0021` class entries are stale
historical interface records, not simultaneous bindings.

## Packages checked

- `C:\Users\Admin\Downloads\Archer_T2U Plus(EU)_V1_20250702_Win10_11.zip` is a legacy
  `190111` InstallShield bundle, not a current Windows 11 driver.
- `C:\Users\Admin\Downloads\mb_driver_597_chipset_5.11.02.217.zip` contains a motherboard
  chipset installer and was not run.
- TP-Link's December 2025 V1 package was already unpacked under
  `C:\Users\Admin\AppData\Local\Codex\wifi-driver-packages` and was used for the
  controlled driver switch.

## Desktop script findings

The Desktop scripts were audited but the aggressive ones were not run.

- `WiFi_COMPLETE_FIX.ps1`, `WiFi_FULL_FIX.ps1`, and `WiFi_FIX.ps1` contain malformed or
  incorrect registry paths in some branches, hard-coded gateway values, and repeated
  disable/enable cycles. They are not reliable verification tools.
- `wifi-watchdog.ps1`, `wifi-watchdog-v2.ps1`, and `wifi-preventive-reset.ps1` perform
  repeated adapter/service resets; the latter two do not parse in Windows PowerShell 5.
- `WiFi-Watchdog.bat` launched `AppData\Local\Temp\wifi-nuclear-watchdog.ps1`. Its log shows
  repeated adapter removal attempts and a hang on missing `Remove-PnpDevice`. That stale
  launcher was stopped and is not part of the current test.
- The repository monitor is passive and is the only recorder left running.

## Association checkpoint

The saved newest `Idelive_5G` WPA2/AES profile was added to the current interface without
printing its key. The SSID is visible on channel 44 at roughly 74%, but one manual
association attempt failed before authentication with WLAN event 8002 (“specific network
is not available”, RSSI 255). This is consistent with the adapter/driver association path;
it is not evidence of a password leak or a router rate limit.

## Next safe test

Enter the `Idelive_5G` password through Windows' Wi-Fi UI (do not paste it into logs or
chat). With the passive monitor running, retry once. If it associates, run the bounded
load harness before changing any hidden adapter setting. If it still fails at 74% signal,
the next one-variable test is the alternate USB controller/USB 2.0 port, not another
Desktop reset script.
