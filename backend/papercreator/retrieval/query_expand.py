"""Query expansion: turning an idea or a paper into effective search queries.

The requirement is "give me the papers related to my idea or to this paper".
A raw idea paragraph is a terrible query for keyword-based sources (arXiv,
PubMed, DBLP): they do prefix/phrase matching and a 200-word blob matches
nothing. So the seed text has to become a small set of *good* queries.

Two strategies, in order of preference:

1. **LLM expansion** (:func:`expand_with_llm`) - when a model is configured, ask
   it for search-engine-shaped queries plus discipline-appropriate synonyms and
   the canonical term for the concept. This handles "the thing I mean is called
   X in the literature", which no rule can.
2. **Rule-based expansion** (:func:`expand_with_rules`) - always available,
   zero cost, no network. Extracts candidate key phrases by frequency and
   position, drops stopwords, and adds known term variants.

The rule-based path is not a degraded fallback to apologise for - it runs first
to seed the LLM prompt, and its output is merged with the LLM's so a model
hallucinating irrelevant terms cannot displace terms that are demonstrably in
the user's own text.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..core.logging_setup import get_logger
from ..core.util import collapse_ws, dedupe_preserving_order, detect_language

log = get_logger(__name__)

# Stopwords plus academic boilerplate that carries no topical signal.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "of", "in", "on", "at", "to", "for", "with", "without",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "can", "could", "will", "would",
    "should", "may", "might", "must", "we", "our", "us", "they", "their", "it",
    "its", "he", "she", "his", "her", "you", "your", "i", "my", "not", "no",
    "such", "which", "who", "whom", "whose", "what", "when", "where", "how",
    "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "only", "own", "same", "so", "too", "very", "just", "also", "however",
    "paper", "papers", "study", "studies", "propose", "proposed", "proposes",
    "present", "presents", "presented", "show", "shows", "shown", "results",
    "result", "method", "methods", "approach", "approaches", "based", "using",
    "use", "used", "uses", "novel", "new", "recent", "recently", "work",
    "works", "research", "however", "moreover", "furthermore", "thus",
    "therefore", "although", "while", "since", "because", "between", "among",
    "into", "through", "during", "before", "after", "above", "below", "up",
    "down", "out", "off", "over", "under", "again", "further", "once", "here",
    "there", "why", "abstract", "introduction", "conclusion", "et", "al",
    "figure", "table", "section", "eq", "ie", "eg", "etc", "one", "two",
    "three", "first", "second", "third", "many", "much", "well", "make",
    "makes", "made", "given", "give", "gives", "significant", "significantly",
    "important", "different", "various", "several", "large", "small", "high",
    "low", "better", "best", "good", "improve", "improves", "improved",
    "improvement", "performance", "experiments", "experimental", "evaluate",
    "evaluation", "dataset", "datasets", "task", "tasks", "model", "models",
}

# Domain term variants worth searching alongside the user's phrasing.
# Bidirectional: hitting either side adds the other.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "llm": ("large language model", "language model"),
    "large language model": ("LLM", "foundation model"),
    "gnn": ("graph neural network",),
    "graph neural network": ("GNN", "graph representation learning"),
    "cnn": ("convolutional neural network",),
    "convolutional neural network": ("CNN",),
    "rl": ("reinforcement learning",),
    "reinforcement learning": ("RL", "policy learning"),
    "nlp": ("natural language processing",),
    "natural language processing": ("NLP", "computational linguistics"),
    "transformer": ("attention mechanism", "self-attention"),
    "vit": ("vision transformer",),
    "rag": ("retrieval augmented generation", "retrieval-augmented generation"),
    "retrieval augmented generation": ("RAG",),
    "federated learning": ("distributed learning", "privacy-preserving learning"),
    "knowledge graph": ("knowledge base", "semantic network"),
    "few-shot": ("few shot learning", "low-resource"),
    "zero-shot": ("zero shot learning",),
    "self-supervised": ("self supervised learning", "contrastive learning"),
    "multimodal": ("multi-modal", "vision-language"),
    "diffusion model": ("denoising diffusion", "score-based generative model"),
    "explainability": ("interpretability", "XAI", "explainable AI"),
    "interpretability": ("explainability", "explainable AI"),
    "anomaly detection": ("outlier detection", "novelty detection"),
    "time series": ("temporal data", "sequential data"),
    "agent": ("autonomous agent", "multi-agent system"),
    "multi-agent": ("multiagent system", "agent collaboration"),
    "fine-tuning": ("fine tuning", "parameter-efficient fine-tuning", "PEFT"),
    "prompt engineering": ("prompting", "in-context learning"),
}

_ACRONYM = re.compile(r"\b([A-Z]{2,6})\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")


def extract_key_phrases(text: str, *, top_k: int = 8) -> list[str]:
    """Candidate topical phrases from free text.

    Scores n-grams (1-3 words) by frequency, boosting those appearing in the
    first sentence - in an abstract, the topic is almost always stated there.
    Phrases consisting only of stopwords are dropped.
    """
    cleaned = collapse_ws(text)
    if not cleaned:
        return []
    sentences = _SENTENCE_SPLIT.split(cleaned)
    first_sentence = sentences[0].lower() if sentences else ""

    words = [w for w in re.split(r"[^\w\-]+", cleaned.lower()) if w]
    scores: Counter[str] = Counter()
    for size in (3, 2, 1):
        for index in range(len(words) - size + 1):
            gram_words = words[index: index + size]
            if gram_words[0] in _STOPWORDS or gram_words[-1] in _STOPWORDS:
                continue
            if all(w in _STOPWORDS or len(w) < 3 for w in gram_words):
                continue
            gram = " ".join(gram_words)
            if len(gram) < 4:
                continue
            # Longer phrases are more specific, so weight them up.
            weight = 1.0 + 0.6 * (size - 1)
            if gram in first_sentence:
                weight *= 1.8
            scores[gram] += weight

    # Drop a shorter phrase fully contained in a higher-scoring longer one to
    # avoid returning "graph neural" alongside "graph neural network".
    ordered = [phrase for phrase, _ in scores.most_common(top_k * 4)]
    kept: list[str] = []
    for phrase in ordered:
        if any(phrase != other and phrase in other for other in kept):
            continue
        kept.append(phrase)
        if len(kept) >= top_k:
            break
    return kept


def extract_acronyms(text: str) -> list[str]:
    """Uppercase acronyms, which are often the literature's canonical term."""
    found = [a for a in _ACRONYM.findall(text or "") if a.lower() not in _STOPWORDS]
    return dedupe_preserving_order(found)[:6]


