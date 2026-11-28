# Watson trace synchronization: evidence before animation

**Status:** design proposal; no instrumentation enabled, no production HTTP
probes, no profile/proxy changes, no credential reads. Research date:
2026-11-24. This note proposes metadata-only correlation, not capture of raw
prompts, generated text, token IDs, tool arguments/results or credentials.

## 1. Decision

Build a small, local, bounded **request/span evidence graph** before presenting
cross-module activity as observed. Keep three independent axes explicit:

1. **Causality:** which admitted request/attempt produced a downstream call?
2. **Time:** which clock measured a duration, and when did a collector observe
   the evidence?
3. **Representation:** which tokenizer/template, compaction revision, cache
   accounting and output kind gave a token count its meaning?

Do not animate the same hypothetical token moving through DSH, Headroom,
LiteLLM and llama.cpp. These modules transform requests, buffer/rechunk streams,
and may initiate auxiliary calls; no inspected current interface establishes
that one transport chunk equals one model token or one UI update.

The initial deliverable should show an **observed gateway timeline**, explicitly
**inferred/unknown proxy edges**, and **process-scoped backend counters**. Only
promote an edge to observed after a controlled propagation test joins both ends.

## 2. Inspected topology and provenance

The supervised route is:

```text
DSH Host -> gateway :4141 -> Headroom :18083 -> LiteLLM :18082 -> llama.cpp :18081
                            (direct mode bypasses both proxy processes)
```

