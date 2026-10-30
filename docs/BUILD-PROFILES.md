# TypeBridge: Public and Diagnostics build profiles

The normal personal build updates **both** the main `dist/TypeBridge.exe`
(Public) and the existing `dist/profiles/Diagnostics/TypeBridge.exe` from
the same working tree, including uncommitted changes:

```powershell
.\build-typebridge.ps1
```

It verifies each frozen module inventory and six-direction self-test, then
writes each profile's `BUILD-PROFILE.json`. It preserves existing `data/`,
user settings and `TypeBridge-data` in both output directories.

The local builder now creates **temporary** candidates inside `build/`. Both
profiles must pass PyInstaller module auditing and frozen six-direction
self-tests, and the source/dictionary snapshots must remain unchanged, before
the installed `dist/TypeBridge.exe` or Diagnostics EXE is replaced. If candidate
verification fails, the existing EXE and manifest remain untouched. Publication
uses short-lived, SHA-256-verified rollback copies in the build temporary
directory; failed replacements are restored to their previous bytes when
possible. Staging files are removed on completion or failure. As before, the
candidate self-test uses temporary SHA-256-verified copies of the required
external dictionaries under its own `data/` directory; it does not read or
write the installed profile's personal settings. As before, the
running TypeBridge process must be explicitly closed via the tray **before**
build; the script also rejects a running process at publication time. This
is tested with simulated failed candidate verification and publication, not
with a physical power interruption during two-profile replacement.

The separate **versioned dual-profile builder** (`build_profiles.py`) still
requires a clean committed source and creates the two variants below:

| Profile | EXE location | Content tracing | GitHub Releases |
|---|---|---|---|
| Public | `dist/TypeBridge.exe` | Not present in the frozen EXE | Only eligible variant |
| Diagnostics | `dist/profiles/Diagnostics/TypeBridge.exe` | Explicit opt-in using both debug flags | Never uploaded |

Each folder contains its own `data/`, default settings, `BUILD-PROFILE.json`
with the Git commit, profile, EXE SHA-256, dictionary hashes and frozen self-test,
and a separate `TypeBridge-data` directory for personal settings when used.
Both build pipelines resolve Public to the root of the selected build output,
and Diagnostics to `profiles/Diagnostics`. For local builds that root is `dist`;
the versioned release pipeline uses a separate clean output root and **does
not modify the user's installed `dist`**. Both preserve existing profile data.
An old `dist/profiles/Public/TypeBridge.exe`, if present, is a historical,
**stale** file, not a build target; it is not silently deleted or overwritten.
Future autostart registration from an auxiliary profile selects the canonical
`dist/TypeBridge.exe`. Existing Windows registry values are not changed by
building; inspect and correct them separately with the user's consent.

Public excludes `word_trace`, `browser_readback`, and heavy diagnostic probes
at PyInstaller analysis time; the builder then checks the actual embedded PYZ
archive. The Public frozen self-test runs with **both** flags temporarily
present and requires `word_trace_enabled=false`. The Diagnostics self-test
requires the inverse. Only flags created by the test are deleted afterward.

The release workflow also builds both variants from its clean Git snapshot,
but packages **only Public**. The Diagnostics EXE stays on the CI runner and
is neither released nor uploaded as an artifact. The published open-source
source ZIP may contain diagnostic Python code, but the compiled Public EXE
cannot import the excluded word-tracing modules. Both outer and embedded
source ZIPs are checked for flags, logs, journals and personal settings.

**Diagnostics warning:** `debug-words.log` can contain typed browser text,
including passwords. Use disposable test fields only. Do not attach raw logs
to public GitHub issues without carefully reviewing their contents.

**Scope:** This protects Public from word-trace logging. It does not repair
the unresolved alpha limitation in browser password-field correction. Full
No01 physical acceptance and the stable-release gate remain outstanding.
