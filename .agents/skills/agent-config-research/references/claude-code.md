# claude-code

Anthropic's official CLI. Documentation lives at `code.claude.com/docs/en/`.

Last verified: 2026-05-16. Re-verify before writing report cells.

## Canonical doc pages

- Skills: <https://code.claude.com/docs/en/skills>
- Subagents: <https://code.claude.com/docs/en/sub-agents>
- Memory / instructions: <https://code.claude.com/docs/en/memory>
- Settings & file precedence: <https://code.claude.com/docs/en/settings>
- Plugins (skills/subagents namespacing): <https://code.claude.com/docs/en/plugins>

## Known-current paths

| Concept       | User/global                              | Project                            |
| ------------- | ---------------------------------------- | ---------------------------------- |
| Skills        | `~/.claude/skills/<name>/SKILL.md`       | `.claude/skills/<name>/SKILL.md`   |
| Subagents     | `~/.claude/agents/<name>.md`             | `.claude/agents/<name>.md`         |
| Instructions  | `~/.claude/CLAUDE.md`                    | `./CLAUDE.md`, `./.claude/CLAUDE.md` |

## Things to capture in the notes column

- **Precedence**: enterprise (managed-policy) > personal (`~/.claude/`) > project (`.claude/`).
- **Plugin skills/subagents** are namespaced and loaded from plugin manifests — mention briefly.
- **`CLAUDE.local.md`** is gitignored and loads alongside `CLAUDE.md`.
- **Nested `CLAUDE.md`** files in subdirectories are lazy-loaded when those paths are touched.
- **Managed-policy `CLAUDE.md`** cannot be excluded by the user — relevant for enterprise.
- **Invocation**: subagents are invoked via `@agent-<name>`, `/agents`, or `claude --agent <name>`.

## Search queries that have worked

- `claude code skills location`
- `claude code subagents path`
- `claude code CLAUDE.md precedence`
- `claude code settings.json hierarchy`
