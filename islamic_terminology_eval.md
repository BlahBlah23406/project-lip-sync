# Islamic Terminology Benchmark Evaluation — Gemma-4 E2B LoRA Adapter

## Verification note (2026-11-01)

This is a qualitative review of the **10 examples** saved in `final_report.json`, not a newly executed inference benchmark across all 10 episodes. BLEU/chrF below are the pre-existing training report metrics on **200 evaluation rows** (`n_eval: 200`); they are not sacred-term-specific scores. Example cells are excerpts, not complete model outputs. Human review remains required before production use.

## Scope
Evaluate the fine-tuned `google/gemma-4/transformers/gemma-4-e2b-it` LoRA adapter
(`gemma4-e2b-en-bn-lora`) on Islamic term-aware translation quality.
Data comes from the staged 10-episode **Islamic Term-Aware Translation Dataset**
(`tmp_existing_ds/Islamic Term-Aware Translation Dataset/`) and the 10
representative validation examples in `kaggle_gemma4_ft/output/final_report.json`.

## Model Under Test
- **Base model:** `google/gemma-4/transformers/gemma-4-e2b-it/1`
- **Adapter path:** `project-lip-sync/kaggle_gemma4_ft/output/gemma4-e2b-en-bn-lora/`
- **Training:** 14,191 rows, 444 steps, Tesla T4 fp16
- **Overall MT metrics:** Base BLEU 0.039 → **Fine-tuned BLEU 16.16**; Base chrF 0.113 → **Fine-tuned chrF 44.24**

## Sacred-Register Criteria
| # | Criterion | Definition | Weight |
|---|-----------|------------|--------|
| 1 | **Honorific preservation** | Divine names (Allah), Prophetic titles (ﷺ, "the Prophet"), and scholarly titles (Shaykh) keep their conventional Bengali honorifics. | High |
| 2 | **Theological accuracy** | Core theological concepts (Tawhid, Shirk, Shafa’ah, Hidayah, etc.) are not distorted, secularized, or over-explained. | High |
| 3 | **Pronoun/register respect** | Pronouns and verb forms for Allah and the Prophet follow Islamic Bengali conventions (e.g., formal/respectful pronouns). | Medium |
| 4 | **Arabic/loan-term handling** | Arabic loanwords (`Subhanallah`, `Insha’Allah`, `Ralfittana`, etc.) are transliterated naturally rather than translated away or left untranslated. | Medium |
| 5 | **Brevity for dubbing** | Translation stays short enough to fit the original timing window without stripping sacred meaning. | Medium |

## Quantitative MT Result (Full Validation)
- **BLEU:** 0.039 → **16.16** (+16.12)
- **chrF:** 0.113 → **44.24** (+44.13)
- **Outputs identical:** false
- **Token-cap behavior:** Base hit the 256-token cap on most outputs and produced degenerate repetition; LoRA rarely hit the cap and generated coherent Bangla.

## Qualitative Error Analysis (10 Representative Examples)

