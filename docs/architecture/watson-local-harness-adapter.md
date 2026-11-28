# Watson Local and Universal Agentic Harness

Keep deployment resolution outside the Universal Agentic Harness (UAH) kernel.
The Watson model registry is operational configuration, not an agent manifest.

```text
UAH AgentManifest → ModelConfiguration → opaque endpoint_ref
                                      → deployment adapter → Watson Local
                                                             → selected model alias
```

| Deployment value | Role |
| --- | --- |
| `watson-local/saluki27b`, `watson-local/inkling2-mini` | Example opaque `endpoint_ref` values; the adapter resolves them to the private loopback gateway and request model ID. |
| `saluki27b`, `inkling2-mini` | Stable serving aliases in Watson Local. They are neither model artifact identities nor UAH `agent_id` values. |
| Artifact revision, validated capabilities, allocated context, decoding settings | Inputs to UAH's content-addressed `ModelConfiguration`. Changing them yields a new configuration and therefore a new `AgentManifest` identity. |
| URL and bearer credential reference | Deployment adapter inputs only. Never place the bearer value in a model configuration, lease, registry, log, or agent manifest. |

The UAH deployment adapter belongs behind its existing `ProviderPort`. Resolve
`endpoint_ref` there, compare its approved context/output budget to authenticated
`GET /v1/models`, and send `POST /v1/chat/completions` with the alias. The gateway
handles the single GPU, exact alias/context readiness, and noninterrupting model
admission. Treat HTTP 409 as busy, not as permission to replace a process. Do not
silently retry an ambiguous/partially returned invocation or substitute another
model; use the harness's run/lease and retry policy. Honor model modalities and
allow only inline images for approved vision profiles. The adapter must not
load tool-bearing models until the applicable tool and trust policy admits them.

A runnable UAH provider adapter and lease integration are future work; no UAH
source or agent IDs were changed for the Windows launcher. See UAH's own
`agent_configuration.py`, `agent_identity.py`, `model_allocator.py`,
`model_invocation.py`, and `docs/architecture/watson_inference_seams.md` for the
contract owners. This stack exercises Chat Completions; Responses is deliberately
not exposed until the Headroom route is separately qualified.
