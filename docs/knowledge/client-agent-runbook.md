# Client Agent Runbook

This machine is only a client. The Windows inference host runs `llama.cpp` locally and exposes LiteLLM on the ZeroTier interface.

## Install Codex Client Config

```bash
./scripts/unix/install-codex-client-config.sh
```

The script registers `[model_providers.qwen36-zerotier]` in `~/.codex/config.toml`, writes a selectable `~/.codex/qwen36-zerotier.config.toml` profile, and installs a merged model catalog at `~/.codex/model-catalogs/qwen36-plus-bundled.json`. It does not change the global default model.

## Verify Proxy

```bash
./scripts/unix/verify-client.sh
```

Useful overrides:

```bash
LLM_PROXY_BASE_URL=http://10.88.140.94:4000/v1 ./scripts/unix/verify-client.sh
LLM_MODEL=qwen36-turbo-hermes ./scripts/unix/verify-client.sh
```

## Expected Success

- `/v1/models` returns `qwen36-turbo-hermes`.
- `/v1/chat/completions` returns a small text response (useful for curl checks only).
- `/v1/responses` returns a small text response (this is what Codex uses via `wire_api = "responses"`).
- Image attachments from Codex are not supported end-to-end: llama.cpp is running text-only Qwen even though the base model is multimodal.

## Run Codex CLI With Qwen

```bash
codex exec --profile qwen36-zerotier "Say hello"
```

The `--profile` flag tells Codex to use the `qwen36-zerotier` provider for this session. Without it, Codex routes `qwen36-turbo-hermes` through the default OpenAI provider, which will fail.

## Run Codex Desktop App With Qwen — Shell Wrapper

The Codex Desktop app does not pass `--profile` when spawning CLI sessions. To make `qwen36-turbo-hermes` work from the app's model dropdown, install a shell wrapper that auto-injects the profile:

### Setup

1. Rename the original codex binary:
   ```bash
   mv ~/.local/bin/codex ~/.local/bin/codex.real
   ```

2. Create `~/.local/bin/codex` with the wrapper script (see the bootstrap HTML file for the full script). The wrapper detects `-m qwen36-turbo-hermes` in CLI args and auto-injects `-p qwen36-zerotier`.

3. Make it executable:
   ```bash
   chmod +x ~/.local/bin/codex
   ```

4. Restart Codex Desktop. Select "Qwen36 Turbo Hermes" from the model dropdown — it now routes through `qwen36-zerotier` automatically.

### npm Update Resilience

Running `npm update -g @openai/codex` overwrites the wrapper. Restore it with:

```bash
~/.codex/restore-codex-wrapper.sh
```

Then restart Codex Desktop.

## Images and Multimodal Input

Codex can attach images (`codex exec -i photo.png ...` or pasted images in the
Desktop app). Those become Responses API `input_image` parts. LiteLLM forwards
them to llama.cpp, which currently returns:

`image input is not supported - hint: if this is unexpected, you may need to provide the mmproj`

That is expected for a **text-only** Qwen GGUF load. Vision would require
starting llama.cpp with the matching **mmproj** (and a vision-capable build),
then updating LiteLLM if needed. Until then, use text-only prompts with this
profile.

## Common Client-Side Failures

- `Connection refused`: LiteLLM is not running, is not bound to `0.0.0.0`, or Windows Firewall blocks TCP 4000.
- `Timeout`: ZeroTier route/firewall issue or the host is offline. If `ip route get 10.88.140.94` shows your Wi-Fi gateway instead of a ZeroTier device, run `sudo zerotier-cli set 3b19b3a716937e29 allowManaged=1`, reconnect ZeroTier, and verify again.
- `401`/credential errors: the proxy is probably running with a stale authenticated config; restart it from `scripts/windows/Start-Qwen36LiteLLM.ps1`.
- `unknown variant chat_completions, expected responses`: set `wire_api = "responses"` in `[model_providers.qwen36-zerotier]`. Codex does not accept `chat_completions` for custom providers; LiteLLM still exposes `/v1/chat/completions` for manual curl tests.
- Codex websocket warnings: Codex is likely not reading `~/.codex/config.toml` or is pointed at the raw `llama.cpp` server instead of LiteLLM.
- Desktop app routes to OpenAI: The shell wrapper is missing or was overwritten by npm update. Restore with `~/.codex/restore-codex-wrapper.sh`.
