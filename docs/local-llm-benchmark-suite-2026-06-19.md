# Local LLM Benchmark Suite - 2026-06-19

## Decision

Use llama.cpp TurboQuant with the MTP pi-tune Q3 model for Hermes on this
RTX 5070 Ti 16GB machine. The stable Hermes name remains
`qwen36-turbo-hermes` at `http://10.88.140.94:4000/v1` through LiteLLM.

Lucebox/DFlash is not a supported primary backend on this GPU. Its upstream
documentation specifies at least 22GB VRAM for the Qwen3.6 target plus decode
draft and uses a 24GB RTX 3090 as the reference system. PFlash `skip-park` is
documented as a 32GB+ option. The prior 128k launch violated both constraints.

## Test Machine

- GPU: NVIDIA GeForce RTX 5070 Ti, 16,303 MiB VRAM
- CPU: AMD Ryzen 7 5800X
- Target context: 65,536 tokens
- Long prompt: 17,397 tokens, 26 generated tokens
- llama.cpp profile: Flash Attention, K=`q8_0`, V=`turbo2`, GPU layers `99`,
  parallel `1`

## Results

| Backend / model | Prompt tok/s | Decode tok/s | Long end-to-end | Notes |
|---|---:|---:|---:|---|
| llama.cpp TurboQuant, Qwen3.6 Q3 | 1,449.33 | 39.58 | 12.612s | Valid 65k baseline |
| llama.cpp TurboQuant, MTP Q3, normal profile | 1,287.76 | 38.75 | 15.066s | MTP disabled by normal profile |
| llama.cpp TurboQuant, MTP Q3, `draft-mtp` | 1,173.14 | **54.12** | 15.466s | 80% MTP acceptance; selected Hermes backend |
| DFlash Q3, 128k, PFlash cold | n/a | 2.88 | 131.119s | 54.75s scoring/compression before generation |
| DFlash Q3, 128k, PFlash warm | n/a | 18.53 | 55.583s | LiteLLM was not the bottleneck |
| DFlash Q3 + IQ4 draft, 65k, no PFlash | n/a | 3.53 | 11.6s / 36 tok | 11.5% draft acceptance; not viable |

`completion_tokens / entire request wall time` is not decode throughput. The
earlier 0.15 tok/s reading came from this incorrect calculation on a long
request. DFlash's internal logs showed the separate decode rate, but it still
lost materially to the llama.cpp MTP configuration on this hardware.

## Compatibility Matrix

| Artifact | Role | Result |
|---|---|---|
| `Qwen3.6-27B-Q3_K_M.gguf` | DFlash target / llama target | Starts in both engines |
| `Qwen3.6-27B-DFlash-IQ4_XS.gguf` | DFlash decode draft | Valid only with `--draft`; rejected as a target (`dflash-draft` architecture) |
| `Qwen3.6-27B-MTP-pi-tune-Q3_K_M.gguf` | llama.cpp MTP target | Valid with `hermes-qwen36-64k-mtp`; 54.12 decode tok/s |
| `Qwen3.6-27B-MTP-pi-tune-Q3_K_M.gguf` | DFlash target | Rejected: 65 blocks are not divisible by required full-attention interval 4 |
| `LFM2-8B-A1B-Q4_K_M.gguf` alongside MTP | Concurrent model | Loads, but leaves only 207 MiB VRAM and 305 MiB RAM free; stopped without traffic |

## Current Hermes Stack

```text
Hermes / Discord / Codex
  -> ZeroTier 10.88.140.94:4000/v1
  -> LiteLLM runtime config
  -> llama.cpp :8080
  -> Qwen3.6-27B-MTP-pi-tune-Q3_K_M.gguf
```

The configured client model remains `qwen36-turbo-hermes`. LiteLLM maps that
stable name to the active MTP backend, so no Hermes config edit is required.

## Guardrails Added

- Lucebox launch rejects DFlash draft files passed as targets.
- Lucebox launch rejects the known-incompatible MTP target.
- `--prefill-skip-park` is rejected below 32GB VRAM.
- Standard DFlash launch rejects <22GB VRAM unless explicitly passed
  `-AllowExperimentalLowVram`.
- 65k and 128k DFlash wrappers no longer enable PFlash plus `skip-park` by
  default; their experimental path uses request-scoped draft residency.

## Disk Footprint

`C:\Users\Admin\PROJECTS\lucebox-hub` currently occupies 3.82 GiB across
9,377 files. It has not been removed.

## Follow-up

Benchmark LFM2 only with the MTP Hermes server stopped, then restore MTP before
making it available through LiteLLM. Do not host both models simultaneously on
this machine.
