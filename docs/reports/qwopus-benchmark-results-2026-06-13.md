# Qwopus-VL-3.6-27B Q3_K_M bring-up and benchmark

Date: 2026-06-13

## Current live state

Qwopus VL is live on the Windows llama.cpp server and was intentionally left running for Juan to test.

- Direct llama.cpp: `http://172.24.16.1:8080/v1` from WSL, `http://localhost:8080/v1` on Windows
- LiteLLM proxy: `http://10.88.140.94:4000/v1` over ZeroTier, `http://localhost:4000/v1` on Windows
- API model alias kept as `qwen36-turbo-hermes` so existing Hermes/LiteLLM/Codex configs continue working
- Model: `D:\MODELS\Qwopus-VL-3.6-27B-Q3_K_M\Qwopus3.6-27B-v2-Q3_K_M.gguf`
- mmproj: `D:\MODELS\Qwopus-VL-3.6-27B-Q3_K_M\mmproj-F32.gguf`
- Context: `65536`
- Thinking/reasoning: **off** (`--reasoning off`), matching the previous TurboQuant setup
- KV cache: `-ctk q8_0 -ctv turbo2`
- Flash attention: on
- Metrics: on

Verified live command line includes:

```text
--reasoning off --mmproj D:\MODELS\Qwopus-VL-3.6-27B-Q3_K_M\mmproj-F32.gguf --mmproj-offload -c 65536 -ctk q8_0 -ctv turbo2
```

## Scripts created

### llama.cpp server repo

`C:\Users\Admin\PROJECTS\llama-cpp-server\scripts\start_qwopus_vl.ps1`

Purpose: start Qwopus text-only or VL mode with the same 65k/turbo KV profile as the current Hermes model.

Examples:

```powershell
# Text-only Qwopus, 65k context
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Admin\PROJECTS\llama-cpp-server\scripts\start_qwopus_vl.ps1 -ContextSize 65536 -Metrics -StopExisting

# Vision-language Qwopus, 65k context
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Admin\PROJECTS\llama-cpp-server\scripts\start_qwopus_vl.ps1 -ContextSize 65536 -Vision -Metrics -StopExisting
```

### ZeroTier proxy repo

`C:\Users\Admin\PROJECTS\zerotier-llm-proxy\scripts\windows\Start-QwopusZeroTierStack.ps1`

Purpose: stack-level launcher for Qwopus + LiteLLM + optional webchat2api Oracle.

Example:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Admin\PROJECTS\zerotier-llm-proxy\scripts\windows\Start-QwopusZeroTierStack.ps1 -Vision -Metrics -StopExisting
```

### Watson benchmark suite

`/home/juanbeck/Watson/scripts/llama_benchmark_suite.py`

Runs dependency-free throughput, coding smoke, and vision smoke tests against a llama.cpp OpenAI-compatible endpoint. From Windows it can target `http://localhost:8080/v1`; from WSL use the Windows host IP.

## Throughput results

Baseline from existing `llama-cpp` skill: Qwen3.6 TurboQuant Hermes Q3_K_M, 65k context.

| Test | Baseline TurboQuant | Qwopus text-only | Qwopus VL/mmproj | Result |
|---|---:|---:|---:|---|
| Short prompt, ~500 generated | 42.7 tok/s, TTFT 0.933s | 35.729 tok/s, TTFT 2.354s | 35.897 tok/s, TTFT 2.443s | Qwopus ~16% slower |
| ~2k context, ~200 generated | 25.9 tok/s, TTFT 2.885s | 19.940 tok/s, TTFT 5.013s | 20.391 tok/s, TTFT 5.218s | Qwopus ~21% slower |
| ~8k context, ~100 generated | 8.9 tok/s, TTFT 8.894s | 6.208 tok/s, TTFT 13.763s | 5.978 tok/s, TTFT 14.468s | Qwopus ~30-33% slower |

Raw result files:

- `reports/benchmarks/llama-bench-qwopus-text-65k-20260613_060215.json`
- `reports/benchmarks/llama-bench-qwopus-vl-65k-20260613_061006.json`

## Coding smoke results

Simple coding prompts completed successfully. These are not a deep quality benchmark, only a sanity check that code generation works and latency is usable.

| Prompt | Qwopus text-only | Qwopus VL/mmproj |
|---|---:|---:|
| `binary_search` | 26.528 completion tok/s | 25.490 completion tok/s |
| `api_endpoint` | 28.503 completion tok/s | 29.412 completion tok/s |
| `regex_parser` | 34.885 completion tok/s | 35.834 completion tok/s |

Raw result files:

- `reports/benchmarks/llama-bench-qwopus-text-coding-20260613_060302.json`
- `reports/benchmarks/llama-bench-qwopus-vl-65k-20260613_061006.json`

## Vision smoke test

llama.cpp reports Qwopus VL capabilities as:

```json
"capabilities": ["completion", "multimodal"]
```

A first 1x1 image test returned an incorrect color (`white`), likely because the original tiny PNG payload was too degenerate. After replacing it with a generated 64x64 solid red PNG, Qwopus VL correctly answered:

```text
Red
```

Raw result file:

- `reports/benchmarks/llama-bench-qwopus-vl-vision-red-20260613_061144.json`

## Proxy verification

LiteLLM remained up and routes to the Qwopus-backed llama.cpp alias:

```text
Qwopus stack is perfectly fine.
```

from a `POST http://localhost:4000/v1/chat/completions` request with model `qwen36-turbo-hermes`.

## Readout

- Qwopus VL works locally with llama.cpp + `mmproj-F32.gguf`.
- Vision is genuinely enabled and smoke-tested.
- Throughput is clearly worse than the existing TurboQuant Hermes baseline at the same 65k context/KV settings.
- Keeping the alias as `qwen36-turbo-hermes` was the right compatibility move: no existing client config needed to change.
- For day-to-day front-model speed, the baseline TurboQuant model still wins. Qwopus is worth testing manually for code quality and native local vision.

## Follow-up candidates

1. Run a deeper coding benchmark with executable tests rather than preview-only prompts.
2. Try smaller context windows (`32768`, `16384`) to see if Qwopus quality/latency tradeoff becomes attractive.
3. If quality looks better, add a clean LiteLLM alias such as `qwopus-vl-36-27b` and update client profiles deliberately.
4. If vision will be used heavily, build a small image QA suite (color, OCR-ish text, chart/diagram description) instead of relying on one red-square smoke test.
