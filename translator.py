import os
import re
import time
import httpx
import anthropic
from pathlib import Path
from dotenv import load_dotenv

for _env_dir in (Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent):
    load_dotenv(_env_dir / ".env", override=False)

LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL") or "http://localhost:11434/api"
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL") or "gemma4:e2b"

SYSTEM_PROMPT = """You are a translator specializing in Islamic religious content.
Translate the following English lecture captions into Bangla (বাংলা).

CRITICAL TIME CONSTRAINT & FORMAT RULES:
The translated text will be converted to speech (TTS) and must be SPOKEN ALOUD inside a
fixed time window given for each line. This is the hardest constraint in the task.

A line that does not fit its window cannot be slowed down -- it gets sped up, and sped-up
speech is unintelligible. So: LENGTH IS THE CONSTRAINT, and meaning must be fitted into it.
- If the faithful translation does not fit, COMPRESS THE MEANING: say the same thing with
  fewer words. Drop intensifiers, filler, repetition, and rhetorical padding.
- English lecture speech is repetitive; Bangla does not need to reproduce every repetition.
- A shorter line that keeps the core meaning is ALWAYS better than a complete line that
  overruns its window.
Within that limit, the Bangla must still be grammatical and sound natural when spoken.

Additional Annotation Requirements (Speaker Diarization & Quote Detection):
1. Speaker Tracking:
   - Identify different speakers based on text flow, style, dialogue shifts, Q&A structures, or tags (e.g., host vs speaker, or Zakir vs Audience).
   - Label each translation line with its speaker ID using prefix tags: `[SPEAKER_A]`, `[SPEAKER_B]`, `[SPEAKER_C]`, or `[SPEAKER_D]`.
   - `[SPEAKER_A]` is the default/main speaker. Use subsequent IDs consistently for other speakers.

2. Classical Arabic/Quranic Recitation Detection:
   - Identify segments where the lecturer is ACTUALLY reciting classical Arabic (in the Arabic language, either written in Arabic script like "الحمد لله" or transliterated Arabic like "Alhamdulillah", "Bismillah", "Innama al-a'malu...", or explicit transcript notes like "[Arabic]" or "(reciting Arabic)").
   - CRITICAL: Do NOT tag a segment as [QUOTE_ARABIC] if the lecturer is speaking in English or reading an English translation of a Quranic verse, Hadith, or quote (e.g., "Indeed, Allah is with the patient" or "The Prophet said..."). These must be translated into Bangla and NOT tagged with [QUOTE_ARABIC] so they can be spoken in Bangla TTS.
   - ONLY use [QUOTE_ARABIC] when the speaker is speaking the Arabic language. If they are speaking English, even if they are translating a Quranic verse, DO NOT use the [QUOTE_ARABIC] tag.
   - If a segment is an Arabic recitation, translate its meaning as usual to Bangla, but **prepend** the tag `[QUOTE_ARABIC]` after the speaker tag.
   - Example output line for a quote: `1. [SPEAKER_A] [QUOTE_ARABIC] আলহামদুলিল্লাহি রাব্বিল আলামিন।`

General Rules:
- Preserve Islamic terms as-is or in established Bangla transliteration:
  Allah, Quran, Hadith, Sunnah, Salah, Zakat, Hajj, Ummah, Sheikh, Imam,
  InshaAllah (ইনশাআল্লাহ), Alhamdulillah (আলহামদুলিল্লাহ), SubhanAllah (সুবহানআল্লাহ),
  Bismillah (বিস্মিল্লাহ), MashaAllah (মাশাআল্লাহ), JazakAllah (জাযাকাল্লাহ), etc.
- Keep Arabic phrases (du'as, Quranic verses) in Arabic script or their common transliteration if helpful, alongside translation.
- Each input segment is prefixed with its duration: e.g. "[2.3s] 1. Hello everyone"
- HARD BUDGET: at most 3.5 spoken Bangla syllables per 1 second of that duration. A [2.3s]
  line gets at most ~8 syllables. Count them. Going over means the line gets sped up and
  becomes unintelligible, which ruins the dub -- staying under is more important than
  capturing every nuance.
- Prefer short everyday Bangla words over long formal/Sanskritised ones.
- Do not pad. Do not add words that are not in the English.
- Output ONLY the numbered translations, one per line, matching the input numbering exactly.
- Do not add explanations, notes, or extra text."""

