import os
import re
import httpx
import anthropic

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

MODEL = "claude-haiku-4-5"


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
        
    return cleaned, speaker, is_arabic_quote


# --- Ollama Cloud Engine ---

def _get_ollama_endpoint() -> str:
    return os.getenv("LLM_BASE_URL") or "https://ollama.com/api"


def _translate_single_segment_ollama(segment: dict) -> dict:
    """Ollama fallback translation for a single segment."""
    endpoint = _get_ollama_endpoint()
    api_key = os.getenv("LLM_API_KEY")
    model_name = os.getenv("LLM_MODEL_NAME") or "gemma3:27b"
    
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
    except Exception as e:
        print(f"Ollama fallback translation failed: {e}")
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
        model_name = os.getenv("LLM_MODEL_NAME") or "gemma3:27b"
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
    model_name = os.getenv("LLM_MODEL_NAME") or "gemma3:27b"
    
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
    
    response = httpx.post(f"{endpoint}/generate", json=payload, headers=headers, timeout=90.0)
    if response.status_code != 200:
        raise RuntimeError(f"Ollama batch translation failed (HTTP {response.status_code}): {response.text}")
        
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


# --- Main Entry Point ---

def translate_segments(segments: list[dict], batch_size: int = 20) -> tuple[list[dict], int, int]:
    """Translates all segments from English to Bangla in batches, dynamically picking Ollama or Anthropic."""
    api_key = os.getenv("LLM_API_KEY")
    translated = []
    total_in = 0
    total_out = 0

    if api_key:
        print("Using Ollama Cloud translation provider...")
        for i in range(0, len(segments), batch_size):
            batch = segments[i : i + batch_size]
            results, in_t, out_t = _translate_batch_ollama(batch)
            total_in += in_t
            total_out += out_t
            for seg, res in zip(batch, results):
                translated.append({
                    "text": res["text"],
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
            results, in_t, out_t = _translate_batch(client, batch)
            total_in += in_t
            total_out += out_t
            for seg, res in zip(batch, results):
                translated.append({
                    "text": res["text"],
                    "start": seg["start"],
                    "duration": seg["duration"],
                    "speaker": res["speaker"],
                    "is_arabic_quote": res["is_arabic_quote"]
                })

    return translated, total_in, total_out
