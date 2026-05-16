# copilot-vscode

GitHub Copilot extension for Visual Studio Code. Documentation lives at
`code.visualstudio.com/docs/copilot/` and
`docs.github.com/en/copilot/customizing-copilot/`.

Last verified: 2026-05-16. Re-verify before writing report cells.

## Canonical doc pages

- Custom instructions (VS Code): <https://code.visualstudio.com/docs/copilot/copilot-customization>
- Prompt files: <https://code.visualstudio.com/docs/copilot/copilot-customization#_prompt-files>
- Custom chat modes: <https://code.visualstudio.com/docs/copilot/chat/chat-modes>
- MCP in VS Code: <https://code.visualstudio.com/docs/copilot/chat/mcp-servers>
- GitHub-hosted instructions docs: <https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions>

## Concept mapping (be careful — IDE products do not have 1:1 parity with CLIs)

| Concept       | Closest VS Code analogue                                                                       |
| ------------- | ---------------------------------------------------------------------------------------------- |
| Skills        | **Not supported** as a first-class feature. Closest: **prompt files** and **chat modes**.      |
| Subagents     | **Not supported**. Closest: **custom chat modes** (`*.chatmode.md`).                           |
| Instructions  | **Supported.** `.github/copilot-instructions.md`, `.github/instructions/**/*.instructions.md`. |

## Known-current paths

| Concept           | User/global                                              | Project / workspace                                                |
| ----------------- | -------------------------------------------------------- | ------------------------------------------------------------------ |
| Instructions      | VS Code user `settings.json` → `github.copilot.chat.codeGeneration.instructions` | `.github/copilot-instructions.md`, `.github/instructions/**/*.instructions.md` (with `applyTo:` glob) |
| Prompt files      | `<user-profile>/prompts/*.prompt.md`                     | `.github/prompts/*.prompt.md`                                      |
| Chat modes        | `<user-profile>/chatmodes/*.chatmode.md`                 | `.github/chatmodes/*.chatmode.md`                                  |
| MCP servers       | VS Code user `settings.json` → `mcp.servers`             | `.vscode/mcp.json`                                                 |

## Things to capture in the notes column

- **Enable flags**: prompt files and chat modes are gated by settings
  (`chat.promptFiles`, `chat.modeFiles`) — note current default.
- **`applyTo:` glob** in `.instructions.md` frontmatter scopes the rule to
  matching files.
- **User-profile location** is OS-specific (`%APPDATA%\Code\User`,
  `~/Library/Application Support/Code/User`, `~/.config/Code/User`) — mention
  this rather than picking one.
- **Workspace vs user settings**: project `.vscode/settings.json` overrides
  user settings for that workspace.
- **GitHub-managed instructions** can be set repo-level in the Copilot policy
  UI and apply even without a file on disk.

## Search queries that have worked

- `vscode copilot custom instructions`
- `vscode copilot prompt files location`
- `vscode copilot chat modes path`
- `vscode copilot MCP servers settings`
