# Qwopus-VL-3.6-27B Benchmarking Plan

## Goal
Benchmark Qwopus-VL-3.6-27B-Q3_K_M against the current base turboquant model (Qwen3.6-Turbo-27B-Q3_K_M) to determine if Qwopus offers better coding performance and throughput.

## Model Details
- **Location:** `/mnt/d/MODELS/Qwopus-VL-3.6-27B-Q3_K_M/`
- **Files:** `Qwopus3.6-27B-v2-Q3_K_M.gguf` + `mmproj-F32.gguf` (vision projector)
- **Quantization:** Q3_K_M (same as base model)
- **Vision:** YES — has mmproj file for multimodal input

## Current Baseline
- **Model:** Qwen3.6-Turbo-Hermes-27B-Q3_K_M (turboquant)
- **Context:** 65k tokens
- **Endpoint:** llama.cpp on Windows host at http://172.24.16.1:8080/v1

## Constraints
- ⚠️ **Only ONE model can run on llama.cpp at a time** — main session is currently active
- Must coordinate model swaps to avoid disrupting the primary workflow
- Oracle (GPT-5 via webchat2api) is available for expert guidance

## Testing Strategy

### Phase 1: Text-only benchmark (no vision)
1. Load Qwopus text-only on llama.cpp (swap from current model)
2. Run throughput benchmark using `benchmark-throughput.py` from llama-cpp skill
3. Test with same params as base model: n_ctx=65536, n_batch=2048, etc.
4. Record: tokens/sec, time to first token, memory usage

### Phase 2: Vision benchmark (VL mode)
1. Load Qwopus with mmproj for vision support
2. Test multimodal capabilities
3. Measure throughput impact of vision projector

### Phase 3: Coding quality comparison
1. Run same coding prompts through both models
2. Compare output quality, correctness, speed
3. Use Oracle (GPT-5) to evaluate code quality if needed

## Benchmark Script
Use the script from llama-cpp skill: `scripts/benchmark-throughput.py`
- Measures: tokens/sec, time to first token, total generation time
- Tests various context lengths and batch sizes

## Model Swap Procedure
1. Stop current llama.cpp server
2. Start with Qwopus model path
3. Run benchmarks
4. Restore original model
5. Compare results

## Comparison Metrics
| Metric | Base Turboquant | Qwopus VL | Winner |
|--------|----------------|-----------|--------|
| Tokens/sec (prompt) | TBD | TBD | - |
| Tokens/sec (generation) | TBD | TBD | - |
| Time to first token | TBD | TBD | - |
| Memory usage | TBD | TBD | - |
| Coding quality (Oracle eval) | TBD | TBD | - |
| Vision capability | No | Yes | Qwopus |

## Notes
- Turboquant versions available for both models — test with turboquant if possible
- Target: maintain 65k context window or improve
- Consider using subagents to run benchmarks while main session stays active
