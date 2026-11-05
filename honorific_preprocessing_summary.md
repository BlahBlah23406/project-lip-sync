# Islamic Honorific Pre/Post Processing — Implementation Summary

**Date:** 2026-11-04  
**File modified:** `translator.py`  
**Backup:** `translator.py.bak-20261104-1245`

## What was implemented

A regex-based honorific pre/post-processing layer in `translator.py` that preserves Islamic honorifics and sacred Arabic terms during English→Bangla translation.

## Changes

### 1. HONORIFIC_GAZETTEER dictionary
A comprehensive mapping of English Islamic terms to:
- **Placeholder tokens** (e.g., `__HON_SAW__`, `__TERM_SUBHANALLAH__`) used during translation
- **Bengali/Arabic restoration forms** (e.g., `ﷺ`, `সুবহানআল্লাহ`)

### 2. `pre_process_honorifics(text)` function
- Scans English input for honorifics and sacred terms
- Replaces them with placeholder tokens that survive translation
- Returns processed text + set of inserted placeholders

### 3. `post_process_honorifics(text, expected_placeholders)` function
- Restores placeholder tokens to proper Bengali/Arabic forms
- Also handles lowercase variants some models produce
- Calls `sacred_term_critic` if expected placeholders are provided

### 4. `sacred_term_critic(original, restored, expected)` function
- Lightweight rule-based checker
- Logs warnings when expected honorifics are missing from the translated output
- Enables targeted re-translation if honorifics are dropped

### 5. Integration in `translate_segments()`
- Pre-processes each segment text BEFORE sending to Ollama/Claude
- Post-processes each translated result AFTER receiving it back
- Works with both Ollama Cloud and Anthropic Claude translation paths

## Honorifics/Terms Covered (26 total)

| Category | English Forms | Bengali Restoration |
|----------|--------------|-------------------|
| Prophetic honorific | ﷺ, (SAW), (PBUH), sallallahu alayhi wa sallam, peace be upon him | ﷺ |
| Companion honorific | (RA), radiAllahu anhu, radiyallahu anhu | (রা.) |
| Prophet name honorific | (AS), alayhi salam, alayhis salam | (আ.) |
| Divine phrase | Allah subhana wa ta'ala, Allah (SWT), SWT | আল্লাহ সুবহানাহু ওয়া তাআলা |
| Sacred terms | Subhanallah, Alhamdulillah, Insha'Allah, Bismillah, MashaAllah, JazakAllah | সুবহানআল্লাহ, আলহামদুলিল্লাহ, ইনশাআল্লাহ, বিসমিল্লাহ, মাশাআল্লাহ, জাযাকাল্লাহ |
| Islamic concepts | dua, fitna, hidaya/Hidayah, salah, zakat, hajj, ummah | দোয়া, ফিতনা, হেদায়েত, সালাহ, যাকাত, হজ্জ, উম্মাহ |
| Titles | shaykh/sheikh, imam | শেখ, ইমাম |
| Scripture | Quran, Hadith, Sunnah | কুরআন, হাদিস, সুন্নাহ |
| Theology | Tawhid, Shirk | তাওহিদ, শিরক |

## Validation

- `python -m py_compile translator.py` — PASS
- 6 functional tests covering: single honorific, multiple honorifics, no honorifics, Arabic script, critic warning on dropped term, RA/AS honorifics — ALL PASS

## Design decisions

- No changes to existing SYSTEM_PROMPT, translation logic, or API calls
- Only adds the pre/post-processing layer and integrates it into `translate_segments`
- Placeholders use `__HON_*__` / `__TERM_*__` format to avoid collision with natural text
- Case-insensitive matching for robustness
- Compiled regex patterns for efficiency
