# Inkling Mini Mix-12G: 150K context and native-vision qualification

## Scope

The target is the existing OrcaRouter abliterated Mix-12G on the RTX 5070 Ti,
not a model or toolkit upgrade. A DSH pressure percentage is client-side history
usage; it is not the fraction of an already allocated KV buffer occupied in GPU
memory. In this worker llama.cpp declares allocated per-device KV buffers at
startup. Do not extrapolate maximum context from Task Manager's free VRAM alone.

**Selected default:** `inkling2-mini-long`, fixed **150,016 total tokens**, native
text + vision, **10,000 explicit output ceiling / 4,096 default if omitted**.
The exact Mix-12G decoder and official pinned Mini Q8 projector are unchanged;
projection is on CPU and decoder KV on GPU. The user explicitly approved this
150K default after the mixed long-input qualification.

**Explicit fallback:** `inkling2-mini-100k`, fixed 100,096, same vision/output
policy, and the normal **2,048 MiB free VRAM / 2 GiB available RAM** reserve.
Only the trusted 150K profile declares the approved experimental **512 MiB /
1.25 GiB** admission/readiness limits. Global reserves and startup's **3 GiB
host RAM** remain unchanged, including for Saluki. HTTP registrations and
runtime overlays cannot introduce the experimental policy. This profile policy
is an admission/readiness check, **not continuous cancellation of arbitrary
in-flight inference**; the bounded benchmark and qualification runner separately
sample and enforce their reserve throughout those tests.

Only one owned worker is allowed. Each experimental stop requires verified idle
admission and capture of alias, context and ceiling; the runner verifies all
three on restore. Early tests below restored the then-current 100K alias; the
final rollout selects 150K with an explicit separately named 100K fallback.
DSH default/capability and exact Inkling compaction changes were subsequently
authorized. Saluki and its serving policy were not changed.

## Bounded experiments

- **150K native timing:** stock staged b12651; Q4_0 K/V; batch 128, microbatch 32;
  fit target 1,450 MiB; no context shift or prompt reuse; exactly **150,016
  allocated**, **2,048** and **147,968** uncached inputs, 256 generated tokens
  each. Timed first visible text and native phase counters. The long synthetic
  request does not test 10,000 output; reserving that much would leave at most
  140,016 fully formatted input tokens before additional margin.
- **Optional long-chat holdout:** about 139,776 input tokens, seeded early/mid/late
  markers and missing/null in a natural-stop JSON reply, with a 1,536 output cap.
  It is a targeted grounding check, not a guarantee for arbitrary documents.
- **Native vision canary:** official pinned base Mini Q8_0 projector paired with
  the exact locally installed abliterated Mix-12G at **32,768** allocations,
  under standard reserves. Test two generated PNGs with identical instructions
  but different positions and colors, plus separate synthetic OCR. No user
  image, external inference or subscription activation. This does not test
  multimodal 150K fit or DSH image-routing compatibility.

High-context experiments enforce a **512 MiB hard VRAM floor / 1.25 GiB host
floor**, declared GPU-only KV, verified exact-PID WDDM observations, and abort
on stale data, shared over 512 MiB while inferencing, or more than 256 MiB growth
relative to the ready baseline. The startup phase is described separately.
A WDDM shared-allocation counter cannot prove absence of paging, nor does a
workstation measurement certify active Sunshine streaming. See the [previous
100K/110K evidence](<watson-inkling-output-context-2026-11-24.md>),
[projector provenance](<../knowledge/inkling-vision-compatibility.md>) and
[historical caption-relay findings](<../knowledge/watson-inkling-vision-relay.md>).

## 150K timing pass

Exactly **147,968 uncached input tokens + 256 generated** completed at 150,016
allocation. The short control was in the same allocated slot.

| Actual input | Prefill tok/s | Decode tok/s | TTFT | Minimum free VRAM | Minimum available RAM |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2,048 | 515.20 | 76.71 | 3.98 s | 1,488.7 MiB | 4.44 GiB |
| 147,968 | 247.83 | 47.82 | 597.11 s | 1,096.8 MiB | 3.18 GiB |

