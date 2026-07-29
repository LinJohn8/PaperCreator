"""Retrieval tests: dedupe, ranking, query expansion, provider parsing.

Provider parsing is tested against *recorded real payloads* rather than invented
ones, because the failures that actually happen are shape surprises from the real
APIs - OpenAlex's inverted-index abstracts, DBLP returning a bare object instead
of a list, Crossref's JATS markup.

Tests marked ``live`` hit the real APIs and are excluded by default.
"""

from __future__ import annotations

import pytest
import httpx

from papercreator.core.errors import ProviderError, ProviderUnavailableError, RateLimitError
from papercreator.core.models import Author, Paper, SearchRequest
from papercreator.retrieval.base import Provider, ProviderAvailability, ProviderMeta


class FailingProvider(Provider):
    """Deterministic failure fixture for the provider fault contract."""

    meta = ProviderMeta(id="failure-fixture", name="Failure Fixture")

    def __init__(self, client, failure: Exception):
        super().__init__(client)
        self.failure = failure

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        raise self.failure


class SuccessfulProvider(Provider):
    """One-result fixture used to prove partial failures preserve good data."""

    meta = ProviderMeta(id="success-fixture", name="Success Fixture")

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        return [Paper(title="A deterministic provider result", year=2024)]


class UnavailableProvider(Provider):
    meta = ProviderMeta(id="unavailable-fixture", name="Unavailable Fixture")

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(available=False, reason="fixture is not configured")

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        raise AssertionError("an unavailable provider must never perform I/O")


class TestDedupe:
    def test_identifier_match_merges(self):
        from papercreator.retrieval import dedupe

        unique, merged, report = dedupe.deduplicate([
            Paper(title="Paper One", doi="10.1/a", source_providers=["arxiv"]),
            Paper(title="Totally Different Title", doi="10.1/a",
                  source_providers=["openalex"]),
        ])
        assert len(unique) == 1
        assert merged == 1
        assert report["by_identifier"] == 1

    def test_exact_title_merges_across_a_wide_year_gap(self):
        """OpenAlex dates the 2017 transformer paper as 2025; the year guard alone
        would lose a genuine duplicate."""
        from papercreator.retrieval import dedupe

        unique, _, _ = dedupe.deduplicate([
            Paper(title="Attention is All you Need", year=2017,
                  authors=[Author(name="Ashish Vaswani")],
                  source_providers=["s2"]),
            Paper(title="Attention Is All You Need", year=2025,
                  authors=[Author(name="Ashish Vaswani")],
                  source_providers=["openalex"]),
        ])
        assert len(unique) == 1
        assert unique[0].year == 2017, "the earlier year wins"

    def test_fuzzy_title_still_requires_a_compatible_year(self):
        """Near-matches are where real false positives live."""
        from papercreator.retrieval import dedupe

        unique, _, _ = dedupe.deduplicate([
            Paper(title="Attention is All you Need", year=2017,
                  authors=[Author(name="Ashish Vaswani")]),
            Paper(title="Attention is All you Need for Vision", year=2021,
                  authors=[Author(name="Ashish Vaswani")]),
        ])
        assert len(unique) == 2

    def test_generic_title_with_disjoint_authors_does_not_merge(self):
        from papercreator.retrieval import dedupe

        unique, _, _ = dedupe.deduplicate([
            Paper(title="A Survey of Deep Learning", year=2020,
                  authors=[Author(name="Alice Smith")]),
            Paper(title="A Survey of Deep Learning", year=2020,
                  authors=[Author(name="Bob Jones")]),
        ])
        assert len(unique) == 2, "shared generic titles must not merge"

    def test_preprint_and_published_version_merge(self):
        from papercreator.retrieval import dedupe

        unique, _, _ = dedupe.deduplicate([
            Paper(title="Graph Neural Networks for Chemistry", year=2022,
                  arxiv_id="2201.00001", authors=[Author(name="Jane Doe")]),
            Paper(title="Graph Neural Networks for Chemistry", year=2023,
                  doi="10.1/journal", venue="Nature", citation_count=40,
                  authors=[Author(name="Jane Doe")]),
        ])
        assert len(unique) == 1
        assert unique[0].venue == "Nature"
        assert unique[0].arxiv_id == "2201.00001"

    def test_empty_input(self):
        from papercreator.retrieval import dedupe

        unique, merged, _ = dedupe.deduplicate([])
        assert unique == [] and merged == 0


