"""Store layer tests: paper merging, projects, documents, snapshots.

The merge logic gets the most attention here because it is where cross-provider
data actually combines, and a wrong merge silently corrupts the library - the
kind of bug that is only noticed much later, in an export.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from papercreator.core.errors import ConflictError
from papercreator.core.models import Author, Paper


class TestPaperMerge:
    def test_same_doi_produces_one_row(self, temp_home):
        from papercreator.store import papers as papers_store

        first = papers_store.upsert(
            Paper(title="A Paper", doi="10.1/x", abstract="short",
                  source_providers=["arxiv"], year=2020)
        )
        second = papers_store.upsert(
            Paper(title="A Paper", doi="https://doi.org/10.1/X",
                  abstract="a considerably longer abstract with more detail",
                  citation_count=99, venue="NeurIPS",
                  source_providers=["openalex"], year=2020)
        )
        assert second.id == first.id
        papers_store.delete(first.id)

    def test_merge_takes_the_best_of_each_source(self, temp_home):
        """arXiv has the abstract, OpenAlex the citations, DBLP the venue."""
        from papercreator.store import papers as papers_store

        papers_store.upsert(
            Paper(title="Merge Target", doi="10.2/y",
                  abstract="the full abstract text from the preprint server",
                  source_providers=["arxiv"], year=2021)
        )
        merged = papers_store.upsert(
            Paper(title="Merge Target", doi="10.2/y", abstract="truncated",
                  citation_count=1234, venue="ICML",
                  keywords=["graph"], source_providers=["openalex"], year=2021)
        )
        assert merged.abstract.startswith("the full abstract"), "longer abstract wins"
        assert merged.citation_count == 1234, "citations fill in"
        assert merged.venue == "ICML", "venue fills in"
        assert set(merged.source_providers) == {"arxiv", "openalex"}
        papers_store.delete(merged.id)

    def test_user_fields_survive_reimport(self, temp_home):
        """A provider refresh must never overwrite the user's own triage."""
        from papercreator.store import papers as papers_store

        original = papers_store.upsert(
            Paper(title="User Data", doi="10.3/z", source_providers=["arxiv"])
        )
        papers_store.update_fields(
            original.id, notes="my note", rating=5, read_status="read",
            tags=["important"],
        )
        papers_store.upsert(
            Paper(title="User Data", doi="10.3/z", citation_count=10,
                  source_providers=["openalex"])
        )
        after = papers_store.require(original.id)
        assert after.notes == "my note"
        assert after.rating == 5
        assert after.read_status == "read"
        assert after.tags == ["important"]
        assert after.citation_count == 10, "provider data still merges"
        papers_store.delete(original.id)

    def test_year_conflict_prefers_the_earlier_and_records_it(self, temp_home):
        """Observed live: OpenAlex dates the 2017 transformer paper as 2025."""
        from papercreator.store.papers import merge_papers

        existing = Paper(title="T", year=2025, source_providers=["openalex"])
        incoming = Paper(title="T", year=2017, source_providers=["s2"])
        merged = merge_papers(existing, incoming)
        assert merged.year == 2017
        assert merged.raw["conflicts"]["year"] == [2017, 2025]

    def test_one_year_difference_is_not_a_conflict(self):
        """Preprint vs publication normally differs by a year; not worth churning."""
        from papercreator.store.papers import merge_papers

        merged = merge_papers(
            Paper(title="T", year=2022), Paper(title="T", year=2023)
        )
        assert merged.year == 2022
        assert "conflicts" not in merged.raw

    def test_origin_is_not_demoted(self):
        """A retrieved hit matching the user's own idea must not overwrite it."""
        from papercreator.store.papers import merge_papers

        merged = merge_papers(
            Paper(title="My Idea", origin="idea"),
            Paper(title="My Idea", origin="retrieved"),
        )
        assert merged.origin == "idea"


class TestPaperId:
    def test_id_is_derived_from_the_strongest_identifier(self):
        assert Paper(title="x", doi="10.1/a").compute_id().startswith("doi_")
        assert Paper(title="x", arxiv_id="2301.1").compute_id().startswith("arx_")
        assert Paper(title="x", pmid="123").compute_id().startswith("pmid_")
        assert Paper(title="x").compute_id().startswith("t_")

    def test_same_doi_gives_the_same_id_regardless_of_form(self):
        a = Paper(title="x", doi="10.1/ABC").ensure_id()
        b = Paper(title="different title", doi="https://doi.org/10.1/abc").ensure_id()
        assert a.id == b.id


