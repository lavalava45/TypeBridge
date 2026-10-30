# TypeBridge language packages (R16/3a)

This folder is **not** the existing `dictionary_packs/` collection of themed
vocabularies. It is reserved for one **language package per subdirectory**.
Package folders are named by language code (for example, `ar`, `es`, `pt-br`).

The loader is read-only and checks package data. The updated **source** engine,
GUI and HKL mapper can use verified external packages after the user selects
at most three active languages and fully restarts TypeBridge. The **currently
installed EXE has not been rebuilt or physically tested for these languages**;
placing a package in this source directory does not upgrade that EXE.

See [the schema and safety rules](../docs/LANGUAGE-PACKS.md).

To validate the directory without launching the application or changing the
keyboard layout, run from the repository root:

```powershell
python -m language_pack_loader --root language_packs
```

The three built-in RU/EN/HE languages appear even with no external packages.
There is no fixed installed-language limit; at most three are active at once.
The `fixtures/r16-demo-packs/` directory contains tiny CC0-declared example
AR/ES data for future controlled tests, **not complete dictionaries**.
