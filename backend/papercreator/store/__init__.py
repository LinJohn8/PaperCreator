"""Persistence layer: one module per aggregate.

Every module here is a *repository* of plain functions over SQLite (plus, for
projects and documents, the on-disk mirror). No business logic, no HTTP, no LLM
calls - those live one layer up. Import direction is ``store -> core`` only.

* :mod:`papers` - library, collections, search history
* :mod:`projects` - project rows + workspace directories
* :mod:`documents` - manuscript documents/sections + disk mirror
* :mod:`analyses` - embedding cache, landscape snapshots
* :mod:`runs` - agent run/step records, LLM usage ledger
* :mod:`snapshots` - manuscript version snapshots
* :mod:`skills_store` - skill registry mirror
* :mod:`settings_store` - settings.json / secrets.json + app_state kv
"""

from . import (  # noqa: F401
    analyses,
    documents,
    papers,
    prompts,
    projects,
    runs,
    settings_store,
    skills_store,
    snapshots,
)

__all__ = [
    "analyses",
    "documents",
    "papers",
    "prompts",
    "projects",
    "runs",
    "settings_store",
    "skills_store",
    "snapshots",
]
