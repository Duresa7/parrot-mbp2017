import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from mbp2017.fixes.base import Context, FixError, Options, State
from mbp2017.hardware import probe
from mbp2017.system import Result, System
from mbp2017.ui import UI
from tests.helpers import FakeRunner, make_mac

from mbp2017.fixes.t1_wake import T1WakeFix


class T1WakeFixTests(unittest.TestCase):
    def context(self, **overrides):
        tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(tmp.cleanup)
        self.root = Path(make_mac(tmp.name, **overrides))
        self.runner = FakeRunner()
        system = System(str(self.root), runner=self.runner, euid=0, env={}, out=io.StringIO())
        ui = UI(color=False, assume_yes=True, interactive=False, out=io.StringIO())
        return Context(system, probe(system), ui, Options(assume_yes=True))

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_gates_and_dependency(self):
        fix = T1WakeFix()
        self.assertEqual(fix.status(self.context(t1=None)).state, State.NOT_NEEDED)
        ctx = self.context()
        self.assertEqual(fix.status(ctx).state, State.BLOCKED)
        with self.assertRaises(FixError):
            fix.install(ctx)
        self.runner.scripts[("dpkg-query",)] = Result(0, "t1bridge\t0.1.12\tii \n")
        self.assertEqual(fix.status(ctx).state, State.TODO)

    def test_lifecycle(self):
        ctx = self.context()
        ctx.options.selected.add("t1bridge")
        fix = T1WakeFix()
        hook = fix.files[0].path
        fix.install(ctx)
        self.assertEqual(fix.status(ctx).state, State.DONE)
        self.assertEqual(ctx.system.path(hook).stat().st_mode & 0o777, 0o755)
        self.assertFalse([c for c in self.runner.calls if c[0] != "dpkg-query"])
        fix.remove(ctx)
        self.assertFalse(ctx.system.exists(hook))
        self.assertEqual(fix.status(ctx).state, State.TODO)

    def test_dry_run(self):
        ctx = self.context()
        ctx.options.selected.add("t1bridge")
        ctx.system.dry_run = True
        before = self.snapshot()
        T1WakeFix().install(ctx)
        self.assertEqual(before, self.snapshot())


class HookScriptTests(unittest.TestCase):
    def run_hook(self, *args, state=""):
        python = shutil.which("python3")
        if not python:
            self.skipTest("python3 is unavailable")
        for device in Path("/sys/bus/usb/devices").glob("*"):
            ids = [(device / name).read_text().strip() for name in ("idVendor", "idProduct")
                   if (device / name).exists()]
            if ids == ["05ac", "8600"]:
                self.skipTest("this machine has a T1")
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=repo) as tmp:
            log = Path(tmp) / "calls"
            stub = Path(tmp) / "systemctl"
            stub.write_text('#!/bin/sh\n'
                            'if [ "$1" = show ]; then printf "%b" "$TEST_STATE"; exit 0; fi\n'
                            f'echo "$*" >>"{log}"\n')
            stub.chmod(0o755)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ.get("PATH", ""), TEST_STATE=state)
            result = subprocess.run([python, str(repo / "mbp2017/data/parrot-mbp2017-t1-wake"), *args],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return log.read_text().splitlines() if log.exists() else []

    def test_restarts_the_relay_only_after_it_failed(self):
        loaded = "LoadState=loaded\\n"
        for state, restarted in [
            (loaded + "ActiveState=activating\\nSubState=auto-restart\\n", True),
            (loaded + "ActiveState=failed\\nSubState=failed\\n", True),
            (loaded + "ActiveState=active\\nSubState=running\\n", False),
            (loaded + "ActiveState=inactive\\nSubState=dead\\n", False),
            ("LoadState=not-found\\nActiveState=inactive\\nSubState=dead\\n", False),
        ]:
            with self.subTest(state=state):
                calls = self.run_hook("post", "suspend", state=state)
                self.assertEqual(calls, ["restart t1bridge-keybag.service"] if restarted else [])

    def test_does_nothing_before_sleep(self):
        state = "LoadState=loaded\\nActiveState=failed\\nSubState=failed\\n"
        self.assertEqual(self.run_hook("pre", "suspend", state=state), [])
        self.assertEqual(self.run_hook(state=state), [])
