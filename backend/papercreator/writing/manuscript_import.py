"""Conflict-aware import of an existing document into manuscript sections."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any

from ..core.db import transaction
from ..core.errors import ConflictError, ValidationError
from ..core.util import new_id, safe_filename, slugify, utc_now_iso, word_count
from ..importers.document_text import extract_document
from ..store import documents as documents_store
from ..store import projects as projects_store
from ..store import snapshots as snapshots_store

SUPPORTED = {".pdf", ".docx", ".md", ".markdown", ".txt", ".tex"}
MAX_SOURCE_BYTES = 100 * 1024 * 1024
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_COMMON_HEADING = re.compile(
    r"^(abstract|摘要|introduction|引言|绪论|related work|literature review|"
    r"相关工作|文献综述|method(?:ology)?|methods|方法|实验方法|results?|结果|"
    r"discussion|讨论|conclusion|conclusions|结论|references|参考文献)\s*$",
    re.IGNORECASE,
)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_markdown(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _section(title: str, content: str, level: int, index: int) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", title).strip() or f"Imported section {index + 1}"
    content = content.strip()
    return {
        "index": index,
        "title": title[:300],
        "key": slugify(title, max_length=50, fallback=f"imported-{index + 1}"),
        "level": max(1, min(6, level)),
        "content": content,
        "characters": len(content),
        "word_count": word_count(content),
    }


def _split_markdown(text: str) -> list[dict[str, Any]]:
    matches = list(_MD_HEADING.finditer(text))
    if not matches:
        return []
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(_section(match.group(2), text[match.end():end], len(match.group(1)), index))
    return sections


def _split_plain(text: str, fallback_title: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    headings = [index for index, line in enumerate(lines) if _COMMON_HEADING.match(line.strip())]
    if len(headings) < 2:
        return [_section(fallback_title or "Imported manuscript", text, 1, 0)]
    sections: list[dict[str, Any]] = []
    prefix = "\n".join(lines[: headings[0]]).strip()
    if prefix:
        sections.append(_section(fallback_title or "Front matter", prefix, 1, 0))
    for heading_index, line_index in enumerate(headings):
        end = headings[heading_index + 1] if heading_index + 1 < len(headings) else len(lines)
        sections.append(
            _section(lines[line_index], "\n".join(lines[line_index + 1 : end]), 1, len(sections))
        )
    return sections


def _analyse(
    path_value: str,
    *,
    project_id: str = "",
    use_ocr: bool = False,
    ocr_languages: str = "eng",
    ocr_max_pages: int = 50,
) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise ValidationError("choose an existing manuscript file")
    if path.suffix.lower() not in SUPPORTED:
        raise ValidationError(
            "unsupported manuscript format; use PDF, DOCX, Markdown, TXT or TeX"
        )
    size = path.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise ValidationError("manuscript import is limited to 100 MB per file")
    try:
        extraction = extract_document(
            path,
            use_ocr=use_ocr,
            ocr_languages=ocr_languages,
            ocr_max_pages=ocr_max_pages,
        )
    except (RuntimeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc
    requires_ocr = path.suffix.lower() == ".pdf" and not extraction.text.strip()
    if not extraction.text.strip() and not requires_ocr:
        raise ValidationError("no text could be extracted from the selected manuscript")
    if requires_ocr:
        sections = []
    elif path.suffix.lower() in {".md", ".markdown"}:
        sections = _split_markdown(_read_markdown(path))
    else:
        sections = _split_plain(extraction.text, extraction.title or path.stem)
    existing: set[str] = set()
    if project_id:
        document = documents_store.primary_document(project_id)
        existing = {item.key for item in documents_store.list_sections(document.id)}
    result = {
        "source_path": str(path),
        "source_name": path.name,
        "source_sha256": _digest(path),
        "size_bytes": size,
        "method": extraction.method,
        "title": extraction.title,
        "page_count": extraction.page_count,
        "warnings": extraction.warnings,
        "truncated": extraction.truncated,
        "sections": [{**item, "key_conflict": item["key"] in existing} for item in sections],
        "section_count": len(sections),
        "characters": sum(item["characters"] for item in sections),
        "existing_sections": len(existing),
        "requires_ocr": requires_ocr,
        "ocr_used": bool(extraction.metadata.get("ocr")),
        "ocr_languages": ocr_languages if use_ocr else "",
        "ocr_max_pages": ocr_max_pages if use_ocr else 0,
        "extraction_metadata": extraction.metadata,
    }
    if path.suffix.lower() == ".pdf":
        from ..importers.local_ocr import capabilities

        result["ocr_capabilities"] = capabilities()
    return result


def preview(
    path_value: str,
    *,
    project_id: str = "",
    use_ocr: bool = False,
    ocr_languages: str = "eng",
    ocr_max_pages: int = 50,
) -> dict[str, Any]:
    """Return bounded excerpts for review; full text never round-trips via UI."""
    analysed = _analyse(
        path_value,
        project_id=project_id,
        use_ocr=use_ocr,
        ocr_languages=ocr_languages,
        ocr_max_pages=ocr_max_pages,
    )
    return {
        **analysed,
        "sections": [
            {
                **{key: value for key, value in item.items() if key != "content"},
                "content_preview": str(item["content"])[:2_000],
            }
            for item in analysed["sections"]
        ],
    }


def apply(
    project_id: str,
    *,
    source_path: str,
    source_sha256: str,
    mode: str = "append",
    selected_indices: list[int] | None = None,
    confirm_replace: bool = False,
    use_ocr: bool = False,
    ocr_languages: str = "eng",
    ocr_max_pages: int = 50,
) -> dict[str, Any]:
    if mode not in {"append", "replace"}:
        raise ValidationError("import mode must be append or replace")
    if mode == "replace" and not confirm_replace:
        raise ConflictError("replacing the manuscript requires explicit confirmation")
    project = projects_store.require(project_id)
    current = _analyse(
        source_path,
        project_id=project_id,
        use_ocr=use_ocr,
        ocr_languages=ocr_languages,
        ocr_max_pages=ocr_max_pages,
    )
    if current["source_sha256"] != source_sha256:
        raise ConflictError("the source file changed after preview; preview it again")
    chosen = current["sections"]
    if selected_indices is not None:
        wanted = set(selected_indices)
        chosen = [item for item in chosen if int(item["index"]) in wanted]
    if not chosen:
        raise ValidationError("select at least one imported section")

    source = Path(source_path).resolve()
    audit_dir = projects_store.project_root(project) / ".papercreator" / "imports"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9A-Za-z_-]", "-", utc_now_iso())
    managed = audit_dir / safe_filename(f"{stamp}-{source_sha256[:10]}-{source.name}")
    temporary = audit_dir / f".{new_id('import')}.tmp"
    shutil.copy2(source, temporary)
    os.replace(temporary, managed)

    document = documents_store.primary_document(project_id)
    snapshot: dict[str, Any] | None = None
    database_committed = False
    try:
        if mode == "replace":
            snapshot = snapshots_store.capture(
                project_id, label=f"before importing {source.name}", kind="manual"
            )
        with transaction():
            if mode == "replace":
                for section in documents_store.list_sections(document.id):
                    documents_store.delete_section(section.id)
            used = {item.key for item in documents_store.list_sections(document.id)}
            created = []
            for item in chosen:
                base = str(item["key"])
                key = base
                suffix = 2
                while key in used:
                    key = f"{base}-{suffix}"
                    suffix += 1
                used.add(key)
                section = documents_store.create_section(
                    document.id,
                    key=key,
                    title=str(item["title"]),
                    title_zh=str(item["title"]) if project.language == "zh" else "",
                    level=int(item["level"]),
                    content=str(item["content"]),
                    guidance=f"Imported from {source.name}; review structure and citations.",
                )
                created.append(section.model_dump(exclude={"content", "content_zh"}))
        database_committed = True
        documents_store.flush_document_to_disk(document.id, force=True)
    except BaseException:
        # Before commit there is no imported state to audit.  After commit, keep
        # the source copy even if the disk mirror fails: the DB now references
        # imported text and the copy plus safety snapshot are recovery evidence.
        if not database_committed:
            managed.unlink(missing_ok=True)
        raise
    return {
        "mode": mode,
        "created": created,
        "created_count": len(created),
        "managed_source": str(managed),
        "source_sha256": source_sha256,
        "safety_snapshot": snapshot,
        "warnings": current["warnings"],
    }
