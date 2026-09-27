import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpers import KERNEL, FakeRunner, make_mac
from mbp2017.fixes.audio import AudioFix, CLONE_DIR, COMMIT, DKMS_NAME, LOG_PATH, REPO_URL
from mbp2017.fixes.base import Context, FixError, Options, State
from mbp2017.hardware import probe
from mbp2017.system import Result, System
from mbp2017.ui import UI


def dkms_line(kernel: str, status: str) -> str:
    return f"{DKMS_NAME}/0.1, {kernel}, x86_64: {status}\n"


class AudioTests(unittest.TestCase):
    def fixture(self, scripts=None, **overrides) -> Context:
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        system = System(make_mac(temp.name, **overrides), runner=FakeRunner(scripts or {}),
                        euid=0, env={})
        with patch.object(system, 'kernel_release', return_value=KERNEL):
            hw = probe(system)
        return Context(system, hw, UI(color=False, assume_yes=True, interactive=False, out=io.StringIO()),
                       Options())

    def test_gate_not_needed(self):
        ctx = self.fixture(cs8409=False)
        fix = AudioFix()
        self.assertEqual(fix.status(ctx).state, State.NOT_NEEDED)
        self.assertEqual(fix.install(ctx), [])
        self.assertEqual(fix.health(ctx), [])
        self.assertEqual(ctx.system.runner.calls, [])

    def test_status_done_for_running_kernel(self):
        ctx = self.fixture(scripts={('dkms', 'status', DKMS_NAME):
                                    Result(0, dkms_line(KERNEL, 'installed (Original modules exist)'))})
        self.assertEqual(AudioFix().status(ctx).state, State.DONE)

    def test_status_partial_for_other_kernel(self):
        ctx = self.fixture(scripts={('dkms', 'status', DKMS_NAME):
                                    Result(0, dkms_line('6.1.0-other', 'installed'))})
        status = AudioFix().status(ctx)
        self.assertEqual(status.state, State.PARTIAL)
        self.assertIn('6.1.0-other', status.detail)

    def test_status_partial_when_cloned_without_dkms(self):
        ctx = self.fixture(scripts={('dkms', 'status', DKMS_NAME): Result(1, '')})
        ctx.system.makedirs(CLONE_DIR, 0o755)
        self.assertEqual(AudioFix().status(ctx).state, State.PARTIAL)

    def test_status_todo(self):
        ctx = self.fixture(scripts={('dkms', 'status', DKMS_NAME): Result(1, '')})
        self.assertEqual(AudioFix().status(ctx).state, State.TODO)

    def test_install_sequence_fresh_clone(self):
        scripts = {
            ('dpkg-query',): Result(0, 'git\t1\tii \npatch\t1\tii \n'),
            ('dkms', 'status', DKMS_NAME): [Result(1, ''), Result(0, dkms_line(KERNEL, 'installed'))],
            ('git', 'clone', REPO_URL, CLONE_DIR): Result(0),
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'checkout', '--detach', COMMIT): Result(0),
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'rev-parse', 'HEAD'): Result(0, COMMIT + '\n'),
            ('./install.cirrus.driver.sh', '-i'): Result(0, 'built\n'),
        }
        ctx = self.fixture(scripts=scripts)
        notes = AudioFix().install(ctx)
        calls = ctx.system.runner.calls
        self.assertIn(['git', 'clone', REPO_URL, CLONE_DIR], calls)
        self.assertIn(['git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'checkout', '--detach', COMMIT], calls)
        self.assertIn(['git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'rev-parse', 'HEAD'], calls)
        self.assertIn(['./install.cirrus.driver.sh', '-i'], calls)
        apt_call = next(call for call in calls if call[:2] == ['apt-get', 'install'])
        self.assertIn('build-essential', apt_call)
        self.assertIn(f'linux-headers-{KERNEL}', apt_call)
        self.assertNotIn('git', apt_call)
        self.assertTrue(any('Reboot' in note for note in notes))
        self.assertEqual(ctx.system.runner.options[
            calls.index(['./install.cirrus.driver.sh', '-i'])]['cwd'], str(ctx.system.path(CLONE_DIR)))

    def test_install_existing_clone_fetches_instead_of_cloning(self):
        ctx = self.fixture(scripts={
            ('dpkg-query',): Result(0, ''),
            ('dkms', 'status', DKMS_NAME): [Result(1, ''), Result(0, dkms_line(KERNEL, 'installed'))],
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'fetch'): Result(0),
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'checkout', '--detach', COMMIT): Result(0),
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'rev-parse', 'HEAD'): Result(0, COMMIT + '\n'),
            ('./install.cirrus.driver.sh', '-i'): Result(0),
        })
        ctx.system.makedirs(CLONE_DIR, 0o755)
        AudioFix().install(ctx)
        calls = ctx.system.runner.calls
        self.assertIn(['git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'fetch'], calls)
        self.assertNotIn(['git', 'clone', REPO_URL, CLONE_DIR], calls)

    def test_install_fails_when_checkout_does_not_match_pin(self):
        ctx = self.fixture(scripts={
            ('dpkg-query',): Result(0, ''),
            ('dkms', 'status', DKMS_NAME): Result(1, ''),
            ('git', 'clone', REPO_URL, CLONE_DIR): Result(0),
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'checkout', '--detach', COMMIT): Result(0),
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'rev-parse', 'HEAD'): Result(0, 'deadbeef\n'),
        })
        with self.assertRaises(FixError):
            AudioFix().install(ctx)

    def test_install_fails_and_points_at_log_when_still_not_installed(self):
        ctx = self.fixture(scripts={
            ('dpkg-query',): Result(0, ''),
            ('dkms', 'status', DKMS_NAME): [Result(1, ''), Result(1, '')],
            ('git', 'clone', REPO_URL, CLONE_DIR): Result(0),
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'checkout', '--detach', COMMIT): Result(0),
            ('git', '-c', f'safe.directory={CLONE_DIR}', '-C', CLONE_DIR, 'rev-parse', 'HEAD'): Result(0, COMMIT + '\n'),
            ('./install.cirrus.driver.sh', '-i'): Result(0, ''),
        })
        with self.assertRaisesRegex(FixError, LOG_PATH.replace('.', r'\.')):
            AudioFix().install(ctx)

    def test_install_skips_when_already_done(self):
        ctx = self.fixture(scripts={('dkms', 'status', DKMS_NAME):
                                    Result(0, dkms_line(KERNEL, 'installed'))})
        self.assertEqual(AudioFix().install(ctx), [])
        self.assertEqual(len(ctx.system.runner.calls), 1)

    def test_remove_sequence(self):
        ctx = self.fixture(scripts={('./install.cirrus.driver.sh', '-r'): Result(0, 'removed\n')})
        ctx.system.makedirs(CLONE_DIR, 0o755)
        AudioFix().remove(ctx)
        calls = ctx.system.runner.calls
        self.assertIn(['./install.cirrus.driver.sh', '-r'], calls)
        self.assertIn(['rm', '-rf', CLONE_DIR], calls)

    def test_remove_does_nothing_without_clone(self):
        ctx = self.fixture()
        self.assertEqual(AudioFix().remove(ctx), [])
        self.assertEqual(ctx.system.runner.calls, [])

    def test_health_warns_per_kernel(self):
        ctx = self.fixture(kernels=[KERNEL, 'old-kernel'], headers={KERNEL: True, 'old-kernel': True},
                           scripts={('dkms', 'status', DKMS_NAME): Result(0, dkms_line(KERNEL, 'installed'))})
        checks = AudioFix().health(ctx)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].level, 'warn')
        self.assertEqual(checks[0].message,
                         'The speaker driver is not installed for kernel old-kernel. '
                         'With internet connected, run: sudo dkms autoinstall -k old-kernel')

    def test_health_empty_without_hardware(self):
        ctx = self.fixture(cs8409=False)
        self.assertEqual(AudioFix().health(ctx), [])
