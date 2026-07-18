# twagent
Python CLI that materialises one canonical TOML (`~/.config/twagent/config.toml`) into per-agent config — Claude Code, Copilot CLI, Pi, Codex, VS Code, opencode.

## Commands
- Single test: `uv run pytest tests/test_foo.py::test_bar` (bare `pytest` will miss deps; always go through `uv run`).
- Full test suite: `make test` — excludes `integration` and `experimentation` markers; coverage gate is `fail_under = 85` (CI fails silently below this).
- Lint pipeline: `make lint` (ruff) **and** `make ty` (Astral's `ty` typechecker — NOT mypy). Both also run in pre-commit; install with `make pre-commit-install`.
- Version bump: ONLY via `make bump-{patch,minor,major}`. The version is mirrored across `VERSION`, `pyproject.toml`, and `src/twagent/__init__.py` — never edit by hand.

## Architectural Invariants
- `--select` is **exhaustive**: only the artifact *kinds* derivable from the selection are deployed. Selecting a servers-only profile rewrites MCP files and nothing else. NEVER assume `apply --select X` is a superset of an earlier deploy.
- `apply` is the **only** mutator of agent directories. It removes orphan symlinks on each run (see commit `becf250`). NEVER hand-delete files under `~/.claude/skills`, `~/.copilot/skills`, etc. — re-run `apply` instead.
- Current TOML schema is `schema_version = 4`. v2 had `[[scopes]]`; if you see that, it's stale.
- Adding a new agent target is **config-only** UNLESS its MCP wire format is genuinely new — then add a translator in `src/twagent/mcp.py`. Do not invent new MCP shapes elsewhere.
- `FormatProfile` has two escape hatches for formats that are not a point in the declarative space: `serializer` (`json`/`toml`) and `builder`. Prefer the shared `_build_server_dict` plus declarative axes; reach for a `builder` only when a format renames or omits keys wholesale (see `codex`). Do NOT add per-format boolean flags to `FormatProfile`.
- `mcp.write_config` MUST update-in-place (`existing.update(compiled)`), never rebuild the document from parsed data — that is what preserves foreign tables, comments, and formatting in harness-owned state files (`~/.claude.json`, `~/.codex/config.toml`).
- Profile `extends` resolution is depth-first, parent-first, **first-occurrence wins** on duplicate names.

## Strict Antipatterns
- NEVER add a `mypy` config or import — typechecking is `ty` only.
- NEVER reintroduce `[[scopes]]` blocks; the v2→v3 migration deliberately killed them.
- NEVER bypass `make bump-*` to update the version string in one file.

## Gotchas
- `fzf` for `--interactive` requires **>= 0.41** (README says 0.35; the code raises below 0.41 — see `selector.py::_detect_fzf`). Set `TWAGENT_NO_FZF=1` to force the built-in fallback.
- `RUN_ENV=local` in the Makefile is currently dead — no code reads it. Don't add features that depend on it without wiring a reader first.
- MCP `${VAR}` references are resolved by the agent at runtime, never by `twagent`. Claude Code and Copilot retain placeholders; Codex translates them to `env_vars`, `env_http_headers`, or `bearer_token_env_var`. `${VAR:-default}` is rejected; set defaults in the launch environment.
- `pi` has no MCP format until a specific extension is selected and tested. VS Code and opencode accept literal MCP values but reject runtime references because their expansion contracts are unverified.
- **codex** is the only TOML target and the only agent with a custom `builder`. Its shape is pinned to codex's own `McpServerConfig` (`codex-rs/config/src/mcp_types.rs`): NO `type` key (the transport enum is `#[serde(untagged, deny_unknown_fields)]`, so an emitted `type` is a hard parse error, not noise), runtime headers use `env_http_headers`/`bearer_token_env_var`, static headers use `http_headers`, and `tools` → `enabled_tools` EXCEPT the `["*"]` wildcard which must be omitted (`enabled_tools` is a literal tool-name list; omitting is how codex spells "all tools"). Dropping a non-wildcard `tools` would silently widen the server — don't. **No sse transport** — sse servers are skipped with a warning. Codex subagents are `.toml` and prompts are deprecated upstream, which is why codex declares only `instructions`/`skills`/`mcp`.
- Codex skills belong in `~/.agents/skills` (the cross-vendor location), NOT `~/.codex/skills` — the latter is deprecated in codex's own `loader.rs`.

## Domain glossary
- **artifact**: one skill / subagent / prompt / instruction / MCP server. Globally-unique `name`, registered in one of five registries.
- **profile**: named bundle of artifact references; composable via `extends`.
- **global_profile**: per-agent default profile deployed by `apply --global`.
- **kind**: one of `instructions | skills | subagents | prompts | mcp` — drives which paths a deploy touches.

## When in doubt
- Behavioural questions: `docs/reference/commands.md` and `docs/reference/config.md`.
- Conceptual model: `docs/overview.md`.
- Worked example: `tests/fixtures/sample_config.toml` is the canonical schema-v4 reference.
