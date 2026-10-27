# TypeBridge: Public and Diagnostics build profiles

One local command builds **both** from the same clean, committed source:

```powershell
.\build-typebridge.ps1
```

| Profile | EXE location | Content tracing | GitHub Releases |
|---|---|---|---|
| Public | `dist/profiles/Public/TypeBridge.exe` | Not present in the frozen EXE | Only eligible variant |
| Diagnostics | `dist/profiles/Diagnostics/TypeBridge.exe` | Explicit opt-in using both debug flags | Never uploaded |

Each folder contains its own `data/`, default settings, `BUILD-PROFILE.json`
with the Git commit, profile, EXE SHA-256, dictionary hashes and frozen self-test,
and a separate `TypeBridge-data` directory for personal settings when used.
The existing `dist/TypeBridge.exe`, its flags and user data are not touched.

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