class TestKeywordNormalisation:
    def test_packed_keyword_strings_are_split(self):
        """Observed live in DOAJ data: a single pipe-joined 'keyword'."""
        paper = Paper(
            title="x",
            keywords=["molecular property prediction|causal|graph neural network"],
        )
        assert len(paper.keywords) == 3
        assert "causal" in paper.keywords

    def test_a_phrase_containing_a_comma_is_not_split(self):
        paper = Paper(title="x", keywords=["machine learning, applied"])
        assert paper.keywords == ["machine learning, applied"]

    def test_duplicates_are_removed_case_insensitively(self):
        paper = Paper(title="x", keywords=["GNN", "gnn", "GNN"])
        assert len(paper.keywords) == 1


class TestFullTextSearch:
    def test_finds_by_title(self, stored_papers):
        from papercreator.store import papers as papers_store

        result = papers_store.search_library(text="molecular property")
        assert result["total"] >= 1

    def test_punctuation_in_the_query_does_not_break_fts5(self, stored_papers):
        """FTS5 treats -, *, : and " as syntax; a raw paper title contains them."""
        from papercreator.store import papers as papers_store

        for query in ['GNN-based: "quoted" NEAR*', "a - b", '"', "*", "AND OR"]:
            result = papers_store.search_library(text=query)
            assert isinstance(result["total"], int), f"query {query!r} raised"


class TestProjects:
    def test_scaffolds_the_directory(self, project):
        from pathlib import Path

        root = Path(project.path)
        assert root.is_dir()
        for name in ("manuscript", "references", "assets", "exports", "analysis"):
            assert (root / name).is_dir(), f"{name}/ missing"
        assert (root / "project.json").is_file(), "recovery manifest missing"

    def test_slug_collisions_are_avoided(self, temp_home):
        from papercreator.store import projects as projects_store

        first = projects_store.create(title="Duplicate Name", git_enabled=False)
        second = projects_store.create(title="Duplicate Name", git_enabled=False)
        assert first.slug != second.slug
        projects_store.delete(first.id, remove_files=True)
        projects_store.delete(second.id, remove_files=True)

    def test_delete_refuses_outside_the_workspace(self, temp_home):
        """Guards against a relocated project deleting an unrelated directory."""
        import tempfile
        from pathlib import Path

        from papercreator.core.errors import ConflictError
        from papercreator.store import projects as projects_store

        outside = Path(tempfile.mkdtemp(prefix="pc_outside_"))
        created = projects_store.create(title="Relocated", git_enabled=False)
        projects_store.relocate(created.id, str(outside))
        with pytest.raises(ConflictError, match="outside the workspace"):
            projects_store.delete(created.id, remove_files=True)
        projects_store.delete(created.id, remove_files=False)


