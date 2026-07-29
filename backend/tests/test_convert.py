"""Conversion tests: citations, Markdown/LaTeX, DOCX, exporters.

Citation handling gets the most attention: a wrong ``\\cite`` key or a mangled
BibTeX field produces a broken build or, worse, a silently wrong reference list -
which is exactly the failure a reviewer notices.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from papercreator.core.models import Author, Paper


@pytest.fixture()
def citable_papers():
    return [
        Paper(
            title="The Graph Neural Network Model",
            authors=[Author(name="Franco Scarselli"), Author(name="Marco Gori")],
            year=2009,
            venue="IEEE Transactions on Neural Networks",
            venue_type="journal",
            doi="10.1109/tnn.2008.2005605",
            abstract="A model for graphs & networks with 100% coverage.",
            raw={"crossref": {"volume": "20", "issue": "1", "page": "61-80",
                              "publisher": "IEEE"}},
        ).ensure_id(),
        Paper(
            title="BERT: Pre-training of Deep Bidirectional Transformers",
            authors=[Author(name="Jacob Devlin")],
            year=2019,
            venue="NAACL",
            venue_type="conference",
            arxiv_id="1810.04805",
        ).ensure_id(),
    ]


class TestCitationKeys:
    def test_keys_are_author_year(self, citable_papers):
        from papercreator.writing.citations import CitationKeyMap

        keys = CitationKeyMap.build(citable_papers)
        assert keys.key_for(citable_papers[0].id) == "SCARSELLI2009"
        assert keys.key_for(citable_papers[1].id) == "DEVLIN2019"

    def test_collisions_get_a_letter_suffix(self):
        from papercreator.writing.citations import CitationKeyMap

        papers = [
            Paper(title="A", authors=[Author(name="Jane Smith")], year=2020).ensure_id(),
            Paper(title="B", authors=[Author(name="John Smith")], year=2020).ensure_id(),
        ]
        keys = CitationKeyMap.build(papers)
        generated = [keys.key_for(paper.id) for paper in papers]
        assert generated == ["SMITH2020", "SMITH2020a"]

    def test_the_map_is_bidirectional(self, citable_papers):
        from papercreator.writing.citations import CitationKeyMap

        keys = CitationKeyMap.build(citable_papers)
        key = keys.key_for(citable_papers[0].id)
        assert keys.paper_for(key) == citable_papers[0].id


class TestBibtex:
    def test_special_characters_are_escaped(self, citable_papers):
        from papercreator.writing.citations import CitationKeyMap, build_bibtex

        bibtex = build_bibtex(citable_papers, CitationKeyMap.build(citable_papers))
        # An unescaped & or % breaks the LaTeX build outright.
        assert r"\&" in bibtex
        assert r"\%" in bibtex

    def test_acronyms_are_brace_protected(self):
        from papercreator.writing.citations import protect_capitals

        protected = protect_capitals("BERT and GraphSAGE for NLP")
        assert "{BERT}" in protected
        assert "{GraphSAGE}" in protected
        assert "{NLP}" in protected
        assert "{and}" not in protected, "ordinary words must not be protected"

    def test_entry_type_follows_the_venue(self, citable_papers):
        from papercreator.writing.citations import CitationKeyMap, build_bibtex

        bibtex = build_bibtex(citable_papers, CitationKeyMap.build(citable_papers))
        assert "@article{SCARSELLI2009" in bibtex
        assert "@inproceedings{DEVLIN2019" in bibtex
        assert "booktitle" in bibtex, "conference papers need booktitle, not journal"

    def test_author_names_are_reordered_for_bibtex(self, citable_papers):
        from papercreator.writing.citations import CitationKeyMap, build_bibtex

        bibtex = build_bibtex(citable_papers, CitationKeyMap.build(citable_papers))
        assert "Scarselli, Franco" in bibtex, "BibTeX needs Last, First"

    def test_arxiv_entries_carry_the_eprint(self, citable_papers):
        from papercreator.writing.citations import CitationKeyMap, build_bibtex

        bibtex = build_bibtex(citable_papers, CitationKeyMap.build(citable_papers))
        assert "eprint = {1810.04805}" in bibtex
        assert "archivePrefix = {arXiv}" in bibtex


class TestCitationRewriting:
    def test_markers_become_cite_commands(self, citable_papers):
        from papercreator.writing import citations

        keys = citations.CitationKeyMap.build(citable_papers)
        text, unknown = citations.to_latex_citations(
            "Graph networks [SCARSELLI2009] and transformers [DEVLIN2019] differ.", keys
        )
        assert r"\cite{scarselli2009}" in text
        assert not unknown

    def test_adjacent_markers_merge_into_one_command(self, citable_papers):
        """[A][B] must become \\cite{a,b}, not two commands."""
        from papercreator.writing import citations

        keys = citations.CitationKeyMap.build(citable_papers)
        text, _ = citations.to_latex_citations(
            "Both approaches [SCARSELLI2009][DEVLIN2019] apply.", keys
        )
        assert r"\cite{scarselli2009,devlin2019}" in text

    def test_unknown_markers_are_reported_not_dropped(self, citable_papers):
        """A fabricated citation must be visible, not silently deleted."""
        from papercreator.writing import citations

        keys = citations.CitationKeyMap.build(citable_papers)
        text, unknown = citations.to_latex_citations(
            "A claim [FABRICATED2024] with no source.", keys
        )
        assert unknown == ["FABRICATED2024"]
        assert "[FABRICATED2024]" in text, "left in place so the problem is visible"

    def test_numbering_follows_first_appearance(self, citable_papers):
        from papercreator.writing import citations

        keys = citations.CitationKeyMap.build(citable_papers)
        by_id = {paper.id: paper for paper in citable_papers}
        text, numbering, _ = citations.to_numbered_citations(
            "Second [DEVLIN2019] then first [SCARSELLI2009].", keys, by_id
        )
        assert numbering["DEVLIN2019"] == 1
        assert numbering["SCARSELLI2009"] == 2
        assert "[1]" in text and "[2]" in text

    def test_numbering_continues_across_sections(self, citable_papers):
        """One document must share one numbering sequence."""
        from papercreator.writing import citations

        keys = citations.CitationKeyMap.build(citable_papers)
        by_id = {paper.id: paper for paper in citable_papers}
        _, numbering, _ = citations.to_numbered_citations(
            "First [SCARSELLI2009].", keys, by_id
        )
        _, numbering, _ = citations.to_numbered_citations(
            "Second [DEVLIN2019].", keys, by_id, existing=numbering
        )
        assert sorted(numbering.values()) == [1, 2]

    def test_validation_separates_known_from_unknown(self, citable_papers):
        from papercreator.writing import citations

        keys = citations.CitationKeyMap.build(citable_papers)
        report = citations.validate_markers(
            "[SCARSELLI2009] and [MADEUP2020]", keys
        )
        assert report["valid"] == ["SCARSELLI2009"]
        assert report["unknown"] == ["MADEUP2020"]
        assert "DEVLIN2019" in report["unused"]

    def test_cited_papers_returns_only_what_is_cited(self, citable_papers):
        """A bibliography with 200 uncited entries is a visible defect."""
        from papercreator.writing import citations

        keys = citations.CitationKeyMap.build(citable_papers)
        by_id = {paper.id: paper for paper in citable_papers}
        cited = citations.cited_papers(["Only [DEVLIN2019] here."], keys, by_id)
        assert len(cited) == 1
        assert cited[0].arxiv_id == "1810.04805"


class TestMarkdownToLatex:
    def test_headings_and_emphasis(self):
        from papercreator.convert.markdown_latex import markdown_to_latex

        result = markdown_to_latex("# Title\n\nSome **bold** and *italic* text.")
        assert r"\section{Title}" in result
        assert r"\textbf{bold}" in result
        assert r"\emph{italic}" in result

    def test_math_is_never_escaped(self):
        """Escaping inside math is the classic naive-converter failure."""
        from papercreator.convert.markdown_latex import markdown_to_latex

        result = markdown_to_latex("Inline $x_i^2 \\alpha$ and:\n\n$$\n\\sum_{i=1}^n x_i\n$$")
        assert "$x_i^2 \\alpha$" in result
        assert r"\sum_{i=1}^n x_i" in result
        assert r"\_" not in result.split("$$")[1] if "$$" in result else True

    def test_code_blocks_are_verbatim(self):
        from papercreator.convert.markdown_latex import markdown_to_latex

        result = markdown_to_latex("```python\nx = a_b % c\n```")
        assert r"\begin{verbatim}" in result
        assert "x = a_b % c" in result, "code must not be escaped"

    def test_special_characters_in_prose_are_escaped(self):
        from papercreator.convert.markdown_latex import markdown_to_latex

        result = markdown_to_latex("100% of A&B costs $5 with under_score.")
        assert r"\%" in result and r"\&" in result and r"\_" in result

    def test_lists_become_environments(self):
        from papercreator.convert.markdown_latex import markdown_to_latex

        result = markdown_to_latex("- one\n- two\n\n1. first\n2. second")
        assert r"\begin{itemize}" in result and r"\end{itemize}" in result
        assert r"\begin{enumerate}" in result

    def test_tables_become_tabular(self):
        from papercreator.convert.markdown_latex import markdown_to_latex

        result = markdown_to_latex(
            "| Method | Accuracy |\n|---|---:|\n| GNN | 0.94 |\n| MLP | 0.88 |"
        )
        assert r"\begin{tabular}" in result
        assert "GNN" in result and "0.94" in result

    def test_unicode_safe_mode_preserves_cjk(self):
        """pdflatex cannot typeset CJK; xelatex needs it left alone."""
        from papercreator.convert.markdown_latex import markdown_to_latex

        result = markdown_to_latex("中文摘要内容", unicode_safe=True)
        assert "中文摘要内容" in result

    def test_non_unicode_mode_maps_symbols(self):
        from papercreator.convert.markdown_latex import markdown_to_latex

        result = markdown_to_latex("Values α ≤ β — see", unicode_safe=False)
        assert r"$\alpha$" in result
        assert r"$\leq$" in result
        assert "---" in result


class TestLatexToMarkdown:
    def test_round_trip_preserves_structure(self):
        from papercreator.convert.markdown_latex import latex_to_markdown, markdown_to_latex

        original = "# Introduction\n\nSome **bold** text with [KEY] citation.\n"
        back = latex_to_markdown(markdown_to_latex(original))
        assert "# Introduction" in back
        assert "**bold**" in back

    def test_cite_commands_become_markers(self):
        from papercreator.convert.markdown_latex import latex_to_markdown

        result = latex_to_markdown(r"As shown \cite{smith2020,jones2021}.")
        assert "[SMITH2020]" in result and "[JONES2021]" in result

    def test_preamble_is_dropped(self):
        from papercreator.convert.markdown_latex import latex_to_markdown

        result = latex_to_markdown(
            "\\documentclass{article}\n\\usepackage{x}\n"
            "\\begin{document}\n\\section{Body}\nText.\n\\end{document}"
        )
        assert "documentclass" not in result
        assert "# Body" in result


class TestDocxWriter:
    def test_produces_a_valid_zip_with_the_required_parts(self, tmp_path):
        """Word rejects the whole file if a required part is missing."""
        from papercreator.convert import docx_min

        target = tmp_path / "test.docx"
        docx_min.write_docx(
            [
                docx_min.Block(kind="title", text="A Paper Title"),
                docx_min.Block(kind="heading", text="Introduction", level=1),
                docx_min.Block(kind="paragraph", text="Body with **bold** text."),
                docx_min.Block(kind="bullet", text="a point"),
                docx_min.Block(kind="table", rows=[["Method", "Score"], ["GNN", "0.9"]]),
            ],
            target,
            title="A Paper Title",
        )
        assert target.is_file() and target.stat().st_size > 1000
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            for required in (
                "[Content_Types].xml", "_rels/.rels", "word/document.xml",
                "word/styles.xml", "word/numbering.xml",
                "word/_rels/document.xml.rels", "docProps/core.xml",
            ):
                assert required in names, f"{required} missing"
            document = archive.read("word/document.xml").decode("utf-8")
            assert "A Paper Title" in document
            assert "<w:tbl>" in document, "table not written"
            assert document.count("<w:b/>") >= 1, "bold run not written"

    def test_xml_special_characters_are_escaped(self, tmp_path):
        from papercreator.convert import docx_min

        target = tmp_path / "escape.docx"
        docx_min.write_docx(
            [docx_min.Block(kind="paragraph", text='A & B < C > D "quoted"')],
            target,
        )
        with zipfile.ZipFile(target) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
        assert "&amp;" in document and "&lt;" in document
        # A raw ampersand would make Word refuse to open the file.
        assert " & " not in document

    def test_markdown_parses_into_blocks(self):
        from papercreator.convert.docx_min import markdown_to_blocks

        blocks = markdown_to_blocks(
            "# Heading\n\nA paragraph that\nwraps across lines.\n\n"
            "- item one\n- item two\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n"
        )
        kinds = [block.kind for block in blocks]
        assert "heading" in kinds and "paragraph" in kinds
        assert kinds.count("bullet") == 2
        assert "table" in kinds
        # Markdown joins single newlines into one paragraph.
        paragraph = next(b for b in blocks if b.kind == "paragraph")
        assert "wraps across lines" in paragraph.text


class TestLatexProject:
    def test_writes_a_compilable_structure(self, project, citable_papers, tmp_path):
        from papercreator.convert import latex_project
        from papercreator.writing.citations import CitationKeyMap

        blocks = [
            {"key": "abstract", "title": "Abstract", "level": 1,
             "text": "We study graphs [SCARSELLI2009].", "words": 5},
            {"key": "introduction", "title": "Introduction", "level": 1,
             "text": "Transformers [DEVLIN2019] changed the field.", "words": 6},
        ]
        report = latex_project.export_latex_project(
            project=project,
            blocks=blocks,
            papers=citable_papers,
            keys=CitationKeyMap.build(citable_papers),
            target_dir=tmp_path / "latex",
        )
        root = Path(report["path"])
        assert (root / "main.tex").is_file()
        assert (root / "references.bib").is_file()
        assert (root / "sections" / "introduction.tex").is_file()
        assert (root / "build.sh").is_file() and (root / "build.bat").is_file()

        main = (root / "main.tex").read_text(encoding="utf-8")
        assert r"\begin{document}" in main and r"\end{document}" in main
        # The abstract belongs in the abstract environment, not as a section.
        assert r"\begin{abstract}" in main
        assert r"\input{sections/introduction}" in main
        assert "sections/abstract" not in main
        assert r"\bibliography{references}" in main
        assert r"\cite{scarselli2009}" in main
        introduction = (root / "sections" / "introduction.tex").read_text(
            encoding="utf-8"
        )
        assert r"\cite{devlin2019}" in introduction
        assert r"\textbackslash{}cite" not in main + introduction

    def test_chinese_forces_xelatex(self, project, citable_papers, tmp_path):
        """pdflatex cannot typeset CJK; silently producing an unbuildable project
        would be worse than overriding the choice."""
        from papercreator.convert import latex_project
        from papercreator.writing.citations import CitationKeyMap

        report = latex_project.export_latex_project(
            project=project,
            blocks=[{"key": "intro", "title": "引言", "level": 1,
                     "text": "本文研究图神经网络在分子性质预测中的应用。", "words": 10}],
            papers=citable_papers,
            keys=CitationKeyMap.build(citable_papers),
            target_dir=tmp_path / "cjk",
            engine="pdflatex",  # deliberately wrong
        )
        assert report["engine"] == "xelatex"
        main = (Path(report["path"]) / "main.tex").read_text(encoding="utf-8")
        assert "ctex" in main
        assert "inputenc" not in main, "conflicts with xelatex"
        assert any("xelatex" in warning for warning in report["warnings"])

    def test_unmatched_markers_are_reported(self, project, tmp_path):
        from papercreator.convert import latex_project
        from papercreator.writing.citations import CitationKeyMap

        report = latex_project.export_latex_project(
            project=project,
            blocks=[{"key": "intro", "title": "Intro", "level": 1,
                     "text": "A claim [NOTREAL2024].", "words": 3}],
            papers=[],
            keys=CitationKeyMap.build([]),
            target_dir=tmp_path / "unmatched",
        )
        assert any("citation marker" in warning for warning in report["warnings"])


class TestExporters:
    def test_capabilities_are_reported_honestly(self, temp_home):
        from papercreator.convert import exporters

        capabilities = exporters.describe_capabilities()
        assert isinstance(capabilities["pandoc"], bool)
        assert set(capabilities["latex_engines"]) >= {"pdflatex", "xelatex"}
        # Every format must work without an external binary.
        assert all(entry["always_available"] for entry in capabilities["formats"])

    def test_markdown_export_includes_a_reference_list(self, project, citable_papers):
        from papercreator.convert import exporters
        from papercreator.store import documents as documents_store
        from papercreator.store import papers as papers_store

        stored, _, _ = papers_store.upsert_many(citable_papers)
        collection = papers_store.ensure_collection(project.id)
        papers_store.add_to_collection(collection["id"], [p.id for p in stored])

        document = documents_store.primary_document(project.id)
        documents_store.create_section(
            document.id, key="introduction", title="Introduction",
            content="Graph networks [SCARSELLI2009] matter.",
        )
        result = exporters.export_markdown(project.id)
        content = Path(result["path"]).read_text(encoding="utf-8")
        assert "## References" in content
        assert "[1]" in content, "markers must be numbered"
        assert result["references"] == 1

    def test_docx_export_writes_an_openable_file(self, project, citable_papers):
        from papercreator.convert import exporters
        from papercreator.store import documents as documents_store

        document = documents_store.primary_document(project.id)
        documents_store.create_section(
            document.id, key="method", title="Method", content="The approach."
        )
        result = exporters.export_docx(project.id, use_pandoc=False)
        target = Path(result["path"])
        assert target.is_file()
        with zipfile.ZipFile(target) as archive:
            assert "word/document.xml" in archive.namelist()
        assert result["writer"] == "builtin"
        assert result["warnings"], "the built-in writer's limits must be stated"

    def test_export_of_an_empty_manuscript_is_refused_clearly(self, project):
        from papercreator.convert import exporters
        from papercreator.core.errors import ValidationError

        with pytest.raises(ValidationError, match="nothing to export"):
            exporters.export_latex(project.id)


class TestOverleafImportSafety:
    def test_apply_stops_before_database_write_when_disk_changed(
        self, project, monkeypatch
    ):
        import subprocess

        from papercreator.convert import overleaf
        from papercreator.core.errors import ConflictError
        from papercreator.store import documents as documents_store
        from papercreator.writing import manuscript

        manuscript.apply_template(project.id, "generic")
        document = documents_store.primary_document(project.id)
        section = documents_store.get_section_by_key(document.id, "introduction")
        assert section is not None
        section_file = (
            documents_store.document_dir(document)
            / documents_store.section_filename(section, document.format)
        )
        section_file.write_text(
            "# Introduction\n\nexternal editor text\n", encoding="utf-8"
        )

        monkeypatch.setattr(overleaf, "_credentials", lambda: ("https://x", "token"))

        def fake_git(args, cwd, *, timeout=180.0):
            destination = Path(args[-1])
            (destination / "sections").mkdir(parents=True, exist_ok=True)
            (destination / "sections" / "introduction.tex").write_text(
                "\\section{Introduction}\nOverleaf replacement text.\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, b"", b"")

        monkeypatch.setattr(overleaf, "_run_git", fake_git)
        with pytest.raises(ConflictError) as caught:
            overleaf.pull_from_overleaf(project.id, apply_to_manuscript=True)
        assert caught.value.code == "manuscript_sync_conflict"
        assert documents_store.require_section(section.id).content == ""
        assert "external editor text" in section_file.read_text(encoding="utf-8")
