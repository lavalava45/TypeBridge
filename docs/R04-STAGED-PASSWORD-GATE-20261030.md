# TypeBridge: R04 staged password-field hardening, 2026-10-30

## Release-candidate change: unconditional conservative field scope (31 October)

Earlier opt-in-only paragraphs below describe historical milestones. They no
longer describe the **current source** default: `r04_text_target.py` now
requires one of `EDIT`, `RichEdit20W`, `RichEdit50W`, `RichEditD2DPT`, or
Scintilla *owned and hosted by Notepad++*. The real `LiveHook.context()`
rejects browser/WebView processes and all unknown/custom controls BEFORE
starting recognition, snippet matching, manual correction or hotkeys.
This is enforced regardless of `experimental_password_guard`, whose native
UIA worker can still restrict supported controls further. Unverified DOM
fields within one HWND cannot race into a text mutation because **no DOM field
is ever an allowed target**. Native EDIT/RichEdit are synchronously checked
for `ES_PASSWORD` before the context is exposed, including at each worker
revalidation. Normal physical input passes through without editing.

Consequence: browser/Electron/custom-editor autocorrection is deliberately
disabled by default, even for ordinary unmasked text. No exception whitelist
for individual sites is permitted. Separate clipboard/global-hotkey routes,
malformed native controls and all real physical acceptance remain distinct
risks; this is not a blanket guarantee for every password manager or editor.

Status: **SOURCE-ONLY, OPT-IN FOR UNIT/MODEL FIXTURES; NOT ENABLED IN GUI**.
Do not mark R04 or physical No01 complete. This work deliberately prioritizes
rollbacks and backward compatibility after prior real-input regressions.

## Preservation and rollback

The independent prechange full-source SHA-verified snapshot is at
`E:\Downloads\devspace-test\typebridge\_r16_backups\20261030-r04-prechange`
(274 files, `prechange-source.zip` and `manifest.json`). Prechange Git commit:
`ed06635` in both local and remote branch
`safety/r16-pre-r04-20261030`. All experimental edits are isolated on
`feature/r04-password-guard-staged-20261030`. Neither branch is a public
release or replaces the current `main`.

## Implemented — incremental, default behavior unchanged

1. Native EDIT password-style recognition in `LiveHook.context()` also covers
   `RichEdit20W`, `RichEdit50W`, `RichEditD2DPT`, matching preexisting native
   style detection in the out-of-process metadata helper. Password-style
   fields are rejected before language conversion or native mutation.
2. A `password_guard=` parameter permits **explicit opt-in injection** of
   the existing `PasswordFieldGuard` into the **real** `LiveHook` constructor.
   The GUI and normal startup NEVER supply this parameter. The default is
   `None`: users see no changed browser behavior and no new UIA process.
3. When an explicit guard is supplied, `context()` rejects `password`,
   `unknown`, expired, and mismatched metadata; the guard runs in its own
   worker and is never queried using UIA inside low-level keyboard callback.
   Context tracks the metadata revision; native and word scopes compare it.
   This prevents stale snapshot reuse at before-write focus checks.
4. Mouse boundaries and keyboard-only focus navigation (Tab, Enter, Escape,
   navigation keys and non-modifier keys with Ctrl/Alt/Win) invalidate the
   explicit guard before corrections. User keys continue to be passed through
   unmodified when metadata is uncertain; text replacement fails closed.
5. The experimental helper is started only inside `run()` when explicitly
   injected, and is closed after hooks are unregistered in `finally`.
   A helper shutdown error cannot strand the global hooks installed.

## Verified and remaining limits

Tests in `test_r04_staged_hook_integration.py` exercise the REAL LiveHook
constructor with mocked Win32 APIs (not `LiveHook.__new__`), actual
`context()`, `handle_keyboard`, `handle_mouse` and `SendInput` preflight.
The legacy `test_password_fields.py` expectation that injected guard had no
effect is updated; old unguarded context remains explicitly covered.

*No actual global keyboard hook, foreground focus change, physical keyboard
typing, password entry, user settings, or production EXE rebuild occurred.*

