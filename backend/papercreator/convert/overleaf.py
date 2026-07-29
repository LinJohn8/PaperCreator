"""Overleaf integration.

Two routes, because Overleaf's git access is a paid feature and most users will
not have it:

**Git bridge** (paid Overleaf) - :func:`push_to_overleaf` clones the Overleaf
project into a scratch directory, replaces the generated files, commits and
pushes. This is a true two-way channel: :func:`pull_from_overleaf` brings a
co-author's edits back into the local manuscript.

**Zip upload** (any Overleaf account) - :func:`prepare_zip` produces the archive
that Overleaf's "New Project > Upload Project" accepts. One-way, but works for
everyone and needs no credentials.

The git URL and token are stored as secrets (never logged, masked in API
responses). The token is injected into the remote URL only for the duration of a
single subprocess call and is scrubbed from any captured output.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from ..core.config import get_settings
from ..core.errors import ConfigurationError, ExternalToolError, ValidationError
from ..core.logging_setup import get_logger, scrub
from ..core.util import safe_filename, utc_now_iso
from ..store import projects as projects_store
from . import exporters

log = get_logger(__name__)

# Files PaperCreator owns in the Overleaf project. A push replaces exactly these
# and leaves everything else (a co-author's figures, a custom .cls) untouched.
MANAGED = ("main.tex", "references.bib", "sections", "figures")


def _credentials() -> tuple[str, str]:
    settings = get_settings().overleaf
    url = settings.git_url.strip()
    token = settings.git_token.strip()
    if not url:
        raise ConfigurationError(
            "no Overleaf git URL is configured. In Overleaf open Menu > Git and "
            "copy the project URL, then paste it into Settings > Overleaf. "
            "(Git access is an Overleaf premium feature - if you do not have it, "
            "use the zip export instead.)"
        )
    if not token:
        raise ConfigurationError(
            "no Overleaf git token is configured. Generate one at "
            "https://www.overleaf.com/user/settings (Git integration) and paste it "
            "into Settings > Overleaf."
        )
    return url, token


def _authenticated_url(url: str, token: str) -> str:
    """Embed the token in the remote URL.

    Overleaf's git bridge authenticates with the token as the password and any
    username. Embedding it avoids an interactive credential prompt, which would
    hang a subprocess. The URL is never logged - callers pass output through
    :func:`scrub`.
    """
    parsed = urlparse(url)
    netloc = f"git:{quote(token, safe='')}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _run_git(
    args: list[str], cwd: Path, *, timeout: float = 180.0
) -> subprocess.CompletedProcess:
    if shutil.which("git") is None:
        raise ExternalToolError(
            "git is not installed, which the Overleaf git bridge requires. Use the "
            "zip export instead, or install git."
        )
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True,
            timeout=timeout, check=False,
            # Never let git prompt: in a subprocess that is an indefinite hang.
            env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo",
                 "PATH": _path_env()},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExternalToolError(f"git {args[0]} failed: {exc}") from exc
    return result


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "")


def _decode(stream: bytes) -> str:
    return scrub(stream.decode("utf-8", "replace"))


def prepare_zip(
    project_id: str, *, document_class: str = "article", language: str = "primary"
) -> dict[str, Any]:
    """Build the zip that Overleaf's "Upload Project" accepts.

    Overleaf expects the ``.tex`` files at the archive root (not inside a folder),
    so the archive is written with paths relative to the LaTeX directory.
    """
    report = exporters.export_latex(
        project_id, document_class=document_class, language=language
    )
    source = Path(report["path"])
    project = projects_store.require(project_id)
    target = (
        projects_store.project_root(project) / "exports"
        / f"{safe_filename(project.slug)}-overleaf.zip"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(source.rglob("*")):
            if file.is_file() and file.suffix not in (".aux", ".log", ".out"):
                archive.write(file, file.relative_to(source))
    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "engine": report["engine"],
        "instructions": [
            "Open https://www.overleaf.com/project",
            "New Project > Upload Project",
            f"Select {target.name}",
            f"In Overleaf: Menu > Compiler, set it to {report['engine']}"
            + (" (required for Chinese text)" if report["engine"] == "xelatex" else ""),
        ],
        "warnings": report.get("warnings", []),
    }


def push_to_overleaf(
    project_id: str,
    *,
    document_class: str = "article",
    language: str = "primary",
    message: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Export LaTeX and push it to the configured Overleaf project.

    Only :data:`MANAGED` paths are replaced, so files a co-author added in
    Overleaf survive. A non-fast-forward push is refused unless ``force`` is set,
    because Overleaf history is the co-author's work.
    """
    url, token = _credentials()
    report = exporters.export_latex(
        project_id, document_class=document_class, language=language
    )
    source = Path(report["path"])
    project = projects_store.require(project_id)

    with tempfile.TemporaryDirectory(prefix="pc_overleaf_") as temp:
        workdir = Path(temp) / "repo"
        clone = _run_git(
            ["clone", "--depth", "1", _authenticated_url(url, token), str(workdir)],
            Path(temp),
            timeout=300,
        )
        if clone.returncode != 0:
            raise ExternalToolError(
                "could not clone the Overleaf project. Check the git URL and "
                "token, and that git access is enabled on your Overleaf plan.",
                details={"stderr": _decode(clone.stderr)[:600]},
            )

        # Replace only the paths we own.
        replaced: list[str] = []
        for name in MANAGED:
            target = workdir / name
            origin = source / name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()
            if origin.is_dir():
                shutil.copytree(origin, target)
                replaced.append(name)
            elif origin.is_file():
                shutil.copy2(origin, target)
                replaced.append(name)

        _run_git(["add", "-A"], workdir)
        status = _run_git(["status", "--porcelain"], workdir)
        if not status.stdout.strip():
            return {
                "pushed": False,
                "reason": "the Overleaf project already matches the local export",
                "replaced": replaced,
            }

        commit_message = message or (
            f"PaperCreator: update {project.title} ({utc_now_iso()})"
        )
        commit = _run_git(["commit", "-m", commit_message], workdir)
        if commit.returncode != 0:
            raise ExternalToolError(
                "could not commit to the Overleaf clone",
                details={"stderr": _decode(commit.stderr)[:600]},
            )
        push_args = ["push", "origin", "HEAD"]
        if force:
            push_args.insert(1, "--force")
        push = _run_git(push_args, workdir, timeout=300)
        if push.returncode != 0:
            stderr = _decode(push.stderr)
            if "non-fast-forward" in stderr or "rejected" in stderr:
                raise ExternalToolError(
                    "Overleaf rejected the push because the remote has newer "
                    "commits - somebody edited the project there. Pull those "
                    "changes first, or push with force to overwrite them.",
                    details={"stderr": stderr[:600], "action": "pull_first"},
                )
            raise ExternalToolError(
                "the push to Overleaf failed",
                details={"stderr": stderr[:600]},
            )
        return {
            "pushed": True,
            "commit_message": commit_message,
            "replaced": replaced,
            "engine": report["engine"],
            "files": len(list(source.rglob("*"))),
            "warnings": report.get("warnings", []),
        }


