"""Extract searchable text from managed manuscript files.

TXT/Markdown/LaTeX and DOCX use the standard library. PDF uses ``pypdf``;
image-only PDFs report that OCR is required instead of pretending an empty
extraction succeeded. External source files are never read here: callers pass
the atomic managed copy inside ``.papercreator``.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

MAX_EXTRACTED_CHARS = 2_000_000
MAX_LIBRARY_ABSTRACT_CHARS = 80_000
MAX_DOCX_DOCUMENT_XML_BYTES = 32 * 1024 * 1024
MAX_DOCX_AUXILIARY_XML_BYTES = 8 * 1024 * 1024

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_W = f"{{{_WORD_NS}}}"
_M = f"{{{_MATH_NS}}}"


@dataclass(slots=True)
class DocumentExtraction:
    text: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    method: str = ""
    page_count: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def abstract_text(self) -> str:
        return self.text[:MAX_LIBRARY_ABSTRACT_CHARS]

    def audit(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "characters": len(self.text),
            "page_count": self.page_count,
            "truncated": self.truncated,
            "warnings": self.warnings,
            "title_detected": self.title,
            "authors_detected": self.authors,
            **self.metadata,
        }


def _bounded(text: str) -> tuple[str, bool]:
    normalised = re.sub(r"[ \t]+", " ", text.replace("\x00", ""))
    normalised = re.sub(r"\n{3,}", "\n\n", normalised).strip()
    if len(normalised) <= MAX_EXTRACTED_CHARS:
        return normalised, False
    return normalised[:MAX_EXTRACTED_CHARS].rstrip(), True


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _guess_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = re.sub(r"^[#*\s]+", "", line).strip()
        if 3 <= len(candidate) <= 300 and not candidate.startswith(("\\", "<!--")):
            return candidate
    return fallback


def _plain_source(path: Path) -> DocumentExtraction:
    raw = _read_text(path)
    method = "plain-text"
    if path.suffix.lower() in {".md", ".markdown"}:
        method = "markdown"
        raw = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", raw, flags=re.DOTALL)
        raw = re.sub(r"```.*?```", " ", raw, flags=re.DOTALL)
        raw = re.sub(r"!\[[^]]*]\([^)]*\)", " ", raw)
        raw = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", raw)
        raw = re.sub(r"^[#>]+\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"(?<!\\)[*_~`]", "", raw)
    elif path.suffix.lower() == ".tex":
        method = "latex-source"
        raw = re.sub(r"(?m)(?<!\\)%.*$", "", raw)
        raw = re.sub(r"\\(?:section|subsection|subsubsection|chapter)\*?\{([^{}]*)\}", r"\n\1\n", raw)
        raw = re.sub(r"\\(?:cite|ref|label)\{[^{}]*\}", " ", raw)
        raw = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^]]*\])?", " ", raw)
        raw = raw.replace("{", "").replace("}", "")
    text, truncated = _bounded(raw)
    return DocumentExtraction(
        text=text,
        title=_guess_title(text, path.stem),
        method=method,
        truncated=truncated,
    )


def _xml_local_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _docx_xml_part(
    archive: zipfile.ZipFile, name: str, *, max_bytes: int
) -> ElementTree.Element:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ValueError(f"DOCX is missing required part: {name}") from exc
    if info.file_size > max_bytes:
        raise ValueError(
            f"DOCX XML part {name} exceeds the {max_bytes // (1024 * 1024)} MiB safety limit"
        )
    try:
        return ElementTree.fromstring(archive.read(info))
    except ElementTree.ParseError as exc:
        raise ValueError(f"DOCX XML part {name} is malformed") from exc


def _omml_text(element: ElementTree.Element) -> str:
    """Render common Office Math structures as stable, editable linear math."""

    name = _xml_local_name(element)

    def child(part: str) -> ElementTree.Element | None:
        return next((item for item in element if _xml_local_name(item) == part), None)

    def rendered(part: str) -> str:
        item = child(part)
        return _omml_text(item) if item is not None else ""

    if name == "t":
        return element.text or ""
    if name in {"ctrlPr", "rPr", "naryPr", "radPr", "fPr", "dPr", "funcPr"}:
        return ""
    if name == "f":
        return f"({rendered('num')})/({rendered('den')})"
    if name == "sSup":
        return f"{rendered('e')}^{{{rendered('sup')}}}"
    if name == "sSub":
        return f"{rendered('e')}_{{{rendered('sub')}}}"
    if name == "sSubSup":
        return f"{rendered('e')}_{{{rendered('sub')}}}^{{{rendered('sup')}}}"
    if name == "rad":
        degree = rendered("deg")
        prefix = f"root[{degree}]" if degree else "sqrt"
        return f"{prefix}({rendered('e')})"
    if name == "func":
        return f"{rendered('fName')}({rendered('e')})"
    if name == "d":
        begin = next(
            (item.attrib.get(f"{_M}val", "(") for item in element.iter(f"{_M}begChr")),
            "(",
        )
        end = next(
            (item.attrib.get(f"{_M}val", ")") for item in element.iter(f"{_M}endChr")),
            ")",
        )
        return f"{begin}{rendered('e')}{end}"
    if name == "nary":
        operator = next(
            (item.attrib.get(f"{_M}val", "sum") for item in element.iter(f"{_M}chr")),
            "sum",
        )
        lower = rendered("sub")
        upper = rendered("sup")
        bounds = (f"_{{{lower}}}" if lower else "") + (f"^{{{upper}}}" if upper else "")
        return f"{operator}{bounds} {rendered('e')}"
    if name == "m":
        rows = []
        for row in element:
            cells = [_omml_text(item) for item in row if _xml_local_name(item) == "e"]
            if cells:
                rows.append(", ".join(cells))
        return f"matrix({'; '.join(rows)})"
    return "".join(_omml_text(item) for item in element)


def _docx_node_text(element: ElementTree.Element) -> str:
    name = _xml_local_name(element)
    if element.tag == f"{_W}t":
        return element.text or ""
    if element.tag == f"{_W}tab":
        return "\t"
    if element.tag in {f"{_W}br", f"{_W}cr"}:
        return "\n"
    if element.tag == f"{_W}footnoteReference":
        return f"[^fn{element.attrib.get(f'{_W}id', '?')}]"
    if element.tag == f"{_W}endnoteReference":
        return f"[^en{element.attrib.get(f'{_W}id', '?')}]"
    if element.tag == f"{_M}oMathPara":
        formula = _omml_text(element).strip()
        return f"\n$$\n{formula}\n$$\n" if formula else ""
    if element.tag == f"{_M}oMath":
        formula = _omml_text(element).strip()
        return f"${formula}$" if formula else ""
    if name in {"instrText", "delText"}:
        return ""
    return "".join(_docx_node_text(item) for item in element)


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    return _docx_node_text(paragraph).strip()


def _docx_table_text(table: ElementTree.Element) -> str:
    rows: list[str] = []
    for row in table.findall(f"{_W}tr"):
        cells: list[str] = []
        for cell in row.findall(f"{_W}tc"):
            parts = [
                _docx_paragraph_text(paragraph)
                for paragraph in cell.iter(f"{_W}p")
            ]
            cells.append(" <br> ".join(part for part in parts if part).replace("|", r"\|"))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _docx_body_blocks(container: ElementTree.Element) -> list[str]:
    blocks: list[str] = []
    for child in container:
        if child.tag == f"{_W}p":
            text = _docx_paragraph_text(child)
            if text:
                blocks.append(text)
        elif child.tag == f"{_W}tbl":
            text = _docx_table_text(child)
            if text:
                blocks.append(text)
        else:
            blocks.extend(_docx_body_blocks(child))
    return blocks


def _docx_notes(
    archive: zipfile.ZipFile, part: str, *, note_name: str, marker: str
) -> list[str]:
    try:
        archive.getinfo(part)
    except KeyError:
        return []
    root = _docx_xml_part(
        archive, part, max_bytes=MAX_DOCX_AUXILIARY_XML_BYTES
    )
    notes: list[str] = []
    for note in root.findall(f"{_W}{note_name}"):
        note_id = note.attrib.get(f"{_W}id", "")
        try:
            if int(note_id) < 1:
                continue
        except ValueError:
            continue
        parts = [
            _docx_paragraph_text(paragraph)
            for paragraph in note.iter(f"{_W}p")
        ]
        text = " ".join(item for item in parts if item).strip()
        if text:
            notes.append(f"[^{marker}{note_id}]: {text}")
    return notes


def _docx(path: Path) -> DocumentExtraction:
    blocks: list[str] = []
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    with zipfile.ZipFile(path) as archive:
        document = _docx_xml_part(
            archive, "word/document.xml", max_bytes=MAX_DOCX_DOCUMENT_XML_BYTES
        )
        body = document.find(f"{_W}body")
        blocks.extend(_docx_body_blocks(body if body is not None else document))
        footnotes = _docx_notes(
            archive, "word/footnotes.xml", note_name="footnote", marker="fn"
        )
        endnotes = _docx_notes(
            archive, "word/endnotes.xml", note_name="endnote", marker="en"
        )
        if footnotes or endnotes:
            blocks.extend(["Notes", *footnotes, *endnotes])
            warnings.append("DOCX footnotes/endnotes were appended after the manuscript body.")
        equation_count = len(list(document.iter(f"{_M}oMath")))
        table_count = len(list(document.iter(f"{_W}tbl")))
        if equation_count:
            warnings.append(
                "DOCX equations were preserved as linear math text; verify complex equation layout."
            )
        try:
            core = _docx_xml_part(
                archive, "docProps/core.xml", max_bytes=MAX_DOCX_AUXILIARY_XML_BYTES
            )
            for node in core.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag in {"title", "creator", "subject", "keywords"} and (node.text or "").strip():
                    metadata[tag] = (node.text or "").strip()
        except ValueError:
            warnings.append("DOCX core metadata was not available.")
    text, truncated = _bounded("\n\n".join(blocks))
    creator = str(metadata.get("creator") or "").strip()
    return DocumentExtraction(
        text=text,
        title=str(metadata.get("title") or "").strip() or _guess_title(text, path.stem),
        authors=[creator] if creator else [],
        method="docx-ooxml",
        truncated=truncated,
        warnings=warnings,
        metadata={
            "document_properties": metadata,
            "docx_structure": {
                "tables": table_count,
                "equations": equation_count,
                "footnotes": len(footnotes),
                "endnotes": len(endnotes),
            },
        },
    )


def _pdf(
    path: Path,
    *,
    use_ocr: bool = False,
    ocr_languages: str = "eng",
    ocr_max_pages: int = 50,
) -> DocumentExtraction:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - guard for old installations
        raise RuntimeError("PDF text extraction requires the pypdf package") from exc

    reader = PdfReader(str(path))
    warnings: list[str] = []
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                return DocumentExtraction(
                    title=path.stem, method="pypdf", page_count=len(reader.pages),
                    warnings=["The PDF is encrypted and needs a password before text can be extracted."],
                )
        except Exception:  # noqa: BLE001 - malformed third-party document
            return DocumentExtraction(
                title=path.stem, method="pypdf", page_count=len(reader.pages),
                warnings=["The encrypted PDF could not be opened."],
            )
    pages: list[str] = []
    for index, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - preserve other readable pages
            warnings.append(f"Page {index + 1} could not be extracted: {type(exc).__name__}.")
    ocr_metadata: dict[str, Any] = {}
    if use_ocr:
        from .local_ocr import MAX_OCR_PAGES, ocr_pdf_pages

        if not 1 <= ocr_max_pages <= MAX_OCR_PAGES:
            raise ValueError(f"OCR page limit must be between 1 and {MAX_OCR_PAGES}")
        candidates = [index for index, page_text in enumerate(pages) if len(page_text.strip()) < 20]
        selected = candidates[:ocr_max_pages]
        if len(candidates) > len(selected):
            warnings.append(
                f"OCR page limit reached; {len(candidates) - len(selected)} page(s) were not processed."
            )
        if selected:
            result = ocr_pdf_pages(path, selected, languages=ocr_languages)
            for index, page_text in result["texts"].items():
                if page_text.strip():
                    pages[int(index)] = page_text
            warnings.extend(result["warnings"])
            ocr_metadata = {
                "ocr": {
                    "engine": result["engine"],
                    "renderer": result["renderer"],
                    "languages": result["languages"],
                    "dpi": result["dpi"],
                    "candidate_pages": len(candidates),
                    "processed_pages": len(selected),
                    "text_pages_after_ocr": sum(bool(item.strip()) for item in pages),
                }
            }
    text, truncated = _bounded("\n\n".join(pages))
    if not text:
        warnings.append("No PDF text layer was found; this may be a scanned document that needs OCR.")
    raw_meta = reader.metadata or {}
    metadata = {str(key).lstrip("/"): str(value) for key, value in raw_meta.items() if value}
    author = metadata.get("Author", "").strip()
    return DocumentExtraction(
        text=text,
        title=metadata.get("Title", "").strip() or _guess_title(text, path.stem),
        authors=[author] if author else [],
        method="pypdf+tesseract-ocr" if ocr_metadata else "pypdf",
        page_count=len(reader.pages),
        truncated=truncated,
        warnings=warnings,
        metadata={"pdf_metadata": metadata, **ocr_metadata},
    )


def extract_document(
    path: Path,
    *,
    use_ocr: bool = False,
    ocr_languages: str = "eng",
    ocr_max_pages: int = 50,
) -> DocumentExtraction:
    """Extract one supported managed file without external programs."""
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".tex"}:
        return _plain_source(path)
    if suffix == ".docx":
        return _docx(path)
    if suffix == ".pdf":
        return _pdf(
            path,
            use_ocr=use_ocr,
            ocr_languages=ocr_languages,
            ocr_max_pages=ocr_max_pages,
        )
    raise ValueError(f"unsupported document extraction format: {suffix or '(none)'}")
