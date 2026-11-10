"""Lightweight persistence for Lip Sync human evaluation feedback.

Stores feedback as JSONL lines keyed by video ID, segment ID, timestamp,
rating, labels, and free-text comments. Provides helpers to append and read.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FEEDBACK_DIR = Path("feedback")
FEEDBACK_DIR.mkdir(exist_ok=True)
FEEDBACK_FILE = FEEDBACK_DIR / "evaluations.jsonl"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    """Append a feedback record to JSONL after injecting a UTC timestamp and id."""
    record = {
        **payload,
        "submitted_at": _utc_now_iso(),
    }
    if "id" not in record:
        record["id"] = _utc_now_iso().replace(":", "-").replace("+", "_")
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def iter_feedback():
    """Yield every persisted feedback record."""
    if not FEEDBACK_FILE.exists():
        return
    with FEEDBACK_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def get_feedback_file_path() -> Path:
    return FEEDBACK_FILE
