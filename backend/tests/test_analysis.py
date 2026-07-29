"""Analysis tests: embeddings, reduction, clustering, keywords, heatmap, gaps.

The tests use the ten synthetic papers from ``conftest``, which span three
deliberately separable topics (molecular GNNs, language models, computer vision).
That makes a clustering failure a real failure rather than noise, and it keeps the
suite deterministic and offline.
"""

from __future__ import annotations

import numpy as np
import pytest


class TestEmbeddings:
    def test_a_backend_is_always_available(self, temp_home):
        """The landscape must work on a plain `pip install`, with no model download."""
        from papercreator.analysis import embeddings

        backend, model_key, _ = embeddings.resolve_backend("auto")
        assert backend in ("sentence-transformers", "llm", "tfidf", "hashing")
        assert model_key

    def test_tfidf_produces_normalised_vectors(self, sample_papers):
        from papercreator.analysis import embeddings

        result = embeddings.embed_papers(sample_papers, backend="tfidf", use_cache=False)
        assert result.vectors.shape[0] == len(sample_papers)
        assert result.dim > 0
        assert result.corpus_relative is True
        norms = np.linalg.norm(result.vectors, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-4), "consumers assume unit vectors"

    def test_hashing_always_works(self, sample_papers):
        """Hashing is fixed per text, so it is portable as well as dependency-free."""
        from papercreator.analysis import embeddings

        result = embeddings.embed_papers(sample_papers, backend="hashing", use_cache=False)
        assert result.vectors.shape == (len(sample_papers), 256)
        assert result.backend == "hashing"
        assert result.corpus_relative is False
        alone = embeddings.embed_query(
            sample_papers[0].embedding_text(), backend="hashing"
        )[0]
        assert np.allclose(result.vectors[0], alone), (
            "a hashing vector must not depend on which other papers were embedded"
        )

    def test_lexical_embeddings_still_group_related_papers(self, sample_papers):
        """TF-IDF cannot do synonymy, but shared vocabulary should still show."""
        from papercreator.analysis import embeddings

        result = embeddings.embed_papers(sample_papers, backend="tfidf", use_cache=False)
        similarity = result.vectors @ result.vectors.T
        # Papers 0 and 1 are both molecular GNN work; 0 and 3 are unrelated.
        assert similarity[0, 1] > similarity[0, 3]

    def test_blocked_model_host_degrades_instead_of_failing(self, temp_home, sample_papers):
        """Regression: an unreachable Hugging Face used to abort the analysis after
        several minutes of retries instead of falling back."""
        from papercreator.analysis import embeddings

        result = embeddings.embed_papers(
            sample_papers, backend="sentence-transformers", use_cache=False
        )
        assert result.vectors.shape[0] == len(sample_papers), "must still produce vectors"
        if result.backend != "sentence-transformers":
            assert result.warnings, "a downgrade must be explained"

    def test_blocker_message_distinguishes_its_causes(self, temp_home):
        """'Not installed' and 'host blocked' need different user actions."""
        from papercreator.analysis import embeddings

        blocker = embeddings.sentence_transformers_blocker()
        if blocker:
            assert any(
                phrase in blocker
                for phrase in ("not installed", "not reachable", "not in the local cache")
            ), blocker

    def test_nearest_neighbours_ordering(self):
        from papercreator.analysis import embeddings

        vectors = np.array(
            [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], dtype=np.float32
        )
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        neighbours = embeddings.nearest_neighbours(vectors[0], vectors, k=3)
        assert [index for index, _ in neighbours][:2] == [0, 1]


