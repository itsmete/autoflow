---
description: Orchestrator runs one task end to end (worker -> verify -> review -> log)
agent: orchestrator
---

Run this task end to end: $ARGUMENTS

Loop: write the worker spec -> delegate -> verify with git diff + tests -> (reviewer if the change touches auth, tenancy, caching, executor or migrations) -> update `.agent/TASKS.md` and write a session log in `.agent/log/`.
Stop and ask me only if a wrong guess would be hard to undo. Report in max 10 lines at the end.
