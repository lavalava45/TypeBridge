# TypeBridge: Deep native-layer audit (2026-11-07)

## Scope, baseline and method

Independent worker-2 review of native/editor code changed in the 2026-11-01–07 interval. Baseline: current working tree (including untracked modules and concurrently edited files), with parent committed changes `ca4becf`, `83cdf63`, `ea3c957`, `cc4e9b1`, `a2d1e0b`. Review included control/data flow, contract checks, callers, cleanup after partial native operations, concurrent focus/clipboard changes, and mock-only regression tests. No hooks, user foreground, original SwitchER64, GUI, EXE build or VM were started by this worker. Repo `AGENTS.md` restrictions respected. SIMPLE_SWITCHER_MODE intentionally disables password checks and quarantine; not changed.

## Checked change areas and call-site contracts

| Module / edit areas | Callers and expected behavior | Result / remaining risk |
|---|---|---|
| `native_transaction_queue.py`: ownership and finishing state, recovery buffer admission, modifier release, trigger KEYUP bypass, overflow | `typebridge_app.py` off-hook input replay; `test_native_transaction_queue.py`, `test_notepad_barrier_replay_20261107.py`, `test_p0_queue_overflow_recovery_20261107.py`, `test_hook_cycle_offhook.py` | Bounded capture/recovery and owner checks exercised by models. Actual physical stream ordering after focus/HKL transitions requires separate live acceptance. |
| `clipboard_hotkeys.py`: context refresh TTL and generation; worker command queue and lifecycle, suppression/release, injected event bypass, context identity checks | `typebridge_app.py` feature setup; `test_hotkey_context.py`, `test_clipboard_hotkeys.py` | No confirmed regression. `handle()` still depends on a recent foreground context (TTL 200 ms), so cannot prove A→B→A races absent without physical tests. |
| `clipboard_tools.py`: privacy flags, delayed history reader, plain-paste worker, deadline and target rechecks, exact sequence/owner rollback, SendInput short-prefix release, token cancellation | `ClipboardTools.request_plain`, `paste_item`, `receive`, `selection_copy`, app GUI call-sites; `test_clipboard_tools.py`, `test_audit_plain_paste_worker_20261106.py`, `test_audit_clipboard_rollback_20261106.py` | Existing mock coverage passed. Native clipboard interaction is not atomic with foreground/application changes; existing refusal and encrypted checkpoint reduce damage but cannot eliminate every race. |
| `clipboard_selection.py`: preflight policy, snapshot format limits, DPAPI protect before Copy, strict supported format enumeration, one-shot SendInput, partial-keyup release, owner-scoped restore | `quick_search.read_selection`, `ClipboardTools.PlainPasteRollback`, `clipboard_recovery.restore_file`; `test_clipboard_selection.py`, `test_clipboard_recovery_20261107.py` | Covered fail-before-mutate, partial restore, and owner-sequence refusal. Full clipboard format interoperability remains outside mock proof. |
| `clipboard_recovery.py`: bitmap metadata and pixels, serialize/decode/canonical names, bounded DPAPI write+fsync+load, safe discard, explicit restore | `NativeCopy.protect_snapshot/restore`, `typebridge_app.py` tray recovery; `test_clipboard_recovery_20261107.py` | Verified checkpoint persistence and partial failure with tests. Recovery is explicit and overwrites clipboard by user request; does not run automatically. |
| `uia_native_worker.py`: process lifetime, IPC lock/timeout/retry, target identity, UTF-16 caret samples, UIA focus/password watcher generations, selected-word lease dispatch | `password_fields.PasswordFieldGuard`, `quick_search.read_selection`, `caret_word_recovery`, `browser_readback`; `test_native_uia_caret_20261107.py`, `test_native_password_20261107.py`, `test_simple_selected_word_20261107.py` | **Fixed** unhandled startup `RuntimeError` and stale unstarted Process retention (finding N-01 below). Focus/DOM identity cannot make SendInput atomic. |
| `uia_selection_native.py`: selected-word lease creation/expiry/consume, scope/runtime ID, exact endpoint/digest two-phase verification, generic selection fallback states | `uia_native_worker.worker_main`, `caret_word_recovery`, `quick_search`; `test_native_uia_caret_20261107.py`, `test_simple_selected_word_20261107.py` | Verified model rejection for collapsed/stale/changed selection; no live COM provider integration claim. |
| `uia_password_native.py`: Win32 style vs shared browser UIA tree, positive password evidence, addressability and metadata, native process lookup | `uia_native_worker.worker_main` metadata branch, `password_fields.PasswordFieldGuard`; `test_native_password_20261107.py` | Guard semantics maintained for strict mode; simple-mode bypass is explicit product choice. Provider failures can still make strict checks unavailable. |
| `scintilla_selection.py`: 64-bit `Sci_TextRangeFull`, bounded remote allocations and process identity, UTF-8 selection metadata, prepare/commit/verify, one native replacement, Undo behavior | `typebridge_app.py` manual Pause, `experiments/r10_private_scintilla_20261107.py`, `test_scintilla_selection_20261107.py`, `test_r10_selection_foundation_20261107.py` | Model tests pass. `SCI_SETSEL(2160)` is `(anchor,caret)` and current call is correct. Strict refusal if `SCI_GETTEXTRANGEFULL(2039)` unavailable is correct. Separate isolated fixture currently uses Scintilla 4.4.6, which predates API 2039 (5.2.3); not a production transport defect. |
| `selection_cycle.py`: position units, selected physical-key mapping, next-layout plan, owner/revision/focus binding, once-only replace/verify | `ScintillaSelectionAdapter`, `typebridge_app.py`, `test_r10_selection_foundation_20261107.py`, `test_scintilla_selection_20261107.py` | Pure model contracts exercised. Unknown/multi-key mapping ambiguity refused by design. |
| `early_scintilla.py`: separate early hook and off-hook budgets, non-repeated write/cleanup, timeouts | `early_native` Scintilla integration and `ScintillaRangeTransport`; `test_scintilla_selection_20261107.py`, `test_r10_pause_scintilla_20261107.py` | Duration guard and no automatic retry reviewed; COM/native scheduling remains environment dependent. |
| `early_native_harness.py`: isolated private desktop EDIT + separate synthetic field, no physical input | `test_early_native_edit.py`, isolated fixtures | No import-time desktop or GUI start. Only explicit fixture execution creates windows on inactive desktop. |

