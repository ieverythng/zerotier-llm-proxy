# Watson stack masterplan

Date: 2026-06-25

## Goal

Make the Watson/Hermes local inference stack recoverable, measurable, and usable at the highest context size that still behaves well in Discord.

## Current decision

Use 65k context as the interactive default. Treat 80k, 96k, 112k, and 128k as high-context test profiles until measured prefill throughput proves they are usable.

## Operating principles

- Canonical startup path is `scripts/windows/Start-Qwen36ZeroTierStack.ps1`.
- Lazarus is the recovery harness, not an alternate stack owner.
- Context selection must be verified from live server metadata, not inferred from launcher arguments.
- Throughput reports must separate prefill behavior from decode behavior.
- Discord usability is gated by time-to-first-token, not only average generated tok/s.

## Workstreams

### 1. Runtime correctness

- Keep the context-aware launcher check in place.
- Ensure Lazarus always targets the Windows LiteLLM endpoint from WSL.
- Preserve a quick 65k restore path after failed high-context runs.
- Document the Hermes stale-import fix as a gateway restart procedure.

### 2. Prefill measurement

- Add a staged sweep over 4k, 8k, 16k, 32k, 48k, and 65k before retesting 80k+.
- Capture elapsed time, time-to-first-token where possible, generated tok/s, and GPU memory/utilization snapshots.
- Use realistic prompts in addition to synthetic filler prompts.
- Stop any test that exceeds the Discord usability threshold.

### 3. High-context profile evaluation

- Retest 80k only after 65k has a measured prefill baseline.
- Promote a higher context only if it improves task quality enough to justify latency.
- Keep 128k as a batch/research mode unless it becomes interactive with prefill optimization.

### 4. Documentation and artifacts

- Store plans in `docs/plans/`.
- Store architecture and runtime contracts in `docs/architecture/`.
- Store measured artifacts and imported matrices in `docs/artifacts/`.
- Store benchmark reports in `docs/reports/`.
- Store runbooks in `docs/knowledge/`.
- Store ledgers and traces in `docs/traces/`.

## Immediate sequence

1. Run a 65k smoke after any launcher or Lazarus change.
2. Run prefill sweep from small to large context.
3. Record results into a dated report under `docs/reports/`.
4. Update the benchmark matrix artifact only after the sweep has real measurements.
5. Decide whether 65k remains default or whether an intermediate context earns an interactive profile.

## Promotion criteria

A context profile can become the default only if it passes:

- load gate: correct model alias and `meta.n_ctx`;
- smoke gate: proxy test passes;
- functional gate: Watson harness passes;
- usability gate: Discord prompt returns initial output fast enough for normal interaction;
- recovery gate: Lazarus can restore the stack to a known-good 65k state after a failed run.
