"""PaperCreator backend.

Local-first multi-agent workbench for writing research papers: retrieval from
scholarly APIs, 3D landscape / gap analysis over the retrieved corpus,
LLM agent pipelines that draft the manuscript, and export to
Markdown / LaTeX / DOCX / Overleaf with git-backed versioning.

Layering (imports point downward only)::

    api  ->  agents / retrieval / analysis / writing / convert / skills / vcs
         ->  store  ->  core

See ``docs/architecture.md`` for the full picture.
"""

__version__ = "0.1.0"
