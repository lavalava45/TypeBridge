"""Run explicitly admitted GUI unittest IDs on a private, inactive Win32 desktop.

The parent never switches desktops, sends input, changes HKL, or installs hooks.
Use ONLY audited test IDs, not a whole test module or unittest discovery.
"""
import argparse
import ast
import concurrent.futures
import hashlib
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs' / 'test-logs'
USER32 = ctypes.WinDLL('user32', use_last_error=True)
USER32.CreateDesktopW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   ctypes.c_void_p, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_void_p)
USER32.CreateDesktopW.restype = wintypes.HANDLE
USER32.CloseDesktop.argtypes = (wintypes.HANDLE,)
USER32.CloseDesktop.restype = wintypes.BOOL

# Explicit safety admission: only a test method that constructs a withdrawn Tk
# root (or i18n's Tk-backed setUp), with mocked hooks/input and temp user data.
# Not a complete list of potentially runnable tests in these modules.
ELIGIBLE = {
    'test_app_exclusions': ['HiddenExclusionsGuiTests.test_categories_custom_names_and_cancelled_save_without_mapping'],
    'test_caps_startup': ['GuiTests.test_one_checkbox_no_stop_or_pause_label_and_settings',
                          'GuiTests.test_correction_preference_saved_and_restored_without_installing_hook'],
    'test_case_direct_hotkeys': ['DictionaryGuiTests.test_gui_adds_reloads_and_deletes_case_sensitive_words_and_exclusions'],
    'test_clipboard_selection': ['HiddenPreviewTests.test_only_confirmation_opens_browser_and_settings_change_cancels'],
    'test_clipboard_tools': ['HiddenGuiTests.test_settings_and_picker_fit_without_mapping_or_native_access'],
    'test_dictionary_import': ['BundledAndGuiTests.test_hidden_window_one_click_builtin_and_custom_preview'],
    'test_early_keyboard_probe': ['KeyboardProbeTests.test_opening_hidden_trial_does_not_send_input_install_hooks_or_focus_fields'],
    'test_early_native_edit': ['TrialWindowTests.test_opening_withdrawn_trial_does_not_create_or_start_hook'],
    'test_input_journal': ['SettingsAndGuiTests.test_hidden_viewer_and_tray_indicator'],
    'test_manual_only_lifecycle_20261106': ['ManualOnlyLifecycleTests.test_disabled_auto_schedules_paused_hook_and_preserves_checkbox'],
    'test_option_labels': ['OptionLabelsTests.test_positive_rare_checkbox_preserves_existing_config_and_saves_inverse',
                           'OptionLabelsTests.test_single_early_checkbox_drives_both_legacy_fields_and_roundtrips'],
    'test_p0_guard_transport_integration': ['P0GuardTransportIntegration.test_hidden_field_guard_option_retains_saved_policy_without_running_hook'],
    'test_pause_toggle_settings_20261107': ['TogglePauseSettingsTests.test_gui_exposes_editable_combo_and_current_settings'],
    'test_quick_search': ['HiddenSearchGuiTests.test_provider_editing_and_localization_without_focus_or_search'],
    'test_r16_4g_actual_gui': ['ActualSettingsPanelTest.test_fourth_v2_language_appears_and_is_editable_without_code_registration'],
    'test_r16_activation': ['ActivationTests.test_gui_checked_subset_saves_and_preserves_default_order',
                             'ActivationTests.test_gui_rejects_missing_hkl_before_writing_settings_or_registry'],
    'test_r16_active_settings': ['ActiveSettingsTests.test_gui_settings_save_path_keeps_loaded_active_subset'],
    'test_r16_runtime_packages': ['RuntimePackageTests.test_gui_starts_with_corrupt_disabled_es_and_preserves_its_personal_words',
                                   'RuntimePackageTests.test_gui_refuses_ru_f3_before_settings_or_registry_write',
                                   'RuntimePackageTests.test_gui_shows_installed_languages_and_saves_selection_without_hooks'],
    'test_settings_copy': ['SettingsCopyTests.test_visibility_changes_immediately_without_starting_services'],
    'test_settings_widgets': ['SettingsGeometryTests.test_page_content_is_scrollable_in_withdrawn_window'],
    'test_snippets': ['GuiTests.test_crud_and_apply_multiline_rule_in_separate_window'],
    'test_ui_i18n': ['HiddenWindowTests.test_status_and_choice_vars_keep_original_data_across_languages',
                     'HiddenWindowTests.test_all_windows_switch_in_place_without_changing_words_templates_or_enabling_services',
                     'HiddenWindowTests.test_language_save_failure_keeps_previous_locale'],
}

