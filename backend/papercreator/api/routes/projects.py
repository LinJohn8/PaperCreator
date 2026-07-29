"""Project routes: CRUD, collections, disk import, git initialisation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.logging_setup import get_logger
from ...core.models import ProjectModel
from ...store import papers as papers_store
from ...store import projects as projects_store

log = get_logger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    title: str
    title_zh: str = ""
    description: str = ""
    idea: str = ""
    research_field: str = ""
    target_venue: str = ""
    template_id: str = "generic"
    language: str = "en"
    bilingual: bool = True
    citation_style: str = "ieee"
    slug: str = ""
    git_enabled: bool = True
    # Create the section skeleton immediately, so the editor is usable at once.
    apply_template: bool = True
    target_words: int = 0


class ProjectUpdate(BaseModel):
    title: str | None = None
    title_zh: str | None = None
    description: str | None = None
    idea: str | None = None
    research_field: str | None = None
    target_venue: str | None = None
    template_id: str | None = None
    language: str | None = None
    bilingual: bool | None = None
    citation_style: str | None = None
    status: str | None = None
    settings: dict[str, Any] | None = None


@router.get("")
def list_projects(status: str = "") -> dict[str, Any]:
    projects = projects_store.list_projects(status=status)
    return {
        "items": [p.model_dump() for p in projects],
        "importable": projects_store.discover_on_disk(),
    }


@router.post("")
def create_project(request: ProjectCreate) -> dict[str, Any]:
    """Create a project: row, workspace directory, collection, git repo, sections."""
    project = projects_store.create(
        title=request.title,
        title_zh=request.title_zh,
        description=request.description,
        idea=request.idea,
        research_field=request.research_field,
        target_venue=request.target_venue,
        template_id=request.template_id,
        language=request.language,
        bilingual=request.bilingual,
        citation_style=request.citation_style,
        slug=request.slug,
        git_enabled=request.git_enabled,
    )
    result: dict[str, Any] = {"project": project.model_dump()}

    if request.apply_template:
        from ...writing import manuscript

        document = manuscript.apply_template(
            project.id, request.template_id, target_words=request.target_words
        )
        result["document"] = {
            "id": document.id,
            "sections": [s.key for s in document.sections],
        }

    if request.git_enabled:
        from ...vcs import git as git_module

        if git_module.git_available():
            result["git"] = git_module.init_repo(
                projects_store.project_root(project)
            )
        else:
            result["git"] = {
                "created": False,
                "reason": "git is not installed; version control is unavailable "
                          "for this project until it is",
            }
    return result


@router.get("/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    """Project plus everything the workbench needs to open it."""
    from ...store import analyses as analyses_store
    from ...store import documents as documents_store
    from ...vcs import git as git_module
    from ...writing import manuscript

    project = projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    return {
        "project": projects_store.update(project_id).model_dump(),  # refresh counts
        "document": document.model_dump(),
        "collections": papers_store.list_collections(project_id),
        "stats": manuscript.manuscript_stats(project_id),
        "bilingual": manuscript.bilingual_status(project_id),
        "analyses": analyses_store.list_analyses(project_id, limit=10),
        "latest_analysis_id": analyses_store.latest_analysis_id(project_id),
        "git": git_module.status(projects_store.project_root(project)),
    }


@router.patch("/{project_id}")
def update_project(project_id: str, request: ProjectUpdate) -> dict[str, Any]:
    fields = {k: v for k, v in request.model_dump().items() if v is not None}
    project = projects_store.update(project_id, **fields)
    return {"project": project.model_dump()}


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    remove_files: bool = Query(
        False, description="also delete the workspace directory (irreversible)"
    ),
) -> dict[str, Any]:
    """Delete a project.

    ``remove_files`` is off by default and refuses to touch anything outside the
    workspace. Papers stay in the global library, since other projects may use
    them.
    """
    return projects_store.delete(project_id, remove_files=remove_files)


class RelocateRequest(BaseModel):
    path: str


@router.post("/{project_id}/relocate")
def relocate_project(project_id: str, request: RelocateRequest) -> dict[str, Any]:
    project = projects_store.relocate(project_id, request.path)
    return {"project": project.model_dump()}


class ImportRequest(BaseModel):
    path: str
    reindex: bool = True


@router.post("/import")
def import_project(request: ImportRequest) -> dict[str, Any]:
    """Recreate a project row from a workspace directory's ``project.json``.

    Used after a database loss or when a workspace is copied between machines.
    Paper links cannot be recovered - they exist only in the database - so the
    response says so.
    """
    from ...store import documents as documents_store

    project = projects_store.import_from_disk(request.path)
    result: dict[str, Any] = {
        "project": project.model_dump(),
        "warnings": [
            "paper-to-project links live only in the database and could not be "
            "recovered; re-run a search or re-import your .bib to rebuild them"
        ],
    }
    if request.reindex:
        document = documents_store.primary_document(project.id)
        result["reindexed"] = documents_store.reindex_from_disk(document.id)
    return result


# ---------------------------------------------------------------- collections


class CollectionCreate(BaseModel):
    name: str
    kind: str = "manual"
    description: str = ""


@router.get("/{project_id}/collections")
def list_collections(project_id: str) -> dict[str, Any]:
    projects_store.require(project_id)
    return {"items": papers_store.list_collections(project_id)}


@router.post("/{project_id}/collections")
def create_collection(project_id: str, request: CollectionCreate) -> dict[str, Any]:
    projects_store.require(project_id)
    return papers_store.ensure_collection(project_id, request.name, kind=request.kind)


class CollectionPapers(BaseModel):
    paper_ids: list[str]


@router.post("/{project_id}/collections/{collection_id}/papers")
def add_papers(
    project_id: str, collection_id: str, request: CollectionPapers
) -> dict[str, Any]:
    projects_store.require(project_id)
    added = papers_store.add_to_collection(collection_id, request.paper_ids)
    projects_store.touch(project_id)
    return {"added": added, "requested": len(request.paper_ids)}


@router.delete("/{project_id}/collections/{collection_id}/papers")
def remove_papers(
    project_id: str, collection_id: str, request: CollectionPapers
) -> dict[str, Any]:
    projects_store.require(project_id)
    removed = papers_store.remove_from_collection(collection_id, request.paper_ids)
    return {"removed": removed}


@router.delete("/{project_id}/collections/{collection_id}")
def delete_collection(project_id: str, collection_id: str) -> dict[str, Any]:
    projects_store.require(project_id)
    return {"deleted": papers_store.delete_collection(collection_id)}


@router.get("/{project_id}/papers")
def project_papers(
    project_id: str,
    collection_id: str = "",
    text: str = "",
    sort: str = "relevance",
    limit: int = Query(100, le=500),
    offset: int = 0,
) -> dict[str, Any]:
    """Papers linked to this project, with the library's filters available."""
    projects_store.require(project_id)
    return papers_store.search_library(
        text=text, project_id=project_id, collection_id=collection_id,
        sort=sort, limit=limit, offset=offset,
    )
