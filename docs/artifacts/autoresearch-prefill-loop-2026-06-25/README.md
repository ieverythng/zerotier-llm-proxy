# Watson autoresearch prefill loop ledger

This folder is the durable artifact ledger for the Watson/Hermes prefill optimization loop.

The normalized ledger is `results.csv`; `results.tsv` is the same data in a spreadsheet-friendly format. Raw measurement CSVs are kept beside them when a run is important enough to cite in reports.

## Current metric

- Primary metric: `prompt_per_second`
- Direction: higher is better
- Scope: direct llama.cpp cold prompt/prefill throughput
- Guardrail: no challenger is promoted from synthetic prefill alone

## Canonical safe default

Always restore the interactive stack with the repo launcher:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Start-Qwen36ZeroTierStack.ps1 -ContextSize 65536 -Metrics -ReplaceLiteLLM -RouteHermesThroughHeadroom
```

Then verify:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Test-Qwen36ContextMode.ps1 -ExpectedContextWindow 65536
```

Expected default llama.cpp knobs after restore:

- context: `65536`
- batch: `512`
- ubatch: `256`
- reasoning: launcher passes `--reasoning off`
- route: Hermes/LiteLLM via Headroom when `-RouteHermesThroughHeadroom` is used

## Fixed-corpus gated run

Use the fixed 42k corpus to compare launcher profiles without prompt-shape drift:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Measure-Qwen36DirectPrefill.ps1 `
  -PromptTokens 42000 `
  -TokenTolerance 256 `
  -MaxOutputTokens 8 `
  -Variant baseline-default-42k `
  -CorpusPath _tmp\bench\autoresearch-prefill-loop\fixed-corpus-42k.txt `
  -OutCsv _tmp\bench\autoresearch-prefill-loop\baseline-default-42k-YYYYMMDD_HHMMSS.csv `
  -RequestTimeoutSec 300
```

For the current challenger:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Stop-Qwen36ZeroTierStack.ps1
Start-Sleep -Seconds 3
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Start-Qwen36ZeroTierStack.ps1 -ContextSize 65536 -BatchSize 1024 -UBatchSize 1024 -Metrics -ReplaceLiteLLM -RouteHermesThroughHeadroom

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Measure-Qwen36DirectPrefill.ps1 `
  -PromptTokens 42000 `
  -TokenTolerance 256 `
  -MaxOutputTokens 8 `
  -Variant challenger-b1024-ub1024-42k `
  -CorpusPath _tmp\bench\autoresearch-prefill-loop\fixed-corpus-42k.txt `
  -OutCsv _tmp\bench\autoresearch-prefill-loop\challenger-b1024-ub1024-42k-YYYYMMDD_HHMMSS.csv `
  -RequestTimeoutSec 300
```

## Ledger decision semantics

- `baseline`: safe reference point.
- `keep`: improved synthetic metric and remains eligible for repeat/task gates.
- `needs-repeat` / `repeat`: promising but not promotable yet.
- `discard`: measured and rejected.
- `fail`: harness, stack, or correctness failure; not a performance result.
- `measure`: control point or instrumentation check.

## Latest same-corpus finding

The 2026-06-26 gated run used the same 41,383-token corpus with `cache_n=0`:

| Step | Profile | Batch | UBatch | prompt tok/s | Elapsed s | Decision |
|---:|---|---:|---:|---:|---:|---|
| 8 | canonical default | 512 | 256 | 1235.880 | 33.821 | baseline |
| 9 | challenger | 1024 | 1024 | 1319.205 | 31.683 | needs-repeat |
| 10 | challenger repeat | 1024 | 1024 | 1326.823 | 31.489 | no-promote |
| 11 | balanced challenger | 1024 | 768 | 1300.004 | 32.136 | needs-task-gate |
| 12 | balanced challenger repeat | 1024 | 768 | 1162.069 | 36.081 | discard |
| 13 | fresh canonical default | 512 | 256 | 1224.956 | 34.083 | baseline |
| 14 | balanced challenger | 1024 | 512 | 1288.665 | 32.412 | needs-repeat-task-gate |
| 15 | balanced challenger repeat | 1024 | 512 | 1287.496 | 32.445 | synthetic-repeat-pass |
| 16 | route gate | 1024 | 512 | — | 4.608 | route-gate-pass |
| 17 | workflow gate harness v1 | 1024 | 512 | — | 0.039 | fail |
| 18 | local workflow gate | 1024 | 512 | — | 11.519 | workflow-gate-pass |
| 19 | opt-in launcher | 1024 | 512 | — | — | ops-artifact |
| 20 | opt-in launcher live validation | 1024 | 512 | — | — | launcher-verified |

The `1024/1024` challenger improved cold direct prefill by about 7.4% on repeat, but it left only about 105–109 MiB free after the runs. Keep it as the current speed ceiling, not as an interactive promotion candidate.

The `1024/768` challenger initially improved cold direct prefill by about 5.2% while leaving about 365 MiB free after the run, but its repeat fell below the default same-corpus baseline. Treat it as rejected until a control run explains the variance.

## Streaming TTFT and peak-VRAM validation (2026-07-11)

