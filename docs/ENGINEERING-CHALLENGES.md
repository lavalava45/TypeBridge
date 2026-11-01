# TypeBridge: Seven Hard Engineering Problems

> [Русская версия](ENGINEERING-CHALLENGES-RU.md) · [Home](../README.md) · [Current status](STATUS.md)

Switching a Windows keyboard layout is relatively straightforward. **Correcting text while it is being typed** is not. The application must preserve incoming keystrokes, avoid breaking Undo, refrain from changing the wrong field, and choose a language across different writing systems.

These are seven engineering challenges we encountered while building TypeBridge, what we implemented, and what we have **actually verified**. This is a snapshot of the **v0.1.0-alpha.7 era (November 2026)**, not a promise about all future releases.

## 1. Editing text while the user keeps typing

**The challenge.** While a word is being recognized and replaced, the user may already be pressing another letter, Backspace, Space, or switching windows. A naive delete-and-reinsert operation can reorder events, lose characters, or insert them into a different field.

**Our approach.** For supported native editors, editing runs in a worker rather than directly inside the keyboard hook. A bounded queue holds incoming physical keystrokes while a native correction is in progress. Before delivering queued events, the system checks the target field and keyboard layout. Uncertain delivery or partial writes disable unsafe retries. In certain cases, pending printable keys can be recovered **only on the user's explicit request** and only in the original field.

**Evidence and limits.** A user-reported fast EN→RU trial in Word succeeded **5/5** times. The complete matrix of rapid input, focus changes, and partial delivery remains open. A successful `SendInput` call is not proof of what an editor actually committed.

## 2. Six directions across Russian, English, and Hebrew

**The challenge.** Detecting text that “doesn't look Russian” is not enough. The same **physical key positions** may produce plausible words in several languages. Hebrew adds right-to-left writing, different characters, and editor-specific behavior.

**Our approach.** The recognition engine handles **RU↔EN, RU↔HE, and EN↔HE**: six directed conversions. It maps physical keys, evaluates dictionary candidates, and considers known words in the current language, exclusions, and ambiguous alternatives. Manual last-word cycling is separate from automatic confidence thresholds.

**Evidence and limits.** The user tested all six directions in Word successfully. That supports these basic real-input scenarios, not universal compatibility across editors, input methods, keyboard shortcuts, or typing speeds.

## 3. Correcting a word **before Space**

**The challenge.** At a word boundary, the token is complete. Before Space, a switcher must decide whether the prefix is sufficient to make a confident correction without changing an unfinished word prematurely.

**Our approach.** An optional experimental mode recognizes a confident full word or a uniquely supported continuation. It rejects conflicting interpretations and chooses an editing transport according to the host: **Scintilla** in Notepad++, native operations for compatible RichEdit controls, and a general keyboard path elsewhere. Where available, we verify the original range, caret, resulting text, and Windows keyboard layout (HKL).

**Evidence and limits.** Users have reported successful early correction in Notepad++ and desktop ChatGPT, including `ghbdtn → привет` before Space. This remains experimental: the general keyboard transport cannot read back the final content of every target editor.

## 4. Distinguishing ordinary input fields from password fields

**The challenge.** Two HTML inputs in the same browser tab can share the same Windows `HWND`. JavaScript can move focus from an ordinary field to a password field between a safety check and the edit. Checking a window title or process name is insufficient.

**Our approach.** TypeBridge combines the native `ES_PASSWORD` style with **Windows UI Automation** metadata for individual elements. The guard tracks element identity, freshness, focus changes, and unknown states. An unverified field must not authorize corrective mutation; ordinary physical typing should pass through unchanged.

**Evidence and limits.** A user-reported harmless “ordinary → password → ordinary” trial produced **3/3** expected outcomes. A separate controlled test confirmed native `EDIT`/`ES_PASSWORD` classification.

**Still unsolved.** A UI Automation snapshot and a subsequent Windows input operation are **not atomic**. Script-driven DOM focus changes can still create a race. **Alpha.7 must not be presented as guaranteed safe for secrets across all browsers and applications.** Password-field safety (R04) remains an open engineering problem.

