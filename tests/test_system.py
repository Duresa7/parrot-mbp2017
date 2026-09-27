import io
import json
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from helpers import FakeRunner, make_mac
from mbp2017.system import CommandError, Result, System


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = FakeRunner()
        self.out = io.StringIO()
        self.system = System(str(self.root), runner=self.runner, out=self.out, euid=1000, env={})

    def write(self, path, text, mode=0o644):
        target = self.system.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(mode)
        return target

    def test_path_and_reads(self):
        self.write('/etc/example', 'hello')
        self.assertEqual(self.system.read_text('/etc/example'), 'hello')
        self.assertEqual(self.system.read_bytes('/etc/example'), b'hello')
        self.assertEqual(self.system.read_text('/missing', 'fallback'), 'fallback')
        self.assertEqual(self.system.listdir('/missing'), [])
        self.assertEqual(self.system.listdir('/etc'), ['example'])
        self.assertEqual(self.system.glob('/etc/*'), ['/etc/example'])
        for path in ('relative', '/etc/../../outside'):
            with self.assertRaises(ValueError):
                self.system.path(path)
        with patch.object(Path, 'open', side_effect=PermissionError):
            self.assertEqual(self.system.read_text('/etc/example', 'private'), 'private')

    def test_state_paths(self):
        self.assertEqual(System.STATE_DIR, '/var/lib/parrot-mbp2017')
        self.assertEqual(System.MANIFEST, System.STATE_DIR + '/manifest.json')
        self.assertEqual(System.REPLACED, System.STATE_DIR + '/replaced')

    def test_unmanaged_write_contents_modes_owner_and_log(self):
        for content in ('private text', b'\x00\xff'):
            with self.subTest(content=content), patch.object(self.system, 'chown') as chown:
                self.system.write_file('/backup/data', content, 0o600, owner=(123, 456))
                chown.assert_called_once_with('/backup/data', 123, 456)
                expected = content.encode() if isinstance(content, str) else content
                self.assertEqual(self.system.read_bytes('/backup/data'), expected)
                self.assertEqual(self.system.path('/backup/data').stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.system.exists(System.MANIFEST))
        self.assertIn('wrote file: /backup/data', self.system.read_text('/var/log/parrot-mbp2017.log'))
        self.assertNotIn('private text', self.system.read_text('/var/log/parrot-mbp2017.log'))

    def test_create_tar_members_modes_owner_and_log(self):
        self.write('/source/nested/file', 'private data')
        with patch.object(self.system, 'chown') as chown:
            self.system.create_tar('/source', 'EFI/APPLE', '/backup/data.tar', 0o600, owner=(123, 456))
        chown.assert_called_once_with('/backup/data.tar', 123, 456)
        with tarfile.open(self.system.path('/backup/data.tar')) as archive:
            self.assertEqual(archive.getnames(), ['EFI/APPLE', 'EFI/APPLE/nested', 'EFI/APPLE/nested/file'])
            self.assertEqual(archive.extractfile('EFI/APPLE/nested/file').read(), b'private data')
        self.assertEqual(self.system.path('/backup/data.tar').stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.system.exists(System.MANIFEST))
        self.assertIn('created archive: /source to /backup/data.tar',
                      self.system.read_text('/var/log/parrot-mbp2017.log'))

    def test_unmanaged_write_and_tar_failures_preserve_destination(self):
        self.write('/backup/data', 'original')
        self.write('/source/file', 'new')
        with patch('mbp2017.system.os.replace', side_effect=OSError('rename failed')):
            with self.assertRaises(OSError):
                self.system.write_file('/backup/data', 'replacement', 0o600)
        with patch('mbp2017.system.tarfile.TarFile.add', side_effect=OSError('read failed')):
            with self.assertRaises(OSError):
                self.system.create_tar('/source', 'EFI/APPLE', '/backup/data', 0o600)
        self.assertEqual(self.system.read_text('/backup/data'), 'original')
        self.assertEqual(self.system.listdir('/backup'), ['data'])
        self.assertFalse(self.system.exists(System.MANIFEST))

    def test_new_file_idempotent_and_remove(self):
        self.assertTrue(self.system.install_file('test', '/etc/new', b'content', 0o750))
        self.assertEqual(self.system.path('/etc/new').stat().st_mode & 0o777, 0o750)
        first = self.system.path('/etc/new').stat().st_mtime_ns
        self.assertFalse(self.system.install_file('test', '/etc/new', b'content', 0o750))
        self.assertEqual(first, self.system.path('/etc/new').stat().st_mtime_ns)
        self.assertEqual(self.system.owned_paths('test'), ['/etc/new'])
        self.assertFalse(self.system.remove_file('other', '/etc/new'))
        self.assertTrue(self.system.remove_file('test', '/etc/new'))
        self.assertFalse(self.system.exists('/etc/new'))
        self.assertFalse(self.system.remove_file('test', '/etc/new'))

    def test_read_text_defaults_for_directory_and_invalid_utf8(self):
        self.system.makedirs('/directory')
        self.system.path('/binary').write_bytes(b'\xff\xfe')
        for path in ('/directory', '/binary'):
            with self.subTest(path=path):
                self.assertIsNone(self.system.read_text(path))
                self.assertEqual(self.system.read_text(path, 'fallback'), 'fallback')

    def test_replaced_file_backup_survives_multiple_installs(self):
        self.write('/etc/example', 'original', 0o640)
        self.system.install_file('test', '/etc/example', 'new')
        self.system.install_file('test', '/etc/example', 'newer')
        self.assertEqual(self.system.read_text(System.REPLACED + '/etc/example'), 'original')
        restarted = System(str(self.root), runner=self.runner)
        restarted.remove_file('test', '/etc/example')
        self.assertEqual(restarted.read_text('/etc/example'), 'original')
        self.assertEqual(restarted.path('/etc/example').stat().st_mode & 0o777, 0o640)

    def test_preexisting_file_is_preserved(self):
        self.write('/etc/example', 'same')
        self.assertFalse(self.system.install_file('test', '/etc/example', 'same'))
        manifest = json.loads(self.system.read_text(System.MANIFEST))
        self.assertTrue(manifest['test']['files']['/etc/example']['preexisting'])
        self.assertFalse(self.system.remove_file('test', '/etc/example'))
        self.assertEqual(self.system.read_text('/etc/example'), 'same')

    def test_upgrading_preexisting_file_preserves_original(self):
        self.write('/etc/example', 'same', 0o640)
        self.system.install_file('test', '/etc/example', 'same')
        self.system.install_file('test', '/etc/example', 'updated')
        self.system.remove_file('test', '/etc/example')
        self.assertEqual(self.system.read_text('/etc/example'), 'same')
        self.assertEqual(self.system.path('/etc/example').stat().st_mode & 0o777, 0o640)

    def test_multiple_owners_rejected(self):
        self.system.install_file('one', '/etc/test', 'a')
        with self.assertRaises(ValueError):
            self.system.install_file('two', '/etc/test', 'b')
        with self.assertRaises(ValueError):
            self.system.install_block('two', '/etc/test', 'b')

    def test_block_add_replace_remove_preserves_original(self):
        for original in ('# user data\n\n', '# user data without newline', '', '# Windows lines\r\n'):
            with self.subTest(original=original):
                self.write('/etc/quirks', original)
                self.assertTrue(self.system.install_block('input', '/etc/quirks', 'body\n'))
                self.assertTrue(self.system.block_is_current('input', '/etc/quirks', 'body'))
                self.assertFalse(self.system.install_block('input', '/etc/quirks', 'body'))
                self.system.install_block('input', '/etc/quirks', 'replacement')
                self.assertNotIn('\nbody\n', self.system.read_text('/etc/quirks'))
                with self.system.path('/etc/quirks').open('a') as stream:
                    stream.write('# later user content\n')
                self.system.remove_block('input', '/etc/quirks')
                self.assertEqual(self.system.read_text('/etc/quirks'), original + '# later user content\n')

    def test_unreadable_block_is_not_overwritten(self):
        self.write('/etc/quirks', 'private content')
        with patch.object(self.system, 'read_bytes', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.system.install_block('input', '/etc/quirks', 'replacement')
        self.assertEqual(self.system.read_text('/etc/quirks'), 'private content')
        self.assertFalse(self.system.exists(System.MANIFEST))

    def test_unreadable_manifest_is_not_replaced(self):
        self.system.install_file('test', '/file', 'original')
        original_read = self.system.read_bytes

        def read(path):
            if path == System.MANIFEST:
                raise PermissionError
            return original_read(path)

        with patch.object(self.system, 'read_bytes', side_effect=read):
            with self.assertRaises(PermissionError):
                self.system.install_file('test', '/file', 'replacement')
        self.assertEqual(self.system.read_text('/file'), 'original')

    def test_new_block_file_deleted_and_multiple_blocks_preserved(self):
        self.system.install_block('one', '/etc/new', 'one')
        self.system.install_block('two', '/etc/new', 'two')
        self.system.remove_block('one', '/etc/new')
        self.assertTrue(self.system.block_is_current('two', '/etc/new', 'two'))
        self.system.remove_block('two', '/etc/new')
        self.assertFalse(self.system.exists('/etc/new'))

    def test_malformed_block_fails_without_overwriting(self):
        original = '# >>> parrot-mbp2017 input >>>\nuser text\n'
        self.write('/etc/quirks', original)
        with self.assertRaises(ValueError):
            self.system.install_block('input', '/etc/quirks', 'new')
        self.assertEqual(self.system.read_text('/etc/quirks'), original)

    def test_unowned_block_removal_does_nothing(self):
        self.write('/etc/quirks', self.system._block('input', 'existing'))
        self.assertFalse(self.system.remove_block('input', '/etc/quirks'))
        self.assertTrue(self.system.exists('/etc/quirks'))

    def test_dry_run_writes_nothing(self):
        self.system.dry_run = True
        self.system.install_file('test', '/etc/new', 'new')
        self.system.install_block('test', '/etc/quirks', 'body')
        self.system.note('test', 'value', {'a': [1]})
        self.system.clear_notes('test')
        self.system.makedirs('/new')
        self.system.copy_file('/source', '/target', 0o600)
        self.system.write_file('/backup/data', 'private data', 0o600, owner=(123, 456))
        self.system.create_tar('/missing', 'EFI/APPLE', '/backup/data.tar', 0o600, owner=(123, 456))
        self.system.chown('/target', 1000, 1000)
        self.system.run(['would-not-exist'], mutating=True)
        self.system.run_streamed(['build'], log_path='/log')
        self.system.extract_tar('/archive.tar', '/extracted')
        self.system.remove_tree('/extracted')
        self.system.log('test')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.runner.calls, [])
        self.assertIn('would run:', self.out.getvalue())
        self.assertIn('would write file: /backup/data', self.out.getvalue())
        self.assertIn('would create archive: /missing to /backup/data.tar', self.out.getvalue())

    def test_dry_run_remove_preserves_manifest_and_file(self):
        self.system.install_file('test', '/file', 'data')
        self.system.install_block('test', '/block', 'body')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.system.dry_run = True
        self.system.remove_file('test', '/file')
        self.system.remove_block('test', '/block')
        after = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_notes(self):
        self.system.note('test', 'state', {'nested': [1]})
        notes = self.system.notes('test')
        notes['state']['nested'].append(2)
        self.assertEqual(self.system.notes('test'), {'state': {'nested': [1]}})
        self.system.clear_notes('test')
        self.assertEqual(self.system.notes('test'), {})

    def test_commands_check_options_and_logging(self):
        self.system.runner = FakeRunner({('test',): Result(4, stderr='one\ntwo\nlast')})
        with self.assertRaises(CommandError) as caught:
            self.system.run(['test'], input='data', env={'TEST': 'yes'}, timeout=2)
        self.assertEqual(caught.exception.argv, ['test'])
        self.assertIn('last', str(caught.exception))
        self.assertEqual(self.system.runner.options[0]['input'], 'data')
        self.assertEqual(self.system.runner.options[0]['timeout'], 2)
        self.assertEqual(self.system.runner.options[0]['env']['TEST'], 'yes')
        self.assertFalse(self.system.run(['test'], check=False).ok)
        self.assertIn('run: test', self.system.read_text('/var/log/parrot-mbp2017.log'))

    def test_missing_executable_and_log_failure(self):
        self.system.runner = None
        with patch('mbp2017.system.subprocess.run', side_effect=FileNotFoundError('missing')):
            self.assertEqual(self.system.run(['missing'], check=False).returncode, 127)
        with patch.object(Path, 'mkdir', side_effect=PermissionError):
            self.system.log('must not raise')

    def test_timeout_reports_actionable_command_failure(self):
        self.system.runner = None
        with patch('mbp2017.system.subprocess.run', side_effect=subprocess.TimeoutExpired(['test'], 2)):
            with self.assertRaises(CommandError) as caught:
                self.system.run(['test'], timeout=2)
        self.assertEqual(caught.exception.result.returncode, 124)
        self.assertIn('timed out', str(caught.exception))

    def test_streamed_runner(self):
        self.system.runner = FakeRunner({('build',): Result(7, 'first\nsecond\n', 'error\n')})
        lines = []
        result = self.system.run_streamed(['build'], log_path='/var/log/build',
                                          on_line=lines.append, cwd='/build')
        self.assertEqual(result, 7)
        self.assertEqual(lines, ['first', 'second', 'error'])
        self.assertEqual(self.system.read_text('/var/log/build'), 'first\nsecond\nerror\n')
        self.assertEqual(self.system.runner.options[0]['cwd'], str(self.root / 'build'))

    def test_package_versions(self):
        self.system.runner = FakeRunner({('dpkg-query',): Result(1,
            'one\t1.0\tii \ntwo\t2.0\thi \nold\t3\trc \nbroken\t4\tiU \n')})
        self.assertEqual(self.system.package_versions(['one', 'two', 'old', 'broken']),
                         {'one': '1.0', 'two': '2.0'})
        self.assertEqual(self.system.package_versions([]), {})

    def test_user_and_which(self):
        make_mac(self.root)
        self.assertEqual(self.system.invoking_user().name, 'alice')
        self.system.euid = 0
        self.assertIsNone(self.system.invoking_user())
        self.system.env['SUDO_USER'] = 'alice'
        self.assertEqual(self.system.invoking_user().home, '/home/alice')
        self.assertEqual(self.system.which('plasmashell'), '/usr/bin/plasmashell')
        self.assertIsNone(self.system.which('absent'))

    def test_copy_file_and_directory(self):
        self.write('/source', 'bytes')
        self.system.makedirs('/nested/directory', 0o700)
        self.system.copy_file('/source', '/nested/directory/copy', 0o600)
        self.assertEqual(self.system.read_text('/nested/directory/copy'), 'bytes')
        self.assertEqual(self.system.path('/nested/directory').stat().st_mode & 0o777, 0o700)


class PathTests(unittest.TestCase):
    def test_sbin_directories_are_searched_for_commands(self):
        system = System(tempfile.mkdtemp(), env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(system.env["PATH"], "/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin")
        system = System(tempfile.mkdtemp(), env={"PATH": "/usr/sbin:/usr/bin"})
        self.assertEqual(system.env["PATH"].split(":").count("/usr/sbin"), 1)


class ManifestAccessTests(unittest.TestCase):
    def test_manifest_is_readable_without_root(self):
        system = System(tempfile.mkdtemp())
        system.install_file("test", "/etc/example", "content")
        self.assertEqual(system.path(System.MANIFEST).stat().st_mode & 0o777, 0o644)

    def test_unreadable_manifest_asks_for_sudo(self):
        system = System(tempfile.mkdtemp())
        with patch.object(System, "read_bytes", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaisesRegex(PermissionError, "Run this with sudo"):
                system.notes("t1bridge")


if __name__ == "__main__":
    unittest.main()
