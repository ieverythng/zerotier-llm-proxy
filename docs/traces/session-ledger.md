# Session Ledger Workflow

The canonical server runs `qwen3.8` at a rounded 100,096-token allocation.
Legacy Qwen3.6 TurboQuant profiles remain available for controlled experiments,
but long prompts are expensive. For Hermes/Discord work, keep durable session
state outside the model request and inject a compact ledger after compaction.

## When To Use It

- Before a long Hermes/Discord run.
- Before restarting llama.cpp with a different `-ContextSize`.
- After benchmarks, repo changes, decisions, or user constraints.
- Immediately before or after a context compaction.

## Windows Helper

Create or update the default local ledger at `_tmp/session-ledger.md`:

```powershell
.\scripts\windows\Update-QwenSessionLedger.ps1 `
  -ActiveGoal "Optimize qwen3.8 Hermes/Discord endpoint for 100k context with measured throughput." `
  -StableFact "Active llama launcher is C:\Users\Admin\PROJECTS\zerotier-llm-proxy\scripts\windows\Start-WatsonStack.ps1." `
  -Constraint "Default production context is 100096; legacy 65536 TurboQuant profiles are special modes." `
  -Measurement "2026-06-05: 98304 synthetic context through LiteLLM took about 152.53s for a short answer." `
  -PrintPromptBlock
```

Use `-PrintPromptBlock` when handing state to a new compacted session. Paste only the relevant ledger block plus the immediate task, not the full transcript.

## What Belongs In The Ledger

- Active goal and current operating mode.
- Stable repo paths, ports, model ids, and runtime mapping.
- Benchmark results and commands that produced them.
- Decisions that should not be reopened without new measurements.
- Open questions and the next concrete test.

## What Does Not Belong

- Full chat transcripts.
- Large command output.
- Generated benchmark CSV contents.
- Speculative ideas without a next test.

## Recommended Pattern

1. Run or change one thing.
2. Record the result with `Update-QwenSessionLedger.ps1`.
3. Keep normal traffic on `65536`.
4. Use `98304` or `131072` only for targeted recovery, audit, or stress tests.
5. After compaction, paste the ledger summary and continue from the next step.
