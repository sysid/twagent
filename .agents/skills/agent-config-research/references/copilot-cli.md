# copilot-cli

GitHub Copilot CLI (`copilot`, formerly `gh copilot`). Documentation lives at
`docs.github.com/en/copilot/how-tos/copilot-cli/`.

Last verified: 2026-05-16. Re-verify before writing report cells.

## Canonical doc pages

- Skills: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills>
- Custom agents: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli>
- Custom instructions: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions>
- CLI reference (flags): <https://docs.github.com/en/copilot/reference/cli-commands/copilot>

## Known-current paths

| Concept       | User/global                                                            | Project                                                              |
| ------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Skills        | `~/.copilot/skills/`, `~/.agents/skills/`                              | `.github/skills/`, `.claude/skills/`, `.agents/skills/`              |
| Agents        | `~/.copilot/agents/<name>.agent.md`                                    | `.github/agents/<name>.agent.md`                                     |
| Instructions  | `$HOME/.copilot/copilot-instructions.md`                               | `AGENTS.md` (root, primary), `.github/copilot-instructions.md`       |

## Things to capture in the notes column

- **Cross-agent compatibility**: Copilot CLI deliberately reads `.claude/skills/`
  and `CLAUDE.md` / `GEMINI.md` so users don't have to duplicate config.
- **Skill folder requirement**: must contain a `SKILL.md`; directory name
  lowercase-with-hyphens.
- **Nested `AGENTS.md`** is read (directory-scoped context).
- **`.github/instructions/**/*.instructions.md`** with an `applyTo:` glob in
  frontmatter — scoped instructions.
- **Env var**: `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` adds extra instruction dirs.
- **Agent invocation**: `/agent`, `copilot --agent <name>`, or description
  match. User-scope agents win on name conflict.

## Search queries that have worked

- `github copilot cli skills location`
- `github copilot cli AGENTS.md`
- `github copilot cli custom agents path`
- `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`
