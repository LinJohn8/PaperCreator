"""Markdown <-> LaTeX conversion.

Written rather than delegated to Pandoc because the app must work without any
external binary, and because the conversion needed here is narrow and known: the
manuscript is agent-generated academic prose with citation markers, emphasis,
lists, math and tables. A general-purpose converter would be heavier and would
still need the citation handling that only this codebase knows about.

When Pandoc *is* available, :mod:`convert.exporters` prefers it for DOCX, where
the fidelity difference is large. For LaTeX the direct path is used, because the
citation rewriting has to be integrated with the key map.

Round-trip is not lossless and does not claim to be: LaTeX is far larger than the
Markdown subset. What is guaranteed is that markdown -> latex -> markdown
preserves paragraph structure, headings, emphasis, lists, and citation markers.
"""

from __future__ import annotations

import re
from typing import Any

from ..core.logging_setup import get_logger

log = get_logger(__name__)

# Characters LaTeX treats specially in text mode. Backslash must be handled
# first, otherwise the replacements for other characters get re-escaped.
_LATEX_SPECIAL = [
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]

# Unicode that pdflatex cannot typeset without extra packages. xelatex handles
# these natively, so the mapping is only applied for pdflatex.
_UNICODE_TO_LATEX = {
    "–": "--", "—": "---", "‘": "`", "’": "'", "“": "``", "”": "''",
    "…": r"\ldots{}", "×": r"$\times$", "≤": r"$\leq$", "≥": r"$\geq$",
    "≈": r"$\approx$", "≠": r"$\neq$", "±": r"$\pm$", "°": r"$^\circ$",
    "α": r"$\alpha$", "β": r"$\beta$", "γ": r"$\gamma$", "δ": r"$\delta$",
    "ε": r"$\epsilon$", "θ": r"$\theta$", "λ": r"$\lambda$", "μ": r"$\mu$",
    "π": r"$\pi$", "σ": r"$\sigma$", "τ": r"$\tau$", "φ": r"$\phi$",
    "ω": r"$\omega$", "Δ": r"$\Delta$", "Σ": r"$\Sigma$", "Ω": r"$\Omega$",
    "→": r"$\rightarrow$", "←": r"$\leftarrow$", "↔": r"$\leftrightarrow$",
    "∈": r"$\in$", "∀": r"$\forall$", "∃": r"$\exists$", "∞": r"$\infty$",
    "√": r"$\sqrt{}$", "∑": r"$\sum$", "∏": r"$\prod$", "∫": r"$\int$",
    "•": r"$\bullet$", "™": r"\texttrademark{}", "©": r"\copyright{}",
}

_INLINE_MATH = re.compile(r"\$([^$\n]+)\$")
_BLOCK_MATH = re.compile(r"^\$\$\s*$(.*?)^\$\$\s*$", re.MULTILINE | re.DOTALL)
_CODE_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_UL_ITEM = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_ITEM = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")

_HEADING_COMMANDS = {
    1: "section", 2: "subsection", 3: "subsubsection",
    4: "paragraph", 5: "subparagraph", 6: "subparagraph",
}


def escape_latex(text: str, *, unicode_safe: bool = False) -> str:
    """Escape plain text for LaTeX.

    ``unicode_safe=True`` means the target engine is xelatex/lualatex, which can
    typeset Unicode directly - important for a Chinese manuscript, where mapping
    CJK to LaTeX commands is not possible anyway.
    """
    out = text
    for char, replacement in _LATEX_SPECIAL:
        out = out.replace(char, replacement)
    if not unicode_safe:
        for char, replacement in _UNICODE_TO_LATEX.items():
            out = out.replace(char, replacement)
    return out


