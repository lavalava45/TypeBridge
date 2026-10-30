"""Pure and mocked tests; NEVER open a window or inject real input."""

import ctypes
import io
import types
import sys
import unittest
from unittest.mock import Mock, patch

import r16_six_direction_trial as trial


class TrialPreparationTests(unittest.TestCase):
    def test_six_unique_direction_fixtures_match_the_existing_engine_tests(self):
        self.assertEqual(len(trial.CASES), 6)
        self.assertEqual({(c.source, c.target) for c in trial.CASES}, {
            ('ru','en'), ('he','en'), ('en','ru'),
            ('he','ru'), ('en','he'), ('ru','he')})
        self.assertEqual([(c.physical, c.expected) for c in trial.CASES], [
            ('hello','hello'), ('hello','hello'),
            ('ghbdtn','привет'), ('ghbdtn','привет'),
            ('akuo','שלום'), ('akuo','שלום')])

    def test_early_space_undo_requires_exact_readback_and_hkl(self):
        case = trial.CASES[2]
        good = trial.classify(case, pre_space='привет', pre_langid=0x0419,
                              after_space='привет ', after_langid=0x0419,
                              after_undo='ghbdtn')
        self.assertTrue(good['before_space'])
        self.assertTrue(good['space_exact'])
        self.assertTrue(good['undo_restored_source'])
        self.assertTrue(good['undo_changed_text'])
        bad = trial.classify(case, pre_space='ghbdtn', pre_langid=0x0409,
                             after_space='привет ', after_langid=0x0419,
                             after_undo='привет')
        self.assertFalse(bad['before_space'])
        self.assertTrue(bad['space_exact'])
        self.assertFalse(bad['undo_restored_source'])
        self.assertTrue(bad['undo_changed_text'])

    def test_loss_duplicate_or_wrong_hkl_never_pass(self):
        case = trial.CASES[4]
        for text, langid, final in [
            ('שלו', 0x040D, 'שלום '),
            ('שלום', 0x0409, 'שלום '),
            ('שלום', 0x040D, 'שלום  '),
        ]:
            result = trial.classify(case, pre_space=text, pre_langid=langid,
                                    after_space=final, after_langid=0x040D,
                                    after_undo='akuo')
            self.assertFalse(result['before_space'] and result['space_exact'])

    def test_focus_loss_invalidates_otherwise_correct_result(self):
        case = trial.CASES[0]
        result = trial.classify(case, pre_space='hello', pre_langid=0x0409,
                                after_space='hello ', after_langid=0x0409,
                                after_undo='руддщ', focus_safe=False)
        self.assertFalse(result['before_space'])
        self.assertFalse(result['space_exact'])
        self.assertFalse(result['focus_safe'])

    def test_unexpected_input_is_hashed_not_logged(self):
        case = trial.CASES[0]
        result = trial.classify(case, pre_space='DO-NOT-LOG-THIS', pre_langid=0x0409,
                                after_space='unexpected-private-text',
                                after_langid=0x0409, after_undo='also-private')
        output = str(result)
        for secret in ('DO-NOT-LOG-THIS', 'unexpected-private-text', 'also-private'):
            self.assertNotIn(secret, output)
        self.assertEqual(result['pre_space_observation']['length'], 15)

    def test_plan_is_safe_default_and_does_not_construct_any_gui(self):
        output = io.StringIO()
        with patch('sys.stdout', output), patch.object(trial, 'AcceptanceWindow') as gui:
            self.assertEqual(trial.main([]), 0)
        gui.assert_not_called()
        self.assertEqual(output.getvalue().count('source HKL'), 6)

    def test_live_modes_require_explicit_permission(self):
        for mode in ('--observe', '--inject'):
            with (self.subTest(mode=mode),
                  patch.object(trial, 'AcceptanceWindow') as gui,
                  patch('sys.stderr', io.StringIO())):
                with self.assertRaises(SystemExit) as error:
                    trial.main([mode])
                self.assertEqual(error.exception.code, 2)
                gui.assert_not_called()

    def test_synthetic_mode_requires_special_trial_process_before_window(self):
        with (patch.object(trial, 'assert_injected_trial_running',
                           side_effect=RuntimeError('no special trial')),
              patch.object(trial, 'AcceptanceWindow') as gui):
            with self.assertRaisesRegex(RuntimeError, 'no special trial'):
                trial.main(['--inject', '--consent', trial.CONFIRMATION])
            gui.assert_not_called()

    def test_normal_confirmed_observer_constructs_only_its_own_window(self):
        with patch.object(trial, 'AcceptanceWindow') as gui:
            self.assertEqual(trial.main(['--observe', '--consent', trial.CONFIRMATION]), 0)
        gui.assert_called_once_with('observe')
        gui.return_value.run.assert_called_once_with()

    def test_windows_input_struct_and_balanced_vk_keys(self):
        self.assertEqual(ctypes.sizeof(trial.INPUT), 40)
        for vk in (0x41, 0x5A, 0x20):
            packets = trial.keyboard_events(vk, scan_code=25)
            self.assertEqual(len(packets), 2)
            self.assertEqual((packets[0].data.ki.dwFlags, packets[1].data.ki.dwFlags),
                             (0x8, 0xA))
            self.assertEqual(packets[0].data.ki.wScan, 25)
        undo = trial.keyboard_events(0x5A, ctrl=True)
        self.assertEqual(len(undo), 4)
        self.assertEqual([p.data.ki.wVk for p in undo], [0x11, 0x5A, 0x5A, 0x11])
        with self.assertRaises(ValueError):
            trial.keyboard_events(0x0D)  # No Enter, Tab or shortcut injection.

    def test_injection_checks_focus_before_reaching_win32(self):
        reader = trial.WindowsReader.__new__(trial.WindowsReader)
        reader.user32 = Mock()
        reader.foreground_is_own_edit = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, 'Focus left'):
            reader.inject(123, trial.keyboard_events(0x41, scan_code=30))
        reader.user32.SendInput.assert_not_called()

    def test_synthetic_callbacks_cancel_on_generation_change(self):
        runner = trial.AcceptanceWindow.__new__(trial.AcceptanceWindow)
        runner.phase = 'typing'
        runner.generation = 12
        runner.api = Mock()
        runner.index = 0
        runner.root = Mock()
        with patch.object(trial, 'assert_injected_trial_running') as gate:
            runner._inject_letters(2, generation=11)
            runner._inject_space(generation=11)
            runner._inject_undo(generation=11)
        gate.assert_not_called()
        runner.api.inject.assert_not_called()

    def test_auto_mode_chains_six_cases_and_reset_invalidates_scheduled_start(self):
        runner = trial.AcceptanceWindow.__new__(trial.AcceptanceWindow)
        runner.phase = 'done'
        runner.generation = 4
        runner.index = 0
        runner.mode = 'inject'
        runner.root = Mock()
        runner._show_case = Mock()
        runner.start_case = Mock()
        runner.next_case()
        self.assertEqual(runner.index, 1)
        self.assertEqual(runner.phase, 'idle')
        runner.root.after.assert_called_once_with(900, runner._start_auto_next, 5)
        runner._start_auto_next(4)
        runner.start_case.assert_not_called()
        runner._start_auto_next(5)
        runner.start_case.assert_called_once_with()

    def test_native_edit_observation_state_machine_is_mockable_without_windows(self):
        runner = trial.AcceptanceWindow.__new__(trial.AcceptanceWindow)
        runner.phase = 'typing'
        runner.index = 2
        runner.generation = 1
        runner.mode = 'observe'
        runner.early = runner.space = runner.undo = None
        runner.focus_safe = True
        runner.edit = 22
        runner.api = Mock()
        runner.api.foreground_is_own_edit.return_value = True
        runner.api.text.side_effect = ['привет', 'привет ', 'ghbdtn']
        runner.api.langid.side_effect = [0x0419, 0x0419, 0x0409]
        runner.root = Mock()
        runner.status = Mock()
        runner.start_btn = Mock()
        runner.next_btn = Mock()
        runner.results = []
        runner._save_report = Mock()
        runner.poll()
        self.assertEqual(runner.early, ('привет', 0x0419))
        runner.poll()
        self.assertEqual(runner.space, ('привет ', 0x0419))
        runner.poll()
        self.assertEqual(runner.phase, 'done')
        self.assertEqual(len(runner.results), 1)
        self.assertTrue(runner.results[0]['physical_case_pass'])
        self.assertTrue(runner.results[0]['release_gate_blocked'])
        runner._save_report.assert_called_once()
        runner.api.user32 = Mock()
        runner.api.user32.SendInput.assert_not_called()

    def test_special_process_guard_allows_only_one_trial_process_tree(self):
        # PyInstaller onefile commonly spawns 2+ TypeBridge.exe processes.
        # All descendants of the one authorized headless root are permitted.
        root = Mock(pid=10, info={'name': 'TypeBridge.exe',
                                   'cmdline': ['TypeBridge.exe','--headless','--accept-injected']})
        child = Mock(pid=11, info={'name': 'TypeBridge.exe',
                                    'cmdline': ['TypeBridge.exe', '--multiprocessing-fork']})
        root.ppid.return_value = 1
        child.ppid.return_value = 10
        root.children.return_value = [child]
        fake_psutil = types.SimpleNamespace(
            process_iter=Mock(return_value=[root, child]), Error=OSError)
        with patch.dict(sys.modules, {'psutil': fake_psutil}):
            self.assertEqual(trial.assert_injected_trial_running(), 10)
            stranger = Mock(pid=12, info={'name':'TypeBridge.exe',
                                         'cmdline':['TypeBridge.exe','--autostart']})
            stranger.ppid.return_value = 1
            fake_psutil.process_iter.return_value = [root, child, stranger]
            with self.assertRaisesRegex(RuntimeError, 'one TypeBridge process tree'):
                trial.assert_injected_trial_running()


if __name__ == '__main__':
    unittest.main()
