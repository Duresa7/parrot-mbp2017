import io
from pathlib import Path
import tempfile
import unittest

from helpers import FakeRunner
from mbp2017 import dkms
from mbp2017.system import Result, System


class DkmsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        self.runner = FakeRunner()
        self.system = System(temp.name, runner=self.runner, euid=1000, env={}, out=io.StringIO())

    def test_entries_preserve_order_and_filter_module_and_malformed_lines(self):
        self.runner.scripts[('dkms', 'status', 'speaker')] = Result(0,
            'other/1, ignored, x86_64: installed\n'
            'speaker/1: added\n'
            'malformed\n'
            'speaker/1, second, x86_64: installed (Original modules exist)\n'
            'speaker/1, first, x86_64: built\n'
            'speaker/2, second, x86_64: installed\n')
        self.assertEqual(dkms.entries(self.system, 'speaker'), [
            {'kernel': 'second', 'status': 'installed (Original modules exist)'},
            {'kernel': 'first', 'status': 'built'},
            {'kernel': 'second', 'status': 'installed'},
        ])
        self.assertEqual(dkms.installed_kernels(self.system, 'speaker'), {'second'})

    def test_failed_query_preserves_audio_entries_but_not_installed_kernels(self):
        self.runner.scripts[('dkms', 'status', 'speaker')] = Result(
            1, 'speaker/1, kernel, x86_64: installed\n')
        self.assertEqual(dkms.entries(self.system, 'speaker'), [
            {'kernel': 'kernel', 'status': 'installed'}])
        self.assertEqual(dkms.installed_kernels(self.system, 'speaker'), set())

    def test_missing_command_has_no_entries(self):
        self.runner.scripts[('dkms',)] = Result(127)
        self.assertEqual(dkms.entries(self.system, 'speaker'), [])
        self.assertEqual(dkms.installed_kernels(self.system, 'speaker'), set())
