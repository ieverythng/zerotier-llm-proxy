# Contributing to Watson

## Branch and worktree order

1. Fetch the approved upstream and record the exact base. Keep `main` and any running operator checkout unchanged. Start each change in a named worktree from the reviewed baseline; avoid mixing migrations, rollout scripts, and experiments in one PR.
2. For a dirty baseline, first inventory tracked, staged, and untracked paths; record ownership and dependencies. Copy only reviewed files to an integration worktree. Do not use `git add -A`, reset, or stash someone else's working tree to prepare a release.
3. Before publication, run the checks for the changed paths and inspect the complete diff against `origin/main`, including new files. Keep credentials, local session IDs, `.runtime` outputs, raw trajectories, local network IDs, and unrelated project data out of Git. Use clearly labeled synthetic evidence rather than real conversations.
4. Open a focused PR with the exact baseline, scope, test counts, separate live evidence if any, known gaps, and rollback. A passing unit suite does not authorize deployment. Keep experimental engine, sampler, preset, and benchmark changes in follow-up worktrees until paired tests qualify them.

## Verification norms

- Unit tests use isolated fixture paths and fake processes, servers, and inference where practical. Run the affected Python unittest modules from the repository root. Run Windows launcher tests that explicitly use fakes or AST sampling, not the actual deployment scripts, for offline verification.
- For a separately authorized live trial, pin the exact resident alias, engine, context, output limit, modalities, and resource baseline. Verify the owned Watson worker is idle, then run one local inference trial at a time. Capture only bounded operational evidence. Do not change a production route to rescue an inconclusive test.
- Review standards and requested behavior independently. Name what did not run. Do not publish local test outputs or claim the installed Desktop settings match a checked-in declaration without runtime evidence.

See the [coordination guide](<docs/coordination/README.md>) for team task boards and session handoffs.