def synonyms_for(term: str) -> list[str]:
    lowered = term.lower().strip()
    out: list[str] = list(_SYNONYMS.get(lowered, ()))
    # Also match when a known key appears inside a longer phrase.
    for key, values in _SYNONYMS.items():
        if key != lowered and key in lowered:
            out.extend(values)
    return dedupe_preserving_order(out)[:4]


def expand_with_rules(
    *, query: str = "", seed_text: str = "", max_queries: int = 6
) -> dict[str, Any]:
    """Deterministic expansion. Always available, no network, no cost."""
    source = seed_text or query
    phrases = extract_key_phrases(source, top_k=8)
    acronyms = extract_acronyms(source)

    queries: list[str] = []
    if query.strip():
        queries.append(collapse_ws(query))

    # Top phrases individually, then the strongest pair as a conjunction - the
    # pair is usually the actual research question ("graph neural network" +
    # "molecular property prediction").
    for phrase in phrases[:4]:
        queries.append(phrase)
    if len(phrases) >= 2:
        queries.append(f"{phrases[0]} {phrases[1]}")

    synonym_terms: list[str] = []
    for term in [*phrases[:3], *acronyms[:2]]:
        for synonym in synonyms_for(term):
            synonym_terms.append(synonym)
            queries.append(synonym)

    return {
        "queries": dedupe_preserving_order(
            [q for q in (collapse_ws(x) for x in queries) if len(q) > 2]
        )[:max_queries],
        "key_phrases": phrases,
        "acronyms": acronyms,
        "synonyms": dedupe_preserving_order(synonym_terms)[:8],
        "method": "rules",
        "language": detect_language(source),
    }


