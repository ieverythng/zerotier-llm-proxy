# Watson candidate check: Ternary Bonsai vs Underdog Saluki

Date: 2026-10-09
Scope: safe, short-run validation on the RTX 5070 Ti 16 GiB host. Qwen3.8 was
intentionally not loaded or queried in this cycle.

## Outcome

Saluki is the current live Hermes candidate because it passed the current
text, tool-call, image, streaming, and Hermes CLI route checks. This is a
functional choice, not a final quality/performance promotion: the fresh Bonsai
and Saluki runs were not a matched benchmark, and a near-100k-token prefill
was deliberately not run while Sunshine and the desktop were in use.

The existing Bonsai evaluation remains relevant: Bonsai is much faster at
decode and has considerably more spare VRAM, but it failed one of four routed
agent workflows and had strict-JSON formatting failures. Saluki passed the
small end-to-end tool-call gate in this cycle and has a working multimodal
projection, but still needs a matched quality suite before it can be called
objectively better.

## Runtime configurations and evidence

| | Ternary Bonsai 27B Q2_0 | Underdog Saluki 27B IQ2-mix |
|---|---|---|
| Context allocation | 100,096; Q4_0 K/V in the fresh allocation run | 100,096; Q8_0 K/V |
| Multimodal | No projector in the tested profile | F16 mmproj loaded with `--mmproj-offload` |
| GPU request | CUDA device fit; all-layer offload requested in the tested profile | `-ngl 99`, flash attention on; all-layer offload requested |
| VRAM | about 9.76 GiB used / 6.2 GiB free at idle in the fresh run | 13.2–13.7 GiB used; minimum sampled free 2,344 MiB |
| Latest 100k-class full-prompt throughput | Not measured in the fresh Q4_0 allocation run | Not measured; allocation was 100,096, longest fresh prompt was 22,460 tokens |

For Saluki, `llama-server` reported a 100,096-token slot and loaded the F16
multimodal projector. The observed 22,460-token request prefetched at
1,410.9 tok/s and decoded 7 tokens at 49.45 tok/s. A separate 2,649-token
request measured 1,416.7 tok/s prefill and 61.22 tok/s decode over 30 output
tokens. The 748-token image request measured 940.4 tok/s prefill and
60.99 tok/s decode over 36 output tokens, and correctly read the supplied
image. These short samples are not full-context performance claims.

Through the production ZeroTier/Headroom/LiteLLM/llama.cpp route:

- Text completion returned the expected sentinel.
- A tool-call request returned a structured `get_model_context` tool call.
- SSE returned five content chunks followed by `[DONE]`; first content delta
  arrived in about 300 ms and the short request completed in about 2.1 s.
- A Hermes CLI one-shot returned the exact `SALUKI_ROUTE_OK` sentinel using
  the saved `saluki27b` profile. The Hermes default now advertises context
  100,096 and `supports_vision: true`; the existing project/user memory
  headers were preserved.

The Saluki launch requested `-ngl 99` and `--mmproj-offload`. The captured
startup log confirms the projector loaded but does not retain an explicit
`offloaded N/N layers` line. VRAM use and the llama.cpp working set are
consistent with the intended all-GPU configuration, but an exact layer-count
claim is not proven by the retained log.

## Safety and caveats

- The Saluki process remained above the hard 2 GiB VRAM reserve in all sampled
  checks. Its minimum, 2,344 MiB, is only about 344 MiB above that floor, so
  this is a narrow margin for a concurrent Sunshine workload. Current idle
  sample: 2,366 MiB free, 0% GPU utilization.
- Host available memory fluctuated substantially: it briefly sampled 0.8 GiB,
  later held at 4.05–4.08 GiB for three readings, then fell below 1 GiB during
  stricter launcher runs. Those runs stopped before proxy setup and left the
  existing model untouched. Read-only samples later rose through 1.59–2.52 GiB;
  the latest process snapshot had 12.7 GiB total process working set (ChatGPT
  group 2.32 GiB, WSL VM 1.52 GiB, llama.cpp 1.45 GiB). WSL itself reported
  6.9 GiB available. This is not a stable RAM pass; do not infer peak-context
  RAM safety from the short prompts.
- The Saluki process reports about 1.47 GiB working set but about 15.05 GiB
  private/committed memory. These are different measurements; the latter is
  not equivalent to resident RAM. Keep it in the memory watcher and investigate
  if host commit or pagefile activity climbs.
- A short model request briefly sampled up to 96% GPU utilization. No sustained
  or concurrent load test was run. This does not prove a no-interference
  Sunshine profile.
- Hermes gateway is active and was restarted to load the new model/auth config.
  Hermes still warns that three stopped profiles share the default Discord
  bot credential. The active default profile is usable, but gateway
  multiplexing/ownership is not clean and was intentionally not migrated here.
- `Start-WatsonStack.ps1` still defaults to the historical `qwen3.8` profile,
  and its external profile launcher does not pass the Saluki mmproj/direct-I/O
  settings. A dedicated [Saluki launcher](../../scripts/windows/Start-SalukiStack.ps1)
  now pins those flags, refuses to replace a mismatched listener, and enforces
  Hermes model/context, proxy-token presence, RAM/VRAM preflight and post-load
  gates. A pre-final-gate live reuse run configured/verified LiteLLM and
  Headroom without reloading the model. After the final RAM gate was added, the
  launcher correctly refused to declare readiness when RAM would not stay above
  the 2 GiB floor for two samples; it made no service changes. A cold load was
  deliberately not exercised while Sunshine/desktop use was in scope. Do not
  run `Start-WatsonStack.ps1` with defaults while Qwen is excluded.

The launcher and its tests are in the repository; `test_saluki_launcher.ps1`,
`test_watson_launcher.ps1`, `test_litellm_aliases.ps1`, and `git diff --check`
passed. The isolated Headroom auth test now refuses to start when production
Headroom is already bound, preventing shared-memory-store collisions. Its
isolated branch was not run beside the live service; production auth and the
Hermes request path were verified end-to-end instead.

## Prior Bonsai results (not a matched comparison)

The existing [2026-07-29 Bonsai evaluation](watson-bonsai-evaluation-2026-07-29.md)
records 1,369 tok/s prefill at about 42k, 70.9 tok/s mean forced decode, and
1,056.7 tok/s prefill at about 98.5k with FP16 KV. The 98.5k minimum free VRAM
was 1,906 MiB, below the current 2 GiB hard floor. Q4_0 KV raised VRAM headroom
substantially but reduced decode throughput and was not precision-equivalent.
That report's workflow result was 3/4 for Bonsai versus 4/4 for Watson.

Those historical measurements use a different date, runtime configuration,
and prompt suite. They are useful context, not a direct Saluki-vs-Bonsai win.

## Current stack versions

- llama.cpp: official `b11433` CUDA 13.4 Windows build, commit
  `50569eb87`; binary SHA-256
  `5E58114A496F64F21CBFED8E8E6F647C2810BDC052E87F73BC665C3C6F467208`.
- LiteLLM: 1.104.2.
- Headroom: 0.40.0, with non-loopback proxy authentication enabled.
- ZeroTier Windows service: 1.16.2.

Saluki source: [ConwayResearch/Underdog-Saluki-27B-1.0 model card](https://huggingface.co/ConwayResearch/Underdog-Saluki-27B-1.0).
Runtime source: [llama.cpp b11433 release](https://github.com/ggml-org/llama.cpp/releases/tag/b11433).
