from dataclasses import replace
import io
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from helpers import FakeRunner, make_mac
import mbp2017.fixes.t1_backup as t1_backup_module
from mbp2017.fixes.base import Context, FixError, Options, State
from mbp2017.fixes.t1_backup import T1BackupFix
from mbp2017.hardware import probe
from mbp2017.system import Result, System
from mbp2017.ui import UI


class T1BackupTests(unittest.TestCase):
    def fixture(self, *, euid=0, scripts=None, **overrides) -> Context:
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        system = System(make_mac(temp.name, **overrides), runner=FakeRunner(scripts or {}),
                        euid=euid, env={'SUDO_USER': 'alice'})
        return Context(system, probe(system),
                       UI(color=False, assume_yes=True, interactive=False, out=io.StringIO()), Options())

    def backup_dir(self, ctx: Context) -> str:
        return f"{ctx.system.invoking_user().home}/parrot-mbp2017-t1-backup"

    # -- t1-backup status and install --------------------------------

    def test_not_needed_without_t1(self):
        ctx = self.fixture(t1=None)
        self.assertEqual(T1BackupFix().status(ctx).state, State.NOT_NEEDED)

    def test_not_needed_without_t1_data(self):
        ctx = self.fixture(t1_data=False)
        self.assertEqual(T1BackupFix().status(ctx).state, State.NOT_NEEDED)

    def test_status_unknown_prompts_sudo(self):
        ctx = self.fixture(euid=1000, t1_data=False)
        self.assertIsNone(ctx.hw.t1_data)
        status = T1BackupFix().status(ctx)
        self.assertEqual(status.state, State.TODO)
        self.assertIn('sudo', status.detail)

    def test_install_requires_root_to_read_t1_data(self):
        ctx = self.fixture(euid=1000, t1_data=False)
        with self.assertRaises(FixError):
            T1BackupFix().install(ctx)

    def test_install_creates_archive_and_checksums_with_correct_modes(self):
        ctx = self.fixture()
        self.assertEqual(T1BackupFix().status(ctx).state, State.TODO)
        with patch.object(t1_backup_module, '_today', return_value='2026-01-02'):
            notes = T1BackupFix().install(ctx)
        backup_dir = self.backup_dir(ctx)
        self.assertEqual(ctx.system.path(backup_dir).stat().st_mode & 0o777, 0o700)
        archive = f"{backup_dir}/EFI-APPLE-2026-01-02.tar"
        sums = f"{backup_dir}/SHA256SUMS"
        self.assertTrue(ctx.system.exists(archive))
        self.assertEqual(ctx.system.path(archive).stat().st_mode & 0o777, 0o600)
        self.assertEqual(ctx.system.path(sums).stat().st_mode & 0o777, 0o600)
        text = ctx.system.read_text(sums)
        self.assertIn('EFI/APPLE/EMBEDDEDOS/combined.memboot', text)
        self.assertIn('EFI/APPLE/EMBEDDEDOS/version.plist', text)
        self.assertIn('EFI/APPLE/EMBEDDEDOS/FDRData/fixture', text)
        self.assertTrue(any('Keep a copy' in note for note in notes))
        self.assertEqual(T1BackupFix().status(ctx).state, State.DONE)

    def test_status_changes_when_esp_file_changes(self):
        ctx = self.fixture()
        with patch.object(t1_backup_module, '_today', return_value='2026-01-02'):
            T1BackupFix().install(ctx)
        self.assertEqual(T1BackupFix().status(ctx).state, State.DONE)
        changed = ctx.system.path(f"{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS/combined.memboot")
        changed.write_text('different firmware\n')
        self.assertEqual(T1BackupFix().status(ctx).state, State.TODO)

    def test_backup_dir_option_overrides_default(self):
        ctx = self.fixture()
        ctx.options.backup_dir = '/custom-backup'
        with patch.object(t1_backup_module, '_today', return_value='2026-01-02'):
            T1BackupFix().install(ctx)
        self.assertTrue(ctx.system.exists('/custom-backup/SHA256SUMS'))
        self.assertFalse(ctx.system.exists(self.backup_dir(ctx)))

    def test_remove_does_not_delete_backup(self):
        ctx = self.fixture()
        with patch.object(t1_backup_module, '_today', return_value='2026-01-02'):
            T1BackupFix().install(ctx)
        backup_dir = self.backup_dir(ctx)
        notes = T1BackupFix().remove(ctx)
        self.assertTrue(ctx.system.exists(backup_dir))
        self.assertIn(backup_dir, notes[0])

    # -- restore-t1 --from ---------------------------------------------

    def test_restore_from_refuses_when_embeddedos_exists(self):
        ctx = self.fixture()
        with self.assertRaises(FixError):
            T1BackupFix().restore_t1(ctx, '/anything')

    def test_restore_from_extracted_backup(self):
        ctx = self.fixture(t1_data=False)
        source = '/mnt/extracted'
        embedded = ctx.system.path(f'{source}/EFI/APPLE/EMBEDDEDOS')
        (embedded / 'FDRData').mkdir(parents=True)
        (embedded / 'FDRData' / 'fixture').write_text('data\n')
        (embedded / 'combined.memboot').write_text('firmware\n')
        (embedded / 'version.plist').write_text('version\n')
        notes = T1BackupFix().restore_t1(ctx, source)
        self.assertTrue(ctx.system.exists(f'{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS/FDRData/fixture'))
        self.assertTrue(ctx.system.exists(f'{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS/combined.memboot'))
        self.assertTrue(any('05ac:8600' in note for note in notes))

    def test_restore_from_tar_backup(self):
        ctx = self.fixture()
        with patch.object(t1_backup_module, '_today', return_value='2026-01-03'):
            T1BackupFix().install(ctx)
        backup_dir = self.backup_dir(ctx)
        shutil.rmtree(ctx.system.path(f'{ctx.hw.esp}/EFI/APPLE'))
        T1BackupFix().restore_t1(ctx, backup_dir)
        self.assertTrue(ctx.system.exists(f'{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS/combined.memboot'))
        self.assertTrue(ctx.system.exists(f'{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS/FDRData/fixture'))
        self.assertEqual(
            ctx.system.read_text(f'{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS/combined.memboot'),
            'fixture firmware\n')

    def test_restore_from_rejects_path_traversal_in_tar(self):
        ctx = self.fixture(t1_data=False)
        source = '/mnt/bad-backup'
        host_source = ctx.system.path(source)
        host_source.mkdir(parents=True)
        with tarfile.open(host_source / 'EFI-APPLE-2026-01-01.tar', 'w') as tar:
            data = b'x'
            info = tarfile.TarInfo(name='../evil')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with self.assertRaises(FixError):
            T1BackupFix().restore_t1(ctx, source)

    # -- restore-t1 --online ---------------------------------------------

    def test_online_refuses_when_not_recovery(self):
        ctx = self.fixture()
        with self.assertRaises(FixError):
            T1BackupFix().restore_t1(ctx, 'online')

    def test_online_refuses_without_charger(self):
        ctx = self.fixture(t1='recovery', t1_data=False)
        with self.assertRaises(FixError):
            T1BackupFix().restore_t1(ctx, 'online')

    def test_online_refuses_with_legacy_driver(self):
        ctx = self.fixture(t1='recovery', t1_data=False)
        charger = ctx.system.path('/sys/class/power_supply/ADP1/online')
        charger.parent.mkdir(parents=True)
        charger.write_text('1\n')
        legacy = ctx.system.path('/etc/modprobe.d/apple-touchbar.conf')
        legacy.parent.mkdir(parents=True)
        legacy.write_text('blacklist apple_ib\n')
        with self.assertRaises(FixError):
            T1BackupFix().restore_t1(ctx, 'online')

    def test_online_confirmation_declined_makes_no_changes(self):
        ctx = self.fixture(t1='recovery', t1_data=False)
        charger = ctx.system.path('/sys/class/power_supply/ADP1/online')
        charger.parent.mkdir(parents=True)
        charger.write_text('1\n')
        ctx.ui.assume_yes = False
        notes = T1BackupFix().restore_t1(ctx, 'online')
        self.assertEqual(notes, ['No changes made.'])
        self.assertEqual(ctx.system.runner.calls, [])

    def test_online_restore_happy_path(self):
        ctx = self.fixture(t1='recovery', t1_data=False)
        charger = ctx.system.path('/sys/class/power_supply/ADP1/online')
        charger.parent.mkdir(parents=True)
        charger.write_text('1\n')
        revive_bin = f"{t1_backup_module.T1_REVIVE_DIR}/bin/t1-revive"
        scripts = {
            ('dpkg-query',): Result(0, ''),
            ('apt-get', 'install'): Result(0),
            ('modprobe', 'acpi_call'): Result(0),
            ('systemctl', 'stop', 'usbmuxd'): Result(0),
            ('git', 'clone', t1_backup_module.T1_REVIVE_REPO, t1_backup_module.T1_REVIVE_DIR): Result(0),
            ('git', '-C', t1_backup_module.T1_REVIVE_DIR, 'checkout', '--detach',
             t1_backup_module.T1_REVIVE_COMMIT): Result(0),
            ('chown', '-R'): Result(0),
            ('runuser', '-u', 'alice', '--', 'bash', 'build.sh'): Result(0, 'built\n'),
            (revive_bin, 'preflight'): Result(0, 'Arch package check: NO (package needed)\n'),
            ('tar', '-C'): Result(0),
            ('systemd-inhibit',): Result(0, '=== regenerate complete\n'),
        }
        ctx.system.runner = FakeRunner(scripts)
        original_run_streamed = ctx.system.run_streamed

        def run_streamed_with_effects(argv, **kwargs):
            if argv and argv[0] == 'systemd-inhibit':
                embedded = ctx.system.path(f'{ctx.hw.esp}/EFI/APPLE/EMBEDDEDOS')
                (embedded / 'FDRData').mkdir(parents=True, exist_ok=True)
                (embedded / 'combined.memboot').write_text('firmware\n')
                (embedded / 'version.plist').write_text('version\n')
            return original_run_streamed(argv, **kwargs)

        ctx.system.run_streamed = run_streamed_with_effects
        fake_hw_after = replace(ctx.hw, t1_state='running')
        with patch.object(t1_backup_module, 'probe', return_value=fake_hw_after):
            notes = T1BackupFix().restore_t1(ctx, 'online')
        calls = ctx.system.runner.calls
        self.assertIn(['modprobe', 'acpi_call'], calls)
        self.assertIn(['systemctl', 'stop', 'usbmuxd'], calls)
        self.assertIn(['runuser', '-u', 'alice', '--', 'bash', 'build.sh'], calls)
        self.assertIn([revive_bin, 'preflight'], calls)
        self.assertTrue(any(call[0] == 'tar' for call in calls))
        self.assertTrue(any(call[0] == 'systemd-inhibit' for call in calls))
        self.assertTrue(any('t1-backup' in note for note in notes))

    def test_online_preflight_failure_stops_with_message(self):
        ctx = self.fixture(t1='recovery', t1_data=False)
        charger = ctx.system.path('/sys/class/power_supply/ADP1/online')
        charger.parent.mkdir(parents=True)
        charger.write_text('1\n')
        revive_bin = f"{t1_backup_module.T1_REVIVE_DIR}/bin/t1-revive"
        scripts = {
            ('dpkg-query',): Result(0, ''),
            ('apt-get', 'install'): Result(0),
            ('modprobe', 'acpi_call'): Result(0),
            ('systemctl', 'stop', 'usbmuxd'): Result(0),
            ('git', 'clone', t1_backup_module.T1_REVIVE_REPO, t1_backup_module.T1_REVIVE_DIR): Result(0),
            ('git', '-C', t1_backup_module.T1_REVIVE_DIR, 'checkout', '--detach',
             t1_backup_module.T1_REVIVE_COMMIT): Result(0),
            ('chown', '-R'): Result(0),
            ('runuser', '-u', 'alice', '--', 'bash', 'build.sh'): Result(0),
            (revive_bin, 'preflight'): Result(0, 'Network check: NO (no internet)\n'),
        }
        ctx.system.runner = FakeRunner(scripts)
        with self.assertRaises(FixError):
            T1BackupFix().restore_t1(ctx, 'online')
