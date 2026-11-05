#!/usr/bin/env python
"""
Islamic Term-Aware Translation Benchmark
=========================================
Runs the Gemma-4 model (via Ollama Cloud) against the 10-episode Islamic
Term-Aware Translation Dataset, comparing translations with and without
the honorific preprocessing layer.

Scoring:
  - BLEU and chrF against Bengali reference translations
  - Sacred terminology preservation rate (honorifics, Quranic terms, titles)
  - Per-episode and aggregate breakdown

Output: project-lip-sync/islamic_benchmark_results/
"""

import os
import sys
import re
import json
import time
import pathlib
import requests
from datetime import datetime

# Windows UTF-8
os.environ.setdefault("PYTHONUTF8", "1")

# --- Configuration ---
DATASET_DIR = pathlib.Path(__file__).parent.parent / "tmp_existing_ds" / "Islamic Term-Aware Translation Dataset"
OUTPUT_DIR = pathlib.Path(__file__).parent / "islamic_benchmark_results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Ollama Cloud API — use local proxy (same as working splice pipeline)
# OLLAMA_CLOUD_KEY is the real key, LLM_API_KEY is what translator.py uses
LLM_API_KEY = os.environ.get("LLM_API_KEY", "") or os.environ.get("OLLAMA_CLOUD_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11460/api")
LLM_MODEL = os.environ.get("LLM_MODEL_NAME", "gemma4:31b")

# Do NOT load .env from project-lip-sync (it has expired ollama.com URL)
# The local proxy at 127.0.0.1:11460 handles auth via OLLAMA_CLOUD_KEY

SYSTEM_PROMPT = """You are an expert Islamic translator specializing in English to Bengali (Bangla) translation.
Translate the following English text into natural, fluent Bengali.
Preserve the Islamic register and all sacred terminology.
If the text contains placeholder tokens like __SACRED_N__, copy them exactly as-is into the translation.
Do not add commentary, notes, or explanations — only output the Bengali translation."""

# --- Honorific Gazetteeer (from translator.py) ---
_TERM_RULES = [
    (r"ﷺ|(?i:\(saw\)|\(pbuh\))|\((?:SAW|PBUH)\)|\b(?:SAW|PBUH)\b|(?i:\bsallallahu alayhi wa sallam\b)", 'ﷺ'),
    (r"\(RA\)|\bRA\b|(?i:\bradi(?:y)?allahu anh[ua]\b)", '(রা.)'),
    (r"\(AS\)|\bAS\b|(?i:\balayhi(?:s)? salaam?\b|\bpeace be upon him\b)", '(আ.)'),
    (r"(?i:\bAllah\s*\(SWT\)|\bAllah subhan(?:a|ahu) wa ta['']?ala\b)", 'আল্লাহ সুবহানাহু ওয়াতাআলা'),
    (r"\(SWT\)|\bSWT\b", 'সুবহানাহু ওয়াতাআলা'),
    (r"(?i:\bSubhanallah\b)", 'সুবহানআল্লাহ'),
    (r"(?i:\bAlhamdulil{1,2}ah\b)", 'আলহামদুলিল্লাহ'),
    (r"(?i:\bInsha['']?\s*Allah\b)", 'ইনশাআল্লাহ'),
    (r"(?i:\bMasha['']?\s*Allah\b)", 'মাশাআল্লাহ'),
    (r"(?i:\bBismillah\b)", 'বিসমিল্লাহ'),
    (r"(?i:\bJazakallah\b)", 'জাযাকাল্লাহ'),
    (r"(?i:\bAllah\b)", 'আল্লাহ'),
    (r"(?i:\bdu['']?a\b)", 'দোয়া'),
    (r"(?i:\bfitnah?\b)", 'ফিতনা'),
    (r"(?i:\bhidayah?\b)", 'হেদায়েত'),
    (r"(?i:\bTawhid\b)", 'তাওহিদ'),
    (r"(?i:\bShirk\b)", 'শিরক'),
    (r"(?i:\bShafa['']?ah\b)", 'শাফাআত'),
    (r"(?i:\bQur['']?an\b)", 'কুরআন'),
    (r"(?i:\bHadith\b)", 'হাদিস'),
    (r"(?i:\bSunnah\b)", 'সুন্নাহ'),
    (r"(?i:\bSalah\b)", 'সালাহ'),
    (r"(?i:\bZakat\b)", 'যাকাত'),
    (r"(?i:\bHajj\b)", 'হজ্জ'),
    (r"(?i:\bUmmah\b)", 'উম্মাহ'),
    (r"(?i:\b(?:Shaykh|Sheikh)\b)", 'শেখ'),
    (r"(?i:\bImam\b)", 'ইমাম'),
]
_TERM_RULES.extend([
    (r"ﷻ", "ﷻ"),
    (r"\(RH\)|\bRH\b|(?i:\brahimahullah\b)", "(রহ.)"),
    (r"رضي الله عنه", "رضي الله عنه"),
    (r"عليه السلام", "عليه السلام"),
    (r"رحمه الله", "رحمه الله"),
])

