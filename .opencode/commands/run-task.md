---
description: Run one task end to end (worker -> verify -> review -> log) with the active agent
---

Run this task end to end: $ARGUMENTS

Loop: write the worker spec -> delegate to a FRESH worker -> verify with `git diff --stat` + `./run_tests.sh` -> (reviewer if the change touches RBAC, tenancy, cache/Lua, executor, signals/Celery or migrations) -> update `.agent/TASKS.md`, add a `logbook.md` entry (match the existing format), commit the log files.
Stop and ask me only if a wrong guess would be hard to undo. Report to me in Turkish, max 10 lines.
