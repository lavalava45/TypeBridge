# R16 AR/ES demonstration language packages

These are **tiny, synthetic, CC0-declared test packages**, **not production
dictionaries**. They exist only for a controlled test of selecting languages,
correcting a few known words, RTL handling and shared Latin script.

Both language directories use exactly the same manifest format as arbitrary
external language packages. Neither contains Python or executable code.
The `fixtures/generate_r16_demo.py` script is development-only: it creates the data packages
and is **not needed by TypeBridge when installing a package**.

From the TypeBridge source directory:

```powershell
python fixtures/generate_r16_demo.py
python -m language_pack_loader --root fixtures/r16-demo-packs
```

For an eventual **authorized** physical typing trial, copy only the `ar` or
`es` subfolder to `language_packs/` **beside the newly built EXE**, while
TypeBridge is fully closed; restart and choose at most three active languages.
Do not copy these demo folders into the current running installation or claim
they are full language support. A matching Windows HKL must already exist;
the app must not install a new keyboard layout automatically.

The source word lists have deliberately limited coverage. General Arabic and
Spanish corrections will require independently sourced, licensed dictionaries,
quality testing, normalization/IME review and actual controlled No01 acceptance.