# Expanded admission was audited from test methods AND class setUp/setUpClass.
# All remaining methods in the 22 explicit modules use model/mock APIs, temp
# files or local test dictionaries. Do not widen this to new files implicitly.
# A bounded Win32 DPAPI roundtrip on synthetic text in a TemporaryDirectory is
# admitted for this exact reviewed test. It does not read user documents,
# clipboard, other windows or TypeBridge's persistent journal.
SYNTHETIC_DPAPI_ID = 'test_input_journal.StoreTests.test_real_windows_dpapi_roundtrip_with_synthetic_data_only'
EXPANDED_BLOCKED = {}
AUDITED_SOURCE_SHA256 = {
    'test_app_exclusions': '7710bb5d97aeeaad5b519088feb3bda40d9c0d0c879db74b26a6f9b5f81a9504',
    'test_caps_startup': '7c69b8c1e98c4144aeec21a2c594923d8dcaf603c58cebf1dc5143b8a3290c5e',
    'test_case_direct_hotkeys': '144d29d027e09b0f1fbe49fa6d9828bb8a0b863eefe95e453de305bbb7de7c11',
    'test_clipboard_selection': 'cc0d1776157d98319b6086efdb8407905dba3a7b72b6986b4e4fedaf90a0908c',
    'test_clipboard_tools': '22d3d33c2d8584d7923fc463eb7ce92a485c94107d4480a79ce6c6beb8e0bde8',
    'test_dictionary_import': '7ccd3a72aa8869662d8724dba554c09717a05c6245e3894a17479b1997390483',
    'test_early_keyboard_probe': '418187c032a98a08f91e3c7c18242539c5f8bf76b4213279dd7d5f003dbcc202',
    'test_early_native_edit': 'e314d7add51a7737182e77591f008650ace8e015abe4122d92e6acd9fbfdb6d1',
    'test_input_journal': '552360ae04baabe744d28862b61f923efdfd396486e9b2afa3c53a51c7d00a22',
    'test_manual_only_lifecycle_20261106': 'a8e2d566cd3a9cbae4bdb78e726e05005ecb5f0a157a1e59b967ce9ad29f3084',
    'test_option_labels': '78fdd345ed76b80b3c99364be2d9e2ca84cbb1c8de8b68fd0fc388bdb0caffc0',
    'test_p0_guard_transport_integration': '0d35213bce3cfdbb0f46958bd99fb33443a6d568da3c78b58122d62be5a1ef3f',
    'test_pause_toggle_settings_20261107': '320111276f928de9cef2a5df1b0cf88c6ba3ae3ef700c7ef3281518750892b92',
    'test_quick_search': '5834019b5d40faba64bfa1a8e43f1d0605a8118f8fda6b79654bfa222e44455b',
    'test_r16_4g_actual_gui': '4a73204e2000b39cd38081ca979d9997f857e1888efed56eda0b52ea753d0d9c',
    'test_r16_activation': '18d1ac1d9575ec6b447a34b9255ebf9f8e202bc4a2b9ac6d19866380cbc6c402',
    'test_r16_active_settings': 'e08024ff2f0dfb4d065920a423161896a71916211ed7eb9d41b734e742754f0e',
    'test_r16_runtime_packages': '52cb10c5720bfcbd2030d8f69535bc8b868ebacee34fcdc5cbb9410ae547c9b2',
    'test_settings_copy': '8edd6b041a8b52f0ef1c12b72688e210ba3c11dc715926b7a630d0715fdb12b6',
    'test_settings_widgets': '6b33fe6fc514c2fde95c40b5b8da6fb57668cc27d9381d2824b98593b58c520c',
    'test_snippets': '7d86c74cffd9a56ec97286ac628d5cdc1a6a0ee237bdfe1804aff81a7d3c3d3b',
    'test_ui_i18n': 'f50b640571a36d0e53cbc3ff9ad64740e9978cc63c5aab8b0224799cb96c46cd',
}
HEAVY_CLASSES = ('test_caps_startup.CapsTests', 'test_snippets.HookTests')


