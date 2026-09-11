# CUDA runtime unification (2026-09-04)

## Decision

Active Windows launchers now converge on the verified shared CUDA 13.3 bundle:

`C:\Users\Admin\PROJECTS\llama-b10621-win-cuda133`

The user-scoped `LLAMA_CPP_BIN_DIR` environment variable is set to the same
directory. Legacy TurboQuant/Bonsai build trees are retained as explicit
experiment inputs; they are no longer a default for Watson, Hermes, iTRADER,
or the KV-cache sweep.

## Changes

- Updated `llama-cpp-server` script and profile defaults.
- Updated iTRADER foreground/background launchers and `.cmd` wrappers.
- Updated the Qwen KV-cache sweep to accept `-SourceBinDir`, defaulting to the
  shared bundle.
- Added fail-closed stale-listener handling to the background llama launcher,
  Watson launcher, Headroom launcher, and iTRADER background launcher.
- Added a five-second `nvidia-smi` preflight to CUDA launchers so a wedged
  driver cannot be mistaken for a model-loading delay.
- A startup failure now terminates the child process tree when possible and
  reports an occupied unhealthy port instead of leaving a hidden 503 service.
- Watson startup now verifies WSL can reach Headroom before declaring Hermes/
  Discord ready.

## Verification

- PowerShell parsing passed for all touched launchers.
- `tests/test_watson_launcher.ps1` passed.
- `tests/test_cuda_path_unification.ps1` passed.
- The live launcher was exercised against the wedged 8080 listener and failed
  in ~7 seconds with the actionable message that the unhealthy listener could
  not be stopped; it did not launch LiteLLM/Headroom on a broken model server.
- Fresh profile smoke tests on isolated ports `18082` (llama-cpp-server) and
  `18083` (iTRADER background launcher) both failed in ~10 seconds on the
  driver preflight and left no child listener behind.
- The iTRADER Windows environment healthcheck passes with Python 3.11.13 and
  its required packages. Its WSL-oriented `run_repo_python.sh` wrapper is not
  usable from PowerShell because it tries to execute the Windows venv binary
  through `/mnt/c`; the native Windows venv is the valid check on this host.
- Revalidation at 19:28 CEST still finds PID `17012` bound to `0.0.0.0:8080`,
  with a five-second timeout on `/health`; ports `4000` and `8787` refuse
  connections while the WSL Hermes gateway remains active.
- Hermes configuration confirms two distinct routes: the default `qwen3.8`
  provider uses Headroom at `172.24.16.1:8787/v1`, while the Discord session's
  custom provider uses LiteLLM at `172.24.16.1:4000/v1`. A direct Hermes CLI
  success can therefore coexist with a broken gateway route if it used the
  direct llama endpoint or ran while the proxies were healthy; it does not
  prove that the current Headroom/LiteLLM chain is reachable.

## Current blocker

The existing `llama-server` process on port 8080 is a wedged CUDA process: its
HTTP health endpoint returns 503, `nvidia-smi` does not return, and Windows
cannot terminate the process tree. A reboot or GPU reset is required before a
fresh runtime smoke test. No build directories were deleted during this pass.

Windows also recorded a `nvlddmkm` 153 **BusReset TDR** at 14:45:32 and a
WHEA-Logger 18 processor cache-hierarchy error immediately before the reboot;
those are hardware/driver stability signals, not a Discord transport error.

The canonical bundle is about 668 MB. The approximately 641 MB TurboQuant,
394 MB Bonsai, 353 MB custom Qwen38, 95 MB Vulkan, and 1.8 GB BeeLlama trees
remain available as named experiment/compatibility inputs; no destructive
deletion was performed while the GPU is wedged.

## Recovery command

After the machine has been rebooted (or the GPU has been reset), run from the
proxy repo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Start-WatsonStack.ps1 `
  -Model qwen3.8 -ContextSize 100096 -RouteHermesThroughHeadroom
```

The launcher will refuse to continue unless the CUDA preflight, llama health,
LiteLLM, Headroom, and the WSL-to-Headroom route all pass.

The post-recovery benchmark order and promotion gates are recorded in
`docs/plans/qwen38-autoresearch-resume-2026-09-04.md`.

## Remote restart readiness

Per-user Startup shortcuts now launch:

- `Orca.lnk` → `C:\Users\Admin\AppData\Local\Programs\orca\Orca.exe`
- `ChatGPT.lnk` → the installed `OpenAI.Codex` packaged app via
  `shell:AppsFolder`

They run after the `Admin` user signs in. Windows auto-login was not enabled.

## Elevated recovery sequence

Open **PowerShell as Administrator** through Orca and try the process-level
kill first:

```powershell
$targetPid = 17012
taskkill.exe /PID $targetPid /T /F
Start-Sleep -Seconds 2
Get-Process -Id $targetPid -ErrorAction SilentlyContinue
Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue
```

If the process/listener remains, restart only the NVIDIA display device (the
screen may blink briefly):

```powershell
$gpuId = (Get-PnpDevice -Class Display |
  Where-Object FriendlyName -like 'NVIDIA*' |
  Select-Object -First 1 -ExpandProperty InstanceId)
$gpuId
pnputil.exe /restart-device "$gpuId"
Start-Sleep -Seconds 10
nvidia-smi.exe
```

If `nvidia-smi` still hangs or the listener survives, use an explicit reboot:

