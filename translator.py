import os
import re
import anthropic

SYSTEM_PROMPT = """You are a translator specializing in Islamic religious content.
Translate the following English lecture captions into Bangla (বাংলা).

CRITICAL TIME CONSTRAINT:
The translated text will be converted to speech (TTS) and played in real-time.
To prevent audio clips from overlapping or lagging behind the video, the translations MUST be extremely short and highly concise. It is better to summarize or slightly condense a sentence than to have it overlap.

Rules:
- Preserve Islamic terms as-is or in established Bangla transliteration:
  Allah, Quran, Hadith, Sunnah, Salah, Zakat, Hajj, Ummah, Sheikh, Imam,
  InshaAllah (ইনশাআল্লাহ), Alhamdulillah (আলহামদুলিল্লাহ), SubhanAllah (সুবহানআল্লাহ),
  Bismillah (বিসমিল্লাহ), MashaAllah (মাশাআল্লাহ), JazakAllah (জাযাকাল্লাহ), etc.
- Keep Arabic phrases (du'as, Quranic verses) in Arabic script or their common transliteration.
- Each segment is prefixed with its spoken duration, e.g. "[2.3s] 1. Hello everyone"
- Translate with absolute brevity. Use extremely concise phrasing. Cut out all filler words.
- Aim for at most 8 characters (with spaces) per 1 second of duration (e.g., if duration is 2.5s, the translation must be at most 20 characters).
- For very short segments (under 1.5s), use 1–3 words maximum.
- Prefer a summarized, condensed translation over a complete literal one when time is tight.
- Never shorten or alter core Islamic terms (Allah, Quran, etc.).
- Output ONLY the numbered translations, one per line, matching the input numbering exactly.
- Do not add explanations, notes, or extra text."""

MODEL = "claude-haiku-4-5"


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.Anthropic(api_key=api_key)


def _translate_batch(client: anthropic.Anthropic, segments: list[dict]) -> tuple[list[str], int, int]:
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

    translations = []
    for line in lines:
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
        translations.append(cleaned)

    # Guard against mismatched counts — pad with originals if needed
    while len(translations) < len(segments):
        translations.append(segments[len(translations)]["text"])

    in_tokens = getattr(response.usage, "input_tokens", 0)
    out_tokens = getattr(response.usage, "output_tokens", 0)

    return translations[: len(segments)], in_tokens, out_tokens


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
        bangla_texts, in_t, out_t = _translate_batch(client, batch)
        total_in += in_t
        total_out += out_t
        for seg, bangla in zip(batch, bangla_texts):
            translated.append({"text": bangla, "start": seg["start"], "duration": seg["duration"]})

    return translated, total_in, total_out