**Not proven:** browser HTML password detection for every UIA provider,
same-HWND asynchronous script-driven DOM focus, entire password-manager
ecosystem, UIA helper startup latency, lost physical keystrokes, real
Undo/Redo, and actual No01 before-Space behavior. An installed TypeBridge
process may still use the older default behavior. This is intentionally
NOT equivalent to an R04 fix in production.

## Next gated steps

1. Isolate an offline metadata-provider simulation with same-HWND script
   transitions; measure and cap snapshot latency without blocking callbacks.
2. Complete real password-field and safe EDIT observations in a dedicated
   test control (on the user's live desktop **only with separate consent**).
3. Audit every route to text mutation: boundary, live early, native early,
   manual cycle, direct language shortcut, snippet, clipboard and diagnostics.
4. Run native private-desktop regressions, then physically test exact input,
   focus, fast tail, spacing, HKL and single Undo in an empty test editor.
5. Only after all of those gates, decide whether to enable a strict guard
   for the user by default. Unknown UIA states must always pass typing
   through while refusing corrective mutations.

**Until then:** R01/R02/R03 remain only model-validated where explicitly
tested; R04 is **OPEN**; No01 is **BLOCKED**. Do not claim the source-only
staged guard protects current browser passwords or is release-ready.

## Additional source-only hardening, 30 October 2026 (same-HWND gate)

The opt-in helper now includes the opaque UI Automation `GetRuntimeId()` of a
verified focused element in each checked metadata response. A missing runtime
ID fails closed for browser/custom controls. The native EDIT/RichEdit path
uses a separate `win32:<focus HWND>` identity and retains its password-style
checks. No field text or document content is included in the identity.

`PasswordFieldGuard` treats two different UIA element IDs as two distinct
scopes even when top-level and focused Win32 HWNDs and the reported safe state
are unchanged. A response that started more than 500 ms before receipt (or has
an implausibly future start time) cannot authorize text mutation; its remaining
permission lifetime is counted from query *start*, not from receipt. The hook
also clears its tracked token and snippet state on a password-metadata revision
change even if HWND, language and process are identical.

This addresses two **statically identified permission-reuse risks**: missed UIA
focus notifications followed by polling of another DOM input, and carrying a
token from a prior same-HWND field into the newly verified one. Regression cases use
real `LiveHook` construction and mocked Win32 APIs, plus simulated metadata
messages; they never install a global hook or type into a user window. The
native C# helper is compiled by the offline metadata test, not launched as a
live focus watcher. Baseline opt-in tests remain separate from the real GUI,
which still passes no `password_guard`.

**Not resolved:** an unreported element change *between* metadata polling and
the actual mutation, providers returning misleading runtime IDs or password
flags, real browser timing, compatibility, physical input delivery, Undo and
all live R01–R03/No01 release gates. This work is not approval to turn the
guard on by default, publish a new EXE, or claim R04 complete.

**Validation after these edits:** 27/27 focused unit/model tests (including
offline compilation of the C# metadata helper) and 111/111 expanded regression
tests across eight Python test modules, on Python 3.12. The first expanded run
found a preexisting incomplete RichEdit mock in `test_nontext_focus.py`:
`GetWindowLongPtrW` returned a bare `Mock` instead of a numeric style. Only
that fixture was corrected; its individual suite passed 6/6, and the complete
111/111 regression was then rerun successfully in 417.106 seconds.
All Win32 hook and input operations in these tests are mocked/in-memory;
no live password provider was started and no physical No01 acceptance occurred.

## P0 continuation, 31 October 2026 — opt-in GUI/headless wiring (SOURCE ONLY)

`Settings.experimental_password_guard` defaults to `false`, is strictly
validated and round-trips through a copied settings file. The GUI now exposes
a plainly labelled experimental checkbox; headless and GUI startup both use
`staged_password_guard(settings)` when constructing the **real** `LiveHook`.
The helper is *not* started in constructors or while settings are edited.
It starts only inside a later, separately authorized `LiveHook.run()`.

The opted-in factory requires the six-field element-identity protocol.
Legacy short `safe` messages, unknown/password states, missing IDs and expired
responses cannot enable mutation. The running hook refuses hot-swapping the
guard at runtime: saved changes apply only after a full exit and restart.
With R04 enabled, plaintext diagnostic word/readback traces are suppressed
regardless of the pre-existing debug-word flag; old files are not deleted.
If metadata becomes safe halfway through a word, the untracked suffix remains
uncorrected through the first separator, preventing a partial token from being
misidentified as a whole word. Physical keys still pass normally.

**R01/R03/No01 integration:** after a verified native replacement, focus and
password metadata are rechecked before CapsLock/HKL side effects. For generic
`SendInput` the existing bounded single-remainder logic prevents replaying
an accepted prefix; a new real-constructor test verifies a transition into
`password` after partial acceptance stops the remainder. This only verifies
Windows input *submission* behavior on mocks, never editor document contents
or physical Undo. The six RU/EN/HE paths and strict before-Space/HKL/Undo
criteria are described in the separate plan:
[`P0-R04-NO01-PHYSICAL-ACCEPTANCE-20261031.md`](P0-R04-NO01-PHYSICAL-ACCEPTANCE-20261031.md).

**Remaining risks:** UIA provider misreporting, silent DOM changes between
polling and a Windows mutation, real metadata-helper load/timing, plaintext
already logged before guard activation, clipboard/journal features (separate
protections), Unicode/IME/unknown editors, physical key loss, and true
application Undo. Default-off does **not** make the old published alpha safe
for secrets. **R04 OPEN; R01/R03 PARTIAL; physical No01 BLOCKED.**

**Final source validation (31 October 2026):** the combined 18-module
integration/regression suite completed with **227/227 tests, zero failures or
errors** in 359.125 seconds on Python 3.12. It covered the real LiveHook
constructor with mocked Win32, R04 opt-in/metadata, R01/R03 held/partial input,
off-hook/native paths, snippets, manual cycle, GUI/localization, six-direction
models and No01 acceptance predicates. Earlier overlapping targeted runs
included 129/129 transport tests, 24/24 six-direction-related checks and 34/34
GUI/localization checks; these counts must NOT be added to 227. Compilation
of the modified Python sources and `git diff --check` also succeeded.
No live metadata helper, physical input, global hooks, frozen EXE, active
user settings or public release were used in this validation.

## Subsequent controlled Windows native-field check — 31 October 2026

**This section describes a later, separately authorized trial**, not the
source-only validation above. An actual out-of-process UIA helper was started
only when our disposable test window had foreground focus. It polled two own
native Win32 EDIT controls, one ordinary and one `ES_PASSWORD`, without any
keyboard hook or typed text. In five-second windows: ordinary `safe` **44/50**
(six initial unknown/startup samples), password `password` **51/51**, and
ordinary after returning `safe` **50/50**. The helper exited cleanly, and
field focus remained in our window. This supports the native EDIT password
classification but **does not establish browser/DOM identity safety, timing
under real typing, IME behavior or live mutation prevention**. R04 remains
OPEN; default-off must remain unchanged. Separate evidence and broader
synthetic transport limitations:
[P0-WINDOWS-TRIAL-RESULTS-20261031.md](P0-WINDOWS-TRIAL-RESULTS-20261031.md).

**Source-level fail-closed correction:** the real opt-in factory now marks
`PasswordFieldGuard` as `native_only`. `LiveHook.context()` refuses browser
processes and every custom/unknown control class even when recent metadata
reports `safe`. EDIT/RichEdit must pass the synchronous native password-style
check *and* UIA metadata. This prevents the previously identified stale-safe
same-HWND DOM-focus race from authorizing a browser `SendInput` mutation **when
R04 is opted in**. Browser typing passes through without layout correction;
default-off legacy behavior remains unchanged. This is not a claim of full
browser-password protection in the default mode. Real browser correction
requires a future independently verified per-mutation field identity model.