The long request completed in **602.45 s** wall time with zero prompt cache.
llama.cpp declared **1,374.75 MiB CUDA KV** (1,318.5 + 56.25) and no CPU KV.
The verified worker's WDDM shared allocation was constant at **358 MiB** during
both measured requests (startup transient **588 MiB**); long-input peak
observed dedicated usage was **13,507.94 MiB**. No guard tripped. The result
passed the experimental floor but is **not production-safe at the current
2,048 MiB reserve**. Increasing the cap has real latency cost; previous stock
100K cold test was about 321 s with 98,048 actual input.

The guarded test worker exited. The normal 100K startup reserve initially
refused restoration: **14,692 MiB free** versus its estimated **14,848 MiB**
preload requirement. No floor was lowered. The user quit ChatGPT Desktop after
we observed that process at about 320 MiB dedicated. At **14,922 MiB free** the
same 100,096 allocation and 10,000 output ceiling were verified restored, with
2,197 MiB free after load. No unrelated process was terminated by automation.

## 150K long-chat recall pass

A separate **139,694-token actual** formatted chat at the 150,016 allocation
returned all three independently seeded marker fields and the correct null for
the missing one, in strict JSON, stopping naturally after **313 tokens**.
Wall time was **560.52 s**. The sampled minima were **1,276.9 MiB free VRAM /
4.84 GiB available RAM**, with worker WDDM shared still **358 MiB** and no
experimental guard breach. This is a successful targeted recall test on one
synthetic document, not a public long-context benchmark score. The normal
100K reload initially missed its estimate by 77 MiB; a later natural headroom
recovery allowed the unchanged safety gate to pass, restoring 100K/10K.

## Native vision pass on the exact abliterated model

