"""Filesystem layout resolution.

Single source of truth for *where things live*. Every other module asks this
module for a directory instead of computing paths itself, so the layout can be
relocated (portable install, tests, multiple workspaces) by changing only the
``PAPERCREATOR_WORKBENCH`` environment variable.

The installed desktop product has one user-selected root:

``workbench_root``  (PAPERCREATOR_WORKBENCH)
    A normal folder selected by the user. PaperCreator never scatters project
    content through this folder: every managed byte lives below its hidden
    ``.papercreator`` child.

``home``
    ``<workbench_root>/.papercreator``. Contains the database, settings,
    secrets, logs, caches, imported resources and writing projects. Backing up
    this directory backs up the complete PaperCreator workbench.

``workspace``
    Kept as the compatibility name used by the project store. It resolves to
    ``<home>/projects``; each child is one writing project and optional Git
    repository.

Default home:
    Windows -> ``%APPDATA%\\PaperCreator``
    macOS   -> ``~/Library/Application Support/PaperCreator``
    Linux   -> ``$XDG_DATA_HOME/papercreator`` or ``~/.local/share/papercreator``

``PAPERCREATOR_HOME`` and ``PAPERCREATOR_WORKSPACE`` remain supported for
tests and legacy/headless launches. The Electron launcher uses
``PAPERCREATOR_WORKBENCH``. Repo development defaults to the repository as the
workbench root, so its managed state remains ``<repo>/.papercreator``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

_ENV_HOME = "PAPERCREATOR_HOME"
_ENV_WORKSPACE = "PAPERCREATOR_WORKSPACE"
_ENV_WORKBENCH = "PAPERCREATOR_WORKBENCH"

MANAGED_DIRNAME = ".papercreator"
WORKBENCH_SCHEMA_VERSION = 1


def _platform_default_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "PaperCreator"
        return Path.home() / "AppData" / "Roaming" / "PaperCreator"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PaperCreator"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "papercreator"
    return Path.home() / ".local" / "share" / "papercreator"


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk upwards looking for this repository's marker directory.

    Returns ``None`` when running from an installed package rather than a
    checkout. Used both for the dev-home override and to locate ``.env``.
    """
    # A PyInstaller executable may physically sit inside a source checkout
    # during packaging tests. It is still an installed runtime and must never
    # inherit the checkout's .env or private development state.
    if getattr(sys, "frozen", False) and start is None:
        return None
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "backend" / "papercreator").is_dir():
            return candidate
    return None


def _resolve_layout() -> tuple[Path | None, Path]:
    """Return ``(selected workbench root, managed home)``.

    ``PAPERCREATOR_HOME`` deliberately wins for backwards compatibility with
    isolated tests and headless automation. New desktop launches always set
    ``PAPERCREATOR_WORKBENCH`` and therefore receive the single-root layout.
    """
    explicit = os.environ.get(_ENV_HOME)
    if explicit:
        home = Path(explicit).expanduser().resolve()
        root = home.parent if home.name.lower() == MANAGED_DIRNAME else None
        return root, home
    workbench = os.environ.get(_ENV_WORKBENCH)
    if workbench:
        root = Path(workbench).expanduser().resolve()
        return root, root / MANAGED_DIRNAME
    repo = find_repo_root()
    if repo is not None:
        root = repo.resolve()
        return root, root / MANAGED_DIRNAME
    return None, _platform_default_home()


