"""Live retrieval smoke check (hits real APIs; not part of the unit suite).

Run: ``python tests/smoke_retrieval.py``
Purpose: verify each provider parses real responses correctly, and that dedupe
and ranking behave on genuinely messy multi-source data.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PAPERCREATOR_HOME", tempfile.mkdtemp(prefix="pc_smoke_"))

from papercreator.core import db, logging_setup, paths  # noqa: E402
from papercreator.core.models import SearchRequest  # noqa: E402


async def main() -> int:
    logging_setup.setup_logging("INFO")
    paths.get_paths().ensure()
    db.init_db()

    from papercreator.retrieval import pipeline, registry

    print("\n=== provider catalogue ===")
    for entry in registry.describe_all():
        flag = "OK " if entry["available"] else "-- "
        print(f"{flag}{entry['id']:16s} {entry['tier']:9s} {entry['coverage'][:34]:36s}"
              f"{entry['unavailable_reason'][:40]}")

    print("\n=== keyword search across free providers ===")
    request = SearchRequest(
        query="graph neural network molecular property prediction",
        mode="keyword",
        providers=["arxiv", "openalex", "crossref", "europepmc", "dblp", "doaj"],
        limit_per_provider=12,
        total_limit=60,
        year_from=2019,
    )
    response = await pipeline.search_async(request, persist=True, use_llm_expansion=False)
    for stat in response.stats:
        note = f" ERROR: {stat.error}" if stat.error else ""
        print(f"  {stat.provider:16s} {stat.count:4d} results  {stat.duration_ms:6d}ms{note}")
    print(f"  before dedupe: {response.total_before_dedupe}  "
          f"after: {response.total_after_dedupe}  merged: {response.duplicates_merged}")
    for warning in response.warnings:
        print(f"  warning: {warning}")

    print("\n  top 8 by fused rank:")
    for index, paper in enumerate(response.papers[:8], 1):
        ranking = paper.raw.get("ranking", {})
        print(f"   {index}. [{paper.score:.3f}] {paper.title[:70]}")
        print(f"      {paper.year} | {paper.venue[:40]} | cites={paper.citation_count}"
              f" | providers={','.join(paper.source_providers)}")
        print(f"      fusion={ranking.get('fusion', 0):.2f}"
              f" overlap={ranking.get('term_overlap', 0):.2f}"
              f" abstract={len(paper.abstract)}ch")

    multi = [p for p in response.papers if len(p.source_providers) > 1]
    print(f"\n  merged across providers: {len(multi)}")
    for paper in multi[:3]:
        print(f"   - {paper.title[:60]} <- {paper.source_providers}")

    print("\n=== idea-mode search (rule expansion) ===")
    idea = SearchRequest(
        mode="idea",
        seed_text=(
            "I want to use multi-agent large language models to automatically "
            "write survey papers, where each agent handles retrieval, gap "
            "analysis and section drafting separately."
        ),
        providers=["arxiv", "openalex"],
        limit_per_provider=10,
        total_limit=20,
    )
    idea_response = await pipeline.search_async(
        idea, persist=True, use_llm_expansion=False
    )
    print(f"  expanded queries: {idea.expanded_queries}")
    for stat in idea_response.stats:
        print(f"  {stat.provider:16s} {stat.count:4d}  {stat.error}")
    for paper in idea_response.papers[:5]:
        print(f"   [{paper.score:.3f}] {paper.title[:72]}")

    print("\n=== cross-provider merge on a known-shared paper ===")
    # A famous paper every source indexes: dedupe must collapse it to one record
    # that carries the union of the fields (abstract from one, citations from
    # another, venue from a third).
    shared = SearchRequest(
        query="Attention Is All You Need transformer",
        providers=["arxiv", "openalex", "crossref", "semanticscholar"],
        limit_per_provider=6,
        total_limit=20,
    )
    shared_response = await pipeline.search_async(
        shared, persist=False, use_llm_expansion=False
    )
    print(f"  before dedupe {shared_response.total_before_dedupe} -> after "
          f"{shared_response.total_after_dedupe} (merged "
          f"{shared_response.duplicates_merged})")
    for paper in shared_response.papers[:4]:
        print(f"   [{len(paper.source_providers)} src] {paper.title[:58]}")
        print(f"      providers={paper.source_providers} doi={paper.doi or '-'}"
              f" arxiv={paper.arxiv_id or '-'} cites={paper.citation_count}"
              f" abstract={len(paper.abstract)}ch venue={paper.venue[:28]!r}")

    print("\n=== identifier resolution ===")
    resolved = await pipeline.resolve_identifier("10.1109/tnn.2008.2005605")
    if resolved:
        print(f"  {resolved.title} ({resolved.year}) cites={resolved.citation_count}"
              f" providers={resolved.source_providers}")
    else:
        print("  not resolved")

    print("\n=== dedupe unit behaviour (synthetic) ===")
    from papercreator.core.models import Paper as P
    from papercreator.retrieval import dedupe as dd

    candidates = [
        P(title="Attention Is All You Need", arxiv_id="1706.03762",
          abstract="The dominant sequence transduction models...",
          source_providers=["arxiv"], year=2017),
        # same paper, DOI-only record with citations - must merge via title
        P(title="Attention is all you need.", doi="10.5555/3295222.3295349",
          citation_count=90000, venue="NeurIPS", source_providers=["crossref"],
          year=2017),
        # subtitle variant, same authors - must merge
        P(title="Attention Is All You Need!", arxiv_id="1706.03762v5",
          source_providers=["openalex"], year=2017),
        # different paper that shares a title prefix - must NOT merge
        P(title="Attention Is Not All You Need: Pure Attention Loses Rank",
          arxiv_id="2103.03404", source_providers=["arxiv"], year=2021),
        # generic title, disjoint authors - must NOT merge
        P(title="A Survey of Deep Learning", year=2020,
          authors=[{"name": "Alice Smith"}], source_providers=["dblp"]),
        P(title="A Survey of Deep Learning", year=2020,
          authors=[{"name": "Bob Jones"}], source_providers=["dblp"]),
    ]
    unique, merged, report = dd.deduplicate(candidates, title_threshold=0.90)
    print(f"  {report}")
    for paper in unique:
        print(f"   - {paper.title[:52]!r} providers={paper.source_providers}"
              f" cites={paper.citation_count} venue={paper.venue!r}")
    assert len(unique) == 4, f"expected 4 unique, got {len(unique)}"
    top = unique[0]
    assert top.citation_count == 90000, "citations must merge in"
    assert top.venue == "NeurIPS", "venue must merge in"
    assert top.abstract, "abstract must survive the merge"
    assert set(top.source_providers) == {"arxiv", "crossref", "openalex"}
    print("  merge assertions passed")

    print("\n=== year-conflict rule (real OpenAlex 2025 vs 2017 case) ===")
    # Exact normalised title + shared author must merge even across an 8-year
    # year disagreement, and the earlier year must win.
    conflicting = [
        P(title="Attention is All you Need", year=2017,
          authors=[{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
          abstract="The dominant sequence transduction models are based on...",
          source_providers=["semanticscholar"], arxiv_id="1706.03762"),
        P(title="Attention Is All You Need", year=2025,
          authors=[{"name": "Ashish Vaswani"}, {"name": "Niki Parmar"}],
          doi="10.65215/2q58a426", citation_count=6596,
          source_providers=["openalex"]),
        # Fuzzy (not exact) title + 4-year gap must NOT merge: this is where
        # false positives live.
        P(title="Attention is All you Need for Vision", year=2021,
          authors=[{"name": "Ashish Vaswani"}], source_providers=["arxiv"]),
    ]
    resolved_group, merged_n, conflict_report = dd.deduplicate(
        conflicting, title_threshold=0.90
    )
    for paper in resolved_group:
        print(f"   - year={paper.year} {paper.title[:44]!r}"
              f" providers={paper.source_providers}"
              f" conflicts={paper.raw.get('conflicts', {})}")
    assert len(resolved_group) == 2, (
        f"exact-title/different-year pair must merge, got {len(resolved_group)}"
    )
    winner = resolved_group[0]
    assert winner.year == 2017, f"earlier year must win, got {winner.year}"
    assert winner.raw.get("conflicts", {}).get("year") == [2017, 2025], (
        "the year disagreement must be recorded, not hidden"
    )
    assert winner.citation_count == 6596, "citations must still merge in"
    print("  year-conflict assertions passed")

    from papercreator.store import papers as papers_store

    print(f"\n=== library: {papers_store.library_stats()} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
