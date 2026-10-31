# P0 — R04 / R01 / R03 / No01: gated physical acceptance protocol

Prepared 31 October 2026. **PLAN ONLY — NOT EXECUTED.** It grants no
permission to install a global hook, launch a trial, change focus/HKL or type.

## Preconditions (separate explicit user authorization required)

1. User explicitly approves the test window(s), duration/scope and use of the
   *physical* keyboard. No real passwords, confidential documents or production
   inputs. Keep original SwitchER64 unchanged; no VM.
2. User exits every running TypeBridge instance through **Exit** in the tray;
   verify processes have stopped. Never terminate it automatically or replace
   `dist/TypeBridge.exe` while it runs. Preserve profile/files and canonical
   build instructions in `AGENTS.md`.
3. Obtain a verifiable backup of the exact executable/profile and source
   baseline. Build only through the agreed canonical build procedure after
   the explicit approval and shutdown. Check source version/manifest.
4. Use own disposable empty native EDIT and local HTML fixture with ordinary,
   password, textarea and contenteditable inputs. Explicitly separate source-
   only mock observations, real UIA metadata, and real physical-key events.
5. Confirm word-debug logging disabled, no personal text captured, diagnostics
   contain only counts/status; ensure the experimental guard is ON only for its
   controlled run. Test normal default-off/rollback separately. Ensure cleanup
   and hook uninstallation after each run; abort if focus/window is lost.

## R04: field transitions and fail-closed edits

Check natural typing passthrough and absence of any automatic/manual/snippet
mutation in a password input, masked native EDIT/RichEdit, unknown element,
unsupported provider, and stale metadata (including deliberately delayed
helper responses). Repeat `ordinary → password → ordinary` in **one browser
HWND** via mouse, Tab/Shift+Tab and script-driven DOM focus; observe UIA element
identity/revision before and immediately before an attempted correction.
Test all text mutation entrypoints: early/boundary, manual/cycle, snippets,
direct HKL shortcut, CapsLock side effects, clipboard paste/search and
Diagnostics separately. Unknown/failure must not swallow physical keystrokes.
Do not claim universal password protection if the UIA provider misreports.

## R01/R03/No01: six-direction matrix (physical press, no injected substitute)

| Source HKL | Physical token | Target HKL | Expected before Space | After Space |
|---|---|---|---|---|
| RU | `hello` | EN | `hello` | `hello ` |
| HE | `hello` | EN | `hello` | `hello ` |
| EN | `ghbdtn` | RU | `привет` | `привет ` |
| HE | `ghbdtn` | RU | `привет` | `привет ` |
| EN | `akuo` | HE | `שלום` | `שלום ` |
| RU | `akuo` | HE | `שלום` | `שלום ` |

Observe exact source/target HKL and text immediately **before** Space, after
Space, then one application Undo (record both changed text and whether source
typing is restored), and Redo where supported. Run each in own empty editable
control with the correct physical key map. `r16_six_direction_trial.py` has a
safe default `--plan`; its `--observe`/`--inject` modes are **not authorized by
this document** and require separate permission. Synthetic `SendInput`,
`WM_CHAR` and private-desktop checks do not establish physical acceptance.

Stress with fast tails, held letter KEYDOWN/repeat/KEYUP, immediate Space,
Backspace, second word, HKL switch, mouse/Tab field change mid-correction,
partial `SendInput`, timeout and unavailable/slow editor. Confirm each retained
key is delivered exactly once or a loss is explicitly reported and gated; never
replay an accepted prefix, move a buffered key to a new field, or overwrite a
later-edited document during recovery. Check Scintilla/RichEdit Undo boundaries.

## Pass/fail and stop rules

P0 passes only with correct pre-Space text/HKL in all six directions, exact
separator/fast-tail behavior, verified safe Undo, no lost/reordered/duplicated
symbols, and no mutation of a password/unknown/foreign field. Any uncertain
physical delivery, secret mutation, stale-permission write or incomplete Undo
means **BLOCKED**; log non-sensitive failure metadata and stop further trials.
Source-only tests, successful SendInput event counts, screenshots or mere hooks
being installed do not satisfy this gate.

**Current status: R04 OPEN; R01/R03 PARTIAL; No01 BLOCKED.**

## Execution note (31 October 2026)

After the original plan, the user explicitly authorized a controlled build
and test. The automated `SendInput` runs and the separate real UIA native
password-field test are recorded in
[P0-WINDOWS-TRIAL-RESULTS-20261031.md](P0-WINDOWS-TRIAL-RESULTS-20261031.md).
**The physical human-keyboard matrix above was NOT executed.** A confirmed
test window focus and synthetic OS input do not make an injected trial a
physical-keyboard trial. Live real-browser password transitions remain open.
