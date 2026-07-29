"""Local manuscript extraction contracts."""

from __future__ import annotations

import zipfile
from pathlib import Path

from papercreator.importers import extract_document


def test_markdown_extraction_removes_markup_but_keeps_academic_text(tmp_path: Path):
    source = tmp_path / "paper.md"
    source.write_text(
        "# Causal Representation Learning\n\n"
        "We study **causal graphs** with [auditable agents](https://example.test).\n",
        encoding="utf-8",
    )
    result = extract_document(source)
    assert result.title == "Causal Representation Learning"
    assert "auditable agents" in result.text
    assert "https://" not in result.text
    assert result.method == "markdown"


def test_docx_extraction_uses_ooxml_without_office(tmp_path: Path):
    source = tmp_path / "paper.docx"
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Reliable Paper Agents</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Evidence remains linked to every claim.</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    result = extract_document(source)
    assert result.title == "Reliable Paper Agents"
    assert "Evidence remains linked" in result.text
    assert result.method == "docx-ooxml"


def test_complex_academic_docx_preserves_tables_equations_and_notes(tmp_path: Path):
    source = tmp_path / "complex-academic.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
 <w:body>
  <w:p><w:r><w:t>Reliable Equation Extraction</w:t></w:r></w:p>
  <w:tbl>
   <w:tr>
    <w:tc><w:p><w:r><w:t>Variable</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>
   </w:tr>
   <w:tr>
    <w:tc><w:p><w:r><w:t>accuracy</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>0.95</w:t></w:r></w:p></w:tc>
   </w:tr>
  </w:tbl>
  <w:p>
   <w:r><w:t xml:space="preserve">Energy </w:t></w:r>
   <m:oMath>
    <m:r><m:t>E=m</m:t></m:r>
    <m:sSup><m:e><m:r><m:t>c</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>
   </m:oMath>
   <w:r><w:t xml:space="preserve"> and ratio </w:t></w:r>
   <m:oMath><m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath>
   <w:r><w:footnoteReference w:id="2"/></w:r>
  </w:p>
 </w:body>
</w:document>"""
    footnotes_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:footnote w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
 <w:footnote w:id="2"><w:p><w:r><w:t>Source data were independently audited.</w:t></w:r></w:p></w:footnote>
</w:footnotes>"""
    core_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
 xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
 <dc:title>Complex Academic Golden</dc:title>
 <dc:creator>Ada Researcher</dc:creator>
</cp:coreProperties>"""
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/footnotes.xml", footnotes_xml)
        archive.writestr("docProps/core.xml", core_xml)

    result = extract_document(source)

    assert result.text == (
        "Reliable Equation Extraction\n\n"
        "| Variable | Value |\n| accuracy | 0.95 |\n\n"
        "Energy $E=mc^{2}$ and ratio $(a)/(b)$[^fn2]\n\n"
        "Notes\n\n[^fn2]: Source data were independently audited."
    )
    assert result.title == "Complex Academic Golden"
    assert result.authors == ["Ada Researcher"]
    assert result.metadata["docx_structure"] == {
        "tables": 1,
        "equations": 2,
        "footnotes": 1,
        "endnotes": 0,
    }
    assert any("linear math" in warning for warning in result.warnings)


def test_image_only_pdf_reports_ocr_requirement(tmp_path: Path):
    from pypdf import PdfWriter

    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)
    result = extract_document(source)
    assert result.method == "pypdf"
    assert result.page_count == 1
    assert not result.text
    assert any("OCR" in warning for warning in result.warnings)


def test_image_only_pdf_can_use_bounded_local_ocr(tmp_path: Path, monkeypatch):
    from pypdf import PdfWriter
    from papercreator.importers import local_ocr

    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)

    calls = []

    def fake_ocr(path, page_indices, *, languages, dpi=200):
        calls.append((path, page_indices, languages, dpi))
        return {
            "texts": {0: "Scanned Abstract\nEvidence from an offline page."},
            "warnings": ["OCR could not process page 2: fixture."],
            "engine": "tesseract",
            "renderer": "fixture",
            "languages": languages,
            "dpi": dpi,
        }

    monkeypatch.setattr(local_ocr, "ocr_pdf_pages", fake_ocr)
    result = extract_document(
        source, use_ocr=True, ocr_languages="eng", ocr_max_pages=1
    )
    assert result.method == "pypdf+tesseract-ocr"
    assert "Scanned Abstract" in result.text
    assert calls == [(source, [0], "eng", 200)]
    assert result.metadata["ocr"]["candidate_pages"] == 2
    assert result.metadata["ocr"]["processed_pages"] == 1
    assert any("page limit" in warning.lower() for warning in result.warnings)
