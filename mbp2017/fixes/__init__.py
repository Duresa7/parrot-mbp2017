"""Discover fix modules automatically; new fixes need no registry edits."""

import importlib
import inspect
import pkgutil

from .base import Fix

ORDER = ["t1-backup", "wifi", "sleep", "audio", "input", "t1bridge", "desktop", "fingerprint-login"]


def all_fixes() -> list[Fix]:
    found = {}
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name == "base" or module_info.name.startswith("_"):
            continue
        module_name = f"{__name__}.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            raise
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (issubclass(cls, Fix) and cls is not Fix and cls.__module__ == module.__name__
                    and cls.__dict__.get("id")):
                if cls.id in found:
                    raise ValueError(f"Duplicate fix id: {cls.id}")
                found[cls.id] = cls()
    return sorted(found.values(), key=lambda fix: (ORDER.index(fix.id) if fix.id in ORDER else len(ORDER), fix.id))


def get_fix(id: str) -> Fix | None:
    return next((fix for fix in all_fixes() if fix.id == id), None)
