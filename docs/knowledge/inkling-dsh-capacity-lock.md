# Inkling: one DSH row, fixed capacity contract

## Status and boundary

The opt-in `--lock-inkling-capacity` mode in the
[registrar](<../../scripts/dsh/register_local_models.py>) prepares one managed
Inkling picker entry: **`inkling2-mini-long`, at most 150,016 combined context,
4,096 output by default, text + image**. Opt into **10,000 output** with
`--inkling-output 10000`; `--inkling-context 150016` explicitly selects the full
budget instead of preserving a valid smaller one. Registry and live gateway
must match the verified native-vision contract. The registrar does not rename
the short runtime ID, edit the registry/runtime, choose defaults, touch
credentials, restart services, or perform inference. Existing 100K fallback
rows are preserved but not implicitly created by this helper.

**Live rollout applied separately:** after explicit authorization, a removable
one-shot Host bundle used revisioned Settings to save the main row as
**150,016 / 10,000 / text + image**, add the verified `inkling2-mini-100k` fallback,
and persist the main DSH default through `agentDefaultModel.saveSelection`.
Read-back after removing the bundle verified all three. A fresh installed-DSH
ACP session passed image admission and the full Watson route. Existing explicit
session selections and old preset revisions are not silently replaced. See the
[150K rollout report](<../reports/watson-inkling-150k-2026-11-25.md#default-rollout-and-observatory-acceptance>).
The historical 100K investigation below remains provenance, not current setup.

## Why not change the context behind the existing short ID?

The current `inkling2-mini` authority means the 8K/F16 profile. The reviewed long
profile uses another engine, Q4 KV and a smaller microbatch. Calling the short
ID a 100K model would not change what the gateway loads.

DSH 0.2.2-rc.5's pi-ai adapter resolves normal calls from configured model
metadata; endpoint discovery is a configuration-time operation, not automatic
capacity synchronization. The model-switch compaction guard compares provider
and model IDs, so shrinking the context behind the same ID bypasses that switch
guard. Saved 100K metadata with an 8K runtime is unsafe. A future stable logical
alias is possible only after runtime authority is pinned/enforced consistently;
that is a separate implementation and qualification scope.

The small profile also failed the recorded standard DSH tool inventory: a
9,524-token initial request did not fit 8,192 context. The qualified 100K profile
passed a separate short standard-tool headless smoke, not a full Desktop
long-context reliability qualification. See the
[qualification report](<../reports/inkling2-frontier-2026-11-24.md#L59-L65>).

## Validation and preservation contract

The lock mode always reads authenticated gateway **model metadata**, never an
inference request. It requires:

- Exactly one `inkling2-mini-long` registry candidate and gateway record.
- Registry minimum, maximum and selected context all **150016**.
- Both records: presets exactly `[150016]`, verified flag `true`, exactly
  `[text,image]` input, and serving output ceiling **10000**. The registry must
  declare a GGUF projector. Gateway context must match **150016**.
- Existing Watson Local uses OpenAI Chat Completions and the same inspected
  loopback endpoint.
- Exactly one live `llm-pi-ai` settings namespace and one
  `agent-default-model` namespace, each carrying a revision.
- Actual live default must not point at the retired short ID. **No flag
  overrides this guard.** A local default absent from the proposed catalog also
  blocks migration.
- Removing a saved short row additionally requires `--ack-retire-short`, an
  operator assertion that active and saved sessions no longer depend on it.
  The helper does not claim to inspect or migrate every Session automatically.

Only the `providers/watson-local/models` settings leaf is changed. Saluki and
all unrelated model rows remain byte-for-byte JSON-value equivalent, including
their other public settings. Other provider fields, existing `apiKeyEnv`
(including the native `WATSON_LOCAL_API_KEY` reference), hosted providers,
credentials and defaults are untouched. For an existing long row, the helper
preserves its display name and other fields and changes only `contextWindow`,
`maxTokens` and verified `input`. A smaller valid context stays smaller unless
`--inkling-context 150016` is explicit; an oversized value is clamped at 150,016,
and a context too small for the chosen cap is refused.

The helper rereads live default and adapter revisions immediately before the
write, sends the adapter's expected revision, then verifies model rows and
unchanged default after save. **There is no cross-namespace transaction in this
API.** The operator must exclude concurrent session selection/default/settings
writers during migration. Do not run while users are changing models. A
post-save verification failure explicitly reports that a write may have saved;
it does not silently overwrite newer settings to roll back.

## Read-only proposal

```powershell
python -B .\scripts\dsh\register_local_models.py `
  --lock-inkling-capacity --dry-run --registry .\config\models.json
```

The gateway must be available. Its inference token is read from
`LOCAL_STACK_API_KEY` or the existing private inference-token file. Control
credentials are never used. Without legitimate DSH owner authentication this
returns a **blocked candidate-only proposal**, not verified settings or an
applied migration. It does not inspect profile YAML or DSH signing secrets.

An explicitly exported, redacted `settings/describe` JSON document may be passed
with `--settings-view <path>` for offline planning. Its plan remains blocked
because an offline snapshot cannot authorize a live write. It cannot be used
with `--apply`; no DSH authentication exchange is attempted for offline plans.

## Safe native migration sequence

1. Stop new local DSH turns and exclude concurrent model/default/settings
   changes. Record the current provider rows, Session selections and default.
2. In **Settings → Models → Watson Local → Edit**, fetch available models and
   **add the long row while retaining the short row**. Set long context
   **150016** or less, output **4096** for everyday use or **10000** for long
   responses, input **Text + Image**, no advertised reasoning levels.
   Keep the existing endpoint and API-key reference. Save.
3. Review every active/saved Session that should migrate. Choose the long
   route explicitly in those Sessions, or retain compatibility until they are
   migrated. A history made for another route may need compaction. Stock GUI
   selection also saves the deployment default asynchronously; wait for that
   save and verify its actual value before retiring short.
4. Configure an appropriate exact-model compaction policy separately. Default
   compaction headroom/summary cap is 65,536, which is greater than even the
   long route's 10,000 serving output ceiling. The 8K route cannot fit the
   default headroom, and the long route still needs a bounded summary output cap. This helper does not mutate compaction
   settings. Validate the chosen policy and history fit before user work.
5. Once the actual default does not reference short, and no remaining Sessions
   require it, review the authenticated lock dry-run with
   `--ack-retire-short`. Alternatively remove short manually in that same native
   provider editor. Do not remove a referenced ID merely to hide duplication.
6. Verify Watson Local has one managed Inkling row, exact context/output,
   preserved Saluki and API-key reference, and unchanged intended default. Open
   the picker only to inspect; do not make an accidental selection.

If the operator later supplies a legitimate DSH launch URL or cookie through
private environment variables, the supported API apply command is:

```powershell
python -B .\scripts\dsh\register_local_models.py `
  --lock-inkling-capacity --ack-retire-short --apply `
  --registry .\config\models.json --dsh-url http://127.0.0.1:64938
```

Pass the exact current GUI origin after any normal restart. A clean URL alone
is not authentication. This command still refuses a short default and never
changes it. Ordinary `--apply` without the new flag retains its legacy behavior;
**do not use that older two-model registration mode after this migration**,
because its managed catalog contains short Inkling rather than the long ID.

## Historical 100K verification and known mismatch

During diagnosis the Desktop originally advertised `inkling2-mini` (the **8K backend alias**) as **102,400 / 4,096**. The owner then switched to `inkling2-mini-long`; the session trajectory and saved provider confirmed the alias change, but the incorrect **102,400 / 4,096** remained. Two subsequent requests reached the gateway and received its explicit output-budget 400 before any inference. At the old 2,048 ceiling, validation rejected 4,096. A separate transport error occurred during a maintenance/test interval and is not evidence that the model alias was wrong. With the new serving policy active, the fixed route is `inkling2-mini-long` at **100,096 context / 4,096 recommended output**, or **10,000 output** if explicitly selected. Existing DSH settings do not automatically increase. The backend allocation is unchanged when Desktop metadata is edited.

Two independent temporary DSH headless sessions, in empty working directories and with read-only tool permissions, completed an exact-text canary against the already-resident 100K service. One advertised **100,096** context (23.22 s wall), the other **32,768** (8.26 s); both reserved at most **2,048** output and produced no tool calls. This verifies the serving alias with both DSH budgets for small clean sessions, not active Desktop profile persistence or arbitrary long session histories. Never treat a GUI control click as an applied migration without checking saved settings and actual default.

## Rollback evidence

Before a lock write, the CLI exclusively creates a uniquely named public
snapshot under `.runtime/local-stack`. It records prior public model metadata,
prior adapter revision and proposed IDs; no provider headers, custom model
fields, credential values or default payload are exported. Existing snapshot
files are never overwritten.

The snapshot is **rollback evidence, not a blind whole-profile restore**.
For rollback, reread live settings/revision, preserve current unrelated rows and
custom fields, add the prior short public row through the native Models editor
(or an explicitly reviewed path mutation), then migrate any default/Sessions
back intentionally. Do not replace a whole namespace or replay an old revision
unconditionally. Removed managed-row custom fields require the owner's existing
configuration backup; the sanitized snapshot deliberately does not export them.

## Tests and installed-source references

[Tests](<../../tests/test_dsh_local_registration.py>) use fake RPC and fake
metadata only. They cover immutable candidate bounds, stale-default refusal
with acknowledgement, revision/default races before write, endpoint mismatch,
retirement confirmation, idempotence, exact unrelated-row preservation,
credential/default isolation, private-plan filtering, snapshots and blocked
unauthenticated/offline planning.

```powershell
python -B -m unittest discover -s tests -p test_dsh_local_registration.py -v
```

Relevant DSH installed sources (inside the Desktop ASAR):

- `dsh-llm-pi-ai/src/adapter.ts:346-399`: configured model resolution and captured
  prepared calls; no endpoint context refresh.
- `dsh-llm-pi-ai/src/index.ts:299-308`: discovery is configuration-time.
- `dsh-compaction-basic/src/index.ts:183-203`: model-switch gate compares IDs.
- `dsh-compaction-basic/src/config.ts:75-108,172-203`: headroom, summary cap,
  effective output reservation and pressure validation.
- `dsh-api-settings-controller/src/index.ts:97-157`: redacted describe and
  revision-checked path mutation.
- `dsh-agent-default-model/src/index.ts:51-55,82-93`: live default schema and
  serialized profile-backed saves.
