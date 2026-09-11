# Watson Autoresearch Prefill Trace — 2026-06-25

Primary artifact: [watson-autoresearch-prefill-trace-2026-06-25.html](watson-autoresearch-prefill-trace-2026-06-25.html)

This report converts the Watson direct-prefill optimization sweep into an autoresearch-style progress trace: one tested modification per step, one primary metric, and an explicit keep/discard/repeat/fail decision.

## Optimized metric

- Metric: cold direct `prompt_per_second`.
- Direction: higher is better.
- Scope: synthetic direct llama.cpp prefill only.
- Promotion rule: no challenger is promoted from synthetic prefill alone; it must pass Hermes/Discord task elapsed-time, tool-fidelity, and VRAM gates.

## Experiment ledger

| Step | Decision | Variant | Cold prompt tok/s | Elapsed s | Prompt n | Notes |
|---:|---|---|---:|---:|---:|---|
| 0 | keep | current-65k-default | 1213.997 | 39.873 | 43917 | Baseline 65k cold direct prefill with current TurboQuant stack defaults. |
| 1 | keep | b1024-ub512 | 1294.740 | 33.766 | 42091 | `n_batch=1024`, `n_ubatch=512` improved the 61k cold prefill baseline. |
| 2 | discard | b2048-ub512 | 1289.601 | 33.869 | 42091 | Raising batch to 2048 was slower than the previous challenger. |
| 3 | keep | b1024-ub1024 | 1340.942 | 32.648 | 42092 | Best early direct-prefill observation, but not yet promotable. |
| 4 | repeat | b1024-ub1024-repeat | 1307.122 | 33.100 | 42080 | Repeat remained above baseline but below the first win; needs another repeat and task gate before promotion. |
| 5 | discard | b1024-ub1024-repeat3 | 987.449 | 43.522 | 41012 | Canonical-launcher repeat with `cache_n=0` regressed below baseline; do not promote from this mixed prompt-shape evidence. |
| 6 | control | fixed-current-live-42k | 1193.416 | 41.916 | 41382 | Live-stack fixed-corpus control with `cache_n=0`; validates the deterministic harness but is not a same-restart comparison. |
| 7 | fail | baseline-default-42k-aborted | — | — | — | Aborted harness run before serializer tightening; not a model performance result. |
| 8 | baseline | baseline-default-42k | 1235.880 | 33.821 | 41383 | Canonical default same-corpus baseline after JSON serializer fix; `cache_n=0`. |
| 9 | needs-repeat | challenger-b1024-ub1024-42k | 1319.205 | 31.683 | 41383 | Same fixed corpus with `n_batch=1024`, `n_ubatch=1024`; +6.7% prompt tok/s but only about 105 MiB GPU free after the run. |
| 10 | no-promote | challenger-b1024-ub1024-repeat2-42k | 1326.823 | 31.489 | 41383 | Repeat confirms the speed signal, but post-run GPU free memory remained about 109 MiB. Keep as speed ceiling candidate only. |
| 11 | needs-task-gate | challenger-b1024-ub768-42k | 1300.004 | 32.136 | 41383 | Intermediate `n_ubatch=768` keeps most of the gain with better post-run GPU free memory, about 365 MiB. Best current balanced candidate. |
| 12 | discard | challenger-b1024-ub768-repeat2-42k | 1162.069 | 36.081 | 41383 | Repeat of the balanced candidate fell below the default same-corpus baseline despite `cache_n=0`; do not task-gate or promote. |
| 13 | baseline | baseline-default-repeat2-42k | 1224.956 | 34.083 | 41383 | Fresh canonical default control; confirms default baseline stability before `1024/512`. |
| 14 | needs-repeat-task-gate | challenger-b1024-ub512-42k | 1288.665 | 32.412 | 41383 | `1024/512` beat the fresh default by about 5.2% and kept about 610 MiB free after the run. Best current balanced candidate. |
| 15 | synthetic-repeat-pass | challenger-b1024-ub512-repeat2-42k | 1287.496 | 32.445 | 41383 | Repeat confirmed `1024/512`: about +5.1% versus fresh default with stable VRAM headroom. |
| 16 | route-gate-pass | route-gate-b1024-ub512 | — | 4.608 | 6050 | LiteLLM/Headroom route smoke passed on `1024/512`; `Test-Qwen36Proxy` returned exact `qwen36 proxy ok`, and 0/8k proxy throughput completed. |
| 17 | fail | hermes-workflow-gate-harness-v1 | — | 0.039 | — | First workflow-gate harness used an invalid Responses input shape; logged as harness failure, not profile failure. |
| 18 | workflow-gate-pass | hermes-workflow-gate-b1024-ub512 | — | 11.519 | 12424 | Heavier local Hermes-style gate passed: exact response, strict JSON tool selection, 12k-token long-history retention, and continuation JSON. |
| 19 | ops-artifact | launcher-balanced-1024-512 | — | — | — | Added an opt-in candidate launcher that calls the canonical stack script with `-BatchSize 1024 -UBatchSize 512` and verifies the live command line. |
| 20 | launcher-verified | launcher-balanced-1024-512-live | — | — | — | Opt-in launcher was run end-to-end and verified the live `1024/512` command line; default was restored afterward. |

