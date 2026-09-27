"""Install the pinned Debian packages for the T1 and undo their integration."""

from hashlib import sha256
from pathlib import PurePosixPath
import re

from .base import Context, Fix, FixError, Health, State, Status
from ..hardware import Hardware
from .. import dkms


VERSION = "0.1.12-1~parrot1"
CACHE = "/var/cache/parrot-mbp2017/t1bridge-0.1.12"
BUILD_LOG = "/var/log/parrot-mbp2017-build.log"
HOLDS = ("libfprint-2-2", "fprintd", "libpam-fprintd")
PACKAGES = (*HOLDS, "t1bridge", "t1bridge-dkms")
STOCK_RULE = "/usr/lib/udev/rules.d/39-usbmuxd.rules"
OVERRIDE = "/etc/udev/rules.d/39-usbmuxd.rules"
RULE_MATCH = "|5ac/8600/*"
RULE_HEADER = ("# Installed by parrot-mbp2017 (t1bridge).\n"
               "# The stock rule sets bConfigurationValue=0 on the T1 and deconfigures it.\n")
SHA_NOTE = "usbmuxd_source_sha256"
LEGACY_FILES = ("/etc/modprobe.d/apple-touchbar.conf",
                "/etc/modules-load.d/apple-touchbar.conf",
                "/etc/udev/rules.d/99-ibridge.rules")