SYSTEM_PROMPT += "\nCopy every __SACRED_N__ token exactly once, unchanged, in the corresponding translated line. Preserve respectful Islamic register; never omit sacred tokens to meet timing. Do not tag English as Arabic merely because it contains a sacred token."

MODEL = "claude-haiku-4-5"


# --- Islamic Honorific Pre/Post Processing ---
#
# This layer preserves Islamic honorifics and sacred Arabic terms across translation.
# English input honorifics are replaced with placeholder tokens BEFORE translation,
# then restored to their proper Bengali/Arabic forms AFTER translation.
# This prevents the LLM from dropping, translating, or secularizing sacred terms.

# Abbreviations are case-sensitive: ordinary English "as" and "saw" are words.
_TERM_RULES = [
    (r"ﷺ|(?i:\(saw\)|\(pbuh\))|\((?:SAW|PBUH)\)|\b(?:SAW|PBUH)\b|(?i:\bsallallahu alayhi wa sallam\b)", 'ﷺ'),
    (r"\(RA\)|\bRA\b|(?i:\bradi(?:y)?allahu anh[ua]\b)", '(রা.)'),
    (r"\(AS\)|\bAS\b|(?i:\balayhi(?:s)? salaam?\b|\bpeace be upon him\b)", '(আ.)'),
    (r"(?i:\bAllah\s*\(SWT\)|\bAllah subhan(?:a|ahu) wa ta['’]?ala\b)", 'আল্লাহ সুবহানাহু ওয়াতাআলা'),
    (r"\(SWT\)|\bSWT\b", 'সুবহানাহু ওয়াতাআলা'),
    (r"(?i:\bSubhanallah\b)", 'সুবহানআল্লাহ'),
    (r"(?i:\bAlhamdulil{1,2}ah\b)", 'আলহামদুলিল্লাহ'),
    (r"(?i:\bInsha['’]?\s*Allah\b)", 'ইনশাআল্লাহ'),
    (r"(?i:\bMasha['’]?\s*Allah\b)", 'মাশাআল্লাহ'),
    (r"(?i:\bBismillah\b)", 'বিসমিল্লাহ'),
    (r"(?i:\bJazakallah\b)", 'জাযাকাল্লাহ'),
    (r"(?i:\bAllah\b)", 'আল্লাহ'),
    (r"(?i:\bdu['’]?a\b)", 'দোয়া'),
    (r"(?i:\bfitnah?\b)", 'ফিতনা'),
    (r"(?i:\bhidayah?\b)", 'হেদায়েত'),
    (r"(?i:\bTawhid\b)", 'তাওহিদ'),
    (r"(?i:\bShirk\b)", 'শিরক'),
    (r"(?i:\bShafa['’]?ah\b)", 'শাফাআত'),
    (r"(?i:\bQur['’]?an\b)", 'কুরআন'),
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

HONORIFIC_GAZETTEER = dict(_TERM_RULES)


def sacred_term_critic(original, restored, expected=None):
    """Return occurrence-aware checks for canonical sacred terms in a translation."""
    from collections import Counter
    if expected is None:
        _, expected = pre_process_honorifics(original)
    counts = Counter(expected.values())
    return {term: {"expected": count, "observed": restored.count(term),
                   "preserved": restored.count(term) >= count}
            for term, count in counts.items()}


def pre_process_honorifics(text: str) -> tuple[str, dict[str, str]]:
    """Protect each occurrence separately so repeated honorifics cannot disappear."""
    replacements = {}
    def protect(match):
        token = f"__SACRED_{len(replacements)}__"
        while token in text or token in replacements:
            token = "_" + token
        replacements[token] = _TERM_RULES[int(match.lastgroup[1:])][1]
        return token
    return _SACRED_PATTERN.sub(protect, text), replacements


def post_process_honorifics(text: str, expected_placeholders=None) -> str:
    """Restore protected terms; reject missing/duplicated tokens before TTS."""
    replacements = expected_placeholders or {}
    for token, value in replacements.items():
        if len(re.findall(re.escape(token), text, flags=re.IGNORECASE)) != 1:
            raise ValueError(f"Sacred term token missing or duplicated: {token}")
        text = re.sub(re.escape(token), lambda _: value, text, flags=re.IGNORECASE)
    if re.search(r"__SACRED_\d+__", text, flags=re.IGNORECASE):
        raise ValueError("Unknown sacred term token in translation")
    return text


# --- Helper Methods ---

def _clean_text(text: str) -> str:
    """Strip out <think>...</think> tags which are output by some advanced reasoning models."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_segment_line(line: str) -> tuple[str, str, bool]:
    """Parse speaker ID and Arabic quote tags from a raw translation line."""
    cleaned = _clean_text(line).strip()
    
    # Parse [SPEAKER_X] (case-insensitive check)
    speaker = "SPEAKER_A"
    speaker_match = re.search(r"\[(SPEAKER_[A-D])\]", cleaned, re.IGNORECASE)
    if speaker_match:
        speaker = speaker_match.group(1).upper()
        cleaned = re.sub(r"\[SPEAKER_[A-D]\]", "", cleaned, flags=re.IGNORECASE).strip()

    # Parse [QUOTE_ARABIC] (case-insensitive check)
    is_arabic_quote = False
    if re.search(r"\[QUOTE_ARABIC\]", cleaned, re.IGNORECASE):
        is_arabic_quote = True
        cleaned = re.sub(r"\[QUOTE_ARABIC\]", "", cleaned, flags=re.IGNORECASE).strip()

    # Strip any leading duration marker the model may echo, e.g. [2.3s]
    cleaned = re.sub(r"^\[\d+(?:\.\d+)?s\]\s*", "", cleaned).strip()
        
    return cleaned, speaker, is_arabic_quote


# --- Ollama Cloud Engine ---

def _get_ollama_endpoint() -> str:
    return os.getenv("LLM_BASE_URL") or "https://ollama.com/api"


def _translate_single_segment_ollama(segment: dict) -> dict:
    """Ollama fallback translation for a single segment."""
    endpoint = _get_ollama_endpoint()
    api_key = os.getenv("LLM_API_KEY")
    model_name = os.getenv("LLM_MODEL_NAME") or "qwen3.5:397b-cloud"
    
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        
    prompt = f"Translate the following single English lecture segment into extremely concise Bangla. Do not include any explanations. Maintain the same formatting rules.\nSegment: [{segment['duration']:.1f}s] {segment['text']}"
    
    payload = {
        "model": model_name,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False
    }
    
    # Try cloud first, then local server.
    try:
        response = httpx.post(f"{endpoint}/generate", json=payload, headers=headers, timeout=60.0)
        if response.status_code != 200:
            raise RuntimeError(f"Ollama generation failed (HTTP {response.status_code}): {response.text}")
            
        res_data = response.json()
        raw = res_data.get("response", "").strip()
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", raw).strip()
        cleaned = re.sub(r"^\[\d+\]\s*", "", cleaned).strip()
        
        text, speaker, is_quote = _parse_segment_line(cleaned)
        return {"text": text, "speaker": speaker, "is_arabic_quote": is_quote}
    except Exception as cloud_err:
        print(f"  Cloud Ollama single-segment failed ({cloud_err}); trying local Ollama...")
        try:
            payload["model"] = LOCAL_LLM_MODEL
            response = httpx.post(f"{LOCAL_LLM_BASE_URL}/generate", json=payload, timeout=120.0)
            response.raise_for_status()
            res_data = response.json()
            raw = res_data.get("response", "").strip()
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", raw).strip()
            cleaned = re.sub(r"^\[\d+\]\s*", "", cleaned).strip()
            text, speaker, is_quote = _parse_segment_line(cleaned)
            return {"text": text, "speaker": speaker, "is_arabic_quote": is_quote}
        except Exception as local_err:
            print(f"Ollama fallback translation failed: cloud={cloud_err}, local={local_err}")
            return {"text": segment["text"], "speaker": "SPEAKER_A", "is_arabic_quote": False}


SHORTER_SYSTEM_PROMPT = """You are a translator specializing in Islamic religious content.
You MUST translate the English text into Bangla (বাংলা) that is EXTREMELY SHORT and CONCISE.

CRITICAL: Your translation MUST be shorter than the syllable budget provided.
- Bangla speech timing is based on syllables, not characters: aim for about 3-4 syllables per second.
- Use the shortest possible Bangla phrasing while preserving the core meaning.
- Drop filler words, hedging, and unnecessary detail.
- Use common abbreviations and shorter synonyms.
- The result must sound natural when spoken aloud, but brevity is the TOP priority.
- Preserve Islamic terms (Allah, Quran, Hadith, etc.) in standard Bangla transliteration.
- Output ONLY the Bangla translation. No explanations, no numbering, no tags."""


def count_bangla_syllables(text: str) -> int:
    """
    Estimate syllable count for Bangla text.

    A simple phonetic heuristic: each vowel nucleus (standalone vowel or
    vowel sign attached to a consonant) counts as one syllable. Consonants
    without a following vowel sign also form a syllable (often a final
    consonant/cluster).
    """
    if not text:
        return 0

    # Standalone Bangla vowels
    standalone_vowels = set("অআইঈউঊঋএঐওঔ")
    # Bangla vowel signs (কার) attached to consonants
    vowel_signs = set("ািীুৃেৈোৌ")

    count = 0
    has_vowel_sign = False

    for ch in text:
        if ch in standalone_vowels:
            count += 1
            has_vowel_sign = False
        elif ch in vowel_signs:
            # A vowel sign attached to a consonant creates a new syllable nucleus.
            count += 1
            has_vowel_sign = True
        elif ch == "্":
            # Hasant joins consonants; no new syllable here.
            continue
        elif "\u0980" <= ch <= "\u09FF":
            # Bangla consonant or other character.
            if not has_vowel_sign:
                # Treat an isolated consonant as its own syllable (closed syllable).
                count += 1
            has_vowel_sign = False
        else:
            # Non-Bangla characters (spaces, punctuation, digits): reset state
            # but do not increment the syllable counter.
            has_vowel_sign = False

    return max(count, 1)


def _accept_shorter(cleaned: str, current_text: str, max_syllables: int) -> str | None:
    """Decide whether a reprompt result is worth keeping.

    ANY strictly shorter line is a win and is kept. The old rule demanded the result
    land under `max_syllables * 1.2` and threw it away otherwise -- so a line that
    came back 30% shorter (real, useful compression) was discarded, and the pipeline
    fell back to brute-force speeding up the ORIGINAL long line. That fallback is what
    produced the unintelligible audio; never discard a shorter line again.
    """
    if not cleaned:
        return None
    got = count_bangla_syllables(cleaned)
    was = count_bangla_syllables(current_text)
    if got >= was:
        print(f"    reprompt came back no shorter ({was} -> {got} syllables); keeping original")
        return None
    if got <= max_syllables:
        print(f"    reprompt OK: {was} -> {got} syllables (budget {max_syllables})")
    else:
        print(f"    reprompt shorter but over budget: {was} -> {got} syllables "
              f"(budget {max_syllables}) -- keeping it anyway, shorter is strictly better")
    return cleaned


def retranslate_shorter(segment: dict, max_syllables: int) -> str:
    """
    Reprompt the LLM for a shorter Bangla line when the synthesized clip overflows
    its caption slot. Shortening the TEXT is the only good way to fit a slot --
    speeding up the SPEECH is what destroyed intelligibility before 2026-07-14.

    Args:
        segment: The segment dict with 'text' (current Bangla), 'start', 'duration',
                 and optionally 'original_text' (English source).
        max_syllables: Target syllable count, in count_bangla_syllables() units.

    Returns:
        A shorter Bangla translation, or the original text if nothing better came back.
    """
    current_text = segment.get("text", "")
    original_english = segment.get("original_text", "")
    
    # Build the prompt with the syllable budget
    if original_english:
        prompt = (
            f"The following English text needs to be translated into Bangla in at most {max_syllables} syllables.\n"
            f"English: {original_english}\n"
            f"Current Bangla (TOO LONG): {current_text}\n"
            f"Produce a SHORTER Bangla translation (max {max_syllables} syllables). Output ONLY the Bangla text."
        )
    else:
        prompt = (
            f"The following Bangla text is too long for the available time window.\n"
            f"Current Bangla: {current_text}\n"
            f"Rewrite it in at most {max_syllables} syllables while keeping the core meaning.\n"
            f"Output ONLY the shortened Bangla text."
        )
    
    api_key = os.getenv("LLM_API_KEY")
    
    if api_key:
        # Use Ollama Cloud
        endpoint = _get_ollama_endpoint()
        model_name = os.getenv("LLM_MODEL_NAME") or "qwen3.5:397b-cloud"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        
        payload = {
            "model": model_name,
            "prompt": prompt,
            "system": SHORTER_SYSTEM_PROMPT,
            "stream": False
        }
        
        try:
            response = httpx.post(f"{endpoint}/generate", json=payload, headers=headers, timeout=60.0)
            if response.status_code == 200:
                raw = response.json().get("response", "").strip()
                cleaned = _clean_text(raw)
                # Strip any numbering artifacts
                cleaned = re.sub(r"^\d+[\.)\]]\s*", "", cleaned).strip()
                cleaned = re.sub(r"^\[.*?\]\s*", "", cleaned).strip()
                accepted = _accept_shorter(cleaned, current_text, max_syllables)
                if accepted:
                    return accepted
        except Exception as e:
            print(f"    reprompt via Ollama failed: {e}")
    else:
        # Use Anthropic Claude
        try:
            client = _get_client()
            response = client.messages.create(
                model=MODEL,
                max_tokens=500,
                system=SHORTER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = next((b.text for b in response.content if b.type == "text"), "").strip()
            cleaned = _clean_text(raw)
            cleaned = re.sub(r"^\d+[\.)\]]\s*", "", cleaned).strip()
            cleaned = re.sub(r"^\[.*?\]\s*", "", cleaned).strip()
            accepted = _accept_shorter(cleaned, current_text, max_syllables)
            if accepted:
                return accepted
        except Exception as e:
            print(f"    reprompt via Anthropic failed: {e}")
    
    # Fallback: return original text unchanged
    return current_text


def _translate_batch_ollama(segments: list[dict]) -> tuple[list[dict], int, int]:
    """Ollama batch translation."""
    endpoint = _get_ollama_endpoint()
    api_key = os.getenv("LLM_API_KEY")
    model_name = os.getenv("LLM_MODEL_NAME") or "qwen3.5:397b-cloud"
    
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        
    numbered = "\n".join(
        f"[{seg['duration']:.1f}s] {i + 1}. {seg['text']}"
        for i, seg in enumerate(segments)
    )
    
    payload = {
        "model": model_name,
        "prompt": numbered,
        "system": SYSTEM_PROMPT,
        "stream": False
    }
    
    def _try_generate(url, gen_payload, gen_headers, timeout_seconds):
        """Post to an Ollama endpoint and return parsed response or raise."""
        try:
            response = httpx.post(f"{url}/generate", json=gen_payload, headers=gen_headers, timeout=timeout_seconds)
            if response.status_code == 401:
                raise PermissionError("Ollama HTTP 401 Unauthorized")
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
            return response
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise RuntimeError(f"{type(e).__name__}: {e}") from e

    response = None
    last_error = None
    providers = [(endpoint, model_name, headers, "cloud")]
    # Always include local fallback so a bad cloud key/token does not block the run.
    providers.append((LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL, {}, "local"))

    for url, prov_model, prov_headers, label in providers:
        prov_payload = dict(payload)
        prov_payload["model"] = prov_model
        max_retries = 3 if label == "cloud" else 2
        timeout_seconds = 300.0
        for attempt in range(1, max_retries + 1):
            try:
                print(f"  Ollama {label} batch {len(segments)} segments, attempt {attempt}/{max_retries} (timeout {timeout_seconds:.0f}s)...")
                response = _try_generate(url, prov_payload, prov_headers, timeout_seconds)
                break
            except PermissionError:
                print("Ollama HTTP 401; falling back to Anthropic Claude...")
                return _translate_batch(_get_client(), segments)
            except Exception as e:
                last_error = e
                print(f"  Ollama {label} batch attempt {attempt} failed: {type(e).__name__}: {e}")
                if attempt < max_retries:
                    backoff = 2 ** attempt
                    print(f"  Retrying in {backoff}s...")
                    time.sleep(backoff)
        if response is not None:
            break

    if response is None:
        raise RuntimeError(f"Ollama batch translation failed after all providers: {last_error}")

    res_data = response.json()
    raw = res_data.get("response", "").strip()
    
    raw = _clean_text(raw)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    
    parsed_dict = {}
    for line in lines:
        match = re.match(r"^(?:\[?(\d+)\]?[\.\)]?\s*)(.*)", line)
        if match:
            idx = int(match.group(1)) - 1
            content, speaker, is_quote = _parse_segment_line(match.group(2))
            parsed_dict[idx] = {
                "text": content,
                "speaker": speaker,
                "is_arabic_quote": is_quote
            }
            
    # Handle missing segment fallbacks
    parsed_results = []
    for idx, seg in enumerate(segments):
        if idx in parsed_dict:
            parsed_results.append(parsed_dict[idx])
        else:
            print(f"Ollama segment index {idx + 1} missing. Running single fallback...")
            fallback_res = _translate_single_segment_ollama(seg)
            parsed_results.append(fallback_res)
            
    prompt_eval_count = res_data.get("prompt_eval_count", 0)
    eval_count = res_data.get("eval_count", 0)
    
    return parsed_results, prompt_eval_count, eval_count


# --- Anthropic Claude Engine ---

def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.Anthropic(api_key=api_key)


def _translate_single_segment(client: anthropic.Anthropic, segment: dict) -> dict:
    """Fallback translation for a single segment."""
    prompt = f"Translate the following single English lecture segment into extremely concise Bangla. Do not include any explanations. Maintain the same formatting rules.\nSegment: [{segment['duration']:.1f}s] {segment['text']}"
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next((b.text for b in response.content if b.type == "text"), "").strip()
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", raw).strip()
        cleaned = re.sub(r"^\[\d+\]\s*", "", cleaned).strip()

        text, speaker, is_quote = _parse_segment_line(cleaned)
        return {
            "text": text,
            "speaker": speaker,
            "is_arabic_quote": is_quote
        }
    except Exception as e:
        print(f"Fallback translation failed for segment '{segment['text']}': {e}")
        return {
            "text": segment["text"],
            "speaker": "SPEAKER_A",
            "is_arabic_quote": False
        }


def _translate_batch(client: anthropic.Anthropic, segments: list[dict]) -> tuple[list[dict], int, int]:
    numbered = "\n".join(
        f"[{seg['duration']:.1f}s] {i + 1}. {seg['text']}"
        for i, seg in enumerate(segments)
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": numbered}],
    )

    raw = next((b.text for b in response.content if b.type == "text"), "").strip()
    raw = _clean_text(raw)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]

    parsed_dict = {}
    for line in lines:
        match = re.match(r"^(?:\[?(\d+)\]?[\.\)]?\s*)(.*)", line)
        if match:
            idx = int(match.group(1)) - 1
            content, speaker, is_quote = _parse_segment_line(match.group(2))

            parsed_dict[idx] = {
                "text": content,
                "speaker": speaker,
                "is_arabic_quote": is_quote
            }

    in_tokens = getattr(response.usage, "input_tokens", 0)
    out_tokens = getattr(response.usage, "output_tokens", 0)

    # Fallback to sequential parsing if index-based parsing fails
    if len(parsed_dict) < len(segments) / 2:
        print(f"Warning: Index-based parsing only identified {len(parsed_dict)}/{len(segments)} segments. Falling back to sequential line parsing.")
        parsed_results = []
        for line in lines:
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
            cleaned = re.sub(r"^\[\d+\]\s*", "", cleaned).strip()

            content, speaker, is_quote = _parse_segment_line(cleaned)
            parsed_results.append({
                "text": content,
                "speaker": speaker,
                "is_arabic_quote": is_quote
            })

        while len(parsed_results) < len(segments):
            # Run individual fallback translation for missing segments
            missing_idx = len(parsed_results)
            print(f"Sequential parsing is short by {len(segments) - missing_idx} segments. Running fallback translation for index {missing_idx}...")
            fallback_res = _translate_single_segment(client, segments[missing_idx])
            parsed_results.append(fallback_res)
            in_tokens += 100
            out_tokens += 50

        return parsed_results[: len(segments)], in_tokens, out_tokens

    # Build final results with single-segment fallbacks
    parsed_results = []
    for idx, seg in enumerate(segments):
        if idx in parsed_dict:
            parsed_results.append(parsed_dict[idx])
        else:
            print(f"Segment index {idx + 1} was missing in batch response. Running fallback translation...")
            fallback_res = _translate_single_segment(client, seg)
            parsed_results.append(fallback_res)
            in_tokens += 100
            out_tokens += 50

    return parsed_results, in_tokens, out_tokens


def _translate_batch_ollama_adaptive(batch: list[dict]) -> tuple[list[dict], int, int]:
    """Batch translation that halves the batch on provider failure instead of
    re-retrying the same prompt. A batch of 20 repeatedly hung/500'd on
    gemma4:31b-cloud (Jul 19), wedging the whole run for 8 supervisor relaunches;
    smaller prompts get through. Bottoms out at single segments, which never raise."""
    try:
        return _translate_batch_ollama(batch)
    except RuntimeError as e:
        if len(batch) == 1:
            print(f"  Batch of 1 still failing ({e}); using single-segment fallback.")
            return [_translate_single_segment_ollama(batch[0])], 0, 0
        mid = len(batch) // 2
        print(f"  Batch of {len(batch)} failed ({e}); splitting into {mid}+{len(batch) - mid}...")
        l_res, l_in, l_out = _translate_batch_ollama_adaptive(batch[:mid])
        r_res, r_in, r_out = _translate_batch_ollama_adaptive(batch[mid:])
        return l_res + r_res, l_in + r_in, l_out + r_out


# --- Main Entry Point ---

def translate_segments(segments: list[dict], batch_size: int = 20) -> tuple[list[dict], int, int]:
    """Translates all segments from English to Bangla in batches, dynamically picking Ollama or Anthropic.
    
    Islamic honorifics and sacred terms are pre-processed (replaced with placeholders)
    before translation and post-processed (restored to Bengali/Arabic forms) after.
    """
    api_key = os.getenv("LLM_API_KEY")
    translated = []
    total_in = 0
    total_out = 0

    if api_key:
        print("Using Ollama Cloud translation provider...")
        for i in range(0, len(segments), batch_size):
            batch = segments[i : i + batch_size]
            # Pre-process: protect honorifics and sacred terms
            processed_batch = []
            placeholder_map = []
            for seg in batch:
                proc_text, inserted = pre_process_honorifics(seg["text"])
                processed_batch.append({**seg, "text": proc_text})
                placeholder_map.append(inserted)
            
            results, in_t, out_t = _translate_batch_ollama_adaptive(processed_batch)
            if len(results) != len(batch):
                raise ValueError("Translation segment count mismatch")
            total_in += in_t
            total_out += out_t
            for seg, res, expected_ph in zip(batch, results, placeholder_map):
                # Post-process: restore honorifics and sacred terms
                restored_text = post_process_honorifics(res["text"], expected_ph if expected_ph else None)
                translated.append({
                    "text": restored_text,
                    "start": seg["start"],
                    "duration": seg["duration"],
                    "speaker": res["speaker"],
                    "is_arabic_quote": res["is_arabic_quote"]
                })
    else:
        print("Using Anthropic Claude translation provider...")
        client = _get_client()
        for i in range(0, len(segments), batch_size):
            batch = segments[i : i + batch_size]
            # Pre-process: protect honorifics and sacred terms
            processed_batch = []
            placeholder_map = []
            for seg in batch:
                proc_text, inserted = pre_process_honorifics(seg["text"])
                processed_batch.append({**seg, "text": proc_text})
                placeholder_map.append(inserted)
            
            results, in_t, out_t = _translate_batch(client, processed_batch)
            if len(results) != len(batch):
                raise ValueError("Translation segment count mismatch")
            total_in += in_t
            total_out += out_t
            for seg, res, expected_ph in zip(batch, results, placeholder_map):
                # Post-process: restore honorifics and sacred terms
                restored_text = post_process_honorifics(res["text"], expected_ph if expected_ph else None)
                translated.append({
                    "text": restored_text,
                    "start": seg["start"],
                    "duration": seg["duration"],
                    "speaker": res["speaker"],
                    "is_arabic_quote": res["is_arabic_quote"]
                })

    return translated, total_in, total_out


# A shortening pass must not undo the sacred-term guard after initial translation.
_retranslate_shorter_unprotected = retranslate_shorter
def retranslate_shorter(segment: dict, max_syllables: int) -> str:
    current = segment.get("text", "")
    candidate = _retranslate_shorter_unprotected(segment, max_syllables)
    for sacred in set(value for _, value in _TERM_RULES):
        if candidate.count(sacred) < current.count(sacred):
            print("Shortening omitted a sacred term; keeping the protected translation")
            return current
    return candidate