def expanded_inventory():
    """Static inventory only. Never import a test module during discovery."""
    result = []
    for module in sorted(ELIGIBLE):
        path = ROOT / (module + '.py')
        if hashlib.sha256(path.read_bytes()).hexdigest() != AUDITED_SOURCE_SHA256[module]:
            raise RuntimeError(f'Unreviewed test-source change: {module}; re-audit before running')
        source = path.read_text(encoding='utf-8-sig')
        lines = source.splitlines()
        tree = ast.parse(source, filename=str(path))
        gui = set(ELIGIBLE[module])
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            fixtures = [method for method in cls.body
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and method.name in ('setUp', 'setUpClass')]
            setup = '\n'.join('\n'.join(lines[method.lineno-1:method.end_lineno])
                              for method in fixtures)
            for method in cls.body:
                if not isinstance(method, ast.FunctionDef) or not method.name.startswith('test_'):
                    continue
                local = cls.name + '.' + method.name
                identity = module + '.' + local
                body = '\n'.join(lines[method.lineno-1:method.end_lineno])
                combined = body + '\n' + setup
                category = 'gui-private' if local in gui or 'tk.Tk(' in combined else 'mock-private'
                signals = [label for label, words in (
                    ('SendInput', ('SendInput',)),
                    ('hooks', ('SetWindowsHookEx', 'RegisterHotKey')),
                    ('registry', ('winreg.', 'write_registration', 'read_registration')),
                    ('clipboard', ('OpenClipboard', 'EmptyClipboard', 'clipboard', 'Clipboard')),
                    ('foreground', ('GetForegroundWindow', 'SetForegroundWindow')),
                    ('fixtures', ('Mock(', 'patch(', 'tempfile', 'TemporaryDirectory')),
                ) if any(word in combined for word in words)]
                reason = EXPANDED_BLOCKED.get(identity)
                result.append({'id':identity, 'module':module, 'line':method.lineno,
                               'category':category, 'signals':signals,
                               'setup': [item.name for item in fixtures],
                               'admission':'blocked' if reason else 'eligible',
                               'reason':reason or ('Admitted real DPAPI synthetic-only temp-path roundtrip; no external document/clipboard access'
                                   if identity == SYNTHETIC_DPAPI_ID else 'Reviewed synthetic/model method; inactive desktop')})
    return result