Downloaded **only the 345,910,944-byte projector** from the [pinned ggml-org
release](https://huggingface.co/api/models/ggml-org/Inkling-2-Mini-GGUF/tree/e8a2cecf0e78914f07cc27b9be711a9b2c7561d9)
and checked its GGUF signature, byte count, and SHA-256
`233287272ec5ac39b4168677aba54a4c024f202410f5629fac1830e1059f8abb`.
The decoder remained the user's existing abliterated Mix-12G. No model weights
were downloaded or changed. Only generated inline PNGs went to the isolated
localhost worker; no user image went to a provider.

| Generated image | Actual input tokens | Generated tokens | Wall | Criterion |
| --- | ---: | ---: | ---: | --- |
| Left red square | 94 | 85 | 1.922 s | Exact label + natural stop |
| Right blue square with identical wording | 94 | 87 | 1.656 s | Different exact label + natural stop |
| Four large synthetic digits | 66 | 82 | 1.500 s | Exact OCR + natural stop |

All three passed on **b12651 at 32,768**, Q4_0 K/V, batch 128 / microbatch 32,
fit target 2,200 MiB. Sampled vision minima: **2,614 MiB free VRAM** and
**5.07 GiB available RAM**, above normal reserves. The worker declared
**344.25 MiB device KV**, no CPU KV. It also declared a host compute buffer;
that is not CPU KV. Timings exclude model load (roughly 23 s). This proves the
exact pair conditions on pixels for simple local cases, not arbitrary vision
quality, complex tool calls, the normal proxy path, DSH photo handling, or a
100K/150K multimodal fit. Keep live model metadata text-only pending separate
route and capacity verification. A hosted caption relay is not needed for
these verified native cases; if one is ever requested, do not silently fall
back to a subscription or send user attachments off the host.

## 100K main-profile qualification

The matched 100,096 text-only baseline scored **519.33 prefill / 74.65 decode
tok/s** at 2,048 actual input, 3.95 s TTFT, with **2,093.5 MiB minimum free**.
The same-context GPU-projector attempt crossed the unchanged normal reserve
while loading (**1,888.6 MiB free**); the guard stopped only its owned worker.
This establishes reserve pressure, not an inference failure or a complete
measurement of the encoder's peak. Do not equate the 330 MiB file size with its
actual GPU workspace requirement.

CPU projection passed all three native image checks at 100,096: **2.06 / 1.88 /
1.73 s** wall, with **2,125.8 MiB free VRAM / 4.49 GiB available RAM** at minima.
The image encoder/projector runs on the host; the decoder's KV is still on the
GPU. The main `inkling2-mini-long` profile was then changed to `input:
[text,image]`, the hash-verified official Mini Q8 projector, `mmproj_gpu:false`,
and `image_min_tokens:0`. Context and output policy are unchanged. Saluki and
short Inkling were untouched. The projector was copied under the existing
local model-artifact directory, not embedded in source control.

The updated **main** alias subsequently passed the generated image/OCR cases
through **gateway → Headroom → LiteLLM → llama.cpp**, plus a separate exact-text
reply. Vision wall times including proxies were **3.34 / 1.99 / 1.80 s**.
Sampled minima were **2,212.9 MiB free VRAM / 3.79 GiB available RAM**. This is
local, native vision; no hosted captioner or image upload was involved. The
DSH catalog still requires matching image capability before it will deliver
pixels rather than a placeholder.

### Mixed text-and-image pressure pass at 100K

A fresh 100,096-allocation CPU-projector run processed a formatted text-plus-image prompt reported as **89,605 actual
input tokens**,  and returned valid five-field JSON: three
planted marker values, missing/null, and the four digits found only in pixels.
It stopped naturally at **196 completion tokens**, in **285.95 s wall**.
The engine log reported **317.32 prefill / 55.65 decode tok/s** for this mixed
request; no streamed first-token timestamp was recorded for this JSON response.
This input leaves room for the intended 10K output reservation, but the canary
itself used a bounded 1,536-token cap rather than forcing 10K generation.
The minima were **2,132.0 MiB free VRAM / 4.36 GiB available RAM**; the exact
worker's shared GPU allocation remained **348 MiB**, all KV stayed on GPU, and
no guard tripped. The three small image/OCR checks also repeated successfully.

These are synthetic acceptance tests inspired by the task separation of
[LongBench](https://github.com/THUDM/LongBench) (long-document recall and
reasoning) and [OCRBench](https://github.com/Yuliang-Liu/MultimodalOCR) (visual
text reading). They are **not** runs of those datasets or comparable benchmark
scores. A small routed set additionally checks structured extraction,
arithmetic, code-bound diagnosis, accepted-requirement tracking and mock
function selection without execution. The first exact-string arithmetic rubric
rejected `391.0`; an independent replay showed the arithmetic itself was
correct. The numeric-equivalence rubric accepts 391 or 391.0 and records that
change rather than treating the original strict-format failure as a model math
error.

The projector has no second autoregressive conversation KV cache. Its encoder
weights and transient attention/compute workspace are separate. The resulting
image embeddings become tokens in the decoder's existing input context and KV.
Image resolution, cropping and pixel preprocessing can affect token count and
workspace, so the simple fixture does not bound high-resolution screenshots.

## 150K mixed vision and recall pass

The same 150,016 allocation with **CPU projection and GPU KV** passed a second
mixed test, not merely a loaded-context check. It reported **139,742 actual
input tokens**, all five strict-JSON fields correct (three dispersed text
markers, null for the absent marker, and digits visible only in the image),
and stopped naturally after **323 output tokens**. Wall time was **556.42 s**.
The output cap for this acceptance check was 1,536, not a forced 10K decode.
Two position/color images and separate OCR repeated successfully afterward.

Sampled minima were **1,343.09 MiB free VRAM / 3.84 GiB available RAM**; exact
worker shared GPU allocation remained **358 MiB**, with GPU-only KV and no
experimental guard breach. It passes the explicitly approved 512 MiB floor,
not the normal 2,048 MiB floor. The runner then verified restoration of the
**100,096 text+vision / 10,000 output** fallback. This single mixed acceptance
run does not establish performance for arbitrary photos, many images, or a
10K answer at the same input length.

## 200K capacity probe

At **200,192 allocated context**, the original fitter target (1,450 MiB)
auto-offloaded additional weights: CUDA-host model buffer rose to **679.90 MiB**.
GPU KV was demonstrated (**1,815.75 MiB**, zero CPU KV), but ready-state WDDM
shared was **794 MiB**, above the bounded-shared criterion. The runner refused
inference. The lowest sampled free VRAM while loading was **1,155.5 MiB**; this
was not a 512-MiB-floor failure and shared allocation alone does not prove paging.

One final matched capacity arm used the **explicitly approved 512 MiB fitter
target and floor**. It retained the normal model placement, GPU-only KV, and
passed both position/color images and OCR (2.08 / 1.92 / 1.73 s). Its sampled
minima were **679.44 MiB free VRAM / 4.38 GiB available RAM**; exact worker
WDDM shared remained **366 MiB**, with no guard breach. The 100K main vision
profile and 10K output ceiling were restored and verified afterward.

**Interpretation:** 200K allocation can load and answer tiny images under the
approved floor; **no near-200K prompt, recall, long decode, or TTFT was tested**.
Only about 167 MiB remained above the hard floor in this run. It is not a
replacement for the qualified 150K mixed long-input result or the guarded
100K main profile. Arbitrary image workloads can need more temporary memory.

## Default rollout and Observatory acceptance

The main alias was promoted to 150,016 with a 13,800 MiB prelaunch estimate,
CPU projector, GPU KV, and the selected experimental policy. The old private
per-alias 100,096 context override initially blocked the gateway's fail-closed
startup validation. With the stack verified stopped, that exact old value was
backed up and migrated to the approved 150,016; other overrides were unchanged.
The full route then started successfully with the intended resident model.

DSH's exact saved provider row now declares **150,016 / 10,000 / [text,image]**,
with a separately verified **100,096 / 10,000 / [text,image]** fallback row.
The supported `agentDefaultModel.saveSelection` persisted
`watson-local/inkling2-mini-long`, and read-back verified the default after the
temporary migration bundle was removed. Existing explicit session selections
are intentionally not overwritten.

The one-shot migration had originally failed because plugin activation ran
inside HMR's asynchronous transaction. A timer inherited that transaction,
causing **HMR transactions cannot be nested**. The corrected version registers
only in activation, then executes once on the exact authorized session's normal
successful `get_goal` tool-result event. Actual installed HMR + Settings tests
reproduce the failure and verify the independent event call chain. It uses
revisioned, leaf-addressed Settings mutations and the supported default-model
writer, not direct profile-file edits or an unauthenticated mutation endpoint.

Watson Observatory now preselects the validated 150K vision profile **without
automatically launching anything**. **C** exposes the distinct 100K fallback
and short profile; **B → 3** runs the native bounded `vision-quick` profile.
**V** displays report-backed historical qualification evidence, independently
of whether a model is resident. Live resident identity remains separate from
selection and evidence.

The real 150K-default Observatory quick run passed **7/7 cases** (position/color
pair, image-only OCR, arithmetic, strict extraction, code-bound correction and
accepted-revision tracking). Its separate **1,024-input / 64-output** timing was
**491.76 prefill / 70.72 decode tok/s**, **2.078 s TTFT**, 2.969 s wall. Across
the whole suite the sampled minima were **1,548 MiB free VRAM / 3.48 GiB available
RAM**, with the selected 512 MiB/1.25 GiB policy enforced. This short-input rate
is not the near-150K rate. Results persist sanitized per-case status, usage and
timing, never image data, full prompts, raw answers or credentials.

A fresh disposable session through the **installed DSH ACP image-admission
path**, the full proxies and the actual 150K main returned the exact answer
from generated pixels in **5.92 s**. No tools ran and the temporary session
closed. A status check taken immediately after session completion saw transient
cleanup still busy; the subsequent independent status check verified the
correct 150K model resident and idle with no runtime error. No real-user image
or history was used.

## Compaction and terminal changes

The configuration-only `@local/watson-inkling-compaction` bundle was installed
through DSH's supported manager with application **applied** and the Standard
preset active. Its only semantic policy change is the exact Inkling Long route:
**8,192 headroom / 4,096 summary output**. An installed-engine smoke through
Watson requested 4,096 despite a 10,000 chat reservation, ended naturally after
381 generated tokens from 510 input tokens, and preserved both synthetic
markers in 6.34 s. This proves the summarizer, not durable replacement in the
user's existing session. Existing agents keep their starting preset revision;
use a fresh Standard session. See the [policy guide](<../knowledge/inkling-dsh-compaction-policy.md>).

The terminal now continues labeled system-only counters when the supervised
route is offline, verifies a restarted supervisor before showing its resident
model, and displays an expiring **read-only scratch-worker advisory** when a
known experiment publishes matching process identities and heartbeat. A live
read-only snapshot during the 100K trial confirmed system gauges and the
identified scratch worker without inventing gateway flow. Reopen the Python
terminal to get updated UI code; it is not retroactively hot-patched into an
already running process.