```powershell
shutdown.exe /r /t 0
```

Do not run the reboot line until remote access is confirmed and the restart is
intentional.

On 2026-09-04 the non-elevated automation session successfully submitted a
restart request (System event 1074), but the boot timestamp did not change and
Windows subsequently returned `A system shutdown is in progress` for further
requests. An elevated interactive console is therefore required to terminate
the stuck process/device and complete the restart.

## Post-reboot validation (2026-09-05)

The machine subsequently rebooted successfully at `2026-09-05 16:00:56` CEST.
The NVIDIA driver is healthy (`616.56`), and the previous launcher failure was
traced to a PowerShell `Start-Process` edge case: `WaitForExit()` completed but
the `ExitCode` property was still `$null` even though `nvidia-smi` had returned
valid output. The root Watson, llama-cpp-server, and iTRADER foreground
preflights now refresh the process and treat a null exit code as success when
the output is non-empty. All launcher AST checks and the CUDA-path/launcher
tests pass.

The canonical stack is running and has remained healthy after long-context
load:

- llama.cpp: `0.0.0.0:8080`, alias `qwen3.8`, `n_ctx=100096`;
- LiteLLM: `0.0.0.0:4000`, four healthy qwen3.8 endpoints;
- Headroom 0.37.0: `0.0.0.0:8787`, global SQLite memory enabled and ready;
- Hermes gateway: active, default route `172.24.16.1:8787/v1`, Discord custom
  route `172.24.16.1:4000/v1`;
- ZeroTier address: `10.88.140.94`, with all three health endpoints reachable;
- post-load OpenAI contract probe through Headroom: all six cases passed;
- Hermes workflow gate at approximately 12k synthetic history tokens: all four
  cases passed (exact response, tool JSON, retention, continuation).
- real Ubuntu/WSL chat canary through `172.24.16.1:8787/v1` returned the exact
  marker `WSL_HERMES_ROUTE_OK` with the Hermes project/user headers.
- a disposable CCR compression produced hash `b703b7700b7a22fa75ead7b0`, and
  `/v1/retrieve` returned the original marker-bearing content (1760 tokens),
  proving `headroom_retrieve` storage/retrieval end to end.
- `hermes status --all` reports the `watson-llama` qwen3.8 provider, Discord
  configured, the gateway active with PID 2359, and 10 active sessions;
  `hermes doctor` confirms the Discord tool and built-in memory provider.

The global Headroom database survives the reboot and currently contains five
USER-scope Watson memories, including the selected IQ3_S model and context
length 100096. A disposable CCR entry is now present from the live retrieval
proof below; it is TTL-bound and not part of the durable memory set.

Hermes did briefly restart several times between boot and stack readiness; the
journal attributes those exits to provider timeouts while port 8080 was still
down. Since the final restart at 16:09:49 CEST it has stayed active. The only
later Discord warning is a slash-command sync timeout caused by a Discord
rate-limit bucket; it is scheduled for retry on the next reconnect and is
separate from the now-healthy model route.

The first bounded performance controls are recorded under
`_tmp/bench/autoresearch-prefill-loop/`. Direct llama.cpp controls completed at
16k/32k/65k/98k/100096 requested tokens with minimum free VRAM of 688 MiB or
more. The exact 100096 launch limit tokenized to 98608 tokens and completed in
122.4 s at 808.3 prompt tok/s and 26.9 decode tok/s. This validates 100k as an
operational context on the current profile; it is not yet a claim that 100k is
the latency-optimal setting.

The direct LiteLLM control measured approximately 3,492 prompt tok/s on a
4.5k-token request. The uncached Headroom arm measured approximately 776 prompt
tok/s on a 6.4k-token request because Headroom adds memory/tool context and
compression work; a repeat with the identical body was a semantic-cache hit.
With the ONNX compressor fully ready, a fresh 6.5k-token Headroom request
measured 815 prompt tok/s versus 1,407 tok/s through LiteLLM. This remains the
next optimization target, while persistent memory stays enabled.

An isolated no-cache ablation confirmed the source of much of that overhead:
disabling automatic memory-tool schemas reduced the first request from 22.64 s
to 2.88 s and the injected prompt from roughly 7.0k to 4.6k tokens. The
challenger was rejected because it removes `memory_save`/`memory_search`; it was
torn down without touching the production 8787 route. The next optimization
must preserve those tools while making their injection stable and cheaper.
A `--memory-top-k 3` candidate was also tested with no-cache; it produced the
same ~7.0k/~6.5k input sizes and 20.85 s/7.92 s latency pair, so reducing the
number of recalled facts does not address the dominant overhead.

The iTRADER environment check also needs an explicit interpreter: bare
`python` resolves to `C:\Users\Admin\itrader`, whose NumPy 2.3.5 is ABI-incompatible
with its pandas/pyarrow wheels. The repository's `.venv\Scripts\python.exe`
matches the pinned NumPy 1.26.4/pandas 2.1.4/pyarrow 13.0.0 set and passes all
required-package checks. That venv currently contains CPU-only Torch and no
optional `llama_cpp`/Transformers package; this does not change the shared
llama.cpp CUDA server path, but callers should use the repo wrapper (or the
explicit `.venv` interpreter) rather than the global `python` shim.

No `nvlddmkm`, WHEA-Logger, or display-driver 4101 events have been recorded
since the successful reboot, including during the 100k-context load.
