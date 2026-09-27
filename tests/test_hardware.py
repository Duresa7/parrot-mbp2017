from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helpers import KERNEL, FakeRunner, make_mac
from mbp2017.hardware import MODELS, probe, summary_rows, to_dict
from mbp2017.system import System


class HardwareTests(unittest.TestCase):
    def fixture(self, *, euid=0, **options):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        system = System(make_mac(temp.name, **options), euid=euid, runner=FakeRunner(), env={})
        with patch.object(system, 'kernel_release', return_value=KERNEL):
            return probe(system)

    def test_tested_model(self):
        hw = self.fixture()
        self.assertTrue(hw.supported and hw.tested and hw.known_model)
        self.assertEqual(hw.model_name, '15-inch, 2017')
        self.assertTrue(hw.amd_gpu and hw.wifi_bcm43602 and hw.audio_cs8409)
        self.assertEqual(hw.wifi_driver, 'brcmfmac')
        self.assertTrue(hw.t1 and hw.t1_data and hw.spi_keyboard and hw.spi_touchpad)
        self.assertEqual(hw.t1_config, 2)
        self.assertTrue(hw.is_parrot and hw.debian13_based and hw.plasma)
        self.assertEqual(hw.headers, {KERNEL: True})
        self.assertEqual(hw.kernel, KERNEL)
        self.assertIn(('Model', 'MacBookPro14,3 — 15-inch, 2017'), summary_rows(hw))
        self.assertTrue(to_dict(hw)['supported'])

    def test_intel_only_and_recovery(self):
        hw = self.fixture(model='MacBookPro14,2', amd=False, t1='recovery', t1_config=1, t1_data=False)
        self.assertFalse(hw.amd_gpu or hw.tested)
        self.assertTrue(hw.supported and hw.t1)
        self.assertEqual(hw.t1_state, 'recovery')
        self.assertFalse(hw.t1_data)

    def test_non_apple_and_unknown_model(self):
        self.assertFalse(self.fixture(vendor='Other').supported)
        self.assertFalse(self.fixture(model='MacBookPro99,1').supported)
        self.assertEqual(len(MODELS), 6)

    def test_non_root_unknown_esp_data(self):
        hw = self.fixture(euid=1000, t1_data=False)
        self.assertIsNone(hw.t1_data)
        self.assertIn(('T1 data', 'unknown, run with sudo'), summary_rows(hw))
        self.assertTrue(self.fixture(euid=1000).t1_data)

    def test_absent_hardware_and_kernels(self):
        hw = self.fixture(amd=False, bcm=False, cs8409=False, t1=None, spi_keyboard=False,
                          spi_touchpad=False, esp=None, plasma=False,
                          kernels=['one', 'two'], headers={'one': True, 'two': False})
        self.assertFalse(hw.t1 or hw.wifi_bcm43602 or hw.audio_cs8409 or hw.spi_keyboard or hw.plasma)
        self.assertIsNone(hw.esp)
        self.assertEqual(hw.headers, {'one': True, 'two': False})

    def test_debian_detection(self):
        hw = self.fixture(distro_id='debian', distro_codename='trixie')
        self.assertFalse(hw.is_parrot)
        self.assertTrue(hw.debian13_based)
