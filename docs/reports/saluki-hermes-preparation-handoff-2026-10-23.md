# Saluki / Hermes rehearsal preparation

Date: 2026-10-23

Status: preparation only; no model, Hermes, gateway, proxy, or WSL process was
started. No inference was run and no credential was read or recorded.

## Non-active preset

Use the existing Saluki launcher only after Juan explicitly says the machine is
ready. The existing IQ2-mix model, F16 projector, and CUDA server binary were
verified present. Proposed command for about 50k context:

```powershell
& 'C:\Users\Admin\PROJECTS\zerotier-llm-proxy\scripts\windows\Start-SalukiStack.ps1' `
  -ContextSize 50000 -BatchSize 512 -UBatchSize 128 `
  -GpuLayerCount 99 -KVCacheType q4_0 `
  -MinimumFreeVramMiB 2048 -MinimumAvailableRamGiB 3 `
  -MaximumGpuUtilizationPercent 92
```

The script rounds 50,000 up to 50,176 (256-token alignment). It requests all
GPU layers, Q4_0 K/V, flash attention, one slot, projector offload, and a
1,024-token image floor. It is a full stack activation, not just a model start:
it verifies Hermes through WSL, configures LiteLLM if needed, sets the Hermes
Saluki profile, and can restart `hermes-gateway` if the profile differs. Do
not run it until the user closes the relevant applications and explicitly says
ready.

## Paths and expected Hermes identity

Values below come from the checked-in launcher/profile scripts; live WSL state
was deliberately not inspected, so these are expected settings, not a claim
that the currently configured gateway has been verified.

- WSL distribution: `Ubuntu`
- Linux user/home: `juanbeck` / `/home/juanbeck`
- Hermes executable: `/home/juanbeck/.local/bin/hermes`
- Hermes Python interpreter: `/home/juanbeck/.hermes/hermes-agent/venv/bin/python3`
- Hermes config and env: `/home/juanbeck/.hermes/config.yaml` and
  `/home/juanbeck/.hermes/.env`
- User service checked by the launcher: `hermes-gateway`
- The inspected script edits the default Hermes config; it does not select a
  separately named Hermes profile.
- Expected provider/model: `watson-llama` / `saluki27b`
- Expected Headroom project/user headers: `watson-global` / `juanbeck`
  (token values intentionally omitted)

- Saluki model: `D:\MODELS\Underdog-Saluki-27B-1.0-IQ2-mix.gguf`
- Vision projector: `D:\MODELS\mmproj-Underdog-Saluki-27B-1.0-F16.gguf`
- Server binary: `C:\Users\Admin\PROJECTS\llama-b11433-win-cuda134\llama\llama-server.exe`

The F16 projector exists and is explicitly passed with `--mmproj-offload`;
previous Saluki tests loaded it and passed a basic image-read probe. Actual
projector layer residency, and image/tool quality at 50,176 context, are not
proven by this preparation.

## Resource gate and abort conditions

The launcher has stronger existing checks than Juan's minimum of 1 GiB free
host RAM and 1 GiB free VRAM after startup; none were weakened:

- Before model start: two healthy samples, at least 3 GiB available host RAM,
  GPU utilization no higher than 92%, and at least 14,721 MiB free VRAM. That
  VRAM gate is the configured 2,048 MiB post-load reserve plus the launcher's
  fixed 12,673 MiB allocation allowance.
- After model start: at least 2,048 MiB free VRAM and 2 GiB available host RAM;
  the launcher checks the reserve again after stack/profile setup.
- Abort on any missing model/projector/binary, absent proxy-token environment
  variable (without printing it), occupied/mismatched port 8080, failing model
  health/context, proxy route failure, Hermes config mismatch, or reserve loss.
  Model health/reserve failures stop the process spawned by this launcher. A
  later proxy/Hermes failure may leave that model process running; if that
  happens, inspect the exact PID/owner and logs before stopping only that
  launcher-owned model. Do not kill an unknown process or restart WSL/gateway
  as generic cleanup.

One read-only Windows snapshot found no listeners on ports 8080/4000/8787 and
no process named `llama-server`, `litellm`, or `headroom`. Available host RAM
was 1.88 GiB, free GPU memory 15,203 MiB, GPU utilization 1%, encoder 2%, and
decoder 0%. The VRAM reading was only 482 MiB above the launcher's pre-start
threshold, and host RAM was below the 3 GiB launch gate; do not start. Recheck
only after the user confirms readiness and applications are closed.

## Integration boundary

For later Cookbook/MCP work, consume this preset as an external, user-approved
launcher invocation with a readiness gate and captured health/resource result.
Do not copy Windows/WSL credentials, point a new component at raw llama.cpp,
or silently mutate Hermes/LiteLLM routing from Cookbook. Hermes remains the
agent gateway; the existing Headroom -> LiteLLM -> llama.cpp route and project/user
headers should remain the validated seam. No Cookbook checkout, interpreter,
or state path was supplied/inspected in this preparation.
