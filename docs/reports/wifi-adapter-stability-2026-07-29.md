# TP-Link Archer T2U Plus stability test - 2026-07-29

## Hardware and driver

- Adapter: TP-Link Archer T2U Plus V1 / Realtek RTL8821AU
- Hardware ID: `USB\VID_2357&PID_0120`
- Driver: `rtwlanu.sys` / `1030.52.1216.2025`
- Driver provider: Realtek Semiconductor Corp.
- USB location: `Port_#0008.Hub_#0001`, USB 2.0 device

## Power-state audit

The earlier power-management changes are present:

- active plan: Ultimate Performance;
- Wi-Fi AC power mode: Maximum Performance;
- USB selective suspend on AC: disabled;
- driver `EnableUsbSS`: `0`;
- driver `InactivePs`: `0`;
- driver `bLeisurePs`: `0`.

The driver returns Windows Error 31 when queried through
`Get-NetAdapterPowerManagement`, so the registry and active power-plan values
are the authoritative observable settings for this driver.

## Bounded stress result

The first phase downloaded 1 GiB from Cloudflare:

- average application throughput: 204.366 Mbps;
- download failures: 0;
- adapter/PnP disappearance samples: 0;
- adapter-disconnected samples during the ten-minute monitor: 0;
- new `RtlWlanu` hardware-error events during the monitor: 0;
- network-interface error/discard counter deltas: 0.

The run was not a clean stability pass:

- gateway ICMP failures: 30/595;
- internet ICMP failures: 39/595;
- signal declined from 96-100% to 68-71%;
- negotiated link rate declined from 433.3 Mbps to 195 Mbps.

A three-minute HTTP-correlated follow-up recorded:

- gateway ICMP failures: 9/175;
- internet ICMP failures: 9/175;
- HTTPS failures: 2/36;
- no PnP disappearance or `RtlWlanu` hardware-error event.

After the follow-up, the 5 GHz connection stopped and Windows repeatedly logged
WLAN event 8002 with `The driver disconnected while associating`. The adapter
eventually recovered by connecting to the 2.4 GHz SSID:

- failed/retried SSID: `MOVISTAR_PLUS_3A60`, 802.11ac, channel 52;
- recovery SSID: `MOVISTAR_3A60`, 802.11n, channel 6.

## Conclusion

The test did not reproduce a USB selective-suspend power-off: the device
remained present and healthy in PnP throughout. It did reproduce a real 5 GHz
connectivity failure under/after sustained load, followed by repeated
driver-level association failures and automatic 2.4 GHz fallback.

For unattended remote access, keep the current 2.4 GHz fallback until the 5 GHz
path is separately stabilized. The most defensible next checks are:

1. configure the 5 GHz access point on a fixed non-DFS channel in the 36–48
   range rather than channel 52;
2. place the dongle on a short USB extension away from the PC chassis and other
   RF/thermal sources;
3. compare the exact TP-Link regional driver for the adapter's printed hardware
   revision against the installed Realtek beta driver;
4. run the preserved monitor for an eight-hour unattended window.

No power plan, driver, adapter, or Wi-Fi profile setting was changed by this
test.

## Later observation

At 15:42 Windows recorded a security stop/success pair for `MOVISTAR_3A60`, but
no accompanying event 8002 or 8003 driver disconnect. At the final 15:55 check,
the adapter was connected on channel 52 at 84% signal and 325 Mbps in both
directions. This recovery is encouraging, but it does not erase the earlier
repeated driver-association failures or the unattended-use risk.

## Artifacts

- `docs/artifacts/wifi-stability-2026-07-29/usb-wifi-stability-20260729_150044.csv`
- `docs/artifacts/wifi-stability-2026-07-29/usb-wifi-stability-20260729_150044.json`
- `docs/artifacts/wifi-stability-2026-07-29/usb-wifi-stability-20260729_151153.csv`
- `docs/artifacts/wifi-stability-2026-07-29/usb-wifi-stability-20260729_151153.json`
- `scripts/windows/Test-UsbWifiStability.ps1`

## External references

- TP-Link regional support page:
  <https://www.tp-link.com/pt/support/download/archer-t2u-plus/>
- Microsoft NDIS selective-suspend overview:
  <https://learn.microsoft.com/windows-hardware/drivers/network/ndis-selective-suspend>
- Microsoft USB selective-suspend overview:
  <https://learn.microsoft.com/windows-hardware/drivers/usbcon/usb-selective-suspend>
