# Portable Windows releases

The `Windows portable release` GitHub Actions workflow builds on Windows x64
with Python 3.12. Push a new version tag (for example `v0.1.0-alpha.3`) to build
and publish a release. Tags containing a hyphen are marked as prereleases.
Manual **Run workflow** on `main` builds downloadable Actions artifacts without
publishing a release. A published release is never overwritten by a rerun.

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
and refers to the executable's current location. Installed RU/EN/HE Windows
keyboard layouts are still required.

Maintainers can verify a downloaded archive in PowerShell:

```powershell
Get-FileHash .\TypeBridge-VERSION-windows-x64.zip -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

Workflow actions are pinned to commit hashes. Updating those hashes is a
deliberate maintenance operation. Releases use GitHub's built-in token with
`contents: write`; no additional repository secret is needed.
