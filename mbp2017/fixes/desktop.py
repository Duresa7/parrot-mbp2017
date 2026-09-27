"""Touch Bar desktop integration for Plasma and PipeWire."""

from .base import Context, FileFix, FixError, ManagedFile, State, Status
from .t1bridge import installed_or_selected
from ..hardware import Hardware


class DesktopFix(FileFix):
    id = "desktop"
    title = "Touch Bar controls and lock screen Touch ID in Plasma"
    summary = "Connect Touch Bar controls to Plasma and let Touch ID unlock the lock screen."
    why = ("t1bridge needs a desktop provider to show volume and media buttons. "
           "The provider also keeps Plasma's brightness slider and popups in sync, and the Touch Bar "
           "restarts with every login. "
           "Plasma's packaged lock screen fingerprint service hangs after a matched finger and "
           "stops listening after one timeout; a corrected copy fixes both.")
    requires = ("t1bridge",)
    after = "relogin"
    files = (
        ManagedFile("/usr/local/lib/parrot-mbp2017/desktop-provider", "desktop-provider", mode=0o755),
        ManagedFile("/etc/systemd/user/t1-touchbar.service.d/parrot-mbp2017-desktop-provider.conf",
                    "parrot-mbp2017-desktop-provider.conf"),
        ManagedFile("/etc/systemd/user/graphical-session.target.d/parrot-mbp2017-touchbar.conf",
                    "parrot-mbp2017-touchbar-session.conf"),
        ManagedFile("/etc/pam.d/kde-fingerprint", "parrot-mbp2017-kde-fingerprint", when=lambda hw: hw.plasma),
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
        ctx.ui.info("Install the desktop provider so Touch Bar controls work with your desktop.")
        return super().install(ctx)

    def after_install(self, ctx: Context) -> list[str]:
        user = ctx.system.invoking_user()
        if user:
            ctx.ui.info("Reload the user service settings and refresh the Touch Bar if it is running.")
            prefix = ["systemctl", "--user", "-M", user.name + "@"]
            ctx.system.run(prefix + ["daemon-reload"], check=False, mutating=True)
            ctx.system.run(prefix + ["try-restart", "t1-touchbar.service"], check=False, mutating=True)
        return ["Log out and back in if the Touch Bar does not show volume keys."]

    def after_remove(self, ctx: Context) -> list[str]:
        return self.after_install(ctx)
