# AF - Current Status

_Last updated: 2026-11-04_

## Where we are
- **Faz 0** (scheduling fixes: cron field order, `one_time`/`ClockedSchedule`, `n`-budget tracking; webhook HMAC + replay window + throttling) is **DONE** (`0f296e7`).
- **Faz 1.A** (rule engine v2: `engine/paths.py`, `operators.py` with 20 operators, `decision.py` trace, short-circuit DNF grouping, quantifiers, `on_missing` policy, `AutomationLog.decision`) is **DONE** in working tree (385 tests green, 1 expectedFailure on `test_engine_purity.py` with 9 remaining violations).
- **Next:** Commit Faz 1.A + OpenCode setup (`T-100`), then execute **Faz 1.B (`AutomationStep` + `AutomationEdge` DAG)** (`T-101`, `T-102`), followed by Faz 1.C–1.E (`T-103`..`T-107`).

## Active branch
- `develop` (30 commits ahead of `origin/develop`; `master` is still pre-merge at `8052eaf`).

## Done
- Caching layer repair + Lua test suite (`51ad3ba`)
- Full system test suite + 21 bug fixes (`9c7d28b`)
- Faz 0: Scheduler (`apps/automations/scheduling.py`) + Webhook HMAC auth (`0f296e7`)
- Faz 1.A: Rule layer v2 (`paths.py`, `operators.py`, `decision.py`, `evaluator.py`, migrations `0007`–`0008`, 57 condition tests)

## In progress
- **T-100**: Commit uncommitted Faz 1.A + OpenCode setup changes on `develop`.
- **T-101**: Faz 1.B — `AutomationStep` + `AutomationEdge` models & forward/backward data migration from `AutomationAction`.
- **T-102**: Faz 1.B — Step/Edge serializers, DAG validation, and dual-write backward compatibility with `actions` API.

## Known problems / risks
- **Graph migration risk (ROADMAP §7 Faz 1):** `AutomationAction` must remain readable and the legacy `actions` payload in `AutomationWriteSerializer` must dual-write to `AutomationStep` + `AutomationEdge` so existing clients and 385 tests do not break.
- **Engine purity:** 9 Django/DRF/Celery imports remain inside `apps/automations/engine/` (tracked by `@unittest.expectedFailure` in `tests/test_engine_purity.py`). All new engine modules must be 100% pure Python.
- **Real Redis run (`./run_tests.sh --redis`):** Still pending a machine with Docker permission (fakeredis+lupa covers Lua in CI/dev).

## Next step
- Finish `T-100`, `T-101`, and `T-102` (Faz 1.B Step + Edge graph model, migration, and API serializers), then proceed to `T-103` (Faz 1.C Expression & ExecutionContext engine).
