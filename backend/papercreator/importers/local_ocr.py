"""Capability-gated, offline OCR for selected PDF pages.

The adapter never downloads models or invokes a shell.  It needs a local
Tesseract executable plus either the optional pypdfium2 package or pdftoppm.
Callers decide which pages need OCR and enforce the product page budget.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


MAX_OCR_PAGES = 200
DEFAULT_OCR_PAGES = 50
DEFAULT_DPI = 200
PAGE_TIMEOUT_SECONDS = 60
_LANGUAGES = re.compile(r"^[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*$")


def _pdfium_available() -> bool:
    try:
        import pypdfium2  # noqa: F401

        return True
    except ImportError:
        return False


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=creationflags,
    )


def capabilities() -> dict[str, Any]:
    tesseract = shutil.which("tesseract")
    pdftoppm = shutil.which("pdftoppm")
    renderers = []
    if _pdfium_available():
        renderers.append("pypdfium2")
    if pdftoppm:
        renderers.append("pdftoppm")
    languages: list[str] = []
    diagnostics: list[str] = []
    if tesseract:
        try:
            result = _run([tesseract, "--list-langs"], timeout=10)
            if result.returncode == 0:
                languages = sorted({
                    line.strip() for line in result.stdout.splitlines()[1:]
                    if re.fullmatch(r"[A-Za-z0-9_]+", line.strip())
                })
            else:
                diagnostics.append("Tesseract language discovery failed.")
        except (OSError, subprocess.TimeoutExpired) as exc:
            diagnostics.append(f"Tesseract language discovery failed: {type(exc).__name__}.")
    available = bool(tesseract and renderers and languages)
    if not tesseract:
        diagnostics.append("Tesseract was not found on PATH.")
    if not renderers:
        diagnostics.append("Install the pypdfium2 OCR extra or provide pdftoppm on PATH.")
    if tesseract and not languages:
        diagnostics.append("No Tesseract language packs were detected.")
    return {
        "available": available,
        "offline": True,
        "engine": "tesseract" if tesseract else "",
        "engine_path": tesseract or "",
        "renderers": renderers,
        "languages": languages,
        "default_languages": "eng" if "eng" in languages else (languages[0] if languages else ""),
        "default_max_pages": DEFAULT_OCR_PAGES,
        "max_pages": MAX_OCR_PAGES,
        "dpi": DEFAULT_DPI,
        "page_timeout_seconds": PAGE_TIMEOUT_SECONDS,
        "diagnostics": diagnostics,
    }


def _render_pdfium(source: Path, page_index: int, target: Path, dpi: int) -> None:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(source))
    try:
        page = document[page_index]
        try:
            bitmap = page.render(scale=dpi / 72)
            try:
                bitmap.to_pil().save(target, format="PNG")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def _render_pdftoppm(
    executable: str, source: Path, page_index: int, target: Path, dpi: int
) -> None:
    prefix = target.with_suffix("")
    result = _run(
        [
            executable, "-f", str(page_index + 1), "-l", str(page_index + 1),
            "-singlefile", "-r", str(dpi), "-png", str(source), str(prefix),
        ],
        timeout=PAGE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0 or not target.is_file():
        raise RuntimeError(result.stderr.strip() or "pdftoppm did not create a page image")


def ocr_pdf_pages(
    source: Path,
    page_indices: list[int],
    *,
    languages: str,
    dpi: int = DEFAULT_DPI,
) -> dict[str, Any]:
    if not _LANGUAGES.fullmatch(languages):
        raise ValueError("OCR languages must be Tesseract language ids joined with '+'")
    details = capabilities()
    if not details["available"]:
        raise RuntimeError("local OCR is unavailable: " + " ".join(details["diagnostics"]))
    requested = languages.split("+")
    missing = [language for language in requested if language not in details["languages"]]
    if missing:
        raise RuntimeError(f"Tesseract language pack(s) not installed: {', '.join(missing)}")
    if len(page_indices) > MAX_OCR_PAGES:
        raise ValueError(f"OCR is limited to {MAX_OCR_PAGES} pages per preview")
    renderer = details["renderers"][0]
    tesseract = str(details["engine_path"])
    texts: dict[int, str] = {}
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="papercreator-ocr-") as temporary:
        temporary_root = Path(temporary)
        for page_index in page_indices:
            image = temporary_root / f"page-{page_index + 1}.png"
            try:
                if renderer == "pypdfium2":
                    _render_pdfium(source, page_index, image, dpi)
                else:
                    _render_pdftoppm(
                        str(shutil.which("pdftoppm")), source, page_index, image, dpi
                    )
                result = _run(
                    [tesseract, str(image), "stdout", "-l", languages, "--psm", "3"],
                    timeout=PAGE_TIMEOUT_SECONDS,
                )
                if result.returncode != 0:
                    warnings.append(
                        f"OCR failed on page {page_index + 1}: "
                        f"{(result.stderr.strip() or 'Tesseract error')[:300]}"
                    )
                    texts[page_index] = ""
                else:
                    texts[page_index] = result.stdout.strip()
            except subprocess.TimeoutExpired:
                warnings.append(f"OCR timed out on page {page_index + 1}.")
                texts[page_index] = ""
            except Exception as exc:  # noqa: BLE001 - keep other pages usable
                warnings.append(
                    f"OCR could not process page {page_index + 1}: {type(exc).__name__}."
                )
                texts[page_index] = ""
    return {
        "texts": texts,
        "warnings": warnings,
        "engine": "tesseract",
        "renderer": renderer,
        "languages": languages,
        "dpi": dpi,
    }
