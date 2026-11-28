# Watson persistent terminal controller

The Rich terminal opens **before** the inference stack. Pick a registered model
and context, explicitly start or attach, stop the whole stack, and start again
without closing the app. No browser is needed. The terminal is independent of
its workload: closing it always detaches, never shuts down inference.

The implementation is [the terminal module](../local_stack/tui.py), using the
[verified lifecycle module](../local_stack/lifecycle.py). Process ownership,
startup ordering, admission and shutdown verification belong to the lifecycle
and gateway modules—not to keyboard handlers.

## Open it

```powershell
.\scripts\windows\Start-LocalModelStack.ps1
```

The default command only opens the controller; it does not preload a model or
start proxies. The window remains usable while stopped, starting, running,
stopping, or unable to verify ownership. Profile/context choices are possible
while offline. The app does not guess that an unverified listener belongs to it.

Existing automation flags remain available:

```powershell
# Start/attach the supervised stack without opening a terminal UI:
.\scripts\windows\Start-LocalModelStack.ps1 -NoUi

# Foreground supervision, retaining the existing explicit operator mode:
.\scripts\windows\Start-LocalModelStack.ps1 -Serve

# Open the controller for the explicitly selected direct diagnostic route:
.\scripts\windows\Start-LocalModelStack.ps1 -Direct
```

Direct mode omits the proxies; it is diagnostic, not equivalent to the full
Headroom/LiteLLM route. Attaching to a stack with a different route mode must
refuse rather than silently restart it.

To open the controller directly with the staged interpreter:

```powershell
.\.runtime\local-model-deps\tui-rich15.0.0\Scripts\python.exe -m local_stack ui
```

Use a real terminal, preferably Windows Terminal, for interactive keys. The UI
adapts to narrow/short screens. Redirected output, or `ui --snapshot`, prints a
read-only observation without starting anything. Read-only network observations
have short timeouts, and even the first observation runs outside the interactive
drawing loop. A slow request does not prevent detaching.

## Keys and normal flow

| Key | Action |
| --- | --- |
| Up/Down or J/K | Select a model. No runtime change is made. |
| C | Select a preset/custom context locally; apply later with Enter. |
| Enter or L | Explicitly start/attach the owned stack and safely load the selected model/context, or swap while idle. |
| S | Confirm `STOP` to stop the **entire owned stack**; the app stays open. |
| B | Choose an exclusive, bounded **Inkling-only** synthetic benchmark. |
| E | Open resource evidence and the last benchmark's full measured results. |
| H | Toggle the full CPU, GPU, RAM, and VRAM charts; the overview keeps compact meters. |
| Z | Cycle the visible history: 2, 5, or 15 minutes. |
| ? | Show all controls and which ones are read-only. |
| A | Register a GGUF using an approved architecture/engine profile while connected. |
| / | Filter the model library. Escape clears the filter. |
| R | Refresh authoritative lifecycle state. |
| Q or Ctrl-C | Detach the terminal; leave stack and admitted work running. |

Selecting a model/context never schedules a later switch. If an inference,
stream, benchmark, start, or stop is busy, a conflicting action **refuses**. It
is not queued and will not surprise you by executing later. Retry explicitly
when idle. The gateway remains authoritative when another terminal or DSH
session races with this window.

A verified full stop means the lifecycle module observed the owned processes
and listeners disappear. A lost response, changed supervisor generation,
unknown listener, or timeout produces **uncertain/refused**, not a false success.
No keyboard handler kills processes or replaces listeners.

The dashboard separates the **selected** context from the **loaded** context.
Allocation includes input plus output. Changing allocation does not transfer
measurement evidence or demonstrate model quality. Historical profile/context
measurements are not new readiness evidence. DSH model metadata is independently
persisted and may need refreshing after an applied context change.

## Live control panel

The regular dashboard keeps compact system CPU, GPU, RAM and VRAM meters
visible alongside the model library. Press **H** for a dedicated chart view
and **Z** to choose a 2-, 5-, or 15-minute window. Two minutes is the initial
zoom level, not a lease or a sampling timeout. Both views share the same
bounded in-memory history; nothing is written to disk. RAM and VRAM percentages
use measured totals, not model footprint estimates. CPU activity is averaged
over the recorded interval. Missing, stale, and disconnected samples remain
gaps, not zeros. Wide terminals show filled history charts with sample age;
narrow ones show compact sparklines. Flow animation only runs for fresh active
observations, not for idle or stale data. Keys are handled before repaint, with
a 20 ms input-check interval; status remains on a separate two-second cadence.
A faster paint is not an invented telemetry sample.

During a verified stack stop/start or a temporary gateway outage, the terminal
collects read-only **SYSTEM ONLY** utilization in its background observer. It
does not claim a resident model or flow from those gauges. It automatically
reattaches to a newly verified supervisor on a later successful poll, without
changing selection or launching a model. A native experiment can publish a
bounded identity/heartbeat record; the terminal displays its **VERIFIED SCRATCH**
advisory separately and read-only. Stale, recycled, or unidentified worker
records are not adopted as controllable models. Reopen the terminal after a
code update; an old Python process does not receive live source patches.

