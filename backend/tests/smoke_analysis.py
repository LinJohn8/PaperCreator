"""Live analysis smoke check: retrieve real papers, build a landscape.

Run: ``python tests/smoke_analysis.py``
Verifies the full analysis path on genuine data, including the degradation
behaviour when the optional stack (umap/hdbscan/sentence-transformers) is absent.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PAPERCREATOR_HOME", tempfile.mkdtemp(prefix="pc_ana_"))

from papercreator.core import db, logging_setup, paths  # noqa: E402
from papercreator.core.models import SearchRequest  # noqa: E402


async def fetch_corpus() -> list:
    from papercreator.retrieval import pipeline as retrieval

    request = SearchRequest(
        query="graph neural network molecular property prediction",
        providers=["arxiv", "openalex", "europepmc", "doaj"],
        limit_per_provider=40,
        total_limit=160,
        year_from=2018,
    )
    response = await retrieval.search_async(
        request, persist=True, use_llm_expansion=False
    )
    print(f"retrieved {len(response.papers)} papers "
          f"(before dedupe {response.total_before_dedupe}, "
          f"merged {response.duplicates_merged})")
    for stat in response.stats:
        print(f"  {stat.provider:14s} {stat.count:4d} {stat.error}")
    return response.papers


def main() -> int:
    logging_setup.setup_logging("INFO")
    paths.get_paths().ensure()
    db.init_db()

    from papercreator.analysis import graph, incremental, pipeline

    print("\n=== analysis capabilities ===")
    caps = pipeline.describe_capabilities()
    print(f"  optional stack: {caps['optional_stack_installed']}")
    for backend in caps["embedding_backends"]:
        print(f"  embed  {backend['id']:22s} available={backend['available']!s:5s}"
              f" quality={backend['quality']}")
    for reducer in caps["reducers"]:
        print(f"  reduce {reducer['id']:22s} available={reducer['available']!s:5s}"
              f" new_points={reducer['supports_new_points']}")
    for clusterer in caps["clusterers"]:
        print(f"  clust  {clusterer['id']:22s} available={clusterer['available']}")

    papers = asyncio.run(fetch_corpus())
    if len(papers) < 20:
        print("not enough papers retrieved to exercise the analysis")
        return 1

    print("\n=== building landscape ===")
    result = pipeline.build_analysis(papers, name="smoke landscape")
    print(f"  id={result.id}")
    print(f"  backend={result.embedding_model} reducer={result.reducer} "
          f"clusterer={result.clusterer}")
    print(f"  {result.n_papers} papers -> {result.n_clusters} clusters, "
          f"{len(result.gaps)} gap candidates, {len(result.keywords)} keywords")
    print(f"  metrics: silhouette={result.metrics.get('cluster_silhouette')} "
          f"trustworthiness={result.metrics.get('trustworthiness')} "
          f"noise={result.metrics.get('cluster_n_noise')} "
          f"duration={result.metrics.get('duration_ms')}ms")
    for warning in result.warnings:
        print(f"  warning: {warning}")

    print("\n  clusters:")
    for info in result.clusters:
        print(f"   [{info.id}] {info.size:3d} papers  coherence={info.coherence:.3f}"
              f"  {info.year_min}-{info.year_max}  {info.label}")
        print(f"        keywords: {', '.join(info.keywords[:6])}")

    print("\n  coordinate extent:")
    xs = [p.x for p in result.points]
    ys = [p.y for p in result.points]
    zs = [p.z for p in result.points]
    print(f"   x [{min(xs):.2f}, {max(xs):.2f}] y [{min(ys):.2f}, {max(ys):.2f}]"
          f" z [{min(zs):.2f}, {max(zs):.2f}]")
    assert max(abs(min(xs)), abs(max(xs))) <= 10.5, "coords must be normalised"

    print("\n  heatmap:")
    print(f"   grid {result.heatmap.grid_size}x{result.heatmap.grid_size}"
          f" bounds={result.heatmap.bounds}"
          f" layers={len(result.heatmap.layers)}"
          f" z_slices={len(result.heatmap.z_slices)}")
    assert len(result.heatmap.grid) == result.heatmap.grid_size

    print("\n  gap candidates:")
    for gap in result.gaps[:6]:
        print(f"   [{gap.score:.3f}] {gap.kind}")
        print(f"      {gap.description[:150]}")
        print(f"      evidence: {gap.evidence.get('detector')} | "
              f"{list(gap.evidence)[:6]}")

    print("\n  keyword trends:")
    trends = result.metrics.get("trends", {})
    print(f"   emerging: {[t['term'] for t in trends.get('emerging', [])[:6]]}")
    print(f"   fading:   {[t['term'] for t in trends.get('fading', [])[:6]]}")

    print("\n=== placing my own idea into the landscape ===")
    try:
        placement = incremental.place_idea(
            result.id,
            title="Multi-agent LLM system for automated survey writing",
            abstract=(
                "We propose a multi-agent framework where large language models "
                "handle literature retrieval, research gap analysis and section "
                "drafting as separate cooperating agents, applied to molecular "
                "property prediction surveys."
            ),
            keywords=["multi-agent", "large language model", "survey generation"],
        )
        print(f"  point: x={placement.point.x} y={placement.point.y} "
              f"z={placement.point.z} cluster={placement.nearest_cluster}"
              f" ({placement.nearest_cluster_label})")
        print(f"  novelty={placement.novelty} "
              f"density_percentile={placement.density_percentile}")
        print(f"  nearest papers:")
        for near in placement.nearest_papers[:4]:
            print(f"    {near['similarity']:.3f}  {near['title'][:64]}")
        print(f"  interpretation: {placement.interpretation}")
        print(f"  中文: {placement.interpretation_zh}")

        print("\n=== removing it again ===")
        removal = incremental.remove_from_analysis(result.id, [placement.paper_id])
        print(f"  {removal}")
    except Exception as exc:  # noqa: BLE001 - the refusal path is a valid outcome
        print(f"  placement refused (expected with corpus-relative embeddings):")
        print(f"    {type(exc).__name__}: {exc}")

    print("\n=== citation / co-authorship graph ===")
    graph_result = graph.analyse_graph(papers)
    citation = graph_result["citation"]
    print(f"  coverage={citation['coverage']} internal_edges="
          f"{citation['internal_edges']} external_refs="
          f"{citation['external_references']} isolated={citation['isolated_papers']}")
    print(f"  components: {[c['size'] for c in citation['components'][:5]]}")
    print("  most influential in-set:")
    for item in graph_result["influential_papers"][:5]:
        print(f"    pr={item['pagerank']:.5f} in={item['cited_by_in_set']:2d}"
              f" global={item['global_citations']:6d}  {item['title'][:56]}")
    print(f"  co-citation pairs: {len(graph_result['co_citation'])}")
    print(f"  coupling pairs: {len(graph_result['bibliographic_coupling'])}")
    print(f"  authors with 2+ papers: {len(graph_result['coauthors']['authors'])}")

    print("\n=== reload from store ===")
    from papercreator.store import analyses as store

    reloaded = store.require_analysis(result.id)
    print(f"  reloaded {reloaded.n_papers} papers, {len(reloaded.points)} points, "
          f"{len(reloaded.clusters)} clusters, {len(reloaded.gaps)} gaps")
    assert reloaded.n_clusters == result.n_clusters
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
