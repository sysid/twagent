# copilot-cli

GitHub Copilot CLI (`copilot`, formerly `gh copilot`). Documentation lives at
`docs.github.com/en/copilot/how-tos/copilot-cli/`.

Last verified: 2026-05-16. Re-verify before writing report cells.

## Canonical doc pages

- Skills: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills>
- Custom agents: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli>
- Custom instructions: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions>

Note: the prior CLI reference URL `/reference/cli-commands/copilot` 404s as of
2026-05-16. The `--agent` flag is documented inline on the custom-agents page;
no replacement reference page found.

## Known-current paths

| Concept       | User/global                                                            | Project                                                              |
| ------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Skills        | `~/.copilot/skills/`, `~/.agents/skills/`                              | `.github/skills/`, `.claude/skills/`, `.agents/skills/`              |
| Agents        | `~/.copilot/agents/<name>.agent.md`                                    | `.github/agents/<name>.agent.md`                                     |
| Instructions  | `$HOME/.copilot/copilot-instructions.md`                               | `AGENTS.md` (root, primary), `.github/copilot-instructions.md`       |

## Things to capture in the notes column

- **Cross-agent compatibility (skills)**: Copilot CLI explicitly reads
  `.claude/skills/` and `.agents/skills/` for cross-agent reuse. The skills
  page does **not** mention `CLAUDE.md`/`GEMINI.md` — those are instructions,
  not skills.
- **Cross-agent compatibility (instructions)**: `CLAUDE.md` and `GEMINI.md`
  are accepted but **only at the repo root**.
- **Skill folder requirement**: must contain a `SKILL.md` (case-sensitive);
  directory name lowercase-with-hyphens.
- **Nested `AGENTS.md`** is read as additional (lower-weight) context; root
  `AGENTS.md` is the primary.
- **`.github/instructions/**/*.instructions.md`** with an `applyTo:` glob in
  frontmatter — scoped instructions. `AGENTS.md` itself does NOT use `applyTo:`.
- **Env var**: `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` adds extra dirs scanned for
  `AGENTS.md` and `.github/instructions/**/*.instructions.md`.
- **Agent invocation**: `/agent` (interactive picker), explicit name in
  prompt, description match, or `copilot --agent <name>`. User-scope agents
  win on name conflict.

## Search queries that have worked

- `github copilot cli skills location`
- `github copilot cli AGENTS.md`
- `github copilot cli custom agents path`
- `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`
