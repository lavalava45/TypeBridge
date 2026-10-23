# Portable Windows releases

The `Windows portable release` GitHub Actions workflow builds on Windows x64
with Python 3.12. Push a new version tag (for example `v0.1.0-alpha.3`) to build
and publish a release. Tags containing a hyphen are marked as prereleases.
Manual **Run workflow** on `main` builds downloadable Actions artifacts without
publishing a release. A published release is never overwritten by a rerun.

The builder takes committed source with `git archive`, downloads the pinned
FrequencyWords/OpenSubtitles data using `prepare_dictionaries.py`, and bundles
only that open dictionary data. Local research dictionaries and personal files
are not uploaded. The local `dist/TypeBridge.exe` is not involved.

Downloads include:

- `TypeBridge-VERSION-windows-x64.zip`: EXE, RU/EN/HE dictionaries, example
  dictionary packs, English/Russian/Hebrew instructions, source archive and licenses.
- `SHA256SUMS.txt`: SHA-256 of the ZIP.
- `BUILD-MANIFEST.json`: source commit, dependency versions, dictionary sources,
  file hashes and the result of the frozen executable's background self-test.

The build must pass its dictionary/model self-test, archive integrity check and
private-file exclusion check before publication. These are not proof of physical
typing compatibility in every editor. The executable is currently unsigned.

Users extract the entire ZIP and run `TypeBridge.exe`. Neither Python,
administrator rights nor an installer are needed for ordinary use. Settings
are stored in `%LOCALAPPDATA%\TypeBridge`, not beside the EXE: this is an
installation-free bundle, not a USB-isolated settings mode. Autostart is optional
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
