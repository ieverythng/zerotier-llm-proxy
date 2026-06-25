# Watson prefill throughput optimization framework

Date: 2026-06-25

## Executive decision

Keep `65,536` context as the Hermes/Discord interactive default while we run a proper optimization round. The current 65k baseline is better than expected: even near-cap synthetic prompts completed without the multi-minute stall seen during the earlier 80k attempt. The next goal is not simply "raise context"; it is to reduce full agent task elapsed time while preserving tool correctness.

The surprising result is the 32k -> 61k plateau. The primary hypothesis is prefix/prompt cache reuse: the larger runs reported high `cached_tokens`, so they were not pure cold-prefill measurements. This is operationally useful, but it means the next sweep must separate cold-cache prefill, warm-prefix-cache prefill, decode speed, Headroom compression, tool-call correctness, and full Discord task wall time.

## Source-backed optimization levers

This plan is grounded in the local stack docs plus current inference-server docs:

- llama.cpp server docs state that prompt cache reuse can avoid re-evaluating shared prompt prefixes, and responses can expose `timings` including `cache_n`, `prompt_n`, `prompt_ms`, `prompt_per_second`, `predicted_ms`, and `predicted_per_second`.
- llama.cpp server parameters relevant to this round include `--ctx-size`, `--batch-size`, `--ubatch-size`, `--flash-attn`, `--cache-type-k`, `--cache-type-v`, `--perf`, `--metrics`, and slot/cache behavior.
- llama-server manpage documents `--cache-prompt` as enabled by default, `--cache-reuse` as dependent on prompt caching, and `--metrics` as the Prometheus-compatible metrics endpoint switch.
- vLLM automatic prefix caching documentation frames the same optimization class: repeated long-prefix and multi-round chat workloads improve in the prefilling phase, while decode-heavy workloads do not benefit as much.
- vLLM metrics documentation is useful as a measurement model: separate request-level timing, prompt token throughput, generation throughput, KV cache usage, and prefix-cache query/hit counters.

Reference URLs:

- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://manpages.debian.org/unstable/llama.cpp-tools/llama-server.1.en.html
- https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/
- https://docs.vllm.ai/en/stable/design/metrics/

## Baseline: what we already measured

All rows used the 65k live profile and passed marker retention plus tool fidelity. The key observation is that elapsed time did not scale linearly from 32k to 61k because cached prompt tokens rose sharply.

| Target | Prompt tokens | Cached tokens | Elapsed | Completion tok/s est. | Interpretation |
| ---: | ---: | ---: | ---: | ---: | --- |
| 4k | 4,134 | 4,130 | 1.218s | 36.123 | Almost fully cached; not a cold-prefill datum. |
| 8k | 8,187 | 3,874 | 4.606s | 6.513 | Partial cache reuse. |
| 16k | 16,293 | 7,927 | 6.901s | 6.375 | Partial cache reuse. |
| 32k | 32,484 | 16,033 | 15.560s | 2.828 | First meaningful large-context checkpoint. |
| 48k | 48,675 | 32,224 | 17.998s | 2.445 | Warm-prefix behavior likely dominates. |
| 61k | 60,813 | 48,415 | 15.889s | 2.769 | Non-monotonic result; likely cache and/or prompt-shape effect. |

Conclusion: the current result supports 65k usability, but it does not yet prove cold-prefill throughput at 65k. The next benchmark must record both "tokens requested" and "tokens actually evaluated".

## Four-layer optimization model

### 1. Measurement correctness

Do not promote a profile unless the measurement splits these quantities:

- time to first byte;
- time to first token;
- total request elapsed time;
- prompt/prefill timing from server `timings`;
- decode timing from server `timings`;
- `cache_n` or `cached_tokens`;
- `prompt_n`, `tokens_evaluated`, or equivalent uncached prompt work;
- GPU utilization and VRAM snapshots before, during, and after;
- Headroom tokens removed and compression latency when Headroom is in route.

Required output fields for future JSON/CSV reports:

```text
profile_name
server_context_size
prompt_context_target
cache_mode
prompt_tokens
cached_tokens
cache_n
prompt_n
prompt_ms
prompt_per_second
predicted_ms
predicted_per_second
ttfb_ms
ttft_ms
task_elapsed_s
headroom_tokens_removed
headroom_elapsed_ms
gpu_used_before_mib
gpu_used_peak_mib
gpu_used_after_mib
tool_json_valid
tool_correct
recovery_ok
```

