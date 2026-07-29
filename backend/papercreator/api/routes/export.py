"""Export routes: Markdown, LaTeX, DOCX, BibTeX, bundle, PDF, Overleaf.

Exports write into the project's ``exports/`` directory and return the path;
``GET /api/export/download`` then serves the file. That two-step shape exists
because the desktop app usually wants to reveal the file in Explorer rather than
download it, and because a LaTeX export is a directory, not a single file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...core.errors import NotFoundError, ValidationError
from ...core.logging_setup import get_logger
from ...store import projects as projects_store

log = get_logger(__name__)
router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """Which formats work on this machine, and what is missing for the rest."""
    from ...convert import exporters

    return exporters.describe_capabilities()


# Static conversion routes must be registered before ``/{project_id}`` below.
# Starlette matches routes in declaration order; putting these after the dynamic
# project route makes the literal word "convert" look like a project id.
class ConvertRequest(BaseModel):
    text: str
    direction: str = "md2tex"       # md2tex | tex2md
    unicode_safe: bool = False


@router.post("/convert")
def convert_text(request: ConvertRequest) -> dict[str, Any]:
    """Convert a snippet between Markdown and LaTeX."""
    from ...convert import markdown_latex

    if request.direction == "md2tex":
        return {
            "direction": request.direction,
            "result": markdown_latex.markdown_to_latex(
                request.text, unicode_safe=request.unicode_safe
            ),
        }
    if request.direction == "tex2md":
        return {
            "direction": request.direction,
            "result": markdown_latex.latex_to_markdown(request.text),
            "note": "LaTeX to Markdown is lossy: custom macros and unusual "
                    "environments are simplified",
        }
    raise ValidationError("direction must be 'md2tex' or 'tex2md'")


@router.get("/convert/capabilities")
def convert_capabilities() -> dict[str, Any]:
    from ...convert import markdown_latex

    return markdown_latex.describe()


class ExportRequest(BaseModel):
    format: str = "markdown"
    language: str = "primary"
    citation_style: str = ""
    # markdown
    include_bibliography: bool = True
    include_frontmatter: bool = True
    # latex
    document_class: str = "article"
    engine: str = ""
    include_toc: bool = False
    # docx
    use_pandoc: bool | None = None
    # bibtex
    cited_only: bool = True


@router.post("/{project_id}")
def export_project(project_id: str, request: ExportRequest) -> dict[str, Any]:
    """Export the manuscript. Returns the written path plus any warnings."""
    from ...convert import exporters

    projects_store.require(project_id)
    if request.format not in exporters.FORMATS:
        raise ValidationError(
            f"unknown format '{request.format}'. Available: "
            f"{', '.join(exporters.FORMATS)}"
        )

    # Each exporter accepts a different subset; passing an unknown keyword would
    # be a TypeError, so the options are filtered per format.
    common = {"language": request.language}
    options: dict[str, Any] = {
        "markdown": {
            **common,
            "citation_style": request.citation_style,
            "include_bibliography": request.include_bibliography,
            "include_frontmatter": request.include_frontmatter,
        },
        "latex": {
            **common,
            "document_class": request.document_class,
            "engine": request.engine,
            "include_toc": request.include_toc,
        },
        "docx": {
            **common,
            "citation_style": request.citation_style,
            "use_pandoc": request.use_pandoc,
        },
        "bibtex": {"cited_only": request.cited_only},
        "zip": {**common},
    }[request.format]

    result = exporters.export_project(project_id, request.format, **options)
    log.info(
        "exported project %s as %s to %s",
        project_id, request.format, result.get("path"),
    )
    return result


@router.post("/{project_id}/pdf")
def build_pdf(
    project_id: str,
    document_class: str = Query("article"),
    engine: str = Query(""),
) -> dict[str, Any]:
    """Export LaTeX and compile it locally.

    Requires a TeX engine on PATH. When absent, the LaTeX project is still written
    and the error names its path so the user can upload it to Overleaf instead.
    """
    from ...convert import exporters

    projects_store.require(project_id)
    return exporters.build_pdf(
        project_id, document_class=document_class, engine=engine
    )


@router.get("/{project_id}/download")
def download(project_id: str, path: str = Query(..., description="path from an export")) -> FileResponse:
    """Serve a previously exported file.

    The path must resolve inside the project's own directory - otherwise this
    endpoint would be an arbitrary file read.
    """
    project = projects_store.require(project_id)
    root = projects_store.project_root(project).resolve()
    target = Path(path).expanduser().resolve()
    try:
        inside = target.is_relative_to(root)
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise ValidationError(
            f"'{target}' is outside the project directory and cannot be served"
        )
    if not target.is_file():
        raise NotFoundError(f"'{target}' does not exist or is a directory")
    return FileResponse(
        str(target), filename=target.name, media_type="application/octet-stream"
    )


@router.get("/{project_id}/files")
def list_exports(project_id: str) -> dict[str, Any]:
    """What has already been exported, for the export panel's history."""
    from ...convert import exporters

    project = projects_store.require(project_id)
    directory = exporters.export_dir(project_id)
    items: list[dict[str, Any]] = []
    for entry in sorted(directory.rglob("*")):
        if entry.is_file():
            stat = entry.stat()
            items.append({
                "path": str(entry),
                "name": entry.name,
                "relative": str(entry.relative_to(directory)),
                "bytes": stat.st_size,
                "modified": stat.st_mtime,
                "suffix": entry.suffix,
            })
    return {
        "directory": str(directory),
        "project_root": str(projects_store.project_root(project)),
        "items": items,
    }


