"""Stable Broadcom Wi-Fi using the kernel's brcmfmac driver."""

from .base import Context, FileFix, FixError, ManagedFile, State, Status
from ..hardware import Hardware


class WifiFix(FileFix):
    id = "wifi"
    title = "Stable Wi-Fi on the Broadcom BCM43602"
    summary = "Stop driver conflicts and dropouts, and reload the driver around sleep."
    why = ("Other Broadcom drivers can fight brcmfmac for this chip. Power saving can "
           "drop connections, and unlimited retries let Wi-Fi recover after a short outage. "
           "The chip's firmware can also fail to sleep or wake in place, which stops the Mac "
           "sleeping or leaves Wi-Fi dead, so the driver is unloaded before sleep and reloaded after.")
    files = (
        ManagedFile("/etc/modprobe.d/parrot-mbp2017-broadcom.conf", "parrot-mbp2017-broadcom.conf"),
        ManagedFile("/etc/NetworkManager/conf.d/parrot-mbp2017-wifi.conf", "parrot-mbp2017-wifi.conf"),
        ManagedFile("/usr/lib/systemd/system-sleep/parrot-mbp2017-wifi", "parrot-mbp2017-wifi-sleep", mode=0o755),
    )
    firmware = "/lib/firmware/brcm/brcmfmac43602-pcie.bin"

    def gate(self, hw: Hardware) -> str | None:
        return None if hw.wifi_bcm43602 else "No Broadcom BCM43602 Wi-Fi detected."

    def status(self, ctx: Context) -> Status:
        status = super().status(ctx)
        if status.state == State.NOT_NEEDED:
            return status
        missing = not ctx.system.exists(self.firmware)
        conflict = "broadcom-sta-dkms" in ctx.system.package_versions(["broadcom-sta-dkms"])
        if missing or conflict:
            detail = "Install the Wi-Fi firmware. " if missing else ""
            detail += "Remove the conflicting broadcom-sta-dkms driver." if conflict else ""
            return Status(State.PARTIAL if status.state == State.DONE else status.state, detail.strip())
        return status

    def install(self, ctx: Context) -> list[str]:
        if self.gate(ctx.hw):
            return []
        system = ctx.system
        packages_changed = False
        if "broadcom-sta-dkms" in system.package_versions(["broadcom-sta-dkms"]):
            ctx.ui.info("Remove broadcom-sta-dkms so brcmfmac can use the Wi-Fi chip without a driver conflict.")
            if not ctx.options.assume_yes and not ctx.ui.confirm("Remove broadcom-sta-dkms?", default=False):
                raise FixError("Wi-Fi setup cancelled. Allow removal of broadcom-sta-dkms to continue.")
            system.run(["apt-get", "purge", "-y", "broadcom-sta-dkms"],
                       env={"DEBIAN_FRONTEND": "noninteractive"}, mutating=True)
            packages_changed = True
        if not system.exists(self.firmware):
            ctx.ui.info("Install the firmware that brcmfmac needs to operate the Wi-Fi chip.")
            system.run(["apt-get", "install", "-y", "firmware-brcm80211"],
                       env={"DEBIAN_FRONTEND": "noninteractive"}, mutating=True)
            packages_changed = True
        # FileFix runs its hook only for content changes; package-only repairs need it too.
        files_current = all(system.file_is_current(item.path, system.data_text(item.data_name))
                            for item in self.files)
        ctx.ui.info("Set up brcmfmac, connection retries and the sleep script to keep Wi-Fi working.")
        notes = super().install(ctx)
        if packages_changed and files_current:
            notes = self.after_install(ctx)
        if system.exists("/sys/module/wl"):
            notes.append("Reboot to unload wl and let brcmfmac take over Wi-Fi.")
        return notes

    def after_install(self, ctx: Context) -> list[str]:
        ctx.ui.info("Update the startup driver image and reload NetworkManager so it uses the Wi-Fi settings.")
        ctx.system.run(["update-initramfs", "-u"], mutating=True)
        ctx.system.run(["nmcli", "general", "reload", "conf"], check=False, mutating=True)
        return []

    def after_remove(self, ctx: Context) -> list[str]:
        return self.after_install(ctx)
