---
description: Start or take over a session - verify state against git, propose next tasks
---

Start of session (or takeover from another model). Do this:

1. Read `.agent/STATUS.md`, `.agent/TASKS.md`, the phase section of `ROADMAP.md` that matches the current work, and `tail -n 80 logbook.md`.
2. Run `git branch --show-current`, `git log --oneline -10`, `git status --short`, `git diff --stat`.
3. Compare STATUS.md with git. If they disagree, git is right. List the differences. For every uncommitted file decide: finished, half-done or unknown (use `explore` to read the diff if needed).
4. Tell me in Turkish, max 12 lines: where the project really stands, the state of uncommitted work, and the next 1-3 tasks you propose (with the reason for the order).
5. Wait for my approval before delegating anything.

Extra focus for this session (may be empty): $ARGUMENTS
