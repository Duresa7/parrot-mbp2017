import io
from pathlib import Path
import tempfile
import unittest

from mbp2017.fixes.base import Context, FixError, Options, State
from mbp2017.hardware import probe
from mbp2017.system import Result, System
from mbp2017.ui import UI
from tests.helpers import FakeRunner, make_mac

from mbp2017.fixes.wifi import WifiFix


class WifiFixTests(unittest.TestCase):
    def context(self, **overrides):
        tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(tmp.cleanup)
        self.root = Path(make_mac(tmp.name, **overrides))
        self.runner = FakeRunner()
        self.output = io.StringIO()
        system = System(str(self.root), runner=self.runner, euid=0,
                        env={"SUDO_USER": "alice"}, out=self.output)
        ui = UI(color=False, assume_yes=True, interactive=False, out=self.output)
        return Context(system, probe(system), ui, Options(assume_yes=True))

    def write(self, path, content=""):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_gate(self):
        ctx = self.context(bcm=False)
        self.assertEqual(WifiFix().status(ctx).state, State.NOT_NEEDED)
        self.assertEqual(WifiFix().install(ctx), [])

    def test_lifecycle_and_commands(self):
        ctx = self.context()
        fix = WifiFix()
        self.write(fix.firmware)
        original = self.write(fix.files[0].path, "# original\n")
        self.assertEqual(fix.status(ctx).state, State.TODO)
        fix.install(ctx)
        self.assertEqual(fix.status(ctx).state, State.DONE)
        self.assertEqual(ctx.system.path(fix.files[2].path).stat().st_mode & 0o777, 0o755)
        changes = [c for c in self.runner.calls if c[0] != "dpkg-query"]
        self.assertEqual(changes, [["update-initramfs", "-u"], ["nmcli", "general", "reload", "conf"]])
        self.runner.calls.clear()
        fix.install(ctx)
        self.assertFalse([c for c in self.runner.calls if c[0] != "dpkg-query"])
        fix.remove(ctx)
        self.assertEqual(original.read_text(), "# original\n")
        self.assertFalse(ctx.system.exists(fix.files[1].path))
        self.assertFalse(ctx.system.exists(fix.files[2].path))
        self.assertEqual(fix.status(ctx).state, State.TODO)
        self.assertEqual([c for c in self.runner.calls if c[0] != "dpkg-query"], changes)

    def test_packages_reboot_and_reload_failure(self):
        ctx = self.context()
        fix = WifiFix()
        self.runner.scripts[("dpkg-query",)] = Result(0, "broadcom-sta-dkms\t1\tii \n")
        self.runner.scripts[("nmcli",)] = Result(1)
        self.write("/sys/module/wl/fixture")
        notes = fix.install(ctx)
        commands = [c for c in self.runner.calls if c[0] != "dpkg-query"]
        self.assertEqual(commands, [["apt-get", "purge", "-y", "broadcom-sta-dkms"],
                                   ["apt-get", "install", "-y", "firmware-brcm80211"],
                                   ["update-initramfs", "-u"], ["nmcli", "general", "reload", "conf"]])
        for call, opts in zip(self.runner.calls, self.runner.options):
            if call[0] == "apt-get":
                self.assertEqual(opts["env"]["DEBIAN_FRONTEND"], "noninteractive")
        self.assertIn("Reboot", " ".join(notes))

    def test_declined_purge_and_dry_run(self):
        ctx = self.context()
        self.runner.scripts[("dpkg-query",)] = Result(0, "broadcom-sta-dkms\t1\tii \n")
        ctx.options.assume_yes = ctx.ui.assume_yes = False
        with self.assertRaises(FixError):
            WifiFix().install(ctx)
        self.assertFalse(any(c[0] == "apt-get" for c in self.runner.calls))
        ctx.options.assume_yes = True
        ctx.system.dry_run = True
        before = self.snapshot()
        WifiFix().install(ctx)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(any(c[0] != "dpkg-query" for c in self.runner.calls))

    def test_package_only_repair_and_preexisting_files(self):
        ctx = self.context()
        fix = WifiFix()
        for item in fix.files:
            self.write(item.path, ctx.system.data_text(item.data_name))
        self.assertEqual(fix.status(ctx).state, State.PARTIAL)
        fix.install(ctx)
        self.assertEqual([c for c in self.runner.calls if c[0] != "dpkg-query"], [
            ["apt-get", "install", "-y", "firmware-brcm80211"],
            ["update-initramfs", "-u"], ["nmcli", "general", "reload", "conf"]])
        self.write(fix.firmware)
        self.assertEqual(fix.status(ctx).state, State.DONE)
        self.runner.calls.clear()
        fix.remove(ctx)
        self.assertTrue(all(ctx.system.exists(item.path) for item in fix.files))
        self.assertFalse(self.runner.calls)

    def test_remove_dry_run_preserves_files(self):
        ctx = self.context()
        fix = WifiFix()
        self.write(fix.firmware)
        fix.install(ctx)
        ctx.system.dry_run = True
        before = self.snapshot()
        self.runner.calls.clear()
        fix.remove(ctx)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.runner.calls)


class SleepHookTests(unittest.TestCase):
    def test_hook_leaves_the_driver_alone_when_it_did_not_unload_it(self):
        import os
        import shutil
        import subprocess

        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")
        if Path("/sys/module/brcmfmac").exists() or Path("/run/parrot-mbp2017-wifi-unloaded").exists():
            self.skipTest("this machine uses brcmfmac")
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=repo) as tmp:
            log = Path(tmp) / "calls"
            stub = Path(tmp) / "modprobe"
            stub.write_text(f'#!/bin/sh\necho "$*" >>"{log}"\n')
            stub.chmod(0o755)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ.get("PATH", ""))
            script = str(repo / "mbp2017/data/parrot-mbp2017-wifi-sleep")
            for args in [["pre", "suspend"], ["post", "suspend"], []]:
                result = subprocess.run([bash, script, *args], env=env, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(log.exists())
