#!/usr/bin/env python3
"""LipSync wrapper for the canonical sentence_cleanup module.

The canonical cleanup implementation lives in the crowdsource-translation
repo at `crowdsource-translation/scripts/sentence_cleanup.py`. This module
imports it and re-exports the exact API the LipSync pipeline expects:

    check_available, clean_fragment, clean_fragments, CleanupUnavailable

Usage:
    python sentence_cleanup.py --text "because the prophet"
    python sentence_cleanup.py --file fragments.txt --output cleaned.txt
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add the crowdsource-translation scripts directory to the import path.
_CANONICAL_DIR = (
    Path(__file__).resolve().parent.parent / "crowdsource-translation" / "scripts"
)
if str(_CANONICAL_DIR) not in sys.path:
    sys.path.insert(0, str(_CANONICAL_DIR))

from sentence_cleanup import (  # type: ignore
    DEFAULT_MODEL,
    CleanupUnavailable,
    check_available,
    clean_fragment,
    clean_fragments,
    clean_sentences,
)


def _read_lines(path: str | None) -> list[str]:
    if path is None or path == "-":
        stream = sys.stdin
    else:
        stream = open(path, "r", encoding="utf-8")
    try:
        return [line.strip() for line in stream if line.strip()]
    finally:
        if stream is not sys.stdin:
            stream.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean English sentence fragments via Ollama.")
    parser.add_argument("--file", "-f", help="Input file with one fragment per line.")
    parser.add_argument("--output", "-o", help="Output file (default: stdout).")
    parser.add_argument("--text", "-t", action="append", help="Fragment to clean ( repeatable).")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, help="Ollama model to use.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent.")
    args = parser.parse_args(argv)

    fragments: list[str] = []
    if args.text:
        fragments.extend(args.text)
    if args.file:
        fragments.extend(_read_lines(args.file))
    if not fragments and not sys.stdin.isatty():
        fragments.extend(_read_lines(None))

    if not fragments:
        print("No input fragments.", file=sys.stderr)
        return 1

    cleaned = clean_sentences(
        fragments,
        dry_run=args.dry_run,
        model=args.model,
    )

    out = sys.stdout if args.output is None else open(args.output, "w", encoding="utf-8")
    try:
        for c in cleaned:
            out.write(c + "\n")
    finally:
        if out is not sys.stdout:
            out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