The gateway lane shares the utilization charts' time axis. A marker means a
particular stage was **seen on a status poll**, not that it began at that instant
or caused a spike. Polls can miss brief stages. The [request-flow view](<local-model-request-flow.md>)
stays visible in compact form. Its downstream activity is inferred unless a
module directly reports it.
The [trace synchronization design](<architecture/watson-trace-synchronization.md>)
explains how to prove request-level handoffs before displaying them as observed.
Neither feature changes DSH context or reports an unobserved token position.

## One Inkling row, explicit context profiles

When the known Inkling profiles reference the **same normalized GGUF artifact**,
Watson Observatory shows one family row. The runtime profiles remain separate:

- `inkling2-mini-long`: the selected **150,016-token text + vision default**,
  preselected on a fresh terminal but never launched automatically. It carries
  the explicitly approved 512 MiB VRAM / 1.25 GiB RAM experimental policy.
- `inkling2-mini-100k`: the **100,096-token text + vision fallback**, keeping the
  normal 2,048 MiB VRAM / 2 GiB RAM policy.
- `inkling2-mini`: the existing short profile, with unchanged 4,096/8,192 presets
  and approved custom range. It does not inherit vision or experimental policy.

Press **C** on the Inkling row to choose one of those existing context profiles.
For a custom allocation, first choose the approved runtime profile; the input
must stay inside that profile's original minimum/maximum. The fixed long profile
cannot be widened or treated as a general 8K-to-150K range. Choosing a context
only changes this terminal's pending selection. Enter applies the exact chosen
alias/context through the existing safe lifecycle; B uses the same exact variant
for its supported benchmark. No registry, default, or DSH provider entry is
silently changed.

The dashboard separately identifies **selected alias/context** and **loaded
alias/context**. The grouped row's loaded marker means one family variant is
resident; it does not claim that a pending short profile is already loaded when
the long variant is active. Memory estimates and measurements follow the actual
selected alias/context, not the shared display name. Short-profile evidence is
never relabeled as 100K evidence. The previously selected variant survives moving
to Saluki and back within the same terminal session.

Search recognizes either underlying alias and profile name, but searching for
“100K” does not opt into it. The A registration wizard still uses the complete,
ungrouped, approved profiles. If an alias is missing, either artifact path is
unknown, or paths differ, no grouping occurs. Serving aliases remain available
unchanged for DSH/runtime compatibility. The request-flow strip is unaffected;
it reports the actual active request metadata.

## Honest memory and performance evidence

The display intentionally separates:

- **Live system resources:** the gateway's telemetry sample of free system RAM,
  free VRAM and GPU activity. Cached samples are marked **stale**, and missing
  freshness is marked unavailable. Offline views do not retain old numbers as
  live. These are system-wide observations, not the selected model's usage.
- **Estimated startup VRAM allowance:** a configured admission allowance. It is
  not a measured allocation, a context-specific prediction, or a guarantee of fit.
- **Measured historical memory:** observed minimum free RAM/VRAM during a matching
  model/context benchmark. Persisted evidence is matched by the benchmark module
  to the launch profile/artifacts. A different context is not silently assigned
  the old measurement.
- **Measured performance:** actual prompt/output tokens, prefill tok/s, decode
  tok/s, TTFT, measurement time and quality result. No demonstration throughput
  is hardcoded into the controller.

Press **B** for `quick`, `near-context`, or **3: `vision-quick`** on the eligible
native-vision Inkling alias. The latter combines a short timing run with seven
synthetic text/image checks and sanitized per-case results. It is not a public
leaderboard score or a near-150K stress button. Press **V** for read-only,
report-backed high-context evidence, even while the route is offline.
Optimization and these benchmark
controls are **Inkling-only**; the terminal lifecycle works for all registered
models. Quick benchmarks are supported at approved allocations. Near-context
benchmarks are available only at configured context **at most 8,192 tokens**;
higher-context qualification is separate work, not an implied capability of this
button. The gateway reserves the model exclusively and enforces AC power,
resource floors and bounded runtime. Synthetic performance tests and natural-EOS
quality checks are reported distinctly. There are no arbitrary prompt or shell
command fields, no direct benchmark subprocess launched by the UI, and no
silent benchmark retry. The selected model remains resident after a successful
benchmark.

## Selected 150K vision profile and 100K fallback

`inkling2-mini-long` is the explicitly chosen **150,016-token text + vision**
default. It uses stock b12651, Q4_0 KV, batch 128/microbatch 32 and no context
shifting. The pinned official Mini Q8 projector runs on CPU; the decoder and KV
remain on GPU. Its separate trusted experimental policy uses **512 MiB free
VRAM / 1.25 GiB available RAM**, while startup still requires 3 GiB RAM. These
are admission/readiness checks, not continuous cancellation of ordinary requests.
The dedicated benchmark and qualification runners separately enforce their
resource guards while testing. Saluki and normal global reserves are unchanged.

