"""Writing subsystem: manuscript structure, bilingual pairing, citations.

Sits between the store (rows and files) and the converters (output formats).

Public surface::

    from papercreator.writing import manuscript, citations, templates

    manuscript.apply_template(project_id, "survey")
    manuscript.bilingual_status(project_id)   # is the zh/en pair still aligned?
    manuscript.swap_languages(project_id)
    manuscript.assemble(project_id)           # text + keys + cited papers
    manuscript.regenerate_bibliography(project_id)
    citations.CitationKeyMap.build(papers)
    citations.to_latex_citations(text, keys)
    templates.list_templates()

See ``docs/systems/writing_system.md``.
"""

from . import citations, manuscript, templates  # noqa: F401

__all__ = ["citations", "manuscript", "templates"]
