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

### Three-language distribution verified on 24 October 2026

The downloaded public `v0.1.0-alpha.5` ZIP already includes all three language
lists; users do not need to download dictionaries separately:

| File | Entries in alpha.5 | Data license |
| --- | ---: | --- |
| `data/ru.txt` | 212,414 | CC BY-SA 4.0 |
| `data/en.txt` | 176,155 | CC BY-SA 4.0 |
| `data/he.txt` | 185,358 | CC BY-SA 4.0 |

All three are independently prepared from the pinned FrequencyWords sources,
not copied from the local research RU/EN lists. The ZIP contains
`data/SOURCES.json`, `data/DATA-LICENSE.txt`, the full license in
`licenses/CC-BY-SA-4.0.txt`, and this attribution document. Its SHA-256 and all
53 manifest file hashes were checked. These are frequency-derived lists, not
Hunspell orthographic dictionaries; their correction decisions can differ
from the local research build. The executable and text copies carry the same
prepared data. TypeBridge's own source code remains separately MIT-licensed.

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

The upstream authorship of the extracted RU/EN files has not been established.
This does not mean that every word or any possible upstream dictionary is
proprietary. If their original open source is established, its actual license
must be considered. Similar words, or overlap with another dictionary, do not
establish provenance of the entire extracted list.

### Verified orthographic alternatives, not currently bundled

Hspell and Hunspell are different projects. Hspell supplies Hebrew linguistic
data; Hunspell supports dictionaries for many languages. Dictionary licenses
must be checked individually, separately from the checker library's license.

| Language | Verified upstream | Redistribution terms |
| --- | --- | --- |
| Russian | Alexander I. Lebedev dictionary, distributed by LibreOffice and wooorm/dictionaries | Permissive notice permits source/binary redistribution and modification; retain copyright, conditions and disclaimer, mark modifications, and do not imply author endorsement. [Full upstream notice](https://raw.githubusercontent.com/wooorm/dictionaries/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/ru/license) |
| English | SCOWL-derived en_US Hunspell dictionary, distributed by wooorm/dictionaries | Upstream permits redistribution with the applicable SCOWL/Ispell and constituent-source notices. Preserve the full composite notice rather than relabeling the data MIT. [Full upstream notice](https://raw.githubusercontent.com/wooorm/dictionaries/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/en/license) |
| Hebrew | Hspell | Its README explicitly applies AGPLv3 to dictionary files and generated word lists as well as code. It is not the source of our current Hebrew list and has not been substituted for it. [Upstream README](https://github.com/ivrix/hspell/blob/master/README) |

Comparison with normalized RU/EN Hunspell base entries from the pinned wooorm
snapshot did not establish identical lists. That comparison did not expand
affix rules and cannot prove or disprove historical derivation. No local
dictionary, confidence filter or public data source was replaced by this review.

## Dependencies

Runtime dependencies are listed in `requirements.txt`: psutil, pywin32 and
Pillow. PyInstaller is used for local builds. Each retains its upstream license;
refer to the installed package metadata and upstream license files. Python and
Tcl/Tk retain their respective licenses as well.
