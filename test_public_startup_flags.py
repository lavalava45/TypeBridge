"""Exercise startup callback registration without creating a window or hook."""

from unittest import TestCase
from unittest.mock import Mock, patch

from typebridge_app import TypeBridgeGui


class PublicStartupFlagsTests(TestCase):
    def setUp(self):
        self.gui = TypeBridgeGui.__new__(TypeBridgeGui)
        self.root = Mock()
        self.gui._raw_probe = None
        self.gui._control_probe = None
        self.gui._hook_watchdog = None

    def test_public_with_both_debug_flags_never_schedules_missing_probes(self):
        with patch('typebridge_app.DEBUG_ENABLED', True), \
                patch('typebridge_app.DEBUG_WORDS_ENABLED', False), \
                patch('typebridge_app.BUILD_PROFILE', 'public'):
            self.gui._schedule_debug_probes(self.root)
        self.root.after.assert_not_called()

    def test_public_without_debug_flags_schedules_no_probes(self):
        with patch('typebridge_app.DEBUG_ENABLED', False), \
                patch('typebridge_app.BUILD_PROFILE', 'public'):
            self.gui._schedule_debug_probes(self.root)
        self.root.after.assert_not_called()

    def test_diagnostics_with_debug_flags_still_schedules_all_probes(self):
        self.gui._raw_probe = Mock()
        self.gui._control_probe = Mock()
        self.gui._hook_watchdog = Mock()
        with patch('typebridge_app.DEBUG_ENABLED', True), \
                patch('typebridge_app.BUILD_PROFILE', 'diagnostics'):
            self.gui._schedule_debug_probes(self.root)
        self.assertEqual(self.root.after.call_args_list,
                         [((800, self.gui._raw_probe.start), {}),
                          ((1100, self.gui._control_probe.start), {}),
                          ((4000, self.gui._poll_hook_watchdog), {})])

    def test_diagnostics_without_debug_flags_schedules_no_probes(self):
        with patch('typebridge_app.DEBUG_ENABLED', False), \
                patch('typebridge_app.BUILD_PROFILE', 'diagnostics'):
            self.gui._schedule_debug_probes(self.root)
        self.root.after.assert_not_called()