_SACRED_PATTERN = re.compile("|".join(f"(?P<T{i}>{pattern})" for i, (pattern, _) in enumerate(_TERM_RULES)))

# Bengali sacred terms to check for in output
BENGALI_SACRED_TERMS = [
    'ﷺ', '(রা.)', '(আ.)', 'আল্লাহ', 'সুবহানাহু', 'সুবহানআল্লাহ', 'আলহামদুলিল্লাহ',
    'ইনশাআল্লাহ', 'মাশাআল্লাহ', 'বিসমিল্লাহ', 'জাযাকাল্লাহ', 'দোয়া', 'ফিতনা',
    'হেদায়েত', 'তাওহিদ', 'শিরক', 'শাফাআত', 'কুরআন', 'হাদিস', 'সুন্নাহ',
    'সালাহ', 'যাকাত', 'হজ্জ', 'উম্মাহ', 'শেখ', 'ইমাম', '(রহ.)'
]

# English sacred terms to detect in source
ENGLISH_SACRED_TERMS = [
    'ﷺ', '(SAW)', '(PBUH)', '(RA)', '(AS)', '(SWT)', 'Allah', 'Subhanallah',
    'Alhamdulillah', "Insha'Allah", 'Insha Allah', 'MashaAllah', 'Masha Allah',
    'Bismillah', 'JazakAllah', 'dua', 'fitna', 'hidaya', 'Tawhid', 'Shirk',
    'Quran', 'Hadith', 'Sunnah', 'Salah', 'Zakat', 'Hajj', 'Ummah',
    'Shaykh', 'Sheikh', 'Imam', 'Prophet'
]


def pre_process_honorifics(text):
    """Replace honorifics with placeholder tokens before translation."""
    replacements = {}
    def protect(match):
        token = f"__SACRED_{len(replacements)}__"
        while token in text or token in replacements:
            token = "_" + token
        replacements[token] = _TERM_RULES[int(match.lastgroup[1:])][1]
        return token
    return _SACRED_PATTERN.sub(protect, text), replacements


def post_process_honorifics(text, replacements=None):
    """Restore placeholder tokens to Bengali/Arabic forms."""
    replacements = replacements or {}
    for token, value in replacements.items():
        text = re.sub(re.escape(token), lambda _: value, text, flags=re.IGNORECASE)
    # Clean up any remaining placeholders
    text = re.sub(r"__SACRED_\d+__", "", text, flags=re.IGNORECASE)
    return text


