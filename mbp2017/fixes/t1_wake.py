"""Bring the Touch Bar and Touch ID back when the Mac wakes."""

from .base import Context, FileFix, FixError, ManagedFile, State, Status
from .t1bridge import installed_or_selected
from ..hardware import Hardware


class T1WakeFix(FileFix):
    id = "t1-wake"
    title = "Touch Bar and Touch ID after sleep"
    summary = "Turn the Touch Bar back on and get Touch ID ready as soon as the Mac wakes."
    why = ("t1bridge switches the Touch Bar panel off before sleep and does not switch it back on. "
           "Touch ID's key service also loses its link to the T1 during sleep, and systemd waits "
           "longer after every sleep to restart it, so the lock screen's fingerprint check fails. "
           "A sleep hook fixes both right after waking, before the desktop resumes.")
    requires = ("t1bridge",)
    files = (
        ManagedFile("/usr/lib/systemd/system-sleep/parrot-mbp2017-t1-wake", "parrot-mbp2017-t1-wake",
                    mode=0o755),
    )

    def gate(self, hw: Hardware) -> str | None:
        return None if hw.t1 else "No T1 detected."

    def status(self, ctx: Context) -> Status:
        reason = self.gate(ctx.hw)
        if reason:
            return Status(State.NOT_NEEDED, reason)
        if not installed_or_selected(ctx):
            return Status(State.BLOCKED, "Install the t1bridge fix first.")
        return super().status(ctx)

    def install(self, ctx: Context) -> list[str]:
        status = self.status(ctx)
        if status.state == State.BLOCKED:
            raise FixError(status.detail)
        if status.state == State.NOT_NEEDED:
            return []
        ctx.ui.info("Install a sleep hook that turns the Touch Bar back on and restarts Touch ID's "
                    "key service after waking.")
        return super().install(ctx)