class TestReduction:
    def test_pca_is_always_available(self, sample_papers):
        from papercreator.analysis import embeddings, reduce

        vectors = embeddings.embed_papers(
            sample_papers, backend="tfidf", use_cache=False
        ).vectors
        projection = reduce.reduce_vectors(vectors, dims=3, reducer="pca")
        assert projection.coords.shape == (len(sample_papers), 3)
        assert projection.supports_transform is True, "needed for incremental placement"

    def test_coordinates_are_normalised_into_a_fixed_cube(self, sample_papers):
        """The frontend camera and grid assume a bounded coordinate range."""
        from papercreator.analysis import embeddings, reduce

        vectors = embeddings.embed_papers(
            sample_papers, backend="tfidf", use_cache=False
        ).vectors
        coords = reduce.reduce_vectors(vectors, dims=3, reducer="pca").coords
        assert np.abs(coords).max() <= 10.001

    def test_tiny_corpora_degrade_rather_than_crash(self):
        from papercreator.analysis import reduce

        vectors = np.random.default_rng(0).random((2, 8)).astype(np.float32)
        projection = reduce.reduce_vectors(vectors, dims=3, reducer="umap")
        assert projection.coords.shape == (2, 3)
        assert projection.warnings, "the substitution must be explained"

    def test_a_single_paper_is_placed_at_the_origin(self):
        """'Analyse just my idea' must not raise."""
        from papercreator.analysis import reduce

        projection = reduce.reduce_vectors(
            np.ones((1, 5), dtype=np.float32), dims=3, reducer="auto"
        )
        assert projection.coords.shape == (1, 3)
        assert projection.reducer == "passthrough"

    def test_stored_scaling_reproduces_the_transform(self, sample_papers):
        """Incremental placement re-applies the saved normalisation."""
        from papercreator.analysis import embeddings, reduce

        vectors = embeddings.embed_papers(
            sample_papers, backend="tfidf", use_cache=False
        ).vectors
        projection = reduce.reduce_vectors(vectors, dims=3, reducer="pca")
        scaling = projection.metrics["scaling"]
        raw = projection.model.transform(vectors[:1])
        again = reduce.apply_normalisation(raw, scaling)
        assert np.allclose(again.ravel()[:3], projection.coords[0], atol=1e-3)


class TestClustering:
    def test_separable_topics_are_separated(self, sample_papers):
        from papercreator.analysis import cluster, embeddings

        vectors = embeddings.embed_papers(
            sample_papers, backend="tfidf", use_cache=False
        ).vectors
        assignment = cluster.cluster_vectors(vectors, method="kmeans", n_clusters=3)
        assert assignment.n_clusters == 3
        assert len(assignment.labels) == len(sample_papers)

    def test_labels_are_ordered_by_size(self):
        """Cluster 0 must always be the largest, so the UI palette is stable."""
        from papercreator.analysis.cluster import relabel_by_size

        relabelled = relabel_by_size(np.array([5, 5, 5, 2, 2, 9, -1]))
        assert list(relabelled[:3]) == [0, 0, 0]
        assert list(relabelled[3:5]) == [1, 1]
        assert relabelled[6] == -1, "noise stays noise"

    def test_metrics_exclude_noise(self, sample_papers):
        """Including noise would penalise HDBSCAN for correctly finding outliers."""
        from papercreator.analysis import cluster, embeddings

        vectors = embeddings.embed_papers(
            sample_papers, backend="tfidf", use_cache=False
        ).vectors
        labels = np.array([0, 0, 0, 1, 1, 1, 2, 2, -1, -1])
        metrics = cluster.evaluate_clustering(vectors, labels)
        assert metrics["n_noise"] == 2
        assert metrics["n_clustered"] == 8

    def test_tiny_corpus_becomes_one_cluster(self):
        from papercreator.analysis import cluster

        assignment = cluster.cluster_vectors(
            np.random.default_rng(1).random((3, 6)).astype(np.float32), method="auto"
        )
        assert assignment.n_clusters == 1
        assert assignment.method == "single"


