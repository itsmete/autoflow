---
description: Write the full state to .agent/ so a different model can continue (handoff)
---

Prepare a handoff. The next model has NO memory of this chat, so everything must be in files.

First collect facts, do not rely on memory:
- `git branch --show-current`, `git log --oneline -10`, `git status --short`, `git diff --stat`

Then rewrite `.agent/STATUS.md` (max ~80 lines) with exactly these sections:
1. **Snapshot**: date, branch, HEAD commit hash + message, last known test result (e.g. "388 OK on <date>").
2. **Done**: finished tasks with commit hashes.
3. **Uncommitted work**: every modified/untracked file, which task it belongs to, and whether it is finished, half-done or unknown. Be honest.
4. **Task in flight**: ID, goal, acceptance criteria, what is already done, what is missing. Worker sessions cannot be transferred, so write the spec so a NEW worker can start from it.
5. **Decisions** made and why (one line each).
6. **Gotchas**: things that burned us (tool errors, flaky tests, traps in the code).
7. **Next 3 steps** in order.

Update `.agent/TASKS.md` to match. Add a `logbook.md` entry for anything done since the last entry (match the existing format). Commit the log files.
Finally tell me in Turkish, max 8 lines, what the next model must do first. Extra notes from me: $ARGUMENTS
