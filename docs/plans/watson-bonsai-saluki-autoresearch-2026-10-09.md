# Watson Bonsai / Saluki autoresearch plan

Date: 2026-10-09
Status: Bonsai is the live candidate after a successful 100,096-context Q8_0
KV fit and short routed stream canary. Neither candidate has passed the complete
comparative promotion gate. Qwen3.8 is explicitly out of scope and must remain
unloaded/unqueried.

Current measurements and caveats are in
[the candidate evaluation report](../reports/watson-bonsai-saluki-evaluation-2026-10-09.md).

## Goal

Select a reliable model/profile for the ZeroTier → Headroom → LiteLLM →
llama.cpp → Hermes path, with multimodal support where available. Prioritize
coherent tool use, prefill/TTFT and decode, a 100,096-token allocation, at least
2 GiB free VRAM, and enough host RAM to keep the desktop, ChatGPT, Orca, and
Sunshine responsive. Avoid increasing GPU contention while Sunshine is active.

## Guardrails

- Never start or route to Qwen3.8 during this loop.
- Run only one model server at a time. Keep `-np 1`; do not combine MTP/speculative
  drafts with 100k context until a separate VRAM budget proves safe.
- Hard-stop a challenger if sampled free VRAM falls below 2,048 MiB, host
  available RAM falls below 2 GiB, GPU/driver health changes, or an endpoint
  fails. Aim for at least 2.5 GiB VRAM before recommending concurrent Sunshine
  use because the current Saluki profile has only about 344 MiB margin over the
  hard floor.
- Do not run a long-prompt benchmark while the user is actively using Sunshine.
  Start with short probes, then request a quiet test window for the context
  staircase.
- Do not call a model “fully GPU resident” without retained startup evidence
  such as the exact GPU-offloaded layer count. Record requested flags and
  observed VRAM separately.
- Keep Q8_0 K/V as the precision control. Q4_0 K/V is a separate challenger;
  it cannot be promoted on VRAM savings alone.

## Baseline before any ablation

1. Confirm the current model ID, context, llama.cpp binary/hash, launch flags,
   Headroom auth, LiteLLM model catalog, Hermes provider/default, and zero active
   requests.
2. Record 3 idle samples each for free VRAM, GPU utilization, host available
   RAM, and llama.cpp working set.
3. Run one short text sentinel, one strict tool call, and one SSE stream per
   candidate. Test image input only for Saluki, which has a projector.
4. Do not start a new server while another candidate is loaded. Preserve the
   known-good Saluki config so it can be restored after each experiment.

## Ablation order

1. **Cold-start reproducibility:** the explicit
   [Saluki launch path](../../scripts/windows/Start-SalukiStack.ps1) is now
   implemented and its idempotent reuse path passed against the live 100,096
   server. It pins the b11433 binary, model, mmproj, `-ngl 99`,
   `--mmproj-offload`, Q8_0 K/V, flash attention, `-np 1`, and refuses unsafe
   RAM/VRAM preflight, wrong Hermes model/context, missing proxy auth token, or
   a mismatched listener. A pre-final-gate run verified the full live flow
   without a model reload. The stricter final RAM gate correctly refused when
   available RAM did not stay above 2 GiB for two samples; later read-only
   sampling reached 2.52 GiB. A Bonsai cold fit run subsequently succeeded on
   the Prism build with `-ngl 99`, Q8_0 K/V, and direct I/O. The dedicated
   [Bonsai launcher](../../scripts/windows/Start-BonsaiStack.ps1) is now in
   place; its live reuse path has passed without reloading or generating. The
   generic launcher still defaults to Qwen3.8 and must not be invoked bare.
2. **Matched short baseline:** both Bonsai and Saluki, one cold and one warm
   run, same 8k–16k prompts and same short decode length. Capture prefill,
   first-token latency, decode, token counts, minimum VRAM, host RAM, and GPU
   utilization independently.
3. **Context staircase:** only in a quiet Sunshine window, step through
   32k → 65k → 80k → 98k input at the 100,096 allocation. Stop on any safety
   threshold or correctness failure; do not repeat a failed high-water mark.
4. **KV cache:** for the best safe candidate, compare Q8_0 control to Q4_0
   challenger at the same context and prompt. Keep Q4 only if strict retention,
   tool calling, and quality probes remain equivalent and the VRAM benefit is
   material.
