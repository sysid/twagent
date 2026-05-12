import re
from pathlib import Path

# Matches ${VAR_NAME} and ${VAR_NAME:-default_value}
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def find_unresolved(text: str, variables: dict[str, str]) -> list[str]:
    """Return names of variables in text that can't be resolved."""
    missing: list[str] = []
    for match in _VAR_PATTERN.finditer(text):
        name = match.group(1)
        default = match.group(2)
        if name not in variables and default is None:
            missing.append(name)
    return missing


def resolve_variables(text: str, variables: dict[str, str]) -> str:
    """Resolve ${VAR} and ${VAR:-default} placeholders in text.

    Raises ValueError listing ALL unresolved variables (no default, not in map).
    """
    missing: list[str] = []

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        default = match.group(2)
        if name in variables:
            return variables[name]
        if default is not None:
            return default
        missing.append(name)
        return match.group(0)

    result = _VAR_PATTERN.sub(_replace, text)

    if missing:
        var_list = ", ".join(missing)
        raise ValueError(f"Unresolved variables: {var_list}")

    return result


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a dict. Supports KEY=VALUE, comments, quotes."""
    if not path.exists():
        raise FileNotFoundError(f"Dotenv file not found: {path}")

    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value

    return result
