"""Managed workbench resources.

Every imported file or directory is copied below the selected workbench's
``.papercreator/library`` tree before it is registered.  The database stores a
path relative to ``.papercreator`` so moving the complete workbench to another
drive does not break it.  External paths are provenance only.

Writing projects are intentionally not resources: they are owned by
``store.projects`` and live in ``.papercreator/projects``.  This module covers
the material a project consumes (ideas, literature, the user's previous work,
code, datasets and supplementary files).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.db import dumps, execute, loads, query, query_one
from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..core.logging_setup import get_logger
from ..core.models import WorkbenchResource, WorkbenchResourceKind
from ..core.paths import get_paths
from ..core.util import new_id, slugify, utc_now_iso

log = get_logger(__name__)

RESOURCE_KINDS: tuple[WorkbenchResourceKind, ...] = (
    "idea",
    "reference_paper",
    "own_paper",
    "code_project",
    "dataset",
    "supplementary",
    "inbox",
)

# Code imports should preserve source and Git history, not generated dependency
# trees or obvious secret files.  The ignored names are returned in metadata so
# the copy is auditable rather than silently incomplete.
_CODE_GENERATED_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "target",
}

_COPY_CHUNK_BYTES = 4 * 1024 * 1024
_MIN_FREE_RESERVE_BYTES = 16 * 1024 * 1024
_MAX_FREE_RESERVE_BYTES = 512 * 1024 * 1024
_PARTIAL_NAME = re.compile(r"^\.partial-res_[0-9a-f]{16}$")

ProgressCallback = Callable[[float, str], None]
CancellationCheckpoint = Callable[[], None]


@dataclass(frozen=True)
class _ImportFile:
    source: Path
    relative: Path
    size: int
    mtime_ns: int
    device: int
    inode: int


@dataclass(frozen=True)
class _ImportInventory:
    files: tuple[_ImportFile, ...]
    directories: tuple[Path, ...]
    total_bytes: int
    excluded_generated: tuple[str, ...]
    excluded_links: tuple[str, ...]
    excluded_special: tuple[str, ...]


def _category_dir(kind: WorkbenchResourceKind) -> Path:
    paths = get_paths()
    return {
        "idea": paths.ideas_dir,
        "reference_paper": paths.reference_papers_dir,
        "own_paper": paths.own_papers_dir,
        "code_project": paths.code_projects_dir,
        "dataset": paths.datasets_dir,
        "supplementary": paths.supplementary_dir,
        "inbox": paths.inbox_dir,
    }[kind]


def category_directories() -> dict[str, str]:
    return {kind: str(_category_dir(kind)) for kind in RESOURCE_KINDS}


def _row_to_resource(row: Any) -> WorkbenchResource:
    data = dict(row)
    return WorkbenchResource(
        id=data["id"],
        kind=data["kind"],
        title=data.get("title") or "",
        description=data.get("description") or "",
        managed_path=data.get("managed_path") or "",
        original_path=data.get("original_path") or "",
        is_directory=bool(data.get("is_directory")),
        mime_type=data.get("mime_type") or "",
        size_bytes=int(data.get("size_bytes") or 0),
        checksum=data.get("checksum") or "",
        project_id=data.get("project_id") or "",
        paper_id=data.get("paper_id") or "",
        metadata=loads(data.get("metadata"), {}) or {},
        created_at=data.get("created_at") or "",
        updated_at=data.get("updated_at") or "",
    )


def get(resource_id: str) -> WorkbenchResource | None:
    row = query_one("SELECT * FROM workbench_resources WHERE id=?", (resource_id,))
    return _row_to_resource(row) if row else None


def require(resource_id: str) -> WorkbenchResource:
    resource = get(resource_id)
    if resource is None:
        raise NotFoundError(f"workbench resource {resource_id} not found")
    return resource


def list_resources(
    *, kind: str = "", project_id: str = "", limit: int = 500
) -> list[WorkbenchResource]:
    conditions: list[str] = []
    params: list[Any] = []
    if kind:
        if kind not in RESOURCE_KINDS:
            raise ValidationError(f"unknown resource kind '{kind}'")
        conditions.append("kind=?")
        params.append(kind)
    if project_id:
        conditions.append("project_id=?")
        params.append(project_id)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(max(1, min(limit, 2000)))
    rows = query(
        f"SELECT * FROM workbench_resources{where} "
        "ORDER BY updated_at DESC LIMIT ?",
        params,
    )
    return [_row_to_resource(row) for row in rows]


def absolute_path(resource: WorkbenchResource) -> Path:
    """Resolve a stored relative path and reject a corrupted traversal value."""
    home = get_paths().home.resolve()
    target = (home / resource.managed_path).resolve()
    if not target.is_relative_to(home):
        raise ConflictError(
            f"resource {resource.id} has an unsafe managed path; repair the database"
        )
    return target


def _digest_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _digest_directory(path: Path) -> tuple[int, str, int]:
    """Content digest stable across a workbench move."""
    digest = hashlib.sha256()
    size = 0
    count = 0
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = child.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8", errors="surrogatepass"))
        file_size, file_digest = _digest_file(child)
        digest.update(file_digest.encode("ascii"))
        size += file_size
        count += 1
    return size, digest.hexdigest(), count


def _noop_progress(_fraction: float, _message: str) -> None:
    return None


def _noop_checkpoint() -> None:
    return None


def _is_reparse_or_symlink(path: Path, info: os.stat_result | None = None) -> bool:
    """Return true for links and Windows junction/reparse entries.

    ``shutil.copytree`` follows Windows junctions in configurations that are
    difficult to audit.  Managed imports therefore never follow any link-like
    entry.  Root links are rejected; nested links are recorded and skipped.
    """
    current = info or path.lstat()
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    attributes = int(getattr(current, "st_file_attributes", 0))
    return stat.S_ISLNK(current.st_mode) or bool(attributes & reparse_flag)


def _is_secret_env_name(name: str) -> bool:
    return name == ".env" or (
        name.startswith(".env.") and not name.endswith(".example")
    )


def _relative_label(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _source_error(message: str, *, path: Path, phase: str) -> ValidationError:
    return ValidationError(
        message,
        code="resource_import_source_invalid",
        details={"phase": phase, "source_path": str(path)},
    )


def _scan_directory(
    source: Path,
    *,
    kind: WorkbenchResourceKind,
    progress: ProgressCallback,
    checkpoint: CancellationCheckpoint,
) -> _ImportInventory:
    """Create a non-following, deterministic source inventory.

    The copy consumes exactly this inventory.  Size/mtime/device/inode are
    checked again on the open file handle, so a source mutation does not yield
    a silently mixed managed snapshot.
    """
    files: list[_ImportFile] = []
    directories: list[Path] = []
    excluded_generated: list[str] = []
    excluded_links: list[str] = []
    excluded_special: list[str] = []
    total_bytes = 0
    scanned = 0

    def walk_error(exc: OSError) -> None:
        raise _source_error(
            f"cannot scan source directory: {exc}", path=source, phase="scan"
        ) from exc

    progress(0.01, "Scanning source directory…")
    for root_text, dir_names, file_names in os.walk(
        source, topdown=True, followlinks=False, onerror=walk_error
    ):
        checkpoint()
        root = Path(root_text)
        try:
            root_info = root.lstat()
        except OSError as exc:
            raise _source_error(
                f"source directory changed during scan: {exc}",
                path=root,
                phase="scan",
            ) from exc
        if root != source and _is_reparse_or_symlink(root, root_info):
            raise ConflictError(
                "source directory changed into a link during import",
                code="resource_import_source_changed",
                details={"phase": "scan", "relative_path": _relative_label(root, source)},
            )

        kept_directories: list[str] = []
        for name in sorted(dir_names):
            candidate = root / name
            relative = _relative_label(candidate, source)
            if kind == "code_project" and name in _CODE_GENERATED_DIRS:
                excluded_generated.append(relative)
                continue
            try:
                info = candidate.lstat()
            except OSError as exc:
                raise _source_error(
                    f"cannot inspect source entry: {exc}",
                    path=candidate,
                    phase="scan",
                ) from exc
            if _is_reparse_or_symlink(candidate, info):
                excluded_links.append(relative)
                continue
            if not stat.S_ISDIR(info.st_mode):
                excluded_special.append(relative)
                continue
            kept_directories.append(name)
            directories.append(Path(relative))
        dir_names[:] = kept_directories

        for name in sorted(file_names):
            checkpoint()
            candidate = root / name
            relative = _relative_label(candidate, source)
            if kind == "code_project" and _is_secret_env_name(name):
                excluded_generated.append(relative)
                continue
            try:
                info = candidate.lstat()
            except OSError as exc:
                raise _source_error(
                    f"cannot inspect source file: {exc}",
                    path=candidate,
                    phase="scan",
                ) from exc
            if _is_reparse_or_symlink(candidate, info):
                excluded_links.append(relative)
                continue
            if not stat.S_ISREG(info.st_mode):
                excluded_special.append(relative)
                continue
            files.append(
                _ImportFile(
                    source=candidate,
                    relative=Path(relative),
                    size=int(info.st_size),
                    mtime_ns=int(info.st_mtime_ns),
                    device=int(info.st_dev),
                    inode=int(info.st_ino),
                )
            )
            total_bytes += int(info.st_size)
            scanned += 1
            if scanned % 250 == 0:
                progress(0.03, f"Scanning source… {scanned:,} files found")

    files.sort(key=lambda item: item.relative.as_posix())
    directories.sort(key=lambda item: item.as_posix())
    progress(
        0.06,
        f"Source scan complete: {len(files):,} files, {_format_bytes(total_bytes)}",
    )
    return _ImportInventory(
        files=tuple(files),
        directories=tuple(directories),
        total_bytes=total_bytes,
        excluded_generated=tuple(excluded_generated),
        excluded_links=tuple(excluded_links),
        excluded_special=tuple(excluded_special),
    )


def _single_file_inventory(source: Path) -> _ImportInventory:
    info = source.lstat()
    if _is_reparse_or_symlink(source, info):
        raise _source_error(
            "the selected source is a link or Windows reparse point",
            path=source,
            phase="preflight",
        )
    if not stat.S_ISREG(info.st_mode):
        raise _source_error(
            "the selected source is not a regular file or directory",
            path=source,
            phase="preflight",
        )
    return _ImportInventory(
        files=(
            _ImportFile(
                source=source,
                relative=Path(source.name),
                size=int(info.st_size),
                mtime_ns=int(info.st_mtime_ns),
                device=int(info.st_dev),
                inode=int(info.st_ino),
            ),
        ),
        directories=(),
        total_bytes=int(info.st_size),
        excluded_generated=(),
        excluded_links=(),
        excluded_special=(),
    )


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _check_disk_space(category: Path, total_bytes: int) -> dict[str, int]:
    usage = shutil.disk_usage(category)
    reserve = max(
        _MIN_FREE_RESERVE_BYTES,
        min(_MAX_FREE_RESERVE_BYTES, max(0, total_bytes) // 20),
    )
    required = max(0, total_bytes) + reserve
    if usage.free < required:
        raise ValidationError(
            "not enough free space for a safe managed copy "
            f"({_format_bytes(required)} required including reserve; "
            f"{_format_bytes(usage.free)} free)",
            code="resource_import_insufficient_space",
            details={
                "phase": "space_preflight",
                "source_bytes": total_bytes,
                "required_bytes": required,
                "free_bytes": usage.free,
                "reserve_bytes": reserve,
                "hint": "Free disk space or choose a workbench on a larger drive.",
            },
        )
    return {
        "source_bytes": total_bytes,
        "required_bytes": required,
        "free_bytes_before": usage.free,
        "reserve_bytes": reserve,
    }


def _matches_inventory(info: os.stat_result, item: _ImportFile) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and int(info.st_size) == item.size
        and int(info.st_mtime_ns) == item.mtime_ns
        and int(info.st_dev) == item.device
        and int(info.st_ino) == item.inode
    )


def _validate_inventory_directory(
    path: Path, *, source_root: Path, relative_path: str
) -> None:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ConflictError(
            "source directory changed after preflight; import was not committed",
            code="resource_import_source_changed",
            details={"phase": "copy", "relative_path": relative_path},
        ) from exc
    if (
        _is_reparse_or_symlink(path, info)
        or not stat.S_ISDIR(info.st_mode)
        or not resolved.is_relative_to(source_root)
    ):
        raise ConflictError(
            "source directory became unsafe after preflight; import was not committed",
            code="resource_import_source_changed",
            details={"phase": "copy", "relative_path": relative_path},
        )


def _copy_inventory_file(
    item: _ImportFile,
    destination: Path,
    *,
    source_root: Path,
    checkpoint: CancellationCheckpoint,
    on_chunk: Callable[[int], None],
) -> str:
    checkpoint()
    try:
        resolved = item.source.resolve(strict=True)
    except OSError as exc:
        raise ConflictError(
            "source file disappeared during import",
            code="resource_import_source_changed",
            details={"phase": "copy", "relative_path": item.relative.as_posix()},
        ) from exc
    if not resolved.is_relative_to(source_root):
        raise ConflictError(
            "source path escaped through a link during import",
            code="resource_import_source_changed",
            details={"phase": "copy", "relative_path": item.relative.as_posix()},
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    try:
        with item.source.open("rb") as reader, destination.open("xb") as writer:
            before = os.fstat(reader.fileno())
            if not _matches_inventory(before, item):
                raise ConflictError(
                    "source file changed after preflight; import was not committed",
                    code="resource_import_source_changed",
                    details={"phase": "copy", "relative_path": item.relative.as_posix()},
                )
            while True:
                checkpoint()
                chunk = reader.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                writer.write(chunk)
                digest.update(chunk)
                on_chunk(len(chunk))
            after = os.fstat(reader.fileno())
            if not _matches_inventory(after, item):
                raise ConflictError(
                    "source file changed while it was being copied; import was not committed",
                    code="resource_import_source_changed",
                    details={"phase": "copy", "relative_path": item.relative.as_posix()},
                )
        shutil.copystat(item.source, destination, follow_symlinks=False)
    except (ConflictError, OSError):
        if destination.exists():
            destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def _remove_partial(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)
        return not path.exists()
    except OSError:
        return False


def cleanup_stale_partials() -> dict[str, Any]:
    """Remove only reserved import staging entries after an unclean restart."""
    removed = 0
    failed: list[str] = []
    for kind in RESOURCE_KINDS:
        category = _category_dir(kind)
        if not category.is_dir():
            continue
        for candidate in category.iterdir():
            if not _PARTIAL_NAME.fullmatch(candidate.name):
                continue
            if _remove_partial(candidate):
                removed += 1
            else:
                failed.append(str(candidate))
    if removed:
        log.warning("removed %s stale resource import staging path(s)", removed)
    if failed:
        log.error("could not remove stale resource import staging paths: %s", failed)
    return {"removed": removed, "failed": failed}


def _insert(
    *,
    resource_id: str,
    kind: WorkbenchResourceKind,
    title: str,
    description: str,
    target: Path,
    original_path: str,
    is_directory: bool,
    size_bytes: int,
    checksum: str,
    project_id: str,
    paper_id: str,
    metadata: dict[str, Any],
) -> WorkbenchResource:
    home = get_paths().home.resolve()
    relative = target.resolve().relative_to(home).as_posix()
    now = utc_now_iso()
    mime_type = "inode/directory" if is_directory else (
        mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    )
    execute(
        "INSERT INTO workbench_resources "
        "(id,kind,title,description,managed_path,original_path,is_directory,"
        "mime_type,size_bytes,checksum,project_id,paper_id,metadata,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            resource_id,
            kind,
            title,
            description,
            relative,
            original_path,
            int(is_directory),
            mime_type,
            size_bytes,
            checksum,
            project_id or None,
            paper_id or None,
            dumps(metadata),
            now,
            now,
        ),
    )
    return require(resource_id)


def create_idea(
    *,
    title: str,
    content: str,
    project_id: str = "",
    paper_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> WorkbenchResource:
    return create_text(
        kind="idea",
        title=title,
        content=content,
        project_id=project_id,
        paper_id=paper_id,
        metadata=metadata,
    )


def create_text(
    *,
    kind: WorkbenchResourceKind,
    title: str,
    content: str,
    project_id: str = "",
    paper_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> WorkbenchResource:
    """Create a managed Markdown note for a metadata-only resource."""
    if kind not in ("idea", "reference_paper", "own_paper", "inbox"):
        raise ValidationError(f"resource kind '{kind}' requires a file or folder")
    if not title.strip() and not content.strip():
        raise ValidationError("a text resource needs a title or content")
    resource_id = new_id("res")
    label = title.strip() or content.strip().splitlines()[0][:100]
    directory = _category_dir(kind)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{resource_id}-{slugify(label, fallback='idea')}.md"
    body = f"# {label}\n\n{content.strip()}\n"
    target.write_text(body, encoding="utf-8")
    size, checksum = _digest_file(target)
    return _insert(
        resource_id=resource_id,
        kind=kind,
        title=label,
        description=content,
        target=target,
        original_path="",
        is_directory=False,
        size_bytes=size,
        checksum=checksum,
        project_id=project_id,
        paper_id=paper_id,
        metadata=metadata or {},
    )


def import_path(
    source_path: str,
    *,
    kind: WorkbenchResourceKind,
    title: str = "",
    description: str = "",
    project_id: str = "",
    paper_id: str = "",
    metadata: dict[str, Any] | None = None,
    progress: ProgressCallback | None = None,
    checkpoint: CancellationCheckpoint | None = None,
) -> WorkbenchResource:
    """Atomically copy a file/folder into its category and register it.

    Directory imports are scanned before any write, checked against available
    space, copied in chunks to a reserved ``.partial-res_*`` sibling, and only
    exposed under their final name after an atomic rename.  The DB row is the
    final commit point.  Failure or cooperative cancellation removes staging.
    """
    if kind not in RESOURCE_KINDS or kind == "idea":
        raise ValidationError("file imports require a non-idea resource kind")
    report = progress or _noop_progress
    cancel_check = checkpoint or _noop_checkpoint
    requested = Path(source_path).expanduser()
    lexical_source = Path(os.path.abspath(requested))
    if not lexical_source.exists():
        raise ValidationError(f"'{lexical_source}' does not exist")
    try:
        lexical_info = lexical_source.lstat()
    except OSError as exc:
        raise _source_error(
            f"cannot inspect selected source: {exc}",
            path=lexical_source,
            phase="preflight",
        ) from exc
    if _is_reparse_or_symlink(lexical_source, lexical_info):
        raise ValidationError(
            "the selected source cannot be a symbolic link or Windows reparse point",
            code="resource_import_link_root",
            details={
                "phase": "preflight",
                "source_path": str(lexical_source),
                "link_policy": "never_follow",
            },
        )
    source = lexical_source.resolve()
    home = get_paths().home.resolve()
    # Copying the managed root or one of its ancestors into itself would recurse
    # forever and could fill the disk.
    if home == source or home.is_relative_to(source):
        raise ValidationError("cannot import the workbench or one of its ancestors")

    category = _category_dir(kind).resolve()
    category.mkdir(parents=True, exist_ok=True)
    resource_id = new_id("res")
    display_title = title.strip() or source.name
    suffix = source.suffix if source.is_file() else ""
    target = category / f"{resource_id}-{slugify(source.stem, fallback='resource')}{suffix}"
    partial = category / f".partial-{resource_id}"
    copied = False

    # Register an existing correctly-categorised managed file in place. This is
    # useful during database recovery and does not create a redundant copy.
    if source.is_relative_to(category):
        target = source
        relative = target.relative_to(home).as_posix()
        existing = query_one(
            "SELECT * FROM workbench_resources WHERE managed_path=?", (relative,)
        )
        if existing:
            return _row_to_resource(existing)
        report(0.08, "Registering existing managed resource in place…")
        if target.is_dir():
            size, checksum, file_count = _digest_directory(target)
            computed_meta: dict[str, Any] = dict(metadata or {})
            computed_meta.update(
                {
                    "file_count": file_count,
                    "import": {
                        "strategy": "register_in_place",
                        "source_files": file_count,
                        "source_bytes": size,
                        "link_policy": "never_follow",
                    },
                }
            )
        else:
            size, checksum = _digest_file(target)
            computed_meta = dict(metadata or {})
            computed_meta["import"] = {
                "strategy": "register_in_place",
                "source_files": 1,
                "source_bytes": size,
                "link_policy": "never_follow",
            }
        report(0.96, "Registering managed resource…")
        return _insert(
            resource_id=resource_id,
            kind=kind,
            title=display_title,
            description=description,
            target=target,
            original_path=str(source),
            is_directory=target.is_dir(),
            size_bytes=size,
            checksum=checksum,
            project_id=project_id,
            paper_id=paper_id,
            metadata=computed_meta,
        )

    cancel_check()
    if source.is_dir():
        inventory = _scan_directory(
            source, kind=kind, progress=report, checkpoint=cancel_check
        )
    else:
        report(0.02, "Inspecting source file…")
        inventory = _single_file_inventory(source)
        report(
            0.06,
            f"Source scan complete: 1 file, {_format_bytes(inventory.total_bytes)}",
        )

    cancel_check()
    space = _check_disk_space(category, inventory.total_bytes)
    report(
        0.09,
        f"Space check passed: {_format_bytes(space['free_bytes_before'])} free",
    )

    copied_bytes = 0
    copied_files = 0

    def on_chunk(count: int) -> None:
        nonlocal copied_bytes
        copied_bytes += count
        if inventory.total_bytes:
            fraction = 0.10 + 0.82 * min(1.0, copied_bytes / inventory.total_bytes)
        else:
            fraction = 0.92
        report(
            fraction,
            f"Copying {copied_files + 1:,}/{len(inventory.files):,} files · "
            f"{_format_bytes(copied_bytes)} / {_format_bytes(inventory.total_bytes)}",
        )

    if partial.exists():
        raise ConflictError(
            "reserved import staging path already exists",
            code="resource_import_staging_collision",
            details={"phase": "copy", "partial_path": str(partial)},
        )

    try:
        if source.is_dir():
            _validate_inventory_directory(
                source, source_root=source, relative_path="."
            )
            partial.mkdir()
            for directory in inventory.directories:
                cancel_check()
                _validate_inventory_directory(
                    source / directory,
                    source_root=source,
                    relative_path=directory.as_posix(),
                )
                (partial / directory).mkdir(parents=True, exist_ok=True)
            directory_digest = hashlib.sha256()
            for item in inventory.files:
                cancel_check()
                file_digest = _copy_inventory_file(
                    item,
                    partial / item.relative,
                    source_root=source,
                    checkpoint=cancel_check,
                    on_chunk=on_chunk,
                )
                directory_digest.update(
                    item.relative.as_posix().encode("utf-8", errors="surrogatepass")
                )
                directory_digest.update(file_digest.encode("ascii"))
                copied_files += 1
            try:
                shutil.copystat(source, partial, follow_symlinks=False)
            except OSError:
                # Directory timestamps/ACL metadata are helpful but not part of
                # the content contract; file content and mode copies remain valid.
                pass
            checksum = directory_digest.hexdigest()
        else:
            item = inventory.files[0]
            checksum = _copy_inventory_file(
                item,
                partial,
                source_root=source.parent.resolve(),
                checkpoint=cancel_check,
                on_chunk=on_chunk,
            )
            copied_files = 1

        cancel_check()
        report(0.94, "Finalising atomic managed copy…")
        partial.rename(target)
        copied = True
    except BaseException as exc:
        cleanup_complete = _remove_partial(partial)
        if cleanup_complete:
            report(0.0, "Import stopped; partial managed copy was cleaned up")
            raise
        raise ConflictError(
            "resource import failed and its partial managed copy could not be removed",
            code="resource_import_cleanup_failed",
            details={
                "phase": "cleanup",
                "partial_path": str(partial),
                "cleanup_complete": False,
                "original_error": f"{type(exc).__name__}: {exc}",
                "hint": "Close processes using the path, then remove this reserved .partial entry.",
            },
        ) from exc

    computed_meta = dict(metadata or {})
    if source.is_dir():
        computed_meta["file_count"] = len(inventory.files)
    excluded = [
        *inventory.excluded_generated,
        *inventory.excluded_links,
        *inventory.excluded_special,
    ]
    if excluded:
        computed_meta["excluded_from_copy"] = excluded
    if inventory.excluded_links:
        computed_meta["excluded_links"] = list(inventory.excluded_links)
    if inventory.excluded_special:
        computed_meta["excluded_special_files"] = list(inventory.excluded_special)
    computed_meta["import"] = {
        "strategy": "atomic_managed_copy",
        "source_files": len(inventory.files),
        "source_directories": len(inventory.directories),
        "source_bytes": inventory.total_bytes,
        "copied_files": copied_files,
        "copied_bytes": copied_bytes,
        "excluded_count": len(excluded),
        "link_policy": "never_follow",
        "space_preflight": space,
    }
    report(0.97, "Registering completed managed copy…")
    try:
        return _insert(
            resource_id=resource_id,
            kind=kind,
            title=display_title,
            description=description,
            target=target,
            original_path=str(source),
            is_directory=source.is_dir(),
            size_bytes=inventory.total_bytes,
            checksum=checksum,
            project_id=project_id,
            paper_id=paper_id,
            metadata=computed_meta,
        )
    except BaseException:
        # A file is not considered imported unless its DB row exists.  Roll
        # back the final path too if the commit point fails.
        execute("DELETE FROM workbench_resources WHERE id=?", (resource_id,))
        if copied and not _remove_partial(target):
            log.error("could not remove unregistered managed resource %s", target)
        raise


def delete(resource_id: str, *, remove_files: bool = False) -> dict[str, Any]:
    resource = require(resource_id)
    target = absolute_path(resource)
    files_removed = False
    extraction_removed = False
    if remove_files and target.exists():
        library = get_paths().library_dir.resolve()
        if not target.is_relative_to(library) or target == library:
            raise ConflictError(f"refusing to remove unsafe resource path '{target}'")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        files_removed = True
    # Extracted text is a rebuildable cache owned by this exact resource.  It is
    # safe to remove even when the managed source file is intentionally kept.
    # Resolve both paths so a malformed id can never escape the cache directory.
    extraction_dir = (get_paths().cache_dir / "extracted").resolve()
    extraction_file = (extraction_dir / f"{resource_id}.txt").resolve()
    if extraction_file.is_relative_to(extraction_dir) and extraction_file.exists():
        extraction_file.unlink()
        extraction_removed = True
    execute("DELETE FROM workbench_resources WHERE id=?", (resource_id,))
    return {
        "deleted": True,
        "files_removed": files_removed,
        "extraction_removed": extraction_removed,
        "managed_path": str(target),
    }


def attach_papers(resource_id: str, paper_ids: list[str]) -> WorkbenchResource:
    """Record library rows created while ingesting a managed paper file."""
    resource = require(resource_id)
    metadata = dict(resource.metadata)
    metadata["paper_ids"] = list(dict.fromkeys(paper_ids))
    execute(
        "UPDATE workbench_resources SET paper_id=?, metadata=?, updated_at=? WHERE id=?",
        (
            paper_ids[0] if paper_ids else None,
            dumps(metadata),
            utc_now_iso(),
            resource_id,
        ),
    )
    return require(resource_id)


def attach_extraction(
    resource_id: str,
    text: str,
    extraction: dict[str, Any],
) -> WorkbenchResource:
    """Persist rebuildable extracted text below the workbench cache.

    Full manuscript text does not belong in a JSON database cell.  The resource
    row stores a portable relative pointer and diagnostics; semantic consumers
    can use the Paper abstract preview while future indexing can read the sidecar.
    """
    resource = require(resource_id)
    directory = get_paths().cache_dir / "extracted"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{resource_id}.txt"
    temporary = directory / f".{resource_id}-{os.getpid()}.tmp"
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)
    metadata = dict(resource.metadata)
    metadata["extraction"] = {
        **extraction,
        "text_path": str(target.relative_to(get_paths().home)).replace("\\", "/"),
        "rebuildable": True,
    }
    execute(
        "UPDATE workbench_resources SET metadata=?, updated_at=? WHERE id=?",
        (dumps(metadata), utc_now_iso(), resource_id),
    )
    return require(resource_id)


def stats() -> dict[str, int]:
    rows = query(
        "SELECT kind, COUNT(*) AS count FROM workbench_resources GROUP BY kind"
    )
    counts = {kind: 0 for kind in RESOURCE_KINDS}
    counts.update({str(row["kind"]): int(row["count"]) for row in rows})
    return counts
