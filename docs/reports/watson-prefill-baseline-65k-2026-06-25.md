# Watson prefill baseline at 65k

Date: 2026-06-25

## Summary

The 65k interactive profile is healthy. The staged prefill checks from 4k through 61k all returned HTTP 200, retained both context boundary markers, and passed the tool-fidelity check.

The main caveat is measurement precision: the current harness reports full request elapsed time and completion token rate, not true streaming time-to-first-token. It is still useful for comparing prompt-size behavior, but the next harness improvement should capture first-token latency directly.

## Live context verification

Direct llama.cpp endpoint:

- URL: `http://127.0.0.1:8080/v1/models`
- model: `qwen36-turbo-hermes`
- reported `meta.n_ctx`: `65536`
- reported `meta.n_ctx_train`: `262144`

## Results

| Target context | Prompt tokens | Cached tokens | Elapsed seconds | Completion tokens | Completion tok/s estimate | Alpha marker | Omega marker | Tool fidelity |
| ---: | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: |
| 4,096 | 4,134 | 4,130 | 1.218 | 44 | 36.123 | yes | yes | pass |
| 8,192 | 8,187 | 3,874 | 4.606 | 30 | 6.513 | yes | yes | pass |
| 16,384 | 16,293 | 7,927 | 6.901 | 44 | 6.375 | yes | yes | pass |
| 32,768 | 32,484 | 16,033 | 15.560 | 44 | 2.828 | yes | yes | pass |
| 49,152 | 48,675 | 32,224 | 17.998 | 44 | 2.445 | yes | yes | pass |
| 61,440 | 60,813 | 48,415 | 15.889 | 44 | 2.769 | yes | yes | pass |

## Interpretation

The 65k profile remains the right default for Discord-facing interaction. It can process near-cap prompts without reproducing the multi-minute stall seen during the earlier 80k synthetic prompt attempt.

The non-monotonic 48k/61k elapsed times are likely influenced by prompt cache reuse. That is operationally useful, but it means we should not treat these numbers as pure prefill throughput. A cold-cache and warm-cache split is needed before making a 80k+ promotion decision.

## Raw artifacts

Raw JSON and Markdown outputs were copied to:

- [prefill-baseline-65k-2026-06-25](../artifacts/prefill-baseline-65k-2026-06-25/)

## Next measurement step

Add or use streaming instrumentation so the report includes:

- time to first byte;
- time to first token;
- total prefill time if llama.cpp exposes it through metrics/logs;
- decode-only tok/s after the first token;
- cold-cache vs warm-cache run labels.
