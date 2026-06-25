# Watson context validation notes — 2026-06-25

## Result

- 128k server context can be loaded through the canonical launcher path.
- A synthetic 80k prompt monopolized the GPU for several minutes and was stopped as not interactive for Discord.
- The stack was restored to the 65k interactive profile.
- 65k restore smoke passed through LiteLLM and the Watson harness.

## Root cause found

The canonical launcher previously accepted an existing llama.cpp process as healthy when the model alias matched, even if the loaded context did not match the requested -ContextSize. This allowed Lazarus to report a successful 128k run while the active server was still at n_ctx=65536.

The launcher now checks /v1/models -> data[].meta.n_ctx. If the model is loaded at the wrong context, it stops llama.cpp and reloads with the requested context.

## Hermes Discord import failure

The Discord error was a Hermes hot-update/stale-module issue:

```text
cannot import name 'env_float' from 'utils' (/home/juanbeck/.hermes/hermes-agent/utils.py)
```

The file on disk defines env_float; the running gateway had cached an older utils module before the update. Restarting hermes-gateway.service refreshed the loaded module graph. Verified imports after restart:

- utils.env_float
- agent.auxiliary_client
- plugins.platforms.discord.adapter

## Validation commands run

```powershell
.\scripts\windows\Test-Qwen36Proxy.ps1
```

```bash
cd /home/juanbeck/Watson
python3 scripts/watson-stack-harness.py \
  --base-url http://172.24.16.1:4000/v1 \
  --headroom-url http://172.24.16.1:8787/health \
  --model qwen36-turbo-hermes \
  --contexts 4096 \
  --max-tokens 64 \
  --timeout 180 \
  --label restored-65k-smoke
```

Smoke report:

```text
Endpoint models: 200
Endpoint headroom: 200
Context 4096: 200, 4.483s, markers retained
Tool fidelity: JSON valid, correct tool selected
Acceptance: PASS
```

## Throughput conclusion

Decode throughput alone is not enough for Discord usability. High context primarily hurts time-to-first-token because the model must prefill the full prompt before visible output begins. Keep 65k as the default interactive target unless a separate prefill optimization pass proves 80k+ can return within the desired latency budget.

