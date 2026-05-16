# pi

Pi Coding Agent. Documentation lives at `pi.dev/docs/latest/`.

Last verified: 2026-05-16. Re-verify before writing report cells.

## Canonical doc pages

- Skills: <https://pi.dev/docs/latest/skills>
- Usage / context files: <https://pi.dev/docs/latest/usage>
- Configuration: <https://pi.dev/docs/latest/configuration>

## Known-current paths

| Concept       | User/global                                | Project                                                |
| ------------- | ------------------------------------------ | ------------------------------------------------------ |
| Skills        | `~/.pi/agent/skills/`, `~/.agents/skills/` | `.pi/skills/`, `.agents/skills/` (cwd + ancestors)     |
| Subagents     | **Not supported**                          | **Not supported**                                      |
| Instructions  | `~/.pi/agent/AGENTS.md`                    | `AGENTS.md` or `CLAUDE.md` in cwd                      |

## Things to capture in the notes column

- **Subagents**: explicitly omitted by design. Docs suggest spawning Pi via
  `tmux`, building an extension, or installing a package to compose behavior.
- **Skill discovery extras**: package-bundled `skills/`, `pi.skills` field in
  `package.json`, a `skills` array in settings, and repeatable `--skill <path>`
  CLI flag.
- **Instructions discovery**: also reads `AGENTS.md` / `CLAUDE.md` in any
  parent directory (walks up the tree).
- **System prompt overrides**: `.pi/SYSTEM.md` or `~/.pi/agent/SYSTEM.md`
  replaces the system prompt; `APPEND_SYSTEM.md` appends to it.
- **Disable context files** with `--no-context-files`.
- **Cross-agent compatibility**: Pi reads `CLAUDE.md` / `AGENTS.md` so a single
  project can drive multiple agents.

## Search queries that have worked

- `pi.dev skills path`
- `pi.dev AGENTS.md context files`
- `pi.dev system prompt override`
- `pi.dev configuration settings`
