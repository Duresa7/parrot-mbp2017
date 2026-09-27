import io
import unittest

from mbp2017.ui import UI


class UITests(unittest.TestCase):
    def test_confirm_modes(self):
        for yes, interactive, default, answer, expected in [
            (True, True, False, 'n\n', True),
            (False, False, False, '', False),
            (False, False, True, '', True),
            (False, True, True, 'no\n', False),
            (False, True, False, 'invalid\nyes\n', True),
        ]:
            ui = UI(color=False, assume_yes=yes, interactive=interactive,
                    out=io.StringIO(), inp=io.StringIO(answer))
            self.assertEqual(ui.confirm('Continue?', default), expected)

    def test_toggle(self):
        out = io.StringIO()
        ui = UI(color=False, assume_yes=False, interactive=True, out=out,
                inp=io.StringIO('invalid\n9\n1,2\n\n'))
        self.assertEqual(ui.toggle([('one', 'One'), ('two', 'Two')], {'one'}), {'two'})
        self.assertIn('[x] One', out.getvalue())
        self.assertIn('[x] Two', out.getvalue())

    def test_ascii_fallback_and_color(self):
        binary = io.BytesIO()
        out = io.TextIOWrapper(binary, encoding='ascii')
        ui = UI(color=False, assume_yes=True, interactive=False, out=out)
        ui.ok('All good — done')
        ui.warn('Look here')
        ui.error('Failed')
        out.flush()
        content = binary.getvalue().decode('ascii')
        self.assertIn('OK: All good - done', content)
        self.assertNotIn('\x1b', content)
        other = io.StringIO()
        UI(color=True, assume_yes=True, interactive=False, out=other).ok('Good')
        self.assertIn('\x1b[32m', other.getvalue())
