# TypeBridge

Automatic keyboard layout correction for **Russian, English and Hebrew** on Windows.

[Русская инструкция](README-RU.md) · [הוראות בעברית](README-HE.md) · [Feature status](docs/FEATURE-STATUS.md) · [MIT license](LICENSE)

TypeBridge corrects words typed in the wrong layout, offers manual last-word
conversion and direct layout shortcuts, and expands abbreviations into literal
text templates. It runs locally in the notification area.

## Features

- RU / EN / HE layout correction at word boundaries.
- Optional early correction in Notepad++ UTF-8 documents (experimental).
  **Why leave it off?** With this option disabled, correction waits until the
  word ends (for example, at Space or punctuation). Checking the completed word
  can reduce premature corrections and avoids the experimental, Notepad++-only
  editing path.
- Manual last-word layout cycling and configurable direct language shortcuts.
- Personal words and exclusions, with optional case sensitivity.
- Optional accidental CapsLock correction: `hELLO` → `Hello`.
- Optional current-layout flag beside the text caret.
- Text expansion: for example `//email` → your address, with instant or separator-triggered rules.
- Optional Windows startup and a saved correction enable/disable setting.

## Download and run

For the ready-to-run Windows x64 bundle, open [Releases](https://github.com/lavalava45/TypeBridge/releases),
download the TypeBridge ZIP, extract all files and run `TypeBridge.exe`.
Python and separate dictionary preparation are not required for the bundle.
The first public package is an unsigned alpha with open frequency dictionaries.

## Run from source

Requires Windows x64, Python 3.12 with tkinter, and installed Russian, English
and Hebrew keyboard layouts. Other language combinations are planned; they are
not implemented yet.

```powershell
git clone https://github.com/lavalava45/TypeBridge.git
cd TypeBridge
py -3.12 -m pip install -r requirements.txt
py -3.12 prepare_dictionaries.py
py -3.12 typebridge_app.py --self-test
py -3.12 typebridge_app.py
```

Enable correction in the settings window. Use **Save settings** to remember
the state for the next launch. Templates have their own enable switch and editor.

Dictionary preparation downloads FrequencyWords/OpenSubtitles word lists. Their
CC BY-SA 4.0 data license is separate from the MIT source-code license. Generated
dictionaries are not committed. Existing local dictionaries are never overwritten.
See [third-party sources](THIRD-PARTY.md).

## Build and check

```powershell
py -3.12 -m pip install -r requirements-build.txt
.\build-typebridge.ps1
py -3.12 -m unittest discover -p "test_*.py"
```

The executable is `dist/TypeBridge.exe`. Automated tests use model input or
withdrawn windows; they do not prove physical typing compatibility in every app.
Early correction is limited to Notepad++; multiline expansion and Undo behavior
still need validation in each editor. This is an actively developed prototype.

## Data and privacy

Current source adds **Русский / עברית / English** interface selection at the top
of Settings. The choice is saved and applied immediately to settings, tray and
tool windows. Hebrew uses right-to-left alignment/layout. It does not change
keyboard layouts, personal words or template content. Pending the next binary build.

Settings, personal dictionaries and templates live in `%LOCALAPPDATA%\TypeBridge`.
The previous LayoutSwitch data files are copied on first launch without deleting
the old files or overwriting existing TypeBridge files. Typed words and template
contents are not written to the event log. The app has no cloud correction service.

The optional **input journal** in current source (pending the next binary build)
is disabled by default. When enabled, it stores encrypted snapshots of explicitly
allowed text editors every two seconds; Notepad and Notepad++ are the defaults.
It has a visible enabled indicator, configurable 1–365 day retention (default 7),
recovery/copy and manual deletion. Text is protected with Windows user-scoped DPAPI.
Unknown/password fields are skipped; browsers are not supported. Disabling stops
capture and saving, but existing records remain until cleared or expired after
re-enabling. See [feature status](docs/FEATURE-STATUS.md) for limits.

No binaries containing the private research dictionaries are distributed here.
The source and the open-data preparation path are available for local builds.
