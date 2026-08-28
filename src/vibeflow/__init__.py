"""Vibeflow public package surface with lazy imports."""

from importlib import import_module
from typing import Any

__all__ = [
    "Ambiguity",
    "Contract",
    "Orchestrator",
    "Risk",
    "Router",
    "TaskResult",
    "TaskStatus",
    "Tier",
]

__version__ = "0.5.0"

_EXPORTS = {
    "Ambiguity": (".contracts", "Ambiguity"),
    "Contract": (".contracts", "Contract"),
    "Risk": (".contracts", "Risk"),
    "Orchestrator": (".orchestrator", "Orchestrator"),
    "TaskResult": (".orchestrator", "TaskResult"),
    "TaskStatus": (".orchestrator", "TaskStatus"),
    "Router": (".router", "Router"),
    "Tier": (".router", "Tier"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
