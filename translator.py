import os
import re
import anthropic

SYSTEM_PROMPT = """You are a translator specializing in Islamic religious content.
Translate the following English lecture captions into Bangla (বাংলা).

CRITICAL TIME CONSTRAINT & FORMAT RULES:
The translated text will be converted to speech (TTS) and played in real-time.
To prevent audio clips from overlapping or lagging, translations MUST be extremely short, highly concise, and fast-paced.

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
- Each input segment is prefixed with duration: e.g. "[2.3s] 1. Hello everyone"
- Aim for at most 6 characters (with spaces) per 1 second of duration.
- For short segments (under 1.5s), use 1–2 words maximum.
- Prefer a summarized, condensed translation over a complete literal one.
- Output ONLY the numbered translations, one per line, matching the input numbering exactly.
- Do not add explanations, notes, or extra text."""

MODEL = "claude-haiku-4-5"


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

        return {
            "text": cleaned,
            "speaker": speaker,
            "is_arabic_quote": is_arabic_quote
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
    lines = [line.strip() for line in raw.splitlines() if line.strip()]

    parsed_dict = {}
    for line in lines:
        # Match lines starting with a number, e.g. "1. ...", "1) ...", "[1] ..."
        match = re.match(r"^(?:\[?(\d+)\]?[\.\)]?\s*)(.*)", line)
        if match:
            idx = int(match.group(1)) - 1
            content = match.group(2).strip()

            # Parse [SPEAKER_X] (case-insensitive check)
            speaker = "SPEAKER_A"
            speaker_match = re.search(r"\[(SPEAKER_[A-D])\]", content, re.IGNORECASE)
            if speaker_match:
                speaker = speaker_match.group(1).upper()
                content = re.sub(r"\[SPEAKER_[A-D]\]", "", content, flags=re.IGNORECASE).strip()

            # Parse [QUOTE_ARABIC] (case-insensitive check)
            is_arabic_quote = False
            if re.search(r"\[QUOTE_ARABIC\]", content, re.IGNORECASE):
                is_arabic_quote = True
                content = re.sub(r"\[QUOTE_ARABIC\]", "", content, flags=re.IGNORECASE).strip()

            parsed_dict[idx] = {
                "text": content,
                "speaker": speaker,
                "is_arabic_quote": is_arabic_quote
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

            # Parse [SPEAKER_X]
            speaker = "SPEAKER_A"
            speaker_match = re.search(r"\[(SPEAKER_[A-D])\]", cleaned, re.IGNORECASE)
            if speaker_match:
                speaker = speaker_match.group(1).upper()
                cleaned = re.sub(r"\[SPEAKER_[A-D]\]", "", cleaned, flags=re.IGNORECASE).strip()

            # Parse [QUOTE_ARABIC]
            is_arabic_quote = False
            if re.search(r"\[QUOTE_ARABIC\]", cleaned, re.IGNORECASE):
                is_arabic_quote = True
                cleaned = re.sub(r"\[QUOTE_ARABIC\]", "", cleaned, flags=re.IGNORECASE).strip()

            parsed_results.append({
                "text": cleaned,
                "speaker": speaker,
                "is_arabic_quote": is_arabic_quote
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


def translate_segments(segments: list[dict], batch_size: int = 20) -> tuple[list[dict], int, int]:
    """Translates all segments from English to Bangla in batches."""
    client = _get_client()
    translated = []
    total_in = 0
    total_out = 0

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
