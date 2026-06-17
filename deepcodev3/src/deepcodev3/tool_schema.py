"""Single source of truth for tool registry, descriptions, and permission categories."""
from __future__ import annotations
from .tools import (
    TOOL_REGISTRY,
    TOOL_DESCRIPTIONS,
    READ_TOOLS,
    WRITE_TOOLS,
    EXEC_TOOLS,
    NETWORK_TOOLS,
)

__all__ = [
    "TOOL_REGISTRY",
    "TOOL_DESCRIPTIONS",
    "category_for_tool",
]

_CATEGORY_MAP = {}
for _name in READ_TOOLS:
    _CATEGORY_MAP[_name] = "read"
for _name in WRITE_TOOLS:
    _CATEGORY_MAP[_name] = "write"
for _name in EXEC_TOOLS:
    _CATEGORY_MAP[_name] = "exec"
for _name in NETWORK_TOOLS:
    _CATEGORY_MAP[_name] = "network"


def category_for_tool(tool_name: str) -> str:
    """Return permission category for a tool: read | write | exec | network | unknown."""
    return _CATEGORY_MAP.get(tool_name, "unknown")