def markdown_to_latex(
    text: str,
    *,
    unicode_safe: bool = False,
    heading_offset: int = 0,
) -> str:
    """Convert a Markdown body to LaTeX.

    Protected regions (code, math) are extracted first and restored last, so the
    escaper never mangles a formula or a code sample - that is the single most
    common failure of naive converters.
    """
    protected: list[str] = []

    def stash(payload: str) -> str:
        protected.append(payload)
        return f"\x00PROT{len(protected) - 1}\x00"

    work = text or ""

    # 1. Protect block math, fenced code, inline math and inline code.
    work = _BLOCK_MATH.sub(
        lambda m: stash(f"\\begin{{equation}}\n{m.group(1).strip()}\n\\end{{equation}}"),
        work,
    )
    work = _CODE_FENCE.sub(
        lambda m: stash(
            "\\begin{verbatim}\n" + m.group(1).rstrip() + "\n\\end{verbatim}"
        ),
        work,
    )
    work = _INLINE_MATH.sub(lambda m: stash(f"${m.group(1)}$"), work)
    work = _INLINE_CODE.sub(
        lambda m: stash("\\texttt{" + escape_latex(m.group(1), unicode_safe=True) + "}"),
        work,
    )

    # 2. Images and links before escaping, since URLs contain ~ _ % # .
    work = _IMAGE.sub(
        lambda m: stash(
            "\\begin{figure}[htbp]\n  \\centering\n"
            f"  \\includegraphics[width=0.9\\linewidth]{{{m.group(2)}}}\n"
            f"  \\caption{{{escape_latex(m.group(1), unicode_safe=unicode_safe)}}}\n"
            "\\end{figure}"
        ),
        work,
    )
    work = _LINK.sub(
        lambda m: stash(
            f"\\href{{{m.group(2)}}}{{"
            f"{escape_latex(m.group(1), unicode_safe=unicode_safe)}}}"
        ),
        work,
    )

    # 3. Tables. Converted before the escaping pass, because pipes must still be
    #    structural - but the LaTeX it generates has to be stashed, or the escaper
    #    would turn every backslash in \begin{tabular} into \textbackslash{}.
    work = _convert_tables(work, unicode_safe=unicode_safe, stash=stash)

    # 4. Line-structure conversion: headings and lists.
    lines = work.split("\n")
    output: list[str] = []
    list_stack: list[str] = []

    for raw_line in lines:
        heading = _HEADING.match(raw_line)
        if heading:
            output.extend(_close_lists(list_stack))
            level = min(6, len(heading.group(1)) + heading_offset)
            command = _HEADING_COMMANDS.get(max(1, level), "paragraph")
            title = escape_latex(heading.group(2).strip(), unicode_safe=unicode_safe)
            output.append(f"\\{command}{{{title}}}")
            continue

        ul = _UL_ITEM.match(raw_line)
        ol = _OL_ITEM.match(raw_line)
        if ul or ol:
            wanted = "itemize" if ul else "enumerate"
            if not list_stack:
                output.append(f"\\begin{{{wanted}}}")
                list_stack.append(wanted)
            elif list_stack[-1] != wanted:
                output.append(f"\\end{{{list_stack.pop()}}}")
                output.append(f"\\begin{{{wanted}}}")
                list_stack.append(wanted)
            content = (ul.group(2) if ul else ol.group(3)).strip()
            output.append(
                f"  \\item {escape_latex(content, unicode_safe=unicode_safe)}"
            )
            continue

        if not raw_line.strip():
            output.extend(_close_lists(list_stack))
            output.append("")
            continue

        output.extend(_close_lists(list_stack))
        output.append(escape_latex(raw_line, unicode_safe=unicode_safe))

    output.extend(_close_lists(list_stack))
    result = "\n".join(output)

    # 5. Emphasis, after escaping (the markers themselves are not special in
    #    LaTeX, and doing it earlier would let the escaper break them).
    result = _BOLD.sub(r"\\textbf{\1}", result)
    result = _ITALIC.sub(r"\\emph{\1}", result)

    # 6. Restore protected regions.
    for index, payload in enumerate(protected):
        result = result.replace(f"\x00PROT{index}\x00", payload)
    return re.sub(r"\n{3,}", "\n\n", result).strip() + "\n"


def _close_lists(stack: list[str]) -> list[str]:
    closers = [f"\\end{{{env}}}" for env in reversed(stack)]
    stack.clear()
    return closers


