# pi

Pi Coding Agent. Documentation lives at `pi.dev/docs/latest/`.

Last verified: 2026-05-16. Re-verify before writing report cells.

## Canonical doc pages

- Skills: <https://pi.dev/docs/latest/skills>
- Usage / context files: <https://pi.dev/docs/latest/usage>
- Settings: <https://pi.dev/docs/latest/settings> (replaces dead `/configuration` URL)
- Extensions: <https://pi.dev/docs/latest/extensions>
- Docs index: <https://pi.dev/docs/latest>

## Known-current paths

| Concept       | User/global                                | Project                                                |
| ------------- | ------------------------------------------ | ------------------------------------------------------ |
| Skills        | `~/.pi/agent/skills/`, `~/.agents/skills/` | `.pi/skills/`, `.agents/skills/` (cwd + ancestors)     |
| Subagents     | **Not supported**                          | **Not supported**                                      |
| Instructions  | `~/.pi/agent/AGENTS.md`                    | `AGENTS.md` or `CLAUDE.md` in cwd                      |

## Things to capture in the notes column

- **Subagents**: no native subagent concept (verified — no `/subagents`,
  `/agents`, `/sub-agents`, `/agent-types`, or `/custom-agents` docs page;
  settings schema has no `subagents` field). Workarounds are:
  - Extension API: `ctx.newSession()`, `ctx.fork()`, `pi.sendUserMessage()`,
    custom tools.
  - Community packages installed via the package system, e.g.
    `pi-subagents`, `@tintinweb/pi-subagents`, `pi-agentteam`.
  - (`/docs/latest/tmux` is about keyboard input handling — NOT an
    orchestration workaround.)
- **Skill discovery extras**: package-bundled `skills/`, `pi.skills` field in
  `package.json`, a `skills` array in settings, and repeatable `--skill <path>`
  CLI flag. `--no-skills` disables auto-discovery; explicit `--skill` paths
  still load. `enableSkillCommands` (default `true`) registers each skill as
  a `/skill:<name>` slash command.
- **Skill file rules**: in `~/.pi/agent/skills/` and `.pi/skills/`, root `.md`
  files become individual skills. In `~/.agents/skills/` and `.agents/skills/`,
  root `.md` files are ignored — only `SKILL.md` directories count.
- **Instructions discovery**: also reads `AGENTS.md` / `CLAUDE.md` in any
  parent directory (walks up the tree).
- **System prompt overrides**: `.pi/SYSTEM.md` or `~/.pi/agent/SYSTEM.md`
  replaces the system prompt; `.pi/APPEND_SYSTEM.md` /
  `~/.pi/agent/APPEND_SYSTEM.md` append to it.
- **Disable context files** with `--no-context-files` / `-nc`.
- **Env var**: `PI_CODING_AGENT_DIR` overrides the `~/.pi/agent` base path.
- **Cross-agent compatibility**: Pi reads `CLAUDE.md` / `AGENTS.md` so a single
  project can drive multiple agents.

## Search queries that have worked

- `pi.dev skills path`
- `pi.dev AGENTS.md context files`
- `pi.dev system prompt override`
- `pi.dev configuration settings`
