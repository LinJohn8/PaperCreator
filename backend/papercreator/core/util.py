"""Small shared helpers: ids, time, text normalisation, similarity, tokens.

Kept dependency-free (stdlib only) so every layer including the store can
import it without cycles.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, TypeVar

T = TypeVar("T")

# ---------------------------------------------------------------- ids & time


def new_id(prefix: str = "") -> str:
    """Short random id. Prefixed ids make debug output readable (``prj_a1b2``)."""
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}_{raw}" if prefix else raw


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """ISO-8601 with seconds precision, UTC, no microseconds.

    Stored as TEXT everywhere; sorts lexicographically, which the queries rely
    on for ``ORDER BY created_at``.
    """
    return utc_now().replace(microsecond=0).isoformat()


def stable_hash(*parts: Any, length: int = 16) -> str:
    """Deterministic short hash - cache keys, content addressing, dedupe keys."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", "replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:length]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------- slugs

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


def slugify(text: str, *, max_length: int = 60, fallback: str = "item") -> str:
    """Filesystem- and URL-safe slug.

    CJK is transliteration-free: characters are dropped, so a purely Chinese
    title yields ``fallback`` plus a hash suffix rather than an empty or
    mojibake directory name. Project slugs become directory names on Windows,
    so the output is restricted to ``[a-z0-9-]``.
    """
    if not text:
        return fallback
    had_cjk = bool(_CJK.search(text))
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_text).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:max_length].strip("-")
    if not slug:
        slug = f"{fallback}-{stable_hash(text, length=6)}"
    elif had_cjk:
        # Two different Chinese titles can reduce to the same ASCII residue.
        slug = f"{slug}-{stable_hash(text, length=4)}"
    # Windows reserved device names cannot be directory names.
    if slug.split("-")[0].upper() in {
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "LPT1", "LPT2",
    }:
        slug = f"x-{slug}"
    return slug


def safe_filename(name: str, *, default: str = "file") -> str:
    """Strip path separators and characters Windows forbids in filenames."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or default


# ---------------------------------------------------------------------- text

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def collapse_ws(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def normalize_title(title: str) -> str:
    """Aggressive normalisation for dedupe comparisons only.

    Lowercase, strip accents, drop punctuation and whitespace. Never persist
    the output - it is lossy and not human-readable.
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", title).lower()
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCT.sub(" ", text)
    return _WS.sub("", text)


def normalize_doi(doi: str | None) -> str:
    """Bare lowercase DOI: strips URL prefixes, ``doi:``, trailing punctuation.

    Providers return all of ``10.1/x``, ``https://doi.org/10.1/x``,
    ``DOI: 10.1/X``; dedupe requires one canonical form.
    """
    if not doi:
        return ""
    text = str(doi).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi.org/", "doi:", "doi "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip().rstrip(".,;)")


def normalize_arxiv_id(value: str | None) -> str:
    """Bare arXiv id without version suffix (``2301.01234``).

    Version is dropped so v1 and v3 of the same preprint deduplicate.
    """
    if not value:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"^(https?://)?(www\.)?arxiv\.org/(abs|pdf)/", "", text)
    text = re.sub(r"^arxiv[:/ ]+", "", text)
    text = re.sub(r"\.pdf$", "", text)
    return re.sub(r"v\d+$", "", text).strip()


def title_similarity(a: str, b: str) -> float:
    """0..1 similarity of two normalised titles.

    ``SequenceMatcher`` (character n-gram based) rather than token overlap:
    it handles the common "same title, one has a subtitle" case gracefully and
    needs no tokenizer for CJK.
    """
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Cheap length prefilter - SequenceMatcher is O(n*m).
    shorter, longer = sorted((len(na), len(nb)))
    if longer > 0 and shorter / longer < 0.6:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def word_count(text: str) -> int:
    """Words for English, characters for CJK, summed.

    A bilingual manuscript needs one number that behaves sanely for both; CJK
    has no spaces so counting whitespace tokens would report ~1 per paragraph.
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    latin = len(re.findall(r"\b[\w'-]+\b", _CJK.sub(" ", text)))
    return cjk + latin


def estimate_tokens(text: str) -> int:
    """Rough token count without a tokenizer dependency.

    ~4 chars/token for Latin, ~1.6 chars/token for CJK. Used for budget
    guards and prompt trimming, where being 20% off is acceptable; actual
    accounting always uses the provider's reported usage.
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = max(0, len(text) - cjk)
    return int(cjk / 1.6 + other / 4) + 1


def detect_language(text: str) -> str:
    """``zh`` if a meaningful share of characters is CJK, else ``en``.

    Deliberately binary: the app only branches between Chinese and English.
    """
    if not text:
        return "en"
    sample = text[:2000]
    cjk = len(_CJK.findall(sample))
    letters = len(re.findall(r"[A-Za-z]", sample))
    if cjk == 0:
        return "en"
    return "zh" if cjk * 3 >= letters else "en"


# --------------------------------------------------------------- collections


def chunked(items: Sequence[T], size: int) -> list[Sequence[T]]:
    if size <= 0:
        return [items]
    return [items[i: i + size] for i in range(0, len(items), size)]


def dedupe_preserving_order(items: Iterable[T]) -> list[T]:
    seen: set[T] = set()
    out: list[T] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None