### 2. llama.cpp runtime knobs

Keep the canonical launcher as the only stack owner:

```powershell
C:\Users\Admin\PROJECTS\zerotier-llm-proxy\scripts\windows\Start-Qwen36ZeroTierStack.ps1
```

Optimization knobs to sweep, in order:

| Knob | Why it matters | Default stance |
| --- | --- | --- |
| `n_ctx` | Sets maximum usable context and KV reservation. | Keep 65k default; test 80k/96k as candidates. |
| `n_batch` / `n_ubatch` | Controls prompt processing chunks and GPU utilization. | Sweep before changing model quantization. |
| Flash Attention | Reduces attention memory/work when supported. | Keep enabled; verify it remains on in each profile. |
| KV K/V cache type | Trades VRAM, precision, and long-context viability. | Keep `q8_0/turbo2` default until a sweep beats it. |
| `--cache-prompt` | Reuses common prompt prefix. | Treat as production-useful, but benchmark cold and warm separately. |
| `--cache-reuse` | Attempts chunk reuse via KV shifting. | Experimental; only accept if measured and stable. |
| `--metrics` / `--perf` | Exposes timings needed for decisions. | Enable during sweeps. |
| slots/cache save-restore | Can preserve or inspect prompt cache state. | Use only in controlled experiments. |

### 3. Agent-route knobs

Hermes/Discord performance is not only model speed. The route can waste or save thousands of tokens.

| Route lever | Test shape | Promotion criterion |
| --- | --- | --- |
| Headroom off/on | Same long Discord task with identical prompt corpus. | Wall time improves or token savings are large enough to justify added latency. |
| Forced compression | Tool-output-heavy history. | No loss of required facts or tool-call correctness. |
| Session ledger injection | Long continuation after compaction. | Smaller prompt with same task success. |
| Repeated tool-output compression | Multi-tool file/terminal task. | Fewer upstream tokens and no repeated tool loops. |
| Discord task shape | Realistic channel continuation, not only synthetic filler. | First visible output and final task completion are both acceptable. |

### 4. Backend experiments

Backend experiments must be gated behind the same harness:

- TurboQuant llama.cpp remains the reference baseline.
- 80k and 96k are candidate contexts only after cold/warm 65k is instrumented.
- 128k remains stress/batch mode unless it passes Discord usability gates.
- Lucebox/DFlash is retested only if it clears API compatibility, context, tool fidelity, decode, and VRAM gates.
- vLLM is evaluated as a sidecar/native-model experiment for prefix caching, metrics, scheduling, and worker models; it is not assumed to beat GGUF llama.cpp without local measurements.

## 32k -> 61k plateau investigation

Primary hypothesis: the apparent plateau is caused by prompt cache reuse, not raw cold-prefill throughput.

Run the following split for each large prompt target (`32k`, `48k`, `61k`):

| Run | Cache mode | Prompt shape | Expected signal |
| --- | --- | --- | --- |
| A | cold | restart or controlled cache-bust before request | Measures actual uncached prefill. |
| B | warm identical | repeat the exact same request immediately | Measures maximum prefix reuse. |
| C | warm suffix-change | same long prefix, new task suffix | Matches common Discord continuation behavior. |
| D | prefix-broken | change early prefix token(s), same suffix | Proves whether the cache is prefix-sensitive. |
| E | Headroom-on | same corpus routed through Headroom | Measures whether compression beats proxy overhead. |

Acceptance rule: do not call a result "prefill throughput" unless the report records `cache_n` or `cached_tokens` and `prompt_n` or `tokens_evaluated`. If those fields are missing, call it "end-to-end elapsed time" only.

## Optimization matrix

### Stage 0: freeze baseline

- Restore 65k through Lazarus.
- Verify `Test-Qwen36ContextMode.ps1 -ExpectedContextWindow 65536`.
- Record current model path, llama.cpp build, profile, KV cache types, Flash Attention state, LiteLLM route, Headroom route, and GPU memory state.

### Stage 1: measurement harness upgrade

- Add streaming time-to-first-token measurement.
- Parse llama.cpp `timings` and usage cache fields.
- Add cache-mode labels: `cold`, `warm_identical`, `warm_suffix_change`, `prefix_broken`.
- Add controlled cache-busting.
- Add GPU snapshot capture.
- Keep harness measurement-only; launcher remains responsible for stack mode.

