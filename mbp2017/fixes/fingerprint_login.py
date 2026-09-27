"""Opt-in fingerprint authentication through the distribution's PAM profile."""

import re

from .base import Context, Fix, FixError, State, Status
from ..hardware import Hardware


class FingerprintLoginFix(Fix):
    id = "fingerprint-login"
    title = "Touch ID for sudo and other password prompts"
    summary = "Allow an enrolled fingerprint to authenticate while keeping password login."
    why = "Enable the distribution's fingerprint authentication profile so Touch ID can be used for sudo and other password prompts."
    default = False
    requires = ("t1bridge",)

    def gate(self, hw: Hardware) -> str | None:
        return None if hw.t1 else "No T1 detected."

    def _enabled(self, ctx: Context) -> bool:
        return "pam_fprintd.so" in (ctx.system.read_text("/etc/pam.d/common-auth", "") or "")

    def status(self, ctx: Context) -> Status:
        reason = self.gate(ctx.hw)
        if reason:
            return Status(State.NOT_NEEDED, reason)
        if ("t1bridge" not in ctx.options.selected and
                "libpam-fprintd" not in ctx.system.package_versions(["libpam-fprintd"])):
            return Status(State.BLOCKED, "Install the t1bridge fix to provide libpam-fprintd first.")
        return Status(State.DONE if self._enabled(ctx) else State.TODO)

    def install(self, ctx: Context) -> list[str]:
        status = self.status(ctx)
        if status.state in (State.NOT_NEEDED, State.DONE):
            return []
        if status.state == State.BLOCKED:
            raise FixError(status.detail)
        ctx.ui.info("Enable fingerprint authentication. Your password still works. Keep a root terminal "
                    "open and test sudo in a new terminal before closing it.")
        if not ctx.options.assume_yes and not ctx.ui.confirm("Enable Touch ID authentication?", default=False):
            raise FixError("Fingerprint login cancelled. Run this fix again when you are ready.")
        user = ctx.system.invoking_user()
        if user:
            result = ctx.system.run(["fprintd-list", user.name], timeout=10, check=False)
            if not result.ok or not re.search(r"\b(?:left|right)-(?:thumb|(?:index|middle|ring|little)-finger)\b", result.stdout):
                ctx.ui.warn("No enrolled finger was found. Without an enrolled finger, sudo falls back to your password.")
        else:
            ctx.ui.warn("Could not identify your normal user to check enrolled fingers. Without an enrolled finger, "
                        "sudo falls back to your password.")
        if not ctx.system.exists("/usr/share/pam-configs/fprintd"):
            raise FixError("The fingerprint authentication profile is missing. Install the t1bridge fix, then retry.")
        ctx.system.run(["pam-auth-update", "--enable", "fprintd"], mutating=True)
        return []

    def remove(self, ctx: Context) -> list[str]:
        if self._enabled(ctx):
            ctx.ui.info("Disable fingerprint authentication so sudo and other password prompts use your password only.")
            ctx.system.run(["pam-auth-update", "--disable", "fprintd"], mutating=True)
        return []
