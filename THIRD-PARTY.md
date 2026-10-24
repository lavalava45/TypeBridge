# Third-party code and language data

The MIT license in this repository covers TypeBridge's own source code, not
third-party dependencies or language data.

## Public dictionary preparation

`prepare_dictionaries.py` downloads word frequency tables from
[hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords), based on
[OpenSubtitles 2018](https://opus.nlpl.eu/OpenSubtitles-v2018.php).
Attribution: Hermit Dave / FrequencyWords and OpenSubtitles 2018 contributors.

FrequencyWords explicitly licenses its **content under CC BY-SA 4.0** and its
generator code under MIT. Generated/filtered frequency lists remain attributed
CC BY-SA 4.0 data; they are not relabeled MIT. Keep source attribution and license
information when redistributing those lists or a binary that bundles them.

- [Upstream license statement](https://github.com/hermitdave/FrequencyWords#license)
- [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)

The preparation script records pinned source URLs and SHA-256 hashes in
`data/SOURCES.json`. Filtering changes include Unicode normalization, script and
length checks, frequency thresholds, removal of Hebrew internal final forms,
and selection of high-frequency confidence lists. Frequency lists may still
contain errors; they are not a verified orthographic dictionary.

The portable public ZIP includes these open dictionaries both inside the EXE
and as plain text in `data/`, with attribution, source hashes, the full CC BY-SA
4.0 license, dependency notices and a matching source-code archive. Public
packages are built in an isolated directory using `build_public_release.py`;
the private working dictionaries are never copied into that build.

## Optional example exclusion packs

`dictionary_packs/programming.json` and `gaming.json` are filtered adaptations
of [CSpell dictionaries](https://github.com/streetsidesoftware/cspell-dicts)
at commit `6e294247ab8932b5e10573d44636e2ce5764fab3`. Their upstream source
snapshots, exact source URLs/hashes and per-package MIT copyright/license notices
are included in `dictionary_packs/`. See its README for filtering details.
These packs are opt-in and do not replace TypeBridge's main language dictionaries.

## Local research data

Earlier private development used RU/EN dictionaries extracted from a third-party application.
Redistribution rights have not been established, so those lists and binaries
containing them are excluded from this repository and public releases.
Personal dictionaries, templates, logs and historical research artifacts are
also excluded. Hspell was evaluated separately; its AGPL data and checker are
not bundled with TypeBridge.

## Dependencies

Runtime dependencies are listed in `requirements.txt`: psutil, pywin32 and
Pillow. PyInstaller is used for local builds. Each retains its upstream license;
refer to the installed package metadata and upstream license files. Python and
Tcl/Tk retain their respective licenses as well.
