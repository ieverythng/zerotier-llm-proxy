# Watson stack audit

Date: 2026-07-11

## Executive result

The live stack is healthy at `65,536` runtime context, but three control-plane
contracts had drifted:

1. The canonical launcher reused llama.cpp when model alias and context matched,
   even if requested batch, ubatch, or model-path settings differed.
2. Hermes displayed `131,072` because its Headroom/LiteLLM route omits runtime
   context metadata and the model name fell through to Hermes' broad
   `qwen = 131072` catalog rule.
3. Headroom received Discord traffic, but the old per-channel memory-header
   integration had not touched its project stores since June. Hermes' built-in
   memory tool also owns normal agent saves, so Headroom persistence and Hermes
   persistence must not be treated as the same feature.

The launcher now verifies requested runtime options before reusing llama.cpp.
Hermes is explicitly configured for `65,536` and sends stable
`x-headroom-project-id: watson-global` and `x-headroom-user-id: juanbeck`
headers. The gateway was restarted and the live request ledger confirmed both
headers.

## Frozen live baseline

| Field | Observed value |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5070 Ti, 16,303 MiB |
| Driver | 610.74 |
| llama.cpp build | `9418 (2cbfdc62a)`, MSVC 19.44 |
| Model | `D:\MODELS\Qwen3.6-27B-Q3_K_M.gguf` |
| Runtime context | `65,536` |
| Training context metadata | `262,144` |
| Runtime batch / ubatch | `512 / 256` |
| KV cache | `q8_0 / turbo2` |
| Flash Attention | on |
| Live VRAM free while idle | approximately 971 MiB during audit |

The running model is the 27B Q3 model, not a 20B model.

## Existing performance winner

The previous fixed-corpus loop found `1024/512` to be the best balanced
candidate:

- repeated cold prefill improvement of approximately 5.1% over fresh default;
- approximately 610 MiB free after the measured request;
- LiteLLM/Headroom route gate passed;
- local Hermes-style workflow gate passed;
- real Discord/Hermes interactive promotion gate still outstanding.

The live stack was intentionally left at the canonical `512/256` default.

## MTP contract repaired

The repo report recommends the MTP pi-tune model and records roughly `54.12`
decode tok/s versus `38.75` without MTP. The current canonical launcher still
defaults to the non-MTP profile. On 2026-07-11, the opt-in MTP profile in the
related llama-cpp-server repo was corrected to the installed
`D:\MODELS\Qwen3.6-27B-MTP-pi-tune-Q3_K_M.gguf` and the proxy-compatible stable
`qwen36-turbo-hermes` alias. Configuration validation confirms the model exists
and the profile still selects `draft-mtp` with a two-token maximum draft.

The repaired profile was then tested rather than promoted on historical numbers.
MTP improved forced 512-token decode by about 60.2% with about 78.7% draft
acceptance, but its 42k prompt run dropped to 1006.671 prompt tok/s, reached only
80 MiB sampled free VRAM, and failed two of four strict workflow cases with
invalid JSON. Reject this exact MTP configuration for 65k production; the normal
non-MTP stack was restored and verified from its live command line.

## Hermes 128k display root cause

Hermes talks to `http://172.24.16.1:8787/v1`. Headroom's proxied `/v1/models`
response contains model IDs but no `context_length`, `max_model_len`, or llama
`meta.n_ctx`. The WSL host address is not treated as a local endpoint by Hermes,
so live probing returns no value. `qwen36-turbo-hermes` then fuzzy-matches the
hardcoded `qwen` family default of `131,072`.

`model.context_length: 65536` now makes Hermes resolve the same value as the live
llama server. `Set-HermesHeadroomMemory.ps1` makes that metadata update
repeatable, and `Switch-Qwen36ContextMode.ps1` now invokes it during deliberate
context changes.

## Headroom memory findings

- Headroom 0.26.0 is healthy with the local memory backend enabled.
- The old Discord-scoped databases exist but contain zero memories and were last
  modified on June 25-26.
- The default project database contains three older USER-scope facts.
- A direct Headroom `memory_save` followed by a separate `memory_search` returned
  the exact probe value under `watson-global`; the probe record was deleted.
- A Hermes one-shot request carried the new global headers, confirmed in
  Headroom's request log.
- That Hermes request saved through Hermes' built-in memory tool to
  `~/.hermes/memories/USER.md`, not Headroom. The disposable fact was removed.
- `headroom_retrieve` is a separate CCR tool for recovering compressed
  tool-output hashes. It is not semantic persistent-memory retrieval.

Headroom's documented default is project-scoped storage. A stable project header
creates shared memory across Discord channels while the user header preserves
user isolation. The official memory documentation is
https://headroomlabs-ai.github.io/headroom/memory/.

## Validation performed

- PowerShell parser passed for all touched launch/context scripts.
- Exact command-line option helper tests passed for quoted paths and batch/ubatch
  match/mismatch cases.
- `Test-Qwen36ContextMode.ps1 -ExpectedContextWindow 65536` passed.
- Hermes configuration check passed.
- Hermes gateway restarted and became active.
- Hermes runtime context resolver returned `65,536`.
- Headroom direct save/search readback passed and test memory was deleted.
- Python reporting scripts passed AST parsing.
- `git diff --check` passed.
