"""Out-of-tree speaker driver for the Cirrus Logic CS8409 codec."""

from __future__ import annotations

from .base import Context, Fix, FixError, Health, State, Status
from ..hardware import Hardware

REPO_URL = "https://github.com/davidjo/snd_hda_macbookpro.git"
COMMIT = "89b22ff90b86468b186706861dd18663562defa7"
CLONE_DIR = "/usr/local/src/snd_hda_macbookpro"
DKMS_NAME = "snd_hda_macbookpro"
DKMS_VERSION = "0.1"
LOG_PATH = "/var/log/parrot-mbp2017-audio.log"
PACKAGES = ("build-essential", "dkms", "git", "patch", "wget")


def _dkms_entries(ctx: Context) -> list[dict[str, str]]:
    result = ctx.system.run(["dkms", "status", DKMS_NAME], check=False)
    entries = []
    for line in result.stdout.splitlines():
        head, sep, status = line.partition(":")
        if not sep:
            continue
        fields = [field.strip() for field in head.split(",")]
        if len(fields) < 2 or fields[0].split("/")[0] != DKMS_NAME:
            continue
        entries.append({"kernel": fields[1], "status": status.strip()})
    return entries


class AudioFix(Fix):
    id = "audio"
    title = "Internal speakers"
    summary = "Build the out-of-tree driver that drives the speaker amplifiers."
    why = ("The mainline snd-hda-codec-cs8409 driver does not drive this Mac's speaker "
           "amplifiers, so the built-in speakers stay silent without this driver.")
    after = "reboot"

    def gate(self, hw: Hardware) -> str | None:
        if not hw.audio_cs8409:
            return "No Cirrus Logic CS8409 sound codec detected."
        return None

    def status(self, ctx: Context) -> Status:
        reason = self.gate(ctx.hw)
        if reason:
            return Status(State.NOT_NEEDED, reason)
        entries = _dkms_entries(ctx)
        if any(entry["kernel"] == ctx.hw.kernel and "installed" in entry["status"] for entry in entries):
            return Status(State.DONE)
        installed_elsewhere = [entry["kernel"] for entry in entries if "installed" in entry["status"]]
        if installed_elsewhere:
            return Status(State.PARTIAL,
                          "Installed for " + ", ".join(installed_elsewhere)
                          + f", not the running kernel ({ctx.hw.kernel}).")
        if ctx.system.is_dir(CLONE_DIR):
            return Status(State.PARTIAL, "The driver source is cloned but not built with DKMS.")
        return Status(State.TODO)

    def _missing_packages(self, ctx: Context) -> list[str]:
        names = [*PACKAGES, f"linux-headers-{ctx.hw.kernel}"]
        installed = ctx.system.package_versions(names)
        return [name for name in names if name not in installed]

    def install(self, ctx: Context) -> list[str]:
        if self.gate(ctx.hw):
            return []
        if self.status(ctx).state == State.DONE:
            return []
        missing = self._missing_packages(ctx)
        if missing:
            ctx.ui.info("Installing packages needed to build the driver: " + ", ".join(missing) + ".")
            ctx.system.run(["apt-get", "install", "-y", *missing], mutating=True)
        if ctx.system.is_dir(CLONE_DIR):
            ctx.ui.info("The driver source is already present. Fetching the pinned commit.")
            ctx.system.run(["git", "-c", f"safe.directory={CLONE_DIR}", "-C", CLONE_DIR, "fetch"],
                           mutating=True)
        else:
            ctx.ui.info(f"Cloning the speaker driver from {REPO_URL}.")
            ctx.system.run(["git", "clone", REPO_URL, CLONE_DIR], mutating=True)
        ctx.system.run(["git", "-c", f"safe.directory={CLONE_DIR}", "-C", CLONE_DIR,
                        "checkout", "--detach", COMMIT], mutating=True)
        if not ctx.system.dry_run:
            verify = ctx.system.run(["git", "-c", f"safe.directory={CLONE_DIR}", "-C", CLONE_DIR,
                                     "rev-parse", "HEAD"], check=False)
            if verify.stdout.strip() != COMMIT:
                raise FixError(f"{CLONE_DIR} is not at the pinned commit {COMMIT}. "
                              "Check the clone for local changes and try again.")
        ctx.ui.info("Building and installing the driver through DKMS. This can take a while.")
        code = ctx.system.run_streamed(["./install.cirrus.driver.sh", "-i"], cwd=CLONE_DIR,
                                       log_path=LOG_PATH, on_line=ctx.ui.detail)
        if ctx.system.dry_run:
            return []
        if code != 0 or self.status(ctx).state != State.DONE:
            raise FixError("The speaker driver did not install. "
                          f"Check {LOG_PATH} for details.")
        return ["Reboot to hear the internal speakers.",
                "Kernel updates need an internet connection so DKMS can rebuild this driver for the new kernel."]

    def remove(self, ctx: Context) -> list[str]:
        if not ctx.system.is_dir(CLONE_DIR):
            return []
        ctx.ui.info("Removing the speaker driver and restoring the stock module.")
        ctx.system.run_streamed(["./install.cirrus.driver.sh", "-r"], cwd=CLONE_DIR, log_path=LOG_PATH)
        ctx.system.run(["rm", "-rf", CLONE_DIR], mutating=True)
        return []

    def health(self, ctx: Context) -> list[Health]:
        if self.gate(ctx.hw):
            return []
        entries = {entry["kernel"]: entry["status"] for entry in _dkms_entries(ctx)}
        checks = []
        for kernel, has_headers in ctx.hw.headers.items():
            if has_headers and "installed" not in entries.get(kernel, ""):
                checks.append(Health("warn", f"The speaker driver is not installed for kernel {kernel}. "
                                     f"With internet connected, run: sudo dkms autoinstall -k {kernel}"))
        return checks
