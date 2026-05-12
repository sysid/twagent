"""Selection for `--select` and `--interactive` (FR-021).

Generalised from twmcp's selector — applies to skills, subagents, prompts,
and servers (the four list-shaped artifact types).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Set
from typing import TYPE_CHECKING

from simple_term_menu import TerminalMenu

if TYPE_CHECKING:
    from twagent.config import Configuration

logger = logging.getLogger(__name__)

_NONE_KEYWORD = "none"

# Capability name → registry attribute on Configuration
_KIND_TO_REGISTRY = {
    "skills": "skills",
    "subagents": "subagents",
    "prompts": "prompts",
    "servers": "servers",
}


def parse_select_value(value: str) -> list[str]:
    """Parse comma-separated names. The keyword `none` returns []."""
    logger.debug("selector.parse_select_value: value=%r", value)
    if value == _NONE_KEYWORD:
        return []
    names = [n.strip() for n in value.split(",") if n.strip()]
    if not names:
        raise ValueError("No names provided. Use --select none for empty selection.")
    if _NONE_KEYWORD in names:
        raise ValueError(f"{_NONE_KEYWORD!r} is reserved; use --select none alone.")
    return names


def validate_names(names: list[str], available: Set[str], kind: str) -> list[str]:
    """Verify every name is in `available`; raise listing unknown."""
    logger.debug(
        "selector.validate_names: kind=%s requested=%d available=%d",
        kind,
        len(names),
        len(available),
    )
    unknown = [n for n in names if n not in available]
    if unknown:
        avail = ", ".join(sorted(available))
        bad = ", ".join(f'"{n}"' for n in unknown)
        raise ValueError(f"Unknown {kind}(s): {bad}\n  Available: {avail}")
    return names


def resolve_profile(profile_name: str, kind: str, config: "Configuration") -> list[str]:
    """Return the per-kind member list for a profile (post-`extends` expansion).

    Generalised from twmcp's `resolve_profile_servers` — works for any of
    the four list-shaped artifact kinds.
    """
    logger.debug(
        "selector.resolve_profile: profile=%s kind=%s",
        profile_name,
        kind,
    )
    from twagent.deploy import expand_profile  # local import: avoid cycle

    if profile_name not in config.profiles:
        avail = ", ".join(sorted(config.profiles)) or "(none defined)"
        raise ValueError(f'Unknown profile "{profile_name}"\n  Available: {avail}')
    if kind not in _KIND_TO_REGISTRY:
        raise ValueError(f"Unknown kind: {kind}. Allowed: {list(_KIND_TO_REGISTRY)}")
    expanded = expand_profile(config, profile_name)
    return list(expanded.get(kind, []))


def is_interactive_terminal() -> bool:
    return sys.stdin.isatty()


def select_interactive(
    items: dict[str, str],
    preselected: "Set[str] | None" = None,
    title: str = "Select items (Space=toggle, Enter=confirm, Esc=cancel):",
) -> list[str] | None:
    """Multi-select picker. `items` maps name → label suffix (e.g. type).

    Returns chosen names; [] if user accepted nothing; None if cancelled.
    """
    logger.debug(
        "selector.select_interactive: items=%d preselected=%d",
        len(items),
        len(preselected) if preselected else 0,
    )
    names = list(items.keys())
    labels = [f"{n} {items[n]}".strip() for n in names]
    pre_labels: list[str] | None = None
    if preselected:
        pre_labels = [f"{n} {items[n]}".strip() for n in names if n in preselected]
    menu = TerminalMenu(
        labels,
        multi_select=True,
        multi_select_select_on_accept=False,
        multi_select_empty_ok=True,
        show_multi_select_hint=True,
        title=title,
        preselected_entries=pre_labels,
    )
    chosen = menu.show()
    if chosen is None:
        if menu.chosen_accept_key is not None:
            return []
        return None
    if isinstance(chosen, int):
        chosen = (chosen,)
    return [names[i] for i in chosen]