def run_on_private_desktop(args, *, timeout, desktop, env):
    startup = subprocess.STARTUPINFO()
    startup.lpDesktop = desktop
    started = time.monotonic()
    try:
        child = subprocess.run(args, cwd=ROOT, env=env, startupinfo=startup,
                               creationflags=subprocess.DETACHED_PROCESS,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout)
        return ('passed' if child.returncode == 0 else 'failed', child.returncode,
                child.stdout.decode('utf-8', errors='replace'), round(time.monotonic() - started, 3))
    except subprocess.TimeoutExpired as e:
        return 'timeout', None, (e.stdout or b'').decode('utf-8', 'replace') + '\nTIMEOUT\n', round(time.monotonic() - started, 3)
    except Exception as e:
        return 'runner-error', None, repr(e) + '\n', round(time.monotonic() - started, 3)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--probe-only', action='store_true')
    p.add_argument('--timeout', type=int, default=12)
    p.add_argument('--modules', nargs='+', help='Subset from the explicit GUI allowlist')
    p.add_argument('--test-ids', nargs='+', help='Only exact audited unittest method IDs; never a pattern')
    p.add_argument('--expanded', action='store_true', help='All audited methods in the original 22 modules')
    p.add_argument('--remaining-only', action='store_true', help='Expanded tests excluding original 29 GUI IDs')
    p.add_argument('--batch-heavy', action='store_true', help='Run the two audited heavy fixture classes once each')
    p.add_argument('--workers', type=int, default=3)
    a = p.parse_args()
    if not 2 <= a.timeout <= 180 or not 1 <= a.workers <= 4:
        p.error('timeout must be 2..180 seconds and workers 1..4')
    name = 'TypeBridgeGuiAudit-' + uuid.uuid4().hex[:16]
    # No DESKTOP_SWITCHDESKTOP (0x0100) right is requested.
    handle = USER32.CreateDesktopW(name, None, None, 0, 0x00FF, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), 'CreateDesktopW failed')
    out = OUT / ('gui-private-matrix-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    desktop = 'WinSta0\\' + name
    results = []
    inventory = expanded_inventory() if a.expanded else []
    if a.test_ids:
        if not a.expanded:
            p.error('--test-ids requires --expanded')
        known = {item['id'] for item in inventory}
        unknown = set(a.test_ids) - known
        if unknown:
            p.error('unknown or not safety-reviewed IDs: ' + ', '.join(sorted(unknown)))
    (out / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        probe = [sys.executable, '-c',
                 'import tkinter as tk; from tkinter import ttk; '
                 'r=tk.Tk();r.withdraw();ttk.Style(r).theme_use();'
                 'r.update_idletasks();print("PRIVATE_TK_OK",r.tk.call("info","patchlevel"));r.destroy()']
        status, code, output, duration = run_on_private_desktop(
            probe, timeout=a.timeout, desktop=desktop, env=env)
        (out / 'tk-probe.log').write_text(output, encoding='utf-8')
        results.append({'id':'tk-probe','status':status,'exit_code':code,'duration':duration,'log':'tk-probe.log'})
        print('PROBE',status,output[:400].replace('\n', ' '),flush=True)
        if status == 'passed' and a.batch_heavy and not a.probe_only:
            # One module import and one setUpClass per heavy class. Still run
            # only explicitly inventoried classes and report every test ID.
            for group in HEAVY_CLASSES:
                command = [sys.executable, '-m', 'unittest', '-v', group]
                batch_status, returncode, output, elapsed = run_on_private_desktop(
                    command, timeout=a.timeout, desktop=desktop, env=env)
                (out / (group + '.batch.log')).write_text(output, encoding='utf-8', errors='replace')
                ids = [row['id'] for row in expanded_inventory() if row['id'].startswith(group + '.')]
                for identity in ids:
                    suffix = identity.split('.')[-1]
                    pattern = re.compile(r'^' + re.escape(suffix) + r'\s+\(.+\)\s+\.\.\.\s+(ok|FAIL|ERROR|skipped .+)$', re.MULTILINE)
                    match = pattern.search(output)
                    individual = ('passed' if match and match.group(1)=='ok' else
                                  'failed' if match else batch_status)
                    log = identity + '.log'
                    (out / log).write_text('BATCH: ' + group + '\n' + output, encoding='utf-8')
                    results.append({'id':identity,'status':individual,
                                    'duration':elapsed,'log':log,'category':'mock-private',
                                    'batch':group,'batch_status':batch_status})
                    print(individual,identity,'batch',elapsed,flush=True)
        elif status == 'passed' and a.expanded and not a.probe_only:
            original = {module + '.' + method for module, methods in ELIGIBLE.items() for method in methods}
            admitted = [row for row in inventory if row['admission']=='eligible'
                        and (not a.modules or row['module'] in a.modules)
                        and (not a.test_ids or row['id'] in a.test_ids)
                        and (not a.remaining_only or row['id'] not in original)]
            for row in inventory:
                if (row['admission']=='blocked' and (not a.modules or row['module'] in a.modules)
                        and (not a.test_ids or row['id'] in a.test_ids)):
                    results.append({'id':row['id'], 'status':'blocked','reason':row['reason'],
                                    'category':row['category']})
            def execute(row):
                identity = row['id']
                command = [sys.executable, '-m', 'unittest', '-v', identity]
                result, returncode, output, elapsed = run_on_private_desktop(
                    command, timeout=a.timeout, desktop=desktop, env=env)
                logfile = re.sub('[^A-Za-z0-9_.-]', '_', identity) + '.log'
                (out / logfile).write_text(output, encoding='utf-8', errors='replace')
                return {'id':identity,'status':result,'exit_code':returncode,
                        'duration':elapsed,'log':logfile,'category':row['category']}
            with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
                futures = [pool.submit(execute, row) for row in admitted]
                for future in concurrent.futures.as_completed(futures):
                    item = future.result()
                    results.append(item)
                    print(item['status'], item['id'], item['duration'], flush=True)
        elif status == 'passed' and not a.probe_only:
            for module, names in ELIGIBLE.items():
                if a.modules and module not in a.modules:
                    continue
                for method in names:
                    identity = module + '.' + method
                    codeargs = [sys.executable, '-m', 'unittest', '-v', identity]
                    result, returncode, text, elapsed = run_on_private_desktop(
                        codeargs, timeout=a.timeout, desktop=desktop, env=env)
                    logfile = re.sub('[^A-Za-z0-9_.-]', '_', identity) + '.log'
                    (out / logfile).write_text(text, encoding='utf-8', errors='replace')
                    results.append({'id':identity,'status':result,'exit_code':returncode,
                                    'duration':elapsed,'log':logfile})
                    print(result,identity,elapsed,flush=True)
    finally:
        USER32.CloseDesktop(handle)
        summary = {'desktop':name,'switched':False,'expanded':a.expanded,
                   'remaining_only':a.remaining_only,
                   'inventory_count':len(inventory),
                   'eligible_module_count':len(ELIGIBLE),
                   'eligible_test_count':sum(map(len,ELIGIBLE.values())),
                   'results':results}
        (out / 'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print('LOG_DIR',out,flush=True)
    return 0 if results and all(r['status'] in ('passed','blocked') for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
