# TypeBridge field-check matrix — 2026-11-06

**Historical strict-policy matrix. Superseded on 2026-11-06 by the user's
explicit decision to allow unknown fields.** The application no longer requires
fresh UIA metadata, addressability or runtime identity before keyboard correction.
Only explicit password evidence blocks it. See
[current policy](USER-FIELD-POLICY-20261106.md). The table below is retained as
an audit record of the rejected policy, not the current application behavior.

## Scope and evidence

Static source inspection and mocked Python unit tests only. **No physical browser, Spotify, real UI Automation provider, live hook, or keyboard input was exercised.** `experimental_password_guard` defaults to `false` (`typebridge_app.py`, `Settings`). `staged_password_guard()` always constructs `PasswordFieldGuard(require_element_id=True)`; **OFF** sets `native_fast_path=True`, **ON** sets `native_fast_path=False` (`typebridge_app.py:336-344`). Turning the option OFF does **not** remove the guard for shared/browser/custom controls.

The matrix describes text-edit permission from `LiveHook._context(for_layout=False)`. Direct layout-only commands (`layout_context`) have a different, intentionally non-text-writing gate; passing that gate does not certify an editable text field.

## Exact code-policy matrix

| Focused target and metadata | OFF (`experimental_password_guard=false`) | ON (`true`) | Rationale |
| --- | --- | --- | --- |
| Native `Edit` / `RichEdit20W` / `RichEdit50W` / `RichEditD2DPT`, not browser/WebView, style without `ES_PASSWORD`; UIA unknown/unavailable | **Permitted** via synchronous native-style fast path | **Permitted only after fresh helper metadata**, which can itself verify native style without UIA; helper failure/timeout denies | `_context` native fast path; PowerShell `NativeState` can issue `win32:<focus>` independently of UIA availability |
| Same native classes, `ES_PASSWORD` style set | **Denied** | **Denied** | `_context` checks Win32 style before metadata (`typebridge_app.py:783-786`) |
| Native non-password with helper reporting `password` or `unknown` | **Permitted** through native-style path | **Denied** | OFF intentionally does not consult helper for certified native controls; helper's actual native style check is separate |
| Native non-password with fresh `safe`, valid matching `win32:` identity | **Permitted** | **Permitted** | `PasswordFieldGuard.permit_revision` and `_context` |
| Chrome/Chromium browser or WebView wrapper, including an `Edit` child, UIA missing, unknown or password | **Denied** | **Denied** | Browser host/class excludes native fast path, and guard requires element identity |
| Chrome wrapper, verified fresh `safe` editable UIA element and valid `uia:` runtime identity | **Permitted** | **Permitted** | `password_fields.py` focused-element metadata and `_context` |
| Chrome wrapper, explicit `IsPassword=false` but UIA text target **unaddressable** (`safe`, `addressable=0`) | **Denied** | **Denied** | Seven-field helper protocol separates field safety from editability; `permit_revision` requires addressable |
| Chrome wrapper with only `win32:<focus>` and purported `safe` | **Denied** | **Denied** | `_context` requires UIA element ID for nonnative/browser targets |
| Chrome wrapper, UIA element unknown, unsupported, password, missing runtime ID, invalid ancestry or target mismatch | **Denied** | **Denied** | Unknown fails closed; native HWND alone cannot identify a DOM field |
| `safe` helper response started before focus invalidation, or older than 500 ms when received | **Denied where helper is required** | **Denied** | `PasswordFieldGuard.ingest` rejects stale/invalidation-order responses |
| Previously verified target after 500 ms without refresh | **Denied where helper is required** | **Denied** | `permit_revision` expires metadata after 0.5 s |
| New DOM input at same HWND, different UIA runtime ID | Old source scope **invalidated** | Old source scope **invalidated** | Snapshot `revision` increments with `element_id`; word/native scope includes revision |

In the PowerShell UIA helper (`password_fields.py:81-135, 200-290`), a shared browser `Edit` or `Document` requires a writable `ValuePattern` or an available `TextPattern`; an explicitly writable focused/enabled `ComboBox` is also accepted. Selection-only or read-only combo boxes, custom panes and other unknown shared elements are unaddressable. Ancestors are checked for `IsPassword`; UIA errors and missing identity yield unknown. A valid `IsPassword=false` check can yield `state=safe, addressable=0`, meaning the field is known unprotected but its text target cannot be safely addressed. The native style shortcut is not applied to browser/WebView processes or Chromium-style windows (`r04_text_target.py:7-28`, PowerShell `SharedScope`). These are model/code-policy statements, **not a provider compatibility guarantee**.

## Timeouts, source identity and failures

- `PasswordFieldGuard` polls metadata at most every 100 ms; its `permit_revision` snapshot TTL is 500 ms. A production off-hook `revalidate` waits up to 350 ms for metadata sampled after the request, except the verified native `win32:` shortcut. A helper response older than 500 ms is rejected; a hung helper results in unknown, with watchdog timeout 3 s after first response or 10 s during startup (`password_fields.py:286-292, 352-442, 474-480`).
- `_word_scope` includes `(top, focus, thread_id, process_name, layout_window, password_revision)`; browser `_native_scope` additionally includes `_browser_field_revision` (`typebridge_app.py:2021-2030`). An identical HWND with a different DOM input is **not** an identical mutation target.
- `_schedule_native` preflights focus/source scope and HKL in its worker. An exception **before `job()` starts** aborts locally; an arbitrary exception after entering `job()` or uncertainty in generic `SendInput`/replay remains a global transport fault. A demonstrably HWND-addressed `NativeEditBridge` write with uncertain readback instead quarantines only its source editor and preserves other text fields; recognized zero-event replay failures can also be scoped. The local stop does not certify the original word's contents, and never retries it automatically.
- Browser `_browser_field_changed_after_submission` and `_postwrite_permitted` recheck source revision, focus and HKL. Neither UIA nor `SendInput` can atomically bind an input event to a DOM node during arbitrary focus changes (`typebridge_app.py:1280-1331`). This residual focus race remains **unproven with physical typing**.
- Explicit in-memory recovery of held keys is limited to the original source scope, language, layout and modifiers, and expires after 180 s; it does not automatically replay into another field (`typebridge_app.py:1536-1669`).

## Verification

`python -m unittest -v test_field_states_20261106.FieldStateMatrixTests`

The new tests model both ON/OFF modes, native password style, browser wrappers, unknown/password/malformed target metadata, delayed UIA responses, same-HWND DOM revisions, and preflight versus post-job exceptions. The tests use mocked Win32/UIA metadata and existing fixtures; they **cannot certify real Chrome/Spotify behavior or physical keystroke safety**.

### Seven-field producer/consumer contract

The production helper now emits `top|focus|state|started|reason|element_id|addressable` (`password_fields.py:290`). `state=safe` means the password check and ancestry checks passed; `addressable=0` means the target is unaddressable and explicitly denies text mutation (`Snapshot.addressable`, `permit_revision`). The consumer retains compatibility with legacy six-field test metadata, which implicitly sets `addressable=true` for `safe`, so these synthetic fixtures do not prove real provider addressability. `test_seven_field_safe_but_unaddressable_is_denied` exercised both ON and OFF Chrome-wrapper cases against the newly merged implementation.