# ----------------------------------------------------------------- overleaf


@router.get("/{project_id}/overleaf/status")
def overleaf_status(project_id: str) -> dict[str, Any]:
    from ...convert import overleaf

    projects_store.require(project_id)
    return overleaf.status()


class OverleafZipRequest(BaseModel):
    document_class: str = "article"
    language: str = "primary"


@router.post("/{project_id}/overleaf/zip")
def overleaf_zip(project_id: str, request: OverleafZipRequest) -> dict[str, Any]:
    """Build the archive Overleaf's "Upload Project" accepts.

    Works with any Overleaf account, unlike the git bridge.
    """
    from ...convert import overleaf

    projects_store.require(project_id)
    return overleaf.prepare_zip(
        project_id,
        document_class=request.document_class,
        language=request.language,
    )


class OverleafPushRequest(BaseModel):
    document_class: str = "article"
    language: str = "primary"
    message: str = ""
    force: bool = False


@router.post("/{project_id}/overleaf/push")
def overleaf_push(project_id: str, request: OverleafPushRequest) -> dict[str, Any]:
    """Push to a configured Overleaf project over its git bridge.

    Replaces only the files PaperCreator owns (main.tex, references.bib,
    sections/, figures/), so a co-author's additions in Overleaf survive. Refuses
    a non-fast-forward push unless ``force`` is set.
    """
    from ...convert import overleaf

    projects_store.require(project_id)
    return overleaf.push_to_overleaf(
        project_id,
        document_class=request.document_class,
        language=request.language,
        message=request.message,
        force=request.force,
    )


@router.post("/{project_id}/overleaf/pull")
def overleaf_pull(
    project_id: str,
    apply_to_manuscript: bool = Query(
        False, description="import the fetched sections into the manuscript"
    ),
) -> dict[str, Any]:
    """Fetch the Overleaf project, optionally importing co-author edits.

    Importing converts LaTeX back to Markdown, which is lossy, and overwrites
    local section text - so a snapshot is taken first and the default is to fetch
    without applying.
    """
    from ...convert import overleaf

    projects_store.require(project_id)
    return overleaf.pull_from_overleaf(
        project_id, apply_to_manuscript=apply_to_manuscript
    )