[Supervisor service definitions:225–242](<../../scripts/windows/local_model_supervisor.py#L225-L242>)
keep all listeners on IPv4 loopback. They stage Headroom **0.45.0** and LiteLLM
**1.110.2**, confirmed by installed distribution metadata, not by importing or
starting their services. The declared Headroom flags include stateless,
no-cache/no-memory-context/no-learn, disabled Kompress/fallback, and one retry
attempt. This is not the legacy Hermes route and does not prove that every
possible transform is absent. LiteLLM's
[isolated configuration:1–18](<../../config/server/local-model-litellm.yaml#L1-L18>)
uses local wildcard routing, no router retries, disabled verbose/telemetry and
disabled spend logs. These flags are configuration facts, not measurements.

Repository HEAD at inspection: `92f37aa45315adb22f563ea1a60b7a0fdca1d62a`.
Several relevant files are dirty/untracked; this note cites **working bytes**,
not a claim that HEAD contains the new flow observer. SHA-256 at inspection:

| Evidence file | SHA-256 |
| --- | --- |
| [Gateway](<../../local_stack/gateway.py>) | `945ce38f9c06ca69bf1fd7181e2190adc4846011890fc0b8201f8f41df0798eb` |
| [Request-flow observer](<../../local_stack/request_flow.py>) | `d687693481dfb6c3c7ed8a7a69c45c3d4adaf0b60fe5f7e595d6206ec3cff578` |
| [Supervisor](<../../scripts/windows/local_model_supervisor.py>) | `f0bbae71ee094114a540dd557f028692d435f08ce47af8636bb125425d8d2147` |
| [LiteLLM route config](<../../config/server/local-model-litellm.yaml>) | `93f6911a5933aedbb4769f0b389c8336c7115847b1494064943a84479b93c696` |

The installed DSH source is **0.2.2-rc.5** in its Desktop ASAR. The b12651
llama.cpp protocol reference below is **tag-specific upstream documentation**;
record the actual selected binary/model hashes and profile revision in any
future proof artifact. A tag document alone does not attest a loaded binary.

## 3. What is observed today, and what is not

| Boundary | Supported claim from inspected source | Missing proof / forbidden inference |
| --- | --- | --- |
| Gateway ingress/admission | Request kind, local ID, admitted/rejected/outcome and monotonic elapsed times | Gateway transport completion is not DSH task success |
| Gateway forwarding | load/validate, forward wait, response-header arrival, bytes written and first nonempty content observation | Forward wait is not specifically Headroom compression, LiteLLM scheduling or GPU prefill |
| Headroom/LiteLLM UI nodes | Configured route implies traffic may traverse these nodes | No joined downstream request spans yet; do not show them as observed active processors |
| llama.cpp metrics | Authenticated, still-owned process epoch, processing gauge and aggregate prompt/generated counters | Not individual-request progress, exact token position, decode phase or a GPU-utilization interpretation |
| DSH context/compaction | Durable request/context and compaction records are independent evidence sources | Gateway bytes/metrics cannot identify DSH compaction or UI rendering |

The [current observer:34–57](<../../local_stack/request_flow.py#L34-L57>)
assigns generation-local `req-N` IDs. Its
[snapshot:127–137](<../../local_stack/request_flow.py#L127-L137>) explicitly marks
proxy nodes inferred/configured and metrics correlation `process_not_request`.
The [metrics sampler:158–236](<../../local_stack/request_flow.py#L158-L236>)
checks ownership before/after a bounded response, refreshes at most every two
seconds, bounds reads to 64 KiB and marks stale/unknown after expiry or failure.
Preserve these honest unknown states.

The [forwarding loop:305–376](<../../local_stack/gateway.py#L305-L376>) holds
single-request admission through upstream teardown. `forwarded` is called only
after a local socket write/flush; that is **not proof the remote UI consumed
or rendered the bytes**. The first-content probe parses bounded transient
OpenAI content fields, not reasoning-only/tool-only frames; call this
`first_content_observed_at_gateway`, not backend first token or browser TTFT.

### Correlation gaps

- [Gateway outbound headers:170–180](<../../local_stack/gateway.py#L170-L180>)
  are newly constructed from downstream authentication and Headroom scope.
  Incoming `traceparent`/request IDs are not currently copied. The response loop
  also does not preserve downstream correlation headers.
- Headroom generates its own `hr_<wall-second>_<counter>` identifier:
  [installed source:2758–2762](<../../.runtime/local-model-deps/headroom0.45.0/Lib/site-packages/headroom/proxy/server.py#L2758-L2762>).
  It is not a proven alias of a gateway request ID.
- Headroom copies incoming headers, removes hop/body-encoding fields and strips
  internal `x-headroom-*` before upstream forwarding:
  [Chat handler:3703–3747](<../../.runtime/local-model-deps/headroom0.45.0/Lib/site-packages/headroom/proxy/handlers/openai.py#L3703-L3747>),
  [strip policy:2177–2202](<../../.runtime/local-model-deps/headroom0.45.0/Lib/site-packages/headroom/proxy/helpers.py#L2177-L2202>).
  This is a candidate propagation seam, **not proof of complete trace-context
  survival through all paths**. Do not disable internal-header stripping.
- LiteLLM has a native `x-litellm-call-id` error-header seam:
  [installed helper:247–264](<../../.runtime/local-model-deps/litellm1.110.2/Lib/site-packages/litellm/proxy/common_utils/openai_error_payload.py#L247-L264>).
  A native call ID should be mapped to our attempt/span, not assumed identical;
  success/streaming propagation still needs dedicated tests.
- Headroom's
  [StageTimer:58–130](<../../.runtime/local-model-deps/headroom0.45.0/Lib/site-packages/headroom/proxy/stage_timer.py#L58-L130>)
  measures with `perf_counter`; repeated measurements of the same stage replace
  the previous value. Do not sum them as an exhaustive nonoverlapping pipeline.

## 4. Proposed correlation contract

Use [W3C Trace Context](<https://www.w3.org/TR/2021/REC-trace-context-1-20211123/>)
for transport: random 128-bit trace IDs and per-operation span IDs, strict format
validation, no identity/content in IDs. Never treat a trace ID, sampled bit or
request header as authorization. Keep tracing inside the existing authenticated
loopback route; no new public listener, permissive CORS, auth forwarding or
external collector.

Proposed identities, with different scopes:

- `operation_id`: one optional DSH turn/work item; only present if an owning
  DSH integration actually supplies it. It is not synthesized from timing.
- `attempt_id`: one provider attempt. A retry is another attempt, even when
  the same Session, user prompt and requested model are retained.
- `trace_id`, `span_id`, `parent_span_id`: causality for an individual outbound
  call/hop. Compaction summarization and other auxiliary model calls get their
  own spans/attempt IDs and explicit purpose; they are not token continuations.
- `gateway_generation`, `service_instance`, `model_epoch`: prevent ID reuse
  across gateway restart, process replacement, model reload or counter reset.
- `native_id`: bounded per-service identifier mapped at an instrumented edge.
  If unavailable, preserve a missing edge rather than inventing a join from
  similar wall times, single-slot operation or matching model names.

Gateway should generate a trusted child span after authentication and admission,
allowlist outgoing trace headers, map request/response IDs in memory, and never
forward client-supplied arbitrary headers. Incoming malformed IDs get a new
local root plus a safe validation outcome, not raw-header logging. A downstream
retry keeps the parent operation link but receives a distinct attempt/span.

For DSH, a future explicit Host plugin can use the documented synchronous
[request-header waterfall:99–114](<C:/Users/Admin/AppData/Local/Programs/DeepSeek%20Harness/resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-llm-pi-ai/src/index.ts#L99-L114>)
for model-hidden trace metadata. Its
[actual context:1–10](<C:/Users/Admin/AppData/Local/Programs/DeepSeek%20Harness/resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-llm-pi-ai/src/types.ts#L1-L10>)
contains provider/model/sessionId/purpose/baseURL, **not turn/step/attempt IDs**.
Do not pretend those missing fields exist or use Session ID alone as a one-call
join. Exact turn/attempt attribution needs an explicit owner-side mapping seam.
The header event excludes model discovery and is not an async side-channel for
starting services or measuring progress.

## 5. Clocks: ordering without false precision

Each producer records its own monotonic durations and local sequence. Use
`monotonic_ns` or equivalent integer timer units, with a `clock_domain` and
process-instance ID. Python documents that monotonic reference points are not
wall-clock timestamps and that only differences are meaningful:
[Python time documentation](<https://docs.python.org/3/library/time.html#time.monotonic>).
Do not subtract browser `performance.now`, Node high-resolution time, Python
monotonic time and WSL clocks without explicitly verifying a common domain.
Even same-machine services require an identified clock contract; UTC equality
is not one.

Store separately:

- `source_wall_utc`: optional event-source timestamp.
- `observed_wall_utc`: when this collector received/observed it.
- `mono_ns` / `duration_ns`: local measurements only.
- `source_seq`, `collector_seq`, `clock_domain`, `clock_resolution_ns`.
- optional `alignment_error_ns` and calibration interval/revision.

This mirrors the distinction between Timestamp and ObservedTimestamp in the
[OpenTelemetry logs model](<https://opentelemetry.io/docs/specs/otel/logs/data-model/#field-observedtimestamp>).
A polled metric's collection time is not the precise time its counter changed.
For a request-boundary handshake, capture local send/receive brackets and remote
receive/send values. Report an alignment interval/uncertainty, never just its
midpoint as exact. Discard/recalibrate on process/clock-domain change or detected
wall-clock jump. Causal parent edges and sequence ordering remain valid when UTC
clocks disagree. Never force negative durations to look like precise positive
cross-service latency.

## 6. Minimal metadata schema and limits

Proposed record, all fields allowlisted; absent facts remain null/unknown:

```json
{
  "schema": "watson.trace/v1",
  "trace_id": "random-validated-id",
  "span_id": "random-span-id",
  "parent_span_id": null,
  "attempt_id": "opaque-attempt-id",
  "service": "gateway",
  "service_instance": "opaque-process-instance",
  "gateway_generation": "opaque-generation",
  "model_epoch": "opaque-epoch",
  "model_id": "inkling2-mini-long",
  "event": "upstream_headers_observed",
  "evidence": "observed",
  "correlation": "request",
  "source_seq": 4,
  "mono_ns": 123456789,
  "source_wall_utc": null,
  "observed_wall_utc": "2026-11-24T00:00:00Z",
  "duration_ns": null,
  "purpose": "conversation",
  "counts": {},
  "error_kind": null
}
```

IDs/times above are illustrative, not captured evidence. Bound event names,
service/model IDs, number of attributes and identifier lengths. Suggested first
budget: <=16 scalar lifecycle records/request plus one outcome and one usage
summary; <=2 KiB/record; <=64 active requests; <=512 events in a ring;
coalesce progress at <=2 Hz. Bounded nonblocking telemetry queue with explicit
`dropped_records`/`partial_trace` counters; never block inference, admission
release or model ownership on logging. Benchmark observer-on/off overhead before
claiming these limits are sufficient. Reuse the current two-second metrics
schedule rather than polling per output chunk.

Default retention: process memory only. Any opt-in local export uses bounded
rotation, private ACLs and an expiry; no stdout body capture, raw Prometheus
responses, URLs with query strings, request/response headers, project/user IDs,
exception messages or arbitrary callback dictionaries. Do not export hashes of
raw prompts as a supposed anonymization measure; low-entropy prompts can be
reidentified. Source hashes and approved configuration digests do not contain
prompt content. Metadata itself still reveals usage timing and model choices.

Do **not** repurpose DSH's Session-log OTel export as this metadata channel:
its own [installed documentation:14,66–74](<C:/Users/Admin/AppData/Local/Programs/DeepSeek%20Harness/resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-session-telemetry-otel/README.md#L66-L74>)
describes feedback-authorized export of canonical event prefixes, including
context, and a distinct capture/privacy policy. Enabling that is neither
necessary nor authorized by this tracing design.

## 7. Counts and progress: required semantics

Every count needs `representation`, `source`, `method`, `revision/epoch`, and
whether it is aggregate, per request, final or estimated.

- **Transport bytes/chunks:** measured at one interface after its serialization
  and buffering. An SSE event may cross many reads; a read may contain many
  events. Neither is a model token. A successful flush is not browser paint.
- **DSH estimate:** identifies current model metadata, output reservation,
  system/tool/history counts, estimator version and surface generation. The
  [token-meter contract:28–49](<C:/Users/Admin/AppData/Local/Programs/DeepSeek%20Harness/resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-token-meter/README.md#L28-L49>)
  uses approximate text estimation and only reuses provider usage for an
  identical request envelope. Report an estimate as such, not a tokenizer fact.
- **Compaction:** DSH's durable start/summary/replacement/end events prove a
  frontend-history transformation. Capture only event IDs/types/sequences,
  before/after scalar counts and estimator identity. Do not copy summary text.
  Headroom's own transform needs separate before/after evidence; disabled
  Kompress is not evidence that DSH did not compact.
- **Final backend accounting:** the
  [b12651 server reference](<https://github.com/ggml-org/llama.cpp/blob/b12651/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions-api>)
  distinguishes `timings.prompt_n` newly processed prompt tokens,
  `timings.cache_n` reused tokens and `predicted_n` generated tokens. Its example
  context formula is `prompt_n + cache_n + predicted_n`; do not compare only
  prompt_n with DSH's full input estimate or treat cache hits as removed context.
- **Process metrics:** backend prompt/generated counters include all requests
  in that process epoch. A delta is not automatically the current request's
  exact usage, especially with direct/bypassing clients, warmup, auxiliary
  requests, cancellation and sampling gaps. Counter reset creates a new epoch;
  it is not negative token production.
- **Reasoning/tool output:** output budget can cover tokens not displayed as
  assistant text. Keep provider-reported visible/reasoning/tool breakdowns only
  when actually supplied. Proposed OTel-compatible totals follow
  [GenAI conventions](<https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/>):
  cached input is a subset of total input and reasoning output a subset of total
  output; do not add each breakdown to its total a second time.
- **Position:** exact backend token indices require an explicit tokenizer and
  formatted-input/model identity plus a request-correlated native counter.
  Tokenizers/templates/transforms change boundaries. Without content capture,
  do not claim a bijection between pre- and post-compaction positions. At most
  expose a count interval or known backend processed-token cursor, clearly
  distinct from transport-delivered bytes and UI-visible characters.

## 8. Staged proof and acceptance gates

### P0 — truthful existing observations

Document generation/epoch boundaries, metric freshness and inferred edges.
Verify current fake observer tests cover bounded parsing, cancelled/rejected
requests and no secret retention. Do not change services. Current observations
remain useful even before cross-hop correlation exists.

### P1 — deterministic fake-chain propagation

Use local test doubles, no GPU/model or production route. Prove per-request
header mapping across gateway -> fake Headroom -> fake LiteLLM -> fake backend;
one trace with distinct spans, mapped native IDs, separate retries and explicit
missing-hop states. Inject malformed/oversized IDs, auth failures, busy409,
fragmented/coalesced SSE, split UTF-8, reasoning-only/tool-only events, absent
usage, early EOF, backpressure, cancellation, duplicate outcomes, telemetry queue
saturation, restarted counters and fake wall-clock jumps. Compare exact
request semantics with tracing disabled/enabled; no new tools/body fields,
credential leakage, extra model calls or ownership side effects.

### P2 — approved isolated real adapters

Only after separate authorization, use the already isolated loopback chain with
synthetic prompts and fixed profiles. Prove trace header survival and native-ID
mapping on **each** actual success/error/stream path; installed source alone is
not proof. Record version/build/config fingerprints and any unmapped boundary.
Compare latency, CPU/memory, bytes and semantic responses observer-on/off, with
an explicit overhead budget and bounded parser/export failures. Test one retry,
cancellation and model reload. Keep raw text outside trace artifacts.

### P3 — DSH linkage and context transformations

An explicitly approved DSH Host integration supplies request-hidden IDs through
supported seams and a separate mapping to durable request/attempt events. Test
conversation versus compaction/auxiliary calls, idle selection, metadata hot
updates, tool rounds and context shrink; preserve recorded Session truth and
captured prepared calls. Do not infer task success from HTTP200, stream EOF or
backend generation counters. Browser receipt/render timing, if needed, is a
separate instrumented client span with its own clock domain.

### P4 — optional exact backend phase/progress

Only with an upstream-supported request-correlated numeric hook or separately
reviewed engine instrumentation expose prefill/decode counts. Validate tokenizer,
cache, speculation and cancellation semantics for the exact binary. No phase
inference from GPU utilization and no speculative per-token animation when this
hook is absent. Aggregate process metrics remain a separate panel.

**Stop condition:** if IDs disappear, time domains cannot be bounded, counters
reset unexpectedly, or a producer cannot attest its scope, render
`unknown/unmapped/partial` and keep inference working. Trace completeness is an
observed property, never permission to weaken authentication or collect text.
