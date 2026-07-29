"""Local file provider: BibTeX / RIS / CSV / JSON already on disk.

Not a network source, but it implements the same :class:`Provider` interface so
an exported Zotero/Mendeley/EndNote library participates in the *same* search,
dedupe, ranking and landscape pipeline as the online results. That is the point:
the user's existing reading list should appear in the 3D map next to newly
retrieved work, not in a separate silo.

Directories to index come from ``settings.retrieval`` extras or, by default,
``<home>/imports``. Files are parsed lazily and cached in memory per process
run, keyed by (path, mtime), because a 5000-entry .bib takes a moment to parse
and a multi-query search would otherwise re-read it.

Supported formats
-----------------
* ``.bib``  - BibTeX (a pragmatic parser: entries, braces, quoted values)
* ``.ris``  - RIS tagged format
* ``.csv``  - any CSV with a title column; common Zotero/Scopus headers mapped
* ``.json`` - a list of paper-like dicts, or CSL-JSON
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.paths import get_paths
from ...core.util import coerce_int, collapse_ws, normalize_arxiv_id, normalize_doi
from ..base import (
    Provider,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderMeta,
    RateLimit,
)

log = get_logger(__name__)

_SUPPORTED = {".bib", ".ris", ".csv", ".json"}

# (path, mtime) -> parsed papers
_cache: dict[tuple[str, float], list[Paper]] = {}


# --------------------------------------------------------------------- BibTeX

_BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,]*),", re.MULTILINE)
_BIB_TYPE_MAP = {
    "article": "journal", "inproceedings": "conference", "conference": "conference",
    "book": "book", "inbook": "book", "incollection": "book",
    "phdthesis": "thesis", "mastersthesis": "thesis", "techreport": "report",
    "misc": "preprint", "unpublished": "preprint",
}
_LATEX_ACCENTS = {
    r"\\'a": "á", r"\\`a": "à", r'\\"a': "ä", r"\\^a": "â", r"\\~n": "ñ",
    r"\\'e": "é", r"\\`e": "è", r'\\"e': "ë", r"\\^e": "ê", r"\\'i": "í",
    r"\\'o": "ó", r'\\"o': "ö", r"\\'u": "ú", r'\\"u': "ü", r"\\ss": "ß",
    r"\\'c": "ć", r"\\v c": "č", r"\\aa": "å", r"\\o": "ø",
}


def clean_latex(text: str) -> str:
    """Strip LaTeX braces/commands from a BibTeX field value.

    A title like ``{Graph} {Neural} Networks for \\emph{Science}`` must become
    plain text before it is embedded or displayed.
    """
    if not text:
        return ""
    out = text
    for pattern, replacement in _LATEX_ACCENTS.items():
        out = re.sub(pattern + r"\{?\}?", replacement, out)
    out = re.sub(r"\\[a-zA-Z]+\s*", " ", out)   # remaining commands
    out = out.replace("{", "").replace("}", "").replace("\\", "")
    out = out.replace("--", "-").replace("~", " ")
    return collapse_ws(out)


def _split_bib_authors(value: str) -> list[Author]:
    """Parse a BibTeX author field: ``Last, First and Last, First``."""
    authors: list[Author] = []
    for chunk in re.split(r"\s+and\s+", value):
        name = clean_latex(chunk)
        if not name:
            continue
        if "," in name:
            family, _, given = name.partition(",")
            name = f"{given.strip()} {family.strip()}".strip()
        authors.append(Author(name=name))
    return authors


def _find_matching_brace(text: str, start: int) -> int:
    """Index just past the ``}`` closing the ``{`` at ``start``."""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text)


def parse_bibtex(text: str) -> list[Paper]:
    """Parse a .bib file into papers.

    Deliberately not a full BibTeX implementation (no @string expansion, no
    crossref inheritance): those are rare in exported libraries, and a
    dependency-free parser keeps the install light. Malformed entries are
    skipped with a debug log rather than aborting the import.
    """
    papers: list[Paper] = []
    for match in _BIB_ENTRY.finditer(text):
        entry_type = match.group(1).lower()
        if entry_type in ("comment", "string", "preamble"):
            continue
        brace_start = text.rfind("{", match.start(), match.end())
        end = _find_matching_brace(text, brace_start)
        body = text[match.end():end]
        fields: dict[str, str] = {}
        # field = {value} | "value" | bare
        for field_match in re.finditer(
            r'(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|"[^"]*"|[^,\n]+)', body
        ):
            key = field_match.group(1).lower()
            raw = field_match.group(2).strip().rstrip(",").strip()
            if raw.startswith("{") and raw.endswith("}"):
                raw = raw[1:-1]
            elif raw.startswith('"') and raw.endswith('"'):
                raw = raw[1:-1]
            fields[key] = raw

        title = clean_latex(fields.get("title", ""))
        if not title:
            continue
        arxiv = fields.get("eprint", "") if "arxiv" in fields.get(
            "archiveprefix", ""
        ).lower() else ""
        venue = clean_latex(
            fields.get("journal") or fields.get("booktitle")
            or fields.get("publisher") or ""
        )
        papers.append(Paper(
            title=title,
            abstract=clean_latex(fields.get("abstract", "")),
            authors=_split_bib_authors(fields.get("author", "")),
            year=coerce_int(re.sub(r"\D", "", fields.get("year", "")), 0) or None,
            venue=venue,
            venue_type=_BIB_TYPE_MAP.get(entry_type, ""),
            doi=normalize_doi(fields.get("doi")),
            arxiv_id=normalize_arxiv_id(arxiv),
            url=fields.get("url", ""),
            keywords=[
                collapse_ws(k) for k in re.split(r"[;,]", fields.get("keywords", ""))
                if collapse_ws(k)
            ][:10],
            origin="manual",
            raw={"bibtex": {"key": match.group(2).strip(), "type": entry_type,
                            "volume": fields.get("volume", ""),
                            "pages": fields.get("pages", "")}},
        ))
    return papers


# ------------------------------------------------------------------------- RIS

_RIS_TYPE_MAP = {
    "JOUR": "journal", "CPAPER": "conference", "CONF": "conference",
    "BOOK": "book", "CHAP": "book", "THES": "thesis", "RPRT": "report",
    "UNPB": "preprint", "ELEC": "web",
}


def parse_ris(text: str) -> list[Paper]:
    """Parse RIS: ``TAG  - value`` lines, records ended by ``ER  -``."""
    papers: list[Paper] = []
    current: dict[str, list[str]] = {}
    last_tag = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        match = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", line)
        if match:
            tag, value = match.group(1), match.group(2).strip()
            last_tag = tag
            if tag == "ER":
                paper = _ris_record_to_paper(current)
                if paper is not None:
                    papers.append(paper)
                current = {}
                continue
            current.setdefault(tag, []).append(value)
        elif last_tag and current.get(last_tag):
            # Continuation line of a wrapped value (common for AB).
            current[last_tag][-1] += " " + line.strip()
    if current:
        paper = _ris_record_to_paper(current)
        if paper is not None:
            papers.append(paper)
    return papers


def _ris_record_to_paper(record: dict[str, list[str]]) -> Paper | None:
    def one(tag: str, default: str = "") -> str:
        values = record.get(tag) or []
        return collapse_ws(values[0]) if values else default

    title = one("TI") or one("T1")
    if not title:
        return None
    year_raw = one("PY") or one("Y1") or one("DA")
    year = coerce_int(re.sub(r"\D", "", year_raw)[:4], 0) or None
    return Paper(
        title=title,
        abstract=one("AB") or one("N2"),
        authors=[
            Author(name=collapse_ws(
                f"{n.split(',')[1]} {n.split(',')[0]}" if "," in n else n
            ))
            for n in [*(record.get("AU") or []), *(record.get("A1") or [])]
            if collapse_ws(n)
        ],
        year=year,
        venue=one("JO") or one("JF") or one("T2") or one("BT"),
        venue_type=_RIS_TYPE_MAP.get(one("TY"), ""),
        doi=normalize_doi(one("DO")),
        url=one("UR"),
        keywords=[collapse_ws(k) for k in (record.get("KW") or []) if collapse_ws(k)][:10],
        origin="manual",
        raw={"ris": {"type": one("TY")}},
    )


# ------------------------------------------------------------------- CSV/JSON

_CSV_TITLE_KEYS = ("title", "document title", "article title", "publication title")
_CSV_ABSTRACT_KEYS = ("abstract", "abstract note", "description")
_CSV_YEAR_KEYS = ("year", "publication year", "date", "publication date")
_CSV_AUTHOR_KEYS = ("author", "authors", "creator", "author full names")
_CSV_VENUE_KEYS = ("journal", "publication title", "venue", "source title",
                   "proceedings title", "container title")
_CSV_DOI_KEYS = ("doi", "di")
_CSV_URL_KEYS = ("url", "link", "fulltext url")
_CSV_KEYWORD_KEYS = ("keywords", "author keywords", "manual tags", "index keywords")


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str:
    lowered = {str(k).strip().lower(): (v or "") for k, v in row.items()}
    for key in keys:
        if lowered.get(key):
            return collapse_ws(str(lowered[key]))
    return ""


def parse_csv(text: str) -> list[Paper]:
    """Parse a CSV export. Column names are matched case-insensitively against
    the header conventions used by Zotero, Scopus, WoS and IEEE Xplore."""
    papers: list[Paper] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        title = _pick(row, _CSV_TITLE_KEYS)
        if not title:
            continue
        author_text = _pick(row, _CSV_AUTHOR_KEYS)
        authors = [
            Author(name=collapse_ws(
                f"{part.split(',')[1]} {part.split(',')[0]}"
                if part.count(",") == 1 else part
            ))
            for part in re.split(r";|\band\b", author_text) if collapse_ws(part)
        ]
        year_text = _pick(row, _CSV_YEAR_KEYS)
        papers.append(Paper(
            title=title,
            abstract=_pick(row, _CSV_ABSTRACT_KEYS),
            authors=authors[:30],
            year=coerce_int(re.sub(r"\D", "", year_text)[:4], 0) or None,
            venue=_pick(row, _CSV_VENUE_KEYS),
            doi=normalize_doi(_pick(row, _CSV_DOI_KEYS)),
            url=_pick(row, _CSV_URL_KEYS),
            keywords=[
                collapse_ws(k) for k in re.split(r"[;,]", _pick(row, _CSV_KEYWORD_KEYS))
                if collapse_ws(k)
            ][:10],
            origin="manual",
        ))
    return papers


def parse_json(text: str) -> list[Paper]:
    """Parse a JSON array of paper-like dicts, or CSL-JSON."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("skipping invalid JSON import: %s", exc)
        return []
    entries = data if isinstance(data, list) else data.get("papers") or data.get("items") or []
    papers: list[Paper] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = collapse_ws(str(entry.get("title") or ""))
        if not title:
            continue
        raw_authors = entry.get("author") or entry.get("authors") or []
        authors: list[Author] = []
        for person in raw_authors:
            if isinstance(person, dict):
                name = collapse_ws(
                    person.get("name")
                    or f"{person.get('given', '')} {person.get('family', '')}"
                )
            else:
                name = collapse_ws(str(person))
            if name:
                authors.append(Author(name=name))
        year = entry.get("year")
        if not year:
            issued = (entry.get("issued") or {}).get("date-parts") or [[]]
            year = issued[0][0] if issued and issued[0] else None
        papers.append(Paper(
            title=title,
            abstract=collapse_ws(str(entry.get("abstract") or "")),
            authors=authors,
            year=coerce_int(year, 0) or None,
            venue=collapse_ws(str(
                entry.get("venue") or entry.get("container-title") or
                entry.get("journal") or ""
            )),
            doi=normalize_doi(entry.get("DOI") or entry.get("doi")),
            url=str(entry.get("URL") or entry.get("url") or ""),
            keywords=[str(k) for k in (entry.get("keywords") or [])][:10],
            origin="manual",
        ))
    return papers


_PARSERS = {
    ".bib": parse_bibtex, ".ris": parse_ris, ".csv": parse_csv, ".json": parse_json,
}


def parse_file(path: Path) -> list[Paper]:
    """Parse one file, using the in-process (path, mtime) cache."""
    try:
        stat = path.stat()
    except OSError:
        return []
    cache_key = (str(path), stat.st_mtime)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    parser = _PARSERS.get(path.suffix.lower())
    if parser is None:
        return []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc)
        return []
    papers = parser(text)
    for paper in papers:
        paper.raw.setdefault("local", {})["source_file"] = str(path)
        paper.ensure_id()
    # Bound the cache so a long session indexing many files cannot grow forever.
    if len(_cache) > 32:
        _cache.clear()
    _cache[cache_key] = papers
    log.info("indexed %s local records from %s", len(papers), path.name)
    return papers


