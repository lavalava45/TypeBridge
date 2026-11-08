# Portable Windows releases

## Release candidate 0.1.0-alpha.9 — 8 November 2026

Use the existing tagged Windows workflow to build and verify the release
**from a clean commit**, never from the working `dist/TypeBridge.exe` or
personal `TypeBridge-data/`. The user explicitly requested publication of
this alpha. Its notes must disclose the active `SIMPLE_SWITCHER_MODE=True`
password-field guard removal, remaining R04/No01 risks and partial physical
acceptance. Preexisting alpha.8 CI cannot validate the alpha.9 code.

Before marking the release complete, check that the tag run succeeds,
GitHub publishes exactly one Public ZIP plus `SHA256SUMS.txt` and
`BUILD-MANIFEST.json`, SHA-256 matches, manifest source commit matches the
tag and private files/logs are absent. Failure leaves the candidate
unpublished; never call a failed CI run a successful release.

The older gate history below is retained as dated evidence, not current
alpha.9 status.

The `Windows portable release` GitHub Actions workflow builds on Windows x64
with Python 3.12. Push a new version tag (for example `v0.1.0-alpha.3`) to build
and publish a release. Tags containing a hyphen are marked as prereleases.
Manual **Run workflow** on a reviewed branch builds downloadable Actions
artifacts without publishing a release. Select the development branch explicitly
when testing changes that are not merged into `main`. A published release is never overwritten by a rerun.

The builder takes committed source with `git archive`, downloads the pinned,
SHA-256-verified full FrequencyWords/OpenSubtitles RU/EN/HE tables and the pinned
Lebedev/Hunspell Russian dictionary through `prepare_open_data.py`.
The new RU dictionary is the union of FrequencyWords and expanded Hunspell
forms, while `frequency_{ru,en,he}.tsv` contains normalized frequencies
measured over each *complete unfiltered* source corpus. Public source hashes,
output hashes, author notices and licenses accompany the dictionaries.
`build_public_release.py` refuses to run from a dirty worktree, including
untracked source/tests: otherwise `git archive HEAD` would silently omit those
changes. Commit/review the complete working state before building a release.
They live in `data/` **beside** the EXE, not embedded in it. The entire `data/`
folder must remain with the executable in the ZIP. Local research dictionaries
and personal files are not uploaded. The local `dist/TypeBridge.exe` is not
involved.

**Two build profiles:** Public and Diagnostics are compiled separately from
one commit. Only the Public EXE is packaged; Diagnostics stays local to the
builder/CI runner. The public frozen archive is inspected for excluded text
tracing modules, and both the outer ZIP and embedded source ZIP are checked
for private files. Each EXE has a `BUILD-PROFILE.json` with its checksum and
source commit. See [BUILD-PROFILES.md](BUILD-PROFILES.md).

Downloads include:

- `TypeBridge-VERSION-windows-x64.zip`: EXE, external RU/EN/HE dictionaries and
  all three frequency indices in `data/`, accompanying sources/license notices,
  example dictionary packs, English/Russian/Hebrew instructions, source archive and licenses.
- `SHA256SUMS.txt`: SHA-256 of the ZIP.
- `BUILD-MANIFEST.json`: source commit, dependency versions, dictionary sources,
  file hashes and the result of the frozen executable's background self-test.

The build must pass every `test_*.py` unittest module on public dictionaries.
Modules run in separate Python processes to bound peak memory use when large
dictionary and early-recognition fixtures are initialized. A failed module
stops the build. The source and frozen dictionary/model self-tests, archive
integrity check and private-file exclusion check must also pass before
publication. These are not proof of physical typing compatibility in every
editor. The executable is currently unsigned.

Users extract the entire ZIP and run `TypeBridge.exe`. Neither Python,
administrator rights nor an installer are needed for ordinary use. Settings
are stored in `TypeBridge-data` beside the EXE and move with it. The containing
folder must be writable. Old AppData settings are copied once; originals remain.
The encrypted journal is bound to its Windows user and is not portable between PCs. Autostart is optional
and refers to the executable's current location. Windows keyboard layouts for every selected active language are still required.

Maintainers can verify a downloaded archive in PowerShell:

```powershell
Get-FileHash .\TypeBridge-VERSION-windows-x64.zip -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

Workflow actions are pinned to commit hashes. Updating those hashes is a
deliberate maintenance operation. Releases use GitHub's built-in token with
`contents: write`; no additional repository secret is needed.

## Current release gate — 1 November 2026

**Latest verified candidate:** Windows CI
[41302267206](https://github.com/lavalava45/TypeBridge/actions/runs/41302267206)
at `13acca0` passed **all 119 isolated unittest modules**, frozen Public and
Diagnostics checks and upload of the verified `TypeBridge-windows-x64`
artifact. Earlier failures at modules 52/60/66/78 were corrected without
weakening content-hash or ZIP safety validation. The branch run does **not**
publish a GitHub Release. The `alpha.7` tag workflow will rebuild and publish
only if its own validations succeed; confirm the published files, source commit
and checksums afterwards. R04/No01 physical/security acceptance is still OPEN.

The following run-failure notes are **historical** and describe earlier CI.

**Update after Windows CI [41292774844](https://github.com/lavalava45/TypeBridge/actions/runs/41292774844), source `9e3c091`:**
The previous `RUNNER~1`/`runneradmin` path-comparison failure is **resolved on
the Windows runner** (`test_portable_diagnostics`, module 52/119, 8/8 passed).
The overall run still **failed** at module 60/119, `test_r16_4b_parallel`:

1. `ModuleNotFoundError: No module named 'jsonschema'` when importing
   `Draft202012Validator`; the clean release test environment needs the
   dependency.
2. `ValueError: v2 payload checksum mismatch: LICENSE.txt` while loading the
   Arabic demonstration v2 package; inspect how the manifest and license bytes
   are produced/copied on the Windows runner. Do not weaken package integrity
   or license verification to make the test pass.

A **new full CI pass and release ZIP** are still required. The prior text
below is kept as the historical state from run 41284863878, not the current
blocker. No version tag or release publication has been authorized here.

Alpha.6 is the latest published prerelease. The development run
[41284863878](https://github.com/lavalava45/TypeBridge/actions/runs/41284863878)
failed in `test_portable_diagnostics` at module 52/119, comparing a Windows
8.3 path with its long form. Earlier Backspace and pre-freeze readiness fixture
failures were fixed; the complete unit gate has not yet passed. Do not describe
this run as green or its package as published.

The path comparison test has since been corrected to compare the report's
resolved path against an independently resolved expected pack root. The
isolated local diagnostics module passed (8/8). The change **still requires
a fresh Windows CI run** before the release gate can be marked green; no new
build or publication was started during this documentation/banner update.

Pre-freeze dictionary tests use `TYPEBRIDGE_DICTIONARY_TEST_DIR`; an empty clean
checkout must not need an installed local EXE. Partially present build artifacts
remain an error. Post-build frozen/profile/ZIP checks remain mandatory.

The local installed EXEs are separate from this CI gate; their manifests identify
source `a28e349`. See [current status](STATUS.md). Documentation-only changes do
not rebuild EXEs or require a new workflow run. Creating a version tag publishes
a release automatically, so perform that step only for an explicitly approved
release. Alpha/beta packaging checks do not close the stable No01/R04 gates.
