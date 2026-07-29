"""HTTP route modules, one per subsystem.

============  ==========================  ==============================
prefix        module                       covers
============  ==========================  ==============================
/api/system   :mod:`system`               health, jobs, SSE, logs, usage
/api/settings :mod:`settings`             settings, provider config/tests
/api/projects :mod:`projects`             projects, collections, import
/api/search   :mod:`search`               retrieval, expansion, resolve
/api/library  :mod:`library`              papers, tags, import, dupes
/api/analysis :mod:`analysis`             landscape, placement, graph
/api/writing  :mod:`writing`              manuscript editing, bilingual
/api/agents   :mod:`agents`               runs, steps, prompt preview
/api/skills   :mod:`skills`               skills CRUD + LLM authoring
/api/export   :mod:`export`               md/tex/docx/bib/pdf/overleaf
/api/versions :mod:`versions`             git, snapshots, compare, restore
/api/workbench :mod:`workbench`           selected root, classified resources
============  ==========================  ==============================

Conventions every route follows:

* Long operations return ``{"job_id": ...}``; progress arrives on
  ``/api/system/events``. A ``/sync`` variant exists where a blocking call is
  genuinely more convenient (search, analysis).
* Errors are raised as :class:`~papercreator.core.errors.AppError` subclasses so
  the handler in ``api/app.py`` renders them as a stable
  ``{"error": {"code", "message", "details"}}`` shape.
* Destructive operations require an explicit flag (``remove_files``, ``confirm``,
  ``force``) and say in their response what was preserved.
"""

from . import (  # noqa: F401
    agents,
    analysis,
    assistant,
    export,
    library,
    projects,
    prompts,
    search,
    settings,
    skills,
    system,
    versions,
    workbench,
    writing,
)

__all__ = [
    "agents",
    "analysis",
    "assistant",
    "export",
    "library",
    "projects",
    "prompts",
    "search",
    "settings",
    "skills",
    "system",
    "versions",
    "workbench",
    "writing",
]
