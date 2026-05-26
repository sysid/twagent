# twagent
Python CLI that materialises one canonical TOML (`~/.config/twagent/config.toml`) into per-agent config — Claude Code, Copilot CLI, Pi, VS Code, opencode.

## Commands
- Single test: `uv run pytest tests/test_foo.py::test_bar` (bare `pytest` will miss deps; always go through `uv run`).
- Full test suite: `make test` — excludes `integration` and `experimentation` markers; coverage gate is `fail_under = 85` (CI fails silently below this).
- Lint pipeline: `make lint` (ruff) **and** `make ty` (Astral's `ty` typechecker — NOT mypy). Both also run in pre-commit; install with `make pre-commit-install`.
- Version bump: ONLY via `make bump-{patch,minor,major}`. The version is mirrored across `VERSION`, `pyproject.toml`, and `src/twagent/__init__.py` — never edit by hand.

## Architectural Invariants
- `--select` is **exhaustive**: only the artifact *kinds* derivable from the selection are deployed. Selecting a servers-only profile rewrites MCP files and nothing else. NEVER assume `apply --select X` is a superset of an earlier deploy.
- `apply` is the **only** mutator of agent directories. It removes orphan symlinks on each run (see commit `becf250`). NEVER hand-delete files under `~/.claude/skills`, `~/.copilot/skills`, etc. — re-run `apply` instead.
- Current TOML schema is `schema_version = 3`. v2 had `[[scopes]]`; if you see that, it's stale.
- Adding a new agent target is **config-only** UNLESS its MCP wire format is genuinely new — then add a translator in `src/twagent/mcp.py`. Do not invent new MCP shapes elsewhere.
- Profile `extends` resolution is depth-first, parent-first, **first-occurrence wins** on duplicate names.

## Strict Antipatterns
- NEVER add a `mypy` config or import — typechecking is `ty` only.
- NEVER reintroduce `[[scopes]]` blocks; the v2→v3 migration deliberately killed them.
- NEVER bypass `make bump-*` to update the version string in one file.

## Gotchas
- `fzf` for `--interactive` requires **>= 0.41** (README says 0.35; the code raises below 0.41 — see `selector.py::_detect_fzf`). Set `TWAGENT_NO_FZF=1` to force the built-in fallback.
- `RUN_ENV=local` in the Makefile is currently dead — no code reads it. Don't add features that depend on it without wiring a reader first.
- `${VAR}` interpolation in MCP `env`/`headers` reads `os.environ` overlaid on the optional `env_file` dotenv (path is relative to the config file). Missing variables raise unless `${VAR:-default}` is used. Interpolation semantics match the legacy `twmcp` project.
- Dry-run output redacts resolved `${VAR}` values by default; pass `--show-secrets` to see them. Don't paste dry-run output into issues without checking.

## Domain glossary
- **artifact**: one skill / subagent / prompt / instruction / MCP server. Globally-unique `name`, registered in one of five registries.
- **profile**: named bundle of artifact references; composable via `extends`.
- **global_profile**: per-agent default profile deployed by `apply --global`.
- **kind**: one of `instructions | skills | subagents | prompts | mcp` — drives which paths a deploy touches.

## When in doubt
- Behavioural questions: `docs/reference/commands.md` and `docs/reference/config.md`.
- Conceptual model: `docs/overview.md`.
- Worked example: `tests/fixtures/sample_config.toml` is the canonical schema-v3 reference.
