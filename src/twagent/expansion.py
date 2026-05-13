"""Profile-expansion logic, isolated from deploy/diff/selector.

Moved here to break the near-circular import between `deploy.py` and
`selector.py`. This module depends only on `config.py`. Public surface:

  - `expand_profile(config, profile_name) -> ProfileExpansion`
  - `needed_capabilities(expanded) -> set[str]`

Both `deploy.py` and `diff.py` and `selector.py` import from here at
module scope; no more function-scope "avoid cycle" imports.
"""

from __future__ import annotations

import logging

from twagent.config import EXPANSION_KINDS, Configuration, ProfileExpansion

logger = logging.getLogger(__name__)


def expand_profile(config: Configuration, profile_name: str) -> ProfileExpansion:
    """Expand a profile's `extends` chain.

    Per data-model.md § Composition semantics: depth-first, parent-first then
    child; first occurrence wins on dedup; per-type (not cross-type).
    """
    logger.debug("expansion.expand_profile: profile=%s", profile_name)
    buckets: dict[str, list[str]] = {kind: [] for kind in EXPANSION_KINDS}
    visited: set[str] = set()

    def _walk(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        prof = config.profiles[name]
        for parent in prof.extends:
            _walk(parent)
        for kind in buckets:
            for ref in getattr(prof, kind):
                if ref not in buckets[kind]:
                    buckets[kind].append(ref)

    _walk(profile_name)
    logger.debug(
        "expansion.expand_profile %s → instructions=%d skills=%d subagents=%d "
        "prompts=%d servers=%d",
        profile_name,
        len(buckets["instructions"]),
        len(buckets["skills"]),
        len(buckets["subagents"]),
        len(buckets["prompts"]),
        len(buckets["servers"]),
    )
    return ProfileExpansion(**buckets)


def needed_capabilities(expanded: ProfileExpansion) -> set[str]:
    """Map a per-kind selection to the set of CAPABILITY names it touches.

    `servers` selection kind → `mcp` capability; everything else maps 1:1.
    Used by both `apply_global` (when --select overrides) and `apply_here`
    to skip capabilities the selection doesn't contribute to.
    """
    needed: set[str] = set()
    for kind, members in expanded.items():
        if not members:
            continue
        needed.add("mcp" if kind == "servers" else kind)
    return needed
