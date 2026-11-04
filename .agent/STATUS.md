# AF - Current Status

_Last updated: 2026-11-05_

## Where we are
- **Faz 0** (scheduling fixes + webhook HMAC/replay/throttle) is **DONE** (`0f296e7`).
- **Faz 1** (Motor Derinliği — `ROADMAP.md` §7) is **100% DONE** (`34f2a56`..`fca930b`):
  - **1.A**: Rule engine v2 (`paths.py`, `operators.py` with 20 operators, `decision.py` trace, short-circuit DNF grouping, quantifiers, `on_missing`).
  - **1.B**: `AutomationStep` + `AutomationEdge` DAG models, migration `0009` converting legacy `AutomationAction` rows, step/edge serializers, DAG validation (cycles, reachability, branch keys), and bidirectional dual-write with `actions`.
  - **1.C**: SSTI-safe expression engine (`engine/expressions.py`) + `ExecutionContext` (`trigger`, `steps`, `vars`, `automation`, `tenant`, `branch`, `now`), filters (`default`, `upper`, `lower`, `strip`, `title`, `length`, `currency`, `date`, `json`), and `TemplateManager` upgrade with optional `Template.fields`.
  - **1.D**: DAG executor (`execute_graph`) supporting `action`, `filter`, `branch`, `set_variable`, and `wait` (with Celery continuation + resume from `start_step_key`).
  - **1.E**: `http_request` action with response output capture (`status_code`, `json`, `text`, `headers`) + timeout bounds; `automation_completed` trigger with `MAX_CHAIN_DEPTH=5` guard; `field_changed` signal event with `pre_save` snapshot diffing.
  - **1.F**: Complete `engine/` purity (**0 Django/DRF/Celery imports** inside `apps/automations/engine/`, `@expectedFailure` removed from `tests/test_engine_purity.py`) via `apps/automations/runtime.py` adapter.
  - **Adversarial review fixes**: Cross-tenant validation on `channel`, `template`, and `source_automation_id`; negative `_chain_depth` clamping; `http_request` timeout clamping (1–60s).
- **Test suite**: **636 tests passing, 0 failures, 0 expected failures**.

## Active branch
- `develop` (38 commits ahead of `origin/develop`; `master` is still pre-merge at `8052eaf`).

## Done
- Faz 0 (`0f296e7`)
- Faz 1 (`34f2a56`, `519fde5`, `84cd4e0`, `c9cef2d`, `9109d4a`, `18471b6`, `717cdc4`, `fca930b`)

## In progress
- None (Faz 1 complete; ready to kick off Faz 2).

## Known problems / risks
- **Channel credentials in cached Automation read payload (Faz 3 / T7):** `ChannelReadSerializer` exposes `credentials`; masking/encryption is planned in Faz 3 (`EncryptedJSONField` + read redaction).
- **Real Redis run (`./run_tests.sh --redis`):** Pending host Docker permission (`T-090`).

## Next step
- Start **Faz 2 — Dayanıklılık ve Gözlemlenebilirlik** (`ROADMAP.md` §5.2 & §7):
  - `T-201`: `AutomationRun` + `AutomationStepRun` models & step memoization (`StepRun.status == 'success'` skipped on replay/retry).
  - `T-202`: Paginated run history API (`automations/<id>/runs/`, `runs/<id>/steps/`) & removing unbounded `logs` from `AutomationReadSerializer`.
  - `T-203`: Dry-run (`RunMode.TEST`) & Replay API.
  - `T-204`: Webhook idempotency (`WebhookDelivery` dedup) & exponential backoff retry + `wait_for_event`.
