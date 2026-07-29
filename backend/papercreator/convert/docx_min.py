"""Minimal DOCX writer with no third-party dependency.

A ``.docx`` is a ZIP of Office Open XML parts. Writing the handful of parts Word
needs is around 200 lines, which is worth it because the alternative is making
``python-docx`` a hard requirement for a feature the user explicitly asked for
("get it into Word"). When ``python-docx`` *is* installed,
:mod:`convert.exporters` prefers it - it handles more cases - but this module
guarantees the feature always works.

Parts written::

    [Content_Types].xml     part type declarations
    _rels/.rels             package -> document relationship
    word/document.xml       the content
    word/styles.xml         Title/Heading1-3/Normal/Quote definitions
    word/_rels/document.xml.rels
    docProps/core.xml       title, author, timestamps

Supported content: title, headings (3 levels), paragraphs with bold/italic/code
runs, bullet and numbered lists, simple tables, page breaks. That covers a
manuscript; it is not a general Word writer.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ..core.logging_setup import get_logger

log = get_logger(__name__)

BlockKind = Literal["title", "heading", "paragraph", "bullet", "numbered",
                    "table", "pagebreak", "quote"]


@dataclass
class Block:
    kind: BlockKind
    text: str = ""
    level: int = 1
    rows: list[list[str]] = field(default_factory=list)


_XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}


def esc(text: str) -> str:
    """Escape text for XML character data.

    Control characters other than tab/newline are stripped: Word rejects the
    whole document if any appear, and provider-sourced text occasionally contains
    them.
    """
    out = []
    for char in text or "":
        if char in _XML_ESCAPES:
            out.append(_XML_ESCAPES[char])
        elif ord(char) < 0x20 and char not in "\t\n":
            continue
        else:
            out.append(char)
    return "".join(out)


_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*]+)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")


def _runs(text: str) -> str:
    """Convert inline Markdown emphasis into a sequence of ``<w:r>`` runs.

    Tokenised in one pass so nested and adjacent markers cannot produce
    overlapping runs (which Word renders unpredictably).
    """
    tokens: list[tuple[str, str]] = []
    position = 0
    pattern = re.compile(
        r"\*\*(?P<bold>[^*]+)\*\*"
        r"|(?<![*\w])\*(?P<italic>[^*]+)\*(?!\*)"
        r"|`(?P<code>[^`]+)`"
    )
    for match in pattern.finditer(text or ""):
        if match.start() > position:
            tokens.append(("plain", text[position:match.start()]))
        if match.group("bold"):
            tokens.append(("bold", match.group("bold")))
        elif match.group("italic"):
            tokens.append(("italic", match.group("italic")))
        else:
            tokens.append(("code", match.group("code")))
        position = match.end()
    if position < len(text or ""):
        tokens.append(("plain", text[position:]))
    if not tokens:
        tokens = [("plain", text or "")]

    parts: list[str] = []
    for style, content in tokens:
        properties = ""
        if style == "bold":
            properties = "<w:rPr><w:b/></w:rPr>"
        elif style == "italic":
            properties = "<w:rPr><w:i/></w:rPr>"
        elif style == "code":
            properties = '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/></w:rPr>'
        # xml:space="preserve" keeps the spaces around emphasis runs.
        parts.append(
            f'<w:r>{properties}<w:t xml:space="preserve">{esc(content)}</w:t></w:r>'
        )
    return "".join(parts)


def _paragraph(text: str, *, style: str = "", numbering: int = 0) -> str:
    properties: list[str] = []
    if style:
        properties.append(f'<w:pStyle w:val="{style}"/>')
    if numbering:
        properties.append(
            f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{numbering}"/></w:numPr>'
        )
    prefix = f"<w:pPr>{''.join(properties)}</w:pPr>" if properties else ""
    return f"<w:p>{prefix}{_runs(text)}</w:p>"


def _table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    # Total width 9360 twips ~ A4 minus margins.
    column_width = max(500, 9360 // max(1, width))
    grid = "".join(f'<w:gridCol w:w="{column_width}"/>' for _ in range(width))
    body: list[str] = []
    for index, row in enumerate(rows):
        cells: list[str] = []
        padded = list(row) + [""] * (width - len(row))
        for cell in padded:
            content = f"**{cell}**" if index == 0 else cell
            cells.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{column_width}" w:type="dxa"/>'
                f"</w:tcPr>{_paragraph(content)}</w:tc>"
            )
        body.append(f"<w:tr>{''.join(cells)}</w:tr>")
    return (
        "<w:tbl><w:tblPr>"
        '<w:tblStyle w:val="TableGrid"/>'
        '<w:tblW w:w="0" w:type="auto"/>'
        '<w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:color="auto"/>'
        '<w:left w:val="single" w:sz="4" w:color="auto"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="auto"/>'
        '<w:right w:val="single" w:sz="4" w:color="auto"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="auto"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="auto"/>'
        "</w:tblBorders></w:tblPr>"
        f"<w:tblGrid>{grid}</w:tblGrid>{''.join(body)}</w:tbl>"
        # A table must be followed by a paragraph or Word reports corruption.
        "<w:p/>"
    )


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>"""


