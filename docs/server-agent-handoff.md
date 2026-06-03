# Server Agent Handoff

This repo intentionally supports a Windows server role, but the Linux agent should not implement or run that side.

The Windows/server agent owns:

- Installing `litellm[proxy]`.
- Creating `C:\Users\Juan\.litellm\config.yaml`.
- Starting `llama.cpp` on `127.0.0.1:8080`.
- Starting LiteLLM on `0.0.0.0:4000`.
- Adding Windows Firewall rules for TCP 4000 when needed.
- Verifying local Windows endpoints:
  - `http://127.0.0.1:8080/v1/models`
  - `http://127.0.0.1:4000/v1/models`

Client-facing contract:

- The server should expose `http://10.88.140.94:4000/v1`.
- The model id should be `qwen36-turbo-hermes`.
- The proxy should support:
  - `GET /v1/models`
  - `POST /v1/chat/completions`
  - `POST /v1/responses`

