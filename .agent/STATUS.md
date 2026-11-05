# AF - Current Status

_Last updated: 2026-11-05_

## Snapshot
- Checked out: `develop`. Newest commit `079d7f2` (`docs(opencode): refine multi-agent orchestrator, worker and reviewer roles`); latest application commit `893d6d6` (`feat(automations): retry failed live runs on the same run`).
- `master` was fast-forwarded to `develop` on 2026-11-05 and the two trees are identical. Both branches are ahead of `origin/*`; **pushing is the human's step, no agent runs `git push`** (remote state at takeover: `origin/develop` = `8ba521e`, `origin/master` = `8052eaf`).
- Full suite verified after T-208 (the same tree `master` now points at): **751 tests OK, 0 failures** (`./run_tests.sh`, 2026-11-05, ~115s); `makemigrations --check` is clean.

## Done
- **Faz 0** (`0f296e7`): scheduling fixes + webhook HMAC/replay/throttle.
- **Faz 1** (`34f2a56`..`fca930b`): rule engine v2; Step/Edge DAG; expressions and execution context; DAG executor and control flow; HTTP action and chained/signal triggers; complete engine purity; tenant/runtime hardening.
- **T-201 / Faz 2.A** (`9307529`): `AutomationRun`, `AutomationStepRun`, migration `0012`, per-run/per-step persistence and successful-step memoization.
- **T-202 + T-203 / Faz 2.B-C** (`b1f126f`): paginated run/step history, unembedded automation `logs`, dry-run provider behavior and run replay.
- **T-206:** two adversarial reviews of Faz 2.A-C; CHANGES REQUESTED, all blocking findings addressed.
- **T-207** (`0d1ea6d`): legacy/graph resume key alignment, steps-less resume, StepRun uniqueness migration `0013`, executor-owned run mode, persisted resume payload/mode, terminal-only replay, stale-step recovery and unexpected-error closure; final reviewer APPROVE.
- **T-204** (`dd33036`): trigger-scoped WebhookDelivery idempotency, raw-body SHA-256 fallback, 202/200/409 contract, migration `0014`, immediate expired reclaim, 24h TTL and hourly cleanup; final reviewer APPROVE.
- **T-208** (`893d6d6`): same-run queue retry with pre-minted/scoped identity, memoization, exponential backoff/jitter/cap, QUEUED/RUNNING/FAILED_PERMANENTLY lifecycle, final-only failure fan-out, wait-continuation handling and once-only scheduled budget; cross-tenant and explicit-id review blockers fixed; final reviewer APPROVE.

## Uncommitted work
- `.opencode/**` harness edits are **committed** in `079d7f2` (orchestrator lite/pro, reviewer, worker, handoff/kickoff/run-task commands).
- **`AGENTS.md` only:** section 9 (multi-agent workflow) sits in the working tree but is still uncommitted, and its five-item cleanup checklist (title, stale §2 status paragraph, §5 test counts, §8.7 note, `CLAUDE.md` pointer) is not applied. Human decision on 2026-11-05 was to leave it uncommitted rather than publish a half-done section.
- No uncommitted application source, tests or migrations.

## Task in flight
- None. T-204 and the interrupted T-208 are implemented, reviewed, tested and committed. T-205 was not started; this session is packaged after the log commit.

## Known problems / risks
- **T-204 delivery/dispatch crash window:** idempotent DB claiming must precede Celery dispatch to prevent duplicates; a process crash between those operations can still lose one delivery without an outbox. Delay exceptions should release a newly claimed row; full transactional-outbox recovery is out of scope.
- **T-208 delivery/recovery windows:** crash between failure recording and retry dispatch (including QUEUED-before-apply_async) can strand a run; duplicate Celery delivery can double-dispatch/reset the in-message retry budget. Beat can overshoot `n` during backoff because budget is spent on final return. Physical dead-letter queue is not configured; terminal `failed_permanently` is implemented.
- **Channel credentials in cached Automation read payload (Faz 3):** `ChannelReadSerializer` exposes `credentials`; masking/encryption remains planned.
- **Real Redis run (`./run_tests.sh --redis`):** blocked on host Docker permission (`T-090`).
- `STATUS.md` and `TASKS.md` were stale at takeover: they omitted the two Faz 2 commits, still listed T-201..T-203 as Todo, reported 636 tests and 38 commits ahead. Git and the fresh test run are the source of truth above.

## Next 3 steps
1. **Next session:** start T-205 (`wait_for_event` + `default`/`bulk` queue isolation) with a fresh worker and the T-204/T-208 delivery/retry contracts as constraints.
2. **Operational verification:** real broker/backoff behavior and `./run_tests.sh --redis` remain unverified; real Redis is blocked on T-090 Docker permission.
3. **Housekeeping:** `AGENTS.md` section 9 is uncommitted and its five-item cleanup checklist is still pending — finish it in one pass (or drop it) before the next push so master and the working tree agree.
