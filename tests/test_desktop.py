import io
from pathlib import Path
import tempfile
import unittest

from mbp2017.fixes.base import Context, FixError, Options, State
from mbp2017.hardware import probe
from mbp2017.system import Result, System
from mbp2017.ui import UI
from tests.helpers import FakeRunner, make_mac

from mbp2017.fixes.desktop import DesktopFix


class DesktopFixTests(unittest.TestCase):
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

    def test_gates_and_dependency(self):
        ctx = self.context(t1=False)
        fix = DesktopFix()
        self.assertEqual(fix.status(ctx).state, State.NOT_NEEDED)
        ctx = self.context()
        self.assertEqual(fix.status(ctx).state, State.BLOCKED)
        self.assertIn("t1bridge", fix.status(ctx).detail)
        with self.assertRaises(FixError):
            fix.install(ctx)
        ctx.options.selected.add("t1bridge")
        self.assertEqual(fix.status(ctx).state, State.TODO)
        ctx.options.selected.clear()
        self.runner.scripts[("dpkg-query",)] = Result(0, "t1bridge\t0.1.12\tii \n")
        self.assertEqual(fix.status(ctx).state, State.TODO)

    def test_lifecycle_and_commands(self):
        ctx = self.context()
        ctx.options.selected.add("t1bridge")
        fix = DesktopFix()
        original = self.write(fix.files[1].path, "# original\n")
        self.runner.scripts[("systemctl",)] = Result(1)
        notes = fix.install(ctx)
        self.assertIn("Log out", " ".join(notes))
        self.assertEqual(fix.status(ctx).state, State.DONE)
        self.assertEqual(ctx.system.path(fix.files[0].path).stat().st_mode & 0o777, 0o755)
        self.assertIn("pam_fprintd.so", ctx.system.read_text(fix.files[3].path))
        self.assertIn("Wants=t1-touchbar.service", ctx.system.read_text(fix.files[2].path))
        commands = [["systemctl", "--user", "-M", "alice@", "daemon-reload"],
                    ["systemctl", "--user", "-M", "alice@", "try-restart", "t1-touchbar.service"]]
        self.assertEqual(self.runner.calls, commands)
        fix.install(ctx)
        self.assertEqual(self.runner.calls, commands)
        fix.remove(ctx)
        self.assertEqual(self.runner.calls, commands * 2)
        self.assertEqual(original.read_text(), "# original\n")
        self.assertFalse(ctx.system.exists(fix.files[0].path))
        self.assertFalse(ctx.system.exists(fix.files[3].path))
        self.assertEqual(fix.status(ctx).state, State.TODO)

    def test_lock_screen_service_needs_plasma(self):
        ctx = self.context(plasma=False)
        ctx.options.selected.add("t1bridge")
        fix = DesktopFix()
        fix.install(ctx)
        self.assertEqual(fix.status(ctx).state, State.DONE)
        self.assertTrue(ctx.system.exists(fix.files[1].path))
        self.assertFalse(ctx.system.exists(fix.files[3].path))

    def test_lock_screen_service_never_prompts_after_a_match(self):
        ctx = self.context()
        auth = [line.split() for line in ctx.system.data_text("parrot-mbp2017-kde-fingerprint").splitlines()
                if line.startswith("auth")]
        modules = [next(word for word in line if word.endswith(".so")) for line in auth]
        # A match skips pam_deny and ends on pam_permit; nothing may prompt for a password.
        self.assertEqual(modules[-3:], ["pam_fprintd.so", "pam_deny.so", "pam_permit.so"])
        self.assertEqual(" ".join(auth[-3][1:3]), "[success=1 default=ignore]")
        self.assertNotIn("pam_kwallet5.so", modules)

    def test_dry_run_and_missing_user(self):
        ctx = self.context()
        ctx.options.selected.add("t1bridge")
        ctx.system.dry_run = True
        before = self.snapshot()
        DesktopFix().install(ctx)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.runner.calls)
        ctx.system.dry_run = False
        ctx.system.env.clear()
        self.assertIn("Log out", " ".join(DesktopFix().install(ctx)))
        self.assertFalse(self.runner.calls)

    def test_remove_dry_run_preserves_files(self):
        ctx = self.context()
        ctx.options.selected.add("t1bridge")
        fix = DesktopFix()
        fix.install(ctx)
        ctx.system.dry_run = True
        before = self.snapshot()
        self.runner.calls.clear()
        fix.remove(ctx)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.runner.calls)


class ProviderScriptTests(unittest.TestCase):
    def test_provider_contract(self):
        import os
        import shutil
        import subprocess

        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=repo) as tmp:
            for name, body in {
                "wpctl": 'printf "%s\\n" "$TEST_VOLUME"',
                "busctl": 'printf "%s\\n" "$TEST_PLAYER"',
            }.items():
                stub = Path(tmp) / name
                stub.write_text("#!/bin/sh\n" + body + "\n")
                stub.chmod(0o755)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ.get("PATH", ""),
                       TEST_VOLUME="Volume: 0.50", TEST_PLAYER="")
            script = str(repo / "mbp2017/data/desktop-provider")
            for volume, player, expected in [
                ("Volume: 0.50", "", "T1BRIDGE-DESKTOP 1 13 50 0"),
                ("Volume: 0.50", "org.mpris.MediaPlayer2.x", "T1BRIDGE-DESKTOP 1 15 50 0"),
                ("Volume: 0.50 [MUTED]", "", "T1BRIDGE-DESKTOP 1 13 50 1"),
            ]:
                result = subprocess.run([bash, script, "v1", "status"], env=env | {
                    "TEST_VOLUME": volume, "TEST_PLAYER": player}, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
            for args in [["v1", "set-volume", "101"], ["v1", "unknown"], [], ["status"]]:
                result = subprocess.run([bash, script, *args], env=env, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 2)
