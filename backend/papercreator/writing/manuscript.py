"""Manuscript operations above the store: templates, bilingual pairing, stats.

The store handles rows and files; this module handles the operations a user
performs on a manuscript as a document: apply a structure, keep the two language
versions aligned, count progress, and assemble text for export.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import ConflictError, ValidationError
from ..core.logging_setup import get_logger
from ..core.models import DocumentModel, Paper, SectionModel
from ..core.util import detect_language, word_count
from ..store import documents as documents_store
from ..store import papers as papers_store
from ..store import projects as projects_store
from . import citations as citations_module
from . import templates as templates_module

log = get_logger(__name__)


def apply_template(
    project_id: str,
    template_id: str = "",
    *,
    target_words: int = 0,
    replace: bool = False,
) -> DocumentModel:
    """Create the section skeleton for a project from a template.

    ``replace=False`` (the default) only adds sections that do not already exist,
    so applying a template to a project with drafted content is safe. ``True``
    refuses if any section has content, rather than silently destroying work -
    the caller must delete sections explicitly.
    """
    project = projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    existing = {s.key: s for s in documents_store.list_sections(document.id)}

    if replace:
        with_content = [k for k, s in existing.items() if s.content.strip()]
        if with_content:
            raise ConflictError(
                f"cannot replace the structure: {len(with_content)} section(s) "
                f"already contain text ({', '.join(with_content[:5])}). Delete "
                f"them individually first, or apply without replace.",
                details={"sections_with_content": with_content},
            )
        for section in existing.values():
            documents_store.delete_section(section.id)
        existing = {}

    resolved_id = template_id or project.template_id or "generic"
    sections = templates_module.scaled_sections(resolved_id, target_words)
    created = 0
    for spec in sections:
        if spec["key"] in existing:
            continue
        english_target = int(spec.get("target_words", 0) or 0)
        chinese_target = int(
            spec.get("target_words_zh", round(english_target * 0.7)) or 0
        )
        documents_store.create_section(
            document.id,
            key=spec["key"],
            title=spec["title"],
            title_zh=spec.get("title_zh", ""),
            ordering=spec["ordering"],
            level=spec.get("level", 1),
            guidance=spec.get("guidance", ""),
            target_words=chinese_target if project.language == "zh" else english_target,
            # Historical column name notwithstanding, this is the paired-language
            # target.  A Chinese-primary project therefore pairs with English.
            target_words_zh=english_target if project.language == "zh" else chinese_target,
            status="empty",
        )
        created += 1

    if resolved_id != project.template_id:
        projects_store.update(project_id, template_id=resolved_id)
    log.info(
        "applied template '%s' to project %s: %s section(s) created",
        resolved_id, project_id, created,
    )
    documents_store.flush_document_to_disk(document.id)
    return documents_store.require_document(document.id)


def bilingual_status(project_id: str) -> dict[str, Any]:
    """Per-section alignment of the two language versions.

    The requirement is a Chinese/English side-by-side manuscript, so the useful
    question is not "is there a translation" but "is the translation still in
    step with the source". Drift is detected by comparing word counts against the
    ratio typical for the language pair: Chinese runs roughly 0.6-1.0 characters
    per English word, so a ratio far outside that means one side was edited after
    the other.
    """
    document = documents_store.primary_document(project_id)
    project = projects_store.require(project_id)
    primary_is_zh = project.language == "zh"

    sections: list[dict[str, Any]] = []
    aligned = missing = drifted = 0
    for section in documents_store.list_sections(document.id):
        primary_words = word_count(section.content)
        paired_words = word_count(section.content_zh)
        entry: dict[str, Any] = {
            "key": section.key,
            "title": section.title,
            "title_zh": section.title_zh,
            "primary_words": primary_words,
            "paired_words": paired_words,
            "primary_language": project.language,
            "paired_language": "en" if primary_is_zh else "zh",
            "status": "empty",
            "detected_primary": detect_language(section.content) if section.content else "",
        }
        if not section.content.strip():
            entry["status"] = "empty"
        elif not section.content_zh.strip():
            entry["status"] = "untranslated"
            missing += 1
        else:
            ratio = paired_words / max(1, primary_words)
            # Wide band: translation length legitimately varies. Outside it, one
            # side has almost certainly been edited independently.
            low, high = (1.0, 2.6) if primary_is_zh else (0.4, 1.6)
            if low <= ratio <= high:
                entry["status"] = "aligned"
                aligned += 1
            else:
                entry["status"] = "drifted"
                entry["ratio"] = round(ratio, 2)
                entry["expected_ratio"] = f"{low}-{high}"
                drifted += 1
        sections.append(entry)

    return {
        "project_id": project_id,
        "document_id": document.id,
        "bilingual_enabled": project.bilingual,
        "primary_language": project.language,
        "summary": {
            "total": len(sections), "aligned": aligned,
            "untranslated": missing, "drifted": drifted,
            "empty": sum(1 for s in sections if s["status"] == "empty"),
        },
        "sections": sections,
        "note": "drift is inferred from the word-count ratio between the two "
                "versions; review flagged sections manually",
    }


def swap_languages(project_id: str) -> dict[str, Any]:
    """Exchange the primary and paired language for every section.

    Needed when a user drafts in Chinese and later decides English is the
    submission language: swapping is preferable to re-translating, which would
    lose the edits made to the Chinese side.
    """
    project = projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    swapped = 0
    for section in documents_store.list_sections(document.id):
        if not section.content.strip() and not section.content_zh.strip():
            continue
        documents_store.update_section(
            section.id,
            content=section.content_zh,
            content_zh=section.content,
            title=section.title_zh or section.title,
            title_zh=section.title or section.title_zh,
        )
        swapped += 1
    new_language = "en" if project.language == "zh" else "zh"
    projects_store.update(project_id, language=new_language)
    documents_store.flush_document_to_disk(document.id)
    log.info(
        "swapped languages for project %s: primary is now %s (%s sections)",
        project_id, new_language, swapped,
    )
    return {"project_id": project_id, "primary_language": new_language,
            "sections_swapped": swapped}


def manuscript_stats(project_id: str) -> dict[str, Any]:
    """Progress figures for the status bar and the project dashboard."""
    document = documents_store.primary_document(project_id)
    base = documents_store.document_stats(document.id)
    sections = documents_store.list_sections(document.id)

    citation_ids: set[str] = set()
    for section in sections:
        citation_ids.update(section.cited_paper_ids)
    library_count = papers_store.project_paper_count(project_id)

    return {
        **base,
        "document_id": document.id,
        "project_id": project_id,
        "papers_in_project": library_count,
        "papers_cited": len(citation_ids),
        "citation_coverage": round(
            len(citation_ids) / library_count, 3
        ) if library_count else 0.0,
        "sections_detail": [
            {
                "key": s.key, "title": s.title, "status": s.status,
                "words": s.word_count, "target": s.target_words,
                "words_zh": word_count(s.content_zh),
                "target_zh": s.target_words_zh,
                "citations": len(s.cited_paper_ids),
                "completion": round(s.word_count / s.target_words, 2)
                if s.target_words else 0.0,
                "completion_zh": round(
                    word_count(s.content_zh) / s.target_words_zh, 2
                ) if s.target_words_zh else 0.0,
            }
            for s in sections
        ],
    }


def assemble(
    project_id: str,
    *,
    language: str = "primary",
    only_keys: list[str] | None = None,
    include_headings: bool = True,
) -> dict[str, Any]:
    """Concatenate the manuscript for export or review.

    Returns the text plus the citation key map and the cited papers, because
    every consumer (LaTeX, DOCX, Markdown) needs all three and computing them
    separately would risk them disagreeing.
    """
    document = documents_store.primary_document(project_id)
    sections = documents_store.list_sections(document.id)
    if only_keys:
        wanted = set(only_keys)
        sections = [s for s in sections if s.key in wanted]

    paper_ids = papers_store.project_paper_ids(project_id)
    papers = papers_store.get_many(paper_ids)
    keys = citations_module.CitationKeyMap.build(papers)
    by_id = {p.id: p for p in papers}

    blocks: list[dict[str, Any]] = []
    for section in sections:
        text = section.content_zh if language == "paired" else section.content
        if not text.strip():
            continue
        title = section.title_zh if language == "paired" and section.title_zh \
            else section.title
        blocks.append({
            "key": section.key,
            "title": title,
            "level": section.level,
            "text": text,
            "words": word_count(text),
        })

    texts = [b["text"] for b in blocks]
    cited = citations_module.cited_papers(texts, keys, by_id)
    body_parts: list[str] = []
    for block in blocks:
        if include_headings:
            body_parts.append(f"{'#' * max(1, min(6, block['level']))} {block['title']}")
        body_parts.append(block["text"])
    return {
        "project_id": project_id,
        "document_id": document.id,
        "language": language,
        "blocks": blocks,
        "text": "\n\n".join(body_parts).strip() + "\n",
        "keys": keys,
        "papers": papers,
        "cited_papers": cited,
        "word_count": sum(b["words"] for b in blocks),
    }


def set_section_content(
    project_id: str,
    section_key: str,
    *,
    content: str | None = None,
    content_zh: str | None = None,
    status: str = "",
) -> SectionModel:
    """Edit one section and refresh its derived citation list.

    The editor saves through here (rather than the store directly) so the cited
    paper set stays in sync with the text on every save - otherwise the export
    bibliography drifts from what the manuscript actually cites.
    """
    document = documents_store.primary_document(project_id)
    section = documents_store.get_section_by_key(document.id, section_key)
    if section is None:
        raise ValidationError(
            f"section '{section_key}' does not exist in this project"
        )
    fields: dict[str, Any] = {}
    if content is not None:
        fields["content"] = content
        paper_ids = papers_store.project_paper_ids(project_id)
        keys = citations_module.CitationKeyMap.build(
            papers_store.get_many(paper_ids)
        )
        fields["cited_paper_ids"] = [
            keys.paper_for(key) for key in citations_module.find_markers(content)
            if keys.paper_for(key)
        ]
    if content_zh is not None:
        fields["content_zh"] = content_zh
    if status:
        fields["status"] = status
    updated = documents_store.update_section(section.id, **fields)
    documents_store.flush_document_to_disk(document.id)
    return updated


def regenerate_bibliography(project_id: str, *, cited_only: bool = True) -> dict[str, Any]:
    """Write ``references/references.bib`` for the project.

    ``cited_only`` is the default because a bibliography listing every retrieved
    paper - most of them uncited - is a visible defect in a submission.
    """
    project = projects_store.require(project_id)
    assembled = assemble(project_id)
    papers: list[Paper] = (
        assembled["cited_papers"] if cited_only else assembled["papers"]
    )
    bibtex = citations_module.build_bibtex(papers, assembled["keys"])
    target = projects_store.project_root(project) / "references" / "references.bib"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(bibtex, encoding="utf-8")
    log.info("wrote %s entries to %s", len(papers), target)
    return {
        "path": str(target),
        "entries": len(papers),
        "cited_only": cited_only,
        "total_in_library": len(assembled["papers"]),
    }
