# TypeBridge P0–P4 isolated work — 31 October 2026

This is a source/test evidence report, **not** a stable release approval.
The user authorized autonomous tests and builds. The real hardware keyboard
still requires a human participant; injected or private-desktop events are
not equivalent. No VM was used or authorized.

## Update: rebuilt EXE and user hardware observation (31 October 2026)

The approved local builder published Public and Diagnostics from clean feature
commit `adda6a2d837df08e98d17cfe75cadc39e9b38145`. Frozen self-tests:
**6/6 layout directions per profile**, plus 46/46 targeted post-build tests.
Five existing profile JSON files retained their original SHA-256 hashes.
After launching the updated Public EXE, the user replied to the six-direction
physical typing instructions: **«все хорошо. со всеми раскладками»**.
Treat this as a **user-reported successful physical RU/EN/HE six-direction
trial**. It is not instrumented proof of each before-Space timing, HKL,
Undo/Redo, stress input, focus transition or live password-field check.
The basic hardware trial is no longer entirely untested, but full P0 and R04
stable-release acceptance criteria remain open.

## P0: input delivery and native editor Undo

`py -3.12 early_native_harness.py --runtime-self-test` ran on a separate,
**inactive Windows desktop**. In a private `RichEdit50W` receiver it checked
all six RU/EN/HE input directions using the real `LiveHook` processing path,
synthetic WM_CHAR delivery, actual text/HKL observations **before Space** and
exact separator. All **6/6** directions passed. The native transaction cases
confirmed six source-preserving Undo/Redo outcomes, plus two longer off-hook
words with correct Undo. The harness installed no global hook, changed no
user HKL, and touched no personal window or clipboard. The `runtime_cases`
array in its output makes the six observations auditable.

`py -3.12 early_keyboard_native_probe.py` then checked the alternate
Backspace/Unicode receiver path on an inactive desktop: six directions in
`EDIT` and six in `RichEdit50W` reached correct target text, **but a single
native Undo did not recover the full source token**. In RichEdit it usually
restored only the prefix before the held last character. A separate private
EDIT-only experiment tried `EM_REPLACESEL` (six directions): replacement text
was exact in all six; one Undo restored an **empty field**, not the original
word. Thus neither raw receiver strategy warrants a blanket Undo guarantee.
These checks support keeping the bounded native RichEdit transaction and
not changing generic keyboard transport merely to satisfy synthetic Undo.

The earlier injected foreground trial was only **2/6 before Space, 6/6 after
Space, 4/6 full Undo** in two reproducible sessions. Its native queue
intentionally aborts on external injected events while busy. It is not a
hardware regression result. Details in
[`P0-WINDOWS-TRIAL-RESULTS-20261031.md`](P0-WINDOWS-TRIAL-RESULTS-20261031.md).

## R04: security boundary for asynchronous UIA

Independent tests reproduce that `PasswordFieldGuard` recognizes malformed,
stale and same-HWND changed element IDs and denies `unknown`/`password`.
However, async UIA polling cannot prove a DOM element did not switch to a
password field *between* snapshot and `SendInput`. The default-off R04 opt-in
now has a **native-only fail-closed policy**: only native EDIT/RichEdit,
additionally checked synchronously for `ES_PASSWORD`, can be corrected.
Browser, WebView and unknown custom controls are always passed through when
R04 is opted in. This avoids the known stale-browser-safe permission path,
but **does not mean default-off browser passwords are protected**. Genuine
HTML input mutation with an atomic per-element safety guarantee remains OPEN.

## P1: fixed-source, stratified decision-quality measurement

The new `experiments/p1_quality_gate_20261031.py` uses a frozen public
Wikipedia title snapshot (held out by page ID), real source lexical sets,
source code and input SHA-256 inventory, reproducible seed, six directions,
stratification by language/length/positive/negative/known/OOV and decision
timing. Output: `build/p1-quality-gate-20261031.json` (generated, not a
release artifact). Results on the checked source snapshot:

| Slice | Correct / total | Interpretation |
|---|---:|---|
| Synthetic known wrong-layout controls | 300/300 | Regression control, **not** independent data |
| Independent held-out confident in-vocabulary | 22/22 | Small clean subset, positive criterion passed |
| Independent held-out ordinary in-vocabulary | 0/43 | Conservative abstentions; potential coverage limitation |
| Held-out OOV positive opportunities | 0/250 | Diagnostic only: guessing unseen words is **not** mandated |
| Negative controls | 459/459 | Zero false switches on this limited sample |
| Policy decision latency | p95 ≈0.213 ms | Synthetic mapper, not physical end-to-end latency |