The matched 150K mixed test handled **139,742 actual input tokens**, correctly
recalled dispersed text markers and digits visible only in an image, and stopped
naturally. Minimum free VRAM was **1,343 MiB**. The complete
[qualification report](<reports/watson-inkling-150k-2026-11-25.md>) distinguishes
long-input timing, tiny-image capacity probes, routed DSH checks and limits.
200K allocation passed only tiny-image checks and is **not** the selected default.

The main profile defaults to **4,096 output tokens if omitted**, with a **10,000
explicit ceiling**. Input, images, history, tools and output share the 150,016
allocation; reserving 10K leaves at most 140,016 formatted input tokens before
additional margin. Historical 10K generation was verified at 100K allocation;
the 150K mixed recall test did not force a 10K answer. Long cold prefill can take
about nine minutes, so short-input quick-check rates do not predict that latency.

`inkling2-mini-100k` retains the proven 100,096 allocation, native vision and
same output policy, using the normal **2,048 MiB / 2 GiB** reserve. **C** selects
this explicit fallback. A live round trip loaded it under its normal reserve
and then restored 150K, keeping the route and process-ownership checks intact.

The authorized DSH rollout saved the main row as **150,016 / 10,000 / text +
image**, added the verified 100K fallback, and persisted the main provider/alias
as the default. New sessions without an explicit selection use that default;
existing explicit session selections and old preset revisions remain unchanged.
A fresh installed-DSH ACP session passed native image delivery through all
proxies. Reopen Observatory to load changed Python UI code; use a fresh Standard
DSH session for the new Inkling compaction policy. Ordinary terminal selection
does not silently edit DSH settings. See the
[DSH capacity guide](<knowledge/inkling-dsh-capacity-lock.md>).

## Watson Local provider timeouts

Long, uncached prefill can exceed the old five-minute idle limit. In DSH
0.2.2-rc.5, `timeoutMs` and `streamIdleTimeoutMs` belong to the existing
`watson-local` provider, not individual model rows. The aligned value is
**1,200,000 ms** for both, covering Inkling and Saluki on that provider. This
applies to new requests; it does not extend an in-flight request.

The isolated gateway, Headroom OpenAI route, and LiteLLM request and stream
limits are configured for **1,200 seconds**. Connection, upload, retry, and
legacy stack settings are not changed. These are request or idle/read limits
at their respective layers, not one end-to-end wall-clock deadline.

Editing startup configuration does not update an already-running proxy.
Activate it in a maintenance window: check that no Watson turn or backend
request is active, verify safe reload headroom, and restart only the owned
Watson stack. Restore its previous model and context. Confirm Headroom's
loopback `/settings/schema` reports `request_timeout: 1200` and DSH's provider
reports both 1,200,000 ms values. A short canary proves the route, not a cold
near-window request surviving beyond the old limit. After a client timeout,
wait for Watson to become idle before retrying; the original request may still
hold admission and cause HTTP 409. More time does not fix a truncated
compaction summary or an output-cap rejection.

## Guided GGUF registration

The wizard searches the top two directory levels of `D:\MODELS` and Downloads;
it does not recursively scan drives or follow symlinks. Projectors are excluded
from main-model discovery. A full GGUF path can also be supplied.

1. Choose an approved architecture profile and confirm **MATCH**. The wizard
   hides profiles that cannot fit the provisional 8,192-token ceiling and shows
   the valid context range before you enter file details.
   GGUF format alone does not prove architecture compatibility.
2. Choose an existing main model with a `GGUF` header. Partial download names and
   image projectors are rejected as main models.
3. Choose a unique ID, display name and provisional context. New artifacts cannot
   inherit prior validation evidence and initially cap at **8,192 tokens**.
   A profile whose reviewed minimum exceeds that cap needs an operator-reviewed
   registration rather than weakening its constraints.
4. For a vision profile, explicitly supply the projector matching the **new**
   model. The old projector is not presumed compatible.
5. Confirm **REGISTER**. Registration saves through the gateway; it does not load
   the model, run inference, or prove correctness.

The wizard never accepts a new executable path, arbitrary flags, command strings
or credentials. Engine trust and schema checks stay in the gateway.

## Simulated tests without GPU or live proxies

```powershell
.\.runtime\local-model-deps\tui-rich15.0.0\Scripts\python.exe -B -m unittest discover -s tests -p test_local_stack_tui.py -v
```

[The selector tests](../tests/test_local_stack_tui.py) cover offline selection,
start/stop/restart delegation, stable captured selections, busy refusal without
scheduling, detach during slow observation, uncertain shutdown, explicit CLI
route forwarding, resource provenance, benchmark-only gateway delegation, safe
registration, and compact/wide rendering. They do not launch a real model.
