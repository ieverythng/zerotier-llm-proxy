# Bendek workspace bridge: enroll a project

The bridge is an optional local DSH Host provider. Watson inference does not require it. One provider owns `session_list`, `session_preflight`, `session_send_message`, and `session_wait_for_message`; duplicate global providers are not supported. One exact owner-approved hub and finite approved spokes per registered workspace. No discovery, cross-workspace messaging, or implicit task, tool or publishing authority.

1. In Desktop **WorkSpace** grouping, copy each intended existing root's session ID from the row menu. Verify canonical workspace placement, ordinary root ownership, and the exact hub. A title or working directory is not enrollment.
2. Keep bindings in a private, unlinked configuration. Prepare a new private version with the previous validated rosters unchanged and one explicit new roster. Do not edit installed linked code or an active profile patch by hand. Keep the pinned original for rollback.
3. Verify synthetic isolation (hub only sees its own roster; each spoke only sees itself and hub), no conflicting provider, and runtime compatibility. Invoke supported Plugin Manager operations only with approval. Verify the old provider really withdrew before enabling a replacement. If a restart is required, stop and wait for an idle owner-controlled restart.
4. From the exact hub, use `/bendek-dsh-workflow`, then read-only `session_list` and `session_preflight` on only the approved IDs. Unknown fields are not admission proof. Qualify a small harmless ACK/terminal-result canary separately before work dispatch. For PTC inspect the SDK bindings even though the visible transport tool is `run_code`.
5. On regression, stop dispatch and withdraw the candidate, then restore the original provider and verify live activation and tool visibility. Do not retry an ambiguously accepted send. Unload cannot unsend a message.

The [coordination guide](<../coordination/README.md>) documents ownership and task-board evidence. Don't publish roster IDs, task inbox payloads, or private machine configuration.
