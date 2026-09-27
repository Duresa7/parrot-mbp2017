import io
from pathlib import Path
import tempfile
import unittest

from mbp2017.fixes.base import Context, FixError, Options, State
from mbp2017.hardware import probe
from mbp2017.system import Result, System
from mbp2017.ui import UI
from tests.helpers import FakeRunner, make_mac

from mbp2017.fixes.sleep import SleepFix


class SleepFixTests(unittest.TestCase):
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

    def test_gates_per_item(self):
        for amd, t1, count in [(False, False, 0), (True, False, 2), (False, "running", 1), (True, "recovery", 3)]:
            with self.subTest(amd=amd, t1=t1):
                ctx = self.context(amd=amd, t1=t1)
                fix = SleepFix()
                self.assertEqual(fix.status(ctx).state, State.TODO if count else State.NOT_NEEDED)
                fix.install(ctx)
                self.assertEqual(sum(ctx.system.exists(f.path) for f in fix.files), count)
                self.assertEqual(fix.status(ctx).state, State.DONE if count else State.NOT_NEEDED)

    def test_lifecycle_and_commands(self):
        ctx = self.context()
        fix = SleepFix()
        original = self.write(fix.files[0].path, "# original\n")
        notes = fix.install(ctx)
        self.assertIn("Thunderbolt", " ".join(notes))
        self.assertIn("Touch ID", " ".join(notes))
        self.assertEqual(fix.status(ctx).state, State.DONE)
        self.assertEqual(self.runner.calls, [["udevadm", "control", "--reload"]])
        fix.install(ctx)
        self.assertEqual(len(self.runner.calls), 1)
        fix.remove(ctx)
        self.assertEqual(self.runner.calls, [["udevadm", "control", "--reload"]] * 2)
        self.assertEqual(original.read_text(), "# original\n")
        self.assertEqual(fix.status(ctx).state, State.TODO)
        self.assertFalse(ctx.system.exists(fix.files[1].path))
        self.assertFalse(ctx.system.exists(fix.files[2].path))

    def test_kernel_command_line_and_dry_run(self):
        ctx = self.context()
        fix = SleepFix()
        self.write("/proc/cmdline", "quiet mem_sleep_default=s2idle splash")
        self.assertEqual(fix.status(ctx).state, State.PARTIAL)
        self.assertIn("kernel command line", fix.status(ctx).detail)
        ctx.system.dry_run = True
        before = self.snapshot()
        fix.install(ctx)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.runner.calls)
        ctx.system.dry_run = False
        fix.install(ctx)
        self.assertFalse(ctx.system.exists(fix.files[0].path))
        self.assertEqual(fix.status(ctx).state, State.DONE)
        fix.remove(ctx)
        self.assertEqual(fix.status(ctx).state, State.PARTIAL)

    def test_remove_after_hardware_disappears(self):
        ctx = self.context()
        fix = SleepFix()
        fix.install(ctx)
        ctx.hw.amd_gpu = False
        ctx.hw.t1_state = "absent"
        fix.remove(ctx)
        self.assertFalse(any(ctx.system.exists(f.path) for f in fix.files))

    def test_similar_kernel_argument_is_not_satisfied(self):
        ctx = self.context(amd=True, t1=False)
        self.write("/proc/cmdline", "other_mem_sleep_default=s2idle mem_sleep_default=s2idle-extra")
        self.assertEqual(SleepFix().status(ctx).state, State.TODO)

    def test_remove_dry_run_preserves_files(self):
        ctx = self.context()
        fix = SleepFix()
        fix.install(ctx)
        ctx.system.dry_run = True
        before = self.snapshot()
        self.runner.calls.clear()
        fix.remove(ctx)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.runner.calls)
