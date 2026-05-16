---
name: agent-config-research
description: Researches where each major coding agent (Claude Code, Copilot CLI, Copilot for VS Code, Copilot for IntelliJ, Pi) reads its skills, subagents, and instruction files — both at user/global scope and per-project scope — and produces a side-by-side comparison report in three Markdown tables (Skills / Agents-subagents / Instructions). Use this skill whenever the user wants to know where a coding agent stores or loads config, where to drop a CLAUDE.md / AGENTS.md / copilot-instructions.md / skill folder, how multiple agents discover the same file, or asks to compare config locations across agents. Trigger on phrases like "agent config locations", "where does X read its instructions", "compare config paths", "where do I put a skill for Y", "agent comparison table", "global vs project config for <agent>", or any request that involves enumerating filesystem locations, settings keys, or env-var overrides for one or more of the supported agents — even when the user does not explicitly say "research".
---

# Agent Config Research

Produce a side-by-side comparison of where each supported coding agent reads its
**skills**, **subagents**, and **always-on instructions** — at user/global scope,
at project scope, and anywhere else (env vars, settings keys, plugins,
managed-policy paths). The output is three Markdown tables with footnote
citations to the **official upstream docs**, matching the format in
`resources/agent-config.md` at the twagent repo root.

The whole point of this skill is that agent docs drift fast (every release
moves a path or adds an override). A request like "where do I put a project
skill for Copilot CLI?" cannot be answered confidently from training data
alone — you must verify against the live docs before writing the row.

## Agents covered

| Slug                | What it is                                              |
| ------------------- | ------------------------------------------------------- |
| `claude-code`       | Anthropic's official CLI for Claude                     |
| `copilot-cli`       | GitHub Copilot CLI (`copilot` / `gh copilot`)           |
| `copilot-vscode`    | GitHub Copilot extension for Visual Studio Code         |
| `copilot-intellij` | GitHub Copilot plugin for IntelliJ / JetBrains IDEs     |
| `pi`                | Pi Coding Agent (pi.dev)                                |

If the user names only a subset, restrict the tables to those rows — do not
invent agents outside this list, and do not silently drop ones they asked for.

## Workflow

1. **Confirm scope.** Default to all five agents and all three concept tables
   (Skills / Agents-subagents / Instructions). If the user specified a subset
   (e.g. "just the CLI agents", "only instructions"), trim accordingly.
2. **Read the per-agent reference file** in `references/<agent>.md`. Each one
   lists the canonical doc URLs, the known-current paths as of the file's
   "Last verified" date, and the search queries that worked last time. Treat
   these as **starting points**, not as ground truth.
3. **Fetch the live docs.** For every cell you are about to write, WebFetch
   the corresponding upstream doc and pull the path/key directly out of the
   current page. If a page has moved, WebSearch for the new location before
   guessing. **Do not write a cell from memory** — agents reorganize their
   config layouts often enough that stale recall is the most common failure
   mode for this skill.
4. **Parallelize.** Per-agent research is independent. If the Agent tool is
   available, dispatch one subagent per agent in a single message and let them
   return structured findings. Otherwise WebFetch sequentially.
5. **Fill the template** in `assets/output-template.md`. Keep rows under
   ~120 chars wide (line-wrapping in monospace viewers is ugly). Use
   footnote-style `[1]`-style citations at the bottom — one per source URL —
   and reuse the same footnote when the same page covers multiple cells.
6. **Write or print.** Default to printing the Markdown to the conversation.
   If the user gave an output path, write the file there and report the path.

## Output template

Use this exact skeleton (also bundled at `assets/output-template.md` for
copy-paste):

```markdown
# Agent Config

## Skills

| Agent | User/global skills | Project skills | Other supported locations / notes |
| ----- | ------------------ | -------------- | --------------------------------- |
| ...   | ...                | ...            | ...                               |

## Agents / subagents

| Agent | User/global agents | Project agents | Other supported locations / notes |
| ----- | ------------------ | -------------- | --------------------------------- |
| ...   | ...                | ...            | ...                               |

## Instructions (always-on context)

| Agent | User/global instructions | Project instructions | Other supported locations / notes |
| ----- | ------------------------ | -------------------- | --------------------------------- |
| ...   | ...                      | ...                  | ...                               |

[1]: <url> "<page title>"
[2]: <url> "<page title>"
```

## Cell-content rules

These rules exist because the example output (`resources/agent-config.md`) is
the spec — readers scan these tables side-by-side and inconsistency breaks the
comparison.

- **Paths in backticks.** Always quote filesystem paths and settings keys
  with backticks (`` `~/.claude/skills/` ``). Bare paths get mangled by table
  renderers.
- **Wildcard the concrete name.** Use `<name>` as the placeholder when the
  path is `~/.something/agents/<name>.md`, not a real example. Real example
  names get mistaken for required filenames.
- **List multiple paths inline, comma-separated.** Don't put each path on its
  own line — the columns get unreadable.
- **"Not supported" is a valid answer.** If a concept genuinely doesn't exist
  for an agent (e.g. Pi has no subagents), write `Not supported` in both path
  columns and use the notes column to explain the closest alternative
  ("docs suggest spawning Pi via `tmux` instead"). Never fabricate a path.
- **Notes column carries override order, precedence, env vars, plugin
  loaders.** Anything that affects *which* of multiple matching files wins
  belongs here.
- **One footnote per source.** Cite the page that the cell content came from,
  with title. Reuse footnotes when the same doc page covers several cells —
  don't proliferate `[1] [2] [3]` for the same URL.

## Concept mapping for IDE agents

The IDE Copilots (`copilot-vscode`, `copilot-intellij`) don't have a 1:1
mapping for "skills" or "subagents" the way CLI agents do. The honest answer
is usually `Not supported` with a notes-column pointer to the nearest
analogue:

- **Skills** → closest analogues in Copilot IDE products are **custom chat
  modes** (`*.chatmode.md`), **prompt files** (`*.prompt.md`), or
  **MCP-loaded tools**. None of these are *called* skills. If the IDE
  doesn't ship a "skill" feature by that name, write `Not supported` and
  explain in notes.
- **Subagents** → IDE Copilots route through a single chat agent; multi-agent
  orchestration is via chat modes or extensions, not a first-class
  subagent registry. Default to `Not supported`.
- **Instructions** → both IDE Copilots *do* support custom instructions
  (`.github/copilot-instructions.md`, `.github/instructions/**/*.instructions.md`
  with `applyTo` globs). This row should be filled in fully.

When in doubt, verify with the live VS Code Copilot or JetBrains Copilot docs
before writing `Not supported` — feature parity changes release-to-release.

## Quality bar

Before returning, sanity-check the report:

- Every cell either has a concrete path/key, or says `Not supported` with
  context — no blank cells, no `?`, no "TBD".
- Every non-trivial claim has a footnote pointing to an official source
  (vendor docs > vendor blog > release notes > third-party only as last
  resort).
- Footnote URLs are reachable (if WebFetch returned 404 / redirect, follow
  it and update the URL).
- Agent column order matches across all three tables.
- No row exceeds ~120 chars when rendered (count the cell text, not the
  Markdown source).

## When NOT to use this skill

- The user wants to know *how to write* a skill / subagent / instruction
  file — that's a content question, not a location question. Use the
  relevant agent's authoring docs instead.
- The user only wants the answer for **one** location for **one** agent
  ("where does Claude Code put its global CLAUDE.md?") — a single
  WebFetch + one-line answer is faster than the full table.
- The user is asking about twagent's own internal config layout — that's
  the twagent README, not this skill.
