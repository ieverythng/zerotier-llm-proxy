# Saluki and Inkling in the running DSH model picker

## Scope and status

The [registrar](<../scripts/dsh/register_local_models.py>) targets the installed
DSH **0.2.2-rc.5** supported Settings/Credentials Remote APIs. It does not edit
DSH YAML, install plugins, select a session model, change deployment defaults,
start models, or send inference requests. Registration is separate from model
qualification. A successful dry-run is **not** persistent registration.

The target provider is `watson-local`, OpenAI Chat Completions, with endpoint
`http://127.0.0.1:4141/v1`. The gateway owns safe single-model switching before
inference. The model IDs are `saluki27b` and `inkling2-mini`.

Only the local provider's managed fields are set. Existing hosted providers,
other local-provider fields, the user default, and the active session are not
modified. Every settings mutation carries the revision just read; conflicts
fail instead of replacing someone else's edits. API-key values are stored only
through DSH `credentials/set`, referenced as `LOCAL_STACK_API_KEY` in config.
The control token is never used as an inference or DSH credential.

## Preview without touching DSH

From the repository root, using an existing Python interpreter:

```powershell
python -B .\scripts\dsh\register_local_models.py --registry .\config\models.json --dry-run
```

The default mode is dry-run. It does not read token files or contact DSH.
The registry must have integer `schema_version: 1` (or `schema: 1`) and a
`models` array. Each of the two entries needs `id`, `name`, integer `context`,
`max_output_tokens`, and `input`. Saluki image input is advertised only when
its `input` declares image and its `mmproj` is an existing file. Inkling remains
text-only until separately qualified. The initial entries advertise no
selectable reasoning levels; server-side model profile controls remain separate.

DSH's unknown-model defaults are 1M context and 128K output, which are unsafe
for these profiles. The registrar always sets explicit capacities. Context is
**input plus output**, and the output budget must leave room for input.
Names include the capacity and `provisional` unless metadata explicitly sets
`context_verified: true`; a model listing alone is not verification.

## Supported authenticated apply

The clean GUI origin after the current resume is `http://127.0.0.1:64938`.
A later restart can change it; pass the exact current `--dsh-url`.

Every DSH RPC requires authentication. Loopback is not permission. DSH's HTTP
carrier does **not** accept an Authorization-header token. The registrar uses
one of these explicitly supplied owner credentials:

1. `DSH_LAUNCH_URL`: the original, authorized DSH root launch URL containing its
   launch token. It exchanges that token through the documented root GET for
   an authority-bound browser cookie. It never follows redirects or prints the
   launch URL. The cookie lives only in process memory.
2. `DSH_AUTH_COOKIE`: an existing authorized cookie supplied by the owner.
   No browser cookie extraction or credential-store signing-secret access is
   implemented. The cookie must match the exact host and port.

Supply either through a private environment/owner bootstrap, not literal CLI
arguments, checked-in files, shell history, or chat. Without one, apply fails
with the native GUI instructions below. Do not fabricate cookies or weaken DSH
authentication to make registration succeed.

The inference key comes from `LOCAL_STACK_API_KEY` or the gateway's existing
`.runtime/local-stack/inference.token`. This is distinct from DSH's owner
authentication and from the control token. Root bootstrap owns key provisioning.

After the parent approves live apply and supplies legitimate DSH authentication:

```powershell
python -B .\scripts\dsh\register_local_models.py `
  --registry .\config\models.json --dsh-url http://127.0.0.1:64938 --apply
```

The script calls `settings/describe`, applies only changed local leaf fields
using `settings/mutate`, saves the inference credential, rereads and verifies
managed fields, then checks credential presence without reading its value.
It prints public success facts only. Every CLI apply creates a unique public
managed-field snapshot under `.runtime/local-stack`; it excludes headers,
foreign providers, and credentials. Existing snapshot paths are never overwritten.
Credential storage failure after settings save is reported as partial success;
rerun after repair. No default or hosted-provider rollback is needed because
those fields were never touched.

## Refresh after a context change

With the gateway running and the same authorized DSH authentication available:

```powershell
python -B .\scripts\dsh\register_local_models.py `
  --registry .\config\models.json --use-gateway-metadata --dry-run
# Review, then use --apply instead of --dry-run.
```

