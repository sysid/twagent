"""Recognize MCP runtime references without resolving their values."""

import re

# Matches ${VAR_NAME} and ${VAR_NAME:-default_value}
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def contains_variable_reference(text: str) -> bool:
    """Return whether text contains canonical `${VAR}` syntax."""
    return _VAR_PATTERN.search(text) is not None


def exact_variable_reference(text: str) -> str | None:
    """Return VAR for an exact `${VAR}` value without a default."""
    match = _VAR_PATTERN.fullmatch(text)
    if match is None or match.group(2) is not None:
        return None
    return match.group(1)


def contains_variable_default(text: str) -> bool:
    """Return whether text contains any `${VAR:-default}` reference."""
    return any(match.group(2) is not None for match in _VAR_PATTERN.finditer(text))
