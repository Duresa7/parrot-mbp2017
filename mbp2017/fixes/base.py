"""Public interfaces for independently developed fixes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Literal

from ..hardware import Hardware
from ..system import System
from ..ui import UI


class State(str, Enum):
    DONE = "done"
    PARTIAL = "partly done"
    TODO = "not set up"
    NOT_NEEDED = "not needed"
    BLOCKED = "blocked"


@dataclass
class Status:
    state: State
    detail: str = ""


class FixError(Exception):
    """An actionable failure that the CLI can display without a traceback."""


@dataclass
class Health:
    level: Literal["ok", "warn", "error"]
    message: str


@dataclass
class Options:
    assume_yes: bool = False
    dry_run: bool = False
    force: bool = False
    debs_dir: str | None = None
    backup_dir: str | None = None
    selected: set[str] = field(default_factory=set)


@dataclass
class Context:
    system: System
    hw: Hardware
    ui: UI
    options: Options


class Fix:
    id = ""
    title = ""
    summary = ""
    why = ""
    default = True
    after: Literal["", "reboot", "relogin"] = ""
    requires: tuple[str, ...] = ()

    def gate(self, hw: Hardware) -> str | None:
        return None

    def status(self, ctx: Context) -> Status:
        raise NotImplementedError

    def install(self, ctx: Context) -> list[str]:
        raise NotImplementedError

    def remove(self, ctx: Context) -> list[str]:
        raise NotImplementedError

    def health(self, ctx: Context) -> list[Health]:
        return []


def _always(hw: Hardware) -> bool:
    return True


@dataclass(frozen=True)
class ManagedFile:
    path: str
    data_name: str
    mode: int = 0o644
    when: Callable[[Hardware], bool] = _always


@dataclass(frozen=True)
class ManagedBlock:
    path: str
    data_name: str
    when: Callable[[Hardware], bool] = _always


class FileFix(Fix):
    files: tuple[ManagedFile, ...] = ()
    blocks: tuple[ManagedBlock, ...] = ()

    def status(self, ctx: Context) -> Status:
        reason = self.gate(ctx.hw)
        if reason:
            return Status(State.NOT_NEEDED, reason)
        current = [ctx.system.file_is_current(item.path, ctx.system.data_text(item.data_name))
                   for item in self.files if item.when(ctx.hw)]
        current += [ctx.system.block_is_current(self.id, item.path, ctx.system.data_text(item.data_name))
                    for item in self.blocks if item.when(ctx.hw)]
        if not current:
            return Status(State.NOT_NEEDED, "No matching hardware detected.")
        if all(current):
            return Status(State.DONE)
        if not any(current):
            return Status(State.TODO)
        return Status(State.PARTIAL, f"{sum(current)} of {len(current)} settings are current.")

    def install(self, ctx: Context) -> list[str]:
        if self.gate(ctx.hw):
            return []
        changed = False
        for item in self.files:
            if item.when(ctx.hw):
                changed |= ctx.system.install_file(self.id, item.path,
                                                   ctx.system.data_text(item.data_name), item.mode)
        for item in self.blocks:
            if item.when(ctx.hw):
                changed |= ctx.system.install_block(self.id, item.path,
                                                    ctx.system.data_text(item.data_name))
        return (self.after_install(ctx) or []) if changed else []

    def remove(self, ctx: Context) -> list[str]:
        # Undo recorded changes even when the hardware is currently absent.
        changed = False
        for item in self.files:
            changed |= ctx.system.remove_file(self.id, item.path)
        for item in self.blocks:
            changed |= ctx.system.remove_block(self.id, item.path)
        return (self.after_remove(ctx) or []) if changed else []

    def after_install(self, ctx: Context) -> list[str]:
        return []

    def after_remove(self, ctx: Context) -> list[str]:
        return []
