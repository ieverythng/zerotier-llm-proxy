# Server Agent Handoff

This repo intentionally supports a Windows server role, but the Linux agent should not implement or run that side.

The Windows/server agent owns:

- Installing `litellm[proxy]`.
- Maintaining `config/server/litellm-config.yaml`.
- Starting `llama.cpp` on `127.0.0.1:8080`.
- Starting LiteLLM on `0.0.0.0:4000`.
- Adding Windows Firewall rules for TCP 4000 when needed.
- Verifying local Windows endpoints:
  - `http://127.0.0.1:8080/v1/models`
  - `http://127.0.0.1:4000/v1/models`

Client-facing contract:

- The server should expose `http://10.88.140.94:4000/v1`.
- The model id should be `qwen36-turbo-hermes`.
- Clients should select it with `codex --profile qwen36-zerotier`; it should not be assumed as the default model.
- The proxy should support:
  - `GET /v1/models`
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
- App verification should use a session-scoped Codex home, not `-c` overrides alone. The desktop/app-server startup path needs Qwen as the active config default for that launched process.