def parse_episode_file(filepath):
    """Parse an episode file into English-Bengali translation pairs.
    
    Format: English paragraph(s) followed by Bengali paragraph(s),
    separated by a blank line. Multiple pairs per file.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split into blocks separated by blank lines
    blocks = [b.strip() for b in re.split(r'\n\s*\n', content) if b.strip()]
    
    pairs = []
    i = 0
    while i < len(blocks) - 1:
        eng_block = blocks[i]
        ben_block = blocks[i + 1]
        
        # Heuristic: English blocks contain mostly Latin script, Bengali blocks contain Bengali script
        has_bengali = bool(re.search(r'[\u0980-\u09FF]', ben_block))
        has_latin_eng = bool(re.search(r'[a-zA-Z]{3,}', eng_block))
        ben_has_latin = bool(re.search(r'[a-zA-Z]{3,}', ben_block))
        eng_has_bengali = bool(re.search(r'[\u0980-\u09FF]', eng_block))
        
        # English block should be primarily Latin, Bengali block should have Bengali script
        if has_latin_eng and not eng_has_bengali and has_bengali:
            pairs.append({
                'english': eng_block,
                'reference': ben_block
            })
            i += 2
        else:
            i += 1
    
    return pairs


def translate_via_ollama(text, use_honorifics=True):
    """Translate text using Ollama Cloud API, with or without honorific preprocessing."""
    if use_honorifics:
        processed_text, replacements = pre_process_honorifics(text)
    else:
        processed_text = text
        replacements = {}
    
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": processed_text}
        ],
        "stream": False,
        "options": {"temperature": 0.3}
    }
    
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    
    url = LLM_BASE_URL.rstrip("/") + "/chat"
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        translated = data.get("message", {}).get("content", "").strip()
    except Exception as e:
        return f"[ERROR: {e}]", replacements
    
    if use_honorifics and replacements:
        try:
            translated = post_process_honorifics(translated, replacements)
        except Exception as e:
            translated = translated + f" [POST_PROCESS_ERROR: {e}]"
    
    return translated, replacements


def compute_bleu(reference, hypothesis):
    """Simple corpus-level BLEU approximation (up to 4-grams)."""
    import math
    
    ref_tokens = reference.split()
    hyp_tokens = hypothesis.split()
    
    if len(hyp_tokens) == 0:
        return 0.0
    
    # Brevity penalty
    bp = min(1.0, math.exp(1 - len(ref_tokens) / max(len(hyp_tokens), 1)))
    
    precisions = []
    for n in range(1, 5):
        ref_ngrams = {}
        for i in range(len(ref_tokens) - n + 1):
            ng = tuple(ref_tokens[i:i+n])
            ref_ngrams[ng] = ref_ngrams.get(ng, 0) + 1
        
        hyp_ngrams = {}
        for i in range(len(hyp_tokens) - n + 1):
            ng = tuple(hyp_tokens[i:i+n])
            hyp_ngrams[ng] = hyp_ngrams.get(ng, 0) + 1
        
        matches = 0
        total = max(len(hyp_tokens) - n + 1, 0)
        for ng, count in hyp_ngrams.items():
            matches += min(count, ref_ngrams.get(ng, 0))
        
        if total == 0:
            precisions.append(0.0)
        else:
            precisions.append(matches / total)
    
    # Geometric mean
    if all(p > 0 for p in precisions):
        geo_mean = math.exp(sum(math.log(p) for p in precisions) / 4)
    else:
        geo_mean = 0.0
    
    return bp * geo_mean * 100


def compute_chrf(reference, hypothesis):
    """Simple character-level F-score (chrF approximation)."""
    ref_chars = list(reference.replace(' ', ''))
    hyp_chars = list(hypothesis.replace(' ', ''))
    
    if len(hyp_chars) == 0:
        return 0.0
    
    # Character n-grams (n=6, standard for chrF)
    n = 6
    total_precision = 0.0
    total_recall = 0.0
    num_n = 0
    
    for k in range(1, n + 1):
        ref_ngrams = {}
        for i in range(len(ref_chars) - k + 1):
            ng = tuple(ref_chars[i:i+k])
            ref_ngrams[ng] = ref_ngrams.get(ng, 0) + 1
        
        hyp_ngrams = {}
        for i in range(len(hyp_chars) - k + 1):
            ng = tuple(hyp_chars[i:i+k])
            hyp_ngrams[ng] = hyp_ngrams.get(ng, 0) + 1
        
        matches = 0
        hyp_total = max(len(hyp_chars) - k + 1, 0)
        ref_total = max(len(ref_chars) - k + 1, 0)
        
        for ng, count in hyp_ngrams.items():
            matches += min(count, ref_ngrams.get(ng, 0))
        
        if hyp_total > 0:
            total_precision += matches / hyp_total
        if ref_total > 0:
            total_recall += matches / ref_total
        num_n += 1
    
    if num_n == 0:
        return 0.0
    
    avg_precision = total_precision / num_n
    avg_recall = total_recall / num_n
    
    if avg_precision + avg_recall == 0:
        return 0.0
    
    f_score = 2 * avg_precision * avg_recall / (avg_precision + avg_recall)
    return f_score * 100


def count_sacred_terms(text, terms=None):
    """Count occurrences of sacred terms in text."""
    if terms is None:
        terms = BENGALI_SACRED_TERMS
    counts = {}
    total = 0
    for term in terms:
        c = text.count(term)
        if c > 0:
            counts[term] = c
            total += c
    return total, counts


def detect_english_sacred(text):
    """Detect English sacred terms in source text."""
    found = []
    for term in ENGLISH_SACRED_TERMS:
        if re.search(re.escape(term), text, re.IGNORECASE):
            found.append(term)
    return found


def run_benchmark():
    """Main benchmark runner."""
    print(f"Islamic Term-Aware Translation Benchmark")
    print(f"=" * 60)
    print(f"Model: {LLM_MODEL}")
    print(f"API: {LLM_BASE_URL}")
    print(f"Dataset: {DATASET_DIR}")
    print(f"Output: {OUTPUT_DIR}")
    print()
    
    all_results = []
    per_episode = []
    
    episode_files = sorted(DATASET_DIR.glob("Ep *.txt"))
    print(f"Found {len(episode_files)} episode files")
    
    for ep_file in episode_files:
        ep_name = ep_file.stem  # e.g. "Ep 1"
        print(f"\n--- Processing {ep_name} ---")
        
        pairs = parse_episode_file(ep_file)
        print(f"  Parsed {len(pairs)} translation pairs")
        
        if not pairs:
            print(f"  WARNING: No pairs found, skipping")
            continue
        
        ep_results = []
        
        for idx, pair in enumerate(pairs):
            eng = pair['english']
            ref = pair['reference']
            
            # Detect sacred terms in source
            eng_sacred = detect_english_sacred(eng)
            
            print(f"  [{idx+1}/{len(pairs)}] Translating ({len(eng)} chars, {len(eng_sacred)} sacred terms)...", end=" ", flush=True)
            
            # Translate without honorifics
            trans_raw, _ = translate_via_ollama(eng, use_honorifics=False)
            
            # Translate with honorifics
            trans_hon, replacements = translate_via_ollama(eng, use_honorifics=True)
            
            # Score both
            bleu_raw = compute_bleu(ref, trans_raw)
            bleu_hon = compute_bleu(ref, trans_hon)
            chrf_raw = compute_chrf(ref, trans_raw)
            chrf_hon = compute_chrf(ref, trans_hon)
            
            # Sacred term preservation
            ref_sacred_count, ref_sacred_terms = count_sacred_terms(ref)
            raw_sacred_count, raw_sacred_terms = count_sacred_terms(trans_raw)
            hon_sacred_count, hon_sacred_terms = count_sacred_terms(trans_hon)
            
            result = {
                'episode': ep_name,
                'pair_index': idx,
                'english': eng[:200] + ('...' if len(eng) > 200 else ''),
                'reference': ref[:200] + ('...' if len(ref) > 200 else ''),
                'translation_raw': trans_raw[:300],
                'translation_honorific': trans_hon[:300],
                'bleu_raw': round(bleu_raw, 2),
                'bleu_honorific': round(bleu_hon, 2),
                'chrf_raw': round(chrf_raw, 2),
                'chrf_honorific': round(chrf_hon, 2),
                'english_sacred_terms': eng_sacred,
                'ref_sacred_count': ref_sacred_count,
                'raw_sacred_count': raw_sacred_count,
                'hon_sacred_count': hon_sacred_count,
                'ref_sacred_terms': ref_sacred_terms,
                'raw_sacred_terms': raw_sacred_terms,
                'hon_sacred_terms': hon_sacred_terms,
                'honorific_replacements': len(replacements),
            }
            
            ep_results.append(result)
            all_results.append(result)
            
            delta_bleu = bleu_hon - bleu_raw
            delta_chrf = chrf_hon - chrf_raw
            print(f"BLEU raw={bleu_raw:.1f} hon={bleu_hon:.1f} (Δ{delta_bleu:+.1f}) | chrF raw={chrf_raw:.1f} hon={chrf_hon:.1f} (Δ{delta_chrf:+.1f}) | sacred: ref={ref_sacred_count} raw={raw_sacred_count} hon={hon_sacred_count}")
            
            # Rate limit
            time.sleep(1)
        
        # Per-episode aggregate
        ep_bleu_raw = sum(r['bleu_raw'] for r in ep_results) / len(ep_results) if ep_results else 0
        ep_bleu_hon = sum(r['bleu_honorific'] for r in ep_results) / len(ep_results) if ep_results else 0
        ep_chrf_raw = sum(r['chrf_raw'] for r in ep_results) / len(ep_results) if ep_results else 0
        ep_chrf_hon = sum(r['chrf_honorific'] for r in ep_results) / len(ep_results) if ep_results else 0
        ep_sacref = sum(r['ref_sacred_count'] for r in ep_results)
        ep_sacraw = sum(r['raw_sacred_count'] for r in ep_results)
        ep_sachon = sum(r['hon_sacred_count'] for r in ep_results)
        
        per_episode.append({
            'episode': ep_name,
            'num_pairs': len(ep_results),
            'avg_bleu_raw': round(ep_bleu_raw, 2),
            'avg_bleu_honorific': round(ep_bleu_hon, 2),
            'avg_chrf_raw': round(ep_chrf_raw, 2),
            'avg_chrf_honorific': round(ep_chrf_hon, 2),
            'total_ref_sacred': ep_sacref,
            'total_raw_sacred': ep_sacraw,
            'total_hon_sacred': ep_sachon,
            'sacred_preservation_raw': round(ep_sacraw / max(ep_sacref, 1) * 100, 1),
            'sacred_preservation_honorific': round(ep_sachon / max(ep_sacref, 1) * 100, 1),
        })
        
        # Save per-episode results
        with open(OUTPUT_DIR / f"{ep_name.replace(' ', '_')}_results.json", 'w', encoding='utf-8') as f:
            json.dump(ep_results, f, ensure_ascii=False, indent=2)
    
    # Aggregate
    total_pairs = len(all_results)
    agg_bleu_raw = sum(r['bleu_raw'] for r in all_results) / total_pairs if total_pairs else 0
    agg_bleu_hon = sum(r['bleu_honorific'] for r in all_results) / total_pairs if total_pairs else 0
    agg_chrf_raw = sum(r['chrf_raw'] for r in all_results) / total_pairs if total_pairs else 0
    agg_chrf_hon = sum(r['chrf_honorific'] for r in all_results) / total_pairs if total_pairs else 0
    total_sacref = sum(r['ref_sacred_count'] for r in all_results)
    total_sacraw = sum(r['raw_sacred_count'] for r in all_results)
    total_sachon = sum(r['hon_sacred_count'] for r in all_results)
    
    summary = {
        'benchmark_date': datetime.now().isoformat(),
        'model': LLM_MODEL,
        'api': LLM_BASE_URL,
        'total_episodes': len(episode_files),
        'total_pairs': total_pairs,
        'aggregate': {
            'avg_bleu_raw': round(agg_bleu_raw, 2),
            'avg_bleu_honorific': round(agg_bleu_hon, 2),
            'avg_chrf_raw': round(agg_chrf_raw, 2),
            'avg_chrf_honorific': round(agg_chrf_hon, 2),
            'bleu_improvement': round(agg_bleu_hon - agg_bleu_raw, 2),
            'chrf_improvement': round(agg_chrf_hon - agg_chrf_raw, 2),
            'total_ref_sacred_terms': total_sacref,
            'total_raw_sacred_preserved': total_sacraw,
            'total_honorific_sacred_preserved': total_sachon,
            'sacred_preservation_rate_raw': round(total_sacraw / max(total_sacref, 1) * 100, 1),
            'sacred_preservation_rate_honorific': round(total_sachon / max(total_sacref, 1) * 100, 1),
        },
        'per_episode': per_episode,
        'note': 'Benchmark uses Ollama Cloud gemma4:31b model (full model, not the E2B LoRA adapter which requires Kaggle GPU). The honorific preprocessing layer is the same as implemented in translator.py.',
    }
    
    with open(OUTPUT_DIR / "summary.json", 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    with open(OUTPUT_DIR / "all_results.json", 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'=' * 60}")
    print(f"BENCHMARK COMPLETE")
    print(f"{'=' * 60}")
    print(f"Total pairs: {total_pairs}")
    print(f"BLEU:  raw={agg_bleu_raw:.2f}  honorific={agg_bleu_hon:.2f}  (Δ{agg_bleu_hon-agg_bleu_raw:+.2f})")
    print(f"chrF:  raw={agg_chrf_raw:.2f}  honorific={agg_chrf_hon:.2f}  (Δ{agg_chrf_hon-agg_chrf_raw:+.2f})")
    print(f"Sacred term preservation:")
    print(f"  Reference total: {total_sacref}")
    print(f"  Raw: {total_sacraw} ({total_sacraw/max(total_sacref,1)*100:.1f}%)")
    print(f"  Honorific: {total_sachon} ({total_sachon/max(total_sacref,1)*100:.1f}%)")
    print(f"\nResults saved to: {OUTPUT_DIR}")
    
    return summary


if __name__ == "__main__":
    run_benchmark()
