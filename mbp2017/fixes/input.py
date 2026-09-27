"""Palm rejection and Touch Bar input rules."""

from .base import Context, FileFix, ManagedBlock, ManagedFile
from ..hardware import Hardware


class InputFix(FileFix):
    id = "input"
    title = "Palm rejection and Touch Bar touches"
    summary = "Stop palms moving the cursor and keep Touch Bar touches off the trackpad."
    why = ("The built-in keyboard reports vendor 0x0000, so the usual Apple keyboard "
           "setting does not apply. Marking it as internal lets the trackpad ignore palms "
           "while typing. The Touch Bar also needs a rule so it is not treated as a second trackpad.")
    after = "relogin"
    files = (ManagedFile("/etc/udev/rules.d/90-parrot-mbp2017-touchbar-not-pointer.rules",
                         "90-parrot-mbp2017-touchbar-not-pointer.rules", when=lambda hw: hw.t1),)
    blocks = (ManagedBlock("/etc/libinput/local-overrides.quirks", "libinput-applespi.quirks",
                           when=lambda hw: hw.spi_keyboard),)

    def gate(self, hw: Hardware) -> str | None:
        if not hw.spi_keyboard and not hw.t1:
            return "No Apple SPI keyboard or T1 detected."
        return None

    def after_install(self, ctx: Context) -> list[str]:
        ctx.system.run(["udevadm", "control", "--reload"], mutating=True)
        ctx.system.run(["udevadm", "trigger", "--action=change", "--subsystem-match=input"], mutating=True)
        return []

    def after_remove(self, ctx: Context) -> list[str]:
        ctx.system.run(["udevadm", "control", "--reload"], mutating=True)
        return []
