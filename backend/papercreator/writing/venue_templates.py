"""Safe import of publisher/Overleaf LaTeX template ZIP archives.

PaperCreator ships original semantic outlines, not third-party class files.
Users can attach the exact venue package they are licensed to use; the archive
is treated as data and never compiled during import.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.errors import ConflictError, ValidationError
from ..core.util import new_id, slugify, utc_now_iso
from ..store import projects as projects_store

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 5_000


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(raw: str) -> PurePosixPath:
    value = raw.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValidationError(f"unsafe archive path: {raw}")
    if re.match(r"^[A-Za-z]:", value):
        raise ValidationError(f"unsafe archive path: {raw}")
    return path


def _inspect(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    expanded = 0
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValidationError("the selected file is not a readable ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValidationError(f"template archive has more than {MAX_ENTRIES} entries")
        for info in infos:
            relative = _safe_name(info.filename)
            if info.flag_bits & 0x1:
                raise ValidationError(f"template archive contains an encrypted entry: {info.filename}")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise ValidationError(f"template archive contains a symbolic link: {info.filename}")
            expanded += int(info.file_size)
            if expanded > MAX_EXPANDED_BYTES:
                raise ValidationError("expanded template archive exceeds 200 MB")
            if not info.is_dir():
                rows.append({
                    "path": str(relative),
                    "bytes": int(info.file_size),
                    "kind": relative.suffix.lower().lstrip(".") or "file",
                })
    return rows, expanded


def preview(path_value: str) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise ValidationError("choose a publisher or Overleaf .zip template archive")
    size = path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise ValidationError("template ZIP is limited to 100 MB")
    entries, expanded = _inspect(path)
    lower_names = [entry["path"].lower() for entry in entries]
    return {
        "source_path": str(path),
        "source_name": path.name,
        "source_sha256": _digest(path),
        "archive_bytes": size,
        "expanded_bytes": expanded,
        "file_count": len(entries),
        "entries": entries[:300],
        "entries_truncated": len(entries) > 300,
        "latex": {
            "tex_files": sum(name.endswith(".tex") for name in lower_names),
            "class_files": sum(name.endswith(".cls") for name in lower_names),
            "style_files": sum(name.endswith(".sty") for name in lower_names),
            "bib_files": sum(name.endswith(".bib") for name in lower_names),
        },
        "license_candidates": [
            entry["path"] for entry in entries
            if Path(entry["path"]).name.lower().startswith(("license", "licence", "copying"))
        ],
        "warnings": [] if any(name.endswith(".tex") for name in lower_names)
        else ["No .tex source was found; verify that this is a LaTeX venue package."],
    }


def apply(
    project_id: str,
    *,
    source_path: str,
    source_sha256: str,
    name: str,
    source_url: str = "",
    license_name: str = "",
    confirm_license: bool = False,
) -> dict[str, Any]:
    if not confirm_license:
        raise ConflictError("confirm that you may use and store this template package")
    if source_url and not source_url.startswith(("https://", "http://")):
        raise ValidationError("template source URL must use http or https")
    project = projects_store.require(project_id)
    inspected = preview(source_path)
    if inspected["source_sha256"] != source_sha256:
        raise ConflictError("the template ZIP changed after preview; preview it again")
    slug = slugify(name or Path(source_path).stem, max_length=60, fallback="venue-template")
    root = projects_store.project_root(project) / "assets" / "venue-templates"
    root.mkdir(parents=True, exist_ok=True)
    target = root / slug
    if target.exists():
        raise ConflictError(f"a venue template named '{slug}' already exists")
    staging = root / f".partial-{new_id('venue')}"
    staging.mkdir(parents=False, exist_ok=False)
    try:
        with zipfile.ZipFile(source_path) as archive:
            for info in archive.infolist():
                relative = _safe_name(info.filename)
                destination = staging.joinpath(*relative.parts)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
        os.replace(staging, target)
    except BaseException:
        import shutil

        shutil.rmtree(staging, ignore_errors=True)
        raise
    manifest_path = projects_store.project_root(project) / ".papercreator" / "venue-templates.json"
    item = {
        "id": new_id("venue"),
        "name": name.strip() or slug,
        "slug": slug,
        "path": str(target),
        "source_archive": str(Path(source_path).resolve()),
        "source_sha256": source_sha256,
        "source_url": source_url.strip(),
        "license": license_name.strip() or "user-confirmed; exact licence not recorded",
        "imported_at": utc_now_iso(),
        "file_count": inspected["file_count"],
    }
    try:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"schema_version": 1, "items": []}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            manifest = {"schema_version": 1, "items": []}
        manifest.setdefault("items", []).append(item)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, manifest_path)
    except BaseException:
        import shutil

        shutil.rmtree(target, ignore_errors=True)
        raise
    return {"template": item, "manifest": str(manifest_path), "warnings": inspected["warnings"]}