class TestDocuments:
    def test_sections_round_trip_through_disk(self, project):
        from papercreator.store import documents as documents_store

        document = documents_store.primary_document(project.id)
        documents_store.create_section(
            document.id, key="introduction", title="Introduction",
            content="The body text.", content_zh="正文内容。",
        )
        documents_store.flush_document_to_disk(document.id)

        # A DB edit after the last flush must not be silently replaced by disk.
        section = documents_store.get_section_by_key(document.id, "introduction")
        documents_store.update_section(section.id, content="changed in the database")
        with pytest.raises(ConflictError) as caught:
            documents_store.reindex_from_disk(document.id)
        assert caught.value.code == "manuscript_sync_conflict"

        result = documents_store.reindex_from_disk(document.id, force=True)
        assert result["updated"] >= 1
        assert result["safety_backup"]["side"] == "database"
        assert Path(result["safety_backup"]["path"]).is_dir()
        restored = documents_store.get_section_by_key(document.id, "introduction")
        assert restored.content == "The body text.", "disk must win on reindex"
        assert restored.content_zh == "正文内容。", "paired language must survive"
        assert result["sync"]["state"] == "in_sync"

    def test_external_edit_blocks_flush_and_force_preserves_it(self, project):
        from pathlib import Path

        from papercreator.store import documents as documents_store

        document = documents_store.primary_document(project.id)
        section = documents_store.create_section(
            document.id, key="method", title="Method", content="database text"
        )
        first = documents_store.flush_document_to_disk(document.id)
        manuscript = Path(first["path"])
        section_file = manuscript / documents_store.section_filename(
            section, document.format
        )
        section_file.write_text("# Method\n\nexternal editor text\n", encoding="utf-8")

        status = documents_store.sync_status(document.id)
        assert status["state"] == "disk_changed"
        assert status["can_flush"] is False
        assert status["can_reindex"] is True
        with pytest.raises(ConflictError):
            documents_store.flush_document_to_disk(document.id)
        assert "external editor text" in section_file.read_text(encoding="utf-8")

        forced = documents_store.flush_document_to_disk(document.id, force=True)
        backup = Path(forced["safety_backup"]["path"]) / "disk" / section_file.name
        assert "external editor text" in backup.read_text(encoding="utf-8")
        assert "database text" in section_file.read_text(encoding="utf-8")
        assert forced["sync"]["state"] == "in_sync"

    def test_disjoint_database_and_disk_section_changes_merge_safely(self, project):
        from pathlib import Path

        from papercreator.store import documents as documents_store

        document = documents_store.primary_document(project.id)
        introduction = documents_store.create_section(
            document.id, key="merge-introduction", title="Introduction",
            content="Introduction baseline.",
        )
        method = documents_store.create_section(
            document.id, key="merge-method", title="Method",
            content="Method baseline.",
        )
        flushed = documents_store.flush_document_to_disk(document.id)
        documents_store.update_section(
            introduction.id, content="Introduction changed in database."
        )
        method_file = Path(flushed["path"]) / documents_store.section_filename(
            method, document.format
        )
        method_file.write_text(
            "# Method\n\nMethod changed in external editor.\n", encoding="utf-8"
        )

        status = documents_store.sync_status(document.id)
        assert status["state"] == "diverged"
        assert status["section_baseline_present"] is True
        assert status["section_changes"]["database"] == ["merge-introduction"]
        assert status["section_changes"]["disk"] == ["merge-method"]
        assert status["section_changes"]["conflicts"] == []
        assert status["can_auto_merge"] is True

        merged = documents_store.merge_disjoint_changes(
            document.id, preview_token=status["merge_preview_token"]
        )
        assert merged["merged_from_database"] == ["merge-introduction"]
        assert merged["merged_from_disk"] == ["merge-method"]
        assert len(merged["safety_backups"]) == 2
        assert all(Path(item["path"]).is_dir() for item in merged["safety_backups"])
        assert documents_store.get_section_by_key(
            document.id, "merge-introduction"
        ).content == "Introduction changed in database."
        assert documents_store.get_section_by_key(
            document.id, "merge-method"
        ).content == "Method changed in external editor."
        assert "Introduction changed in database" in (
            Path(flushed["path"]) / documents_store.section_filename(
                introduction, document.format
            )
        ).read_text(encoding="utf-8")
        assert merged["sync"]["state"] == "in_sync"

        documents_store.update_section(
            introduction.id, content="Second database edit."
        )
        introduction_file = Path(flushed["path"]) / documents_store.section_filename(
            introduction, document.format
        )
        introduction_file.write_text(
            "# Introduction\n\nSecond external edit.\n", encoding="utf-8"
        )
        overlap = documents_store.sync_status(document.id)
        assert overlap["state"] == "diverged"
        assert overlap["section_changes"]["conflicts"] == ["merge-introduction"]
        assert overlap["can_auto_merge"] is False
        with pytest.raises(ConflictError) as caught:
            documents_store.merge_disjoint_changes(
                document.id, preview_token=overlap["merge_preview_token"]
            )
        assert caught.value.code == "manuscript_merge_not_disjoint"

    def test_legacy_divergence_requires_an_explicit_side(self, project):
        from pathlib import Path

        from papercreator.store import documents as documents_store

        document = documents_store.primary_document(project.id)
        section = documents_store.create_section(
            document.id, key="results", title="Results", content="database result"
        )
        flushed = documents_store.flush_document_to_disk(document.id)
        Path(flushed["sync"]["state_file"]).unlink()
        section_file = Path(flushed["path"]) / documents_store.section_filename(
            section, document.format
        )
        section_file.write_text("# Results\n\ndisk result\n", encoding="utf-8")

        status = documents_store.sync_status(document.id)
        assert status["state"] == "untracked_divergence"
        assert status["can_flush"] is False
        assert status["can_reindex"] is False
        with pytest.raises(ConflictError):
            documents_store.flush_document_to_disk(document.id)
        with pytest.raises(ConflictError):
            documents_store.reindex_from_disk(document.id)

    def test_bilingual_separator_parses_back(self):
        from papercreator.store import documents as documents_store

        rendered = documents_store.render_section_file(
            documents_store._row_to_section(
                {
                    "id": "s", "document_id": "d", "parent_id": None,
                    "key": "k", "title": "Title", "title_zh": "标题",
                    "ordering": 10, "level": 1, "content": "English body.",
                    "content_zh": "中文正文。", "status": "drafted",
                    "target_words": 0, "word_count": 2, "guidance": "",
                    "cited_paper_ids": "[]", "meta": "{}",
                    "created_at": "", "updated_at": "",
                }
            ),
            "markdown",
        )
        title, body, body_zh = documents_store.parse_section_file(rendered)
        assert title == "Title"
        assert body == "English body."
        assert body_zh == "中文正文。"

    def test_flush_prunes_only_its_own_files(self, project):
        """A user's own file in the manuscript folder must not be deleted."""
        from pathlib import Path

        from papercreator.store import documents as documents_store

        document = documents_store.primary_document(project.id)
        documents_store.create_section(document.id, key="intro", title="Intro",
                                       content="text")
        result = documents_store.flush_document_to_disk(document.id)
        manuscript = Path(result["path"])
        stray = manuscript / "my-own-notes.md"
        stray.write_text("keep me", encoding="utf-8")

        documents_store.flush_document_to_disk(document.id)
        assert stray.is_file(), "a non-generated file must not be pruned"