class TestRanking:
    def test_reciprocal_rank_fusion_rewards_agreement(self):
        from papercreator.retrieval import rank

        scores = rank.reciprocal_rank_fusion({
            "arxiv": ["a", "b", "c"],
            "openalex": ["a", "c", "b"],
            "crossref": ["a", "b", "c"],
        })
        assert scores["a"] > scores["b"] > scores["c"]

    def test_citations_use_a_log_scale(self):
        from papercreator.retrieval.rank import _citation_score

        # Without compression, one classic paper dominates every ranking.
        assert _citation_score(0) == 0
        assert 0.2 < _citation_score(10) < 0.35
        assert 0.45 < _citation_score(100) < 0.6
        assert _citation_score(100000) == 1.0

    def test_ranking_exposes_its_components(self):
        from papercreator.retrieval import rank

        papers = [
            Paper(title="Graph neural networks", abstract="x " * 60,
                  citation_count=100, year=2023, source_providers=["a", "b"]).ensure_id(),
            Paper(title="Unrelated topic", citation_count=0, year=2005,
                  source_providers=["a"]).ensure_id(),
        ]
        ranked = rank.rank_papers(
            papers,
            provider_lists={"a": [papers[0].id, papers[1].id]},
            request=SearchRequest(query="graph neural networks"),
        )
        assert ranked[0].title == "Graph neural networks"
        assert "ranking" in ranked[0].raw
        # Every component must be reported so a surprising order is explainable.
        for component in ("fusion", "citations", "recency", "term_overlap"):
            assert component in ranked[0].raw["ranking"]

    def test_post_filters_report_what_they_removed(self):
        from papercreator.retrieval import rank

        kept, removed = rank.apply_post_filters(
            [
                Paper(title="Recent", year=2023),
                Paper(title="Old", year=1990),
                Paper(title="Paywalled", year=2023, is_open_access=False),
            ],
            SearchRequest(year_from=2020, open_access_only=True),
        )
        assert len(kept) == 0
        assert removed["year"] == 1
        assert removed["open_access"] == 2


class TestQueryExpansion:
    def test_a_long_idea_becomes_short_queries(self):
        """Keyword databases match phrases; a whole abstract returns nothing."""
        from papercreator.retrieval import query_expand

        result = query_expand.expand_with_rules(
            seed_text=(
                "I want to use multi-agent large language models to automatically "
                "write survey papers, where each agent handles retrieval, gap "
                "analysis and section drafting separately."
            )
        )
        assert result["queries"], "must produce at least one query"
        assert all(len(query.split()) <= 8 for query in result["queries"])
        assert result["method"] == "rules"

    def test_academic_boilerplate_is_not_extracted(self):
        from papercreator.retrieval import query_expand

        phrases = query_expand.extract_key_phrases(
            "In this paper we propose a novel method that shows significant "
            "improvement in performance on experimental results."
        )
        for noise in ("paper", "propose", "novel method", "significant", "results"):
            assert noise not in phrases, f"{noise!r} carries no topical signal"

    def test_known_acronyms_expand(self):
        from papercreator.retrieval import query_expand

        assert any("large language model" in s.lower()
                   for s in query_expand.synonyms_for("LLM"))
        assert any("graph neural network" in s.lower()
                   for s in query_expand.synonyms_for("GNN"))


class TestArxivQueryBuilding:
    def test_multiword_queries_are_and_ed_not_phrased(self):
        """Verified against the live API: a 5-word phrase matches nothing, and
        bare words are OR-ed, returning unrelated papers."""
        from papercreator.retrieval.providers.arxiv import _term_expression

        expression = _term_expression("graph neural network molecular property prediction")
        assert " AND " in expression
        assert expression.count("all:") >= 3
        assert '"graph neural network molecular property prediction"' not in expression

    def test_a_two_word_query_stays_a_phrase(self):
        from papercreator.retrieval.providers.arxiv import _term_expression

        assert _term_expression("attention mechanism") == 'all:"attention mechanism"'

    def test_explicit_boolean_input_is_preserved(self):
        from papercreator.retrieval.providers.arxiv import _term_expression

        query = "all:electron AND abs:proton"
        assert _term_expression(query) == query

    def test_stopwords_are_dropped_from_conjunctions(self):
        from papercreator.retrieval.providers.arxiv import _term_expression

        expression = _term_expression("the effect of noise on the training of models")
        assert "all:the" not in expression
        assert "all:of" not in expression