@dataclass(frozen=True)
class Paths:
    """Resolved directory layout. Immutable; build via :func:`get_paths`."""

    home: Path
    workspace: Path
    workbench_root: Path | None = None

    @property
    def manifest_file(self) -> Path:
        """Versioned marker proving that ``home`` is a PaperCreator workbench."""
        return self.home / "workbench.json"

    # ---------------------------------------------------------------- home
    @property
    def config_dir(self) -> Path:
        return self.home / "config"

    @property
    def settings_file(self) -> Path:
        """Non-secret user settings (JSON). Safe to sync/backup."""
        return self.config_dir / "settings.json"

    @property
    def secrets_file(self) -> Path:
        """API keys entered through the UI. Never logged, never exported."""
        return self.config_dir / "secrets.json"

    @property
    def db_file(self) -> Path:
        return self.home / "papercreator.db"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    @property
    def http_cache_dir(self) -> Path:
        """Raw scholarly-API responses, keyed by request hash. Rebuildable."""
        return self.cache_dir / "http"

    @property
    def embedding_cache_dir(self) -> Path:
        """Vector cache keyed by (model, text hash). Rebuildable but slow."""
        return self.cache_dir / "embeddings"

    @property
    def models_dir(self) -> Path:
        """Local sentence-transformer weights, when the user downloads them."""
        return self.home / "models"

    @property
    def pdf_dir(self) -> Path:
        """Downloaded open-access PDFs, named by paper id."""
        return self.reference_papers_dir / "pdfs"

    @property
    def user_skills_dir(self) -> Path:
        """User- and LLM-authored skills. Takes precedence over builtins."""
        return self.home / "skills"

    @property
    def exports_dir(self) -> Path:
        """Scratch space for exports not tied to a project."""
        return self.home / "exports"

    @property
    def library_dir(self) -> Path:
        """Managed source material, separated by provenance and purpose."""
        return self.home / "library"

    @property
    def ideas_dir(self) -> Path:
        return self.library_dir / "ideas"

    @property
    def reference_papers_dir(self) -> Path:
        return self.library_dir / "reference-papers"

    @property
    def own_papers_dir(self) -> Path:
        return self.library_dir / "own-papers"

    @property
    def code_projects_dir(self) -> Path:
        return self.library_dir / "code-projects"

    @property
    def datasets_dir(self) -> Path:
        return self.library_dir / "datasets"

    @property
    def supplementary_dir(self) -> Path:
        return self.library_dir / "supplementary"

    @property
    def inbox_dir(self) -> Path:
        return self.library_dir / "inbox"

    @property
    def backups_dir(self) -> Path:
        return self.home / "backups"

    # ----------------------------------------------------------- workspace
    def project_dir(self, project_slug: str) -> Path:
        return self.workspace / project_slug

    # -------------------------------------------------------------- setup
    def ensure(self) -> "Paths":
        """Create every directory this layout owns. Idempotent."""
        for directory in (
            self.home,
            self.config_dir,
            self.logs_dir,
            self.cache_dir,
            self.http_cache_dir,
            self.embedding_cache_dir,
            self.models_dir,
            self.library_dir,
            self.ideas_dir,
            self.reference_papers_dir,
            self.pdf_dir,
            self.own_papers_dir,
            self.code_projects_dir,
            self.datasets_dir,
            self.supplementary_dir,
            self.inbox_dir,
            self.user_skills_dir,
            self.exports_dir,
            self.backups_dir,
            self.workspace,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.manifest_file.exists():
            payload = {
                "format": "papercreator-workbench",
                "schema_version": WORKBENCH_SCHEMA_VERSION,
                "product": "PaperCreator",
                "created_at": datetime.now(UTC).isoformat(),
            }
            temporary = self.manifest_file.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.manifest_file)
        return self

    def describe(self) -> dict[str, str]:
        """Flat map for the /api/system/paths endpoint and log banner."""
        return {
            "workbench": str(self.workbench_root or ""),
            "home": str(self.home),
            "workspace": str(self.workspace),
            "projects": str(self.workspace),
            "library": str(self.library_dir),
            "ideas": str(self.ideas_dir),
            "reference_papers": str(self.reference_papers_dir),
            "own_papers": str(self.own_papers_dir),
            "code_projects": str(self.code_projects_dir),
            "datasets": str(self.datasets_dir),
            "supplementary": str(self.supplementary_dir),
            "inbox": str(self.inbox_dir),
            "config": str(self.config_dir),
            "database": str(self.db_file),
            "logs": str(self.logs_dir),
            "cache": str(self.cache_dir),
            "pdfs": str(self.pdf_dir),
            "skills": str(self.user_skills_dir),
        }


@lru_cache(maxsize=1)
def get_paths() -> Paths:
    """Process-wide resolved layout. Cached; call :func:`reset_paths` in tests."""
    workbench_root, home = _resolve_layout()
    explicit_ws = os.environ.get(_ENV_WORKSPACE)
    workspace = (
        Path(explicit_ws).expanduser().resolve() if explicit_ws else home / "projects"
    )
    return Paths(home=home, workspace=workspace, workbench_root=workbench_root)


def reset_paths() -> None:
    """Drop the cache so a test can re-point the environment variables."""
    get_paths.cache_clear()
