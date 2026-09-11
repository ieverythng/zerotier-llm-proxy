# Codex local-model integration contract

Date: 2026-09-12

## Outcome

Watson can be used by Codex without a Responses translation bridge. The verified
route is Codex -> LiteLLM -> llama.cpp, locally or across ZeroTier. The desktop
app uses the same model catalog, but selects the endpoint through the top-level
`openai_base_url` API-mode setting.

## Root cause of the desktop and tool failures

The first Watson catalog generator cloned the highest-priority bundled model.
That entry was an OpenAI-hosted Responses-Lite model and carried three unsuitable
properties into the local Qwen entry:

- `supported_in_api=false` made the desktop app reject Qwen3.8 before sending a
  request when Codex was authenticated with a ChatGPT account.
- `use_responses_lite=true` moved tool declarations into an
  `additional_tools` input item.
- `tool_mode=code_mode_only` described a hosted execution contract rather than
  the standard Responses tools accepted by local servers.

Both the installed and current upstream llama.cpp Responses converters reject
`additional_tools`. Patching the converter or filtering request history would
hide the catalog error and create another transport dialect. The correct fix is
to generate Watson from a bundled standard-Responses entry.

## Required invariants

Every generated Watson catalog entry must satisfy all of these conditions:

1. `slug` is `qwen3.8`.
2. `supported_in_api` is `true`.
3. `use_responses_lite` is `false`.
4. `tool_mode` is unset/null unless a future local server explicitly implements
   that contract.
5. `context_window` and `max_context_window` equal live llama.cpp `meta.n_ctx`.
6. The CLI profile has `wire_api="responses"` and points to the intended local
   or ZeroTier LiteLLM endpoint.
7. Desktop mode sets top-level `openai_base_url` and removes top-level
   `model_provider`.

Project-level `.codex/config.toml` cannot define or override model providers, so
the installer writes the provider to the user's Codex config and writes the
named CLI profile to `%USERPROFILE%\.codex\qwen38-watson.config.toml`.

## Reproducible setup

```powershell
.\scripts\windows\Start-WatsonStack.ps1
.\scripts\windows\Install-CodexQwen38WatsonProfile.ps1
codex exec --profile qwen38-watson "Reply with OK only"
```

For the desktop app:

```powershell
.\scripts\windows\Set-CodexDesktopWatson.ps1
# Restart Codex/ChatGPT, then select Qwen3.8 (Watson).
```

Restore hosted mode with:

```powershell
.\scripts\windows\Set-CodexDesktopWatson.ps1 -Restore
```

For the ZeroTier gate:

```powershell
codex exec --profile qwen38-zerotier "Reply with ZEROTIER_OK only"
```

For the official Ollama fallback on Windows:

```powershell
ollama create qwen3.8-watson -f .\config\ollama\Qwen3.8.Modelfile
```

Ollama 0.24 or newer is required for `ollama launch chatgpt`. Verify
`http://127.0.0.1:11434/api/version`; the client version alone does not prove
which server owns the port. Windows Ollama 0.34.0 successfully imports and loads
this GGUF, including its `qwen35` architecture, but the runner currently exits
at the first inference operation and `/api/chat` returns HTTP 500. The failure
reproduces at 8,192 tokens with llama.cpp stopped and roughly 14.7 GiB VRAM free,
so this Ollama route is staged but is not an operational fallback yet.

## Regression gates

Run:

```powershell
.\tests\test_codex_local_model_contract.ps1
.\tests\test_watson_launcher.ps1
```

The first test parses all integration scripts, asserts catalog-generation
invariants, rejects any bridge dependency in the canonical launcher, and tests a
reversible desktop enable/restore cycle against an isolated Codex home.

Live acceptance additionally requires:

- direct CLI chat through `qwen38-watson`;
- a completed Codex shell tool invocation through `qwen38-watson`;
- direct CLI chat through `qwen38-zerotier`;
- a prompt from the restarted desktop app; and
- an Ollama `/api/chat` response before treating Ollama as a ready fallback.

## Verification record

Completed on 2026-09-12:

- Local `qwen38-watson` chat returned `OK` without the bridge.
- Local `qwen38-watson` emitted a native shell call, Codex executed it, and the
  model returned `WATSON_TOOL_OK`.
- ZeroTier `qwen38-zerotier` chat returned `ZEROTIER_OK`.
- ZeroTier `qwen38-zerotier` completed the same native shell loop and returned
  `ZEROTIER_TOOL_OK`.
- `Start-WatsonStack.ps1` restored llama.cpp at 100,096 context and passed both
  bounded coherence probes after the Ollama experiment.
- Windows Ollama 0.34.0 imported and loaded the model but failed the inference
  gate with HTTP 500; it is not ready for routing. The failed import was removed
  afterward to reclaim Ollama's duplicate 12 GB blob; the source GGUF and
  Modelfile remain available for a future runner retest.

The running desktop app process was started before the corrected catalog was
installed. Its subagent dispatcher therefore still rejected `qwen3.8` with the
old ChatGPT-account error, proving that catalog changes are loaded at app
startup. Desktop Watson mode is configured on disk, but final UI prompt and
subagent gates require one full app restart.