class T1BridgeFix(Fix):
    id = "t1bridge"
    title = "Touch Bar, Touch ID, camera and ambient light sensor"
    summary = "Install the drivers and services that let Linux use the T1."
    why = ("The T1 needs matching drivers and fingerprint packages so the Touch Bar, "
           "Touch ID, camera and ambient light sensor can work together.")
    after = "reboot"

    def gate(self, hw: Hardware) -> str | None:
        return None if hw.t1 else "No T1 detected."

    def _hardware_warnings(self, ctx: Context) -> list[str]:
        warnings = []
        if ctx.hw.t1_state == "recovery":
            warnings.append("The T1 is in recovery mode. The Touch Bar cannot work until "
                            "its firmware is restored with restore-t1.")
        if ctx.hw.t1 and ctx.hw.t1_data is False:
            warnings.append("T1 data is missing. Touch ID will not work until it is restored.")
        return warnings

    def _missing_holds(self, ctx: Context) -> list[str]:
        result = ctx.system.run(["apt-mark", "showhold"], check=False)
        held = set(result.stdout.split()) if result.ok else set()
        return [name for name in HOLDS if name not in held]

    def _group_warning(self, ctx: Context) -> str | None:
        user = ctx.system.invoking_user()
        if user is None or user.uid == 0:
            return None
        for line in (ctx.system.read_text("/etc/group", "") or "").splitlines():
            fields = line.split(":")
            if (len(fields) == 4 and fields[0] == "t1bridge"
                    and (user.name in fields[3].split(",") or fields[2] == str(user.gid))):
                return None
        return f"{user.name} is not in the t1bridge group; re-run install t1bridge."

    def _rule(self, ctx: Context) -> str:
        return ctx.system.read_text(STOCK_RULE, "") or ""

    def _rule_digest(self, ctx: Context) -> str | None:
        if not ctx.system.exists(STOCK_RULE):
            return None
        return sha256(ctx.system.read_bytes(STOCK_RULE)).hexdigest()

    def _override_warning(self, ctx: Context) -> str | None:
        source = self._rule(ctx)
        noted = ctx.system.notes(self.id).get(SHA_NOTE)
        if ((noted and noted != self._rule_digest(ctx))
                or (RULE_MATCH in source and (
                    not ctx.system.file_is_current(
                        OVERRIDE, RULE_HEADER + source.replace(RULE_MATCH, ""))
                    or noted != self._rule_digest(ctx)))):
            return "The usbmuxd override is missing or out of date; re-run install t1bridge."
        return None

    def status(self, ctx: Context) -> Status:
        reason = self.gate(ctx.hw)
        if reason:
            return Status(State.NOT_NEEDED, reason)
        versions = ctx.system.package_versions(["t1bridge", "t1bridge-dkms"])
        if "t1bridge" not in versions:
            return Status(State.TODO, "t1bridge is not installed.")
        missing = [f"{name} {VERSION} is needed" for name in ("t1bridge", "t1bridge-dkms")
                   if versions.get(name) != VERSION]
        holds = self._missing_holds(ctx)
        if holds:
            missing.append("Missing package holds: " + ", ".join(holds))
        missing.extend(message for message in (self._group_warning(ctx), self._override_warning(ctx))
                       if message)
        return Status(State.PARTIAL, "; ".join(missing)) if missing else Status(State.DONE)

    def _package_set(self, ctx: Context, directory: str) -> tuple[list[str], list[str]]:
        paths, problems = [], []
        for package in PACKAGES:
            matches = [path for path in ctx.system.glob(f"{directory}/{package}_*.deb")
                       if not ctx.system.is_dir(path)]
            if len(matches) == 1:
                paths.append(str(ctx.system.path(matches[0])))
            else:
                problems.append(f"{package}_*.deb ({'missing' if not matches else 'multiple matches'})")
        return paths, problems

    def _resolve_packages(self, ctx: Context) -> list[str] | None:
        directory = ctx.options.debs_dir or CACHE
        if not PurePosixPath(directory).is_absolute() or ".." in PurePosixPath(directory).parts:
            raise FixError("Use an absolute --debs directory without '..', then retry.")
        paths, problems = self._package_set(ctx, directory)
        if not problems:
            return paths
        if ctx.options.debs_dir:
            raise FixError(f"Incomplete package set in {directory}: {', '.join(problems)}. "
                           "Provide exactly one .deb for each package, then retry.")
        ctx.ui.info("Building the T1 packages takes a few minutes with Docker "
                    "(about 5 on 6 cores), or longer natively. "
                    f"Full build output goes to {BUILD_LOG}.")
        # __file__ belongs to the checkout, not the target System root.
        script = str(PurePosixPath(__file__).parents[2] / "packaging/t1bridge/build-debs.sh")
        argv = [script]
        if not ctx.system.run(["docker", "info"], check=False).ok:
            ctx.ui.info("Docker is unavailable. A native build installs build tools and "
                        "patched fingerprint libraries with apt on this computer.")
            if not ctx.ui.confirm("Build the packages natively?", default=False):
                raise FixError("Native build cancelled. Start Docker or supply --debs DIR, then retry.")
            argv.append("--native")
        argv.extend(["--out", str(ctx.system.path(CACHE))])
        result = ctx.system.run_streamed(
            argv, log_path=BUILD_LOG,
            on_line=lambda line: ctx.ui.info(line) if line.startswith("== ") else None)
        if result:
            raise FixError(f"The T1 package build failed. Check {BUILD_LOG}, then retry.")
        if ctx.system.dry_run:
            ctx.ui.info("After the build, all five packages must be present before the legacy "
                        "driver is removed or any packages are installed.")
            return None
        paths, problems = self._package_set(ctx, CACHE)
        if problems:
            raise FixError(f"The build left an incomplete package set: {', '.join(problems)}. "
                           f"Check {BUILD_LOG}, then retry.")
        return paths

    def _remove_legacy(self, ctx: Context) -> None:
        system = ctx.system
        result = system.run(["dkms", "status", "apple-ib-drv"], check=False)
        versions = sorted(set(re.findall(r"^apple-ib-drv[/,]\s*([^,\s:]+)",
                                        result.stdout, re.MULTILINE)))
        if result.stdout.strip() and not versions:
            raise FixError("Could not read the legacy Touch Bar driver version. "
                           "Check dkms status apple-ib-drv before retrying.")
        files = [path for path in LEGACY_FILES if system.exists(path)]
        sources = system.glob("/usr/src/apple-ib-drv-*")
        if not (versions or files or sources):
            return
        ctx.ui.info("Removing the old Touch Bar driver because it forces the T1 into a "
                    "mode that conflicts with t1bridge. Its settings will be saved in "
                    f"{system.STATE_DIR}/legacy/.")
        # Keep the original directory names: two settings files have the same basename.
        for path in files:
            backup = system.STATE_DIR + "/legacy" + path
            if not system.exists(backup):
                system.copy_file(path, backup, 0o644)
            system.note(self.id, "legacy_backup:" + path, backup)
        for version in versions:
            system.run(["dkms", "remove", "-m", "apple-ib-drv", "-v", version, "--all"],
                       mutating=True)
            system.note(self.id, "legacy_dkms_removed:" + version, True)
        for path in [*sources, *files]:
            system.run(["rm", "-rf", "--", str(system.path(path))], mutating=True)
            system.note(self.id, "legacy_removed:" + path, True)

    def _install_override(self, ctx: Context) -> None:
        system = ctx.system
        source = self._rule(ctx)
        if RULE_MATCH in source:
            ctx.ui.info("Updating the usbmuxd rule so it stops deconfiguring the T1.")
            changed = system.install_file(self.id, OVERRIDE,
                                          RULE_HEADER + source.replace(RULE_MATCH, ""))
            system.note(self.id, SHA_NOTE, self._rule_digest(ctx))
        else:
            changed = system.remove_file(self.id, OVERRIDE)
            if system.notes(self.id).get(SHA_NOTE):
                system.note(self.id, SHA_NOTE, None)
        if changed:
            system.run(["udevadm", "control", "--reload"], mutating=True)

    def install(self, ctx: Context) -> list[str]:
        if self.gate(ctx.hw):
            return []
        warnings = self._hardware_warnings(ctx)
        for message in warnings:
            ctx.ui.warn(message)
        if self.status(ctx).state == State.DONE:
            return warnings
        versions = ctx.system.package_versions(["t1bridge", "t1bridge-dkms"])
        installing = any(versions.get(name) != VERSION for name in ("t1bridge", "t1bridge-dkms"))
        if installing:
            paths = self._resolve_packages(ctx)
            if paths is None:  # A dry-run build cannot produce packages to validate.
                return warnings
            self._remove_legacy(ctx)
            ctx.ui.info("Installing the five matching T1 packages so the drivers and services work together.")
            ctx.system.run(["apt-get", "install", "-y", "--allow-downgrades", *paths],
                           env={"DEBIAN_FRONTEND": "noninteractive"}, mutating=True)
        holds = self._missing_holds(ctx)
        if holds:
            ctx.ui.info("Holding the fingerprint packages so updates cannot split the matched set.")
            ctx.system.run(["apt-mark", "hold", *holds], mutating=True)
            ctx.system.note(self.id, "holds", list(HOLDS))
        user = ctx.system.invoking_user()
        if user and user.uid != 0 and self._group_warning(ctx):
            ctx.ui.info(f"Adding {user.name} to the t1bridge group so they can use Touch ID.")
            ctx.system.run(["usermod", "-aG", "t1bridge", user.name], mutating=True)
        self._install_override(ctx)
        if not installing:  # Only holds, group or the usbmuxd rule were repaired.
            return warnings
        return warnings + [
            "Reboot, then run sudo t1bridge status and check that every row is ready "
            "(usb-configuration may say selected; diagnostics unavailable is expected).",
            "Import this Mac's T1 data with sudo systemctl start t1bridge-import.service.",
            "As your normal user WITHOUT sudo, run fprintd-enroll -f right-index-finger, "
            "then fprintd-verify -f right-index-finger. Enrolling with sudo fails.",
        ]

    def remove(self, ctx: Context) -> list[str]:
        # Resolve replacements before removing a working fingerprint stack.
        replacements = []
        for package in HOLDS:
            result = ctx.system.run(["apt-cache", "madison", package], check=False)
            versions = [fields[1].strip() for line in result.stdout.splitlines()
                        if len(fields := line.split("|")) >= 3
                        and fields[0].strip() == package and fields[1].strip()
                        and "t1bridge" not in fields[1]] if result.ok else []
            if not versions:
                raise FixError(f"No distribution version of {package} was found. "
                               "Refresh the apt package lists and retry removal.")
            replacements.append(f"{package}={versions[0]}")
        ctx.ui.info("Removing t1bridge and its package holds, then restoring the distribution's "
                    "fingerprint packages. T1 data in /var/lib/t1bridge will be kept.")
        system = ctx.system
        system.run(["apt-get", "remove", "-y", "t1bridge", "t1bridge-dkms"], mutating=True)
        system.run(["apt-mark", "unhold", *HOLDS], mutating=True)
        system.note(self.id, "holds", [])
        # Native builds leave development packages depending on the patched runtime.
        versions = system.package_versions(["libfprint-2-dev", "gir1.2-fprint-2.0"])
        extras = [name for name, version in versions.items() if "t1bridge" in version]
        if extras:
            system.run(["apt-get", "remove", "-y", *extras], mutating=True)
        system.run(["apt-get", "install", "-y", "--allow-downgrades", *replacements],
                   env={"DEBIAN_FRONTEND": "noninteractive"}, mutating=True)
        if system.remove_file(self.id, OVERRIDE):
            system.run(["udevadm", "control", "--reload"], mutating=True)
        system.note(self.id, SHA_NOTE, None)
        return ["Reboot to finish removing t1bridge. The legacy Touch Bar driver is not reinstalled."]

    def health(self, ctx: Context) -> list[Health]:
        checks = [Health("warn", message) for message in self._hardware_warnings(ctx)]
        if "t1bridge" not in ctx.system.package_versions(["t1bridge"]):
            return checks
        installed = dkms.installed_kernels(ctx.system, "t1bridge-dkms")
        for kernel in ctx.hw.kernels:
            if ctx.hw.headers.get(kernel) and kernel not in installed:
                checks.append(Health("warn", f"t1bridge-dkms is not installed for kernel {kernel}. "
                                     "Re-run install t1bridge to rebuild the driver."))
        if ctx.system.is_root():
            result = ctx.system.run(["t1bridge", "status"], check=False)
            for line in result.stdout.splitlines():
                row, separator, value = line.partition(":")
                if separator and value.strip() not in ("ready", "selected", "diagnostics unavailable"):
                    checks.append(Health("warn", f"t1bridge {row.strip()}: {value.strip()}."))
            if not result.ok:
                checks.append(Health("warn", "Could not complete t1bridge status; run sudo t1bridge "
                                     "status to check the T1 services."))
        else:
            checks.append(Health("warn", "Run status with sudo to check the t1bridge services."))
        holds = self._missing_holds(ctx)
        if holds:
            checks.append(Health("warn", "Missing package holds: " + ", ".join(holds)
                                 + ". Re-run install t1bridge to keep the packages matched."))
        checks.extend(Health("warn", message) for message in
                      (self._group_warning(ctx), self._override_warning(ctx)) if message)
        firewall = ctx.system.run(["ufw", "status"], check=False)
        if re.search(r"^Status:\s*active\s*$", firewall.stdout, re.MULTILINE):
            checks.append(Health("warn", "The firewall is active. t1bridge needs inbound IPv6 TCP "
                                 "61500 on the private T1 network link only, never on Wi-Fi. See "
                                 "https://github.com/standardagents/t1bridge/blob/main/docs/"
                                 "setup.md#firewall-recovery-and-removal"))
        return checks
