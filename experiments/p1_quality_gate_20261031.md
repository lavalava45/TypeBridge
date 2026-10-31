# P1 independent RU/EN/HE quality gate — 2026-10-31

Run: `py -3.12 experiments/p1_quality_gate_20261031.py --output build/p1-quality-gate-20261031.json`.
Tests: `py -3.12 -m unittest discover -s experiments -p test_p1_quality_gate_20261031.py -v`.

The JSON artifact is generated under `build/` and includes SHA-256 hashes of
the frozen public input, policy implementation and active lexical source files.
Only the repository's synthetic keyboard mapper and source-only v2 policy run;
no hooks, live typing, Windows spellcheck or personal word collections.

## Predeclared splits and limits

The public fixture is the frozen Wikipedia title snapshot from 2026-10-30.
Earlier experiments assigned title `pageid % 3 == 0` to training; those IDs
are categorically excluded here. All other eligible single-script, mapper-
representable 4–18-character titles are held out. Duplicate normalized
titles are removed per language. Targets are classified by current lexical
membership: confident in-vocabulary, ordinary in-vocabulary, or OOV.
Already-known source-language collisions are excluded from positive cases.
An article title
can also be a proper name, which does not prove general dictionary validity.

Synthetic positive controls are sampled from already confident RU/EN/HE words
with a fixed seed, alongside source-word and duplicated-character typo
negative controls. These controls are not held-out lexical evidence. All
cases are stratified by sample type, source and target language, and physical
key sequence length (2–4, 5–8, 9–12, 13+). Each decision is timed with
`perf_counter_ns`; timings include Python call overhead and are hardware and
load dependent, not end-to-end input latency.

Provisional thresholds, written before running the gate:

* Synthetic known-word correction recall at least **97%** (regression control).
* Independent held-out **confident** in-vocabulary correction recall at least
  **97%**, provided some eligible examples exist. No data means insufficient
  evidence, rather than an automatic pass. Ordinary in-vocabulary recall is
  separately reported, as the conservative classifier may intentionally abstain.
* False switches on the pooled negative controls at most **1%**.
* p95 policy decision execution at most **10 ms** on this host.

OOV coverage is diagnostic only: zero automatic corrections on unseen words
can be the correct safety-preserving behavior. These thresholds are provisional
experiment acceptance criteria, not release approval. No threshold may be
tuned against the same held-out pages without making a new corpus first.

## Observed first run (2026-10-31)

Initial synthetic known positives: **300/300** correctly switched. A subsequent
evaluation including the independent in-vocabulary population found **22/65**
correct decisions across eligible directions (ordinary and confident combined):
**22/22 confident** and **0/43 ordinary**. Held-out OOV diagnostic:
**0/250** corrections. Negative controls: **0/459** false switches.
Final evaluation covers **1,074** decisions, decision p95 **0.2126 ms**.
Direction counts (correct/total): RU→EN 2/8, RU→HE 9/20,
EN→RU 0/6, EN→HE 9/17, HE→RU 0/6, HE→EN 2/8.
The quality gate **PASSES** its four provisional criteria; the 43 ordinary
in-vocabulary abstentions remain a distinct coverage limitation.
The independent sample is small and title-based; some directions have few or
no examples. Its selected subset cannot establish population-wide recall.

The OOV diagnostic examples contain titles absent from the dictionary.
Expected OOV misses reflect conservative design and do not fail acceptance.
Natural-input quality still requires a more representative public or consented
corpus, with independent labels and protected ambiguous cases.