| 21 | instrumented-baseline | baseline-default-stream-42k | 1246.247 | 35.775 | 41383 | Streaming control: TTFT 33.387s and sampled minimum free VRAM 840 MiB. |
| 22 | instrumented-challenger | challenger-b1024-ub512-stream-42k | 1290.782 | 33.049 | 41383 | Streaming candidate: TTFT 32.180s and sampled minimum free VRAM 654 MiB. |
| 23 | instrumented-repeat-pass | challenger-b1024-ub512-stream-repeat2-42k | 1304.722 | 32.660 | 41383 | Repeat: TTFT 31.810s and sampled minimum free VRAM 583 MiB. |
| 24 | instrumented-workflow-gate-pass | hermes-workflow-gate-b1024-ub512-instrumented | — | 11.181 | 12424 | All four workflow cases passed again and Headroom remained healthy. |
| 25 | instrumented-baseline-repeat | baseline-default-stream-repeat2-42k | 1224.286 | 34.792 | 41383 | Explicit restored `512/256` control: TTFT 33.916s and sampled minimum free VRAM 906 MiB. |
| 26 | decode-baseline | decode-default-512-repeat2 | — | 11.828 | 511 | Forced 512-token tail decoded at 45.260 tok/s; minimum free VRAM 815 MiB. |
| 27 | decode-baseline-repeat | decode-default-512-repeat3 | — | 11.713 | 511 | Identical repeat decoded at 45.409 tok/s; minimum free VRAM 837 MiB. |
| 28 | mtp-decode-challenger | decode-mtp-512 | — | 8.022 | 511 | 72.186 tok/s and 78.338% draft acceptance, but only 383 MiB minimum free VRAM. |
| 29 | mtp-decode-repeat | decode-mtp-512-repeat2 | — | 7.556 | 511 | 73.059 tok/s and 78.987% draft acceptance, but only 334 MiB minimum free VRAM. |
| 30 | mtp-prefill-regression | mtp-stream-42k | 1006.671 | 43.448 | 41383 | TTFT 41.466s and minimum free VRAM 80 MiB; reject on prefill and safety. |
| 31 | mtp-workflow-gate-fail | hermes-workflow-gate-mtp | — | 18.700 | 12424 | Two of four cases failed with invalid JSON; reject MTP for 65k production. |

## Current read

The strongest same-corpus speed observation is `n_batch=1024`, `n_ubatch=1024`, context `65536`, KV K `q8_0`, KV V `turbo2`, Flash Attention on. Against the same 41,383-token corpus, it improved cold direct prefill from `1235.880` to `1326.823` prompt tok/s, about +7.4%.

Do not promote `1024/1024`. The VRAM guardrail is currently the main risk: both same-corpus runs left only about 105–109 MiB free after the run.

The attempted balanced candidate, `n_batch=1024`, `n_ubatch=768`, did not survive repeat. It first measured `1300.004` prompt tok/s, about +5.2% over default, but the repeat fell to `1162.069` prompt tok/s with the same corpus and `cache_n=0`. Do not task-gate or promote it without a new explanation/control run.

Current strict conclusion: `1024/1024` is the speed ceiling but fails the VRAM usability guardrail; `1024/768` has better headroom but failed repeat; `1024/512` is now the best balanced candidate, with about +5.1–5.2% over the fresh default and materially better VRAM headroom than `1024/1024`.

`1024/512` has now passed the same-corpus repeat, lightweight LiteLLM/Headroom route gate, and a heavier local Hermes-style workflow gate. The remaining caution is that this is still a synthetic local workflow gate, not a live Discord session with the real Hermes agent loop. Treat it as eligible for an opt-in interactive profile or a live Discord trial, not an unconditional default promotion.

The 2026-07-11 streaming repeats strengthen that conclusion with direct TTFT
and peak-sampling evidence: `1024/512` averaged +5.06% cold prompt throughput
and -4.92% TTFT versus two explicit `512/256` controls, while retaining at
least 583 MiB sampled free VRAM. This confirms the candidate as the balanced
prefill winner; it still awaits a user-observed Discord trial before default
promotion.

The fixed-tail MTP comparison does not change that prefill conclusion. MTP
raised comparable sustained decode from an average 45.335 tok/s to 72.623 tok/s
(about +60.2%) with about 78.7% draft acceptance, but it regressed 42k prefill,
reduced sampled free VRAM to 80 MiB, and failed two strict workflow cases. The
MTP challenger is rejected for the 65k production lane and the non-MTP default
has been restored.