## Findings and changes

**N-01 (fixed, reproduced): UIA worker refuses Process.start by throwing `RuntimeError` through public `UiaService.request`.** At `uia_native_worker.py` startup, `Process.start()` can raise synchronously (e.g. Windows spawn failure). `request()` previously handled only I/O, timeout and decoding exceptions; exception escaped from background consumers and left `self.process` holding an unstarted `Process`, creating an inconsistent future lifecycle. Added narrow `RuntimeError` catch and eager cleanup of both pipe ends and unstarted process in `_start_locked`. Regression `test_native_uia_caret_20261107.py::NativeUiaContractTests::test_failed_worker_process_start_returns_none_and_recovers` first failed with simulated `RuntimeError`; it now passes, confirming `None` refusal and release of startup state. No COM/foreground was invoked.

**N-02 (fixture limitation, not production bug):** Private Scintilla test executable `Lib/site-packages/pythonwin/scintilla.dll` has Windows `FileVersion=4.4.6`. Official Scintilla history states `SCI_GETTEXTRANGEFULL` was added in v5.2.3 (2022-05-22). Therefore the fixture cannot satisfy native adapter `2039` and correctly yields `selection-incomplete-range`. Do not relax full-range validation or call legacy 2162 with an incompatible 64-bit structure. For native acceptance, prepare an owned fixture with Scintilla 5.2.3+; use no user foreground.

**N-03 (separate fixture blocker):** Private Notepad++ probe returned `fixture-no-scintilla-window` and no enumerated window on its newly created inactive desktop. This only establishes that private app startup did not produce a discoverable fixture window; it does not exercise `scintilla_selection.py` or prove its failure. No user Notepad++ window/process was touched.

No other confirmed production defects in the checked changes. This does **not** establish real physical input safety, exact COM provider behavior, real browser focus, external clipboard owner races, or editor-specific Undo correctness.