class TestManuscriptMutationGuards:
    def test_agent_persist_stops_before_database_write_on_disk_change(self, project):
        from papercreator.agents.base import Blackboard
        from papercreator.agents.orchestrator import Orchestrator
        from papercreator.store import documents as documents_store
        from papercreator.writing import manuscript

        manuscript.apply_template(project.id, "generic")
        document = documents_store.primary_document(project.id)
        section = documents_store.get_section_by_key(document.id, "introduction")
        assert section is not None
        section_file = (
            documents_store.document_dir(document)
            / documents_store.section_filename(section, document.format)
        )
        section_file.write_text(
            "# Introduction\n\nexternal editor text\n", encoding="utf-8"
        )
        board = Blackboard(
            project=project,
            sections={"introduction": "agent-generated text"},
            modified_section_keys={"introduction"},
            outline=[{"key": "introduction", "title": "Introduction"}],
            extra={"document_id": document.id},
        )
        orchestrator = object.__new__(Orchestrator)
        orchestrator.project_id = project.id
        orchestrator.warnings = []

        with pytest.raises(ConflictError) as caught:
            orchestrator._persist(board)
        assert caught.value.code == "manuscript_sync_conflict"
        assert documents_store.require_section(section.id).content == ""
        assert "external editor text" in section_file.read_text(encoding="utf-8")

    def test_agent_persist_writes_only_sections_modified_by_the_run(self, project):
        from papercreator.agents.base import Blackboard
        from papercreator.agents.orchestrator import Orchestrator
        from papercreator.store import documents as documents_store
        from papercreator.writing import manuscript

        manuscript.apply_template(project.id, "generic")
        document = documents_store.primary_document(project.id)
        abstract = documents_store.get_section_by_key(document.id, "abstract")
        introduction = documents_store.get_section_by_key(document.id, "introduction")
        assert abstract is not None and introduction is not None
        documents_store.update_section(abstract.id, content="existing author abstract")
        documents_store.flush_document_to_disk(document.id)
        board = Blackboard(
            project=project,
            sections={
                "abstract": "existing author abstract",
                "introduction": "new agent introduction",
            },
            translations={"introduction": "新的智能体引言"},
            modified_section_keys={"introduction"},
            outline=[
                {"key": "abstract", "title": "Abstract", "ordering": 10},
                {"key": "introduction", "title": "Introduction", "ordering": 20},
            ],
            extra={"document_id": document.id},
        )
        orchestrator = object.__new__(Orchestrator)
        orchestrator.project_id = project.id
        orchestrator.warnings = []

        result = orchestrator._persist(board)

        assert result["sections"] == 1
        assert documents_store.require_section(abstract.id).content == "existing author abstract"
        saved = documents_store.require_section(introduction.id)
        assert saved.content == "new agent introduction"
        assert saved.content_zh == "新的智能体引言"


