# AF - Task Board

Format: `- [ ] T-XXX short title (owner: worker/reviewer/human) - notes`

## Todo
- [ ] T-102 Faz 1.B: Step & Edge serializers + dual-write `actions` compatibility (owner: worker) - `AutomationStepSerializer`, `AutomationEdgeSerializer`, DAG validation, `actions` <-> `steps` sync in `AutomationWriteSerializer`, tests in `tests/test_automations_steps_api.py`
- [ ] T-103 Faz 1.C: ExecutionContext & safe expression resolver (owner: worker) - Pure `apps/automations/engine/expressions.py` (`{{trigger.*}}`, `{{steps.<key>.output.*}}`, filters, `{%if%}`), upgrade `TemplateManager` & make `Template.fields` optional
- [ ] T-104 Faz 1.D: DAG Executor & control-flow steps (`action`, `filter`, `branch`, `set_variable`, `wait`) (owner: worker) - Traverse `AutomationStep` + `AutomationEdge`, capture step outputs into context, schedule `wait` continuation
- [ ] T-105 Faz 1.E: `http_request` action with step output capture (owner: worker) - New action/step type with timeout, headers/query/body/auth, captures status/json/headers into `steps.<key>.output`
- [ ] T-106 Faz 1.E: `automation_completed` trigger & `field_changed` signal event (owner: worker) - Chained automations with recursion guard + `pre_save` field diffing on signal triggers
- [ ] T-107 Faz 1.F: Complete `engine/` purity (0 Django/DRF/Celery imports) (owner: worker) - Clean remaining 9 imports in `engine/` and remove `@expectedFailure` in `tests/test_engine_purity.py`

## Doing
- [ ] T-100 Housekeeping: Commit Faz 1.A & OpenCode configuration (owner: worker) - Stage and commit existing Faz 1.A work and OpenCode agent config in two clean Conventional Commits
- [ ] T-101 Faz 1.B: `AutomationStep` & `AutomationEdge` models + data migration (owner: worker) - Add models in `apps/automations/models.py`, migration `0009` converting `AutomationAction` rows into linear `AutomationStep` + `AutomationEdge` chains, model/migration tests

## Blocked
- [ ] T-090 Real Redis test run `./run_tests.sh --redis` (owner: human) - Requires Docker daemon permission on host

## Done
- [x] T-000 Faz 0: Scheduling fixes (`CrontabSchedule` field order, `one_time` `ClockedSchedule`, `n` run_count budget) & Webhook HMAC auth (`0f296e7`)
- [x] T-001 Faz 1.A: Rule engine v2 (`paths.py`, `operators.py`, `decision.py`, `evaluator.py`, migrations `0007`–`0008`, 385 tests green)
