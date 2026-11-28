# Terminal request flow: observed facts, not simulated execution

The persistent controller keeps its existing model library, memory evidence and
keyboard controls. A route strip adds four boxes on roomy terminals:

```text
Gateway → Headroom → LiteLLM → llama.cpp
```

On compact terminals it becomes one line, keeping start/stop/detach controls
visible. Press **E** for the full request-flow evidence alongside memory and
benchmark results. The renderer is in [the terminal module](../local_stack/tui.py);
its metadata comes from [request-flow instrumentation](../local_stack/request_flow.py).

**This display does not observe, perform, or claim DSH compaction.** Proxy routing
also does not prove that Headroom compressed anything.

## What the boxes mean

| Box | Evidence and interpretation |
| --- | --- |
| Gateway | **OBS**: an instrumented gateway stage, such as load/validate, waiting upstream, received response headers, forwarded streaming bytes, or observed actual answer text. These are gateway lifecycle/transport observations—not GPU phases. |
| Headroom | **INFER**: an inference request is in flight through the configured proxy route. No per-hop execution, compression progress, or processing duration is measured here. |
| LiteLLM | **INFER**: the same configured route inference as Headroom. Both may pulse together; the UI does not pretend to observe sequential traversal between them. |
| llama.cpp | **OBS** only when fresh authenticated metrics from the still-owned backend report idle/processing. GPU utilization does not determine this state. The display does not infer prefill versus decode from these metrics. |

A pulse means **the last fresh observation reports activity**. It is not a
completion percentage, throughput measurement, heartbeat guarantee, or promise
that output is forthcoming. Pulses use local monotonic time for smooth drawing;
the passage of time never advances the reported stage.

Proxy pulses are explicitly marked inferred. Idle, unknown, offline, stale and
bypassed nodes do not pulse. Direct diagnostic routing bypasses Headroom/LiteLLM;
native benchmark requests bypass them too, even if ordinary chat uses the full
proxy route. Missing provenance remains **unknown**, not guessed activity.

## Counter scopes

Gateway counters apply to the current gateway process generation and only the
instrumented **Chat Completions and benchmark POSTs**:

- **in / ingress:** observed request attempts;
- **accepted:** admitted work;
- **active:** admitted work still active, not queued clients;
- **done / end / completed:** gateway transport completion;
- **fail / rejected / cancelled:** the corresponding observed outcomes.

Completion does **not** establish correct reasoning, successful tool execution,
or successful completion of the user's task. A restarted gateway starts a new
counter generation; the terminal does not add counters across generations.
Status polling does not count as model traffic. Missing counters display a dash,
not an invented zero.

Backend prompt/generated token totals are cumulative within the backend's own
owned-worker epoch. They are not the active request's token budget, a live
context-window occupancy measurement, or a benchmark rate. They may reset when
the model worker changes. The evidence panel identifies the backend epoch.

The full evidence view can also show:

- current request kind and model alias;
- observed elapsed time and forwarded-byte count;
- first actual nonempty answer-text observation, if present;
- last outcome, duration and classified error kind;
- source timestamps and gateway generation.

Headers, arbitrary bytes, reasoning-only deltas and tool calls do not count as
first actual answer text. That field may legitimately remain unavailable.
Payloads, prompts, generated text and authorization values are not displayed by
this metadata view.

## Freshness and graceful degradation

The terminal timestamps each received status observation using a local monotonic
clock. If it has not received another observation for more than **six seconds**,
request-flow animation freezes and the previous counters are marked as a last
sample. Backend metrics have their own independent stale flag; stale backend
metrics cannot animate the llama.cpp box even while gateway status remains fresh.

While offline or ownership cannot be verified, the strip shows offline/unavailable
instead of retaining an active-looking route. A legacy gateway without the flow
contract displays unknown fields and leaves the rest of the controller usable.
The UI does not issue extra model requests, read raw metrics, restart anything,
or change admission policy in order to populate the strip.

On narrow/short screens:

- `GW`, `HR`, `LL` abbreviate the boxes;
- `~inferred` explicitly qualifies inferred proxy hops;
- `×bypass` marks bypassed hops;
- `?` marks unknown/unavailable evidence;
- **E** exposes details omitted from the one-line summary.

## Offline verification

[Terminal tests](../tests/test_local_stack_tui.py) use synthetic metadata and
injected clocks. They cover observed versus inferred phases, direct/benchmark
bypass, missing/unknown/stale evidence, independent backend staleness,
monotonic-only animation, generation resets, first-answer-text labeling and
compact layout. No model, proxy, or GPU is launched by those tests.

A live synthetic streaming smoke on the ordinary 100,096-context Inkling route also observed `forward_wait` and `streaming`, fresh backend `processing`, and explicitly inferred proxy activity. Ingress, accepted, and transport-completed each increased by one, and active returned to zero. The gateway observed first actual answer text after 22.64 seconds in this smoke; it was a telemetry check during a build, not a prefill benchmark.

```powershell
.\.runtime\local-model-deps\tui-rich15.0.0\Scripts\python.exe -B -m unittest discover -s tests -p test_local_stack_tui.py -v
```