`GET /v1/models` is authenticated with the inference token. The registrar reads
normalized `context_window`, `max_output_tokens`, and `context_verified` fields.
The gateway can merge runtime context overrides into that catalog. An explicit
verification flag should mean exact owned runtime/model/context readiness was
checked, not just that a configuration file contains a number.

Until refresh is actually saved, DSH metadata can remain stale; the gateway
must enforce its own effective context regardless. Do not wire automatic apply
callbacks without a legitimately authenticated owner transport. Configuration
refresh affects later prepared calls, not a running step's captured metadata.

## Native GUI fallback when owner RPC authentication is unavailable

In this same running DSH GUI:

1. Open **Settings → Models → Add model provider → Custom model API**.
2. Provider ID: `watson-local`; display name: `Watson Local`.
3. Protocol: **OpenAI Chat Completions**; endpoint:
   `http://127.0.0.1:4141/v1`.
4. Put the private inference key from `.runtime/local-stack/inference.token`
   into the native masked API-key field. Do not paste it into chat. The native
   page stores a credential reference, never the secret value in YAML.
5. Click **Fetch available models**, check `saluki27b` and `inkling2-mini`, then
   click **Update list**. DSH rc.5 imports the gateway's `contextWindow` and
   `maxTokens`, but its discovery parser does not import modalities. In the
   model options, verify the settings below and add the context to each name.
6. Click **Create provider**. Fetch and Update list only update a draft. Confirm
   **Watson Local** and both models appear in the picker without selecting either.

| Model ID | Display name | Context | Max output | Input |
| --- | --- | ---: | ---: | --- |
| `saluki27b` | Underdog Saluki 27B · 150,016 ctx | 150,016 | 4,096 | Text, image |
| `inkling2-mini` | Inkling 2 Mini Mix-12G · 8,192 ctx | 8,192 | 2,048 | Text |

Keep selectable reasoning levels disabled initially. If a context override is
active, use the value from `--use-gateway-metadata --dry-run` instead of the
baseline shown above.

The native page may derive `WATSON_LOCAL_API_KEY` instead of the scripted
`LOCAL_STACK_API_KEY` reference; either is valid if it stores the same gateway
inference key. Do not later replace a working native reference blindly: review
the scripted plan first. Both references are separate from the control token.

The composer model selector shows model names/efforts, not raw capacity fields;
capacity is visible in Settings model options and in the registrar's truthful
name suffix. Selecting a model is a separate human action. DSH's stock GUI
selection also asynchronously saves the chosen deployment default, so merely
registering models must not call `session/selectModel`.

## Version-specific source evidence

These paths are inside the installed Desktop ASAR; use DSH's file-read tool or
its ASAR-aware Electron runtime, not ordinary native file search:

- `@deepseek-ai/dsh-api-settings-controller/src/index.ts:97-157`:
  redacted reads, revision-checked update/replace/path mutation.
- `@deepseek-ai/dsh-api-settings-controller/src/credentials.ts:83-105`:
  presence-only description and write-only key storage.
- `@deepseek-ai/dsh-client-connection/README.md:39-51`:
  root-token cookie exchange, authority binding, no Authorization auth.
- `@deepseek-ai/dsh-client-connection/src/rpc-host.ts:305-343` and
  `@deepseek-ai/dsh-api-gateway/src/client/index.ts:458`:
  POST `/api/<namespace>/<method>`, client-request envelope with `payload.args`.
- `@deepseek-ai/dsh-llm-pi-ai/src/catalog.ts:603-637`:
  combined context, configured output defaults, modalities and reasoning schema.
- `@deepseek-ai/dsh-api-session-controller/src/commands.ts:166-185`:
  picker selection records next-step route and saves default; no awaited
  model-loader hook. Request-time gateway gating is mandatory for safety.

## Tests

[Unit tests](<../tests/test_dsh_local_registration.py>) use fake RPC/openers only:

```powershell
python -B -m unittest discover -s tests -p test_dsh_local_registration.py -v
```

They cover explicit context, provisional/verified labels, capabilities,
loopback/auth boundaries, revision conflicts, targeted writes, idempotence,
secret-free snapshots, redacted errors, and dry-run isolation. Passing them is
not proof that live DSH authentication or GPU/model qualification passed.
