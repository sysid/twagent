# ADR 0001: Promote prompts to skills rather than curating a prompts registry

- Status: Accepted
- Date: 2026-09-18
- Deciders: Tom, Claude

## Context

Five loose prompt files (`twskls/prompts/tw-confluence-report.md`,
`devops-binx/prompts/active/*.md`) were candidates for registration as twagent
`prompts` artifacts. Investigating how best to register them showed the premise
was wrong — the `prompts` kind buys almost nothing over `skills`, and costs
reach.

Findings that forced the decision:

1. **No context saving.** The folklore that slash commands are "free" while
   skills cost context is false in current Claude Code. The three legacy files
   in `~/.claude/commands/` (`tw-handoff.md`, `tw-init.md`, `tw-pickup.md`)
   appear in the model-visible roster with descriptions —
   `tw-pickup.md` has no frontmatter at all, and the harness derived its
   description from the file's first line. Prompts and skills occupy the same
   roster and both are `/name`-invocable. (Verified for Claude Code only.)

2. **Prompts reach fewer agents.** `codex` and `pi` declare no `prompts`
   capability — codex's prompts are deprecated upstream. Only `claude-code` and
   `copilot-cli` do. Skills reach all four, codex via the cross-vendor
   `~/.agents/skills`.

3. **Prompts have no structure budget.** A prompt is one flat file: no
   `scripts/`, no `references/`, no progressive disclosure, no tests. 20 of 35
   twskls skills ship `scripts/`; 11 ship `references/`. `tw-confluence-report.md`
   is 13.9 KB — larger than `tw-review/SKILL.md` (13.6 KB). It was already
   skill-shaped and misfiled.

4. **Hand-written prompt entries have an extension trap.** `deploy.py:163` is
   `target = target_dir / name` with no suffix logic, so `[prompts.adr]` deploys
   as `commands/adr` — no `.md`, invisible to Claude Code. The artifact name must
   literally carry the extension (`[prompts."adr.md"]`). The shipped fixture
   `tests/fixtures/sample_config.toml:105` has this bug latent. Skills are
   immune: the name is a directory.

## Decision

twagent will not curate a `prompts` registry. Artifacts worth deploying are
authored as **skills** in `twskls/<name>/SKILL.md` and registered as
`[skills.<name>]`. A prompt too trivial to justify a skill directory is not a
twagent artifact at all — it stays a bkmr `_snip_`, which is where the four
`devops-binx/prompts/active/*.md` files already live.

Authoring rule:

> **Skill** — has steps, needs helper scripts or references, or must reach codex/pi.
> **bkmr snippet** — a one-shot template you paste and fill.
> **Prompt artifact** — only when it arrives inside a third-party plugin.

## Consequences

**Good**
- One artifact kind to author, review and test. No parallel prompt taxonomy.
- Every promoted artifact gains codex and pi reach for free.
- The `.md`-in-the-name trap is never hit by hand-written config.
- Promoted content becomes testable under the existing `twskls.py` / `make` harness.

**Bad**
- Ceremony for small content. A 1.7 KB template like `create-prompt.md` cannot
  become a twagent artifact without a directory, frontmatter and a description —
  which is precisely why the rule routes it to bkmr instead.
- Content now has two homes (twskls skills, bkmr snippets) with no automatic
  promotion path between them. Promotion is a manual, deliberate act.

**Ugly**
- The `prompts` capability **cannot be removed** from `[agents.claude-code]` and
  `[agents.copilot-cli]`. The `aaa-security-remediation` plugin injects
  `security-explain.prompt.md` and `security-fix.prompt.md` via its manifest;
  dropping the capability would silently stop deploying them. The kind stays
  supported in code and config — it is simply not something we author into.
- The three legacy files in `configs/claude/dot-claude/commands/` are
  real files in a twagent deploy target. `link_artifacts` reaps orphan *symlinks*
  only (`deploy.py:157`), so they are permanently unmanaged and invisible to
  copilot-cli. Under this ADR they are promotion candidates; until promoted, that
  directory has two owners.

## Alternatives Rejected

- **Register the five files as `[prompts."x.md"]` entries** — five config blocks
  carrying `.md` in the artifact name, invisible to codex and pi, no structure
  budget as the content grows. Optimises for the wrong axis.
- **Ship them as a twagent plugin (`plugin.json` + `[plugins.tw-prompts]`)** —
  solves the extension trap and gives auto-discovery, but still reaches only two
  of four agents and still forbids `scripts/`/`references/`. It would also add a
  twagent-private manifest convention to twskls (twagent reads `<source>/plugin.json`,
  not Claude Code's `.claude-plugin/plugin.json`), buying a second, incompatible
  packaging format in the repo.
- **Point one `[prompts.tw]` entry at the whole directory** — one symlink, so the
  individual prompts never appear in `twagent artefacts`, `info` or `diff`, and
  invocation becomes namespaced (`/tw:tw-confluence-report`). Copilot CLI's
  handling of a subdirectory there is unverified, so it would half-work silently.
