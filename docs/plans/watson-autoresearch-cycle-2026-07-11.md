# Watson autoresearch cycle

Date: 2026-07-11

## Objective

Improve cold prefill, sustained decode, and full Hermes task time on the 16 GB
RTX 5070 Ti while preserving tool correctness and recovery. Move beyond the
stable 65k context only when a higher profile is measurably usable, not merely
loadable.

## Promotion gates

Every challenger must record exact launcher arguments, model path, llama build,
context, cache state, prompt corpus, prompt/decode timings, and GPU state.

| Gate | Requirement |
| --- | --- |
| Reproducibility | Same-corpus improvement survives at least two cold runs. |
| Prefill | Rank on uncached `timings.prompt_per_second`; record `cache_n` and `prompt_n`. |
| Decode | Record `predicted_per_second`; speculative/MTP runs also record acceptance. |
| Correctness | Exact-response, strict tool JSON, long-history retention, and continuation gates pass. |
| Route | LiteLLM and Headroom route smoke passes with the same profile. |
| VRAM | No OOM, no unsafe documented configuration, and retain at least the balanced candidate's approximate 512 MiB post-run margin. Add peak sampling before promoting 80k+. |
| Interactive | Real Hermes/Discord task completes with acceptable first-visible-output and total wall time. |
| Recovery | Canonical 65k restore and context verification pass after the experiment. |

Synthetic throughput alone cannot promote a default.

## Ordered ablations

### A. Close the current 65k candidate

1. Launch opt-in `1024/512`.
2. Run the same fixed 42k corpus twice.
3. Run a real multi-tool Discord task and record first-visible-output plus total
   task elapsed time.
4. Keep as an opt-in profile only if correctness and VRAM gates pass.
5. Restore `512/256` at 65k.

### B. Reconcile and retest MTP

1. Completed 2026-07-11: corrected the MTP profile's installed model path and
   stable alias in the related llama-cpp-server repo without changing the normal
   default.
2. Completed the decisive short-decode and 42k comparisons. The intermediate 8k
   point was unnecessary after the 42k run breached the memory guardrail.
3. MTP averaged 72.623 decode tok/s versus 45.335 for non-MTP (+60.2%) with
   about 78.7% draft acceptance, but 42k prefill fell to 1006.671 tok/s and the
   sampled free-VRAM floor fell to 80 MiB.
4. Rejected for 65k production: two of four strict workflow cases also emitted
   invalid JSON. Restored and command-line-verified the normal non-MTP default.

### C. Instrument VRAM peak and TTFT

`Measure-Qwen36DirectPrefill.ps1` now supports `-Stream`, records response-header
and first-token latency, and samples free VRAM/GPU utilization during the
request. Validate the new fields on small and 42k prompts before using them for
80k+ promotion decisions.

Validation completed on 2k smoke runs and two cold 42k runs per default/candidate
profile. A dedicated forced 512-token decode corpus now also records MTP draft
acceptance, resolving the earlier 32-token-tail instrumentation gap.

### D. Context staircase

Use a monotonic staircase and stop at the first failed safety/usability gate:

1. 65,536 control;
2. 81,920 candidate;
3. 98,304 candidate;
4. 114,688 research candidate;
5. 131,072 stress/batch profile.

At each server context, compare matched prompt lengths first. Then test a prompt
that exercises the extra window. A larger allocation is not a win if the task
does not need the additional tokens.

### E. KV-cache ablation for 80k/96k

Keep model, batch, ubatch, and prompt fixed. Sweep one K/V cache choice at a
time around the existing `q8_0/turbo2` baseline. Reject precision reductions
that lose long-history markers or tool fidelity even if they free enough VRAM
to load a higher context.

### F. Headroom memory ownership A/B

Current safe state: Hermes built-in memory remains primary; Headroom receives a
stable global project/user scope and its persistence path is verified.

Before cutover:

1. export and classify existing Hermes USER/project memories;
2. run a shadow corpus against Hermes memory and Headroom memory separately;
3. compare exact recall, irrelevant-memory injection, prompt-token overhead,
   save/search latency, and cross-channel recall;
4. choose one primary writer to avoid duplicate or conflicting memories;
5. only then remove the Hermes `memory` toolset from Discord so Headroom's
   `memory_save`/`memory_search` tools can own those calls;
6. retain `headroom_retrieve` independently for CCR recovery.

### G. Alternate backends/models

Only after the measurement gaps above close:

- retest MTP variants with reproducible acceptance and task gates;
- consider a smaller draft model only if combined VRAM leaves a safe context
  margin and acceptance materially exceeds the prior 11.5% DFlash result;
- keep DFlash/Lucebox blocked below its documented memory requirements unless
  an explicitly experimental profile is used;
- compare any new quant/model against the selected Q3 lane on tool correctness,
  not only tokens per second.

## Stop and restore rules

Abort a run on OOM, endpoint health loss, invalid tool output, unexplained cache
reuse in a cold claim, or a request exceeding the declared timeout. Log the
failure, restore the canonical 65k profile, and run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/Test-Qwen36ContextMode.ps1 -ExpectedContextWindow 65536
```
