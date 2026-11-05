# AF - Task Board

Format: `- [ ] T-XXX short title (owner: worker/reviewer/human) - notes`

## Todo (Faz 2 — Dayanıklılık ve Gözlemlenebilirlik)
- [ ] T-206 Adversarial review of Faz 2.A-C commits `9307529` and `b1f126f` (owner: reviewer) - Check durable execution/resume, migration safety, dry-run side effects, replay and tenant/RBAC isolation before T-204
- [ ] T-204 Faz 2.D: Webhook idempotency (`WebhookDelivery`) & exponential backoff retry (owner: worker) - `Idempotency-Key` / payload hash dedup + Celery backoff/jitter + `failed_permanently` (`ROADMAP.md` §4.7, T4, T5)
- [ ] T-205 Faz 2.E: `wait_for_event` step & queue isolation (`default` / `bulk`) (owner: worker) - Suspend run until matching event/timeout + queue routing (`ROADMAP.md` §4.4, §4.7, T8)

## Doing

## Blocked
- [ ] T-090 Real Redis test run `./run_tests.sh --redis` (owner: human) - Requires Docker daemon permission on host

## Done
- [x] T-201 Faz 2.A: `AutomationRun` & `AutomationStepRun` models + step memoization (`9307529`; full suite 684 OK; T-206 review pending)
- [x] T-202 Faz 2.B: Paginated Run/StepRun history API & unembedded `logs` (`b1f126f`; full suite 684 OK; T-206 review pending)
- [x] T-203 Faz 2.C: Dry-run (`RunMode.TEST`) & Replay (`replay_of`) (`b1f126f`; full suite 684 OK; T-206 review pending)
- [x] T-000 Faz 0: Scheduling fixes (`CrontabSchedule` field order, `one_time` `ClockedSchedule`, `n` run_count budget) & Webhook HMAC auth (`0f296e7`)
- [x] T-001 Faz 1.A: Rule engine v2 (`paths.py`, `operators.py`, `decision.py`, `evaluator.py`, migrations `0007`–`0008`) (`34f2a56`)
- [x] T-100 Housekeeping: Commit Faz 1.A & OpenCode configuration (`34f2a56`, `519fde5`)
- [x] T-101 Faz 1.B: `AutomationStep` & `AutomationEdge` models + data migration `0009` (`84cd4e0`)
- [x] T-102 Faz 1.B: Step & Edge serializers, DAG validation & `actions` dual-write (`c9cef2d`)
- [x] T-103 Faz 1.C: `ExecutionContext` & pure expression resolver `engine/expressions.py` + `TemplateManager` upgrade (`9109d4a`)
- [x] T-104 Faz 1.D: DAG Executor & control-flow steps (`action`, `filter`, `branch`, `set_variable`, `wait`) (`18471b6`)
- [x] T-105 Faz 1.E: `http_request` action with step output capture (`717cdc4`)
- [x] T-106 Faz 1.E: `automation_completed` trigger & `field_changed` signal event (`fca930b`)
- [x] T-107 Faz 1.F: Complete `engine/` purity (0 Django/DRF/Celery imports) & adversarial review hardening (`fca930b`)
