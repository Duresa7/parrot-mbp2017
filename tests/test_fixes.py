import io
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from helpers import FakeRunner, make_mac
from mbp2017.fixes import ORDER, all_fixes, get_fix
from mbp2017.fixes.base import Context, Fix, Options, State
from mbp2017.fixes.input import InputFix
from mbp2017.hardware import probe
from mbp2017.system import System
from mbp2017.ui import UI


class FixTests(unittest.TestCase):
    def fixture(self, **overrides):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        system = System(make_mac(temp.name, **overrides), runner=FakeRunner(), euid=0, env={})
        return Context(system, probe(system), UI(color=False, assume_yes=True, interactive=False, out=io.StringIO()), Options())

    def test_discovery_and_order(self):
        fixes = all_fixes()
        self.assertIn('input', [fix.id for fix in fixes])
        self.assertIsInstance(get_fix('input'), InputFix)
        self.assertIsNone(get_fix('missing'))
        self.assertEqual([fix.id for fix in fixes], sorted([fix.id for fix in fixes],
                         key=lambda name: (ORDER.index(name) if name in ORDER else len(ORDER), name)))

    def test_discovery_new_modules_without_registry_edits(self):
        modules = {}
        for name in ('desktop', 'zzz', 'audio', 'wifi'):
            module = types.ModuleType('mbp2017.fixes.' + name)
            module.Implementation = type('Implementation', (Fix,), {'id': name, '__module__': module.__name__})
            modules[module.__name__] = module
        infos = [types.SimpleNamespace(name=name.rsplit('.', 1)[-1]) for name in modules]
        with patch('mbp2017.fixes.pkgutil.iter_modules', return_value=infos), \
                patch('mbp2017.fixes.importlib.import_module', side_effect=modules.__getitem__):
            self.assertEqual([fix.id for fix in all_fixes()], ['wifi', 'audio', 'desktop', 'zzz'])

    def test_input_transitions_and_restore(self):
        ctx = self.fixture()
        path = '/etc/libinput/local-overrides.quirks'
        original = '# custom\n[Existing]\nMatchName=Other\n'
        target = ctx.system.path(path)
        target.parent.mkdir(parents=True)
        target.write_text(original)
        fix = InputFix()
        self.assertEqual(fix.status(ctx).state, State.TODO)
        rule = fix.files[0]
        ctx.system.install_file(fix.id, rule.path, ctx.system.data_text(rule.data_name))
        self.assertEqual(fix.status(ctx).state, State.PARTIAL)
        fix.install(ctx)
        self.assertEqual(fix.status(ctx).state, State.DONE)
        self.assertEqual(ctx.system.runner.calls, [['udevadm', 'control', '--reload'],
                         ['udevadm', 'trigger', '--action=change', '--subsystem-match=input']])
        fix.install(ctx)
        self.assertEqual(len(ctx.system.runner.calls), 2)
        fix.remove(ctx)
        self.assertEqual(target.read_text(), original)
        self.assertFalse(ctx.system.exists(rule.path))
        self.assertEqual(fix.status(ctx).state, State.TODO)
        self.assertEqual(ctx.system.runner.calls[-1], ['udevadm', 'control', '--reload'])

    def test_input_gates(self):
        ctx = self.fixture(t1=None, spi_keyboard=False)
        fix = InputFix()
        self.assertEqual(fix.status(ctx).state, State.NOT_NEEDED)
        fix.install(ctx)
        self.assertFalse(ctx.system.exists(System.MANIFEST))
        for options, expected in [({'t1': None}, 'block'), ({'spi_keyboard': False}, 'file')]:
            ctx = self.fixture(**options)
            fix.install(ctx)
            self.assertEqual(fix.status(ctx).state, State.DONE)
            self.assertEqual(len(ctx.system.owned_paths(fix.id)), 1)
            path = fix.blocks[0].path if expected == 'block' else fix.files[0].path
            self.assertTrue(ctx.system.exists(path))

    def test_remove_after_hardware_disappears(self):
        ctx = self.fixture()
        fix = InputFix()
        fix.install(ctx)
        ctx.hw.spi_keyboard = False
        ctx.hw.t1_state = None
        fix.remove(ctx)
        self.assertEqual(ctx.system.owned_paths('input'), [])
        self.assertFalse(ctx.system.exists(fix.blocks[0].path))