An opt-in launcher now exists at [../../scripts/windows/Start-Qwen36AutoresearchCandidate.ps1](../../scripts/windows/Start-Qwen36AutoresearchCandidate.ps1). It preserves the canonical launcher contract by calling `Start-Qwen36ZeroTierStack.ps1`; it stops first by default so batch/ubatch changes cannot be skipped by the context-only health check.

That launcher has been validated end-to-end. It successfully loaded `1024/512`, verified context and live command-line flags, then the stack was restored to default. The first wrapper validation hit the outer automation timeout after success, so the wrapper now exits explicitly with `exit 0`.

The step-5 discard is not proof that `1024/1024` is always worse; it is proof that mixed prompt shapes and one-off live-stack runs are too noisy for promotion. The same-corpus run is the better comparison pattern.

## Harness tightening

The direct-prefill harness now exists at [../../scripts/windows/Measure-Qwen36DirectPrefill.ps1](../../scripts/windows/Measure-Qwen36DirectPrefill.ps1). It calibrates a deterministic prompt through llama.cpp `/tokenize`, stores or reuses a fixed corpus file, sends `/completion` with `cache_prompt=false`, and records prefill timings plus GPU memory into CSV.

On 2026-06-26 it was tightened to:

- avoid slow PowerShell `ConvertTo-Json` behavior on very large prompts by using a small JSON string-literal encoder;
- add phase logging so hangs show whether the script is reading, tokenizing, or submitting completion;
- add `-RequestTimeoutSec`;
- write failed CSV rows for harness failures instead of losing the event.

The first same-corpus baseline-vs-challenger run is complete:

- default `512/256`: `1235.880` prompt tok/s, `33.821s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-42k-20260626_201802.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-42k-20260626_201802.csv);
- challenger `1024/1024`: `1319.205` prompt tok/s, `31.683s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-42k-20260626_202237.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-42k-20260626_202237.csv);
- challenger repeat `1024/1024`: `1326.823` prompt tok/s, `31.489s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-repeat2-42k-20260626_203547.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-repeat2-42k-20260626_203547.csv);
- balanced challenger `1024/768`: `1300.004` prompt tok/s, `32.136s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-42k-20260626_203954.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-42k-20260626_203954.csv).
- balanced challenger repeat `1024/768`: `1162.069` prompt tok/s, `36.081s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-repeat2-42k-20260626_205018.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-repeat2-42k-20260626_205018.csv).
- fresh default repeat `512/256`: `1224.956` prompt tok/s, `34.083s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-repeat2-42k-20260626_205655.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-repeat2-42k-20260626_205655.csv).
- balanced challenger `1024/512`: `1288.665` prompt tok/s, `32.412s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-42k-20260626_210138.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-42k-20260626_210138.csv).
- balanced challenger repeat `1024/512`: `1287.496` prompt tok/s, `32.445s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-repeat2-42k-20260626_211055.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-repeat2-42k-20260626_211055.csv).
- route gate `1024/512`: LiteLLM Responses API returned exact `qwen36 proxy ok`; proxy throughput completed 0 and 8192 context-token cases, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/route-gate-b1024-ub512-proxy-throughput-20260626_2111.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/route-gate-b1024-ub512-proxy-throughput-20260626_2111.csv).
- local Hermes-style workflow gate `1024/512`: all four cases passed, total elapsed `11.519s`; long-history case used `12424` input tokens and elapsed `8.725s`, artifact [`../artifacts/autoresearch-prefill-loop-2026-06-25/hermes-workflow-gate-b1024-ub512-20260626_212515.csv`](../artifacts/autoresearch-prefill-loop-2026-06-25/hermes-workflow-gate-b1024-ub512-20260626_212515.csv).

After the run, the stack was restored through the canonical launcher and `Test-Qwen36ContextMode.ps1 -ExpectedContextWindow 65536` exited successfully.

## Headroom memory side-check

The restarted Hermes gateway is creating per-Discord-channel Headroom project memory stores under `.headroom/memories/projects/discord-<guild>-<channel>/`, which proves scoped headers are reaching Headroom. The current stores are initialized but contain `Total Memories: 0`, so routing works but Watson has not yet called `memory_save`.

## Reasoning-off invariant

The canonical restored stack command line includes `--reasoning off`. A raw direct `/completion` smoke still emitted a `<think>` prefix, so the optimization harness should treat raw-completion reasoning text as model/template behavior unless the server launch log contradicts the launcher invariant.

## Instrumented streaming validation (2026-07-11)

The harness now measures response-header latency, first-token latency, sampled
minimum free VRAM, maximum GPU utilization, and sample count. A native
`nvidia-smi --loop-ms` process avoids PowerShell background-job startup delay.

