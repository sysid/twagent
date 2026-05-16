# copilot-intellij

GitHub Copilot plugin for IntelliJ IDEA and other JetBrains IDEs. Documentation
lives at `docs.github.com/en/copilot/configuring-github-copilot/configuring-github-copilot-in-the-jetbrains-ides`
and the plugin marketplace.

Last verified: 2026-05-16. Re-verify before writing report cells — JetBrains
Copilot lags VS Code in feature parity, and that gap moves every release.

## Canonical doc pages

- JetBrains Copilot overview: <https://docs.github.com/en/copilot/configuring-github-copilot/configuring-github-copilot-in-the-jetbrains-ides>
- Custom instructions support: <https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions>
- Copilot Chat in JetBrains: <https://docs.github.com/en/copilot/using-github-copilot/copilot-chat/using-github-copilot-chat-in-your-ide>
- JetBrains plugin page: <https://plugins.jetbrains.com/plugin/17718-github-copilot>

## Concept mapping (parity is narrower than VS Code)

| Concept       | Closest JetBrains analogue                                                  |
| ------------- | --------------------------------------------------------------------------- |
| Skills        | **Not supported.** No plugin-side equivalent at time of verification.       |
| Subagents     | **Not supported.** No custom agent registry; chat uses a single agent.      |
| Instructions  | **Supported.** `.github/copilot-instructions.md` is read by the JetBrains plugin. |

## Known-current paths

| Concept           | User/global                                                      | Project                                                |
| ----------------- | ---------------------------------------------------------------- | ------------------------------------------------------ |
| Instructions      | IDE Settings → Tools → GitHub Copilot → Chat (toggle/override)   | `.github/copilot-instructions.md`                      |
| MCP servers       | IDE Settings → GitHub Copilot → MCP (if MCP-enabled build)       | (project MCP support varies — verify against current docs) |

## Things to capture in the notes column

- **Feature gap vs VS Code**: prompt files, chat modes, and `*.instructions.md`
  with `applyTo` are typically VS-Code-only. If JetBrains has caught up, note
  it; otherwise call out the gap.
- **Settings UI is the primary surface** (not a settings JSON file). JetBrains
  stores plugin settings under `<config-dir>/options/` per IDE — mention this
  rather than listing a brittle path.
- **GitHub-managed org/repo instructions** apply to JetBrains Copilot just like
  VS Code Copilot — these come from the Copilot policy UI, not the local
  filesystem.
- **Plugin versioning**: feature support tracks the JetBrains plugin version,
  not the VS Code extension version — they ship on different cadences.

## Search queries that have worked

- `github copilot jetbrains custom instructions`
- `github copilot intellij copilot-instructions.md`
- `copilot jetbrains chat modes` (likely returns "not supported")
- `copilot jetbrains MCP support`