def _styles_xml(*, body_font: str = "Times New Roman", body_size_pt: int = 11) -> str:
    """Style definitions. Sizes are in half-points, as OOXML requires."""
    half = body_size_pt * 2
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="{body_font}" w:hAnsi="{body_font}" w:eastAsia="SimSun"/>
<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>
</w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/>
</w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal">
<w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title">
<w:name w:val="Title"/><w:basedOn w:val="Normal"/>
<w:pPr><w:jc w:val="center"/><w:spacing w:before="240" w:after="240"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1">
<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:pPr><w:outlineLvl w:val="0"/><w:spacing w:before="280" w:after="140"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2">
<w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
<w:pPr><w:outlineLvl w:val="1"/><w:spacing w:before="240" w:after="120"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3">
<w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>
<w:pPr><w:outlineLvl w:val="2"/><w:spacing w:before="200" w:after="100"/></w:pPr>
<w:rPr><w:b/><w:i/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote">
<w:name w:val="Quote"/><w:basedOn w:val="Normal"/>
<w:pPr><w:ind w:left="720"/></w:pPr><w:rPr><w:i/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph">
<w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>
<w:pPr><w:ind w:left="720"/><w:spacing w:after="60"/></w:pPr></w:style>
<w:style w:type="table" w:styleId="TableGrid">
<w:name w:val="Table Grid"/></w:style>
</w:styles>"""


# Two numbering definitions: numId 1 = bullets, numId 2 = decimal.
_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>
<w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr></w:lvl>
</w:abstractNum>
<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>
<w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>"""


def _core_xml(title: str, author: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
 xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>{esc(title)}</dc:title>
<dc:creator>{esc(author or "PaperCreator")}</dc:creator>
<cp:lastModifiedBy>PaperCreator</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>"""


def write_docx(
    blocks: list[Block],
    target: Path,
    *,
    title: str = "",
    author: str = "",
    body_font: str = "Times New Roman",
    body_size_pt: int = 11,
) -> Path:
    """Write the blocks to a ``.docx`` file."""
    body: list[str] = []
    for block in blocks:
        if block.kind == "title":
            body.append(_paragraph(block.text, style="Title"))
        elif block.kind == "heading":
            level = max(1, min(3, block.level))
            body.append(_paragraph(block.text, style=f"Heading{level}"))
        elif block.kind == "bullet":
            body.append(_paragraph(block.text, style="ListParagraph", numbering=1))
        elif block.kind == "numbered":
            body.append(_paragraph(block.text, style="ListParagraph", numbering=2))
        elif block.kind == "quote":
            body.append(_paragraph(block.text, style="Quote"))
        elif block.kind == "table":
            body.append(_table(block.rows))
        elif block.kind == "pagebreak":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        else:
            body.append(_paragraph(block.text))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
        '2006/main">\n<w:body>\n'
        + "\n".join(body)
        # sectPr defines the page: A4 portrait with 1-inch margins.
        + '\n<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
          '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"'
          ' w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>\n'
          "</w:body>\n</w:document>"
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr(
            "word/styles.xml",
            _styles_xml(body_font=body_font, body_size_pt=body_size_pt),
        )
        archive.writestr("word/numbering.xml", _NUMBERING)
        archive.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        archive.writestr("docProps/core.xml", _core_xml(title, author))
    log.info("wrote %s blocks to %s", len(blocks), target)
    return target


# ---------------------------------------------------------- markdown -> blocks

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_MD_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_MD_QUOTE = re.compile(r"^>\s?(.*)$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


def markdown_to_blocks(text: str) -> list[Block]:
    """Parse a Markdown body into DOCX blocks.

    Paragraphs are joined across single newlines (Markdown semantics) so a
    hard-wrapped paragraph does not become several Word paragraphs.
    """
    blocks: list[Block] = []
    lines = (text or "").split("\n")
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            blocks.append(Block(kind="paragraph", text=" ".join(paragraph).strip()))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            flush()
            index += 1
            continue

        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            blocks.append(Block(
                kind="heading", text=heading.group(2).strip(),
                level=len(heading.group(1)),
            ))
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and _MD_TABLE_SEP.match(
            lines[index + 1] or ""
        ):
            flush()
            rows: list[list[str]] = [_split_md_row(line)]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_split_md_row(lines[index]))
                index += 1
            blocks.append(Block(kind="table", rows=rows))
            continue

        if stripped.startswith("```"):
            flush()
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            for code_line in code:
                blocks.append(Block(kind="paragraph", text=f"`{code_line}`"))
            continue

        bullet = _MD_BULLET.match(line)
        if bullet:
            flush()
            blocks.append(Block(kind="bullet", text=bullet.group(1).strip()))
            index += 1
            continue

        numbered = _MD_NUMBERED.match(line)
        if numbered:
            flush()
            blocks.append(Block(kind="numbered", text=numbered.group(1).strip()))
            index += 1
            continue

        quote = _MD_QUOTE.match(line)
        if quote:
            flush()
            blocks.append(Block(kind="quote", text=quote.group(1).strip()))
            index += 1
            continue

        paragraph.append(stripped)
        index += 1

    flush()
    return blocks


def _split_md_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]