All four provisional quality gates passed; this establishes an independently
measured baseline, not a release-ready generalization claim. Wikipedia titles
are a limited proxy for natural input; real language frequencies, timing and
rare collision rates remain unknown. Never tune thresholds on these same held-
out titles without introducing a new frozen test set. Avoid personal typing
logs without specific permission.

## P1: R13 Russian adjacent-letter transposition (research, **not live**)

New `russian_transposition_20261031.py` returns a unique adjacent-swap
suggestion only for verified ordinary Russian text when explicitly in
`suggest` mode; default `off`. Unsupported `auto` intentionally returns no
change. Existing words (e.g. `кто`, `кот`), unknown/secret/code/URL contexts,
protected words, excluded candidates, ambiguous swaps and uppercase tokens
are rejected. No actual editor text is modified.

`experiments/r13_transposition_eval_20261031.py` used SHA-256-ordered 2,000
confident RU source words and the shipped RU dictionary (read-only). Among
12,350 generated adjacent swaps, 109 were already valid words and protected.
Of 12,241 unknown swapped forms, 12,130 had a unique source suggestion and
111 ambiguous forms were rejected. This is **recovery on synthetic typos
generated from known answers**, not an observed real-world false-suggestion
rate. Automatic mode and any live GUI integration remain blocked by P0
focus/Undo acceptance and independent natural-text negative samples.

## P2/P3/P4 and release boundaries

R08/R14 bulk dictionary import is still blocked by source/attribution and
held-out collision evaluation. R10–R12 personalization depends on verified
explicit Undo versus ordinary Backspace. R06 context and R09 fastText/n-grams
remain experimental; earlier A/B results did not support runtime integration.

New `test_p4_readiness_20261031.py` verifies real RU/EN/HE bundled v2
manifest file sizes and SHA-256, parity with the legacy bundled dictionaries,
and the current Public frozen EXE's internal manifest hash/profile. A valid
artifact hash does **not** mean that artifact contains the latest changed
source code. Any future build must use `build-typebridge.ps1` after a proper
exit, verify clean source commit, dual frozen self-tests, preserved profile
hashes and the exact new `BUILD-PROFILE.json` commit. Public release ZIP/main
branch changes still require explicit authorization and P0 acceptance.

## Full repository regression and corrective follow-up

After the initial P0 242/242 targeted check, full `unittest discover` ran
**1082 tests** and identified one failure plus six errors. The original log
is retained as `build/full-repository-regression-20261031.log`. Three old
`LiveHook.__new__` fixtures lacked the new guard attribute; the production
`request_config()` was hardened with `getattr(..., None)` to keep guard
lifecycle immutable without throwing in partially constructed contexts.
Four more historical fixture contracts were updated: launch-language state
during startup, optional `catalog` in user-lexicon loading, numeric watchdog
mouse metrics, and the `password_guard` constructor keyword in the mocked
EN/AR start-up hook. No recognition thresholds or input transports changed.

The seven exact failing cases were rerun and passed **7/7**. A complete fresh
`py -3.12 -m unittest discover -s . -p 'test_*.py' -q` then passed
**1082 tests, zero failures/errors, one skipped** in 1519.146 seconds.
Machine log: `build/full-repository-regression-fixed-20261031.log`.
Previously changed source/tests and the additional six files were backed up
with SHA-256 verification under:
`E:\Downloads\devspace-test\typebridge\_r16_backups\20261031-r04-native-only-fail-closed`
and `...\20261031-full-suite-contract-fixes`, respectively. Five existing
profile JSON hashes were snapshotted to
`build/profile-hashes-prebuild-20261031.json` for post-build comparison.
Source-level tests are **not** a substitute for a newly built frozen EXE.

**Release gates as of this work:** R04 **OPEN** for HTML/other custom controls;
R01/R03 **PARTIAL** for physical loss/Undo; No01 hardware **BLOCKED**; P1
offline baseline/prototype prepared; P2/P3 and stable P4 release **not complete**.