def _convert_tables(
    text: str, *, unicode_safe: bool, stash: Any
) -> str:
    """Convert GitHub-flavoured Markdown tables to ``tabular``.

    Cell contents are escaped here (they are prose), but the surrounding LaTeX is
    handed to ``stash`` so the document-wide escaping pass leaves it alone.
    """
    lines = text.split("\n")
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        is_row = line.strip().startswith("|") and line.count("|") >= 2
        has_separator = (
            index + 1 < len(lines) and _TABLE_SEP.match(lines[index + 1] or "")
        )
        if not (is_row and has_separator):
            output.append(line)
            index += 1
            continue

        header = _split_row(lines[index])
        alignment_row = _split_row(lines[index + 1])
        index += 2
        body: list[list[str]] = []
        while index < len(lines) and lines[index].strip().startswith("|"):
            body.append(_split_row(lines[index]))
            index += 1

        spec = "".join(_alignment_char(cell) for cell in alignment_row) or "l" * len(header)
        table: list[str] = [
            "\\begin{table}[htbp]",
            "  \\centering",
            f"  \\begin{{tabular}}{{{spec}}}",
            "    \\hline",
            "    "
            + " & ".join(
                f"\\textbf{{{escape_latex(cell, unicode_safe=unicode_safe)}}}"
                for cell in header
            )
            + " \\\\",
            "    \\hline",
        ]
        for row in body:
            padded = row + [""] * (len(header) - len(row))
            table.append(
                "    "
                + " & ".join(
                    escape_latex(cell, unicode_safe=unicode_safe)
                    for cell in padded[: len(header)]
                )
                + " \\\\"
            )
        table.extend([
            "    \\hline",
            "  \\end{tabular}",
            "  \\caption{}",
            "\\end{table}",
        ])
        output.append(stash("\n".join(table)))
    return "\n".join(output)


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _alignment_char(cell: str) -> str:
    text = cell.strip()
    if text.startswith(":") and text.endswith(":"):
        return "c"
    if text.endswith(":"):
        return "r"
    return "l"


# --------------------------------------------------------------- latex -> md

_TEX_HEADING = re.compile(
    r"\\(section|subsection|subsubsection|paragraph|subparagraph)\*?\{([^}]*)\}"
)
_TEX_TEXTBF = re.compile(r"\\textbf\{([^}]*)\}")
_TEX_EMPH = re.compile(r"\\(?:emph|textit)\{([^}]*)\}")
_TEX_TEXTTT = re.compile(r"\\texttt\{([^}]*)\}")
_TEX_HREF = re.compile(r"\\href\{([^}]*)\}\{([^}]*)\}")
_TEX_CITE = re.compile(r"\\cite[tp]?\{([^}]*)\}")
_TEX_COMMENT = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
_TEX_ENV = re.compile(r"\\(?:begin|end)\{(itemize|enumerate)\}")
_TEX_ITEM = re.compile(r"^\s*\\item\s+", re.MULTILINE)

_LATEX_UNESCAPE = [
    (r"\textbackslash{}", "\\"), (r"\&", "&"), (r"\%", "%"), (r"\$", "$"),
    (r"\#", "#"), (r"\_", "_"), (r"\{", "{"), (r"\}", "}"),
    (r"\textasciitilde{}", "~"), (r"\textasciicircum{}", "^"),
    (r"\ldots{}", "…"), ("---", "—"), ("--", "–"), ("``", "\u201c"),
    ("''", "\u201d"),
]


