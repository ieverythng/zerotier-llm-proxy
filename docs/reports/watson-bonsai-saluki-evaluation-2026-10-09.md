# Watson candidate check: Ternary Bonsai vs Underdog Saluki

Date: 2026-10-09
Scope: safe, short-run validation on the RTX 5070 Ti 16 GiB host. Qwen3.8 was
intentionally not loaded or queried in this cycle.

## Outcome

Bonsai is now the live Hermes candidate. It loaded at a 100,096-token context
allocation, passed an authenticated Headroom/LiteLLM SSE canary, and left about
4.7 GiB free VRAM. Qwen3.8 was not loaded or queried. This is a safe-fit and
route check, not a final quality/performance promotion: there was no matched
Saluki comparison or near-100k-token prompt, and no tool or vision suite was run.

The existing Bonsai evaluation remains relevant: Bonsai is much faster at
decode and has considerably more spare VRAM, but it failed one of four routed
agent workflows and had strict-JSON formatting failures. Saluki passed the
small end-to-end tool-call gate in this cycle and has a working multimodal
projection, but still needs a matched quality suite before it can be called
objectively better.

## Runtime configurations and evidence

| | Ternary Bonsai 27B Q2_0 | Underdog Saluki 27B IQ2-mix |
|---|---|---|
| Context allocation | 100,096; latest fit run used Q8_0 K/V | 100,096; Q8_0 K/V |
| Multimodal | No projector in the tested profile | F16 mmproj loaded with `--mmproj-offload` |
| GPU request | CUDA device fit; all-layer offload requested in the tested profile | `-ngl 99`, flash attention on; all-layer offload requested |
| VRAM | 11.0–11.1 GiB used / 4.6–4.7 GiB free in the latest Q8_0 run | 13.2–13.7 GiB used; minimum sampled free 2,344 MiB |
| Latest 100k-class full-prompt throughput | Not measured; 100,096 was allocated but only a short route canary was run | Not measured; allocation was 100,096, longest fresh prompt was 22,460 tokens |

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

## Bonsai live fit and stream canary (2026-10-09 evening)

The Saluki server was stopped only after the Prism build and host/GPU reserves
were checked. Bonsai was loaded from
`D:\MODELS\Ternary-Bonsai-27B-Q2_0.gguf` using the existing Prism fork build
at commit `7529fdaaf99ffdc5ca71ace9c7409a56b27ad92f`. The exact serving flags
were `-c 100096 -b 512 -ub 256 -ngl 99 -fa on -ctk q8_0 -ctv q8_0 -dio
--jinja --reasoning off -np 1 --api-key llama.cpp --metrics`, with alias
`bonsai27b`. The Prism build is required for this local group-128 Q2_0 file;
the standard b11433 binary is not interchangeable for this artifact.

- llama.cpp, LiteLLM, and authenticated Headroom each exposed only
  `bonsai27b`; `meta.n_ctx` was `100096`.
- Idle VRAM was 4,711 MiB free immediately after load and 4,671 MiB after the
  short stream canary. Host available RAM was 4.44 GiB after load and 4.21 GiB
  after the canary.
- A 20-token-capped streaming request returned HTTP 200, seven completion
  tokens spelling `BONSAI_ROUTE_OK` across deltas, and a final `[DONE]` event.
  The failed first client attempt was malformed JSON produced by Windows
  `curl.exe` argument quoting; it returned HTTP 400 before inference and caused
  no GPU activity. The same request passed through PowerShell's HTTP client.
- Hermes now defaults to `bonsai27b` at context 100,096 and uses Bonsai for
  local compression fallback. The Headroom project/user/token headers remain
  configured, and the WSL gateway was confirmed active. No Discord canary was
  sent.
- A cold WSL restart briefly reduced host available RAM to 0.79 GiB; it
  reclaimed to 2.69 GiB while the gateway remained active. This is a host/WSL
  startup transient, not evidence of model RAM growth. Do not run another cold
  load or full-context prefill until available-RAM margin is stable.
