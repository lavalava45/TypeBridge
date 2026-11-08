"""Native COM transport contract tests. No foreground UIA or keyboard input."""
import json
import hashlib
import statistics
import sys
from time import perf_counter
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caret_word_recovery import (read_caret_word, parse_selected_word,
                                 read_selected_word, verify_selected_word)
from uia_native_worker import (UiaService, FocusEventMonitor,
                               _metadata_with_generation, read_native_caret)
from uia_selection_native import (read_native_selection, read_native_selected_word,
                                  SelectedWordLeases)


class FakeComObject:
    def QueryInterface(self, _interface):
        return self


class NativeUiaContractTests(unittest.TestCase):
    def test_unsupported_ancestor_password_property_does_not_reject_verified_leaf(self):
        from uia_native_worker import _safe_target
        uia = SimpleNamespace(UIA_IsPasswordPropertyId=101,
                              UIA_NativeWindowHandlePropertyId=102)
        leaf, parent = Mock(), Mock()
        leaf.GetRuntimeId.return_value = [1, 2]
        leaf.GetCurrentPropertyValue.side_effect = lambda key: False if key == 101 else 0
        parent.GetCurrentPropertyValue.side_effect = lambda key: object() if key == 101 else 10
        automation = Mock()
        automation.GetFocusedElement.return_value = leaf
        automation.CompareElements.return_value = True
        automation.RawViewWalker.GetParentElement.side_effect = [parent, None]
        with patch('uia_native_worker._scope', return_value=(10, 20)):
            self.assertTrue(_safe_target(automation, leaf,
                {'top': 10, 'focus': 20, 'element_id': 'uia:1,2'}, uia))
        parent.GetCurrentPropertyValue.side_effect = lambda key: True if key == 101 else 10
        automation.RawViewWalker.GetParentElement.side_effect = [parent, None]
        with patch('uia_native_worker._scope', return_value=(10, 20)):
            self.assertFalse(_safe_target(automation, leaf,
                {'top': 10, 'focus': 20, 'element_id': 'uia:1,2'}, uia))

    def test_selected_word_requires_exact_whole_word_and_hash(self):
        from engine import LayoutMapper
        mapper = LayoutMapper()
        word = 'ghbdtn'
        sample = {'state': 'ok', 'text': word, 'element_id': 'uia:1,2',
                  'generation': 3,
                  'digest': hashlib.sha256(word.encode('utf-16-le')).hexdigest(),
                  'lease': 'a' * 32}
        actual = parse_selected_word(sample, mapper, 'en', 'uia:1,2')
        self.assertIsNotNone(actual)
        self.assertEqual(actual.text, word)
        self.assertEqual(mapper.word(actual.keys, 'ru'), 'привет')
        self.assertIsNone(parse_selected_word({**sample, 'text': 'ghbdtn!'}, mapper, 'en', 'uia:1,2'))
        self.assertIsNone(parse_selected_word({**sample, 'text': 'ghbdtn '}, mapper, 'en', 'uia:1,2'))

    def test_native_selection_word_rejects_collapsed_or_changed_selection(self):
        automation = Mock()
        element = automation.GetFocusedElement.return_value
        pattern = element.GetCurrentPattern.return_value.QueryInterface.return_value
        ranges = pattern.GetSelection.return_value
        ranges.Length = 1
        selected = ranges.GetElement.return_value.Clone.return_value
        ranges.GetElement.return_value.CompareEndpoints.return_value = 0
        uia = SimpleNamespace(UIA_TextPatternId=100,
                              IUIAutomationTextPattern=object(),
                              TextPatternRangeEndpoint_Start=0,
                              TextPatternRangeEndpoint_End=1)
        request = {'mode': 'read', 'top': 10, 'focus': 20,
                   'element_id': 'uia:1,2', 'generation': 4}
        leases = SelectedWordLeases()
        with patch('uia_native_worker._safe_target', return_value=True):
            selected.CompareEndpoints.return_value = 0
            self.assertEqual(read_native_selected_word(automation, request, uia,
                                                       generation=4, leases=leases), {'state': 'refused'})
            selected.CompareEndpoints.return_value = -1
            selected.GetText.return_value = 'ghbdtn'
            self.assertEqual(read_native_selected_word(automation, request, uia,
                                                       generation=5, leases=leases), {'state': 'refused'})
            result = read_native_selected_word(automation, request, uia,
                                               generation=4, leases=leases)
            self.assertEqual(result['state'], 'ok')
            self.assertEqual(result['text'], 'ghbdtn')
            self.assertEqual(len(result['lease']), 32)
            self.assertEqual(read_native_selected_word(automation,
                {**request, 'mode': 'verify', 'digest': '0'*64,
                 'lease': result['lease']}, uia,
                generation=4, leases=leases), {'state': 'refused'})

    def test_selected_lease_blocks_identical_text_at_different_endpoints(self):
        automation = Mock()
        element = automation.GetFocusedElement.return_value
        pattern = element.GetCurrentPattern.return_value.QueryInterface.return_value
        ranges = pattern.GetSelection.return_value
        ranges.Length = 1
        selected = ranges.GetElement.return_value.Clone.return_value
        selected.CompareEndpoints.return_value = -1
        selected.GetText.return_value = 'ghbdtn'
        ranges.GetElement.return_value.CompareEndpoints.return_value = 0
        stored = selected.Clone.return_value
        uia = SimpleNamespace(UIA_TextPatternId=100,
                              IUIAutomationTextPattern=object(),
                              TextPatternRangeEndpoint_Start=0,
                              TextPatternRangeEndpoint_End=1)
        request = {'mode':'read','top':10,'focus':20,'element_id':'uia:1,2'}
        leases = SelectedWordLeases()
        with patch('uia_native_worker._safe_target', return_value=True):
            first = read_native_selected_word(automation, request, uia,
                                              generation=2, leases=leases)
            self.assertEqual(first['state'], 'ok')
            verify = {**request, 'mode':'verify', 'generation':2,
                      'digest':first['digest'], 'lease':first['lease']}
            # Different selected occurrence, identical word and RuntimeId:
            # only the saved COM range endpoints reveal the move.
            stored.CompareEndpoints.side_effect = [1, 0]
            self.assertEqual(read_native_selected_word(
                automation, verify, uia, generation=2, leases=leases),
                {'state':'refused'})
            self.assertIsNone(leases.entry)
            # One shot: the same token cannot be replayed after refusal.
            self.assertEqual(read_native_selected_word(
                automation, verify, uia, generation=2, leases=leases),
                {'state':'refused'})

    def test_selected_lease_same_endpoints_consumes_only_once(self):
        automation = Mock()
        element = automation.GetFocusedElement.return_value
        pattern = element.GetCurrentPattern.return_value.QueryInterface.return_value
        ranges = pattern.GetSelection.return_value
        ranges.Length = 1
        selected = ranges.GetElement.return_value.Clone.return_value
        selected.CompareEndpoints.return_value = -1
        selected.GetText.return_value = 'ghbdtn'
        ranges.GetElement.return_value.CompareEndpoints.return_value = 0
        selected.Clone.return_value.CompareEndpoints.return_value = 0
        uia = SimpleNamespace(UIA_TextPatternId=100,
                              IUIAutomationTextPattern=object(),
                              TextPatternRangeEndpoint_Start=0,
                              TextPatternRangeEndpoint_End=1)
        request = {'mode':'read','top':10,'focus':20,'element_id':'uia:1,2'}
        leases = SelectedWordLeases()
        with patch('uia_native_worker._safe_target', return_value=True):
            first = read_native_selected_word(automation, request, uia,
                                              generation=2, leases=leases)
            verify = {**request, 'mode':'verify', 'generation':2,
                      'digest':first['digest'], 'lease':first['lease']}
            self.assertEqual(read_native_selected_word(
                automation, verify, uia, generation=2, leases=leases),
                {'state':'matched'})
            self.assertEqual(read_native_selected_word(
                automation, verify, uia, generation=2, leases=leases),
                {'state':'refused'})

    def test_selected_lease_restart_expiry_and_generation_refused(self):
        now = [100.0]
        clock = lambda: now[0]
        leases = SelectedWordLeases(lifetime=3.0, clock=clock)
        saved = Mock()
        token = leases.issue(saved, top=10, focus=20, element_id='uia:1,2',
                             generation=2, digest='a'*64)
        self.assertIsNone(SelectedWordLeases(clock=clock).consume(token))
        self.assertIsNone(leases.consume('bad'))
        token = leases.issue(saved, top=10, focus=20, element_id='uia:1,2',
                             generation=2, digest='a'*64)
        now[0] += 3.1
        self.assertIsNone(leases.consume(token))

    def test_selected_word_transport_two_phase_same_id_without_send(self):
        from engine import LayoutMapper
        context = SimpleNamespace(top=10, focus=20, language='en')
        text = 'ghbdtn'
        digest = hashlib.sha256(text.encode('utf-16-le')).hexdigest()
        reader = Mock()
        reader.request.side_effect = [
            {'state': 'ok', 'text': text, 'element_id': 'uia:1,2',
             'generation': 4, 'digest': digest, 'lease': 'a' * 32}, {'state': 'matched'}]
        with patch('uia_native_worker.get_uia_service', return_value=reader):
            word = read_selected_word(context, LayoutMapper(), 'uia:1,2', lambda: True)
            self.assertIsNotNone(word)
            self.assertTrue(verify_selected_word(context, word, lambda: True))
        self.assertEqual(reader.request.call_args.args[0], {
            'command': 'selected-word', 'mode': 'verify', 'top': 10,
            'focus': 20, 'element_id': 'uia:1,2',
            'generation': 4, 'digest': digest, 'lease': 'a' * 32})

    def test_metadata_refuses_focus_change_during_probe_even_same_final_field(self):
        monitor = Mock(ready=True)
        monitor.current.side_effect = [(4, False), (6, False)]
        wire = '10|20|safe|123|checked|uia:1,2|1'
        self.assertIsNone(_metadata_with_generation(lambda: wire, monitor, lambda _: None))

    def test_metadata_refuses_password_event_during_final_pump(self):
        monitor = Mock(ready=True)
        generation = [9]
        monitor.current.side_effect = lambda: (generation[0], False)
        wire = '10|20|safe|123|checked|uia:1,2|1'
        def pump(_):
            generation[0] += 1
        self.assertIsNone(_metadata_with_generation(lambda: wire, monitor, pump))

    def test_stable_metadata_includes_generation_as_eighth_field(self):
        monitor = Mock(ready=True)
        monitor.current.return_value = (12, False)
        wire = '10|20|safe|123|checked|uia:1,2|1'
        self.assertEqual(_metadata_with_generation(lambda: wire, monitor, lambda _: None),
                         wire + '|12')

    def test_focus_a_b_a_generation_advances_without_metadata_poll(self):
        automation = Mock()
        uia = SimpleNamespace(IUIAutomationFocusChangedEventHandler=object(),
                              IUIAutomationPropertyChangedEventHandler=object(),
                              UIA_IsPasswordPropertyId=300, TreeScope_Element=1)
        monitor = FocusEventMonitor(automation, uia, SimpleNamespace(COMObject=FakeComObject))
        monitor.start()
        self.assertEqual(monitor.current(), (0, False))
        monitor.focus_handler.HandleFocusChangedEvent(None)  # A -> B
        monitor.focus_handler.HandleFocusChangedEvent(None)  # B -> A
        self.assertEqual(monitor.current(), (2, True))
        monitor.refresh()
        self.assertEqual(monitor.current(), (2, False))
        monitor.property_handler.HandlePropertyChangedEvent(None, 300, True)
        self.assertEqual(monitor.current(), (3, False))
        monitor.property_handler.HandlePropertyChangedEvent(None, 123, False)
        self.assertEqual(monitor.current(), (3, False))
        monitor.stop()
        automation.RemoveFocusChangedEventHandler.assert_called_once()

    def test_failed_property_subscription_prevents_monitor_ready(self):
        automation = Mock()
        automation.AddPropertyChangedEventHandlerNativeArray.side_effect = OSError('fake-provider')
        uia = SimpleNamespace(IUIAutomationFocusChangedEventHandler=object(),
                              IUIAutomationPropertyChangedEventHandler=object(),
                              UIA_IsPasswordPropertyId=300, TreeScope_Element=1)
        monitor = FocusEventMonitor(automation, uia, SimpleNamespace(COMObject=FakeComObject))
        with self.assertRaises(OSError):
            monitor.start()
        self.assertFalse(monitor.ready)
        automation.RemoveFocusChangedEventHandler.assert_called_once()

    def test_forced_refresh_binds_typed_pointer_and_native_id_array(self):
        automation = Mock()
        uia = SimpleNamespace(IUIAutomationFocusChangedEventHandler=object(),
                              IUIAutomationPropertyChangedEventHandler=object(),
                              UIA_IsPasswordPropertyId=300, TreeScope_Element=1)
        monitor = FocusEventMonitor(automation, uia, SimpleNamespace(COMObject=FakeComObject))
        monitor.start()
        self.assertIs(automation.AddFocusChangedEventHandler.call_args.args[1],
                      monitor.focus_interface)
        args = automation.AddPropertyChangedEventHandlerNativeArray.call_args.args
        self.assertIs(args[3], monitor.property_interface)
        self.assertEqual(list(args[4]), [300])
        self.assertEqual(args[5], 1)
        monitor.focus_handler.HandleFocusChangedEvent(None)
        self.assertTrue(monitor.current()[1])
        self.assertTrue(monitor.refresh())
        self.assertTrue(monitor.ready)
        self.assertFalse(monitor.current()[1])
        automation.RemovePropertyChangedEventHandler.assert_called()
        monitor.stop()

    def test_failed_forced_refresh_keeps_worker_unavailable_without_retry_storm(self):
        automation = Mock()
        uia = SimpleNamespace(IUIAutomationFocusChangedEventHandler=object(),
                              IUIAutomationPropertyChangedEventHandler=object(),
                              UIA_IsPasswordPropertyId=300, TreeScope_Element=1)
        monitor = FocusEventMonitor(automation, uia, SimpleNamespace(COMObject=FakeComObject))
        monitor.start()
        monitor.focus_handler.HandleFocusChangedEvent(None)
        automation.AddPropertyChangedEventHandlerNativeArray.side_effect = OSError('provider-transient')
        self.assertFalse(monitor.refresh())
        self.assertFalse(monitor.ready)
        self.assertTrue(monitor.current()[1])
        calls = automation.AddPropertyChangedEventHandlerNativeArray.call_count
        self.assertFalse(monitor.refresh())
        self.assertEqual(automation.AddPropertyChangedEventHandlerNativeArray.call_count, calls)
        automation.AddPropertyChangedEventHandlerNativeArray.side_effect = None
        monitor.retry_at = 0
        self.assertTrue(monitor.refresh())
        self.assertTrue(monitor.ready)
        monitor.stop()

    def test_real_spawn_synthetic_reuses_child_without_uia(self):
        service = UiaService(synthetic=True, timeout=5)
        command = {'command': 'caret', 'top': 1, 'focus': 2, 'element_id': 'uia:1'}
        try:
            self.assertIsNone(service.request(command))
            self.assertIsNotNone(service.process)
            original_pid = service.process.pid
            started = perf_counter()
            self.assertIsNone(service.request(command))
            warm_elapsed = perf_counter() - started
            self.assertEqual(service.process.pid, original_pid)
            self.assertLess(warm_elapsed, 1.0)  # IPC smoke, not a COM latency claim.
        finally:
            service.close()

    def test_reader_uses_one_native_request_and_never_launches_powershell(self):
        context = SimpleNamespace(top=11, focus=22, language='en')
        mapper = Mock(layouts={'en': 0x409})
        service = Mock()
        service.request.return_value = None
        with patch('uia_native_worker.get_uia_service', return_value=service), \
                patch('subprocess.run') as powershell:
            self.assertIsNone(read_caret_word(context, mapper, 'uia:1,2', lambda: True))
        powershell.assert_not_called()
        service.request.assert_called_once()
        request = service.request.call_args.args[0]
        self.assertEqual(request, {'command': 'caret', 'top': 11,
                                   'focus': 22, 'element_id': 'uia:1,2'})

    def test_incomplete_or_changed_context_refuses_without_process(self):
        service = UiaService()
        with patch.object(service, '_start_locked') as start:
            self.assertIsNone(service.request({'command': 'caret'}, valid=lambda: False))
            self.assertIsNone(service.request({'command': 'caret', 'a': 'x' * 2000}))
        start.assert_not_called()

    def test_failed_worker_process_start_returns_none_and_recovers(self):
        service = UiaService(timeout=.1)
        parent, child, process = Mock(), Mock(), Mock()
        process.start.side_effect = RuntimeError('simulated spawn failure')
        process.pid = None
        context = Mock()
        context.Pipe.return_value = parent, child
        context.Process.return_value = process
        service.context = context
        self.assertIsNone(service.request({'command': 'metadata'}))
        child.close.assert_called_once()
        parent.close.assert_called_once()
        self.assertIsNone(service.process)
        self.assertIsNone(service.connection)

    def test_busy_worker_lock_fails_closed_without_blocking_caller(self):
        service = UiaService()
        service.lock.acquire()
        try:
            started = perf_counter()
            self.assertIsNone(service.request({'command': 'metadata'}))
            self.assertLess(perf_counter() - started, .25)
            self.assertIsNone(service.process)
        finally:
            service.lock.release()

    def test_offhook_caret_can_wait_beyond_metadata_median_for_shared_worker(self):
        service = UiaService()
        fake_lock = service.lock = Mock()
        fake_lock.acquire.return_value = False
        self.assertIsNone(service.request({'command': 'metadata'}))
        self.assertEqual(fake_lock.acquire.call_args.kwargs['timeout'], .05)
        self.assertIsNone(service.request({'command': 'caret'}))
        self.assertEqual(fake_lock.acquire.call_args.kwargs['timeout'], .125)
        self.assertIsNone(service.request({'command': 'selected-word'}))
        self.assertEqual(fake_lock.acquire.call_args.kwargs['timeout'], .125)
        self.assertEqual(service.request({'command': 'metadata'}, on_busy='service-busy'),
                         'service-busy')
        fake_lock.release.assert_not_called()

    def test_provider_unavailable_does_not_masquerade_as_lock_contention(self):
        service = UiaService()
        with patch.object(service, '_start_locked', side_effect=OSError('failed-start')):
            self.assertIsNone(service.request({'command': 'metadata'},
                                              on_busy='service-busy'))

    def test_one_process_is_reused_and_serial_request_has_bounded_reply(self):
        service = UiaService(timeout=.15)
        connection = Mock()
        connection.poll.return_value = True
        connection.recv_bytes.return_value = b'{"sample":{"element_id":"uia:1"}}'
        service.connection = connection
        service.process = Mock()
        request = {'command': 'caret', 'top': 1, 'focus': 2, 'element_id': 'uia:1'}
        self.assertEqual(service.request(request), {'element_id': 'uia:1'})
        self.assertEqual(service.request(request), {'element_id': 'uia:1'})
        self.assertEqual(connection.send_bytes.call_count, 2)
        self.assertEqual(connection.poll.call_count, 2)
        self.assertEqual(json.loads(connection.send_bytes.call_args.args[0]), request)

    def test_provider_timeout_closes_connection_and_prevents_immediate_respawn(self):
        service = UiaService(timeout=.01)
        service.connection = Mock()
        service.connection.poll.return_value = False
        process = service.process = Mock(pid=123)
        process.is_alive.side_effect = [True, False, False]
        self.assertIsNone(service.request({'command': 'caret'}))
        process.terminate.assert_called_once()
        with patch.object(service, '_start_locked') as start:
            self.assertIsNone(service.request({'command': 'caret'}))
        start.assert_not_called()

    def test_native_reader_refuses_changed_focus_and_password_without_text_read(self):
        automation = Mock()
        element = automation.GetFocusedElement.return_value
        element.GetRuntimeId.return_value = [1, 2]
        uia = SimpleNamespace(UIA_TextPatternId=100,
                              UIA_IsPasswordPropertyId=101,
                              UIA_NativeWindowHandlePropertyId=102)
        request = {'top': 11, 'focus': 22, 'element_id': 'uia:1,2'}
        with patch('uia_native_worker._scope', return_value=(11, 33)):
            self.assertIsNone(read_native_caret(automation, request, uia))
        element.GetCurrentPattern.assert_not_called()
        with patch('uia_native_worker._scope', return_value=(11, 22)):
            automation.CompareElements.return_value = True
            element.GetCurrentPropertyValue.return_value = True
            self.assertIsNone(read_native_caret(automation, request, uia))
        element.GetCurrentPattern.assert_not_called()

    def test_selection_refuses_wrong_pid_before_reading_any_text(self):
        automation = Mock()
        automation.CompareElements.return_value = True
        element = automation.GetFocusedElement.return_value
        element.GetRuntimeId.return_value = [3, 4]
        element.GetCurrentPropertyValue.side_effect = lambda key: {101: False, 102: 44, 103: 999}[key]
        automation.RawViewWalker.GetParentElement.return_value = None
        uia = SimpleNamespace(UIA_TextPatternId=100, UIA_IsPasswordPropertyId=101,
                              UIA_NativeWindowHandlePropertyId=102, UIA_ProcessIdPropertyId=103)
        with patch('uia_native_worker._scope', return_value=(44, 55)):
            result = read_native_selection(automation, {'top': 44, 'focus': 55, 'pid': 123}, uia)
        self.assertEqual(result, {'state': 'refused'})
        element.GetCurrentPattern.assert_not_called()