class TestKeywords:
    def test_boilerplate_is_excluded(self):
        from papercreator.analysis import keywords

        terms = keywords.extract_terms(
            "In this paper we propose a novel method showing significant "
            "improvement in performance on experimental results."
        )
        for noise in ("paper", "method", "performance", "results", "significant"):
            assert noise not in terms

    def test_generic_heads_are_excluded_alone_but_kept_in_phrases(self):
        """'structure' alone says nothing; 'molecular structure' is a topic."""
        from papercreator.analysis import keywords

        terms = keywords.extract_terms("molecular structure prediction accuracy")
        assert "structure" not in terms
        assert "molecular structure" in terms

    def test_class_tfidf_finds_distinguishing_terms(self):
        from papercreator.analysis.keywords import class_tfidf

        scored = class_tfidf(
            {
                0: ["molecular", "graph", "molecular", "chemistry", "graph"],
                1: ["language", "transformer", "language", "text", "transformer"],
            },
            top_k=3,
            min_count=1,
        )
        assert "molecular" in [term for term, _ in scored[0]]
        assert "language" in [term for term, _ in scored[1]]
        # A term frequent in both classes must not label either.
        assert "molecular" not in [term for term, _ in scored[1]]

    def test_cluster_labels_are_readable(self, sample_papers):
        from papercreator.analysis import keywords

        labels = np.array([0, 0, 0, 1, 1, 1, 2, 2, 2, 2])
        by_cluster = keywords.cluster_keywords(sample_papers, labels, top_k=8)
        assert set(by_cluster) == {0, 1, 2}
        for cluster_id, terms in by_cluster.items():
            label = keywords.label_cluster(terms)
            assert label and label != "Unlabelled", f"cluster {cluster_id} unlabelled"

    def test_subsumed_terms_are_dropped(self):
        """Prevents labels like 'graph, graph neural, graph neural network'."""
        from papercreator.analysis.keywords import _drop_subsumed

        kept = _drop_subsumed(
            [("graph neural network", 3.0), ("graph neural", 2.0), ("graph", 1.0)],
            top_k=5,
        )
        assert [term for term, _ in kept] == ["graph neural network"]

    def test_trend_is_normalised_by_corpus_growth(self, sample_papers):
        """Raw counts would make every term look like it is rising."""
        from papercreator.analysis import keywords

        stats = keywords.global_keyword_stats(sample_papers, top_k=20, min_papers=1)
        assert stats
        assert all(-50 < stat.trend < 50 for stat in stats)


class TestHeatmap:
    def test_grid_shape_and_normalisation(self, sample_papers):
        from papercreator.analysis import heatmap

        points = np.random.default_rng(3).normal(0, 3, (40, 3)).astype(np.float32)
        data = heatmap.build_heatmap(points, grid_size=20)
        assert data.grid_size == 20
        assert len(data.grid) == 20 and len(data.grid[0]) == 20
        assert max(max(row) for row in data.grid) <= 1.0001

    def test_density_is_higher_inside_a_cluster(self):
        from papercreator.analysis import heatmap

        # A tight cluster plus one far-away outlier.
        points = np.vstack([
            np.random.default_rng(4).normal(0, 0.3, (30, 3)),
            np.array([[20.0, 20.0, 20.0]]),
        ]).astype(np.float32)
        densities = heatmap.density_at_points(points)
        assert densities[:30].mean() > densities[30] * 5

    def test_z_slices_partition_the_points(self):
        from papercreator.analysis import heatmap

        points = np.random.default_rng(5).normal(0, 4, (60, 3)).astype(np.float32)
        data = heatmap.build_heatmap(points, grid_size=12, z_slices=4)
        assert len(data.z_slices) == 4
        assert sum(entry["count"] for entry in data.z_slices) == 60