| # | English cue | Sacred element | Reference | Fine-tuned output | Verdict | Notes |
|---|-------------|----------------|-----------|-------------------|---------|-------|
| 1 | "what Allah subhana wa ta'ala wants" | Divine name + tasbih | "আল্লাহর ইচ্ছার সাথে সবচেয়ে সংগতিপূর্ণ" | "যা আল্লাহ সুবহানাহু ওয়া তাআলা চান" | ✅ Pass | Tasbih retained; meaning faithful. |
| 2 | "our Prophet ﷺ mentioned" | Prophetic honorific | "নবী ﷺ বলেছেন" | "নবী ﷺ বলেছেন" | ✅ Pass | Honorific preserved. |
| 3 | "Minister" (context: Yusuf) | Non-sacred but narrative | "মন্ত্রী হলেন" | "মন্ত্রী হলেন" | ✅ Pass | Accurate condensation. |
| 4 | "Sheikh, you mentioned the genocide. Subhanallah" | Scholarly title + tasbih | "শেখ, আপনি গণহত্যার কথা বললেন। সুবহানআল্লাহ" | "শেখ, আপনি গণহত্যা নিয়ে কথা বলেছেন। সুবহানআল্লাহ। ভাবুন, সব কিছু" | ✅ Pass | Source report spells the word for genocide correctly; tasbih retained. |
| 5 | "Allah taught Adam some kalamata Futaba" | Arabic loan terms | "আল্লাহ তাকে কিছু কালামাত বা শব্দ শিখালেন" | "আল্লাহ আদমকে কিছু কথা শিখিয়েছিলেন" | ⚠️ Minor | Omits Arabic loanword entirely; meaning preserved but register flattened. |
| 6 | "Shaitan is promising you poverty... give for Allah" | Theological narrative | "শয়তান আপনাকে দারিদ্র্যের ভয় দেখায় যে, দান করলে আপনি গরিব হয়ে যাবেন" | "শয়তান আপনাকে দারিদ্র্য ও আল্লাহর দানকে তুচ্ছ করে বলছে" | ⚠️ Medium | Adds interpretation not in source; theological point partially shifted. |
| 7 | "disagree, disagree with politeness" | Register | "দ্বিমত হলে বিনয়ে বলুন" | "ভিন্নমত, তবে বিনয়ের সাথে" | ✅ Pass | Polite register, concise. |
| 8 | "If the Ralfittana, ... make Dura to Allah for Hidaya" | Arabic loans + supplication | "ফিতনা না খুঁজে বরং হেদায়েতের জন্য দোয়া করুন" | "যদি কোনো সমস্যা হয়, তবে আল্লাহর কাছে হেদায়েত চান" | ⚠️ Medium | Drops "fitna" Arabic loan and the supplication nuance; over-simplifies. |
| 9 | "walk from Mecca to Medina" | Sacred geography | "মক্কা থেকে মদিনায়" | "মক্কা থেকে মদিনায় যাওয়ার পথে" | ✅ Pass | Geography and intent preserved. |
| 10 | "Ramadan... the Prophet" | Prophetic mention | "রমজান... রাসুল (সা.) যা বুঝিয়েছেন" | "রমজানে নিয়মিত, যা নবী (সা.)-এর" | ⚠️ Minor | Shortened but honorific preserved; slightly ambiguous. |

### Verdict totals
- ✅ Pass: 6 / 10
- ⚠️ Minor issue: 2 / 10
- ⚠️ Medium issue: 2 / 10 (examples 6 and 8)
- ❌ Severe theological/honorific failure: 0 / 10

The labels score the named sacred element only. They do not certify whole-sentence accuracy: example 2 inserts an additional Arabic religious phrase and changes the opening meaning; example 9 has a Bengali case-form issue in the full output. Zero severe labels in this small review is not evidence that the model cannot make severe errors. Timing/syllable-budget compliance was not measured here.

## Key Findings
1. **Honorifics are mostly retained** — Allah, Prophet ﷺ, and scholarly titles survive, which was the biggest gap in the old TinyLlama baseline.
2. **Arabic loanwords are often domesticated** — the model tends to translate the *meaning* of loans like `kalamata Futaba`, `Ralfittana`, and `Dua` into plain Bangla, losing the Islamic register.
3. **Theological paraphrasing can drift** — example 6 reinterprets the source rather than translating it, adding a moral judgment.
4. **Brevity vs. sacred precision trade-off** — the examples show short outputs and some loss of loanwords/nuance; actual duration and syllable-budget compliance still require measurement.

## Recommendations
1. **Pre/post-process honorifics and loans** — Keep a small Bengali-Arabic gazetteer (`Subhanallah`, `Insha’Allah`, `fitna`, `dua`, `Hidayah`) and restore/replace terms after generation.
2. **Inject few-shot Islamic examples** — Add 5–10 sacred-translation exemplars to the translator prompt (`translator.py`) to nudge the model toward register-preserving outputs.
3. **Add a sacred-term critic** — A lightweight rule-based checker flags missing honorifics or loanwords before TTS, triggering a targeted re-translation.
4. **Retrain with term-aware data** — Augment the fine-tuning dataset with parallel pairs that explicitly preserve Arabic loans and honorifics, weighted higher in the loss for Islamic content.

## Files / Artifacts
- LoRA adapter: `project-lip-sync/kaggle_gemma4_ft/output/gemma4-e2b-en-bn-lora/`
- Full training report: `project-lip-sync/kaggle_gemma4_ft/output/final_report.json`
- Islamic dataset: `tmp_existing_ds/Islamic Term-Aware Translation Dataset/Ep 1.txt` … `Ep 10.txt`
- This evaluation: `project-lip-sync/islamic_terminology_eval.md`

## Conclusion
The Gemma-4 LoRA adapter is a large improvement over the base model on raw MT scores and honors most Islamic register requirements. For production dubbing, pair it with a small rule-based sacred-term guard and a few-shot prompt to eliminate the remaining loanword and paraphrase drift.