def latex_to_markdown(text: str, *, keep_citations: bool = True) -> str:
    """Convert a LaTeX body back to Markdown.

    Used when the user pastes LaTeX into the editor or pulls a manuscript back
    from Overleaf. Preamble, bibliography commands and unknown environments are
    dropped, which is the right behaviour for editing prose - a warning is logged
    so the loss is visible.
    """
    work = text or ""

    # Strip the preamble and document wrapper if a full document was pasted.
    body_match = re.search(
        r"\\begin\{document\}(.*?)\\end\{document\}", work, re.DOTALL
    )
    if body_match:
        work = body_match.group(1)
        log.info("latex_to_markdown: dropped preamble and document wrapper")

    work = _TEX_COMMENT.sub("", work)
    for command in (
        "maketitle", "tableofcontents", "clearpage", "newpage", "bibliographystyle",
        "bibliography", "printbibliography", "appendix", "centering", "hline",
        "toprule", "midrule", "bottomrule", "noindent",
    ):
        work = re.sub(rf"\\{command}(\{{[^}}]*\}})?", "", work)

    work = re.sub(
        r"\\begin\{abstract\}(.*?)\\end\{abstract\}",
        lambda m: f"# Abstract\n\n{m.group(1).strip()}\n",
        work, flags=re.DOTALL,
    )
    work = re.sub(
        r"\\begin\{verbatim\}(.*?)\\end\{verbatim\}",
        lambda m: f"```\n{m.group(1).strip()}\n```",
        work, flags=re.DOTALL,
    )
    work = re.sub(
        r"\\begin\{(?:equation\*?|align\*?|displaymath)\}(.*?)"
        r"\\end\{(?:equation\*?|align\*?|displaymath)\}",
        lambda m: f"$$\n{m.group(1).strip()}\n$$",
        work, flags=re.DOTALL,
    )
    # Figures: keep the includegraphics path and the caption as a Markdown image.
    work = re.sub(
        r"\\begin\{figure\}.*?\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}"
        r".*?(?:\\caption\{([^}]*)\})?.*?\\end\{figure\}",
        lambda m: f"![{(m.group(2) or '').strip()}]({m.group(1)})",
        work, flags=re.DOTALL,
    )

    def heading_replacement(match: re.Match[str]) -> str:
        depth = {
            "section": 1, "subsection": 2, "subsubsection": 3,
            "paragraph": 4, "subparagraph": 5,
        }.get(match.group(1), 1)
        return f"\n{'#' * depth} {match.group(2).strip()}\n"

    work = _TEX_HEADING.sub(heading_replacement, work)
    work = _TEX_TEXTBF.sub(r"**\1**", work)
    work = _TEX_EMPH.sub(r"*\1*", work)
    work = _TEX_TEXTTT.sub(r"`\1`", work)
    work = _TEX_HREF.sub(r"[\2](\1)", work)
    if keep_citations:
        # \cite{a,b} -> [A][B], restoring the marker form the agents use.
        work = _TEX_CITE.sub(
            lambda m: "".join(
                f"[{k.strip().upper()}]" for k in m.group(1).split(",") if k.strip()
            ),
            work,
        )
    else:
        work = _TEX_CITE.sub("", work)

    work = _TEX_ITEM.sub("- ", work)
    work = _TEX_ENV.sub("", work)
    # Any remaining unknown environments: keep the content, drop the wrapper.
    work = re.sub(r"\\(?:begin|end)\{[^}]*\}(\[[^\]]*\])?", "", work)
    work = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?\{([^}]*)\}", r"\2", work)
    work = re.sub(r"\\[a-zA-Z]+\*?", "", work)

    for latex, plain in _LATEX_UNESCAPE:
        work = work.replace(latex, plain)
    work = work.replace(" \\\\", "").replace("\\\\", "")
    return re.sub(r"\n{3,}", "\n\n", work).strip() + "\n"


def describe() -> dict[str, Any]:
    return {
        "markdown_to_latex": {
            "supported": ["headings", "paragraphs", "bold", "italic",
                          "inline code", "code blocks", "inline math",
                          "display math", "bullet lists", "numbered lists",
                          "tables", "links", "images", "citation markers"],
            "notes": "protected regions (math, code) are never escaped; use "
                     "xelatex for Chinese text",
        },
        "latex_to_markdown": {
            "supported": ["headings", "abstract", "emphasis", "verbatim",
                          "equations", "figures", "lists", "cite commands"],
            "lossy": ["preamble", "custom macros", "unknown environments",
                      "precise table alignment"],
        },
    }