class TestGapDetection:
    def test_sparse_region_prefers_pockets_over_sprawling_voids(self):
        """Regression: PCA pushes the corpus into a corner, leaving a huge empty
        quadrant that once scored a perfect 1.000."""
        from papercreator.analysis import gaps
        from papercreator.core.models import Paper

        rng = np.random.default_rng(6)
        # A ring of papers with a genuine hole in the middle.
        angles = np.linspace(0, 2 * np.pi, 60, endpoint=False)
        points = np.stack([
            np.cos(angles) * 7 + rng.normal(0, 0.4, 60),
            np.sin(angles) * 7 + rng.normal(0, 0.4, 60),
            rng.normal(0, 0.4, 60),
        ], axis=1).astype(np.float32)
        papers = [Paper(title=f"Paper {i}", year=2020).ensure_id() for i in range(60)]

        found = gaps.detect_sparse_regions(points, papers, grid_size=30, min_score=0.2)
        assert found, "an interior hole must be detected"
        for gap in found:
            share = gap.evidence.get("area_share_of_map", 0)
            assert share is not None
            assert gap.score <= 1.0
            assert "caveat" in gap.evidence, "every gap must state its limits"

    def test_cluster_bridge_needs_semantic_proximity(self):
        from papercreator.analysis import gaps
        from papercreator.core.models import ClusterInfo, Paper

        # Two orthogonal clusters: unrelated, so not a bridge candidate.
        embeddings = np.array(
            [[1.0, 0.0]] * 5 + [[0.0, 1.0]] * 5, dtype=np.float32
        )
        points = np.random.default_rng(7).normal(0, 1, (10, 3)).astype(np.float32)
        papers = [Paper(title=f"P{i}").ensure_id() for i in range(10)]
        labels = np.array([0] * 5 + [1] * 5)
        clusters = [
            ClusterInfo(id=0, label="A", size=5, keywords=["a"]),
            ClusterInfo(id=1, label="B", size=5, keywords=["b"]),
        ]
        found = gaps.detect_cluster_bridges(
            embeddings, points, papers, labels, clusters, min_score=0.3
        )
        assert found == [], "orthogonal topics are not an unexploited bridge"

    def test_temporal_stale_needs_prior_activity(self):
        from papercreator.analysis import gaps
        from papercreator.core.models import ClusterInfo, Paper

        papers = [Paper(title=f"P{i}", year=2010 + i).ensure_id() for i in range(6)]
        points = np.zeros((6, 3), dtype=np.float32)
        labels = np.zeros(6, dtype=int)
        clusters = [ClusterInfo(id=0, label="Dormant", size=6, keywords=["x"],
                                representative_paper_ids=[papers[0].id])]
        found = gaps.detect_temporal_stale(points, papers, labels, clusters)
        assert found, "a cluster silent since 2015 should be flagged"
        assert found[0].evidence["years_silent"] > 4

    def test_every_detector_reports_evidence_and_a_caveat(self, sample_papers):
        from papercreator.analysis import cluster, embeddings, gaps, keywords

        vectors = embeddings.embed_papers(
            sample_papers, backend="tfidf", use_cache=False
        ).vectors
        points = np.random.default_rng(8).normal(0, 4, (len(sample_papers), 3)).astype(
            np.float32
        )
        assignment = cluster.cluster_vectors(vectors, method="kmeans", n_clusters=3)
        cluster_keywords = keywords.cluster_keywords(sample_papers, assignment.labels)
        from papercreator.core.models import ClusterInfo

        clusters = [
            ClusterInfo(id=cid, label=keywords.label_cluster(terms), size=3,
                        keywords=terms)
            for cid, terms in cluster_keywords.items()
        ]
        found = gaps.detect_all(
            embeddings=vectors, points=points, papers=sample_papers,
            labels=assignment.labels, clusters=clusters,
            keywords_by_cluster=cluster_keywords, min_score=0.2,
        )
        for gap in found:
            assert gap.evidence.get("detector"), f"{gap.kind} names no detector"
            assert gap.evidence.get("caveat"), f"{gap.kind} states no caveat"
            assert gap.description and gap.description_zh, "both languages required"

    def test_detector_catalogue_is_honest_about_strength(self):
        from papercreator.analysis import gaps

        described = {entry["id"]: entry for entry in gaps.describe_detectors()}
        assert described["low_density_frontier"]["strength"] == "low"
        assert described["cluster_bridge"]["strength"] == "high"


