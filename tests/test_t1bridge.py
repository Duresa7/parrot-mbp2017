"""Offline coverage of package preparation, migration and T1 health checks."""

from hashlib import sha256
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpers import FakeRunner, KERNEL, make_mac
from mbp2017.fixes.base import Context, FixError, Health, Options, State
from mbp2017.fixes.t1bridge import (BUILD_LOG, CACHE, HOLDS, LEGACY_FILES, OVERRIDE,
                                  PACKAGES, RULE_HEADER, SHA_NOTE, STOCK_RULE, VERSION,
                                  T1BridgeFix)
from mbp2017.hardware import probe
from mbp2017.system import Result, System
from mbp2017.ui import UI


DKMS = f"""acpi-call/1.2.2, {KERNEL}, x86_64: installed
snd_hda_macbookpro/0.1, {KERNEL}, x86_64: installed (Original modules exist)
t1bridge-dkms/0.1.12, {KERNEL}, x86_64: installed (Original modules exist)
"""
HEALTHY = """usb-configuration: selected
drm: ready
ncm: ready
xart: ready
xart-admission: diagnostics unavailable
keybag: ready
broker: ready
touchbar: ready
"""
STOCK = '''SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ENV{PRODUCT}=="5ac/12[9a][0-9a-f]/*|5ac/8600/*", TAG+="systemd"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ENV{PRODUCT}=="5ac/12[9a][0-9a-f]/*|5ac/8600/*", ACTION=="add", ENV{USBMUX_SUPPORTED}="1", ATTR{bConfigurationValue}="0", OWNER="usbmux", ENV{SYSTEMD_WANTS}="usbmuxd.service"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ENV{PRODUCT}=="5ac/12[9a][0-9a-f]/*|5ac/8600/*", ACTION=="bind", ENV{USBMUX_SUPPORTED}="1", OWNER="usbmux", ENV{SYSTEMD_WANTS}="usbmuxd.service"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ENV{PRODUCT}=="5ac/12[9a][0-9a-f]/*|5ac/8600/*", ACTION=="remove", RUN+="/usr/sbin/usbmuxd -x"
'''


