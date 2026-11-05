# AF - Current Status

_Last updated: 2026-11-05_

## Snapshot
- Branch: `develop`; latest application commit: `0d1ea6d` (`fix(automations): harden durable-run resume and mode authority`). Later `docs(agent)` commits may follow; use git for the exact HEAD.
- `develop` is ahead of `origin/develop`; `master` remains pre-merge at `8052eaf`.
- Full suite verified after T-207: **704 tests OK, 0 failures** (`./run_tests.sh`, 2026-11-05, ~118s); `makemigrations --check` is clean.

## Done
- **Faz 0** (`0f296e7`): scheduling fixes + webhook HMAC/replay/throttle.
- **Faz 1** (`34f2a56`..`fca930b`): rule engine v2; Step/Edge DAG; expressions and execution context; DAG executor and control flow; HTTP action and chained/signal triggers; complete engine purity; tenant/runtime hardening.
- **T-201 / Faz 2.A** (`9307529`): `AutomationRun`, `AutomationStepRun`, migration `0012`, per-run/per-step persistence and successful-step memoization.
- **T-202 + T-203 / Faz 2.B-C** (`b1f126f`): paginated run/step history, unembedded automation `logs`, dry-run provider behavior and run replay.
- **T-206:** two adversarial reviews of Faz 2.A-C; CHANGES REQUESTED, all blocking findings addressed.
- **T-207** (`0d1ea6d`): legacy/graph resume key alignment, steps-less resume, StepRun uniqueness migration `0013`, executor-owned run mode, persisted resume payload/mode, terminal-only replay, stale-step recovery and unexpected-error closure; final reviewer APPROVE.

## Uncommitted work (pre-existing at takeover)
- **Finished, awaiting review/commit:** `.opencode/agents/orchestrator.md`, `.opencode/agents/reviewer.md`, `.opencode/agents/worker.md` — role/model/permission/workflow updates.
- **Finished, awaiting review/commit:** `.opencode/commands/kickoff.md`, `.opencode/commands/run-task.md` — updated orchestration commands.
- **Finished, awaiting review/commit:** new `.opencode/agents/orchestrator-lite.md`, `.opencode/agents/orchestrator-pro.md`, `.opencode/commands/handoff.md`.
- **Half-done:** `AGENTS.md` — section 9 was added, but its final five-item cleanup checklist (title, stale status/test counts, §8.7 note, `CLAUDE.md` pointer) is not applied.
- No uncommitted application source, tests or migrations.

## Task in flight
- **T-204 specification:** read-only codebase mapping is complete. No implementation worker is active; webhook key scope, conflicting-payload response and TTL cleanup semantics need a human decision because they define public behavior.

## Known problems / risks
- **T-204 public semantics pending:** ROADMAP requires a duplicate webhook to return 200 without running, but does not define key scope, same-key/different-payload behavior or actual TTL cleanup. These choices are being confirmed before implementation.
- **Channel credentials in cached Automation read payload (Faz 3):** `ChannelReadSerializer` exposes `credentials`; masking/encryption remains planned.
- **Real Redis run (`./run_tests.sh --redis`):** blocked on host Docker permission (`T-090`).
- `STATUS.md` and `TASKS.md` were stale at takeover: they omitted the two Faz 2 commits, still listed T-201..T-203 as Todo, reported 636 tests and 38 commits ahead. Git and the fresh test run are the source of truth above.

## Next 3 steps
1. **T-204 / Faz 2.D.1:** implement `WebhookDelivery` idempotency after the human confirms scope, conflict response and TTL cleanup.
2. **T-208 / Faz 2.D.2:** queue-level exponential backoff/jitter on the SAME run; exhaustion becomes `failed_permanently`, separate from action-level immediate retries.
3. **T-205 / Faz 2.E:** `wait_for_event` and queue isolation (`default` / `bulk`), after retry/idempotency semantics are settled.
