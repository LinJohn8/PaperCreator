"""Conversion subsystem: manuscript -> Markdown / LaTeX / DOCX / BibTeX / Overleaf.

Public surface::

    from papercreator.convert import exporters, overleaf, markdown_latex

    exporters.export_project(project_id, "latex")
    exporters.build_pdf(project_id)            # needs a local TeX engine
    exporters.describe_capabilities()          # what works on this machine
    overleaf.prepare_zip(project_id)           # any Overleaf account
    overleaf.push_to_overleaf(project_id)      # premium git bridge
    markdown_latex.markdown_to_latex(text)

No external binary is required: LaTeX generation and DOCX writing are both
implemented here. Pandoc and a TeX engine are used opportunistically when
present, for better DOCX fidelity and local PDF builds respectively.

See ``docs/systems/export_system.md``.
"""

from . import docx_min, exporters, latex_project, markdown_latex, overleaf  # noqa: F401

__all__ = [
    "docx_min",
    "exporters",
    "latex_project",
    "markdown_latex",
    "overleaf",
]