class TestFullPipeline:
    def test_builds_a_complete_landscape_offline(self, stored_papers, project):
        from papercreator.analysis import pipeline

        result = pipeline.build_analysis(
            stored_papers, project_id=project.id, name="test landscape"
        )
        assert result.id
        assert result.n_papers == len(stored_papers)
        assert result.points and len(result.points) == len(stored_papers)
        assert result.clusters, "clustering produced nothing"
        assert result.keywords, "no keywords extracted"
        assert result.heatmap.grid, "no heatmap grid"
        assert result.embedding_model and result.reducer and result.clusterer

    def test_the_landscape_reloads_from_the_database(self, stored_papers, project):
        from papercreator.analysis import pipeline
        from papercreator.store import analyses as analyses_store

        built = pipeline.build_analysis(stored_papers, project_id=project.id)
        reloaded = analyses_store.require_analysis(built.id)
        assert reloaded.n_papers == built.n_papers
        assert len(reloaded.points) == len(built.points)
        assert len(reloaded.clusters) == len(built.clusters)

    def test_empty_input_is_rejected_clearly(self, temp_home):
        from papercreator.analysis import pipeline
        from papercreator.core.errors import ValidationError

        with pytest.raises(ValidationError, match="zero papers"):
            pipeline.build_analysis([])


class TestIncrementalPlacement:
    def test_corpus_relative_embeddings_refuse_placement(self, stored_papers, project):
        """A TF-IDF vector computed alone is not comparable to the fitted corpus;
        placing it would put the point somewhere meaningless."""
        from papercreator.analysis import incremental, pipeline
        from papercreator.core.errors import ConflictError

        built = pipeline.build_analysis(
            stored_papers, project_id=project.id,
            config=pipeline.config_from_settings({"embedding_backend": "tfidf"}),
        )
        with pytest.raises(ConflictError, match="corpus-relative"):
            incremental.place_idea(
                built.id, title="A new idea", abstract="Something novel."
            )

    def test_hashing_pca_places_an_idea_offline_without_moving_existing_points(
        self, stored_papers, project
    ):
        """The desktop's incremental offline option must deliver real placement."""
        from papercreator.analysis import incremental, pipeline
        from papercreator.store import analyses as analyses_store

        built = pipeline.build_analysis(
            stored_papers,
            project_id=project.id,
            config=pipeline.config_from_settings({
                "embedding_backend": "hashing",
                "reducer": "pca",
                "clusterer": "kmeans",
            }),
        )
        before = {
            point.paper_id: (point.x, point.y, point.z) for point in built.points
        }
        placed = incremental.place_idea(
            built.id,
            title="Graph language models for molecules",
            abstract="A graph neural language model for molecular property prediction.",
            project_id=project.id,
        )
        assert placed.method == "exact_transform"
        assert placed.point.is_seed is True
        assert placed.nearest_papers
        reloaded = analyses_store.require_analysis(built.id)
        assert len(reloaded.points) == len(built.points) + 1
        after = {
            point.paper_id: (point.x, point.y, point.z)
            for point in reloaded.points if point.paper_id in before
        }
        assert after == before, "placing one idea must never move the existing map"

    def test_removing_a_point_keeps_the_paper(self, stored_papers, project):
        from papercreator.analysis import pipeline
        from papercreator.analysis import incremental
        from papercreator.store import papers as papers_store

        built = pipeline.build_analysis(stored_papers, project_id=project.id)
        target = stored_papers[0].id
        result = incremental.remove_from_analysis(built.id, [target])
        assert result["removed"] == 1
        assert papers_store.get(target) is not None, "the paper stays in the library"


class TestCitationGraph:
    def test_reports_low_coverage_rather_than_pretending(self, sample_papers):
        """Only OpenAlex supplies reference lists; the synthetic set has none."""
        from papercreator.analysis import graph

        result = graph.analyse_graph(sample_papers)
        assert result["citation"]["coverage"] == 0.0
        assert result["citation"]["internal_edges"] == 0
        assert result["caveats"], "the limitation must be stated"

    def test_pagerank_ranks_a_cited_paper_higher(self):
        from papercreator.analysis.graph import pagerank

        ranks = pagerank(
            ["a", "b", "c"], [("b", "a"), ("c", "a"), ("c", "b")]
        )
        assert ranks["a"] > ranks["c"], "rank flows to work that is built upon"

    def test_coauthor_graph_states_its_disambiguation_limit(self, sample_papers):
        from papercreator.analysis import graph

        result = graph.coauthor_graph(sample_papers, min_papers=1)
        assert result["note"], "name-collision limitation must be documented"
