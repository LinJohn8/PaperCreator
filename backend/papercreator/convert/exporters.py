"""Export orchestration: one manuscript, five output formats.

Formats and how each handles citations - which is the part that differs:

============  ==================================================================
format        citation handling
============  ==================================================================
markdown      markers -> ``[1]`` numbering + a rendered reference list
latex         markers -> ``\\cite{key}`` + a generated ``.bib`` (real project)
docx          markers -> ``[1]`` numbering + a reference list (Word has no engine)
bibtex        the bibliography alone, for pasting into an existing project
zip           a bundle of the above, for handing to a co-author
============  ==================================================================

Pandoc is used for DOCX when available because its fidelity is better; the
built-in writer (:mod:`convert.docx_min`) is used otherwise, so the feature never
depends on an external binary being installed.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from ..core.errors import ExternalToolError, ValidationError
from ..core.logging_setup import get_logger
from ..core.util import safe_filename, utc_now_iso
from ..store import projects as projects_store
from ..writing import citations as citations_module
from ..writing import manuscript as manuscript_module
from . import docx_min, latex_project
from .markdown_latex import markdown_to_latex

log = get_logger(__name__)

FORMATS = ("markdown", "latex", "docx", "bibtex", "zip")


def pandoc_available() -> bool:
    return shutil.which("pandoc") is not None


def latex_engine_available(engine: str = "pdflatex") -> bool:
    return shutil.which(engine) is not None


def export_dir(project_id: str) -> Path:
    project = projects_store.require(project_id)
    directory = projects_store.project_root(project) / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def export_markdown(
    project_id: str,
    *,
    language: str = "primary",
    citation_style: str = "",
    include_bibliography: bool = True,
    include_frontmatter: bool = True,
    target: Path | None = None,
) -> dict[str, Any]:
    """Markdown with numbered citations and a reference list."""
    project = projects_store.require(project_id)
    assembled = manuscript_module.assemble(project_id, language=language)
    keys = assembled["keys"]
    by_id = {p.id: p for p in assembled["papers"]}
    style = citation_style or project.citation_style or "ieee"

    parts: list[str] = []
    if include_frontmatter:
        title = (
            project.title_zh if language == "paired" and project.title_zh
            else project.title
        )
        parts.append(f"# {title}\n")
        meta: list[str] = []
        if project.research_field:
            meta.append(f"**Field:** {project.research_field}")
        if project.target_venue:
            meta.append(f"**Target venue:** {project.target_venue}")
        meta.append(f"**Exported:** {utc_now_iso()}")
        meta.append(f"**Words:** {assembled['word_count']}")
        parts.append("  \n".join(meta) + "\n")

    numbering: dict[str, int] = {}
    unknown: set[str] = set()
    for block in assembled["blocks"]:
        text, numbering, missing = citations_module.to_numbered_citations(
            block["text"], keys, by_id, existing=numbering
        )
        unknown.update(missing)
        heading = "#" * max(1, min(6, block["level"] + (1 if include_frontmatter else 0)))
        parts.append(f"{heading} {block['title']}\n\n{text}\n")

    if include_bibliography and numbering:
        parts.append("## References\n")
        parts.append(
            citations_module.build_reference_list(
                assembled["papers"], numbering, keys, style=style
            )
        )

    content = "\n".join(parts).rstrip() + "\n"
    path = target or (
        export_dir(project_id) / f"{safe_filename(project.slug)}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "format": "markdown",
        "path": str(path),
        "bytes": len(content.encode("utf-8")),
        "words": assembled["word_count"],
        "references": len(numbering),
        "citation_style": style,
        "warnings": (
            [f"unmatched citation marker(s): {', '.join(sorted(unknown)[:10])}"]
            if unknown else []
        ),
    }


def export_latex(
    project_id: str,
    *,
    language: str = "primary",
    document_class: str = "article",
    engine: str = "",
    include_toc: bool = False,
    target: Path | None = None,
) -> dict[str, Any]:
    """A complete, compilable LaTeX project directory."""
    project = projects_store.require(project_id)
    assembled = manuscript_module.assemble(project_id, language=language)
    if not assembled["blocks"]:
        raise ValidationError(
            "there is nothing to export - the manuscript has no drafted sections"
        )
    directory = target or (
        export_dir(project_id) / f"{safe_filename(project.slug)}-latex"
    )
    report = latex_project.export_latex_project(
        project=project,
        blocks=assembled["blocks"],
        papers=assembled["papers"],
        keys=assembled["keys"],
        target_dir=directory,
        document_class=document_class,
        engine=engine,
        language=language,
        include_toc=include_toc,
        assets_dir=projects_store.project_root(project) / "assets",
    )
    report["format"] = "latex"
    report["words"] = assembled["word_count"]
    if not latex_engine_available(report["engine"]):
        report["warnings"].append(
            f"{report['engine']} was not found on this machine, so the PDF cannot "
            f"be built locally. Upload the folder to Overleaf, or install TeX Live "
            f"/ MiKTeX."
        )
    return report


def export_docx(
    project_id: str,
    *,
    language: str = "primary",
    citation_style: str = "",
    use_pandoc: bool | None = None,
    target: Path | None = None,
) -> dict[str, Any]:
    """Word document. Uses Pandoc when present, else the built-in writer."""
    project = projects_store.require(project_id)
    assembled = manuscript_module.assemble(project_id, language=language)
    if not assembled["blocks"]:
        raise ValidationError(
            "there is nothing to export - the manuscript has no drafted sections"
        )
    keys = assembled["keys"]
    by_id = {p.id: p for p in assembled["papers"]}
    style = citation_style or project.citation_style or "ieee"
    path = target or (export_dir(project_id) / f"{safe_filename(project.slug)}.docx")

    # Build the markdown that both paths consume, with numbered citations.
    numbering: dict[str, int] = {}
    unknown: set[str] = set()
    title = (
        project.title_zh if language == "paired" and project.title_zh
        else project.title
    )
    markdown_parts: list[str] = []
    for block in assembled["blocks"]:
        text, numbering, missing = citations_module.to_numbered_citations(
            block["text"], keys, by_id, existing=numbering
        )
        unknown.update(missing)
        markdown_parts.append(
            f"{'#' * max(1, min(6, block['level']))} {block['title']}\n\n{text}"
        )
    if numbering:
        markdown_parts.append("# References\n\n" + citations_module.build_reference_list(
            assembled["papers"], numbering, keys, style=style
        ))
    markdown_body = "\n\n".join(markdown_parts)

    prefer_pandoc = pandoc_available() if use_pandoc is None else use_pandoc
    engine_used = "builtin"
    if prefer_pandoc and pandoc_available():
        try:
            _pandoc_to_docx(f"% {title}\n\n{markdown_body}", path)
            engine_used = "pandoc"
        except ExternalToolError as exc:
            # Fall back rather than fail: the built-in writer always works.
            log.warning("pandoc failed (%s); using the built-in DOCX writer", exc)

    if engine_used == "builtin":
        blocks = [docx_min.Block(kind="title", text=title)]
        blocks.extend(docx_min.markdown_to_blocks(markdown_body))
        docx_min.write_docx(
            blocks, path, title=title, author="",
            # A CJK manuscript needs a font that has the glyphs.
            body_font="SimSun" if language == "paired" or project.language == "zh"
            else "Times New Roman",
        )

    return {
        "format": "docx",
        "path": str(path),
        "bytes": path.stat().st_size if path.exists() else 0,
        "writer": engine_used,
        "words": assembled["word_count"],
        "references": len(numbering),
        "citation_style": style,
        "warnings": (
            [f"unmatched citation marker(s): {', '.join(sorted(unknown)[:10])}"]
            if unknown else []
        ) + ([] if engine_used == "pandoc" else [
            "used the built-in DOCX writer (headings, lists, tables and emphasis "
            "are supported; equations remain editable literal text). Install "
            "Pandoc for typeset equations and higher fidelity."
        ]),
    }


def _pandoc_to_docx(markdown: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["pandoc", "-f", "markdown", "-t", "docx", "-o", str(target)],
            input=markdown.encode("utf-8"),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExternalToolError(f"could not run pandoc: {exc}") from exc
    if result.returncode != 0:
        raise ExternalToolError(
            f"pandoc exited {result.returncode}: "
            f"{result.stderr.decode('utf-8', 'replace')[:400]}"
        )


def export_bibtex(
    project_id: str, *, cited_only: bool = True, target: Path | None = None
) -> dict[str, Any]:
    """The bibliography alone."""
    project = projects_store.require(project_id)
    assembled = manuscript_module.assemble(project_id)
    papers = assembled["cited_papers"] if cited_only else assembled["papers"]
    content = citations_module.build_bibtex(papers, assembled["keys"])
    path = target or (
        export_dir(project_id) / f"{safe_filename(project.slug)}.bib"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "format": "bibtex",
        "path": str(path),
        "entries": len(papers),
        "cited_only": cited_only,
        "total_in_library": len(assembled["papers"]),
        "bytes": len(content.encode("utf-8")),
        "warnings": [],
    }


def export_bundle(
    project_id: str,
    *,
    language: str = "primary",
    include: tuple[str, ...] = ("markdown", "latex", "docx", "bibtex"),
    target: Path | None = None,
) -> dict[str, Any]:
    """A zip of several formats, for handing the whole thing to a co-author."""
    project = projects_store.require(project_id)
    staging = export_dir(project_id) / "_bundle"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, Any]] = []
    if "markdown" in include:
        reports.append(export_markdown(
            project_id, language=language,
            target=staging / f"{safe_filename(project.slug)}.md",
        ))
    if "latex" in include:
        reports.append(export_latex(
            project_id, language=language, target=staging / "latex",
        ))
    if "docx" in include:
        reports.append(export_docx(
            project_id, language=language,
            target=staging / f"{safe_filename(project.slug)}.docx",
        ))
    if "bibtex" in include:
        reports.append(export_bibtex(
            project_id, target=staging / f"{safe_filename(project.slug)}.bib",
        ))

    path = target or (
        export_dir(project_id) / f"{safe_filename(project.slug)}-bundle.zip"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(staging.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(staging))
    shutil.rmtree(staging, ignore_errors=True)

    return {
        "format": "zip",
        "path": str(path),
        "bytes": path.stat().st_size,
        "included": [r["format"] for r in reports],
        "reports": reports,
        "warnings": [w for r in reports for w in r.get("warnings", [])],
    }


def export_project(
    project_id: str, fmt: str = "markdown", **options: Any
) -> dict[str, Any]:
    """Dispatch to the requested exporter."""
    if fmt not in FORMATS:
        raise ValidationError(
            f"unknown export format '{fmt}'. Available: {', '.join(FORMATS)}"
        )
    if fmt == "markdown":
        return export_markdown(project_id, **options)
    if fmt == "latex":
        return export_latex(project_id, **options)
    if fmt == "docx":
        return export_docx(project_id, **options)
    if fmt == "bibtex":
        return export_bibtex(project_id, **options)
    return export_bundle(project_id, **options)


def build_pdf(
    project_id: str, *, document_class: str = "article", engine: str = ""
) -> dict[str, Any]:
    """Export LaTeX then compile it locally, if an engine is installed.

    Runs the full latex/bibtex/latex/latex sequence, because a single pass leaves
    every citation as ``[?]``. Compilation output is captured and returned - LaTeX
    errors are unreadable without it.
    """
    report = export_latex(
        project_id, document_class=document_class, engine=engine
    )
    directory = Path(report["path"])
    resolved_engine = report["engine"]
    if not latex_engine_available(resolved_engine):
        raise ExternalToolError(
            f"{resolved_engine} is not installed, so a PDF cannot be built here. "
            f"The LaTeX project was written to {directory} - upload it to Overleaf "
            f"or install TeX Live / MiKTeX.",
            details={"latex_path": str(directory), "engine": resolved_engine},
        )
    logs: list[dict[str, Any]] = []
    for step, command in enumerate([
        [resolved_engine, "-interaction=nonstopmode", "-halt-on-error", "main"],
        ["bibtex", "main"],
        [resolved_engine, "-interaction=nonstopmode", "main"],
        [resolved_engine, "-interaction=nonstopmode", "main"],
    ]):
        if command[0] == "bibtex" and not shutil.which("bibtex"):
            logs.append({"step": step, "command": " ".join(command),
                         "skipped": "bibtex not installed"})
            continue
        try:
            result = subprocess.run(
                command, cwd=str(directory), capture_output=True,
                timeout=300, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExternalToolError(
                f"{command[0]} failed: {exc}", details={"cwd": str(directory)}
            ) from exc
        logs.append({
            "step": step,
            "command": " ".join(command),
            "returncode": result.returncode,
            "tail": result.stdout.decode("utf-8", "replace")[-1500:],
        })
    pdf = directory / "main.pdf"
    if not pdf.exists():
        raise ExternalToolError(
            "the LaTeX run finished but produced no PDF. See the log tails for the "
            "first error.",
            details={"logs": logs, "path": str(directory)},
        )
    return {
        "format": "pdf",
        "path": str(pdf),
        "bytes": pdf.stat().st_size,
        "engine": resolved_engine,
        "latex_path": str(directory),
        "logs": logs,
        "warnings": report.get("warnings", []),
    }


def describe_capabilities() -> dict[str, Any]:
    """What can be exported on this machine, for the export dialog."""
    engines = {
        engine: latex_engine_available(engine)
        for engine in ("pdflatex", "xelatex", "lualatex")
    }
    return {
        "formats": [
            {"id": "markdown", "name": "Markdown", "always_available": True,
             "note": "numbered citations plus a reference list"},
            {"id": "latex", "name": "LaTeX project", "always_available": True,
             "note": "main.tex, sections/, references.bib - upload to Overleaf or "
                     "compile locally"},
            {"id": "docx", "name": "Word (.docx)", "always_available": True,
             "note": "Pandoc is used when installed; otherwise the built-in "
                     "writer, which covers headings, lists, tables and emphasis"},
            {"id": "bibtex", "name": "BibTeX", "always_available": True,
             "note": "the bibliography alone"},
            {"id": "zip", "name": "Bundle (.zip)", "always_available": True,
             "note": "several formats in one archive"},
        ],
        "pandoc": pandoc_available(),
        "latex_engines": engines,
        "can_build_pdf": any(engines.values()),
        "document_classes": latex_project.describe_classes(),
        "citation_styles": list(citations_module.CITATION_STYLES),
    }