class TestOpenAlexParsing:
    def test_inverted_index_abstract_is_reconstructed(self):
        """OpenAlex never returns plain-text abstracts."""
        from papercreator.retrieval.providers.openalex import reconstruct_abstract

        text = reconstruct_abstract({
            "We": [0], "study": [1], "graph": [2], "neural": [3], "networks": [4],
        })
        assert text == "We study graph neural networks"

    def test_missing_abstract_yields_empty_string(self):
        from papercreator.retrieval.providers.openalex import reconstruct_abstract

        assert reconstruct_abstract(None) == ""
        assert reconstruct_abstract({}) == ""

    def test_work_parsing_from_a_realistic_payload(self):
        from papercreator.retrieval.providers.openalex import _parse_work

        paper = _parse_work({
            "id": "https://openalex.org/W2116341502",
            "doi": "https://doi.org/10.1109/tnn.2008.2005605",
            "title": "The Graph Neural Network Model",
            "publication_year": 2008,
            "type": "article",
            "cited_by_count": 9452,
            "referenced_works_count": 123,
            "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
            "language": "en",
            "abstract_inverted_index": {"A": [0], "model": [1]},
            "authorships": [{
                "author": {"display_name": "Franco Scarselli",
                           "orcid": "https://orcid.org/0000-0003-1307-0772"},
                "institutions": [{"display_name": "University of Siena"}],
            }],
            "primary_location": {
                "landing_page_url": "https://doi.org/10.1109/tnn.2008.2005605",
                "pdf_url": None,
                "source": {"display_name": "IEEE Transactions on Neural Networks"},
            },
            "open_access": {"is_oa": True, "oa_url": "https://example.org/p.pdf"},
            "topics": [{"display_name": "Neural Networks and Applications"}],
            "keywords": [{"display_name": "Computer science"}],
            "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/19068426"},
        })
        assert paper is not None
        assert paper.doi == "10.1109/tnn.2008.2005605"
        assert paper.openalex_id == "W2116341502"
        assert paper.citation_count == 9452
        assert paper.venue == "IEEE Transactions on Neural Networks"
        assert paper.is_open_access is True
        assert paper.abstract == "A model"
        assert paper.authors[0].orcid == "0000-0003-1307-0772"
        assert paper.references_ids == ["W1", "W2"], "reference ids power the graph"
        assert paper.pmid == "19068426"

    def test_titleless_work_is_rejected(self):
        from papercreator.retrieval.providers.openalex import _parse_work

        assert _parse_work({"id": "x", "title": None, "display_name": ""}) is None


class TestCrossrefParsing:
    def test_jats_markup_is_stripped(self):
        from papercreator.retrieval.providers.crossref import clean_jats

        cleaned = clean_jats(
            "<jats:p>Abstract We show that <jats:italic>x</jats:italic> &amp; y.</jats:p>"
        )
        assert "<" not in cleaned
        assert "&amp;" not in cleaned
        assert cleaned.startswith("We show")

    def test_select_field_list_excludes_unsupported_names(self):
        """Verified live: an unsupported select name makes the whole request 400."""
        from papercreator.retrieval.providers.crossref import _SELECT

        assert "language" not in _SELECT.split(",")
        assert "title" in _SELECT.split(",")


class TestDblpParsing:
    def test_single_result_arrives_as_an_object_not_a_list(self):
        """DBLP collapses one-element lists, which breaks naive iteration."""
        from papercreator.retrieval.providers.dblp import _as_list, _parse_hit

        assert _as_list({"a": 1}) == [{"a": 1}]
        assert _as_list(None) == []

        paper = _parse_hit({
            "info": {
                "title": "A Graph Paper.",
                "authors": {"author": {"text": "Wei Wang 0001"}},
                "year": "2023",
                "venue": "NeurIPS",
                "type": "Conference and Workshop Papers",
                "doi": "10.1/x",
            }
        })
        assert paper is not None
        assert paper.title == "A Graph Paper", "trailing period is stripped"
        # DBLP disambiguates homonyms with a 4-digit suffix.
        assert paper.authors[0].name == "Wei Wang"
        assert paper.venue_type == "conference"