### Stage 2: 65k cold/warm baseline

| Server context | Prompt targets | Cache modes | Required pass |
| ---: | --- | --- | --- |
| 65,536 | 4k, 8k, 16k, 32k, 48k, 61k | cold + warm identical | markers, tool JSON, TTFT, timings |
| 65,536 | 32k, 48k, 61k | suffix-change + prefix-broken | plateau explanation |
| 65,536 | realistic Discord task corpus | Headroom off/on | full task elapsed and correctness |

### Stage 3: runtime knob sweeps

Run one knob family at a time:

1. `n_batch` / `n_ubatch` sweep at 65k.
2. KV K/V cache sweep at 65k.
3. Headroom route/compression sweep on realistic agent tasks.
4. 80k context candidate with winning 65k settings.
5. 96k context candidate only if 80k is stable.
6. 128k stress only after restoring 65k and confirming the endpoint is idle.

### Stage 4: agentic workflow score

Every candidate profile gets scored on:

- short chat TTFT;
- long-history continuation;
- multi-tool Discord-style task;
- file-read/file-edit task;
- strict JSON/tool selection;
- Lazarus recovery to 65k;
- VRAM headroom after test completion.

## Hard promotion gates

| Profile | Promotion rule |
| --- | --- |
| 65k | Remains default unless another profile wins on full task elapsed time without correctness regression. |
| 80k | Candidate only if TTFT and task elapsed stay acceptable on realistic Hermes tasks. |
| 96k | Candidate only if 80k passes and the extra context produces task-quality benefit. |
| 128k | Stress/batch mode unless it passes the same Discord usability gates as 65k. |
| DFlash/Lucebox | Experimental until API compatibility, tool fidelity, and VRAM safety are proven. |
| vLLM | Sidecar/native-model experiment until it beats the baseline on local tasks. |

## Next code work

Apply `deslop-refactor` only to the files that need measurement improvements. Keep changes low-risk and behavior-preserving except for adding explicit metrics.

Planned code changes:

- Enhance `scripts/unix/watson-stack-harness.py` to record streaming TTFT, response `timings`, prompt cache fields, cache-mode labels, and raw JSON output.
- Enhance `scripts/windows/Measure-Qwen36ProxyThroughput.ps1` to parse `timings`, usage cache fields, and GPU snapshots into CSV.
- Add a controlled cache-busting option so cold-cache measurements are not accidentally warm.
- Add report fields for `cache_n`, `prompt_n`, `prompt_ms`, `prompt_per_second`, `predicted_ms`, `predicted_per_second`, `cached_tokens`, `headroom_tokens_removed`, and `task_elapsed_s`.
- Keep `scripts/windows/Start-Qwen36ZeroTierStack.ps1` as the canonical launcher used by Lazarus and all reruns.

Deslop guardrails:

- Do not rewrite the harness broadly.
- Extract repeated HTTP timing and cache-field parsing into small helpers.
- Keep launcher orchestration separate from benchmark measurement.
- Prefer explicit failure when metrics are unavailable instead of silently labeling a run as prefill throughput.

## Validation protocol

Run every optimization round in this order:

1. Restore 65k through Lazarus.
2. Verify context with `Test-Qwen36ContextMode.ps1`.
3. Run cold baseline once.
4. Run warm identical twice.
5. Run suffix-change and prefix-broken variants.
6. Run Headroom off/on with the same prompt corpus.
7. Run one runtime knob sweep only after the baseline fields are complete.
8. Restore 65k through Lazarus.
9. Verify context and proxy smoke before handing the stack back to Discord/Hermes.

Stop immediately if:

- live `meta.n_ctx` differs from the requested context;
- tool JSON validity regresses;
- Discord-style task loops or repeats tools;
- GPU stays saturated with no first token past the usability threshold;
- VRAM headroom falls below the documented safety margin.

## Expected output

The optimization round should produce:

- a dated report under `docs/reports/`;
- raw JSON/CSV artifacts under `docs/artifacts/`;
- a ranked profile table comparing baseline, cache behavior, Headroom route, and runtime knob changes;
- a clear default-mode decision for Hermes/Discord;
- a separate stress-mode decision for 80k/96k/128k.
