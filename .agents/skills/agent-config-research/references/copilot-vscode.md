# copilot-vscode

GitHub Copilot extension for Visual Studio Code. Documentation lives at
`code.visualstudio.com/docs/copilot/` and
`docs.github.com/en/copilot/customizing-copilot/`.

Last verified: 2026-05-16. Re-verify before writing report cells.

## Canonical doc pages

- Custom instructions: <https://code.visualstudio.com/docs/copilot/customization/custom-instructions>
- Prompt files: <https://code.visualstudio.com/docs/copilot/customization/prompt-files>
- Custom agents (formerly chat modes): <https://code.visualstudio.com/docs/copilot/customization/custom-chat-modes>
- AI customization overview: <https://code.visualstudio.com/docs/copilot/copilot-customization>
- MCP in VS Code: <https://code.visualstudio.com/docs/copilot/chat/mcp-servers>
- GitHub-hosted instructions docs: <https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions>

## Concept mapping (be careful — IDE products do not have 1:1 parity with CLIs)

| Concept       | Closest VS Code analogue                                                                            |
| ------------- | --------------------------------------------------------------------------------------------------- |
| Skills        | **Not supported** as a first-class feature. Closest: **prompt files** (`*.prompt.md`) and **custom agents** (`*.agent.md`). |
| Subagents     | **Not supported**. Closest: **custom agents** (`*.agent.md`) — `*.chatmode.md` is deprecated.       |
| Instructions  | **Supported.** `.github/copilot-instructions.md`, `.github/instructions/**/*.instructions.md`, plus `AGENTS.md` and `CLAUDE.md`. |

## Known-current paths

| Concept           | User/global                                                                                         | Project / workspace                                                                                  |
| ----------------- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Instructions      | settings.json → `chat.instructionsFilesLocations`; auto-discovered `~/.copilot/instructions/`, `~/.claude/rules/`, `<user-profile>/User/` | `.github/copilot-instructions.md`, `.github/instructions/**/*.instructions.md` (with `applyTo:` glob) |
| Prompt files      | `<user-profile>/prompts/*.prompt.md`                                                                | `.github/prompts/*.prompt.md`                                                                        |
| Custom agents     | `~/.copilot/agents/*.agent.md` or VS Code user-profile data dir                                     | `.github/agents/*.agent.md` (also reads `.claude/agents/*.agent.md`)                                 |
| MCP servers       | User-profile `mcp.json` (opened via "MCP: Open User Configuration")                                 | `.vscode/mcp.json`                                                                                   |

## Things to capture in the notes column

- **`*.chatmode.md` is deprecated** — rename to `*.agent.md`. The old
  `.github/chatmodes/` location is stale; use `.github/agents/`.
- **Settings keys**: `chat.instructionsFilesLocations` (instructions),
  `chat.promptFilesLocations` (prompts), `chat.agentFilesLocations` (agents,
  replaces `chat.modeFilesLocations`). Parent-repo discovery toggled by
  `chat.useCustomizationsInParentRepositories`.
- **`AGENTS.md` / `CLAUDE.md` are first-class** instruction sources, gated by
  `chat.useAgentsMdFile` and `chat.useClaudeMdFile`. Experimental nested
  `AGENTS.md` via `chat.useNestedAgentsMdFiles`.
- **`applyTo:` glob** in `.instructions.md` frontmatter scopes the rule to
  matching files — accepts comma-separated globs e.g. `"**/*.ts,**/*.tsx"`.
- **User-profile location** is OS-specific (`%APPDATA%\Code\User`,
  `~/Library/Application Support/Code/User`, `~/.config/Code/User`) — mention
  this rather than picking one.
- **MCP** uses dedicated `mcp.json` files (workspace: `.vscode/mcp.json`,
  user: user-profile `mcp.json`), NOT a `settings.json` key. JSON root key
  is `"servers"`.
- **Workspace vs user settings**: project `.vscode/settings.json` overrides
  user settings for that workspace.
- **GitHub-managed instructions** can be set repo-level in the Copilot policy
  UI and apply even without a file on disk.

## Search queries that have worked

- `vscode copilot custom instructions`
- `vscode copilot prompt files location`
- `vscode copilot custom agents agent.md`
- `vscode copilot MCP servers mcp.json`