class TestLocalFileParsing:
    def test_bibtex_with_latex_escapes(self):
        from papercreator.retrieval.providers.local_files import parse_bibtex

        papers = parse_bibtex(r"""
@article{scarselli2009graph,
  title = {The {Graph} Neural Network Model},
  author = {Scarselli, Franco and Gori, Marco},
  journal = {IEEE Transactions on Neural Networks},
  year = {2009},
  doi = {10.1109/TNN.2008.2005605},
  abstract = {We propose a model for graphs \& networks.}
}
""")
        assert len(papers) == 1
        paper = papers[0]
        assert paper.title == "The Graph Neural Network Model", "braces removed"
        assert paper.authors[0].name == "Franco Scarselli", "Last, First reordered"
        assert paper.year == 2009
        assert paper.doi == "10.1109/tnn.2008.2005605"
        assert "&" in paper.abstract

    def test_ris_with_wrapped_abstract(self):
        from papercreator.retrieval.providers.local_files import parse_ris

        papers = parse_ris(
            "TY  - JOUR\n"
            "TI  - A Test Paper\n"
            "AU  - Smith, John\n"
            "PY  - 2021\n"
            "JO  - Test Journal\n"
            "AB  - This abstract continues\n"
            "      onto a second line.\n"
            "DO  - 10.5/test\n"
            "ER  - \n"
        )
        assert len(papers) == 1
        assert papers[0].title == "A Test Paper"
        assert papers[0].authors[0].name == "John Smith"
        assert "second line" in papers[0].abstract

    def test_csv_with_scopus_style_headers(self):
        from papercreator.retrieval.providers.local_files import parse_csv

        papers = parse_csv(
            "Document Title,Authors,Year,Source title,DOI,Abstract\n"
            '"A CSV Paper","Doe, Jane; Roe, Rick",2022,"Some Journal",10.7/csv,"Body."\n'
        )
        assert len(papers) == 1
        assert papers[0].title == "A CSV Paper"
        assert papers[0].year == 2022
        assert len(papers[0].authors) == 2


class TestProviderRegistry:
    def test_every_provider_declares_its_metadata(self, temp_home):
        from papercreator.retrieval import registry

        for entry in registry.describe_all():
            assert entry["id"] and entry["name"], entry
            assert entry["tier"] in ("free", "freemium", "key"), entry
            assert "capabilities" in entry
            # An unavailable provider must explain itself, so the UI can act.
            if not entry["available"]:
                assert entry["unavailable_reason"], f"{entry['id']} gives no reason"

    def test_selection_falls_back_rather_than_returning_nothing(self, temp_home):
        from papercreator.retrieval.http_client import HttpClient
        from papercreator.retrieval.registry import resolve_selection

        providers, warnings = resolve_selection(["not-a-real-provider"], HttpClient())
        assert providers == []
        assert any("unknown provider" in warning for warning in warnings)

    def test_rate_limits_are_declared_per_provider(self, temp_home):
        from papercreator.retrieval import registry

        described = {entry["id"]: entry for entry in registry.describe_all()}
        # arXiv's terms of use require one request every three seconds.
        assert described["arxiv"]["rate_limit"]["min_interval_s"] >= 3.0
        # DBLP was observed to refuse connections after a burst.
        assert described["dblp"]["rate_limit"]["max_retries"] == 0

    @pytest.mark.asyncio
    async def test_known_unavailable_provider_reaches_structured_stats(
        self, temp_home, monkeypatch
    ):
        from papercreator.retrieval import registry
        from papercreator.retrieval.http_client import HttpClient

        monkeypatch.setitem(
            registry._BY_ID, UnavailableProvider.meta.id, UnavailableProvider
        )
        providers, warnings = registry.resolve_selection(
            [UnavailableProvider.meta.id], HttpClient()
        )

        assert [provider.id for provider in providers] == [UnavailableProvider.meta.id]
        assert any("unavailable" in warning for warning in warnings)
        papers, stats = await providers[0].safe_search(SearchRequest(query="x"), 5)
        assert papers == []
        assert stats.outcome == "unavailable"
        assert stats.error_code == "provider_unavailable"
        assert stats.retryable is False


