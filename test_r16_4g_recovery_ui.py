"""Legacy transaction recovery error is visible before hooks, not swallowed."""

from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from typebridge_app import main


class RecoveryStartupUiTests(TestCase):
    def test_gui_explains_legacy_journal_failure_and_does_not_start(self):
        root = Mock()
        with (patch('typebridge_app.tk.Tk', return_value=root),
              patch('typebridge_app.migrate_user_data'),
              patch('typebridge_app.TypeBridgeGui', side_effect=RuntimeError(
                  'Legacy R16/4e package journal requires manual recovery')),
              patch('typebridge_app.messagebox.showerror') as show):
            self.assertEqual(main([]), 1)
        root.withdraw.assert_called_once()
        root.destroy.assert_called_once()
        show.assert_called_once()
        self.assertIn('manual recovery', show.call_args.args[1])
        root.mainloop.assert_not_called()

    def test_headless_refuses_replay_before_live_hook_and_prints_reason(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / 'language_packs'
            root.mkdir()
            error = StringIO()
            with (patch('typebridge_app.LANGUAGE_PACK_DIR', root),
                  patch('typebridge_app.migrate_user_data'),
                  patch('typebridge_app.recover_package_transactions',
                        side_effect=RuntimeError('Legacy R16/4e manual recovery')),
                  patch('typebridge_app.exclusive_live_hook') as live,
                  redirect_stderr(error)):
                self.assertEqual(main(['--headless']), 1)
            self.assertIn('manual recovery', error.getvalue())
            live.assert_not_called()