class LocalFilesProvider(Provider):
    meta = ProviderMeta(
        id="local",
        name="Local files",
        name_zh="本地文献文件",
        description="Searches BibTeX / RIS / CSV / JSON exports on disk, so an "
                    "existing Zotero or EndNote library joins the same pipeline.",
        description_zh="检索本地的 BibTeX / RIS / CSV / JSON 文件，让已有的 Zotero、"
                       "EndNote 文献库进入同一流程。",
        homepage="",
        docs_url="",
        tier="free",
        coverage="whatever you put in the import folder",
        disciplines=["all"],
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=False,
        boolean_operators=False,
        year_range=True,
        author_filter=True,
        returns_abstract=True,
        max_results_per_request=100000,
        supports_pagination=False,
    )
    rate_limit = RateLimit(min_interval_s=0.0, max_concurrency=1, max_retries=0,
                           note="local filesystem, no network")

    @staticmethod
    def import_dirs() -> list[Path]:
        """Directories scanned for bibliography files."""
        paths = get_paths()
        roots = [paths.reference_papers_dir]
        # Also pick up per-project reference folders so a project's own .bib is
        # searchable without extra configuration.
        if paths.workspace.is_dir():
            for entry in paths.workspace.iterdir():
                references = entry / "references"
                if references.is_dir():
                    roots.append(references)
        return [r for r in roots if r.is_dir()]

    def availability(self) -> ProviderAvailability:
        directories = self.import_dirs()
        if not directories:
            home = get_paths().reference_papers_dir
            return ProviderAvailability(
                available=False,
                reason=f"no import folder yet - put .bib/.ris/.csv files in {home}",
            )
        files = [
            p for d in directories for p in d.rglob("*")
            if p.suffix.lower() in _SUPPORTED
        ]
        if not files:
            return ProviderAvailability(
                available=False,
                reason=f"no .bib/.ris/.csv/.json files found in {directories[0]}",
            )
        return ProviderAvailability(available=True)

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        """Token-overlap scoring over the parsed local records.

        Simple and predictable: a record matches when it contains the query
        tokens in its title, abstract, venue, authors or keywords. Semantic
        matching happens later - once imported, these papers are embedded and
        ranked exactly like online results.
        """
        terms: list[str] = []
        for query_text in request.effective_queries():
            terms.extend(t for t in re.split(r"\W+", query_text.lower()) if len(t) > 2)
        if request.seed_text:
            terms.extend(
                t for t in re.split(r"\W+", request.seed_text.lower())[:80]
                if len(t) > 3
            )
        term_set = set(terms)

        scored: list[tuple[float, Paper]] = []
        for directory in self.import_dirs():
            for path in sorted(directory.rglob("*")):
                if path.suffix.lower() not in _SUPPORTED or not path.is_file():
                    continue
                for paper in parse_file(path):
                    if request.year_from and (paper.year or 0) < request.year_from:
                        continue
                    if request.year_to and (paper.year or 9999) > request.year_to:
                        continue
                    if request.open_access_only and not paper.is_open_access:
                        continue
                    if not term_set:
                        scored.append((0.0, paper))
                        continue
                    haystack = " ".join([
                        paper.title.lower(), paper.abstract.lower(),
                        paper.venue.lower(), " ".join(paper.keywords).lower(),
                        " ".join(a.name for a in paper.authors).lower(),
                    ])
                    hits = sum(1 for term in term_set if term in haystack)
                    if hits == 0:
                        continue
                    # Title matches count double - they are far more indicative.
                    title_hits = sum(1 for term in term_set if term in paper.title.lower())
                    scored.append(((hits + title_hits) / len(term_set), paper))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        out: list[Paper] = []
        for score, paper in scored[:limit]:
            paper.score = round(min(1.0, score), 4)
            out.append(paper)
        return out
