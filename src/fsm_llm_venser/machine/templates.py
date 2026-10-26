"""Prompt templates with {name} placeholders.

str.format is not used because it also allows {x.__class__}, which is dangerous when the
template comes from a file. Here only {identifier} is replaced, {{ and }} give literal braces,
and any other brace is an error (this also catches typos like "{ goal }").
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")


class TemplateError(ValueError):
    """A template has a stray brace, or uses a placeholder that has no value."""


def placeholders(template: str) -> set[str]:
    """Find the placeholders of a template and check its braces.

    Args:
        template (str): Text with {name} placeholders.

    Returns:
        set[str]: The placeholder names.

    Raises:
        TemplateError: If a brace is not part of a valid placeholder or escape.
    """
    leftover = _TOKEN.sub("", template)
    if "{" in leftover or "}" in leftover:
        raise TemplateError(
            "stray brace in template: use {name} for a placeholder, {{ and }} for literal braces"
        )
    return {m.group(1) for m in _TOKEN.finditer(template) if m.group(1)}


def render(template: str, values: Mapping[str, object]) -> str:
    """Replace the placeholders of a template.

    Args:
        template (str): Text with {name} placeholders.
        values (Mapping[str, object]): Value of each placeholder.

    Returns:
        str: The text with the values filled in.

    Raises:
        TemplateError: If the template is malformed or a placeholder has no value.
    """
    placeholders(template)  # checks the braces

    def replace(match: re.Match[str]) -> str:
        """Replace one token of the template."""
        token = match.group(0)
        if token in ("{{", "}}"):
            return token[0]
        if match.group(1) not in values:
            raise TemplateError(f"no value for placeholder {{{match.group(1)}}}")
        return str(values[match.group(1)])

    return _TOKEN.sub(replace, template)
