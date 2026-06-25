# Watson Autoresearch Prefill Trace — 2026-06-25

Primary artifact: [watson-autoresearch-prefill-trace-2026-06-25.html](watson-autoresearch-prefill-trace-2026-06-25.html)

This report converts the first Watson direct-prefill optimization sweep into an autoresearch-style progress trace: one tested modification per step, one primary metric, and an explicit keep/discard/repeat decision.

## Optimized metric

- Metric: cold direct `prompt_per_second` at the 61k prompt target.
- Direction: higher is better.
- Scope: synthetic direct llama.cpp prefill only. Promotion still requires Hermes/Discord task elapsed-time and tool-fidelity gates.

## Experiment ledger

| Step | Decision | Variant | 61k cold prompt tok/s | Elapsed s | Prompt n | Notes |
|---:|---|---|---:|---:|---:|---|
| 0 | keep | current-65k-default | 1213.997 | 39.873 | 43917 | Baseline 65k cold direct prefill with current TurboQuant stack defaults. |
| 1 | keep | b1024-ub512 | 1294.740 | 33.766 | 42091 | `n_batch=1024`, `n_ubatch=512` improved the 61k cold prefill baseline. |
| 2 | discard | b2048-ub512 | 1289.601 | 33.869 | 42091 | Raising batch to 2048 was slower than the previous challenger. |
| 3 | keep | b1024-ub1024 | 1340.942 | 32.648 | 42092 | Best observed direct prefill throughput so far. |
| 4 | repeat | b1024-ub1024-repeat | 1307.122 | 33.100 | 42080 | Repeat remained above baseline but below the first win; needs another repeat and task gate before promotion. |

## Current read

The strongest setting so far is `n_batch=1024`, `n_ubatch=1024`, context `65536`, KV K `q8_0`, KV V `turbo2`, Flash Attention on. It improved the first 61k cold direct prefill measurement by about 10.5%, but the repeat was closer to a 7.7% gain and ran with low VRAM headroom.

Do not promote this setting yet. The next decision point is one more repeat plus a LiteLLM/Hermes-style task gate.

## Artifacts

- Normalized CSV ledger: [../artifacts/autoresearch-prefill-loop-2026-06-25/results.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/results.csv)
- Normalized TSV ledger: [../artifacts/autoresearch-prefill-loop-2026-06-25/results.tsv](../artifacts/autoresearch-prefill-loop-2026-06-25/results.tsv)
- Visual trace: [watson-autoresearch-prefill-trace-2026-06-25.html](watson-autoresearch-prefill-trace-2026-06-25.html)