def benchmark_synthetic_warm_pairs(count=80):
    """Standalone no-COM spawn IPC timing, deliberately excluding provider time."""
    service = UiaService(synthetic=True, timeout=5)
    command = {'command': 'caret', 'top': 1, 'focus': 2, 'element_id': 'uia:1'}
    try:
        service.request(command)  # Startup is deliberately excluded.
        pid = service.process.pid
        measurements = []
        for _ in range(count):
            start = perf_counter()
            service.request(command)
            service.request(command)
            measurements.append((perf_counter() - start) * 1000)
            assert service.process is not None and service.process.pid == pid
        ordered = sorted(measurements)
        print(f'synthetic warm two-request IPC: count={count} pid={pid} '
              f'median={statistics.median(measurements):.3f}ms '
              f'mean={statistics.mean(measurements):.3f}ms '
              f'p95={ordered[int(.95*(count-1))]:.3f}ms '
              f'max={ordered[-1]:.3f}ms')
    finally:
        service.close()


def probe_live_metadata_only():
    """Explicit read-only probe: sanitized metadata, no editor content/IDs."""
    service = UiaService(timeout=2.5)
    first_pid = None
    try:
        for attempt in range(2):
            line = service.request({'command': 'metadata'})
            current_pid = service.process.pid if service.process is not None else None
            if first_pid is None:
                first_pid = current_pid
            if not isinstance(line, str):
                print(f'metadata sample {attempt+1}: unavailable')
                continue
            parts = line.split('|')
            if len(parts) != 8:
                print(f'metadata sample {attempt+1}: invalid wire fields={len(parts)}')
                continue
            print(f'metadata sample {attempt+1}: state={parts[2]} '
                  f'reason={parts[4]} addressable={parts[6]} '
                  f'event_generation_present={parts[7].isdigit()} '
                  f'process_reused={current_pid is not None and current_pid == first_pid}')
    finally:
        service.close()