`Measure-Qwen36DirectPrefill.ps1 -Stream` now records response-header latency,
first-token latency, and native `nvidia-smi` samples during the request.

On the same 41,383-token corpus:

| Profile | Prompt tok/s | TTFT | Minimum free VRAM |
| --- | ---: | ---: | ---: |
| default `512/256` | 1246.247 | 33.387s | 840 MiB |
| default `512/256` repeat | 1224.286 | 33.916s | 906 MiB |
| candidate `1024/512` | 1290.782 | 32.180s | 654 MiB |
| candidate `1024/512` repeat | 1304.722 | 31.810s | 583 MiB |

The candidate averages 1297.752 prompt tok/s versus 1235.267 for the default,
an improvement of about 5.06%. Average TTFT improves from 33.651s to 31.995s,
about 4.92% or 1.66s. Both candidate runs stayed above the provisional 512 MiB
minimum-free guardrail. Decode is not promoted from this comparison because the
first default run's low value did not reproduce after restore.

## Fixed-tail decode and MTP rejection (2026-07-11)

The harness now supports short calibrated prompts, forced-length decode with
`-IgnoreEos`, and MTP draft/acceptance counters. On the identical saved
511-token prompt with a forced 512-token tail:

| Profile | Decode tok/s | Draft acceptance | Minimum free VRAM |
| --- | ---: | ---: | ---: |
| default repeat 1 | 45.260 | — | 815 MiB |
| default repeat 2 | 45.409 | — | 837 MiB |
| MTP repeat 1 | 72.186 | 78.338% | 383 MiB |
| MTP repeat 2 | 73.059 | 78.987% | 334 MiB |

MTP improved sustained decode by about 60%, but failed the complete promotion
gate. Its cold 41,383-token run fell to 1006.671 prompt tok/s, reached 41.466s
TTFT, and left only 80 MiB sampled free VRAM. The Hermes workflow gate passed
only two of four cases because tool-selection and continuation-summary JSON were
invalid. Reject this exact MTP profile for 65k production; the default non-MTP
stack was restored and verified.

The `1024/512` challenger is the current balanced candidate. It beat the fresh default by about 5.2%, repeated at about +5.1%, and left about 610 MiB free after the prefill runs. It also passed a lightweight LiteLLM/Headroom route gate and a heavier local Hermes-style workflow gate.

The local workflow gate validated:

- exact response fidelity;
- strict JSON tool-selection behavior;
- 12k-token long-history retention;
- continuation JSON for an agent handoff.

This is strong enough to document `1024/512` as an opt-in interactive candidate. It is not the same as a live Discord session with the full Hermes agent loop, so keep the canonical default at `512/256` until a real Discord trial is observed.

## Opt-in candidate launcher

Use this only when intentionally testing the validated candidate profile:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Start-Qwen36AutoresearchCandidate.ps1
```

The wrapper does not replace the canonical launcher. It stops the stack first by default, then calls:

```powershell
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1 -ContextSize 65536 -BatchSize 1024 -UBatchSize 512 -Metrics -ReplaceLiteLLM -RouteHermesThroughHeadroom
```

It also verifies that the live `llama-server.exe` command line contains `-b 1024` and `-ub 512`. This stop-first behavior is intentional because the canonical launcher currently skips llama startup when model/context already match, and that health check does not prove batch/ubatch changed.

The launcher was validated end-to-end on 2026-06-26: it stopped the default stack, started `1024/512`, passed context verification, and verified the live llama command line. The outer automation timed out after the success log was written, so the wrapper now ends with an explicit `exit 0`.

Restore the canonical default with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Stop-Qwen36ZeroTierStack.ps1
Start-Sleep -Seconds 3
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Start-Qwen36ZeroTierStack.ps1 -ContextSize 65536 -Metrics -ReplaceLiteLLM -RouteHermesThroughHeadroom
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Test-Qwen36ContextMode.ps1 -ExpectedContextWindow 65536
```

## Next gate

Do not run the Hermes/Discord task gate for `1024/768` yet. The next useful gate is a variance-control pass:

`1024/512` has passed the repeat prefill gate and local workflow gate. The next useful gate is a live Hermes/Discord validation:

- real Discord channel request through Hermes;
- tool JSON selection under the real agent loop;
- long-history continuation with actual channel history;
- Lazarus recovery through the canonical launcher if the stack is interrupted;
- final restore to the default `512/256` 65k profile.

If that gate passes, decide whether to promote `1024/512` from opt-in profile to default. Promotion should still consider user-observed Discord stability, not only synthetic prefill speed.

## Report generation

```powershell
python scripts\rank_experiments.py docs\artifacts\autoresearch-prefill-loop-2026-06-25\results.csv --metric prompt_per_second --direction max `
  | Set-Content docs\reports\watson-autoresearch-prefill-ranking-2026-06-26.md -Encoding UTF8

python scripts\plot_autoresearch_trace.py `
  --input docs\artifacts\autoresearch-prefill-loop-2026-06-25\results.csv `
  --output docs\reports\watson-autoresearch-prefill-trace-2026-06-25.html `
  --title "Watson Autoresearch Prefill Trace — 2026-06-25" `
  --metric prompt_per_second `
  --direction max
```
