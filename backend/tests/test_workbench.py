"""Single-root workbench layout and classified resource imports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(temp_home: Path):
    from papercreator.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


class TestWorkbenchLayout:
    def test_managed_layout_is_complete(self, temp_home: Path):
        from papercreator.core.paths import get_paths

        paths = get_paths()
        # Windows runners may expose the temp root through an 8.3 alias such as
        # RUNNER~1.  The application intentionally resolves that alias, so compare
        # against the canonical fixture path rather than its original spelling.
        expected_home = temp_home.resolve()
        assert paths.home == expected_home
        assert paths.workspace == expected_home / "projects"
        expected = (
            paths.ideas_dir,
            paths.reference_papers_dir,
            paths.own_papers_dir,
            paths.code_projects_dir,
            paths.datasets_dir,
            paths.supplementary_dir,
            paths.inbox_dir,
            paths.backups_dir,
        )
        assert all(path.is_dir() for path in expected)
        manifest = json.loads(paths.manifest_file.read_text(encoding="utf-8"))
        assert manifest["format"] == "papercreator-workbench"
        assert manifest["schema_version"] >= 1

    def test_workbench_environment_wraps_hidden_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, temp_home: Path
    ):
        from papercreator.core import paths

        monkeypatch.delenv("PAPERCREATOR_HOME", raising=False)
        monkeypatch.setenv("PAPERCREATOR_WORKBENCH", str(tmp_path))
        paths.reset_paths()
        resolved = paths.get_paths()
        assert resolved.workbench_root == tmp_path.resolve()
        assert resolved.home == tmp_path.resolve() / ".papercreator"
        assert resolved.workspace == resolved.home / "projects"
        # Restore the session fixture's layout before another test touches DB.
        monkeypatch.setenv("PAPERCREATOR_HOME", str(temp_home))
        monkeypatch.delenv("PAPERCREATOR_WORKBENCH", raising=False)
        paths.reset_paths()

    def test_info_explains_every_category(self, client: TestClient):
        response = client.get("/api/workbench")
        assert response.status_code == 200
        payload = response.json()
        assert payload["managed_directory_name"] == ".papercreator"
        assert payload["rules"]["imports_are_copied"] is True
        kinds = {entry["kind"] for entry in payload["categories"]}
        assert kinds == {
            "idea",
            "reference_paper",
            "own_paper",
            "code_project",
            "dataset",
            "supplementary",
            "inbox",
        }
        assert all(entry["description_zh"] for entry in payload["categories"])


class TestManagedResources:
    def test_idea_creates_markdown_and_library_seed(self, client: TestClient):
        response = client.post(
            "/api/workbench/resources",
            json={
                "kind": "idea",
                "title": "Causal graph agents",
                "content": "Use cooperating agents to audit causal graph discovery.",
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        resource = payload["resource"]
        assert resource["kind"] == "idea"
        assert resource["managed_path"].startswith("library/ideas/")
        assert Path(resource["path"]).read_text(encoding="utf-8").startswith(
            "# Causal graph agents"
        )
        assert payload["papers"][0]["origin"] == "idea"
        deleted = client.delete(
            f"/api/workbench/resources/{resource['id']}",
            params={"remove_files": True},
        )
        assert deleted.json()["files_removed"] is True

    def test_bibliography_is_parsed_from_managed_copy(
        self, client: TestClient, tmp_path: Path
    ):
        source = tmp_path / "references.bib"
        source.write_text(
            "@article{managed2026, title={Managed Workbench Import}, "
            "author={Ada Lovelace}, year={2026}}\n",
            encoding="utf-8",
        )
        response = client.post(
            "/api/workbench/resources",
            json={"kind": "reference_paper", "source_path": str(source)},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        resource = payload["resource"]
        managed = Path(resource["path"])
        assert managed.exists() and managed != source
        assert managed.read_bytes() == source.read_bytes()
        assert resource["original_path"] == str(source.resolve())
        assert payload["papers"][0]["title"] == "Managed Workbench Import"
        assert payload["papers"][0]["origin"] == "manual"
        client.delete(
            f"/api/workbench/resources/{resource['id']}",
            params={"remove_files": True},
        )

    def test_own_text_manuscript_is_extracted_from_managed_copy(
        self, client: TestClient, tmp_path: Path, temp_home: Path
    ):
        source = tmp_path / "my-paper.txt"
        source.write_text(
            "Auditable Multi-Agent Writing\n\n"
            "This manuscript studies reproducible evidence and local-first review.",
            encoding="utf-8",
        )
        response = client.post(
            "/api/workbench/resources",
            json={"kind": "own_paper", "source_path": str(source)},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        resource = payload["resource"]
        paper = payload["papers"][0]
        assert paper["origin"] == "own_paper"
        assert paper["title"] == "Auditable Multi-Agent Writing"
        assert "reproducible evidence" in paper["abstract"]
        extraction = resource["metadata"]["extraction"]
        assert extraction["method"] == "plain-text"
        extracted_path = temp_home / extraction["text_path"]
        assert extracted_path.read_text(encoding="utf-8").startswith(
            "Auditable Multi-Agent Writing"
        )
        client.delete(
            f"/api/workbench/resources/{resource['id']}",
            params={"remove_files": True},
        )

    def test_code_copy_excludes_dependencies_and_secret_env(
        self, client: TestClient, tmp_path: Path
    ):
        source = tmp_path / "algorithm-code"
        (source / "src").mkdir(parents=True)
        (source / "node_modules" / "pkg").mkdir(parents=True)
        (source / "src" / "model.py").write_text("print('model')\n", encoding="utf-8")
        (source / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
        (source / ".env").write_text("SECRET=do-not-copy\n", encoding="utf-8")
        (source / ".env.example").write_text("SECRET=\n", encoding="utf-8")
        response = client.post(
            "/api/workbench/resources",
            json={"kind": "code_project", "source_path": str(source)},
        )
        assert response.status_code == 200, response.text
        resource = response.json()["resource"]
        managed = Path(resource["path"])
        assert (managed / "src" / "model.py").is_file()
        assert (managed / ".env.example").is_file()
        assert not (managed / ".env").exists()
        assert not (managed / "node_modules").exists()
        assert resource["metadata"]["excluded_from_copy"]
        client.delete(
            f"/api/workbench/resources/{resource['id']}",
            params={"remove_files": True},
        )

    def test_forgetting_record_preserves_managed_file_by_default(
        self, client: TestClient, tmp_path: Path
    ):
        source = tmp_path / "notes.txt"
        source.write_text("keep me\n", encoding="utf-8")
        created = client.post(
            "/api/workbench/resources",
            json={"kind": "supplementary", "source_path": str(source)},
        ).json()["resource"]
        managed = Path(created["path"])
        result = client.delete(f"/api/workbench/resources/{created['id']}").json()
        assert result["files_removed"] is False
        assert managed.exists(), "deleting a DB record must not silently delete files"
        managed.unlink()

    def test_last_project_is_kept_in_workbench_db(self, client: TestClient, project):
        response = client.patch(
            "/api/workbench/state", json={"last_project_id": project.id}
        )
        assert response.status_code == 200
        assert client.get("/api/workbench").json()["last_project_id"] == project.id
        client.patch("/api/workbench/state", json={"last_project_id": ""})


class TestSafeDirectoryImports:
    def test_background_import_is_atomic_and_auditable(
        self, client: TestClient, tmp_path: Path
    ):
        from papercreator.core.jobs import manager

        source = tmp_path / "large dataset"
        (source / "split" / "empty").mkdir(parents=True)
        (source / "README.txt").write_text("dataset notes\n", encoding="utf-8")
        (source / "split" / "train.csv").write_text(
            "x,y\n1,2\n", encoding="utf-8"
        )

        accepted = client.post(
            "/api/workbench/resources/import",
            json={"kind": "dataset", "source_path": str(source)},
        )
        assert accepted.status_code == 202, accepted.text
        job = manager.wait(accepted.json()["job_id"], timeout=10)
        assert job["status"] == "done", job
        resource = job["result"]["resource"]
        managed = Path(resource["path"])
        assert (managed / "README.txt").read_text(encoding="utf-8") == "dataset notes\n"
        assert (managed / "split" / "train.csv").is_file()
        assert (managed / "split" / "empty").is_dir()
        audit = resource["metadata"]["import"]
        assert audit["strategy"] == "atomic_managed_copy"
        assert audit["source_files"] == 2
        assert audit["copied_bytes"] == resource["size_bytes"]
        assert audit["link_policy"] == "never_follow"
        assert not list(managed.parent.glob(".partial-res_*"))
        client.delete(
            f"/api/workbench/resources/{resource['id']}",
            params={"remove_files": True},
        )

    def test_cancellation_cleans_staging_without_ready_record(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        from papercreator.core.jobs import JobCancelled
        from papercreator.core.paths import get_paths
        from papercreator.store import resources

        source = tmp_path / "cancel-source"
        source.mkdir()
        (source / "large.bin").write_bytes(b"abcdefgh" * 64)
        monkeypatch.setattr(resources, "_COPY_CHUNK_BYTES", 16)
        should_cancel = False

        def progress(_fraction: float, message: str) -> None:
            nonlocal should_cancel
            if message.startswith("Copying"):
                should_cancel = True

        def checkpoint() -> None:
            if should_cancel:
                raise JobCancelled()

        with pytest.raises(JobCancelled):
            resources.import_path(
                str(source),
                kind="dataset",
                progress=progress,
                checkpoint=checkpoint,
            )

        assert not [
            item
            for item in resources.list_resources(kind="dataset")
            if item.original_path == str(source.resolve())
        ]
        assert not list(get_paths().datasets_dir.glob(".partial-res_*"))

    def test_insufficient_space_fails_before_staging(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        from collections import namedtuple

        from papercreator.core.errors import ValidationError
        from papercreator.core.paths import get_paths
        from papercreator.store import resources

        source = tmp_path / "too-large"
        source.mkdir()
        (source / "data.bin").write_bytes(b"0123456789")
        Usage = namedtuple("Usage", "total used free")
        monkeypatch.setattr(
            resources.shutil, "disk_usage", lambda _path: Usage(100, 99, 1)
        )

        with pytest.raises(ValidationError) as raised:
            resources.import_path(str(source), kind="dataset")
        assert raised.value.code == "resource_import_insufficient_space"
        assert raised.value.details["free_bytes"] == 1
        assert not list(get_paths().datasets_dir.glob(".partial-res_*"))

    def test_source_change_aborts_mixed_snapshot(
        self, tmp_path: Path
    ):
        from papercreator.core.errors import ConflictError
        from papercreator.core.paths import get_paths
        from papercreator.store import resources

        source = tmp_path / "changing-source"
        source.mkdir()
        changing = source / "rows.csv"
        changing.write_text("a,b\n", encoding="utf-8")
        changed = False

        def progress(_fraction: float, message: str) -> None:
            nonlocal changed
            if not changed and message.startswith("Source scan complete"):
                changing.write_text("a,b\n1,2\n", encoding="utf-8")
                changed = True

        with pytest.raises(ConflictError) as raised:
            resources.import_path(
                str(source), kind="dataset", progress=progress
            )
        assert raised.value.code == "resource_import_source_changed"
        assert not list(get_paths().datasets_dir.glob(".partial-res_*"))

    def test_empty_directory_change_is_detected_before_commit(self, tmp_path: Path):
        from papercreator.core.errors import ConflictError
        from papercreator.core.paths import get_paths
        from papercreator.store import resources

        source = tmp_path / "changing-empty-directory"
        empty = source / "empty"
        empty.mkdir(parents=True)
        changed = False

        def progress(_fraction: float, message: str) -> None:
            nonlocal changed
            if not changed and message.startswith("Source scan complete"):
                empty.rmdir()
                changed = True

        with pytest.raises(ConflictError) as raised:
            resources.import_path(str(source), kind="dataset", progress=progress)
        assert raised.value.code == "resource_import_source_changed"
        assert not list(get_paths().datasets_dir.glob(".partial-res_*"))

    def test_nested_reparse_entries_are_never_followed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        from papercreator.store import resources

        source = tmp_path / "links-source"
        source.mkdir()
        (source / "normal.txt").write_text("safe", encoding="utf-8")
        (source / "linked.txt").write_text("must not copy", encoding="utf-8")
        original = resources._is_reparse_or_symlink

        def classify(path: Path, info=None):
            if path.name == "linked.txt":
                return True
            return original(path, info)

        monkeypatch.setattr(resources, "_is_reparse_or_symlink", classify)
        resource = resources.import_path(str(source), kind="code_project")
        managed = resources.absolute_path(resource)
        assert (managed / "normal.txt").is_file()
        assert not (managed / "linked.txt").exists()
        assert resource.metadata["excluded_links"] == ["linked.txt"]
        resources.delete(resource.id, remove_files=True)

    def test_startup_cleanup_only_removes_reserved_partial_names(self):
        from papercreator.core.paths import get_paths
        from papercreator.store import resources

        category = get_paths().datasets_dir
        stale = category / ".partial-res_1234567890abcdef"
        stale.mkdir()
        (stale / "orphan.bin").write_bytes(b"partial")
        user_named = category / ".partial-user-data"
        user_named.mkdir()

        result = resources.cleanup_stale_partials()
        assert result == {"removed": 1, "failed": []}
        assert not stale.exists()
        assert user_named.is_dir()
        user_named.rmdir()

    def test_background_endpoint_rejects_regular_files(
        self, client: TestClient, tmp_path: Path
    ):
        source = tmp_path / "small.txt"
        source.write_text("small", encoding="utf-8")
        response = client.post(
            "/api/workbench/resources/import",
            json={"kind": "dataset", "source_path": str(source)},
        )
        assert response.status_code == 422

    def test_background_job_cancel_endpoint_leaves_no_resource(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        from papercreator.core.jobs import manager
        from papercreator.core.paths import get_paths
        from papercreator.store import resources

        source = tmp_path / "cancel-through-api"
        source.mkdir()
        (source / "many-chunks.bin").write_bytes(b"x" * (256 * 1024))
        monkeypatch.setattr(resources, "_COPY_CHUNK_BYTES", 1)

        accepted = client.post(
            "/api/workbench/resources/import",
            json={"kind": "dataset", "source_path": str(source)},
        )
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]
        cancelled = client.post(f"/api/system/jobs/{job_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["requested"] is True
        job = manager.wait(job_id, timeout=10)
        assert job["status"] == "cancelled", job
        assert not [
            item
            for item in resources.list_resources(kind="dataset")
            if item.original_path == str(source.resolve())
        ]
        assert not list(get_paths().datasets_dir.glob(".partial-res_*"))