def pull_from_overleaf(project_id: str, *, apply_to_manuscript: bool = False) -> dict[str, Any]:
    """Fetch the Overleaf project and optionally import its section text back.

    ``apply_to_manuscript=False`` by default: importing overwrites local section
    content, and the caller should show a diff first. When enabled, a snapshot is
    taken so the overwrite is revertable.
    """
    url, token = _credentials()
    project = projects_store.require(project_id)
    landing = projects_store.project_root(project) / ".papercreator" / "overleaf"
    if landing.exists():
        shutil.rmtree(landing, ignore_errors=True)
    landing.parent.mkdir(parents=True, exist_ok=True)

    clone = _run_git(
        ["clone", "--depth", "1", _authenticated_url(url, token), str(landing)],
        landing.parent,
        timeout=300,
    )
    if clone.returncode != 0:
        raise ExternalToolError(
            "could not clone the Overleaf project",
            details={"stderr": _decode(clone.stderr)[:600]},
        )

    tex_files = sorted(p for p in landing.rglob("*.tex") if p.is_file())
    result: dict[str, Any] = {
        "path": str(landing),
        "tex_files": [str(p.relative_to(landing)) for p in tex_files],
        "applied": False,
        "sections_updated": [],
        "warnings": [],
    }
    if not apply_to_manuscript:
        result["warnings"].append(
            "the Overleaf project was fetched but not imported. Review the files, "
            "then import with apply_to_manuscript=true."
        )
        return result

    from ..store import documents as documents_store
    from ..store import snapshots as snapshots_store
    from .markdown_latex import latex_to_markdown

    document = documents_store.primary_document(project_id)
    # Refuse before modifying DB text if local manuscript files have changed
    # outside PaperCreator. The snapshot below protects DB state, not those
    # external file edits.
    documents_store.ensure_sync_safe(document.id, "flush")
    snapshot = snapshots_store.capture(
        project_id, label="before Overleaf import", kind="manual"
    )
    sections_dir = landing / "sections"
    updated: list[str] = []
    if sections_dir.is_dir():
        for tex in sorted(sections_dir.glob("*.tex")):
            key = tex.stem
            section = documents_store.get_section_by_key(document.id, key)
            markdown = latex_to_markdown(tex.read_text(encoding="utf-8", errors="replace"))
            # Drop the leading heading: the store owns section titles.
            body = "\n".join(
                line for index, line in enumerate(markdown.splitlines())
                if not (index == 0 and line.startswith("#"))
            ).strip()
            if section is None:
                documents_store.create_section(
                    document.id, key=key, title=key.replace("-", " ").title(),
                    content=body, status="drafted",
                )
            else:
                documents_store.update_section(section.id, content=body)
            updated.append(key)
    else:
        result["warnings"].append(
            "the Overleaf project has no sections/ directory, so nothing could be "
            "imported section by section. Import main.tex manually if needed."
        )
    documents_store.flush_document_to_disk(document.id)
    result.update({
        "applied": True,
        "sections_updated": updated,
        "snapshot_before": snapshot["id"],
    })
    result["warnings"].append(
        "LaTeX was converted back to Markdown, which is lossy: custom macros and "
        "unusual environments are simplified. A snapshot was taken first."
    )
    return result


def status() -> dict[str, Any]:
    """Overleaf configuration state, for the settings panel."""
    settings = get_settings().overleaf
    return {
        "git_configured": bool(settings.git_url.strip() and settings.git_token.strip()),
        "git_url_set": bool(settings.git_url.strip()),
        "token_set": bool(settings.git_token.strip()),
        "git_available": shutil.which("git") is not None,
        "zip_upload_available": True,
        "managed_paths": list(MANAGED),
        "note": "Git access requires an Overleaf premium plan. The zip export "
                "works with any account.",
    }
