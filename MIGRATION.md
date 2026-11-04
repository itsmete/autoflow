# Claude Code -> OpenCode Migration (AF repo)

## Short answers
- **Agents must be `.md`?** No. You can define agents in `opencode.json` (`"agent": {...}`) OR as markdown files in `.opencode/agents/`. Markdown is better when the prompt is long (ours are). The filename is the agent name (`worker.md` -> `worker`).
- **CLAUDE.md?** Keep it, but make it a pointer: `@AGENTS.md`. OpenCode reads `AGENTS.md` first and only uses `CLAUDE.md` if there is no `AGENTS.md`. Claude Code reads CLAUDE.md and follows the `@AGENTS.md` import. One source of truth, both tools work.

## Mapping table
| Claude Code | OpenCode | What to do |
|---|---|---|
| `CLAUDE.md` (project) | `AGENTS.md` | Copy the content into `AGENTS.md`, replace CLAUDE.md with `@AGENTS.md` |
| `~/.claude/CLAUDE.md` (global) | `~/.config/opencode/AGENTS.md` | Copy only if you want it for all projects |
| `.claude/skills/*/SKILL.md` | same path works | Nothing. OpenCode reads `.claude/skills` directly. (Or move to `.opencode/skills`.) |
| `.claude/commands/*.md` | `.opencode/commands/*.md` | MOVE the files. Frontmatter: `description`, `agent`, `model`. `$ARGUMENTS` works. |
| `.claude/agents/*.md` | `.opencode/agents/*.md` | Convert frontmatter (see below) |
| `.claude/settings.json` permissions | `permission` in `opencode.json` / agent files | Rewrite by hand (see below) |
| Hooks (`settings.json`) | Plugins (JS/TS) | No direct copy. Decide if you still need them. |
| MCP servers (`.mcp.json`) | `mcp` key in `opencode.json` | Rewrite |
| Model aliases (`opus`, `sonnet`) | `provider/model-id` | Always `opencode-go/<id>` |

## Agent frontmatter conversion
Claude Code:
```yaml
---
name: code-reviewer
description: ...
tools: Read, Grep, Glob
model: sonnet
---
```
OpenCode:
```yaml
---
description: ...
mode: subagent
model: opencode-go/mimo-v2.6-pro
permission:
  edit: deny
  bash: deny
---
```
- `name` is not needed (filename = name).
- `tools:` list -> `permission:` (allow/ask/deny per tool).
- `model:` must be `opencode-go/...`. Do NOT use `opencode/...` (that bills your Zen balance -> the 402 error).

## Steps
1. Create a branch: `git switch -c chore/opencode-setup`.
2. Copy this kit into the repo root (merge with existing files; do not overwrite your CLAUDE.md before step 3).
3. Open your old `CLAUDE.md`. Move its content into `AGENTS.md` (replace the TODO sections). Keep it short: rules, commands, conventions. Delete stale text. Then replace `CLAUDE.md` with the one-line pointer.
4. Move `.claude/commands/*` to `.opencode/commands/`. Convert any `.claude/agents/*` as above. Leave `.claude/skills` where it is.
5. Put DeepSeek's report in `.agent/REPO_REPORT.md` and fill `.agent/STATUS.md` from it (or ask the orchestrator to do it with `/kickoff`).
6. Add to `.gitignore` if you do not want logs in git: `.agent/log/`. (I would commit STATUS.md and TASKS.md, they are useful.)
7. Check: run `opencode agent list` (you should see orchestrator, worker, reviewer) and `opencode models | grep opencode-go` (confirm the exact model IDs).
8. Start `opencode`, press Tab to confirm `orchestrator` is the active agent, run `/kickoff`.

## Things to test before you trust it (I could not test them here)
1. **Edit glob on orchestrator.** Ask it: "create `.agent/log/test.md`" (should work) and "edit `AGENTS.md`" (should be denied). If both are allowed or both denied, the path pattern needs a small change (try `"**/.agent/**"` or `".agent/*"`).
2. **Bash rules.** `git push` must be denied for every agent. Try it once.
3. **Subagent really used.** Give a small coding task. Check that the orchestrator calls `worker` and does not try to do it itself. If it ignores workers, make the prompt stronger or set its bash to read-only.
4. **Model IDs.** If `gemini-4-argon` or `mimo-v2.6-pro` is not found, fix the IDs from `opencode models`.
5. **Cost.** Gemini 4 Argon is about 100x more expensive than V4.1 Flash in your shared pool. `steps: 25` for the orchestrator keeps it under control. Watch the usage page after the first session. If it burns too fast, switch the orchestrator to `opencode-go/mimo-v2.6-pro` (about 13x).

## Using both tools
- Claude Code (when your limits come back) reads `CLAUDE.md` -> `@AGENTS.md`, so it follows the same rules.
- `.agent/` logs are plain markdown, so either tool can continue the work.