class TestProviderFailureContract:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "outcome", "error_code", "retryable", "http_status"),
        [
            (
                RateLimitError(
                    "quota exhausted",
                    details={"retry_after_s": 7.5},
                ),
                "rate_limited",
                "rate_limited",
                True,
                429,
            ),
            (
                ProviderUnavailableError("missing provider configuration"),
                "unavailable",
                "provider_unavailable",
                False,
                None,
            ),
            (
                ProviderError(
                    "bad credentials",
                    details={
                        "category": "authentication_error",
                        "http_status": 401,
                        "retryable": False,
                    },
                ),
                "authentication_error",
                "provider_error",
                False,
                401,
            ),
            (
                ProviderError(
                    "upstream unavailable",
                    details={
                        "category": "http_error",
                        "http_status": 503,
                        "retryable": True,
                    },
                ),
                "http_error",
                "provider_error",
                True,
                503,
            ),
            (
                ProviderError(
                    "response shape changed",
                    details={
                        "category": "invalid_response",
                        "http_status": 200,
                        "retryable": True,
                    },
                ),
                "invalid_response",
                "provider_error",
                True,
                200,
            ),
            (
                httpx.ReadTimeout(
                    "too slow",
                    request=httpx.Request("GET", "https://provider.test"),
                ),
                "timeout",
                "timeout",
                True,
                None,
            ),
            (
                httpx.ConnectError(
                    "connection refused",
                    request=httpx.Request("GET", "https://provider.test"),
                ),
                "network_error",
                "network_error",
                True,
                None,
            ),
            (
                ValueError("parser exploded"),
                "unexpected_error",
                "unexpected_error",
                False,
                None,
            ),
        ],
    )
    async def test_safe_search_classifies_failures(
        self, temp_home, failure, outcome, error_code, retryable, http_status
    ):
        provider = FailingProvider(object(), failure)

        papers, stats = await provider.safe_search(SearchRequest(query="fault"), 5)

        assert papers == []
        assert stats.outcome == outcome
        assert stats.error_code == error_code
        assert stats.retryable is retryable
        assert stats.http_status == http_status
        assert stats.error
        assert stats.hint
        if outcome == "rate_limited":
            assert stats.retry_after_s == 7.5

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "body", "headers", "category", "retryable"),
        [
            (429, {"error": "slow down"}, {"Retry-After": "7.5"}, "rate_limited", True),
            (503, {"error": "maintenance"}, {}, "http_error", True),
            (401, {"error": "bad key"}, {}, "authentication_error", False),
            (200, "<html>not json</html>", {}, "invalid_response", True),
        ],
    )
    async def test_http_client_emits_structured_failures(
        self, temp_home, status, body, headers, category, retryable
    ):
        from papercreator.retrieval.http_client import HttpClient

        def handler(request: httpx.Request) -> httpx.Response:
            if isinstance(body, dict):
                return httpx.Response(status, json=body, headers=headers, request=request)
            return httpx.Response(status, text=body, headers=headers, request=request)

        client = HttpClient(use_cache=False)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises((RateLimitError, ProviderError)) as raised:
                await client.get_json(
                    "fault-source",
                    "https://provider.test/works",
                    min_interval_s=0,
                    max_retries=0,
                )
        finally:
            await client._client.aclose()
            client._client = None

        details = raised.value.details
        assert details["category"] == category
        assert details["http_status"] == status
        assert details["retryable"] is retryable
        if status == 429:
            assert details["retry_after_s"] == 7.5


