# P0 Windows trial evidence — 31 October 2026

## Boundary and setup

User authorized building, starting, using a dedicated test field and closing
test-only TypeBridge. This was **not** human hardware-keyboard acceptance.

- Branch `feature/r04-password-guard-staged-20261030`; source commit `3915f93`.
- `build-typebridge.ps1` produced canonical Public and existing Diagnostics
  artifacts; both frozen self-tests were 6/6. Public SHA-256:
  `0a33f9934d0fe9af392297de35b50cd0f9d0c4fb7d7d60306a6136cb7e18e471`.
  Existing Public and Diagnostics `settings.json` SHA-256 values were unchanged.
- Windows had RU/EN/HE loaded. Test-only Public EXE used `--headless
  --accept-injected` with existing early-mode configuration. Separate runner
  sent synthetic `SendInput` to its **own blank native Win32 EDIT** only after
  foreground/focus verification. No user words, browser fields, global HKL
  changes, original SwitchER64, VM, or personal documents were tested.
- Test-only GUI and hook processes were closed after each run; the hook was
  stopped with `WM_QUIT`, and no TypeBridge.exe remained afterward.

## Synthetic native EDIT: six direction results

These are programmatic **injected** events through a real Windows desktop,
real packaged TypeBridge hook and test EDIT, **not physical keystrokes**.

| Run | Evidence file under `build/` | Before Space | Exact after Space + HKL | One Undo restores original | Interpretation |
|---|---|---:|---:|---:|---|
| 1 | `r16-six-direction-20261031T002339608355Z.json` | 2/6 | 6/6 | 4/6 | Focus checked, runtime behavior inconsistent with full No01 criteria |
| 2 | `r16-six-direction-20261031T002800213108Z.json` | not valid | not valid | not valid | Started during hook/dictionary initialization; results must not be used for acceptance |
| 3 | `r16-six-direction-20261031T003021455224Z.json` | 2/6 | 6/6 | 4/6 | Started after test process CPU became stable; actual pre-Space text/HKL additionally sampled |

In the first run, RU→EN and HE→EN had correct text/HKL before Space but did
not restore the full source token on a single Undo. For RU→EN, the hashed Undo
observation matches **the source token missing its last character**, not the
complete source. Other four paths reached expected text/HKL after Space but
were not observed satisfying the pre-Space gate.

In run 3, RU→EN and EN→RU satisfied the pre-Space check, while other paths
did not. The extended recorder retained only the **length, SHA-256 and LANGID**
of the last observed no-Space state, even in failing cases. No unexpected
plain-text content was included in the saved JSON. The first two existing
report schemas remain unmodified.

### Important injected-input limitation

In `LiveHook.handle_keyboard`, while a native transaction queue is busy,
**any** `LLKHF_INJECTED` input calls `gate.abort('external-injected-input')`,
including in `--accept-injected` mode. The latter only bypasses the separate
normal synthetic-input filter. This is an intentional fail-open defense for
real input, and it makes automated SendInput measurements during an off-hook
transaction **not behaviorally identical to an actual hardware keyboard**.
Do not change this safety behavior solely to turn these trials green.
The native EDIT Undo grouping and true physical input ordering remain unproven.

## Real UI Automation helper (no keyboard hook)

A standalone, foreground-confirmed test-only Win32 window hosted ordinary
`EDIT` and `EDIT` with `ES_PASSWORD`. The actual out-of-process PowerShell /
UI Automation `PasswordFieldGuard(require_element_id=True)` was started only
after foreground verification, and tested `ordinary → password → ordinary`
without typing. Observations at 100 ms intervals, about five seconds each:

| Stage | Expected classification | Confirmed samples | Other samples |
|---|---|---:|---|
| Ordinary EDIT (initial) | `safe` | 44/50 | 6 initial `unknown`/provider initialization |
| ES_PASSWORD EDIT | `password` | 51/51 | none |
| Ordinary EDIT again | `safe` | 50/50 | none |

Focused native HWNDs matched the queried field. The helper was closed and its
child process terminated. This is **real native-control UIA evidence**, not
proof of DOM element identity in Chrome, Electron, a web password manager,
custom controls, or a silent focus change between two polls.

## Resulting release gates

- R04 remains **OPEN**. Native EDIT/ES_PASSWORD classification passed this
  controlled real-provider test; browser/custom password safety did not.
- R01/R03 remain **PARTIAL**. In particular, one Undo did not always restore
  the entire source in injected native EDIT trials. The synthetic busy-gate
  prevents attributing this unequivocally to genuine hardware input.
- No01 remains **BLOCKED**. We need a separately supervised actual human
  hardware-keyboard run in the own blank EDIT, including before Space, final
  separator, all six HKLs, first Undo and fast/focus-loss cases. Do not publish
  a claim of physical acceptance or change the default-off R04 policy.
