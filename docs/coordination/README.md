# Watson coordination

Use one owner-approved GRILL and an explicit roster per registered workspace. The same role labels can be reused across workspaces; exact session IDs cannot. The local roster belongs in private configuration, not in this repository. For setup and rollback see the [Bendek workspace runbook](<../knowledge/bendek-workspace-bridge.md>).

## Native Team task board

Record each bounded task before starting it. Give it one writer, an exact branch/worktree and write scope, base revision, evidence and stop conditions. Use dependencies for tasks that cannot run concurrently. The board is advisory, not a filesystem lock. Claim after re-reading the current revision. Finish with exact changed paths, test results, unresolved findings and owned jobs. The lead alone integrates; a nonauthor verifies the result before promotion.

Use separate scopes for release integration, hardware qualification, and speculative variants. Serialize model trials across all teams; confirmation that one agent is idle is not proof the worker is free. Inference, deploy, and Git publication each have independent approval and verification gates.

## Hub and parent board template

| Role | Owns | Required result |
| --- | --- | --- |
| GRILL | Scope, preflight, dependencies, release integration | Verified artifacts, unresolved issues, publication status |
| RESEARCH | Public-source investigation | Citations and distinction between upstream and local evidence |
| DEV1 | Scoped implementation branch | Diff, validation, and residual risks |
| DEV2 | Independent tests and review | Reproducible pass/fail evidence; no edits to the author's files |
| Local testing | Exact-model synthetic trials | Sampling plan, serial ownership, counters, result, no real payloads |

Request only the owners needed for the task. Verify exact roots via native read-only preflight. Send one short correlation-tagged brief via default steer: scope, exact workspace, write paths, dependencies, budget, stop condition, expected ACK and terminal result. An accepted inbox message is not an ACK. Do not retry a possibly accepted dispatch. A worker should never reinterpret a relayed message as direct owner approval, contact sibling roots, resume another project, or publish artifacts. Keep task history and relay contents private unless independently reviewed for publication.
