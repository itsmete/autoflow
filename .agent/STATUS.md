# AF - Current Status

_Last updated: 2026-11-05_

## Snapshot
- Branch: `develop`; HEAD: `b1f126f` (`feat(automations): add paginated run history API, dry-run test mode, and run replay`).
- `develop` is **41 commits ahead of `origin/develop`**; `master` remains pre-merge at `8052eaf`.
- Full suite verified this session: **684 tests OK, 0 failures** (`./run_tests.sh`, 2026-11-05, ~129s).

## Done
- **Faz 0** (`0f296e7`): scheduling fixes + webhook HMAC/replay/throttle.
- **Faz 1** (`34f2a56`..`fca930b`): rule engine v2; Step/Edge DAG; expressions and execution context; DAG executor and control flow; HTTP action and chained/signal triggers; complete engine purity; tenant/runtime hardening.
- **T-201 / Faz 2.A** (`9307529`): `AutomationRun`, `AutomationStepRun`, migration `0012`, per-run/per-step persistence and successful-step memoization.
- **T-202 + T-203 / Faz 2.B-C** (`b1f126f`): paginated run/step history, unembedded automation `logs`, dry-run provider behavior and run replay.

## Uncommitted work (pre-existing at takeover)
- **Finished, awaiting review/commit:** `.opencode/agents/orchestrator.md`, `.opencode/agents/reviewer.md`, `.opencode/agents/worker.md` — role/model/permission/workflow updates.
- **Finished, awaiting review/commit:** `.opencode/commands/kickoff.md`, `.opencode/commands/run-task.md` — updated orchestration commands.
- **Finished, awaiting review/commit:** new `.opencode/agents/orchestrator-lite.md`, `.opencode/agents/orchestrator-pro.md`, `.opencode/commands/handoff.md`.
- **Half-done:** `AGENTS.md` — section 9 was added, but its final five-item cleanup checklist (title, stale status/test counts, §8.7 note, `CLAUDE.md` pointer) is not applied.
- No uncommitted application source, tests or migrations.

## Task in flight
- None. No worker session is transferred; re-delegate with a full spec after approval.

## Known problems / risks
- **T-206 review pending:** commits `9307529` and `b1f126f` touch migrations, durable execution, Celery, public API and tenant-scoped history; tests pass, but this session has not performed an adversarial code review.
- **Channel credentials in cached Automation read payload (Faz 3):** `ChannelReadSerializer` exposes `credentials`; masking/encryption remains planned.
- **Real Redis run (`./run_tests.sh --redis`):** blocked on host Docker permission (`T-090`).
- `STATUS.md` and `TASKS.md` were stale at takeover: they omitted the two Faz 2 commits, still listed T-201..T-203 as Todo, reported 636 tests and 38 commits ahead. Git and the fresh test run are the source of truth above.

## Next 3 steps
1. **T-206:** adversarial review of `9307529` and `b1f126f`; fix findings before building more on the durable-run/API contract.
2. **T-204 / Faz 2.D:** webhook idempotency (`WebhookDelivery`) plus exponential backoff/jitter and permanent-failure handling.
3. **T-205 / Faz 2.E:** `wait_for_event` and queue isolation (`default` / `bulk`), after retry/idempotency semantics are settled.
