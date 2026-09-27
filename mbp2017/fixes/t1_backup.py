"""Back up the T1's firmware and Touch ID data, and restore it when it is lost."""

from __future__ import annotations

import hashlib
import tarfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Iterator

from .base import Context, Fix, FixError, State, Status
from ..hardware import Hardware, probe

RESTORE_WORKDIR = "/var/lib/parrot-mbp2017/restore-t1"

T1_REVIVE_REPO = "https://github.com/niconistal/t1-revive.git"
T1_REVIVE_COMMIT = "c2062f3a09b2d278649d3ec48bbb7d15c8b55bcf"
T1_REVIVE_DIR = "/var/cache/parrot-mbp2017/t1-revive"
T1_REVIVE_BUILD_LOG = "/var/log/parrot-mbp2017-t1-revive-build.log"
T1_REVIVE_LOG = "/var/log/parrot-mbp2017-t1-revive.log"
ESP_BACKUP_DIR = "/var/lib/parrot-mbp2017"

BUILD_PACKAGES = ("autoconf", "automake", "libtool", "pkgconf", "git", "patch",
                  "libzip-dev", "libusb-1.0-0-dev", "libssl-dev", "zlib1g-dev",
                  "libreadline-dev", "acpi-call-dkms", "python3")
BACKPORTS_PACKAGE = "libcurl4-openssl-dev"

