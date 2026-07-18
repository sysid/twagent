"""Tests for MCP runtime-reference syntax recognition."""

import pytest

from twagent.interpolate import (
    contains_variable_default,
    contains_variable_reference,
    exact_variable_reference,
)


@pytest.mark.parametrize(
    "value",
    ["${TOKEN}", "Bearer ${TOKEN}", "prefix-${TOKEN}-suffix", "${TOKEN:-fallback}"],
)
def test_contains_variable_reference(value):
    assert contains_variable_reference(value)


def test_literal_does_not_contain_variable_reference():
    assert not contains_variable_reference("visible")


def test_exact_variable_reference_returns_name():
    assert exact_variable_reference("${TOKEN}") == "TOKEN"


@pytest.mark.parametrize(
    "value",
    ["Bearer ${TOKEN}", "${TOKEN:-fallback}", "${TOKEN}-suffix", "visible"],
)
def test_non_exact_variable_reference_returns_none(value):
    assert exact_variable_reference(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("${TOKEN:-fallback}", True),
        ("Bearer ${TOKEN:-fallback}", True),
        ("${TOKEN}", False),
        ("visible", False),
    ],
)
def test_contains_variable_default(value, expected):
    assert contains_variable_default(value) is expected
