"""Adapter protocol: the only layout-specific code between a run directory and the evaluator."""
from __future__ import annotations

import importlib
import importlib.util
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class RunUnitRef:
    """Handle to one run unit inside a run directory.

    `id` must be stable across invocations and unique within the run root; it becomes
    `run.run_unit_id` in the trace. `location` is whatever the adapter needs to find the unit
    again (usually a directory path).
    """

    id: str
    location: str
    meta: dict = field(default_factory=dict, compare=False, hash=False)


class Adapter(ABC):
    """Convert one run-directory layout into gce.trace.v1 documents.

    Adapters must be read-only (never write inside the run root), deterministic (the same input
    gives the same trace) and must not guess: information that cannot be mapped goes into
    `extras`, and missing answers are reported with status "missing".
    """

    name: str = "adapter"

    def __init__(self, **options: Any):
        self.options = options

    @abstractmethod
    def discover(self, run_root: Path) -> Iterable[RunUnitRef]:
        """Yield every run unit under `run_root`, in a stable order."""

    @abstractmethod
    def to_trace(self, ref: RunUnitRef) -> dict:
        """Build the trace.v1 document for one run unit."""


REGISTRY = {
    "canonical_dir": "gce.adapters.canonical_dir:CanonicalDirAdapter",
}


def load_adapter(spec: str, **options: Any) -> Adapter:
    """Instantiate an adapter from a registry name, 'module:Class', or 'path/to/file.py[:Class]'."""
    target = REGISTRY.get(spec, spec)
    module_part, _, class_name = target.partition(":")
    if module_part.endswith(".py") or Path(module_part).is_file():
        path = Path(module_part).resolve()
        mod_spec = importlib.util.spec_from_file_location(f"gce_adapter_{path.stem}", path)
        if mod_spec is None or mod_spec.loader is None:
            raise ImportError(f"cannot load adapter file {path}")
        module = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_part)
    if class_name:
        cls = getattr(module, class_name)
    else:
        found = [obj for _, obj in inspect.getmembers(module, inspect.isclass) if issubclass(obj, Adapter) and obj is not Adapter and obj.__module__ == module.__name__]
        if len(found) != 1:
            raise ImportError(f"{spec}: expected exactly one Adapter subclass, found {[c.__name__ for c in found]}; use ':ClassName'")
        cls = found[0]
    return cls(**options)
