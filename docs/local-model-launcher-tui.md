# Watson terminal model selector

The keyboard-first UI controls the local single-active-model gateway. It opens no
browser and does not modify DSH's installed application code. Its implementation
is [the terminal module](../local_stack/tui.py); isolated coverage is in
[the selector tests](../tests/test_local_stack_tui.py).

## Open it

From the repository root, use the stack launcher to start the supervised local
stack and open the selector:

```powershell
.\scripts\windows\Start-LocalModelStack.ps1
```

To attach the selector to an already running gateway using the staged interpreter:

```powershell
.\.runtime\local-model-deps\tui-rich15.0.0\Scripts\python.exe -m local_stack ui
```

Use a real terminal, preferably Windows Terminal, for color and interactive keys.
The display adapts to narrower/shorter terminals. Redirected/noninteractive output
prints one read-only status snapshot instead of waiting for keyboard input. A
missing gateway produces an actionable startup message; the UI never secretly
starts another backend.

## Keys

| Key | Action |
| --- | --- |
| Up/Down or J/K | Select a model. Selection alone does not interrupt inference. |
| Enter or L | Ask the gateway to safely load the selected profile. |
| C | Pick a context preset or enter an aligned custom token count. |
| A | Register a GGUF using an existing approved architecture/engine profile. |
| / | Filter the model library by name or ID. Escape clears the filter. |
| R | Refresh authoritative gateway status. |
| S | Explicitly confirm `STOP` to unload only the gateway-owned model. |
| Q or Ctrl-C | Close the selector, leaving the stack and active request running. |

The dashboard shows configured context separately from the actually loaded
context. Context allocation includes **input plus output**. Historical profile
measurements are not a promise that an arbitrary new context or artifact has been
validated. The gateway rechecks resources and readiness on load. Resource figures
are sampled free VRAM, free system RAM and GPU activity, not a promise of a hard
GPU-utilization cap.

Context changes are submitted to the gateway; the next safe load/request applies
them. DSH maintains its own model metadata, so a `metadata_refresh_required`
warning means its catalog also needs refreshing through the supported registration
flow. The selector does not hand-edit DSH profile YAML or advertise a theoretical
model limit as a measured local allocation.

## Guided GGUF registration

The wizard searches only the top two directory levels of `D:\MODELS` and the
current user's Downloads folder. It excludes symlinks and projector files from
main-model discovery. A full model path can also be supplied.

1. Choose an **existing approved profile** and explicitly confirm the same model
   architecture. GGUF is a container format, not architecture compatibility proof.
2. Choose a model GGUF. The file must exist and have a `GGUF` header; partial
   download names and projectors are rejected as main models.
3. Choose a unique ID, display name and initial context. New registrations start
   unverified at no more than **8,192 tokens**; inherited validation evidence is
   cleared and the initial output budget is bounded.
4. For vision profiles, explicitly supply the projector matching the **new**
   model. The old model's projector is not assumed compatible.
5. Confirm `REGISTER`. This saves a profile through the gateway; it does not load
   the model, run inference, or demonstrate correctness.

The wizard never accepts a new executable path, raw command string, arbitrary
flags, or credentials. New engine/architecture support requires a reviewed
operator profile rather than pretending every GGUF is interchangeable. The
registered profile is still subject to the gateway's schema, engine allowlist,
resource gates and exact runtime validation.

## Safety behavior

Only one action is submitted at a time. While the gateway reports active
inference, the selector refuses load/context/register/stop operations rather than
sending an interrupting switch. This is a convenience check: authoritative
serialization and process ownership checks live in the gateway, so another
terminal, DSH session or request cannot bypass them.

Status polling and long-running control requests run outside the drawing loop.
Quitting does not kill an admitted load or an active model. Credentials are not
shown in the dashboard; model metadata is rendered as literal text rather than
Rich markup or terminal escape commands.

## Test without a model or service

```powershell
.\.runtime\local-model-deps\tui-rich15.0.0\Scripts\python.exe -B -m unittest discover -s tests -p test_local_stack_tui.py -v
```

Tests cover catalog/override merging, context limits, keyboard busy admission,
non-mutating quit/snapshot behavior, safe registration, conservative validation
reset, discovery bounds, error redaction and wide/narrow rendering.