Two cold fixed-corpus runs per profile produced:

- default `512/256`: average 1235.267 prompt tok/s and 33.651s TTFT;
- candidate `1024/512`: average 1297.752 prompt tok/s and 31.995s TTFT;
- candidate delta: +5.06% prompt throughput and -4.92% TTFT (about 1.66s);
- candidate sampled minimum-free VRAM: 654 MiB and 583 MiB;
- all four direct runs reported `cache_n=0` and `prompt_n=41383`;
- the candidate passed the four-case workflow gate again in 11.181s;
- the stack was restored and verified at explicit `512/256`, context 65,536.

Do not claim a decode improvement from this run. The first default control
reported 23.456 tok/s, but the restored control reported 36.911 tok/s while the
candidate reported 36.961 and 37.796 tok/s. A longer dedicated decode corpus is
required.

## Artifacts

- Normalized CSV ledger: [../artifacts/autoresearch-prefill-loop-2026-06-25/results.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/results.csv)
- Normalized TSV ledger: [../artifacts/autoresearch-prefill-loop-2026-06-25/results.tsv](../artifacts/autoresearch-prefill-loop-2026-06-25/results.tsv)
- Visual trace: [watson-autoresearch-prefill-trace-2026-06-25.html](watson-autoresearch-prefill-trace-2026-06-25.html)
- Ranking report: [watson-autoresearch-prefill-ranking-2026-06-26.md](watson-autoresearch-prefill-ranking-2026-06-26.md)
- Runbook: [../artifacts/autoresearch-prefill-loop-2026-06-25/README.md](../artifacts/autoresearch-prefill-loop-2026-06-25/README.md)
- Fixed-corpus direct prefill harness: [../../scripts/windows/Measure-Qwen36DirectPrefill.ps1](../../scripts/windows/Measure-Qwen36DirectPrefill.ps1)
- Fixed 42k live control CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/fixed-current-42k-20260626_194012.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/fixed-current-42k-20260626_194012.csv)
- Same-corpus default CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-42k-20260626_201802.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-42k-20260626_201802.csv)
- Same-corpus challenger CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-42k-20260626_202237.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-42k-20260626_202237.csv)
- Same-corpus challenger repeat CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-repeat2-42k-20260626_203547.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-repeat2-42k-20260626_203547.csv)
- Same-corpus balanced challenger CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-42k-20260626_203954.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-42k-20260626_203954.csv)
- Same-corpus balanced challenger repeat CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-repeat2-42k-20260626_205018.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-repeat2-42k-20260626_205018.csv)
- Fresh default repeat CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-repeat2-42k-20260626_205655.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-repeat2-42k-20260626_205655.csv)
- Same-corpus `1024/512` challenger CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-42k-20260626_210138.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-42k-20260626_210138.csv)
- Same-corpus `1024/512` challenger repeat CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-repeat2-42k-20260626_211055.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-repeat2-42k-20260626_211055.csv)
- Route gate log: [../artifacts/autoresearch-prefill-loop-2026-06-25/route-gate-b1024-ub512-test-proxy-20260626_2111.txt](../artifacts/autoresearch-prefill-loop-2026-06-25/route-gate-b1024-ub512-test-proxy-20260626_2111.txt)
- Route throughput CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/route-gate-b1024-ub512-proxy-throughput-20260626_2111.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/route-gate-b1024-ub512-proxy-throughput-20260626_2111.csv)
- Workflow gate harness: [../../scripts/windows/Test-Qwen36HermesWorkflowGate.ps1](../../scripts/windows/Test-Qwen36HermesWorkflowGate.ps1)
- Opt-in candidate launcher: [../../scripts/windows/Start-Qwen36AutoresearchCandidate.ps1](../../scripts/windows/Start-Qwen36AutoresearchCandidate.ps1)
- Opt-in launcher validation log: [../artifacts/autoresearch-prefill-loop-2026-06-25/candidate-launcher-balanced-1024-512-20260626_2137.txt](../artifacts/autoresearch-prefill-loop-2026-06-25/candidate-launcher-balanced-1024-512-20260626_2137.txt)
- Workflow gate CSV: [../artifacts/autoresearch-prefill-loop-2026-06-25/hermes-workflow-gate-b1024-ub512-20260626_212515.csv](../artifacts/autoresearch-prefill-loop-2026-06-25/hermes-workflow-gate-b1024-ub512-20260626_212515.csv)
- Workflow gate JSON: [../artifacts/autoresearch-prefill-loop-2026-06-25/hermes-workflow-gate-b1024-ub512-20260626_212515.json](../artifacts/autoresearch-prefill-loop-2026-06-25/hermes-workflow-gate-b1024-ub512-20260626_212515.json)
