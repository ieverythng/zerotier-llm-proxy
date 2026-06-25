# Lucebox Risk Assessment

**Date:** 2026-06-15
**Assessor:** WatsonOW (GPT-5.5 Thinking verified architecture)
**Risk Level:** Medium-High (experimental stack)

---

## Risk Matrix

| # | Risk | Likelihood | Impact | Severity | Mitigation |
|---|------|-----------|--------|----------|------------|
| R1 | Build fails on Windows | Medium | High | 🔴 High | VS 2022 + CMake 3.28 should handle it; fallback to prebuilt binary if available |
| R2 | No model conversion tool | **High** | **Critical** | 🔴 **Blocker** | Lucebox may require proprietary format; no GGUF→Lucebox converter confirmed |
| R3 | API incompatibility with LiteLLM | Medium | High | 🟡 Medium | Lucebox may not speak OpenAI-compatible API; may need custom adapter |
| R4 | Performance claims don't materialize | Medium | Low | 🟡 Medium | Benchmark before committing; rollback to llama.cpp is trivial |
| R5 | Windows firewall blocks ZeroTier | Low | High | 🟡 Medium | Test connectivity early; add firewall rule for port 18080 |
| R6 | Lucebox crashes under load | Medium | High | 🟡 Medium | Run stability test (100+ requests) before production use |
| R7 | VRAM conflicts with other apps | Low | Medium | 🟢 Low | Lucebox and llama.cpp don't run simultaneously in this config |
| R8 | Project abandonment | **Medium** | **High** | 🔴 **Strategic** | Lucebox is early-stage; check commit activity, Discord engagement |

---

## Critical Risks (Blockers)

### R2: Model Format — The Real Blocker

**This is the #1 risk.** Lucebox uses its own model format (dlpack/dtensor). The questions:

1. **Does a GGUF→Lucebox converter exist?** Not confirmed.
2. **Are pre-converted models available?** Check Lucebox Discord/GitHub releases.
3. **Can you convert yourself?** May require reverse-engineering the format.

**Impact:** Without models in Lucebox format, the server is useless.

**Mitigation strategy:**
- Join Lucebox Discord, ask about model conversion
- Check `dflash` repo for `convert.py` or similar tools
- Look for pre-converted Qwen models in Lucebox ecosystem
- If no path exists, this project is blocked until Lucebox provides tooling

### R3: API Compatibility

Lucebox's `dflash_server` may not speak OpenAI-compatible API. LiteLLM expects:
- `/v1/chat/completions` endpoint
- Standard request/response JSON format
- Streaming support via SSE

If Lucebox uses a custom API, you'd need to write an adapter layer between Lucebox and LiteLLM — adding complexity and latency.

**Mitigation:** Test the API format directly after first successful startup:
```bash
curl http://localhost:18080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test","messages":[{"role":"user","content":"hi"}]}'
```

---

## Medium Risks

### R1: Build Failures

Lucebox is designed for Windows + DirectCompute. Build requirements:
- MSVC compiler (VS 2022)
- DirectX SDK headers
- Potentially proprietary libraries

**Mitigation:** VS Community edition is free and includes what's needed. If CMake fails, check Lucebox issues for build workarounds.

### R6: Stability Under Load

New inference engines often crash under sustained load due to:
- Memory leaks in GPU buffers
- Unhandled edge cases in token generation
- Race conditions in request queuing

**Mitigation:** Run a stress test before trusting it:
```python
# Simple load test
import requests, time
for i in range(100):
    r = requests.post("http://127.0.0.1:4000/v1/chat/completions",
        json={"model": "lucebox-qwen", "messages": [{"role": "user", "content": f"Test {i}"}]})
    assert r.status_code == 200, f"Failed at request {i}"
    time.sleep(0.1)
print("All 100 requests passed")
```

### R8: Project Abandonment

Lucebox is an early-stage project. Signs of health:
- ✅ Active GitHub commits (check last commit date)
- ✅ Responsive Discord community
- ✅ Published benchmarks and documentation
- ❌ No PyPI package yet
- ❌ Limited model format support

**Mitigation:** Treat Lucebox as experimental. Keep llama.cpp as fallback. Don't remove the working stack until Lucebox proves stable for 2+ weeks.

---

## Low Risks

### R5: Firewall Issues

Windows Firewall may block inbound connections on port 18080.

**Fix:** One-time firewall rule:
```powershell
New-NetFirewallRule -DisplayName "Lucebox Server" -Direction Inbound -LocalPort 18080 -Protocol TCP -Action Allow
```

### R7: VRAM Conflicts

Lucebox runs on Windows; your current llama.cpp also runs on Windows. They'd share the same GPU.

**Mitigation:** In the proposed architecture, you switch from llama.cpp → Lucebox, not run both simultaneously. Stop llama.cpp before starting Lucebox.

---

## Overall Assessment

### Go/No-Go Decision

**Recommendation: Proceed with caution (pilot mode)**

**Reasons to go:**
- Potential 3-5x speed improvement justifies the effort
- RTX 5070 Ti is well-suited for DirectCompute workloads
- Rollback to llama.cpp is trivial (config change)
- Learning value even if Lucebox doesn't work out

**Reasons to hesitate:**
- Model format conversion is unconfirmed — could be a hard blocker
- API compatibility unknown — may require custom adapter
- Project maturity is low — maintenance risk

### Recommended Approach

1. **Week 1:** Build + smoke test (just get it running with any model)
2. **Week 2:** Solve model format problem (conversion or pre-converted models)
3. **Week 3:** API compatibility + LiteLLM integration
4. **Week 4:** Benchmarking + stability testing

**Kill criteria:** If after Week 2 you still can't load a model, reconsider the investment.

---

## Investment Estimate

| Phase | Time | Confidence |
|-------|------|-----------|
| Build + first run | 2-4 hours | 80% |
| Model conversion | 2-8 hours (or blocked) | 40% |
| API integration | 1-3 hours | 70% |
| Benchmarking | 1-2 hours | 90% |
| **Total (best case)** | **6-10 hours** | |
| **Total (worst case)** | **15-25 hours** | |

---

*Assessment by WatsonOW | GPT-5.5 Thinking verified architecture claims | 2026-06-15*