## 5. Adding languages without rewriting the engine

**The challenge.** A hard-coded RU/EN/HE engine works for an early prototype, but adding languages would otherwise require changes throughout the recognition logic, UI, dictionaries, and language-selection rules.

**Our approach.** TypeBridge now supports validated, **data-only ZIP language packs** describing alphabets, Windows language IDs, dictionaries, sources, and licenses. Validation covers manifests, file membership, and SHA-256 checksums. One to three languages can be active, while personal words and shortcuts for inactive languages are preserved. Installation, updates, archival removal, and restoration use transaction journaling and interruption recovery checks. Language packs do not execute third-party code.

**Evidence and limits.** The architecture and simulated failure-recovery paths have automated coverage, and RU/EN/HE ship with the release. AR/ES examples are **small demonstration lexicons**, not production dictionaries or proof that Arabic/Spanish physical typing has passed acceptance. Additional languages require separate real-keyboard testing.

## 6. Undo/Redo, Backspace, and editing context

**The challenge.** A sequence of Backspaces followed by Unicode insertion may leave visually correct text while breaking the editor's logical Undo unit. Deleting part of a word, switching fields, or typing again can also invalidate the switcher's internal word state.

**Our approach.** We developed and tested specialized **Scintilla and RichEdit** editing operations alongside the general `SendInput` path. Where supported, the original text range and resulting edit are verified. Backspace recovery is limited to a **known, tracked word**; TypeBridge does not guess an unknown deleted span. Queued keys must not be automatically transferred to a different input field.

**Evidence and limits.** The user reported the correct final word after Word Undo/Redo, and isolated RichEdit tests passed relevant cases. However, other native `EDIT` trials showed that **a single Undo did not consistently restore the whole original word**. Universal Undo behavior has not been proven.

## 7. Avoiding false switches, even if that means missing some corrections

**The challenge.** An aggressive switcher can seem effective until it starts changing valid words, names, code, and abbreviations. False positives matter at least as much as the number of wrong-layout words recovered.

**Our approach.** The decision policy may **abstain from correction** when the source word is recognized, confidence is low, or multiple languages yield competing candidates. We added explainable decision codes and a reproducible held-out evaluation to measure this tradeoff instead of relying on anecdotes.

**Evidence and limits.** On a limited held-out evaluation, there were **459/459 correct non-switches** for negative controls and **22/22 corrections** for a small high-confidence subset. Yet another slice of ordinary positive examples produced **0/43** corrections. Conservative decisions reduce accidental changes but can substantially reduce coverage. These results are a baseline, **not a claim of market-leading accuracy**.

## What makes this work interesting?

None of these individual ideas—multiple languages, early switching, custom dictionaries, or Undo—is claimed as a TypeBridge invention. Other keyboard switchers implement some of them. The difficult part is **making them coexist**: physical key events, several writing systems, dictionary decisions, different Windows editors, delayed input, focus changes, and security boundaries all interact.

**Our main unfinished reliability task** is proving, with physical keyboard input, that keys are never lost, duplicated, reordered, or delivered into the wrong field across the scenarios we intend to support. The browser password/DOM-focus race is a separate unresolved safety issue. That is why TypeBridge remains an **alpha**, not a product with certified input reliability or secret-field protection.

### Supporting engineering records

- [Implementation status and user acceptance evidence](STATUS.md)
- [Editor compatibility and six-direction results](DESKTOP-EDITOR-COMPATIBILITY.md)
- [Reliability roadmap: R01–R04 and No01](../ROADMAP.md)
- [Isolated input, Undo, and recognition-quality experiments](P0-P4-ISOLATED-DEVELOPMENT-20261031.md)
- [Language packages and integrity checks](LANGUAGE-PACKS.md)
- [Alpha.7 release notes](releases/0.1.0-alpha.7.md)

This article documents **our implementation and its known limits**. It does not assert that competitors lack equivalent techniques; such a claim would require a separate technical comparison.
