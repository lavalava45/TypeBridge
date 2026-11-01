# R16 — controlled physical-input acceptance (NOT YET PERFORMED)

This is the execution checklist for the next separately authorized trial.
R16/3b/4 source/model work does **not** satisfy the gates below. No VM and no
unapproved use of the real desktop, installed SwitchER64 or global hooks.

The prepared six-direction native EDIT recorder and its **separately gated**
synthetic mode are documented in
[`R16-SIX-DIRECTION-TRIAL.md`](R16-SIX-DIRECTION-TRIAL.md). Neither live mode
has been run as part of the preparation.

## Preconditions: distinct from model acceptance

1. User explicitly exits TypeBridge through the tray. Confirm the process is
   gone; **never kill the user's current TypeBridge process automatically**.
2. Snapshot and SHA-256-verify the existing EXE, `TypeBridge-data` personal
   files, settings sidecars and dictionaries; do not replace them in-place
   without the user's approval. Never copy personal words into a test report.
3. Build only by `build-typebridge.ps1` into the authorized current paths,
   verify each profile's manifest, frozen module inventory and six-direction
   self-test. This is **not** proof of physical input.
4. For limited AR/ES tests, manually install a validated data-only package
   under `language_packs/<code>/` **beside the new EXE**. The tiny examples at
   `fixtures/r16-demo-packs/` are synthetic and unsuitable for general use.
   Verify every payload size/SHA-256, declared license and catalog LANGID.
5. Confirm each selected Windows HKL is already installed, and the mapper
   resolves the user's intended existing layout without adding a new one.
   A missing HKL must stop before any global hook is installed.
6. Separately authorize physical typing in a new, empty test editor, with
   non-sensitive text only, no existing document changes and no focus theft.

## Test matrix — all statuses currently BLOCKED / not physically run

| Scenario | Expected acceptance |
|---|---|
| RU↔EN, RU↔HE, EN↔HE | Six old directions unchanged; word, punctuation and HKL correct |
| EN→AR→EN after restart | Arabic physical key mapping/RTL preserved, exactly one correction before Space |
| EN→ES→EN after restart | Shared Latin ambiguity refuses guesses; known unique words only |
| External language disabled | No correction or hotkey capture for that language, personal data retained |
| Restore RU/EN/HE | All six directions, `ё/е` and Hebrew final forms intact |
| Early/Undo | Exactly one undo group where supported; no duplicate letters or trailing Space |
| Held keys / fast next word | Held and replayed tail ordering proven; no lost or duplicate keyup/keydown |
| Focus/selection change | No edit of a different field; unknown outcome quarantined, no blind replay |
| Missing/corrupt package or HKL | Fail closed before hook, no data loss, clear diagnostic message |
| Package install / removal | Restart-only; no Python edits or EXE rebuild for a new validated package |
| Sidecar rollback | Old EXE loads legacy `settings.json`; new EXE restores full selection |
| Password/IME/RTL | Never assert universal protection without real editor evidence |

Log exact versions, editor types, installed HKL IDs, masked test words,
observed text/caret/HKL readback and failures in a separate test report.
Do **not** log real passwords or existing user documents. R04 password-field
protection remains a separate known blocker, even if these R16 tests pass.

## Release gate

**BLOCKED** until the user authorizes the next trial, the new EXE is built and
audited, all relevant native/physical tests pass, and production-scale Arabic
and Spanish dictionaries with verified distribution rights are available.
No publishing or claims of full AR/ES support before these requirements.
