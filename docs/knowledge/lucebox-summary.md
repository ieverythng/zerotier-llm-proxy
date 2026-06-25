# Lucebox Research — Executive Summary

**Date:** 2026-06-15
**Session:** Discord #research / MODEL-TESTING
**Verified by:** GPT-5.5 Thinking (architecture), GPT-5 (implementation details)

---

## What Is Lucebox?

Lucebox is a Windows-native inference engine using DirectCompute for GPU acceleration. Claims **20-100x faster** inference than traditional engines like llama.cpp. Uses its own model format (dlpack/dtensor) and ships with `dflash_server` — a CMake-built HTTP server.

**Repo:** https://github.com/lucebox-gpu/dflash
**Community:** Discord server (active, responsive team)

---

## Proposed Architecture

```
Hermes → LiteLLM proxy (WSL :4000) → ZeroTier → Lucebox (Windows :18080) → RTX 5070 Ti
```

Replaces llama.cpp as the inference backend while keeping the same LiteLLM proxy pattern already in place.

---

## Deliverables

| # | File | Purpose |
|---|------|---------|
| 1 | `reports/research/lucebox-research.md` | Initial research: architecture, claims, API format |
| 2 | `reports/research/lucebox-implementation-guide.md` | Full guide: build → run → proxy → test → troubleshoot |
| 3 | `reports/research/lucebox-quickstart.md` | Printable checklist for build day |
| 4 | `reports/research/lucebox-risk-assessment.md` | Risk matrix, go/no-go analysis, investment estimate |
| 5 | `reports/research/lucebox-litellm-config.yaml` | Ready-to-use LiteLLM config template |
| 6 | `scripts/bench-inference.sh` | Benchmark script: llama.cpp vs Lucebox head-to-head |

---

## Key Findings

### ✅ Verified (GPT-5.5 Thinking)
- Lucebox is a **full inference engine**, not just a draft model for llama.cpp
- Uses DirectCompute (Windows GPU API), not CUDA — works on any GPU
- `dflash_server` is the production server, built via CMake
- Claims 20-100x speed improvement over traditional engines

### ⚠️ Unconfirmed (Needs Testing)
- **Model format conversion** — no confirmed GGUF→Lucebox converter exists
- **API compatibility** — whether dflash_server speaks OpenAI-compatible API is untested
- **Actual performance** — benchmarks are theoretical until we run them

---

## #1 Risk: Model Format

Lucebox uses its own model format. Without a GGUF→Lucebox converter or pre-converted Qwen models, the server cannot load your existing models. This is the **potential hard blocker**.

**Action needed:** Join Lucebox Discord, ask about model conversion tools and pre-converted models.

---

## Recommendation

**Proceed in pilot mode.** 4-week phased approach:

| Week | Goal | Kill Criteria |
|------|------|---------------|
| 1 | Build + smoke test | Build fails after 2 hours of troubleshooting |
| 2 | Solve model format | No converter found, no pre-converted models |
| 3 | API + LiteLLM integration | Incompatible API requiring custom adapter |
| 4 | Benchmark + stability | Performance ≤ llama.cpp or crashes under load |

**Rollback is trivial:** revert LiteLLM config to point back to llama.cpp.

---

## Investment Estimate

- **Best case:** 6-10 hours total
- **Worst case:** 15-25 hours total
- **Potential payoff:** 3-5x token throughput improvement on RTX 5070 Ti

---

*WatsonOW | GPT-5.5 Thinking verified | 2026-06-15*