def probe_metadata_steady_state(count=32):
    """Bounded metadata-only observation; never prints IDs or field contents."""
    from collections import Counter
    import time
    service = UiaService(timeout=2.5)
    states, times, generations = Counter(), [], []
    original_process = None
    reused = 0
    try:
        for _ in range(count):
            started = perf_counter()
            reply = service.request({'command': 'metadata'})
            times.append((perf_counter() - started) * 1000)
            if service.process is not None:
                if original_process is None:
                    original_process = service.process.pid
                reused += service.process.pid == original_process
            if not isinstance(reply, str):
                states['unavailable'] += 1
            else:
                parts = reply.split('|')
                if len(parts) != 8 or not parts[7].isdigit():
                    states['invalid-wire'] += 1
                else:
                    states[(parts[2], parts[4], parts[6])] += 1
                    generations.append(int(parts[7]))
            time.sleep(.1)
        sorted_times = sorted(times)
        print('metadata_only_steady:', 'count=', count,
              'outcomes=', dict(states),
              'generation_changes=', sum(a != b for a, b in zip(generations, generations[1:])),
              'generation_observations=', len(generations),
              'same_child_samples=', reused,
              'median_ms=', round(statistics.median(times), 2),
              'p95_ms=', round(sorted_times[int(.95 * (len(times)-1))], 2),
              'max_ms=', round(max(times), 2))
    finally:
        service.close()


