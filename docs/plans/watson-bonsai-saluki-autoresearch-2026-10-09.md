# Watson Bonsai / Saluki autoresearch plan

Date: 2026-10-09
Status: Saluki is the live candidate at a 150,016-token Q4_0 K/V context
allocation after an authenticated vision probe, a two-turn Headroom-routed
tool-flow test, and a context staircase through 142,834 actual prompt tokens.
The longest run reached 667.8 tok/s prefill / 29.6 tok/s decode with at least
2,618 MiB free VRAM and 3.966 GiB available host RAM. During that run, the user
actively tested Sunshine and confirmed the stream remained clear while GPU SM
utilization was near 100%; NVENC telemetry showed an encoder session. Qwen3.8
remains unloaded/unqueried.

Current measurements and caveats are in
[the candidate evaluation report](../reports/watson-bonsai-saluki-evaluation-2026-10-09.md).

## Goal

Select a reliable model/profile for the ZeroTier → Headroom → LiteLLM →
llama.cpp → Hermes path, with multimodal support where available. Prioritize
coherent tool use, prefill/TTFT and decode, a 150,016-token allocation and a
145k-class actual prompt, at least 2 GiB free VRAM, and enough host RAM to keep
the desktop, ChatGPT, Orca, and Sunshine responsive. Measure GPU SM utilization
separately from video encoder/decoder utilization, and avoid impairing an active
Sunshine stream.

## Guardrails

- Never start or route to Qwen3.8 during this loop.
- Run only one model server at a time. Keep `-np 1`; do not combine MTP/speculative
  drafts with 100k context until a separate VRAM budget proves safe.
- Hard-stop a challenger if sampled free VRAM falls below 2,048 MiB, host
  available RAM falls below 2 GiB, GPU/driver health changes, or an endpoint
  fails. Aim for at least 2.5 GiB VRAM before recommending concurrent Sunshine
  use because the current Saluki profile has only about 344 MiB margin over the
  hard floor.
- Only run a long-prompt benchmark during active Sunshine use with the user's
  explicit agreement and live monitoring; stop if the stream degrades or a
  memory/health guardrail is crossed. Otherwise use a quiet test window.
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
3. **Context staircase:** only with a stable host-RAM reserve and a quiet or
   actively monitored Sunshine session, step through 32k → 65k → 100k → 125k
   → 145k actual input at the 150,016 allocation. Stop on any safety threshold
   or correctness failure; do not repeat a failed high-water mark.
4. **KV cache:** at a safe context, compare Q8_0 control to Q4_0 challenger at
   matched prompt lengths. Keep Q4 at 150k only if strict retention, tool
   calling, and quality probes remain acceptable; document the precision/context
   tradeoff instead of treating Q4 as quality-equivalent by assumption.
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
- 150,016 allocation and a successful 145k-class prompt with at least 2,048 MiB
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

- Latest Saluki allocation is 150,016 with Q4_0 K/V and the F16 projector. The
  measured context staircase reached 142,834 actual prompt tokens (not the
  exact allocation maximum): 98,504 tokens measured 799.4/36.5 tok/s
  prefill/decode, 123,126 measured 723.2/32.5, and 142,834 measured
  667.8/29.6. The 21.7k baseline was 1,014.6/53.6. The 56-layer challenger
  reached 96% peak GPU use and only 12.9 tok/s decode, so it was rejected.
- The `--image-min-tokens 1024` profile correctly read a supplied Discord
  screenshot at 1,097 prompt tokens, leaving 2,910 MiB VRAM. A two-turn tool
  interaction through Headroom → LiteLLM → llama.cpp returned a valid
  `lookup_ticket` call and used its mock result. This validates API tool-call
  shape, not Hermes-side execution of a real tool or a full Discord workflow.
- After the Headroom-routed tool test, Windows available RAM briefly sampled
  0.91 GiB, then recovered. Subsequent long-prompt samples remained at or above
  3.966 GiB and the latest idle samples were 4.14–4.19 GiB; continue monitoring
  RAM during future tests and do not start another WSL cold start while the
  model is loaded.
- The observed 99–100% `utilization.gpu` is GPU-kernel activity, not NVENC
  utilization. During the near-145k prefill, telemetry showed one encoder
  session at 25–26 FPS; the user confirmed Sunshine was actively streaming and
  the image remained clear. This is a successful qualitative concurrency test,
  though client-side dropped-frame/latency telemetry was not collected. No hard
  90% utilization cap was tested or applied. The 56-layer profile is not a
  viable substitute because decode dropped below 13 tok/s while peak GPU use
  only fell from 100% to 96%.
- The fresh Bonsai run confirms 100,096 allocation, about 4.7 GiB idle VRAM
  headroom, and one short streamed sentinel through Headroom/LiteLLM. It did
  not measure long-prefill throughput or exercise the full 100k prompt length.
- Saluki's long-run minimum free VRAM was 2,618 MiB, above the hard 2 GiB floor
  and the 2.5 GiB concurrency target; the latest idle sample showed 2,864 MiB
  free. Do not add concurrent draft models or exceed the tested context under
  Sunshine without a fresh VRAM budget.
- A cold WSL/Gateway start briefly reduced available host RAM to 0.79 GiB; it
  later reclaimed to 4.27-4.29 GiB across three idle samples with the gateway
  active. This transient warrants continued monitoring before any long prefill.
- The latest five-case Bonsai suite scored 4/5 in 7.1 seconds, with a
  stack-specific/factual miss in its Discord-style answer. Available RAM fell
  from 3.47-3.48 GiB to 2.01-2.03 GiB afterward while llama-server working set
  grew from 1.61 to 3.09 GiB. The active binary's default prompt-cache limit is
  8,192 MiB; a 1,024 MiB Bonsai cache challenger is implemented and unit-tested
  as an opt-in, but the 8,192 MiB baseline remains the launcher default until
  the challenger is measured. It is not yet loaded. The verified Bonsai PID was
  stopped idle; starting WSL for Hermes config verification left only 1.84 GiB
  host RAM free, so the launcher
  correctly refused to load either model. The active WSL gateway remains up;
  `vmmem` uses about 3 GiB working set and a one-shot clean-cache reclaim did
  not hold host RAM above the 3 GiB cold-start gate. Do not bypass resource
  checks. A global `.wslconfig` memory/reclaim change needs user approval and
  would interrupt Hermes plus affect every WSL2 distribution.
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
  until Hermes client-auth and direct ChatGPT/Codex compatibility are verified.
  The isolated Headroom/LiteLLM 1.104.2 test now passes authenticated model-list
  passthrough using caller Authorization and a streamed chat request using
  `OPENAI_TARGET_API_HEADERS` scoped to the loopback LiteLLM target. Headroom's
  configured extra header is not added to generic passthrough routes, so the
  current Hermes provider still needs an API key before it can use the candidate
  version's protected `/models` route. Do not put the key in the shared iTrader
  environment or cut over production before testing that Hermes path and direct
  ChatGPT/Codex auth. The launcher continues to default to the current PATH
  runtime; the project-local executable is opt-in and refuses to start without
  its key. The shared iTrader environment was not modified.
- Hermes reports duplicate Discord credentials in stopped profiles. Preserve
  current default ownership; do not run gateway multiplex migration until the
  intended Discord profile ownership is explicitly resolved.
