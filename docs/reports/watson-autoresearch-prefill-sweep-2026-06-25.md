# Watson autoresearch prefill sweep

Date: 2026-06-25

## Summary

This was the first bounded autoresearch-style optimization pass for direct prefill throughput. The objective was cold direct llama.cpp prompt throughput at 65k context, measured through `/completion` timing fields rather than LiteLLM end-to-end elapsed time.

The best tested challenger was:

```text
n_batch=1024
n_ubatch=1024
context=65536
KV K=q8_0
KV V=turbo2
Flash Attention=on
```

This profile improved the 61k-class cold direct prefill measurement from roughly `1214` prompt tok/s to `1341` prompt tok/s, about `+10.5%`, while restoring successfully to the canonical 65k stack afterward.

## Plateau finding

The earlier 32k -> 61k elapsed-time plateau was explained by prompt cache reuse:

| Target | Cache mode | Prompt tokens evaluated | Cache tokens | Elapsed | Prompt tok/s |
| ---: | --- | ---: | ---: | ---: | ---: |
| 16k | cold | 11,737 | 0 | 8.423s | 1506.650 |
| 16k | warm identical | 4 | 11,733 | 0.657s | 63.555 |
| 16k | suffix change | 276 | 11,477 | 0.881s | 895.769 |
| 16k | prefix broken | 11,732 | 0 | 8.708s | 1498.226 |
| 32k | cold | 23,437 | 0 | 17.936s | 1409.751 |
| 32k | warm identical | 4 | 23,433 | 0.683s | 65.148 |
| 32k | suffix change | 276 | 23,177 | 0.955s | 822.376 |
| 61k | cold | 43,917 | 0 | 39.873s | 1213.997 |
| 61k | warm identical | 4 | 43,913 | 0.784s | 58.342 |
| 61k | suffix change | 276 | 43,657 | 1.114s | 672.906 |

Interpretation: warm prefix reuse is extremely valuable for Hermes/Discord-style continuation, but it must be labeled separately from cold prefill throughput.

## Batch/ubatch challengers

| Candidate | Target | Prompt tokens evaluated | Elapsed | Prompt tok/s | Decode tok/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline/current | 32k | 23,437 | 17.936s | 1409.751 | 41.264 |
| baseline/current | 61k | 43,917 | 39.873s | 1213.997 | 37.544 |
| b1024/ub512 | 32k | 22,474 | 16.335s | 1446.079 | 37.352 |
| b1024/ub512 | 61k | 42,091 | 33.766s | 1294.740 | 37.816 |
| b2048/ub512 | 32k | 22,474 | 16.378s | 1444.667 | 35.943 |
| b2048/ub512 | 61k | 42,091 | 33.869s | 1289.601 | 37.786 |
| b1024/ub1024 | 32k | 22,475 | 16.300s | 1448.918 | 37.490 |
| b1024/ub1024 | 61k | 42,092 | 32.648s | 1340.942 | 38.008 |

## Decision

Keep the canonical running stack restored at 65k. Do not permanently promote `1024/1024` yet; first repeat it with identical prompt construction and a full Hermes/LiteLLM/Discord-style task gate.

Recommended next autoresearch loop:

1. Repeat `1024/1024` twice to verify the cold 61k win.
2. Test `1536/768` if supported by the launcher.
3. Compare the top candidate through LiteLLM `/v1/chat/completions`.
4. Compare Headroom off/on for the same long-history task.
5. Promote only if tool fidelity and full task elapsed time also improve.

## Skill created

Created and validated:

```text
C:\Users\Admin\.codex\skills\autoresearch-optimizer
```

The skill provides a reusable bounded baseline/challenger/evaluate/keep-or-reject loop and includes a ranking helper:

```text
C:\Users\Admin\.codex\skills\autoresearch-optimizer\scripts\rank_experiments.py
```

External grounding used:

- Karpathy autoresearch: autonomous edit/evaluate/keep-or-reject loop with a fixed budget.
- Awesome autoresearch variants: generalized optimization traces beyond the original setup.
- Cerebras anti-cheating guidance: strict evals and narrow guardrails prevent metric gaming.
- vLLM prefix caching and metrics docs: prefill and cache metrics should be separated from decode metrics.
