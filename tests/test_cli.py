import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpers import FakeRunner, make_mac
from mbp2017.cli import main
from mbp2017.fixes.base import Fix, FixError, Health, State, Status
from mbp2017.system import System


class DummyFix(Fix):
    id = 'dummy'
    title = 'Example fix'
    summary = 'An example.'
    why = 'Used to test scheduling.'

    def status(self, ctx):
        return Status(State.DONE if ctx.system.notes(self.id).get('done') else State.TODO)

    def install(self, ctx):
        ctx.system.note(self.id, 'done', True)
        return ['Example next step.']

    def remove(self, ctx):
        ctx.system.clear_notes(self.id)
        return []


class CLITests(unittest.TestCase):
    def fixture(self, *, euid=0, **overrides):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        root = make_mac(temp.name, **overrides)
        return System(root, euid=euid, runner=FakeRunner(), env={'SUDO_USER': 'alice'})

    def call(self, system, *args):
        output = io.StringIO()
        result = main(list(args), system=system, stdin=io.StringIO(), stdout=output)
        return result, output.getvalue()

    def snapshot(self, system):
        return {str(path.relative_to(system.root)): path.read_bytes()
                for path in system.root.rglob('*') if path.is_file()}

    def test_detect_and_status_json(self):
        system = self.fixture(euid=1000)
        for args in [('detect', '--json'), ('--json', 'status')]:
            result, output = self.call(system, *args)
            self.assertEqual(result, 0, output)
            payload = json.loads(output)
            hw = payload.get('hardware', payload)
            self.assertEqual(hw['model'], 'MacBookPro14,3')
            if 'fixes' in payload:
                self.assertIn('input', [fix['id'] for fix in payload['fixes']])
                self.assertEqual(payload['health'][0]['level'], 'ok')

    def test_install_requires_root(self):
        result, output = self.call(self.fixture(euid=1000), 'install', 'input')
        self.assertEqual(result, 1)
        self.assertIn('sudo', output)

    def test_dry_run_install_writes_nothing(self):
        system = self.fixture(euid=1000)
        before = self.snapshot(system)
        result, output = self.call(system, '--dry-run', 'install', 'input')
        self.assertEqual(result, 0, output)
        self.assertIn('would install', output)
        self.assertIn('would run: udevadm', output)
        self.assertEqual(before, self.snapshot(system))
        self.assertEqual(system.runner.calls, [])

    def test_unsupported_hardware(self):
        system = self.fixture(vendor='Other')
        result, output = self.call(system, 'install', 'input')
        self.assertEqual(result, 3)
        self.assertIn('--force', output)
        self.assertEqual(self.call(system, 'detect')[0], 0)
        self.assertEqual(self.call(system, '--force', '--dry-run', 'install', 'input')[0], 0)

    def test_setup_yes_and_default_command(self):
        # --only keeps the run to one fix; every other fix has its own tests.
        for args in [('setup', '--yes', '--only', 'input'), ('--yes', '--only', 'input')]:
            system = self.fixture()
            result, output = self.call(system, *args)
            self.assertEqual(result, 0, output)
            self.assertIn('parrot-mbp2017 0.1.0', output)
            self.assertIn('Changed: input', output)
            self.assertIn('log out and back in', output)
            self.assertTrue(system.exists('/etc/libinput/local-overrides.quirks'))
            mutating = [call for call in system.runner.calls if call[0] == 'udevadm']
            self.assertEqual(mutating, [['udevadm', 'control', '--reload'],
                                        ['udevadm', 'trigger', '--action=change', '--subsystem-match=input']])

    def test_repeat_install_skips_and_remove(self):
        system = self.fixture()
        self.assertEqual(self.call(system, 'install', 'input')[0], 0)
        before = self.snapshot(system)
        result, output = self.call(system, 'install', 'input')
        self.assertEqual(result, 0)
        self.assertIn('Changed: none', output)
        self.assertEqual(self.snapshot(system), before)
        self.assertEqual(self.call(system, 'remove', 'input')[0], 0)
        self.assertFalse(system.exists('/etc/libinput/local-overrides.quirks'))

    def test_only_skip_unknown_and_usage(self):
        system = self.fixture()
        result, output = self.call(system, 'setup', '--yes', '--only', 'input', '--skip', 'input')
        self.assertEqual(result, 0)
        self.assertIn('Nothing selected', output)
        self.assertFalse(system.exists(System.MANIFEST))
        self.assertEqual(self.call(system, 'install', 'unknown')[0], 2)
        self.assertEqual(self.call(system, 'status', '--only', 'input')[0], 2)
        self.assertEqual(self.call(system, 'list', '--json')[0], 2)
        self.assertEqual(self.call(system, 'install')[0], 2)
        self.assertEqual(self.call(system, 'restore-t1')[0], 2)
        self.assertEqual(self.call(system, '--version'), (0, 'parrot-mbp2017 0.1.0\n'))
        self.assertEqual(self.call(system, '--help')[0], 0)
        self.assertNotIn('--root', self.call(system, '--help')[1])

    def test_health_missing_headers(self):
        result, output = self.call(self.fixture(headers=False), 'status', '--json')
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)['health'][0]['level'], 'warn')

    def test_non_parrot_requires_confirmation(self):
        system = self.fixture(distro_id='debian', distro_codename='trixie')
        result, output = self.call(system, 'install', 'input')
        self.assertEqual(result, 0)
        self.assertIn('No changes made', output)
        self.assertFalse(system.exists(System.MANIFEST))
        self.assertEqual(self.call(system, 'install', 'input', '--yes')[0], 0)
        self.assertTrue(system.exists(System.MANIFEST))

    def test_failures_continue_and_dependencies_block(self):
        class Failing(DummyFix):
            id = 'first'

            def install(self, ctx):
                raise FixError('Please check the example.')

        class Dependent(DummyFix):
            id = 'dependent'
            requires = ('first',)

        class Last(DummyFix):
            id = 'last'
            after = 'reboot'

        system = self.fixture()
        with patch('mbp2017.cli.all_fixes', return_value=[Failing(), Dependent(), Last()]):
            result, output = self.call(system, 'setup', '--yes')
        self.assertEqual(result, 1)
        self.assertIn('Failures: first, dependent', output)
        self.assertIn('Changed: last', output)
        self.assertIn('Reboot now', output)
        self.assertEqual(system.notes('dependent'), {})
        self.assertTrue(system.notes('last')['done'])

    def test_recommended_dependencies_and_opt_in(self):
        class Dependent(DummyFix):
            id = 'dependent'
            requires = ('dummy',)

        class Optional(DummyFix):
            id = 'optional'
            default = False

        system = self.fixture()
        with patch('mbp2017.cli.all_fixes', return_value=[DummyFix(), Dependent(), Optional()]):
            result, output = self.call(system, 'setup', '--yes')
        self.assertEqual(result, 0, output)
        self.assertIn('Changed: dummy, dependent', output)
        self.assertFalse(system.notes('optional'))
        self.assertTrue(system.notes('dependent'))

    def test_restore_delegation(self):
        system = self.fixture()
        with patch('mbp2017.cli.all_fixes', return_value=[]):
            result, output = self.call(system, 'restore-t1', '--online')
        self.assertEqual(result, 1)
        self.assertIn('unavailable', output)
        received = []

        class Backup(DummyFix):
            id = 't1-backup'

            def restore_t1(self, ctx, source):
                received.append(source)
                return ['Restore complete.']

        with patch('mbp2017.cli.all_fixes', return_value=[Backup()]):
            self.assertEqual(self.call(system, 'restore-t1', '--online')[0], 0)
            self.assertEqual(self.call(system, 'restore-t1', '--from', '/backup')[0], 0)
        self.assertEqual(received, ['online', '/backup'])

    def test_hidden_root_constructs_system(self):
        system = self.fixture()
        output = io.StringIO()
        result = main(['--root', str(system.root), 'detect'], stdout=output, stdin=io.StringIO())
        self.assertEqual(result, 0)
        self.assertIn('15-inch, 2017', output.getvalue())