_LLM_SYSTEM = """You turn a researcher's idea or paper abstract into effective \
academic search queries.

Rules:
- Output STRICT JSON only, no prose, no markdown fence.
- Queries must be 2-6 words, the phrasing an indexed database would match.
- Prefer the canonical term the literature actually uses over the user's wording.
- Include the field's standard synonyms and expanded acronyms.
- Never invent a technique that is not implied by the input.

Schema:
{"queries": ["..."], "key_phrases": ["..."], "synonyms": ["..."],
 "canonical_terms": ["..."], "research_area": "...", "notes": "..."}"""


async def expand_with_llm(
    *,
    query: str = "",
    seed_text: str = "",
    max_queries: int = 6,
    rule_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """LLM-assisted expansion.

    Falls back to :func:`expand_with_rules` when no model is configured or the
    call fails - expansion is an enhancement and must never break a search.
    """
    from ..llm import registry as llm_registry
    from ..llm.client import complete_json

    baseline = rule_hint or expand_with_rules(
        query=query, seed_text=seed_text, max_queries=max_queries
    )
    if not llm_registry.has_any_provider():
        baseline["notes"] = "no LLM configured; used rule-based expansion"
        return baseline

    source = (seed_text or query).strip()
    prompt = (
        f"Researcher input ({'idea' if not seed_text or len(source) < 400 else 'abstract'}):\n"
        f"{source[:4000]}\n\n"
        f"Rule-extracted candidate phrases (use them if they are right, replace "
        f"them if the literature uses different terms): "
        f"{', '.join(baseline.get('key_phrases', [])[:8])}\n\n"
        f"Produce at most {max_queries} queries."
    )
    try:
        data = await complete_json(
            prompt, system=_LLM_SYSTEM, purpose="query_expansion", max_tokens=900
        )
    except Exception as exc:  # noqa: BLE001 - expansion must not fail the search
        log.warning("LLM query expansion failed (%s); using rules", exc)
        baseline["notes"] = f"LLM expansion failed: {exc}"
        return baseline

    llm_queries = [
        collapse_ws(str(q)) for q in (data.get("queries") or []) if str(q).strip()
    ]
    # Merge: the user's own query first, then LLM suggestions, then rules. This
    # ordering means a hallucinated term can add noise but never displace the
    # terms actually present in the user's text.
    merged = dedupe_preserving_order([
        *( [collapse_ws(query)] if query.strip() else [] ),
        *llm_queries,
        *baseline.get("queries", []),
    ])[:max_queries]
    return {
        "queries": merged,
        "key_phrases": dedupe_preserving_order([
            *(str(p) for p in (data.get("key_phrases") or [])),
            *baseline.get("key_phrases", []),
        ])[:10],
        "acronyms": baseline.get("acronyms", []),
        "synonyms": dedupe_preserving_order([
            *(str(s) for s in (data.get("synonyms") or [])),
            *baseline.get("synonyms", []),
        ])[:10],
        "canonical_terms": [str(t) for t in (data.get("canonical_terms") or [])][:8],
        "research_area": str(data.get("research_area") or ""),
        "method": "llm",
        "language": baseline.get("language", "en"),
        "notes": str(data.get("notes") or ""),
    }


async def expand(
    *,
    query: str = "",
    seed_text: str = "",
    use_llm: bool = True,
    max_queries: int = 6,
) -> dict[str, Any]:
    """Entry point used by the search pipeline."""
    rules = expand_with_rules(query=query, seed_text=seed_text, max_queries=max_queries)
    if not use_llm:
        return rules
    return await expand_with_llm(
        query=query, seed_text=seed_text, max_queries=max_queries, rule_hint=rules
    )