## Validation

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1; python -m pytest -q --tb=short -k 'not test_settings_and_picker_fit_without_mapping_or_native_access'` with the 17 explicit native/clipboard/selection/hotkey test file arguments: **240 passed, 1 deselected, 131 subtests passed** (5.57 seconds). **Исправление прежнего объяснения:** GUI-тест был исключён из этого native-прогона из соображений безопасной изоляции GUI; `ttk/cursors.tcl` фактически присутствует в установленном Python. Позднее скрытый Tk-тест успешно прошёл на private desktop, и вся GUI-матрица завершилась **261/261 PASS**; см. `docs/GUI-MATRIX-EXPANDED-20261107.md`. Старый вывод об отсутствующем Tcl-файле опровергнут проверкой.
- Narrow red-green test: `python -m pytest -q --tb=short test_native_uia_caret_20261107.py -k failed_worker_process_start`: **1 passed, 24 deselected** after fix (initial failure: unhandled `RuntimeError`, then unstarted process not cleared).
- Historical audit: stale mock in `test_clipboard_selection.py` was corrected by this worker in an earlier assigned task, not changed in this deep-audit round.

## Final native acceptance evidence reconciliation (2026-11-07, read-only follow-up)

This follow-up re-read existing private-desktop probe outputs, source contracts and targeted logs; **no new test was run** because the relevant safety branches already have focused evidence. In particular, neither the 261-test GUI matrix, the 240-test native suite nor the expensive dictionary suite was repeated. No application window, global hook, SendInput, foreground/HKL or user clipboard was touched.

| Target | Existing concrete evidence | What is proven / still open |
|---|---|---|
| **Actual private RichEdit50W** | `build/audit-20261107/r04-private-richedit-probe.json`: `result=PASS`, `desktop=private-inactive`, `hooks=false`, `SendInput=false`, clipboard/focus/HKL unchanged; three addressed corrections EN→RU, EN→HE, RU→EN with `A_readback=true`, `B_unchanged=true`, `undo=true`, `redo=true`; real `_cycle_word` first/repeat with spaces and neighboring text preserved. `build/audit-20261107/early-native-private-rich-edit.json`: six verified replacements and Undo for supported native cases. | Native transport and Undo/Redo **proved for owned private RichEdit fixtures only**. Not proof of physical Pause, app-integrated clipboard, foreground routing, or live Windows keyboard hook. |
| **RichEdit timeout / delayed write** | `r04-private-richedit-probe.json`: a deliberately suspended receiver produces `native:preflight:message-102:error-1460`; after resuming the worker, field A contains `T`, B unchanged; `late_delivery_possible=true`, `retry_performed=false`. | **Important residual risk:** a timed-out cross-process message may still mutate its originally addressed control *after* the sender has refused. Timeout cannot be treated as evidence that no mutation occurred; preserve quarantine/no-retry and require separate real-world acceptance for delayed delivery. This is not a new confirmed cross-field mutation. |
| **Real Notepad++ / Scintilla ABI** | `docs/R10-NOTEPAD-CRASH-20261107.md`: two observed Notepad++ 8.9.8.1 crashes on selected-word Pause, exception `0xc0000005`; dump analysis identified legacy `SCI_GETTEXTRANGE(2162)` 16-byte header being decoded as 24 bytes. Current `scintilla_selection.py:109–124` sends FULL `2039` with pointer-sized fields, refuses incomplete range; no 2162 fallback. Existing narrow model verification: 33 tests. | **The historical crash was a confirmed production defect**, subsequently fixed in *source*. The old compiled EXE may still contain the dangerous legacy ABI unless rebuilt. Current code's modern Notepad++ FULL protocol and Undo have **not** been physically accepted. |
| **Pythonwin Scintilla private fixture** | Installed `pythonwin/scintilla.dll` FileVersion `4.4.6`; private fixture returns `selection-incomplete-range` on FULL `2039`, which appeared in Scintilla 5.2.3. | Expected **fail-closed incompatibility** of the old fixture, **not** a successful test on Scintilla 5.x and not a reason to restore legacy 2162. |
| **Notepad++ inactive private launch** | `docs/R10-NOTEPAD-CRASH-20261107.md`: three disposable `-multiInst -nosession -noPlugin` startup attempts with private settingsDir produced no discoverable editor HWND during bounded 10-second windows; child processes were ended without touching user windows. | `fixture-no-scintilla-window` is an **environment/fixture BLOCKED**, not a failed FULL protocol test. No further Notepad++ process launched in this follow-up. |
| **Chrome / Chromium UIA** | `build/audit-20261107/edge-private-uia-probe.json` records `status=unavailable`, `reason=private-edge-window-not-created`, `user_foreground_changed=false`, `global_input=false`; model tests cover same-element Runtime ID, selection refusal, caret preflight and focus revisions. | Native Chrome/Edge DOM-selected/caret readback and actual focus/Undo are **BLOCKED**, neither PASS nor a confirmed failure. Do not use current user browser windows, create extra Chrome processes or perform live clicks for this audit. |

**Release acceptance boundary:** private RichEdit verified native writes and Undo; Scintilla 4.4.6 correctly refuses modern FULL; modern Notepad++ and Chromium actual editors remain unverified. No model-only or private Tk result closes the physical input/hook/foreground/HKL/Undo acceptance gate. Historical Notepad++ crash and potential delayed RichEdit delivery must remain visible rather than being collapsed into a generic "all native PASS" statement.

## Ownership

This worker changed only `uia_native_worker.py`, `test_native_uia_caret_20261107.py`, and this new report during the current deep-audit assignment. `typebridge_app.py`, `build_profiles.py` and `docs/WEEKLY-CODE-AUDIT*` are owned by prime and were not edited.

## Additional audit: typebridge_app.py dirty-hunk ledger and 2026-11-01..05 commits

Generated against the working tree at review time. `reviewed: structural/static` means the changed lines were inspected for their scope and immediate callers; it does **not** certify live behavior. `reviewed: deep call-site` indicates manual walkthrough with current call paths and focused model tests. For every row the new/old range is explicit so missing edits are detectable.

| # | Old lines | New lines | Enclosing scope | Change | Review status | Changed fragment |
|---:|---|---|---|---|---|---|
| 1 | 49 +2 | 49 +4 | `module/global` | changed | reviewed: structural/static | from direct_hotkeys import (DEFAULTS, SUGGESTIONS, MANUAL_KEYS, Chord, compile_bindings, |
| 2 | 52 +0 | 55 +2 | `module/global` | added | reviewed: structural/static | from scintilla_selection import ScintillaRangeTransport, ScintillaSelectionAdapter |
| 3 | 69 +1 | 73 +2 | `module/global` | changed | reviewed: structural/static | from caret_word_recovery import (parse_caret_word, read_caret_word, |
| 4 | 97 +0 | 103 +5 | `module/global` | added | reviewed: structural/static | # 2026-11-07: user-requested switcher-only mode. Legacy guard/quarantine |
| 5 | 124 +0 | 135 +2 | `module/global` | added | reviewed: structural/static | VK_DELETE = 0x2E |
| 6 | 190 +0 | 203 +3 | `module/global` | added | reviewed: structural/static | # Canonical single manual command. None reads legacy manual_key/cycle_key. |
| 7 | 217 +0 | 233 +3 | `module/global` | added | reviewed: structural/static | # Native HWND text-transport eligibility, as verified with host process |
| 8 | 250 +0 | 269 +3 | `load_settings` | added | reviewed: structural/static | word_key = raw.get('word_key') |
| 9 | 289 +0 | 311 +2 | `load_settings` | added | reviewed: structural/static | word_key=word_key, |
| 10 | 328 +6 | 351 +2 | `staged_password_guard` | changed | reviewed: structural/static | def staged_password_guard(settings: Settings, *, simple=False): |
| 11 | 335 +1 | 354 +2 | `staged_password_guard` | changed | reviewed: structural/static | return PasswordFieldGuard(disabled=bool(simple), require_element_id=True, |
| 12 | 494 +0 | 515 +25 | `manual_cycle_inputs` | added | reviewed: structural/static | def manual_cycle_inputs(word: RecentWord, replacement: str) -> list[Input]: |
| 13 | 523 +0 | 569 +10 | `module/global` | added | reviewed: structural/static | @property |
| 14 | 527 +1 | 582 +1 | `LiveHook.__init__` | changed | reviewed: structural/static | password_guard=None, simple_mode=False): |
| 15 | 530 +0 | 586 +1 | `LiveHook.__init__` | added | reviewed: structural/static | self.simple_mode = bool(simple_mode) |
| 16 | 579 +1 | 635 +1 | `LiveHook.__init__` | changed | reviewed: structural/static | self.debug_words = DEBUG_WORDS_ENABLED and password_guard is None and not self.simple_mode |
| 17 | 739 +1 | 795 +6 | `LiveHook._context` | changed | reviewed: deep call-site | # Most controls and their top-level window belong to one process. |
| 18 | 740 +0 | 802 +4 | `LiveHook._context` | added | reviewed: deep call-site | if self.simple_mode: |
| 19 | 762 +3 | 827 +4 | `LiveHook._context` | changed | reviewed: deep call-site | check_metadata = (not self.simple_mode and not for_layout and password_guard is not None and ( |
| 20 | 778 +1 | 844 +1 | `LiveHook._context` | changed | reviewed: deep call-site | if (not self.simple_mode and not for_layout and focus_class in ("edit", "richedit20w", "richedit50w" |
| 21 | 787 +2 | 853 +5 | `LiveHook._context` | changed | reviewed: deep call-site | # block a callback on the UIA provider. The production policy |
| 22 | 792 +1 | 861 +6 | `LiveHook._context` | changed | reviewed: deep call-site | sample = password_guard.snapshot |
| 23 | 793 +0 | 868 +12 | `LiveHook._context` | added | reviewed: deep call-site | if not native_metadata: |
| 24 | 794 +0 | 881 +1 | `LiveHook._context` | added | reviewed: deep call-site | and not getattr(password_guard, 'explicit_password_only', False) |
| 25 | 810 +1 | 897 +2 | `LiveHook._context` | changed | reviewed: deep call-site | owner, password_revision=password_revision, hkl=raw_hkl, |
| 26 | 812 +0 | 901 +2 | `LiveHook.clear_token` | added | reviewed: structural/static | self._selection_cycle = None |
| 27 | 851 +0 | 942 +2 | `LiveHook._native_blocked` | added | reviewed: structural/static | if self.simple_mode: |
| 28 | 867 +1 | 959 +8 | `LiveHook._quarantine_native` | changed | reviewed: structural/static | if self.simple_mode: |
| 29 | 883 +0 | 983 +4 | `LiveHook._quarantine_native` | added | reviewed: structural/static | # One metadata-only incident code explains a persistent degraded |
| 30 | 885 +0 | 989 +1 | `LiveHook._quarantine_native` | added | reviewed: structural/static | 'uncertain-native-write': 'после неподтверждённой записи в исходное поле', |
| 31 | 890 +1 | 994 +5 | `LiveHook._quarantine_native` | changed | reviewed: structural/static | message = ('Автоисправление ограничено в одном поле. Проверь введённый текст.' |
| 32 | 997 +1 | 1105 +2 | `LiveHook._send_guarded_inputs` | changed | reviewed: structural/static | def _send_guarded_inputs(self, events, valid, *, before_send=None, |
| 33 | 1027 +1 | 1136 +1 | `LiveHook._send_guarded_inputs` | changed | reviewed: structural/static | if guard is not None and not self.simple_mode and layout_only is not True: |
| 34 | 1036 +1 | 1145 +1 | `LiveHook._send_guarded_inputs` | changed | reviewed: structural/static | if (current.password_revision or getattr(guard, 'explicit_password_only', False)) and not guard.reva |
| 35 | 1040 +0 | 1150 +12 | `LiveHook._send_guarded_inputs` | added | reviewed: structural/static | if final_check is not None: |
| 36 | 1101 +1 | 1222 +1 | `LiveHook._schedule_native` | changed | reviewed: deep call-site | layout_only=False, on_finished=None): |
| 37 | 1108 +0 | 1230 +14 | `LiveHook._schedule_native` | added | reviewed: deep call-site | pending = getattr(self, 'native_pending_recovery', None) |
| 38 | 1126 +0 | 1262 +1 | `LiveHook._schedule_native` | added | reviewed: deep call-site | job_started = False |
| 39 | 1138 +0 | 1275 +1 | `LiveHook._schedule_native` | added | reviewed: deep call-site | job_started = True |
| 40 | 1144 +1 | 1281 +14 | `LiveHook._schedule_native` | changed | reviewed: deep call-site | if job_started: |
| 41 | 1163 +0 | 1314 +2 | `LiveHook._schedule_native` | added | reviewed: deep call-site | if on_finished is not None: |
| 42 | 1171 +0 | 1324 +2 | `LiveHook._schedule_native` | added | reviewed: deep call-site | if on_finished is not None: |
| 43 | 1183 +0 | 1338 +1 | `LiveHook._schedule_keyboard` | added | reviewed: deep call-site | self.emit('diagnostic', 'keyboard-job-unavailable:pending-callback-release') |
| 44 | 1194 +0 | 1350 +5 | `LiveHook._schedule_keyboard` | added | reviewed: deep call-site | def finish(): |
| 45 | 1225 +3 | 1385 +1 | `LiveHook._schedule_keyboard` | changed | reviewed: deep call-site | finish() |
| 46 | 1231 +1 | 1389 +1 | `LiveHook._schedule_keyboard` | changed | reviewed: deep call-site | drop_always=drop_always, on_finished=finish, **options) |
| 47 | 1262 +1 | 1420 +1 | `LiveHook._browser_field_changed_after_submission` | changed | reviewed: structural/static | if self.simple_mode or context.process_name.lower() not in BROWSER_APPS: |
| 48 | 1306 +1 | 1464 +1 | `LiveHook._postwrite_permitted` | changed | reviewed: structural/static | # rejects explicit password fields. Calling revalidate() |
| 49 | 1397 +1 | 1555 +21 | `LiveHook._drain_owned_native_input` | changed | reviewed: deep call-site | submission_started = False |
| 50 | 1437 +0 | 1616 +12 | `LiveHook._drain_owned_native_input` | added | reviewed: deep call-site | # A consumed Pause (including its KEYUP) is a command, not |
| 51 | 1441 +1 | 1631 +4 | `LiveHook._drain_owned_native_input` | changed | reviewed: deep call-site | if (not self.simple_mode and reason in ('focus-changed', 'focus-changed-before-commit', |
| 52 | 1442 +0 | 1636 +5 | `LiveHook._drain_owned_native_input` | added | reviewed: deep call-site | 'unsafe-replay-context-changed-after-password-check', |
| 53 | 1445 +1 | 1643 +2 | `LiveHook._drain_owned_native_input` | changed | reviewed: deep call-site | and not uncertain_replay and recoverable |
| 54 | 1450 +2 | 1649 +2 | `LiveHook._drain_owned_native_input` | changed | reviewed: deep call-site | for e in recoverable)): |
| 55 | 1457 +1 | 1656 +1 | `LiveHook._drain_owned_native_input` | changed | reviewed: deep call-site | self.emit('native-recovery-available', len(recoverable)) |
| 56 | 1462 +0 | 1662 +2 | `LiveHook._drain_owned_native_input` | added | reviewed: deep call-site | 'keyboard-context-changed', |
| 57 | 1468 +6 | 1669 +16 | `LiveHook._drain_owned_native_input` | changed | reviewed: deep call-site | 'unsafe-replay-context-changed-before-send', |
| 58 | 1515 +1 | 1726 +7 | `LiveHook.restore_native_pending` | changed | reviewed: structural/static | try: |
| 59 | 1522 +1 | 1739 +5 | `LiveHook.restore_native_pending` | changed | reviewed: structural/static | try: |
| 60 | 1556 +1 | 1777 +1 | `LiveHook.restore_native_pending` | changed | reviewed: structural/static | if len(paired) > 2 * gate.limit: |
| 61 | 1563 +1 | 1784 +2 | `LiveHook.restore_native_pending` | changed | reviewed: structural/static | replay_thread_id=current.thread_id, |
| 62 | 1567 +6 | 1788 +0 | `LiveHook.restore_native_pending` | removed | reviewed: structural/static | for item in paired: |
| 63 | 1712 +1 | 1928 +1 | `LiveHook._try_early` | changed | reviewed: deep call-site | keyboard = self.simple_mode or not self._use_native_text_transport(context, name.value) |
| 64 | 1719 +1 | 1935 +1 | `LiveHook._try_early` | changed | reviewed: deep call-site | if not self.simple_mode and getattr(self, disabled_attribute, False): |
| 65 | 1721 +1 | 1937 +1 | `LiveHook._try_early` | changed | reviewed: deep call-site | if not self.simple_mode and keyboard and self._word_scope(context) in getattr(self, 'native_blocked_ |
| 66 | 1810 +1 | 2026 +1 | `LiveHook._try_early` | changed | reviewed: deep call-site | self._quarantine_native('uncertain-native-write', context) |
| 67 | 1851 +1 | 2067 +1 | `LiveHook._try_early` | changed | reviewed: deep call-site | self._quarantine_native('uncertain-native-write', context) |
| 68 | 1968 +1 | 2184 +2 | `LiveHook._remember` | changed | reviewed: structural/static | def _remember(self, context, keys, text, language, suffix, *, whole=False, |
| 69 | 1971 +1 | 2188 +41 | `LiveHook._remember` | changed | reviewed: structural/static | active_languages=getattr(self.engine, 'active_languages', None), |
| 70 | 2005 +0 | 2263 +4 | `LiveHook._try_caret_recovery` | added | reviewed: structural/static | if self.simple_mode: |
| 71 | 2012 +2 | 2273 +4 | `LiveHook._try_caret_recovery` | changed | reviewed: structural/static | permissive = getattr(guard, 'explicit_password_only', False) is True |
| 72 | 2014 +0 | 2278 +3 | `LiveHook._try_caret_recovery` | added | reviewed: structural/static | if boundary_vk is None: |
| 73 | 2015 +0 | 2282 +2 | `LiveHook._try_caret_recovery` | added | reviewed: structural/static | if permissive or not isinstance(element_id, str) or not element_id.startswith('uia:'): |
| 74 | 2038 +0 | 2307 +3 | `LiveHook._try_caret_recovery` | added | reviewed: structural/static | if boundary_vk is None: |
| 75 | 2040 +1 | 2311 +8 | `LiveHook._try_caret_recovery` | changed | reviewed: structural/static | started = perf_counter() |
| 76 | 2043 +1 | 2321 +1 | `LiveHook._try_caret_recovery` | changed | reviewed: structural/static | or (boundary_vk is not None and (word.suffix or word.caret_offset is not None))): |
| 77 | 2055 +2 | 2333 +4 | `LiveHook._try_caret_recovery` | changed | reviewed: structural/static | word.suffix, whole=True, caret_offset=word.caret_offset) |
| 78 | 2060 +1 | 2340 +192 | `LiveHook._try_caret_recovery` | changed | reviewed: structural/static | drop_always=boundary_vk is None, |
| 79 | 2062 +1 | 2533 +86 | `LiveHook._cycle_browser_selection` | changed | reviewed: confirmed B-01 (simple mode) | if ((not self.simple_mode and (not isinstance(element_id, str) |
| 80 | 2069 +1 | 2625 +1 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | native = not self.simple_mode and self._use_native_text_transport(context, class_name.value) |
| 81 | 2070 +0 | 2627 +11 | `LiveHook._cycle_word` | added | reviewed: deep call-site | if scintilla and not selection_checked: |
| 82 | 2076 +1 | 2642 +0 | `LiveHook._cycle_word` | removed | reviewed: deep call-site | match = self.engine.decide(self.keys, context.language, allow_rare=True) |
| 83 | 2078 +1 | 2644 +1 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | whole=self._physical_keys_form_word(self.keys)) |
| 84 | 2080 +0 | 2647 +4 | `LiveHook._cycle_word` | added | reviewed: deep call-site | emit_pipeline(self, 'manual-cycle', method='word-memory', |
| 85 | 2083 +0 | 2654 +2 | `LiveHook._cycle_word` | added | reviewed: deep call-site | emit_pipeline(self, 'manual-cycle', method='word-memory', |
| 86 | 2092 +1 | 2664 +4 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | # The native prefix transaction can only replace text BEFORE caret. |
| 87 | 2115 +1 | 2690 +1 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | trigger=trigger, drop_always=True, allow_paused=True): |
| 88 | 2120 +3 | 2695 +11 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | # P0-D: the hook's word memory is not a cursor/selection lease. JS or |
| 89 | 2123 +0 | 2707 +1 | `LiveHook._cycle_word` | added | reviewed: deep call-site | gate = getattr(self, 'native_queue', None) |
| 90 | 2125 +1 | 2709 +2 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | and self.settings is settings |
| 91 | 2127 +1 | 2712 +13 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | and getattr(self, 'last_word', None) is word |
| 92 | 2129 +1 | 2726 +52 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | def read_matching(expected_id=''): |
| 93 | 2132 +0 | 2781 +2 | `LiveHook._cycle_word` | added | reviewed: deep call-site | emit_pipeline(self, 'manual-cycle', method='keyboard', reason=reason, |
| 94 | 2138 +0 | 2789 +2 | `LiveHook._cycle_word` | added | reviewed: deep call-site | emit_pipeline(self, 'manual-cycle', method='keyboard', |
| 95 | 2147 +0 | 2800 +10 | `LiveHook._cycle_word` | added | reviewed: deep call-site | if observed != int(hkl) & 0xFFFFFFFF: |
| 96 | 2148 +0 | 2811 +2 | `LiveHook._cycle_word` | added | reviewed: deep call-site | emit_pipeline(self, 'manual-cycle', method='keyboard', |
| 97 | 2153 +3 | 2817 +6 | `LiveHook._cycle_word` | changed | reviewed: deep call-site | # SendInput event count and matching HKL are not a verified document |
| 98 | 2176 +0 | 2844 +2 | `LiveHook._cycle_edit_word` | added | reviewed: structural/static | if not native: |
| 99 | 2199 +1 | 2868 +1 | `LiveHook._cycle_edit_word` | changed | reviewed: structural/static | self._quarantine_native('uncertain-native-write', context) |
| 100 | 2202 +0 | 2872 +6 | `LiveHook._cycle_edit_word` | added | reviewed: structural/static | if context.process_name == 'notepad++.exe': |
| 101 | 2327 +4 | 3002 +1 | `LiveHook._correct` | changed | reviewed: deep call-site | whole = self._physical_keys_form_word(keys) |
| 102 | 2335 +1 | 3007 +1 | `LiveHook._correct` | changed | reviewed: deep call-site | native = not self.simple_mode and self._use_native_text_transport(context, name.value) |
| 103 | 2426 +1 | 3098 +1 | `LiveHook._correct_keyboard` | changed | reviewed: deep call-site | if remember and changed: |
| 104 | 2437 +0 | 3110 +3 | `LiveHook._correct_keyboard` | added | reviewed: deep call-site | self.clear_token() |
| 105 | 2477 +1 | 3152 +1 | `LiveHook._correct_native` | changed | reviewed: deep call-site | self._quarantine_native('uncertain-native-write', context) |
| 106 | 2490 +1 | 3165 +1 | `LiveHook._correct_native` | changed | reviewed: deep call-site | self._quarantine_native('uncertain-native-write', context) |
| 107 | 2532 +1 | 3207 +2 | `LiveHook._correct_native` | changed | reviewed: deep call-site | layout_confirmed = observed == hkl & 0xFFFFFFFF |
| 108 | 2535 +1 | 3211 +6 | `LiveHook._correct_native` | changed | reviewed: deep call-site | if not layout_confirmed: |
| 109 | 2570 +0 | 3252 +4 | `LiveHook.handle_keyboard` | added | reviewed: deep call-site | if (vk == CYCLE_KEYS[effective_word_key(self.settings)] and |
| 110 | 2582 +0 | 3268 +9 | `LiveHook.handle_keyboard` | added | reviewed: deep call-site | if (message in KEYUP_MESSAGES and |
| 111 | 2643 +0 | 3338 +3 | `LiveHook.handle_keyboard` | added | reviewed: deep call-site | if vk != CYCLE_KEYS[effective_word_key(self.settings)]: |
| 112 | 2645 +7 | 3341 +0 | `LiveHook.handle_keyboard` | removed | reviewed: deep call-site | if (vk == VK_PAUSE and self.pressed(VK_CONTROL) |
| 113 | 2659 +0 | 3350 +1 | `LiveHook.handle_keyboard` | added | reviewed: deep call-site | self._pause_toggle_binding = pause_toggle_binding(self.settings) |
| 114 | 2660 +0 | 3352 +8 | `LiveHook.handle_keyboard` | added | reviewed: deep call-site | if (conflicts_with_pause_toggle(chord, self._pause_toggle_binding) |
| 115 | 2689 +0 | 3389 +3 | `LiveHook.handle_keyboard` | added | reviewed: deep call-site | if vk == CYCLE_KEYS[effective_word_key(self.settings)]: |
| 116 | 2703 +1 | 3405 +1 | `LiveHook.handle_keyboard` | changed | reviewed: deep call-site | cycle_recovery = (vk == CYCLE_KEYS[effective_word_key(self.settings)] and vk != 0 |
| 117 | 2738 +1 | 3440 +1 | `LiveHook.handle_keyboard` | changed | reviewed: deep call-site | if (vk == CYCLE_KEYS[effective_word_key(self.settings)] and vk != 0 |
| 118 | 2741 +1 | 3443 +7 | `LiveHook.handle_keyboard` | changed | reviewed: deep call-site | emit_pipeline(self, 'manual-cycle', method='hotkey', reason='received', |
| 119 | 2746 +17 | 3453 +0 | `LiveHook.handle_keyboard` | removed | reviewed: deep call-site | manual_vk = MANUAL_KEYS[self.settings.manual_key] |
| 120 | 3005 +1 | 3696 +3 | `LiveHook.run` | changed | reviewed: structural/static | self.emit("paused" if self.paused else "ready", |
| 121 | 3086 +1 | 3779 +1 | `TypeBridgeGui.__init__` | changed | reviewed: structural/static | if DEBUG_WORDS_ENABLED and not SIMPLE_SWITCHER_MODE: |
| 122 | 3150 +2 | 3843 +3 | `TypeBridgeGui.__init__` | changed | reviewed: structural/static | # The two legacy values remain in saved settings for old builds only. |
| 123 | 3156 +1 | 3849 +0 | `TypeBridgeGui.__init__` | removed | reviewed: structural/static | self.cycle_key = ChoiceVar(value=settings.cycle_key) |
| 124 | 3163 +2 | 3855 +0 | `TypeBridgeGui.__init__` | removed | reviewed: structural/static | self.experimental_password_guard = tk.BooleanVar( |
| 125 | 3192 +0 | 3884 +5 | `TypeBridgeGui.__init__` | added | reviewed: structural/static | elif effective_word_key(settings) != 'Отключено': |
| 126 | 3291 +3 | 3987 +2 | `TypeBridgeGui._build` | changed | reviewed: structural/static | # Keep conservative in the engine and serialized profile; the user |
| 127 | 3300 +5 | 3995 +1 | `TypeBridgeGui._build` | changed | reviewed: structural/static | # Password detection is internal, not an ordinary user preference. |
| 128 | 3311 +3 | 4002 +5 | `TypeBridgeGui._build` | changed | reviewed: structural/static | ttk.Label(cycle_row, text="Переключить раскладку слова:").pack(side="left") |
| 129 | 3338 +8 | 4031 +10 | `TypeBridgeGui._build` | changed | reviewed: structural/static | toggle_row = ttk.Frame(shortcuts) |
| 130 | 3473 +0 | 4169 +36 | `TypeBridgeGui.restore_clipboard_backup` | added | reviewed: structural/static | def restore_clipboard_backup(self): |
| 131 | 3623 +0 | 4355 +5 | `TypeBridgeGui.pause` | added | reviewed: structural/static | # Pausing auto-correction must persist the user's choice while |
| 132 | 3645 +1 | 4381 +2 | `TypeBridgeGui._sync_tray_state` | changed | reviewed: structural/static | self.corrections_enabled.set(self.run_state == 'running' or |
| 133 | 3682 +4 | 4419 +1 | `TypeBridgeGui.current_settings` | changed | reviewed: structural/static | experimental_password_guard=self.search_settings.experimental_password_guard, |
| 134 | 3689 +2 | 4423 +2 | `TypeBridgeGui.current_settings` | changed | reviewed: structural/static | manual_f8=self.search_settings.manual_f8, |
| 135 | 3695 +1 | 4429 +3 | `TypeBridgeGui.current_settings` | changed | reviewed: structural/static | cycle_key=self.search_settings.cycle_key, |
| 136 | 4120 +0 | 4857 +9 | `TypeBridgeGui._drain` | added | reviewed: structural/static | # A blocked search provider cannot hold the GUI's busy flag |
| 137 | 4125 +0 | 4871 +4 | `TypeBridgeGui._drain` | added | reviewed: structural/static | if kind == 'clipboard-recovery-result': |
| 138 | 4240 +0 | 4990 +1 | `TypeBridgeGui._drain` | added | reviewed: structural/static | "restore-clipboard-backup": self.restore_clipboard_backup, |
| 139 | 4314 +1 | 5064 +1 | `TypeBridgeGui.start` | changed | reviewed: structural/static | def start(self, *, manual_only=False) -> None: |
| 140 | 4317 +1 | 5067 +2 | `TypeBridgeGui.start` | changed | reviewed: structural/static | self._starting_manual_only = bool(manual_only) |
| 141 | 4318 +0 | 5070 +6 | `TypeBridgeGui.start` | added | reviewed: structural/static | # A paused/manual-only hook already exists. The UI preference |
| 142 | 4320 +1 | 5077 +1 | `TypeBridgeGui.start` | changed | reviewed: structural/static | if hook and hook.paused != bool(manual_only): |
| 143 | 4382 +1 | 5139 +6 | `TypeBridgeGui.start` | changed | reviewed: structural/static | password_guard=(None if SIMPLE_SWITCHER_MODE else |
| 144 | 4454 +0 | 5217 +25 | `self_test` | added | reviewed: structural/static | # Validate the embedded native COM typelib and frozen multiprocessing |
| 145 | 4473 +1 | 5260 +1 | `self_test` | changed | reviewed: structural/static | and ctypes.sizeof(Input) == 40 and caret_helper_ok and uia_helper_ok, |
| 146 | 4474 +0 | 5262 +1 | `self_test` | added | reviewed: structural/static | 'uia_helper_ok': uia_helper_ok, |
| 147 | 4532 +1 | 5320 +3 | `main` | changed | reviewed: structural/static | password_guard=(None if SIMPLE_SWITCHER_MODE else |

### Committed source evolution checked

- `cc4e9b1`: fix(caret): bound helper retries and preserve worker across focus changes; change reviewed against current API and affected call-site contracts.
- `0b98d16`: fix(input): recover verified caret words after lost tracking; change reviewed against current API and affected call-site contracts.
- `83cdf63`: fix(input): harden replay ownership, password barriers and search deadlines; change reviewed against current API and affected call-site contracts.
- `ca4becf`: fix(input): preserve modifier releases and sync project status; change reviewed against current API and affected call-site contracts.
- `9b19a30`: fix(tray): shorten scoped correction warning and expire menu notice; change reviewed against current API and affected call-site contracts.

### Confirmed B-01: browser selection lost in SIMPLE_SWITCHER_MODE

`test_deep_native_browser_pause_20261107.py::BrowserSimplePauseRegression::test_reader_refusal_does_not_delete_existing_selection_in_simple_mode` is RED on current code: synthetic Chrome text `PREFIX ch, SUFFIX`, selected `ch,`, `read_selected_word()` refused; remembered word `ch,` survives; fallback `_cycle_word(selection_checked=True)` skips caret proof in simple mode and sends a destructive generic packet. Actual model becomes `PREFIבית SUFFIX` rather than staying unchanged. Root paths: `_cycle_browser_selection()` at ~2546-2565 and `_cycle_word()` at ~2761-2777. Proposed production fix (prime-owned): refuse generic browser fallback on selected-word read refusal in simple mode; more generally require collapsed caret/word readback before generic Backspace in all modes, while leaving deliberate password-policy opt-out unchanged.

### B-01 expanded replay evidence and priorities

- Three independent red scenarios in `test_deep_native_browser_pause_20261107.py`: Chrome, Firefox, and Chrome after Space (remembered suffix). Each supplies a noncollapsed synthetic selection and a refused selected-word provider. All three fail the unchanged-document safety assertion. Expected: no SendInput. Actual: nonzero packet and destruction of the existing selection/preceding characters. No real browser was opened.
- Parallel control run: `test_pause_no_keyup_20261107.py`, `test_browser_selected_pause_20261107.py`, `test_p0d_generic_pause_20261107.py`, `test_notepad_barrier_replay_20261107.py`, `test_hook_cycle_offhook.py` passed **91 tests + 35 subtests** in the run including the three red regression tests. These check Pause missing KEYUP/repetition, normal verified Chrome selection replacement, focus/replay failures, and after-Space behavior on mock editors. They do not establish browser/Win32 integration.
- Scope count reconciled with Git: **147 dirty hunks** (71 mapped to priority method/call-site paths, 76 structural/static); five committed `typebridge_app.py` revisions from Nov 1–5, shown above. Report statuses are static review statuses; only the model cases described in this report were exercised. Five commits cover caretaker retry (`cc4e9b1`), caret word recovery (`0b98d16`), queue/barrier safety (`83cdf63`), modifier release (`ca4becf`) and GUI tray warning (`9b19a30`).
- Production `typebridge_app.py` is under prime-agent ownership and remained read-only. No proposed fix has been applied there; B-01 stays red pending coordination.

### Per-hunk ledger of committed typebridge_app.py changes (Nov 1–5)

The historical revisions below were mapped to the containing function in their own committed version, then checked against the current caller and gate semantics. The status is static review; integration has not been performed.

| Commit | New lines in commit | Scope | Review status |
|---|---|---|---|
| `cc4e9b1` | 3593 +8 | `TypeBridgeGui._update_tray` | static contract |
| `0b98d16` | 69 +1 | `module/global` | static contract |
| `0b98d16` | 2004 +58 | `LiveHook._try_caret_recovery` | static contract |
| `0b98d16` | 2081 +2 | `LiveHook._cycle_word` | priority call-site |
| `0b98d16` | 2093 +2 | `LiveHook._cycle_word` | priority call-site |
| `0b98d16` | 2266 +4 | `LiveHook._correct` | priority call-site |
| `0b98d16` | 2277 +4 | `LiveHook._correct` | priority call-site |
| `0b98d16` | 2701 +10 | `LiveHook.handle_keyboard` | priority call-site |
| `0b98d16` | 2713 +4 | `LiveHook.handle_keyboard` | priority call-site |
| `83cdf63` | 216 +1 | `module/global` | static contract |
| `83cdf63` | 809 +1 | `LiveHook._context` | priority call-site |
| `83cdf63` | 862 +1 | `LiveHook._native_recovery_key_safe` | static contract |
| `83cdf63` | 1003 +19 | `LiveHook._send_guarded_inputs` | priority call-site |
| `83cdf63` | 1041 +2 | `LiveHook._send_guarded_inputs` | priority call-site |
| `83cdf63` | 1051 +21 | `LiveHook._send_guarded_inputs` | priority call-site |
| `83cdf63` | 1072 +0 | `LiveHook._send_guarded_inputs` | priority call-site |
| `83cdf63` | 1080 +19 | `LiveHook._replay_modifiers` | static contract |
| `83cdf63` | 1115 +4 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1122 +1 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1128 +1 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1131 +1 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1133 +6 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1140 +1 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1150 +2 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1155 +1 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1161 +2 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1163 +0 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1165 +2 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1170 +1 | `LiveHook._schedule_native` | priority call-site |
| `83cdf63` | 1197 +3 | `LiveHook._schedule_keyboard` | priority call-site |
| `83cdf63` | 1204 +5 | `LiveHook._schedule_keyboard` | priority call-site |
| `83cdf63` | 1251 +1 | `LiveHook._mark_keyboard_trigger_mutation` | static contract |
| `83cdf63` | 1257 +1 | `LiveHook._confirm_keyboard_trigger_acceptance` | static contract |
| `83cdf63` | 1261 +11 | `LiveHook._browser_field_changed_after_submission` | static contract |
| `83cdf63` | 1284 +28 | `LiveHook._postwrite_permitted` | static contract |
| `83cdf63` | 1313 +13 | `LiveHook._drain_native_input` | priority call-site |
| `83cdf63` | 1331 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1333 +10 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1354 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1384 +12 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1401 +2 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1405 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1422 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1428 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1438 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1441 +4 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1452 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1454 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1461 +1 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1465 +2 | `LiveHook._drain_owned_native_input` | priority call-site |
| `83cdf63` | 1504 +2 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1507 +4 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1521 +7 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1530 +3 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1558 +5 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1565 +1 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1568 +2 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1572 +2 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1578 +1 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1586 +1 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1589 +2 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1591 +0 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1593 +2 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1598 +1 | `LiveHook.restore_native_pending` | static contract |
| `83cdf63` | 1926 +6 | `LiveHook._finish_early_layout` | static contract |
| `83cdf63` | 1937 +1 | `LiveHook._finish_early_layout` | static contract |
| `83cdf63` | 1997 +1 | `LiveHook._switch_direct` | static contract |
| `83cdf63` | 2074 +5 | `LiveHook._cycle_word` | priority call-site |
| `83cdf63` | 2084 +2 | `LiveHook._cycle_word` | priority call-site |
| `83cdf63` | 2145 +5 | `LiveHook._cycle_edit_word` | static contract |
| `83cdf63` | 2155 +3 | `LiveHook._cycle_edit_word` | static contract |
| `83cdf63` | 2334 +7 | `LiveHook._correct_keyboard` | priority call-site |
| `83cdf63` | 2348 +3 | `LiveHook._correct_keyboard` | priority call-site |
| `83cdf63` | 2426 +2 | `LiveHook._correct_native` | priority call-site |
| `83cdf63` | 2433 +13 | `LiveHook._correct_native` | priority call-site |
| `83cdf63` | 2447 +5 | `LiveHook._correct_native` | priority call-site |
| `83cdf63` | 2457 +1 | `LiveHook._correct_native` | priority call-site |
| `83cdf63` | 2460 +2 | `LiveHook._correct_native` | priority call-site |
| `83cdf63` | 2467 +1 | `LiveHook._correct_native` | priority call-site |
| `ca4becf` | 70 +2 | `module/global` | static contract |
| `ca4becf` | 1321 +1 | `LiveHook._drain_native_input` | priority call-site |
| `ca4becf` | 2299 +6 | `LiveHook.handle_keyboard` | priority call-site |
| `9b19a30` | 887 +1 | `LiveHook._quarantine_native` | static contract |
| `9b19a30` | 892 +2 | `LiveHook._quarantine_native` | static contract |
| `9b19a30` | 2785 +1 | `TypeBridgeGui.__init__` | static contract |
| `9b19a30` | 3267 +6 | `TypeBridgeGui._update_tray` | static contract |
| `9b19a30` | 3354 +4 | `TypeBridgeGui._tray_state` | static contract |
| `9b19a30` | 3946 +1 | `TypeBridgeGui._drain` | static contract |
| `9b19a30` | 3948 +2 | `TypeBridgeGui._drain` | static contract |
| `9b19a30` | 3960 +1 | `TypeBridgeGui._drain` | static contract |
| `9b19a30` | 3973 +1 | `TypeBridgeGui._drain` | static contract |

Historical commit hunks accounted: **91** across 5 commits.

### B-01 closure status (prime-owned production fix)

The initial three synthetic browser cases were RED before prime changed `typebridge_app.py` and the UIA caret reader. After the prime patch, the original three plus five additional scenarios (legitimate collapsed caret with two trailing spaces, changed element Runtime ID, selection appearing between reads, A→B→A browser revision, unavailable caret provider) all pass in the dedicated `test_deep_native_browser_pause_20261107.py`: **8/8 passed**. Combined with `test_simple_selected_word_20261107.py`, `test_native_uia_caret_20261107.py`, `test_pause_no_keyup_20261107.py`, `test_browser_selected_pause_20261107.py`, and `test_p0d_generic_pause_20261107.py`: **81 passed, 11 subtests passed**. Source patch belongs exclusively to prime; worker changed only dedicated test and report. B-01 is model-verified closed pending real browser acceptance; no real foreground/hook operations were done.
