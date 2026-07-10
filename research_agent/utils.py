from __future__ import annotations

from pathlib import Path
import csv
import re
from typing import Iterable

import pandas as pd

from .config import RESULT_COLUMNS


WHITESPACE_RE = re.compile(r"\s+")


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return WHITESPACE_RE.sub(" ", value).strip()


def normalize_name(value: str | None) -> str:
    return clean_text(value).lower()


def read_csv_records(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    frame = pd.read_csv(path).fillna("")
    return frame.to_dict(orient="records")


def write_csv_records(path: Path, records: Iterable[dict[str, str]]) -> None:
    ensure_parent_dir(path)
    rows = list(records)
    frame = pd.DataFrame(rows, columns=RESULT_COLUMNS if rows else RESULT_COLUMNS)
    frame.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)


def append_csv_record(path: Path, record: dict[str, str]) -> None:
    rows = read_csv_records(path)
    rows.append(record)
    write_csv_records(path, rows)


def coalesce(*values: str | None) -> str:
    for value in values:
        cleaned = clean_text(value)
        if cleaned:
            return cleaned
    return ""


def safe_join(items: Iterable[str], separator: str = "; ") -> str:
    cleaned_items = [clean_text(item) for item in items if clean_text(item)]
    return separator.join(cleaned_items)


def clean_description(text: str | None, app_name: str | None = None) -> str:
    """Clean a raw extracted description sentence.

    Goals:
    - Remove navigation UI fragments ("Skip to content", "Homepage", "Log in", etc.).
    - Strip site chrome like "Product | Company" by choosing the most descriptive segment.
    - Return a single, factual sentence (or a short fallback) describing the product.

    This is heuristic-based: prefer the longest sensible segment, remove boilerplate
    tokens, and ensure we return a readable one-line sentence.
    """
    raw = clean_text(text)
    if not raw:
        if app_name:
            return f"{app_name} is a SaaS product."
        return "SaaS product."

    # Remove common UI/navigation noise
    noise_patterns = [
        r"skip to content",
        r"home\s*page",
        r"log in",
        r"sign in",
        r"pricing",
        r"resources",
        r"menu",
        r"search",
        r"©.*",
        r"all rights reserved",
        r"terms of service",
        r"privacy policy",
        r"cookie",
        r"sitemap",
    ]
    lowered = raw.lower()
    for pat in noise_patterns:
        lowered = re.sub(pat, "", lowered, flags=re.IGNORECASE)

    # Restore basic whitespace
    cleaned = WHITESPACE_RE.sub(" ", lowered).strip()

    # Split on common title separators and pick the most descriptive part
    parts = re.split(r"\||\u2013|\u2014|-|:|—", cleaned)
    parts = [p.strip() for p in parts if p and len(p.strip()) > 3]
    if parts:
        # choose the longest candidate segment that isn't just the app name
        def score(p: str) -> int:
            s = len(p)
            if app_name and app_name.lower() in p.lower():
                s -= 5
            return s

        best = max(parts, key=score)
    else:
        best = cleaned

    best = best.strip()

    # Ensure it's a single sentence: find first terminal punctuation if any
    m = re.search(r"([^.?!]*[.?!])", best)
    if m:
        sent = m.group(1).strip()
    else:
        # fallback: take up to 240 chars and add period
        sent = best[:240].strip()
        if not sent.endswith("."):
            sent = sent + "."

    # If the sentence is too short / not descriptive, fallback to safer phrase
    if len(sent) < 30:
        if app_name:
            return f"{app_name} is a SaaS product."
        return "SaaS product."

    # Capitalize first letter
    sent = sent[0].upper() + sent[1:]
    return sent