_EXIT_MEANINGS = {
    3: "preflight failed",
    4: "it was refused for safety",
    5: "the device is in an unexpected state",
    6: "a network or Apple server failure",
    7: "a reboot is needed first",
}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class T1BackupFix(Fix):
    id = "t1-backup"
    title = "Back up the T1 firmware and Touch ID data"
    summary = "Save a copy of the T1's firmware and Touch ID data from the EFI partition."
    why = ("Touch ID needs this Mac's T1 data in EFI/APPLE on the EFI partition. Reinstalling "
           "Linux often erases it, and recreating it takes an online recovery through Apple's "
           "servers.")

    def gate(self, hw: Hardware) -> str | None:
        if not hw.t1:
            return "No T1 chip detected."
        if hw.t1_data is False:
            return "No T1 firmware data found on the EFI partition to back up."
        return None

    def _backup_dir(self, ctx: Context) -> str | None:
        if ctx.options.backup_dir:
            return ctx.options.backup_dir
        user = ctx.system.invoking_user()
        return f"{user.home}/parrot-mbp2017-t1-backup" if user else None

    def _current_backup_matches(self, ctx: Context, backup_dir: str) -> bool:
        text = ctx.system.read_text(f"{backup_dir}/SHA256SUMS")
        lines = [line for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return False
        for line in lines:
            digest, _, member = line.partition("  ")
            if not member:
                return False
            try:
                data = ctx.system.read_bytes(f"{ctx.hw.esp}/{member}")
            except FileNotFoundError:
                return False
            if hashlib.sha256(data).hexdigest() != digest:
                return False
        return True

    def status(self, ctx: Context) -> Status:
        reason = self.gate(ctx.hw)
        if reason:
            return Status(State.NOT_NEEDED, reason)
        if ctx.hw.t1_data is None:
            return Status(State.TODO, "T1 data presence is unknown. Run with sudo to check it and make a backup.")
        backup_dir = self._backup_dir(ctx)
        if backup_dir and self._current_backup_matches(ctx, backup_dir):
            return Status(State.DONE)
        return Status(State.TODO)

    def _write_backup(self, ctx: Context, backup_dir: str) -> list[str]:
        archive_path = f"{backup_dir}/EFI-APPLE-{_today()}.tar"
        sums_path = f"{backup_dir}/SHA256SUMS"
        efi_apple = f"{ctx.hw.esp}/EFI/APPLE"
        host_archive = ctx.system.path(archive_path)
        host_archive.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(host_archive, "w") as tar:
            tar.add(ctx.system.path(efi_apple), arcname="EFI/APPLE")
        host_archive.chmod(0o600)
        lines = []
        with tarfile.open(host_archive, "r") as tar:
            members = sorted((member for member in tar.getmembers()
                              if member.isfile() and member.name.startswith("EFI/APPLE/EMBEDDEDOS/")),
                             key=lambda member: member.name)
            for member in members:
                data = tar.extractfile(member).read()
                lines.append(f"{hashlib.sha256(data).hexdigest()}  {member.name}\n")
        host_sums = ctx.system.path(sums_path)
        host_sums.write_text("".join(lines), encoding="utf-8")
        host_sums.chmod(0o600)
        user = ctx.system.invoking_user()
        if user is not None:
            ctx.system.chown(archive_path, user.uid, user.gid)
            ctx.system.chown(sums_path, user.uid, user.gid)
        ctx.system.log(f"t1-backup: wrote {archive_path} and {sums_path}")
        return [f"Backup saved to {backup_dir}.",
                "Keep a copy off this Mac (a USB stick or private cloud storage) and never share it: "
                "it contains this Mac's serial number and T1 identity."]

    def install(self, ctx: Context) -> list[str]:
        if self.gate(ctx.hw):
            return []
        if ctx.hw.t1_data is None:
            raise FixError("Could not read the T1 data on the EFI partition. Run this command with sudo.")
        if self.status(ctx).state == State.DONE:
            return []
        backup_dir = self._backup_dir(ctx)
        if backup_dir is None:
            raise FixError("Could not determine the invoking user's home directory. "
                          "Pass --backup-dir DIR to choose where to store the T1 backup.")
        user = ctx.system.invoking_user()
        ctx.ui.info(f"Backing up this Mac's T1 firmware and Touch ID data to {backup_dir}.")
        ctx.system.makedirs(backup_dir, 0o700, owner=(user.uid, user.gid) if user else None)
        if ctx.system.dry_run:
            ctx.ui.info(f"would create T1 backup: {backup_dir}/EFI-APPLE-{_today()}.tar")
            return []
        return self._write_backup(ctx, backup_dir)

    def remove(self, ctx: Context) -> list[str]:
        backup_dir = self._backup_dir(ctx)
        if backup_dir and ctx.system.exists(backup_dir):
            return [f"The T1 backup at {backup_dir} was not deleted. Remove it yourself if you no longer want it."]
        return []

    # -- restore-t1 --------------------------------------------------

    def restore_t1(self, ctx: Context, source: str) -> list[str]:
        if source == "online":
            return self._restore_online(ctx)
        return self._restore_from(ctx, source)

    def _verify_checksums(self, ctx: Context, sums_path: str, base_dir: str) -> None:
        text = ctx.system.read_text(sums_path, "") or ""
        for line in text.splitlines():
            if not line.strip():
                continue
            digest, _, member = line.partition("  ")
            if not member:
                raise FixError(f"{sums_path} is not in sha256sum format.")
            try:
                data = ctx.system.read_bytes(f"{base_dir}/{member}")
            except FileNotFoundError:
                raise FixError(f"{sums_path} lists {member}, which is missing from {base_dir}.")
            if hashlib.sha256(data).hexdigest() != digest:
                raise FixError(f"Checksum mismatch for {member}. The backup may be damaged.")

    @contextmanager
    def _prepare_source(self, ctx: Context, source: str) -> Iterator[tuple[str, str | None]]:
        if ctx.system.exists(f"{source}/EFI/APPLE/EMBEDDEDOS/FDRData"):
            sums = f"{source}/SHA256SUMS"
            sums = sums if ctx.system.exists(sums) else None
            if sums:
                self._verify_checksums(ctx, sums, source)
            yield source, sums
            return
        tar_matches = sorted(ctx.system.glob(f"{source}/EFI-APPLE-*.tar"))
        if not tar_matches:
            raise FixError(f"{source} is not a T1 backup: it has neither "
                          "EFI/APPLE/EMBEDDEDOS/FDRData nor an EFI-APPLE-*.tar archive.")
        tar_path = tar_matches[-1]
        extract_dir = f"{RESTORE_WORKDIR}/{PurePosixPath(tar_path).stem}"
        ctx.system.makedirs(ESP_BACKUP_DIR, 0o700)
        ctx.system.makedirs(RESTORE_WORKDIR, 0o700)
        try:
            ctx.system.makedirs(extract_dir, 0o700)
            try:
                ctx.system.extract_tar(tar_path, extract_dir)
            except (ValueError, tarfile.TarError) as exc:
                raise FixError(str(exc)) from exc
            if not ctx.system.exists(f"{extract_dir}/EFI/APPLE/EMBEDDEDOS/FDRData"):
                raise FixError(f"{tar_path} does not contain EFI/APPLE/EMBEDDEDOS/FDRData.")
            sums = f"{source}/SHA256SUMS"
            sums = sums if ctx.system.exists(sums) else None
            if sums:
                self._verify_checksums(ctx, sums, extract_dir)
            yield extract_dir, sums
        finally:
            ctx.system.remove_tree(extract_dir)

    def _walk_files(self, ctx: Context, base: str) -> list[str]:
        files: list[str] = []
        stack = [""]
        while stack:
            rel = stack.pop()
            current = f"{base}/{rel}" if rel else base
            for name in ctx.system.listdir(current):
                child_rel = f"{rel}/{name}" if rel else name
                child_abs = f"{current}/{name}"
                if ctx.system.is_dir(child_abs):
                    stack.append(child_rel)
                else:
                    files.append(child_rel)
        return sorted(files)

    def _copy_efi_apple(self, ctx: Context, src_efi_apple: str, dst_efi_apple: str) -> None:
        for rel in self._walk_files(ctx, src_efi_apple):
            dst_path = f"{dst_efi_apple}/{rel}"
            if ctx.system.exists(dst_path):
                continue
            ctx.system.copy_file(f"{src_efi_apple}/{rel}", dst_path, 0o644)

    def _verify_restored(self, ctx: Context, esp: str, sums_path: str | None) -> None:
        for name in ("FDRData", "combined.memboot", "version.plist"):
            if not ctx.system.exists(f"{esp}/EFI/APPLE/EMBEDDEDOS/{name}"):
                raise FixError(f"Restore did not produce EFI/APPLE/EMBEDDEDOS/{name} on the ESP.")
        if sums_path:
            self._verify_checksums(ctx, sums_path, esp)

    def _restore_from(self, ctx: Context, source: str | None) -> list[str]:
        if not source:
            raise FixError("Give a backup directory with --from DIR.")
        if ctx.hw.esp is None:
            raise FixError("No EFI system partition is mounted.")
        if ctx.system.exists(f"{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS"):
            raise FixError("The EFI partition already has EFI/APPLE/EMBEDDEDOS. "
                          "Refusing to overwrite existing T1 data.")
        if not ctx.system.is_dir(source):
            raise FixError(f"{source} is not a directory.")
        if ctx.system.dry_run:
            ctx.ui.info(f"would restore T1 data from {source} onto {ctx.hw.esp}")
            return []
        with self._prepare_source(ctx, source) as (base_dir, sums_path):
            self._copy_efi_apple(ctx, f"{base_dir}/EFI/APPLE", f"{ctx.hw.esp}/EFI/APPLE")
            self._verify_restored(ctx, ctx.hw.esp, sums_path)
        return [
            "Shut down the Mac fully, then power it back on: a warm reboot does not reset the T1.",
            "The T1 should then show up as 05ac:8600 (check with 'sudo parrot-mbp2017 status').",
            "This restore path has not been tested on real hardware.",
        ]

    def _legacy_driver_present(self, ctx: Context) -> bool:
        return (ctx.system.exists("/etc/modprobe.d/apple-touchbar.conf")
                or bool(ctx.system.run(["dkms", "status", "apple-ib-drv"], check=False).stdout.strip()))

    def _restore_online(self, ctx: Context) -> list[str]:
        if ctx.hw.t1_state != "recovery":
            raise FixError("The T1 is not in recovery mode (05ac:1281). "
                          "Online restore is only for a T1 with missing firmware.")
        if ctx.hw.esp is None:
            raise FixError("No EFI system partition is mounted.")
        if ctx.system.exists(f"{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS"):
            raise FixError("The EFI partition already has EFI/APPLE/EMBEDDEDOS. "
                          "Restore from that backup instead of an online restore.")
        if (ctx.system.read_text("/sys/class/power_supply/ADP1/online", "") or "").strip() != "1":
            raise FixError("Plug in the charger before an online T1 restore.")
        if self._legacy_driver_present(ctx):
            raise FixError("The legacy apple-ib-drv Touch Bar driver is installed and can wedge the restore. "
                          "Remove it (see the t1bridge fix) before trying again.")
        if not ctx.ui.confirm(
            "This sends the T1's identity to Apple's servers (gs.apple.com, swcdn.apple.com) for "
            "signing. Continue?", default=False,
        ):
            return ["No changes made."]
        if ctx.system.dry_run:
            ctx.ui.info("would run the online T1 restore (t1-revive)")
            return []
        user = ctx.system.invoking_user()
        if user is None:
            raise FixError("Could not determine the invoking user to run the t1-revive build as.")
        installed = ctx.system.package_versions(list(BUILD_PACKAGES) + [BACKPORTS_PACKAGE])
        missing = [name for name in BUILD_PACKAGES if name not in installed]
        if missing:
            ctx.ui.info("Installing t1-revive build dependencies: " + ", ".join(missing) + ".")
            ctx.system.run(["apt-get", "install", "-y", *missing], mutating=True)
        if BACKPORTS_PACKAGE not in installed:
            ctx.ui.info(f"Installing {BACKPORTS_PACKAGE} from {ctx.hw.distro_codename}-backports.")
            ctx.system.run(["apt-get", "install", "-y", "-t", f"{ctx.hw.distro_codename}-backports",
                           BACKPORTS_PACKAGE], mutating=True)
        ctx.system.run(["modprobe", "acpi_call"], mutating=True)
        ctx.system.run(["systemctl", "stop", "usbmuxd"], check=False, mutating=True)
        if ctx.system.is_dir(T1_REVIVE_DIR):
            ctx.system.run(["git", "-c", f"safe.directory={T1_REVIVE_DIR}", "-C", T1_REVIVE_DIR,
                            "fetch"], mutating=True)
        else:
            ctx.ui.info(f"Cloning t1-revive from {T1_REVIVE_REPO}.")
            ctx.system.run(["git", "clone", T1_REVIVE_REPO, T1_REVIVE_DIR], mutating=True)
        ctx.system.run(["git", "-c", f"safe.directory={T1_REVIVE_DIR}", "-C", T1_REVIVE_DIR,
                        "checkout", "--detach", T1_REVIVE_COMMIT], mutating=True)
        ctx.system.run(["chown", "-R", f"{user.uid}:{user.gid}", T1_REVIVE_DIR], mutating=True)
        ctx.ui.info("Building t1-revive.")
        code = ctx.system.run_streamed(["runuser", "-u", user.name, "--", "bash", "build.sh"],
                                       cwd=T1_REVIVE_DIR, log_path=T1_REVIVE_BUILD_LOG, on_line=ctx.ui.detail)
        if code != 0:
            raise FixError(f"t1-revive failed to build. Check {T1_REVIVE_BUILD_LOG} for details.")
        ctx.ui.info("Running t1-revive preflight checks.")
        preflight = ctx.system.run([f"{T1_REVIVE_DIR}/bin/t1-revive", "preflight"], check=False)
        no_lines = [line for line in preflight.stdout.splitlines()
                   if " NO " in line or line.strip().startswith("NO")]
        bad_lines = [line for line in no_lines if "package" not in line.lower()]
        if bad_lines:
            raise FixError("t1-revive preflight found problems:\n" + "\n".join(bad_lines))
        ctx.ui.info(f"Backing up the whole ESP to {ESP_BACKUP_DIR}.")
        esp_parent = str(PurePosixPath(ctx.hw.esp).parent)
        esp_name = PurePosixPath(ctx.hw.esp).name
        esp_backup = f"{ESP_BACKUP_DIR}/esp-before-t1-revive-{_today()}.tar.gz"
        ctx.system.makedirs(ESP_BACKUP_DIR, 0o700)
        ctx.system.run(["tar", "-C", esp_parent, "-czf", esp_backup, esp_name], mutating=True)
        ctx.ui.info("Regenerating the T1 firmware through Apple's servers. This takes a few minutes.")

        def on_regen_line(line: str) -> None:
            if line.startswith("===") or "regenerate complete" in line:
                ctx.ui.detail(line)

        code = ctx.system.run_streamed(
            ["systemd-inhibit", "--what=sleep:idle:handle-lid-switch", "--who=parrot-mbp2017",
             "--why=T1 firmware restore", "bin/t1-revive", "--no-confirm", "regenerate"],
            cwd=T1_REVIVE_DIR, log_path=T1_REVIVE_LOG, on_line=on_regen_line,
        )
        if code != 0:
            meaning = _EXIT_MEANINGS.get(code, f"exit code {code}")
            raise FixError(f"t1-revive regenerate failed: {meaning}. Check {T1_REVIVE_LOG} for details. "
                          "Do not retry blindly; check the log and the T1's state first.")
        hw_after = probe(ctx.system)
        if hw_after.t1_state != "running":
            raise FixError(f"t1-revive finished but the T1 is not showing as 05ac:8600. "
                          f"Check {T1_REVIVE_LOG}.")
        for name in ("FDRData", "combined.memboot", "version.plist"):
            if not ctx.system.exists(f"{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS/{name}"):
                raise FixError(f"t1-revive finished but EFI/APPLE/EMBEDDEDOS/{name} is missing from the ESP.")
        return [
            "Shut down the Mac fully, then power it back on: a warm reboot does not reset the T1.",
            "After it boots, run the t1-backup fix to save a copy of the restored firmware.",
        ]
