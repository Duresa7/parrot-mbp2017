"""Suspend settings for AMD graphics and the T1."""

from .base import Context, FileFix, ManagedFile, State, Status
from ..hardware import Hardware


class SleepFix(FileFix):
    id = "sleep"
    title = "Working suspend on the 15-inch models"
    summary = "Use lighter sleep for AMD graphics and stop the T1 waking the Mac immediately."
    why = ("AMD graphics do not resume from deep sleep on these Macs. Lighter s2idle sleep "
           "works when PCI devices avoid deep power saving and bridges stay awake. "
           "The T1 also needs its wakeup disabled.")
    after = "reboot"
    files = (
        ManagedFile("/etc/systemd/sleep.conf.d/parrot-mbp2017.conf", "parrot-mbp2017-sleep.conf",
                    when=lambda hw: hw.amd_gpu),
        ManagedFile("/etc/udev/rules.d/90-parrot-mbp2017-pci-pm.rules", "90-parrot-mbp2017-pci-pm.rules",
                    when=lambda hw: hw.amd_gpu),
        ManagedFile("/etc/udev/rules.d/99-zz-parrot-mbp2017-t1-nowake.rules",
                    "99-zz-parrot-mbp2017-t1-nowake.rules", when=lambda hw: hw.t1),
    )

    def gate(self, hw: Hardware) -> str | None:
        return None if hw.amd_gpu or hw.t1 else "No AMD graphics or T1 detected."

    def _kernel_s2idle(self, ctx: Context) -> bool:
        return "mem_sleep_default=s2idle" in (ctx.system.read_text("/proc/cmdline", "") or "").split()

    def status(self, ctx: Context) -> Status:
        reason = self.gate(ctx.hw)
        if reason:
            return Status(State.NOT_NEEDED, reason)
        kernel = ctx.hw.amd_gpu and self._kernel_s2idle(ctx)
        current = [bool(kernel and item == self.files[0]) or
                   ctx.system.file_is_current(item.path, ctx.system.data_text(item.data_name))
                   for item in self.files if item.when(ctx.hw)]
        state = State.DONE if all(current) else State.PARTIAL if any(current) else State.TODO
        detail = "s2idle is set on the kernel command line." if kernel else ""
        return Status(state, detail)

    def install(self, ctx: Context) -> list[str]:
        if self.gate(ctx.hw):
            return []
        ctx.ui.info("Set sleep and device power settings so the screen can return after suspend.")
        changed = False
        for item in self.files:
            if item.when(ctx.hw) and not (item == self.files[0] and self._kernel_s2idle(ctx)):
                changed |= ctx.system.install_file(self.id, item.path,
                                                   ctx.system.data_text(item.data_name), item.mode)
        if changed:
            self.after_install(ctx)
        notes = ["Thunderbolt logs errors on resume."]
        if ctx.hw.t1:
            notes.insert(0, "The t1-wake fix brings the Touch Bar and Touch ID back after waking.")
        return notes

    def after_install(self, ctx: Context) -> list[str]:
        ctx.ui.info("Reload device rules; the sleep changes take full effect after a reboot.")
        ctx.system.run(["udevadm", "control", "--reload"], mutating=True)
        return []

    def after_remove(self, ctx: Context) -> list[str]:
        return self.after_install(ctx)
