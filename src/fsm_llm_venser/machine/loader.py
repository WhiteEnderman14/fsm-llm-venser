"""YAML loader for machine and scenario files.

PyYAML follows YAML 1.1, which has two traps for a state machine file:
  - on/off/yes/no are read as booleans, so a state called NO would become False
  - a duplicated key silently overwrites the first one
This loader accepts only true/false as booleans and rejects duplicated keys.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class StrictLoader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans and duplicate key detection."""


# remove the YAML 1.1 boolean rule, then add one that only knows true/false
StrictLoader.yaml_implicit_resolvers = {
    char: [(tag, rule) for tag, rule in rules if tag != "tag:yaml.org,2002:bool"]
    for char, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _construct_mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
    """Build a mapping, failing on a key that appears twice.

    Args:
        loader (StrictLoader): The loader that is reading the file.
        node (yaml.MappingNode): The YAML mapping node.
        deep (bool): Passed to the default constructor.

    Returns:
        Any: The dictionary, built by the default constructor.

    Raises:
        yaml.constructor.ConstructorError: If a key is repeated (it reports line and column).
    """
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_file(path: str | Path) -> Any:
    """Read a YAML file with the strict loader.

    Args:
        path (str | Path): Path of the YAML file.

    Returns:
        Any: The parsed content (normally a dictionary).
    """
    return yaml.load(Path(path).read_text(encoding="utf-8"), Loader=StrictLoader)  # noqa: S506
