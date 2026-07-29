from __future__ import annotations

import zipfile

from papercreator.importers import extract_document


def test_markdown_and_latex_are_extracted_locally(tmp_path):
    markdown = tmp_path / "paper.md"
    markdown.write_text(
        "# Reliable Agents\n\nA **reproducible** result with [evidence](https://example.test).",
        encoding="utf-8",
    )
    extracted = extract_document(markdown)
    assert extracted.title == "Reliable Agents"
    assert "reproducible result with evidence" in extracted.text
    assert extracted.method == "markdown"

    latex = tmp_path / "paper.tex"
    latex.write_text(
        r"\section{Method} We use \textbf{local extraction}. % private comment",
        encoding="utf-8",
    )
    extracted = extract_document(latex)
    assert "Method" in extracted.text
    assert "local extraction" in extracted.text
    assert "private comment" not in extracted.text


def test_docx_ooxml_fallback_contract(tmp_path, monkeypatch):
    # A tiny standards-compliant OOXML payload proves extraction does not upload
    # the manuscript and does not fundamentally depend on Word being installed.
    path = tmp_path / "owned-paper.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>My owned paper</w:t></w:r></w:p>
      <w:p><w:r><w:t>Methods and findings</w:t></w:r></w:p></w:body>
    </w:document>"""
    core_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
      xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Owned Research</dc:title></cp:coreProperties>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("docProps/core.xml", core_xml)

    # python-docx rejects this intentionally minimal package, exercising the
    # direct OOXML fallback.
    extracted = extract_document(path)
    assert extracted.title == "Owned Research"
    assert "My owned paper" in extracted.text
    assert "Methods and findings" in extracted.text
    assert extracted.method == "docx-ooxml"


def test_plain_text_accepts_utf8_bom(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("\ufeffAcademic title\n专业术语", encoding="utf-8")
    extracted = extract_document(path)
    assert extracted.title == "Academic title"
    assert "专业术语" in extracted.text