class TestSnapshots:
    def test_diff_against_current_detects_a_change(self, project):
        from papercreator.store import documents as documents_store
        from papercreator.store import snapshots as snapshots_store

        document = documents_store.primary_document(project.id)
        section = documents_store.create_section(
            document.id, key="method", title="Method", content="original text"
        )
        snapshot = snapshots_store.capture(project.id, label="before")
        documents_store.update_section(section.id, content="revised text entirely")

        diff = snapshots_store.diff_snapshots(snapshot["id"], "current")
        assert diff["summary"]["modified"] == 1
        changed = [s for s in diff["sections"] if s["status"] == "modified"]
        assert changed and "revised" in changed[0]["diff"]

    def test_restore_puts_the_old_text_back(self, project):
        from papercreator.store import documents as documents_store
        from papercreator.store import snapshots as snapshots_store

        document = documents_store.primary_document(project.id)
        section = documents_store.create_section(
            document.id, key="results", title="Results", content="first version"
        )
        snapshot = snapshots_store.capture(project.id, label="v1")
        documents_store.update_section(section.id, content="second version")
        snapshots_store.restore(snapshot["id"])
        assert documents_store.require_section(section.id).content == "first version"

    def test_prune_keeps_manual_snapshots(self, project):
        from papercreator.store import snapshots as snapshots_store

        for index in range(6):
            snapshots_store.capture(project.id, label=f"auto {index}", kind="auto")
        snapshots_store.capture(project.id, label="important", kind="manual")
        snapshots_store.prune(project.id, keep=2)
        remaining = snapshots_store.list_snapshots(project.id)
        labels = [entry["label"] for entry in remaining]
        assert "important" in labels, "a manual snapshot must never be pruned"


class TestRunAudit:
    def test_multiple_llm_calls_are_appended_to_one_step(self, temp_home):
        from papercreator.store import runs as runs_store

        run_id = runs_store.create_run(project_id="", pipeline="section")
        step_id = runs_store.create_step(
            run_id, agent="reader", title="Read papers"
        )
        runs_store.append_step_prompt(step_id, "SYSTEM\nreader\n\nUSER\npaper one")
        runs_store.append_step_prompt(step_id, "SYSTEM\nreader\n\nUSER\npaper two")

        prompt = runs_store.get_step(step_id)["prompt"]
        assert prompt.startswith("SYSTEM\nreader\n\nUSER\npaper one")
        assert prompt.endswith("SYSTEM\nreader\n\nUSER\npaper two")
        assert prompt.count("===== NEXT LLM CALL =====") == 1
        runs_store.delete_run(run_id)


class TestEmbeddingCache:
    def test_unsaved_papers_do_not_break_the_cache_write(self, temp_home):
        """Regression: a foreign-key failure used to abort the whole batch, which
        callers then treated as a backend failure and silently downgraded to
        TF-IDF."""
        from papercreator.store import analyses as analyses_store

        stored = analyses_store.put_embeddings_bulk(
            [("does-not-exist", "test:model", b"\x00\x00\x80?", 1, "hash")]
        )
        assert stored == 0, "the row is skipped, not attempted"

    def test_round_trip_for_a_saved_paper(self, stored_papers):
        import numpy as np

        from papercreator.store import analyses as analyses_store

        vector = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        paper_id = stored_papers[0].id
        analyses_store.put_embeddings_bulk(
            [(paper_id, "test:model", vector.tobytes(), 3, "hash-1")]
        )
        raw = analyses_store.get_embedding(paper_id, "test:model", "hash-1")
        assert raw is not None
        assert np.allclose(np.frombuffer(raw, dtype=np.float32), vector)

    def test_stale_text_hash_misses(self, stored_papers):
        from papercreator.store import analyses as analyses_store

        paper_id = stored_papers[1].id
        analyses_store.put_embeddings_bulk(
            [(paper_id, "test:model", b"\x00\x00\x80?", 1, "old-hash")]
        )
        assert analyses_store.get_embedding(paper_id, "test:model", "new-hash") is None
