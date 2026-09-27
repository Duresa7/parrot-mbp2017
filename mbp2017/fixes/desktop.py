"""Touch Bar desktop integration for Plasma and PipeWire."""

from .base import Context, FileFix, FixError, ManagedFile, State, Status
from ..hardware import Hardware


class DesktopFix(FileFix):
    id = "desktop"
    title = "Touch Bar volume and media keys, brightness popups"
    summary = "Connect Touch Bar controls to desktop audio, media and brightness popups."
    why = ("t1bridge needs a desktop provider to show volume and media buttons. "
           "The provider also keeps Plasma's brightness slider and popups in sync.")
    requires = ("t1bridge",)
    after = "relogin"
    files = (
        ManagedFile("/usr/local/lib/parrot-mbp2017/desktop-provider", "desktop-provider", mode=0o755),
        ManagedFile("/etc/systemd/user/t1-touchbar.service.d/parrot-mbp2017-desktop-provider.conf",
                    "parrot-mbp2017-desktop-provider.conf"),
    )

    def gate(self, hw: Hardware) -> str | None:
        return None if hw.t1 else "No T1 detected."

    def status(self, ctx: Context) -> Status:
        reason = self.gate(ctx.hw)
        if reason:
            return Status(State.NOT_NEEDED, reason)
        if ("t1bridge" not in ctx.options.selected and
                "t1bridge" not in ctx.system.package_versions(["t1bridge"])):
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