class T1BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parents[1])
        self.addCleanup(self.temp.cleanup)
        self.runner = FakeRunner()
        self.out = StringIO()
        self.system = System(make_mac(self.temp.name), runner=self.runner,
                             env={"SUDO_USER": "alice"}, euid=0, out=self.out)
        self.ui = UI(color=False, assume_yes=True, interactive=False, out=self.out)
        with patch.object(self.system, "kernel_release", return_value=KERNEL):
            self.ctx = Context(self.system, probe(self.system), self.ui, Options(debs_dir="/debs"))
        self.fix = T1BridgeFix()

    def write(self, path, content=""):
        target = self.system.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def debs(self, directory="/debs", missing=()):
        for name in PACKAGES:
            if name not in missing:
                self.write(f"{directory}/{name}_{VERSION}_amd64.deb", "fixture")

    def installed(self, versions=None):
        if versions is None:
            versions = {"t1bridge": VERSION, "t1bridge-dkms": VERSION}
        self.runner.scripts[("dpkg-query",)] = Result(
            0, "".join(f"{name}\t{version}\tii \n" for name, version in versions.items()))

    def ready(self):
        self.installed()
        self.runner.scripts[("apt-mark", "showhold")] = Result(0, "\n".join(HOLDS))
        self.write("/etc/group", "t1bridge:x:987:alice\n")

    def test_status_todo_partial_done_and_gate(self):
        self.assertEqual(self.fix.status(self.ctx).state, State.TODO)
        self.installed({"t1bridge": "0.1.11-1"})
        state = self.fix.status(self.ctx)
        self.assertEqual(state.state, State.PARTIAL)
        for detail in ("t1bridge-dkms", VERSION, "holds", "alice"):
            self.assertIn(detail, state.detail)
        self.ready()
        self.assertEqual(self.fix.status(self.ctx).state, State.DONE)
        self.ctx.hw.t1_state = None
        self.assertEqual(self.fix.status(self.ctx).state, State.NOT_NEEDED)
        self.assertEqual(self.fix.install(self.ctx), [])

    def test_install_order_absolute_paths_and_notes(self):
        self.debs()
        notes = self.fix.install(self.ctx)
        calls = self.runner.calls
        apt = next(call for call in calls if call[:2] == ["apt-get", "install"])
        self.assertEqual(apt[:4], ["apt-get", "install", "-y", "--allow-downgrades"])
        self.assertEqual(len(apt[4:]), 5)
        self.assertTrue(all(path.startswith(str(self.system.path("/debs")) + "/")
                            for path in apt[4:]))
        self.assertEqual(self.runner.options[calls.index(apt)]["env"]["DEBIAN_FRONTEND"],
                         "noninteractive")
        hold = ["apt-mark", "hold", *HOLDS]
        usermod = ["usermod", "-aG", "t1bridge", "alice"]
        self.assertLess(calls.index(apt), calls.index(hold))
        self.assertLess(calls.index(hold), calls.index(usermod))
        self.assertEqual(self.system.notes(self.fix.id)["holds"], list(HOLDS))
        for text in ("Reboot", "sudo t1bridge status", "t1bridge-import.service",
                     "WITHOUT sudo", "fprintd-enroll -f right-index-finger",
                     "fprintd-verify -f right-index-finger", "Enrolling with sudo fails"):
            self.assertIn(text, " ".join(notes))

    def test_incomplete_or_ambiguous_packages_never_touch_legacy(self):
        self.debs(missing=("fprintd",))
        self.write(LEGACY_FILES[0], "old settings")
        with self.assertRaisesRegex(FixError, "fprintd.*missing"):
            self.fix.install(self.ctx)
        self.debs()
        self.write("/debs/fprintd_other_amd64.deb")
        with self.assertRaisesRegex(FixError, "multiple matches"):
            self.fix.install(self.ctx)
        self.assertFalse(any(call[0] in ("dkms", "rm", "apt-get", "apt-mark", "usermod")
                             for call in self.runner.calls))
        self.assertEqual(self.system.read_text(LEGACY_FILES[0]), "old settings")

    def test_legacy_versions_backups_and_rooted_removal(self):
        self.debs()
        for index, path in enumerate(LEGACY_FILES):
            self.write(path, f"legacy {index}")
        self.write("/usr/src/apple-ib-drv-0.1/source", "source")
        self.runner.scripts[("dkms", "status", "apple-ib-drv")] = Result(
            0, f"apple-ib-drv/0.1, {KERNEL}, x86_64: installed\n"
            "apple-ib-drv/0.1, other-kernel, x86_64: installed\n")
        self.fix.install(self.ctx)
        command = ["dkms", "remove", "-m", "apple-ib-drv", "-v", "0.1", "--all"]
        self.assertEqual(self.runner.calls.count(command), 1)
        for index, path in enumerate(LEGACY_FILES):
            self.assertEqual(self.system.read_text(System.STATE_DIR + "/legacy" + path),
                             f"legacy {index}")
            self.assertIn(["rm", "-rf", "--", str(self.system.path(path))], self.runner.calls)
            self.assertTrue(self.system.notes(self.fix.id)["legacy_removed:" + path])
        self.assertIn(["rm", "-rf", "--", str(self.system.path("/usr/src/apple-ib-drv-0.1"))],
                      self.runner.calls)

    def test_complete_cache_skips_build(self):
        self.ctx.options.debs_dir = None
        self.debs(CACHE)
        self.fix.install(self.ctx)
        self.assertNotIn(["docker", "info"], self.runner.calls)

    def test_pinned_packages_only_repair_missing_integration(self):
        self.installed()
        self.runner.scripts[("apt-mark", "showhold")] = Result(0, HOLDS[0])
        self.write(STOCK_RULE, STOCK)
        with patch.object(self.fix, '_resolve_packages') as resolve:
            notes = self.fix.install(self.ctx)
        resolve.assert_not_called()
        self.assertFalse(any(call[0] in ('apt-get', 'docker', 'dkms', 'rm')
                             or call[0].endswith('build-debs.sh') for call in self.runner.calls))
        self.assertIn(['apt-mark', 'hold', *HOLDS[1:]], self.runner.calls)
        self.assertIn(['usermod', '-aG', 't1bridge', 'alice'], self.runner.calls)
        self.assertEqual(self.system.read_text(OVERRIDE), RULE_HEADER + STOCK.replace('|5ac/8600/*', ''))
        self.assertNotIn('fprintd-enroll', ' '.join(notes))  # repair only: no reinstall steps

    def test_pinned_packages_keep_existing_holds_and_group(self):
        self.ready()
        self.write(STOCK_RULE, STOCK)
        self.fix.install(self.ctx)
        self.assertFalse(any(call[:2] in (['apt-get', 'install'], ['apt-mark', 'hold'])
                             or call[0] == 'usermod' for call in self.runner.calls))
        self.assertTrue(self.system.exists(OVERRIDE))

    def test_wrong_or_missing_pinned_package_still_installs(self):
        self.debs()
        for versions in ({'t1bridge': VERSION},
                         {'t1bridge': VERSION, 't1bridge-dkms': 'old'},
                         {'t1bridge': 'old', 't1bridge-dkms': VERSION}):
            with self.subTest(versions=versions):
                self.installed(versions)
                self.runner.calls.clear()
                self.fix.install(self.ctx)
                self.assertTrue(any(call[:2] == ['apt-get', 'install'] for call in self.runner.calls))

    def build_runner(self, docker_code=0, build_code=0, produce=True):
        self.ctx.options.debs_dir = None
        self.runner.scripts[("docker", "info")] = Result(docker_code)
        script = str(Path(__file__).parents[1] / "packaging/t1bridge/build-debs.sh")
        self.runner.scripts[(script,)] = Result(build_code, "== building packages\ncompiler noise\n")

        def run(argv, opts):
            result = self.runner(argv, opts)
            if argv[0] == script and produce:
                self.debs(CACHE)
            return result

        self.system.runner = run
        return script

    def test_build_uses_docker_and_displays_only_stage_lines(self):
        script = self.build_runner()
        with patch.object(self.ui, "confirm") as confirm:
            self.fix.install(self.ctx)
        self.assertIn([script, "--out", str(self.system.path(CACHE))], self.runner.calls)
        confirm.assert_not_called()
        self.assertIn("== building packages", self.out.getvalue())
        self.assertNotIn("compiler noise", self.out.getvalue())
        self.assertIn("compiler noise", self.system.read_text(BUILD_LOG))

    def test_build_uses_native_only_after_confirmation(self):
        script = self.build_runner(1)

        def confirm(*args, **kwargs):
            self.assertFalse(any(call[0] == script for call in self.runner.calls))
            return True

        with patch.object(self.ui, "confirm", side_effect=confirm) as prompt:
            self.fix.install(self.ctx)
        prompt.assert_called_once_with("Build the packages natively?", default=False)
        self.assertIn([script, "--native", "--out", str(self.system.path(CACHE))], self.runner.calls)
        self.assertIn("installs build tools", self.out.getvalue())

    def test_native_decline_and_build_failure_stop_before_legacy(self):
        script = self.build_runner(1)
        with patch.object(self.ui, "confirm", return_value=False):
            with self.assertRaisesRegex(FixError, "cancelled"):
                self.fix.install(self.ctx)
        self.assertFalse(any(call[0] == script for call in self.runner.calls))
        self.build_runner(0, build_code=1, produce=False)
        with self.assertRaisesRegex(FixError, BUILD_LOG):
            self.fix.install(self.ctx)
        self.assertFalse(any(call[0] in ("dkms", "apt-get", "rm") for call in self.runner.calls))

    def test_successful_build_must_produce_complete_packages(self):
        self.build_runner(produce=False)
        with self.assertRaisesRegex(FixError, "incomplete package set"):
            self.fix.install(self.ctx)
        self.assertNotIn(["dkms", "status", "apple-ib-drv"], self.runner.calls)

    def test_build_output_is_used(self):
        self.build_runner()
        self.fix.install(self.ctx)
        self.assertTrue(any(call[:2] == ["apt-get", "install"] for call in self.runner.calls))

    def test_override_content_hash_staleness_and_idempotence(self):
        self.debs()
        self.write(STOCK_RULE, STOCK)
        self.fix.install(self.ctx)
        self.assertEqual(self.system.read_text(OVERRIDE), RULE_HEADER + STOCK.replace("|5ac/8600/*", ""))
        self.assertEqual(self.system.notes(self.fix.id)[SHA_NOTE], sha256(STOCK.encode()).hexdigest())
        self.ready()
        self.assertEqual(self.fix.status(self.ctx).state, State.DONE)
        self.runner.calls.clear()
        self.fix.install(self.ctx)
        self.assertFalse(any(call[0] in ("apt-get", "usermod", "udevadm") for call in self.runner.calls))
        self.write(STOCK_RULE, STOCK + "# updated\n")
        self.assertEqual(self.fix.status(self.ctx).state, State.PARTIAL)
        self.assertIn("out of date", " ".join(check.message for check in self.fix.health(self.ctx)))
        self.fix.install(self.ctx)
        self.assertEqual(self.fix.status(self.ctx).state, State.DONE)
        self.write(STOCK_RULE, "# upstream no longer matches the T1\n")
        self.fix.install(self.ctx)
        self.assertFalse(self.system.exists(OVERRIDE))
        self.assertEqual(self.fix.status(self.ctx).state, State.DONE)

    def madison(self):
        versions = ("1:1.94.9-1", "1.94.5-2", "1.94.5-2")
        for package, version in zip(HOLDS, versions):
            self.runner.scripts[("apt-cache", "madison", package)] = Result(
                0, f" {package} | 9+t1bridge1 | repo\n {package} | {version} | "
                "https://deb.parrot.sh/parrot echo/main amd64 Packages\n")
        return [f"{package}={version}" for package, version in zip(HOLDS, versions)]

    def test_remove_restores_distribution_and_preserves_data(self):
        replacements = self.madison()
        self.installed({"libfprint-2-dev": "1:1.94.9-1~t1bridge1",
                        "gir1.2-fprint-2.0": "1:1.94.9-1"})
        self.write("/var/lib/t1bridge/private", "keep me")
        self.write(OVERRIDE, "existing local rule")
        self.system.install_file(self.fix.id, OVERRIDE, RULE_HEADER)
        notes = self.fix.remove(self.ctx)
        calls = self.runner.calls
        remove = ["apt-get", "remove", "-y", "t1bridge", "t1bridge-dkms"]
        unhold = ["apt-mark", "unhold", *HOLDS]
        restore = ["apt-get", "install", "-y", "--allow-downgrades", *replacements]
        extras = ["apt-get", "remove", "-y", "libfprint-2-dev"]
        self.assertLess(calls.index(remove), calls.index(unhold))
        self.assertLess(calls.index(unhold), calls.index(restore))
        self.assertLess(calls.index(extras), calls.index(restore))
        self.assertEqual(self.system.read_text("/var/lib/t1bridge/private"), "keep me")
        self.assertEqual(self.system.read_text(OVERRIDE), "existing local rule")
        self.assertIn(["udevadm", "control", "--reload"], calls)
        self.assertIn("Reboot", " ".join(notes))
        self.assertFalse(any("apple-ib-drv" in " ".join(call) for call in calls))

    def test_remove_without_distribution_candidate_changes_nothing(self):
        with self.assertRaisesRegex(FixError, "Refresh the apt package lists"):
            self.fix.remove(self.ctx)
        self.assertFalse(any(call[0] in ("apt-get", "apt-mark") for call in self.runner.calls))

    def test_health_sample_and_missing_kernel_nonready_row_firewall(self):
        self.ready()
        self.runner.scripts[("dkms", "status", "t1bridge-dkms")] = Result(0, DKMS)
        self.runner.scripts[("t1bridge", "status")] = Result(0, HEALTHY)
        self.assertEqual(self.fix.health(self.ctx), [])
        self.ctx.hw.kernels += ["other-kernel", "no-headers"]
        self.ctx.hw.headers.update({"other-kernel": True, "no-headers": False})
        self.runner.scripts[("t1bridge", "status")] = Result(0, HEALTHY.replace("drm: ready", "drm: waiting"))
        self.runner.scripts[("ufw", "status")] = Result(0, "Status: active\n")
        self.runner.scripts[("apt-mark", "showhold")] = Result(0, "fprintd\n")
        self.write("/etc/group", "t1bridge:x:987:someone-else\n")
        text = " ".join(check.message for check in self.fix.health(self.ctx))
        for expected in ("other-kernel", "drm: waiting", "libfprint-2-2", "alice",
                         "IPv6 TCP 61500", "never on Wi-Fi", "#firewall-recovery-and-removal"):
            self.assertIn(expected, text)
        self.assertNotIn("no-headers", text)
        self.assertNotIn("diagnostics unavailable", text)

    def test_uninstalled_hardware_warnings_and_nonroot_health(self):
        self.ctx.hw.t1_state = "recovery"
        self.ctx.hw.t1_data = False
        text = " ".join(check.message for check in self.fix.health(self.ctx))
        self.assertIn("restore-t1", text)
        self.assertIn("Touch ID will not work", text)
        self.assertFalse(any(call[0] in ("dkms", "ufw", "t1bridge") for call in self.runner.calls))
        self.debs()
        notes = self.fix.install(self.ctx)
        self.assertIn("restore-t1", " ".join(notes))
        self.ready()
        self.system.euid = 1000
        checks = self.fix.health(self.ctx)
        self.assertIn(Health("warn", "Run status with sudo to check the t1bridge services."), checks)
        self.assertNotIn(["t1bridge", "status"], self.runner.calls)

    def test_primary_group_and_no_nonroot_invoker(self):
        self.ready()
        self.write("/etc/group", "t1bridge:x:1000:\n")
        self.assertEqual(self.fix.status(self.ctx).state, State.DONE)
        self.system.env = {}
        self.write("/etc/group", "")
        self.assertEqual(self.fix.status(self.ctx).state, State.DONE)

    def test_dry_run_install_remove_and_build_write_nothing(self):
        self.debs()
        self.write(STOCK_RULE, STOCK)
        self.write(LEGACY_FILES[0], "legacy")
        self.madison()
        self.system.dry_run = True
        self.ctx.options.dry_run = True
        before = {str(p): p.read_bytes() for p in Path(self.temp.name).rglob("*") if p.is_file()}
        self.fix.install(self.ctx)
        self.fix.remove(self.ctx)
        self.ctx.options.debs_dir = None
        self.fix.install(self.ctx)
        after = {str(p): p.read_bytes() for p in Path(self.temp.name).rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(any(call[0] in ("apt-get", "rm", "usermod", "udevadm")
                             or call[:2] in (["apt-mark", "hold"], ["apt-mark", "unhold"])
                             for call in self.runner.calls))
        self.assertIn("all five packages must be present", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