def probe_metadata_contention(count=24):
    """Two metadata-only callers competing for the same IPC lock."""
    import time
    import threading
    from collections import Counter
    service = UiaService(timeout=2.5)
    outcomes, durations = Counter(), []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def caller():
        barrier.wait()
        for _ in range(count):
            start = perf_counter()
            reply = service.request({'command': 'metadata'})
            elapsed = (perf_counter() - start) * 1000
            parts = reply.split('|') if isinstance(reply, str) else ()
            status = ((parts[2], parts[4], parts[6]) if len(parts) == 8 else 'unavailable')
            with lock:
                outcomes[str(status)] += 1
                durations.append(elapsed)
            time.sleep(.1)

    try:
        workers = [threading.Thread(target=caller, daemon=True) for _ in range(2)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join()
        print('metadata_only_contention:', 'n=',len(durations),
              'outcomes=',dict(outcomes),
              'median_ms=',round(statistics.median(durations),2),
              'p95_ms=',round(sorted(durations)[int(.95*(len(durations)-1))],2))
    finally:
        service.close()


if __name__ == '__main__':
    if sys.argv[1:] == ['--benchmark']:
        benchmark_synthetic_warm_pairs()
    elif sys.argv[1:] == ['--metadata-only']:
        probe_live_metadata_only()
    elif sys.argv[1:] == ['--metadata-steady']:
        probe_metadata_steady_state()
    elif sys.argv[1:] == ['--metadata-70']:
        probe_metadata_steady_state(70)
    elif sys.argv[1:] == ['--metadata-contention']:
        probe_metadata_contention()
    else:
        unittest.main()