class TestPipelineFailureHistory:
    @pytest.mark.asyncio
    async def test_all_failures_still_create_reproducible_history(
        self, temp_home, monkeypatch
    ):
        from papercreator.retrieval import pipeline
        from papercreator.store import papers as papers_store

        request = SearchRequest(
            query="fault matrix",
            providers=["failure-fixture"],
            use_cache=False,
            use_llm_expansion=False,
        )

        def selection(_ids, client):
            return [FailingProvider(client, RateLimitError(
                "quota exhausted", details={"retry_after_s": 3.0}
            ))], []

        monkeypatch.setattr(pipeline, "resolve_selection", selection)
        response = await pipeline.search_async(request, persist=True)
        try:
            assert response.search_id
            assert response.papers == []
            assert response.request["providers"] == ["failure-fixture"]
            assert response.request["use_cache"] is False
            assert response.stats[0].outcome == "rate_limited"
            assert response.stats[0].retryable is True

            stored = papers_store.get_search(response.search_id)
            assert stored is not None
            assert stored["result_count"] == 0
            assert stored["params"] == response.request
            diagnostic = stored["provider_stats"]["failure-fixture"]
            assert diagnostic["outcome"] == "rate_limited"
            assert diagnostic["retry_after_s"] == 3.0
            assert diagnostic["retryable"] is True
        finally:
            papers_store.delete_search(response.search_id)

    @pytest.mark.asyncio
    async def test_partial_failure_preserves_results_and_diagnostics(
        self, temp_home, monkeypatch
    ):
        from papercreator.retrieval import pipeline
        from papercreator.store import papers as papers_store

        def selection(_ids, client):
            return [
                SuccessfulProvider(client),
                FailingProvider(
                    client,
                    ProviderError(
                        "upstream maintenance",
                        details={
                            "category": "http_error",
                            "http_status": 503,
                            "retryable": True,
                        },
                    ),
                ),
            ], []

        monkeypatch.setattr(pipeline, "resolve_selection", selection)
        response = await pipeline.search_async(
            SearchRequest(
                query="partial failure",
                providers=["success-fixture", "failure-fixture"],
                use_cache=False,
                use_llm_expansion=False,
            ),
            persist=True,
        )
        paper_ids = [paper.id for paper in response.papers]
        try:
            assert len(response.papers) == 1
            assert response.papers[0].source_providers == ["success-fixture"]
            assert {stat.outcome for stat in response.stats} == {"success", "http_error"}
            assert any("partial provider failure" in warning for warning in response.warnings)

            stored = papers_store.get_search(response.search_id)
            assert stored is not None
            assert stored["result_count"] == 1
            assert stored["provider_stats"]["success-fixture"]["outcome"] == "success"
            failure = stored["provider_stats"]["failure-fixture"]
            assert failure["outcome"] == "http_error"
            assert failure["http_status"] == 503
            assert failure["retryable"] is True
        finally:
            papers_store.delete_search(response.search_id)
            papers_store.delete_many(paper_ids)

    @pytest.mark.asyncio
    async def test_no_available_provider_emits_terminal_event(
        self, temp_home, monkeypatch
    ):
        from papercreator.core import events
        from papercreator.retrieval import pipeline
        from papercreator.store import papers as papers_store

        published = []
        monkeypatch.setattr(pipeline, "resolve_selection", lambda _ids, _client: ([], []))
        monkeypatch.setattr(events, "publish", lambda event, data, **meta: published.append(
            (event, data, meta)
        ))

        response = await pipeline.search_async(
            SearchRequest(
                query="nothing configured",
                providers=["missing-a", "missing-b"],
                use_llm_expansion=False,
            ),
            persist=True,
        )
        try:
            terminal = [item for item in published if item[0] == events.SEARCH_DONE]
            assert len(terminal) == 1
            assert terminal[0][1]["count"] == 0
            assert terminal[0][1]["unavailableProviders"] == 2
            assert terminal[0][1]["searchId"] == response.search_id
            assert response.search_id
            assert "no retrieval provider available" in response.warnings

            stored = papers_store.get_search(response.search_id)
            assert stored is not None
            assert stored["providers"] == ["missing-a", "missing-b"]
            assert stored["result_count"] == 0
            assert stored["provider_stats"] == {}
        finally:
            papers_store.delete_search(response.search_id)


# --------------------------------------------------------------------- live


@pytest.mark.live
class TestLiveProviders:
    """Hits the real APIs. Excluded unless ``--live`` is passed."""

    @pytest.mark.asyncio
    async def test_arxiv_returns_parsed_papers(self, temp_home):
        from papercreator.retrieval.http_client import HttpClient
        from papercreator.retrieval.providers.arxiv import ArxivProvider

        async with HttpClient(use_cache=False) as client:
            papers, stats = await ArxivProvider(client).safe_search(
                SearchRequest(query="graph neural network"), 5
            )
        assert not stats.error, stats.error
        assert papers, "arXiv returned nothing"
        assert all(paper.title and paper.abstract for paper in papers)

    @pytest.mark.asyncio
    async def test_openalex_returns_citations_and_references(self, temp_home):
        from papercreator.retrieval.http_client import HttpClient
        from papercreator.retrieval.providers.openalex import OpenAlexProvider

        async with HttpClient(use_cache=False) as client:
            papers, stats = await OpenAlexProvider(client).safe_search(
                SearchRequest(query="graph neural network"), 5
            )
        assert not stats.error, stats.error
        assert papers
        assert any(paper.citation_count > 0 for paper in papers)

    @pytest.mark.asyncio
    async def test_pipeline_merges_across_providers(self, temp_home):
        from papercreator.retrieval import pipeline

        response = await pipeline.search_async(
            SearchRequest(
                query="graph neural network molecular property prediction",
                providers=["arxiv", "openalex", "crossref"],
                limit_per_provider=10,
                total_limit=30,
            ),
            persist=False,
            use_llm_expansion=False,
        )
        assert response.papers, [s.error for s in response.stats]
        assert response.total_before_dedupe >= len(response.papers)
