# R16: six-direction native EDIT trial — prepared, not performed

The test application is `r16_six_direction_trial.py` in the source root. It
uses a **new, empty Win32 EDIT**, not an existing editor document, and checks
these six fixture directions:

| From | Physical keyboard positions | Expected before Space | To |
|---|---|---|---|
| RU | `hello` | `hello` | EN |
| HE | `hello` | `hello` | EN |
| EN | `ghbdtn` | `привет` | RU |
| HE | `ghbdtn` | `привет` | RU |
| EN | `akuo` | `שלום` | HE |
| RU | `akuo` | `שלום` | HE |

The typed letters are **physical key positions on a QWERTY keyboard**, not
text to paste from the clipboard. Each test records: exact word and HKL
**before Space**, exact text and HKL **after one Space**, and whether one
`Ctrl+Z` restored the original (incorrect-layout) word. If it only removes
Space, that is **not** counted as restoration. Tests also abort if focus
leaves the test-only EDIT. Unexpected text is not stored in readable form;
JSON includes length, equality and SHA-256 only. Test reports appear under
`build/r16-six-direction-<timestamp>.json`, with no writes to personal files.

## 1. Safe commands, available now

These commands do not open any window, install hooks, change focus or typing:

```powershell
python r16_six_direction_trial.py --plan
python -m unittest -q test_r16_six_direction_trial
```

## 2. After separate explicit permission: actual user-pressed keys

First confirm that TypeBridge is running normally with RU/EN/HE active,
correction and early/native correction enabled. This step uses the working
Windows desktop and **requires explicit user authorization** before running:

```powershell
python r16_six_direction_trial.py --observe --consent AUTHORIZE_TYPEBRIDGE_TEST_WINDOW_INPUT
```

In the dedicated window, for each direction: select the indicated **source**
HKL yourself, press **Начать**, type the physical key positions, wait about
one second **without Space**, press Space, then press `Ctrl+Z` once. The tool
observes and classifies the text and window-thread HKL automatically, then
enables **Следующий сценарий**. **Сбросить только это поле** allows retrying
without changing anything outside the test field. Close immediately if it
shows a focus warning. This is an actual physical-typing trial for **one
native EDIT only**, not certification of Notepad++, browsers or other editors.

## 3. After separate explicit permission: synthetic automatic input

Normal TypeBridge *intentionally ignores* input with `LLKHF_INJECTED`. A
regular running Public EXE will therefore **not** respond to simulated
`SendInput` typing. The scripted variant requires a dedicated, pre-existing
**test-only** TypeBridge process started with BOTH `--headless` and
`--accept-injected`, with the user's normal TypeBridge fully exited via the
tray first. It checks the process tree; it does not stop, restart or launch
TypeBridge, change personal settings, or disable normal input protection.

Only after separate authorization, and after the special hook is configured,
the synthetic harness can be started with:

```powershell
python r16_six_direction_trial.py --inject --consent AUTHORIZE_TYPEBRIDGE_TEST_WINDOW_INPUT
```

It focuses **only its own empty EDIT** after the user presses **Начать**,
selects an already loaded HKL in its own thread, emits bounded pairs of
Win32 `SendInput` events, and aborts if its field loses foreground/focus.
After the first **Начать**, it advances through the six cases automatically,
rechecking the focused test field and special trial process before every
injection. The script checks the same pre-Space, Space, and Undo conditions.
All reports from this mode are marked `SYNTHETIC_INJECTED`
and `not_a_release_acceptance=true`. They **cannot prove real physical typing
or replace No01**; synthetic key-event timing and hook paths may differ.

## Safety and interpretation

- Neither live mode has been launched during preparation; `--plan` is the
  default and **cannot type**. Even live modes require a permission token and
  a deliberate start-button click.
- Windows HKLs must already be installed. The runner does not install any
  Windows layout, modify global settings, or test password fields.
- No keyboard hook is installed by the runner. The normal program or an
  explicitly authorized test-only TypeBridge instance is responsible for
  any existing hook. The runner never terminates it.
- Physical observation of pre-Space text is sampled at 25 ms intervals;
  evidence can miss extremely brief transitions. A passing sample is not
  proof that every physical scan code was delivered exactly once.
- No clipboard, personal documents or existing windows are inspected or
  modified. This tool only reads its own native EDIT and owning thread's HKL.
- Failure to restore the source word on the **first** Undo is flagged rather
  than silently accepted. The Undo policy may differ between editors.
- If a result fails, keep the report for analysis. Do not treat an injected
  result, a model result or this one-field test as complete R16 release QA.

The next **authorized** trial should start with observation (physical keys),
then decide whether the separate synthetic mode adds useful coverage.

## Trial evidence, 31 October 2026

After explicit user permission, the canonical Public EXE was rebuilt and
run with `--headless --accept-injected` for test-only synthetic native-EDIT
trials. A first focus-confirmed run and a repeat after startup CPU stabilized
each showed 6/6 exact results after Space, but only 2/6 observed exact text
and target HKL **before** Space, and 4/6 complete source-word Undo results.
An intervening trial started before the hook was demonstrably ready and is
excluded from acceptance figures. The runner now also records the last
pre-Space text/HKL as only length, SHA-256 and LANGID on failed cases.

**Critical interpretation:** `LiveHook` intentionally aborts an active native
input barrier on *any* injected key, even in `--accept-injected` mode. The
source-level mode does not reproduce hardware key scheduling while a worker is
busy. Do not weaken the protection to make a synthetic test pass and do not
credit these results as physical No01 acceptance. Detailed evidence and
report paths: [P0-WINDOWS-TRIAL-RESULTS-20261031.md](P0-WINDOWS-TRIAL-RESULTS-20261031.md).
