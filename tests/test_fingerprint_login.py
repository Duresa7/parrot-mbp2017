import io
from pathlib import Path
import tempfile
import unittest

from mbp2017.fixes.base import Context, FixError, Options, State
from mbp2017.hardware import probe
from mbp2017.system import Result, System
from mbp2017.ui import UI
from tests.helpers import FakeRunner, make_mac

from mbp2017.fixes.fingerprint_login import FingerprintLoginFix


class FingerprintLoginFixTests(unittest.TestCase):
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

    def ready(self):
        ctx = self.context()
        self.runner.scripts[("dpkg-query",)] = Result(0, "libpam-fprintd\t1\tii \n")
        self.write("/usr/share/pam-configs/fprintd", "fixture profile")
        return ctx

    def test_gates_and_dependency(self):
        fix = FingerprintLoginFix()
        self.assertFalse(fix.default)
        self.assertEqual(fix.requires, ("t1bridge",))
        ctx = self.context(t1=False)
        self.assertEqual(fix.status(ctx).state, State.NOT_NEEDED)
        self.assertEqual(fix.install(ctx), [])
        ctx = self.context()
        self.assertEqual(fix.status(ctx).state, State.BLOCKED)
        ctx.options.selected.add("t1bridge")
        self.assertEqual(fix.status(ctx).state, State.TODO)
        with self.assertRaisesRegex(FixError, "t1bridge"):
            fix.install(ctx)

    def test_lifecycle_and_commands(self):
        ctx = self.ready()
        fix = FingerprintLoginFix()
        auth = self.write("/etc/pam.d/common-auth", "auth required pam_unix.so\n")
        original = auth.read_text()
        self.runner.scripts[("fprintd-list",)] = Result(0, " - #0: right-index-finger\n")
        # Model pam-auth-update's file change while all commands stay intercepted.
        def runner(argv, opts):
            result = self.runner(argv, opts)
            if argv == ["pam-auth-update", "--enable", "fprintd"]:
                auth.write_text(original + "auth sufficient pam_fprintd.so\n")
            elif argv == ["pam-auth-update", "--disable", "fprintd"]:
                auth.write_text(original)
            return result
        ctx.system.runner = runner
        self.assertEqual(fix.status(ctx).state, State.TODO)
        fix.install(ctx)
        self.assertEqual(fix.status(ctx).state, State.DONE)
        fix.install(ctx)
        fix.remove(ctx)
        fix.remove(ctx)
        self.assertEqual(auth.read_text(), original)
        self.assertEqual(fix.status(ctx).state, State.TODO)
        commands = [c for c in self.runner.calls if c[0] != "dpkg-query"]
        self.assertEqual(commands, [["fprintd-list", "alice"], ["pam-auth-update", "--enable", "fprintd"],
                                   ["pam-auth-update", "--disable", "fprintd"]])
        index = self.runner.calls.index(["fprintd-list", "alice"])
        self.assertEqual(self.runner.options[index]["timeout"], 10)
        self.assertNotIn("No enrolled finger", self.output.getvalue())
        self.assertIn("root terminal", self.output.getvalue())

    def test_no_finger_and_timeout_warn(self):
        for result in [Result(0, "No fingers enrolled"), Result(124)]:
            ctx = self.ready()
            self.runner.scripts[("fprintd-list",)] = result
            FingerprintLoginFix().install(ctx)
            self.assertIn("sudo falls back to your password", self.output.getvalue())
            self.assertIn(["pam-auth-update", "--enable", "fprintd"], self.runner.calls)

    def test_thumb_is_enrolled(self):
        ctx = self.ready()
        self.runner.scripts[("fprintd-list",)] = Result(0, " - #0: left-thumb\n")
        FingerprintLoginFix().install(ctx)
        self.assertNotIn("No enrolled finger", self.output.getvalue())

    def test_confirmation_and_dry_run(self):
        ctx = self.ready()
        ctx.options.assume_yes = ctx.ui.assume_yes = False
        with self.assertRaises(FixError):
            FingerprintLoginFix().install(ctx)
        self.assertFalse(any(c[0] == "pam-auth-update" for c in self.runner.calls))
        ctx.options.assume_yes = True
        ctx.system.dry_run = True
        before = self.snapshot()
        FingerprintLoginFix().install(ctx)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(any(c[0] == "pam-auth-update" for c in self.runner.calls))

    def test_remove_dry_run_preserves_auth(self):
        ctx = self.ready()
        self.write("/etc/pam.d/common-auth", "auth sufficient pam_fprintd.so\n")
        ctx.system.dry_run = True
        before = self.snapshot()
        FingerprintLoginFix().remove(ctx)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.runner.calls)
