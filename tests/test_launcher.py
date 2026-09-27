import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class LauncherTests(unittest.TestCase):
    def test_launcher_leaves_no_bytecode_in_the_checkout(self):
        checkout = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, checkout)
        shutil.copy2(ROOT / "parrot-mbp2017", checkout)
        shutil.copytree(ROOT / "mbp2017", checkout / "mbp2017",
                        ignore=shutil.ignore_patterns("__pycache__"))
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        result = subprocess.run([sys.executable, str(checkout / "parrot-mbp2017"), "list"],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(checkout.rglob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