- After the gateway settled, three 5-second idle samples held at 4.27-4.29 GiB
  available RAM, 4,695-4,707 MiB free VRAM, 0% GPU utilization, and 1.02 GiB
  llama-server working set. All three model catalogs still listed only Bonsai.

`-ngl 99` requested all-layer GPU offload and the observed VRAM footprint is
consistent with the weights and Q8 cache resident on GPU, but the default
startup log did not retain an explicit `offloaded N/N layers` line. Full
weight residency is strongly indicated, not proven by a layer count. Bonsai's
vision projector is not currently downloaded or loaded; this runtime is
text-only. The context allocation is not a 100k-token prompt test, and no
throughput or long-prefill result is claimed from this canary.

The [current Prism model card](https://huggingface.co/prism-ml/Ternary-Bonsai-27B-gguf)
now labels the group-128 pack `PQ2_0` and recommends a group-64 `Q2_0` variant
for stock llama.cpp. This host's existing `Q2_0.gguf` is the older group-128
artifact, so it was intentionally run on the Prism fork. The [Prism KV-cache
guide](https://github.com/PrismML-Eng/Bonsai-demo/blob/main/KV-CACHE.md)
recommends a model-specific mean-centering bias with Q4_0; Q8_0 was chosen here
to preserve more cache precision without sacrificing the 2 GiB VRAM reserve.

## Safety and caveats

- The previously tested Saluki process stayed above the hard 2 GiB VRAM reserve
  in sampled checks. Its minimum, 2,344 MiB, is only about 344 MiB above that
  floor, so this is a narrow margin for concurrent Sunshine. Saluki is not the
  currently loaded model.
- Host available memory fluctuated substantially: it briefly sampled 0.8 GiB,
  later held at 4.05–4.08 GiB for three readings, then fell below 1 GiB during
  stricter launcher runs. Those runs stopped before proxy setup and left the
  existing model untouched. Read-only samples later rose through 1.59–2.52 GiB;
  the latest process snapshot had 12.7 GiB total process working set (ChatGPT
  group 2.32 GiB, WSL VM 1.52 GiB, llama.cpp 1.45 GiB). WSL itself reported
  6.9 GiB available. This is not a stable RAM pass; do not infer peak-context
  RAM safety from the short prompts.
- During the previous Saluki run its process reported about 1.47 GiB working
  set but about 15.05 GiB private/committed memory. These are different
  measurements; the latter is not equivalent to resident RAM. Keep it in the
  memory watcher and investigate if host commit or pagefile activity climbs.
- A short request in the previous Saluki run briefly sampled up to 96% GPU
  utilization. No sustained or concurrent load test was run; this does not
  prove a no-interference Sunshine profile.
- Hermes gateway is active with Bonsai configured. Hermes still warns that
  three stopped profiles share the default Discord bot credential. The active
  default profile is usable, but gateway multiplexing/ownership is not clean
  and was intentionally not migrated here.
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

## Runtime and stack versions

- Active Bonsai llama.cpp: Prism CUDA build at commit
  `7529fdaaf99ffdc5ca71ace9c7409a56b27ad92f`.
- Saluki launcher build: official `b11433` CUDA 13.4 Windows build, commit
  `50569eb87`; binary SHA-256
  `5E58114A496F64F21CBFED8E8E6F647C2810BDC052E87F73BC665C3C6F467208`.
- LiteLLM: 1.104.2.
- Headroom: 0.40.0, with non-loopback proxy authentication enabled.
- ZeroTier Windows service: 1.16.2.

Saluki source: [ConwayResearch/Underdog-Saluki-27B-1.0 model card](https://huggingface.co/ConwayResearch/Underdog-Saluki-27B-1.0).
Runtime source: [llama.cpp b11433 release](https://github.com/ggml-org/llama.cpp/releases/tag/b11433).
