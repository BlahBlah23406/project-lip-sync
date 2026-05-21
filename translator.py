import os
import re
import anthropic

SYSTEM_PROMPT = """You are a translator specializing in Islamic religious content.
Translate the following English lecture captions into Bangla (বাংলা).

CRITICAL TIME CONSTRAINT & FORMAT RULES:
The translated text will be converted to speech (TTS) and played in real-time.
To prevent audio clips from overlapping or lagging, translations MUST be extremely short and highly concise.

Additional Annotation Requirements (Speaker Diarization & Quote Detection):
1. Speaker Tracking:
   - Identify different speakers based on text flow, style, dialogue shifts, Q&A structures, or tags (e.g., host vs speaker, or Zakir vs Audience).
   - Label each translation line with its speaker ID using prefix tags: `[SPEAKER_A]`, `[SPEAKER_B]`, `[SPEAKER_C]`, or `[SPEAKER_D]`.
   - `[SPEAKER_A]` is the default/main speaker. Use subsequent IDs consistently for other speakers.
2. Classical Arabic/Quranic Quotes:
   - Detect segments where the lecturer recites Arabic (e.g. Quranic verses, Hadith recitations, du'as).
   - If a segment is an Arabic recitation/quote, translate its meaning as usual to Bangla, but **prepend** the tag `[QUOTE_ARABIC]` after the speaker tag.
   - Example output line for a quote: `1. [SPEAKER_A] [QUOTE_ARABIC] আলহামদুলিল্লাহি রাব্বিল আলামিন।`

General Rules:
- Preserve Islamic terms as-is or in established Bangla transliteration:
  Allah, Quran, Hadith, Sunnah, Salah, Zakat, Hajj, Ummah, Sheikh, Imam,
  InshaAllah (ইনশাআল্লাহ), Alhamdulillah (আলহামদুলিল্লাহ), SubhanAllah (সুবহানআল্লাহ),
  Bismillah (বিসমিল্লাহ), MashaAllah (মাশাআল্লাহ), JazakAllah (জাযাকাল্লাহ), etc.
- Keep Arabic phrases (du'as, Quranic verses) in Arabic script or their common transliteration if helpful, alongside translation.
- Each input segment is prefixed with duration: e.g. "[2.3s] 1. Hello everyone"
- Aim for at most 8 characters (with spaces) per 1 second of duration.
- For short segments (under 1.5s), use 1–3 words maximum.
- Prefer a summarized, condensed translation over a complete literal one.
- Output ONLY the numbered translations, one per line, matching the input numbering exactly.
- Do not add explanations, notes, or extra text."""

MODEL = "claude-haiku-4-5"


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.Anthropic(api_key=api_key)


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

    parsed_results = []
    for line in lines:
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
        
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
            
        parsed_results.append({
            "text": cleaned,
            "speaker": speaker,
            "is_arabic_quote": is_arabic_quote
        })

    # Guard against mismatched counts — pad with defaults if needed
    while len(parsed_results) < len(segments):
        orig_text = segments[len(parsed_results)]["text"]
        parsed_results.append({
            "text": orig_text,
            "speaker": "SPEAKER_A",
            "is_arabic_quote": False
        })

    in_tokens = getattr(response.usage, "input_tokens", 0)
    out_tokens = getattr(response.usage, "output_tokens", 0)

    return parsed_results[: len(segments)], in_tokens, out_tokens


def translate_segments(segments: list[dict], batch_size: int = 20) -> tuple[list[dict], int, int]:
    """
    Translates all segments English → Bangla in batches.
    Returns a tuple: (translated_segments, input_tokens, output_tokens)
    """
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