5. **Batching / prefill:** one factor at a time for batch and ubatch. Require
   two cold repetitions and an end-to-end Hermes tool workflow before retaining
   a change.
6. **Agent quality:** same prompts for strict JSON, multi-tool routing,
   long-history retention, concise reasoning-off behavior, and task adherence.
   Saluki additionally gets a controlled image-grounding comparison: default
   image token floor versus `--image-min-tokens 1024`. Its retained startup log
   warns that Qwen-VL grounding needs at least 1,024 image tokens. Record the
   per-probe VRAM minimum and reject a setting that crosses the 2,048 MiB floor.
   Report item-level results; never reduce the comparison to an average score
   alone.
7. **Final route:** verify authenticated llama.cpp → LiteLLM → Headroom → Hermes
   CLI, SSE termination, model/context metadata, and no Qwen aliases. Only after
   that may a Discord canary be attempted.

## Promotion criteria

- Two matched cold runs per candidate with prefill, TTFT, and decode recorded
  separately.
- 100,096 allocation and a successful 98k-class prompt with at least 2,048 MiB
  sampled free VRAM throughout; target 2.5 GiB or more for Sunshine concurrency.
- At least 2 GiB host available RAM, no OOM/pagefile thrash, and no sustained
  GPU saturation that interferes with Sunshine.
- Full routed correctness suite passes, including strict tool-call shape and
  required image grounding for a multimodal winner.
- Hermes model/context metadata matches the live llama.cpp endpoint, memory
  project/user headers remain intact, Headroom token auth is verified, and SSE
  emits a clean `[DONE]`.
- No launcher default or alias can route a candidate name to Qwen3.8.
- The selected model is restored, tested once, and left as the only advertised
  local candidate. Historical Bonsai and Qwen reports remain immutable evidence.

## Current blockers / next action

- The fresh Saluki test confirms 100,096 allocation, functioning vision,
  tool calls, streaming, and Hermes CLI use. Full-context prefill and comparative
  quality remain unmeasured.
- The retained Saluki startup log warns that grounding may require
  `--image-min-tokens 1024`; its prior image read used the default and does not
  pass this grounding gate. The installed b11433 binary supports the flag. Run
  a default-vs-1024 image probe only with VRAM monitoring and keep >=2,048 MiB
  free throughout.
- The fresh Bonsai run confirms 100,096 allocation, about 4.7 GiB idle VRAM
  headroom, and one short streamed sentinel through Headroom/LiteLLM. It did
  not measure long-prefill throughput or exercise the full 100k prompt length.
- Saluki has only ~2.3 GiB free VRAM at idle/warm state. Do not add concurrent
  draft models or launch heavy context fills under Sunshine.
- A cold WSL/Gateway start briefly reduced available host RAM to 0.79 GiB; it
  later reclaimed to 4.27-4.29 GiB across three idle samples with the gateway
  active. This transient warrants continued monitoring before any long prefill.
- The generic `Start-WatsonStack.ps1` still defaults to Qwen. Do not invoke it
  without explicit Bonsai arguments. Use `Start-BonsaiStack.ps1` for the safe
  Bonsai-specific path; its exact live arguments are recorded in the evaluation
  report. The active LiteLLM process is from the shared iTrader environment
  (1.99.0), so isolate and test a 1.104.2 upgrade before changing that shared
  environment. The fetched Prism branch contains a newer Ada PQ2_0 kernel, but
  compile and benchmark it as a separate challenger before replacing the
  serving binary.
- LiteLLM 1.104.2 is staged in a project-local environment and passed a local
  authenticated Bonsai SSE canary. It requires `LITELLM_MASTER_KEY`; Hermes
  currently sends no LiteLLM API key through Headroom, and the ChatGPT/Codex
  profile auth also needs a compatibility check. Keep production on 1.99.0
  until a key is wired into Headroom's upstream fallback and all intended
  clients pass authenticated route tests. The launcher continues to default to
  the current PATH runtime; the project-local executable is opt-in and refuses
  to start without its key. The shared iTrader environment was not modified.
- Hermes reports duplicate Discord credentials in stopped profiles. Preserve
  current default ownership; do not run gateway multiplex migration until the
  intended Discord profile ownership is explicitly resolved.
