# AF - Current Status

_Last updated: 2026-11-05_

## Snapshot
- Branch: `develop`; latest application commit: `dd33036` (`feat(automations): add persistent webhook delivery idempotency`). Later `docs(agent)` commits may follow; use git for the exact HEAD.
- `develop` is ahead of `origin/develop`; `master` remains pre-merge at `8052eaf`.
- Full suite verified after T-204: **724 tests OK, 0 failures** (`./run_tests.sh`, 2026-11-05, ~116s); `makemigrations --check` is clean.

## Done
- **Faz 0** (`0f296e7`): scheduling fixes + webhook HMAC/replay/throttle.
- **Faz 1** (`34f2a56`..`fca930b`): rule engine v2; Step/Edge DAG; expressions and execution context; DAG executor and control flow; HTTP action and chained/signal triggers; complete engine purity; tenant/runtime hardening.
- **T-201 / Faz 2.A** (`9307529`): `AutomationRun`, `AutomationStepRun`, migration `0012`, per-run/per-step persistence and successful-step memoization.
- **T-202 + T-203 / Faz 2.B-C** (`b1f126f`): paginated run/step history, unembedded automation `logs`, dry-run provider behavior and run replay.
- **T-206:** two adversarial reviews of Faz 2.A-C; CHANGES REQUESTED, all blocking findings addressed.
- **T-207** (`0d1ea6d`): legacy/graph resume key alignment, steps-less resume, StepRun uniqueness migration `0013`, executor-owned run mode, persisted resume payload/mode, terminal-only replay, stale-step recovery and unexpected-error closure; final reviewer APPROVE.
- **T-204** (`dd33036`): trigger-scoped WebhookDelivery idempotency, raw-body SHA-256 fallback, 202/200/409 contract, migration `0014`, immediate expired reclaim, 24h TTL and hourly cleanup; final reviewer APPROVE.

## Uncommitted work (pre-existing at takeover)
- **Finished, awaiting review/commit:** `.opencode/agents/orchestrator.md`, `.opencode/agents/reviewer.md`, `.opencode/agents/worker.md` — role/model/permission/workflow updates.
- **Finished, awaiting review/commit:** `.opencode/commands/kickoff.md`, `.opencode/commands/run-task.md` — updated orchestration commands.
- **Finished, awaiting review/commit:** new `.opencode/agents/orchestrator-lite.md`, `.opencode/agents/orchestrator-pro.md`, `.opencode/commands/handoff.md`.
- **Half-done:** `AGENTS.md` — section 9 was added, but its final five-item cleanup checklist (title, stale status/test counts, §8.7 note, `CLAUDE.md` pointer) is not applied.
- No uncommitted application source, tests or migrations.

## Task in flight
- **T-208:** finish the interrupted queue-level retry design and implementation: SAME run id, exponential backoff/jitter, memoized resume, terminal `failed_permanently`, and no transient-failure fan-out. T-205 will NOT be started in this session; package after T-208.

## Known problems / risks
- **T-204 delivery/dispatch crash window:** idempotent DB claiming must precede Celery dispatch to prevent duplicates; a process crash between those operations can still lose one delivery without an outbox. Delay exceptions should release a newly claimed row; full transactional-outbox recovery is out of scope.
- **Channel credentials in cached Automation read payload (Faz 3):** `ChannelReadSerializer` exposes `credentials`; masking/encryption remains planned.
- **Real Redis run (`./run_tests.sh --redis`):** blocked on host Docker permission (`T-090`).
- `STATUS.md` and `TASKS.md` were stale at takeover: they omitted the two Faz 2 commits, still listed T-201..T-203 as Todo, reported 636 tests and 38 commits ahead. Git and the fresh test run are the source of truth above.

## Next 3 steps
1. **T-208 / Faz 2.D.2:** finish and review queue-level retry on the SAME run; no new task beyond the interrupted work.
2. **Package:** final full-suite verification, logbook/STATUS/TASKS update and log commit; no push.
3. **Later, not this session:** T-205 (`wait_for_event` + queue isolation) remains Todo.
