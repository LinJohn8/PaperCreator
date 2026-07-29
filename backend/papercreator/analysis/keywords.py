"""Keyword extraction and trend analysis.

Two jobs:

1. **Label the clusters.** A cluster of 30 papers needs a name a human can read.
   The method is class-based TF-IDF (c-TF-IDF), the technique BERTopic
   popularised: concatenate every document in a cluster into one pseudo-document,
   then score terms by how characteristic they are of *that* cluster versus the
   others. Plain TF-IDF per document cannot do this - it finds what distinguishes
   one paper, not one topic.

2. **Drive the heatmap layers and trend view.** Per-term statistics (frequency,
   first/last year, trend slope) let the UI colour the map by any keyword and
   show which terms are rising or fading - which is where research gaps often
   become visible.

Term extraction favours noun-phrase-shaped n-grams. Author-supplied keywords and
MeSH terms (from PubMed/OpenAlex) are trusted and weighted up, because they are
human-curated and far cleaner than anything extracted from prose.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from ..core.logging_setup import get_logger
from ..core.models import KeywordStat, Paper
from ..core.util import collapse_ws

log = get_logger(__name__)

# Domain-independent noise: stopwords plus academic prose that appears in every
# abstract and therefore distinguishes nothing.
STOPWORDS: set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "also", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "cannot", "could", "did", "do",
    "does", "doing", "don", "down", "during", "each", "few", "for", "from",
    "further", "had", "has", "have", "having", "he", "her", "here", "hers", "him",
    "his", "how", "however", "i", "if", "in", "into", "is", "it", "its", "itself",
    "just", "may", "me", "might", "more", "most", "must", "my", "no", "nor", "not",
    "now", "of", "off", "on", "once", "only", "or", "other", "ought", "our",
    "ours", "out", "over", "own", "same", "shall", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "then", "there",
    "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while",
    "who", "whom", "why", "will", "with", "would", "you", "your", "yours",
    # academic boilerplate
    "abstract", "paper", "papers", "study", "studies", "work", "works",
    "research", "propose", "proposed", "proposes", "present", "presents",
    "presented", "show", "shows", "shown", "result", "results", "method",
    "methods", "methodology", "approach", "approaches", "based", "using", "use",
    "used", "uses", "novel", "new", "recent", "recently", "state", "art",
    "existing", "previous", "prior", "various", "several", "different",
    "significant", "significantly", "important", "effective", "efficient",
    "experiment", "experiments", "experimental", "evaluate", "evaluated",
    "evaluation", "performance", "achieve", "achieves", "achieved", "improve",
    "improves", "improved", "improvement", "compared", "comparison", "provide",
    "provides", "provided", "demonstrate", "demonstrates", "demonstrated",
    "introduce", "introduces", "conclusion", "conclusions", "furthermore",
    "moreover", "therefore", "thus", "hence", "additionally", "finally",
    "first", "second", "third", "one", "two", "three", "many", "much", "well",
    "high", "low", "large", "small", "better", "best", "good", "make", "makes",
    "made", "given", "give", "gives", "found", "find", "finds", "observed",
    "observe", "obtained", "obtain", "considered", "consider", "including",
    "include", "includes", "respectively", "et", "al", "eg", "ie", "etc",
    "figure", "table", "section", "appendix", "supplementary", "however",
    "although", "though", "whereas", "via", "towards", "toward", "within",
    "across", "among", "along", "around", "upon", "without", "per", "vs",
    # Contentless words that appeared as "emerging trends" in a real 150-paper
    # corpus and told the user nothing. Safe to block anywhere in a phrase
    # because no technical term is built from them.
    "key", "capture", "captures", "captured", "limited", "accurate",
    "potential", "possible", "possibility", "ability", "aspect", "aspects",
    "detail", "details", "insight", "insights", "overview", "summary",
    "usage", "advance", "advances", "progress", "purpose", "role",
    "situation", "issue", "issues", "kind", "kinds", "amount", "way", "ways",
}

# Words that are meaningless *on their own* but essential inside a phrase.
# Blocking them in STOPWORDS would be wrong: it would reject "line graph",
# "molecular structure" and "latent space" at the n-gram boundary check. So
# they are filtered only when a term is a single word.
#
# ``structure`` alone is noise; ``molecular structure`` is a topic.
_PHRASE_ONLY: set[str] = {
    "structure", "structures", "structural", "property", "properties",
    "function", "functions", "functional", "feature", "features", "factor",
    "factors", "parameter", "parameters", "variable", "variables",
    "condition", "conditions", "state", "states", "form", "forms",
    "information", "knowledge", "understanding", "analysis", "analyses",
    "review", "framework", "architecture", "design", "designs", "strategy",
    "strategies", "technique", "techniques", "tool", "tools", "algorithm",
    "algorithms", "implementation", "application", "applications",
    "development", "developments", "requirement", "requirements", "goal",
    "goals", "objective", "impact", "effect", "effects", "influence",
    "change", "changes", "difference", "differences", "similarity",
    "relation", "relations", "relationship", "relationships", "correlation",
    "association", "system", "systems", "process", "processes", "problem",
    "problems", "solution", "solutions", "challenge", "challenges", "field",
    "fields", "area", "areas", "domain", "domains", "context", "contexts",
    "range", "ranges", "size", "sizes", "scale", "scales", "rate", "rates",
    "time", "times", "step", "steps", "stage", "stages", "point", "points",
    "line", "lines", "space", "spaces", "level", "levels", "type", "types",
    "case", "cases", "example", "examples", "part", "parts", "set", "sets",
    "group", "groups", "value", "values", "number", "numbers", "accuracy",
    "precision", "recall", "score", "scores", "term", "terms",
    # Domain-generic heads: alone they describe the whole corpus, not a topic.
    "model", "models", "network", "networks", "learning", "prediction",
    "classification", "detection", "generation", "representation",
    "optimization", "estimation", "graph", "graphs", "attention",
    "transformer", "embedding", "embeddings", "data", "dataset", "training",
    "inference", "accuracy",
}

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+\-#.]{1,}")
_YEAR_LIKE = re.compile(r"^\d{4}$")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text or "")]


def extract_terms(text: str, *, max_n: int = 3) -> list[str]:
    """n-grams (1..max_n) that look like meaningful terms.

    An n-gram is kept when it neither starts nor ends with a stopword, which is a
    cheap approximation of noun-phrase boundaries and works well on the compound
    technical terms that dominate paper titles ("graph neural network", "molecular
    property prediction").
    """
    tokens = _tokens(text)
    terms: list[str] = []
    for size in range(1, max_n + 1):
        for index in range(len(tokens) - size + 1):
            gram = tokens[index: index + size]
            if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                continue
            if any(_YEAR_LIKE.match(t) for t in gram):
                continue
            if size == 1:
                word = gram[0]
                # Single words face the extra bar: too short, a stopword, or a
                # generic head that only means something inside a phrase.
                if len(word) < 3 or word in STOPWORDS or word in _PHRASE_ONLY:
                    continue
            terms.append(" ".join(gram))
    return terms


def paper_terms(paper: Paper, *, weight_curated: int = 3) -> list[str]:
    """All terms for one paper, with curated keywords repeated for weight.

    Repetition is how weighting is expressed to a bag-of-words counter: a MeSH
    term or author keyword counts ``weight_curated`` times, so human-assigned
    vocabulary outranks incidentally frequent prose.
    """
    terms = extract_terms(paper.title, max_n=3)
    terms.extend(extract_terms(paper.abstract, max_n=3))
    for curated in [*paper.keywords, *paper.fields_of_study]:
        cleaned = collapse_ws(curated).lower()
        if cleaned and cleaned not in STOPWORDS:
            terms.extend([cleaned] * weight_curated)
    return terms


def class_tfidf(
    documents: dict[int, list[str]], *, top_k: int = 12, min_count: int = 2
) -> dict[int, list[tuple[str, float]]]:
    """c-TF-IDF: terms characteristic of each class.

    For class ``c`` and term ``t``::

        score(t, c) = tf(t, c) * log(1 + avg_class_size / total_freq(t))

    ``tf`` is the term's frequency within the class, normalised by class size so
    a big cluster does not dominate. The log factor down-weights terms that are
    frequent everywhere ("neural network" in an all-ML corpus), which is exactly
    the behaviour needed for readable cluster labels.
    """
    class_counts: dict[int, Counter[str]] = {
        label: Counter(terms) for label, terms in documents.items()
    }
    class_sizes = {label: max(1, sum(counter.values()))
                   for label, counter in class_counts.items()}
    total_freq: Counter[str] = Counter()
    for counter in class_counts.values():
        total_freq.update(counter)
    average_size = (
        sum(class_sizes.values()) / len(class_sizes) if class_sizes else 1.0
    )

    out: dict[int, list[tuple[str, float]]] = {}
    for label, counter in class_counts.items():
        scored: list[tuple[str, float]] = []
        for term, count in counter.items():
            if total_freq[term] < min_count:
                continue
            term_frequency = count / class_sizes[label]
            inverse = math.log(1.0 + average_size / max(1, total_freq[term]))
            score = term_frequency * inverse
            # Multi-word terms are more informative labels than single words at
            # comparable scores.
            score *= 1.0 + 0.25 * (term.count(" "))
            scored.append((term, score))
        scored.sort(key=lambda pair: -pair[1])
        out[label] = _drop_subsumed(scored, top_k)
    return out


def _drop_subsumed(
    scored: list[tuple[str, float]], top_k: int
) -> list[tuple[str, float]]:
    """Remove a term that is a substring of a better-scoring one.

    Prevents labels like "graph, graph neural, graph neural network".
    """
    kept: list[tuple[str, float]] = []
    for term, score in scored:
        if any(term != other and term in other for other, _ in kept):
            continue
        kept.append((term, score))
        if len(kept) >= top_k:
            break
    return kept


def cluster_keywords(
    papers: list[Paper],
    labels: np.ndarray,
    *,
    top_k: int = 12,
) -> dict[int, list[str]]:
    """Keywords per cluster via c-TF-IDF. Noise (-1) is excluded."""
    documents: dict[int, list[str]] = defaultdict(list)
    for paper, label in zip(papers, labels):
        value = int(label)
        if value < 0:
            continue
        documents[value].extend(paper_terms(paper))
    if not documents:
        return {}
    scored = class_tfidf(documents, top_k=top_k)
    return {label: [term for term, _ in terms] for label, terms in scored.items()}


def label_cluster(keywords: list[str], *, max_terms: int = 3) -> str:
    """Build a short human label from a cluster's keywords.

    Prefers multi-word terms (more specific) and title-cases the result. Kept
    deliberately mechanical; an LLM pass can refine labels later, but the map has
    to be readable without any model configured.
    """
    if not keywords:
        return "Unlabelled"
    multi = [k for k in keywords if " " in k]
    chosen = (multi or keywords)[:max_terms]
    seen: set[str] = set()
    parts: list[str] = []
    for term in chosen:
        words = tuple(term.split())
        if any(w in seen for w in words):
            continue
        seen.update(words)
        parts.append(term)
    return " / ".join(p.title() for p in parts[:max_terms]) or "Unlabelled"


def global_keyword_stats(
    papers: list[Paper],
    labels: np.ndarray | None = None,
    *,
    top_k: int = 60,
    min_papers: int = 2,
) -> list[KeywordStat]:
    """Corpus-wide term statistics with a temporal trend.

    ``trend`` is the slope of a least-squares line through per-year document
    frequency, normalised by the term's mean frequency. Positive means the term
    is being used in a growing share of papers. Slope-of-normalised-frequency
    rather than raw counts, because the corpus itself grows over time and raw
    counts would show every term "rising".
    """
    document_frequency: Counter[str] = Counter()
    per_year: dict[str, Counter[int]] = defaultdict(Counter)
    first_year: dict[str, int] = {}
    last_year: dict[str, int] = {}
    term_clusters: dict[str, set[int]] = defaultdict(set)
    papers_per_year: Counter[int] = Counter()

    for index, paper in enumerate(papers):
        terms = set(paper_terms(paper, weight_curated=1))
        document_frequency.update(terms)
        year = paper.year
        if year:
            papers_per_year[year] += 1
        label = int(labels[index]) if labels is not None and index < len(labels) else -1
        for term in terms:
            if year:
                per_year[term][year] += 1
                first_year[term] = min(first_year.get(term, year), year)
                last_year[term] = max(last_year.get(term, year), year)
            if label >= 0:
                term_clusters[term].add(label)

    candidates = [
        (term, count) for term, count in document_frequency.most_common(top_k * 6)
        if count >= min_papers
    ]
    stats: list[KeywordStat] = []
    for term, count in candidates:
        years = per_year.get(term) or {}
        trend = 0.0
        if len(years) >= 3:
            xs = np.array(sorted(years), dtype=np.float64)
            # Share of that year's papers using the term, so corpus growth does
            # not masquerade as a rising trend.
            ys = np.array(
                [years[int(y)] / max(1, papers_per_year[int(y)]) for y in xs],
                dtype=np.float64,
            )
            if ys.mean() > 0:
                slope = float(np.polyfit(xs, ys, 1)[0])
                trend = round(slope / ys.mean(), 4)
        # Score blends prevalence with specificity, so a term used by 3 papers in
        # one cluster can outrank a bland term used by 20 across all clusters.
        specificity = 1.0 / max(1, len(term_clusters.get(term, ())))
        score = (count / max(1, len(papers))) * (0.5 + 0.5 * specificity)
        score *= 1.0 + 0.2 * term.count(" ")
        stats.append(KeywordStat(
            term=term,
            count=count,
            score=round(score, 5),
            first_year=first_year.get(term),
            last_year=last_year.get(term),
            trend=trend,
            cluster_ids=sorted(term_clusters.get(term, ())),
        ))
    stats.sort(key=lambda s: -s.score)
    deduped = _drop_subsumed_stats(stats, top_k)
    return deduped


def _drop_subsumed_stats(stats: list[KeywordStat], top_k: int) -> list[KeywordStat]:
    kept: list[KeywordStat] = []
    for stat in stats:
        if any(stat.term != other.term and stat.term in other.term for other in kept):
            continue
        kept.append(stat)
        if len(kept) >= top_k:
            break
    return kept


def emerging_and_fading(
    stats: list[KeywordStat], *, limit: int = 10, min_count: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """Split terms into rising and declining, for the trend panel.

    Requires a recent last_year for "emerging": a term with a positive slope that
    stopped appearing three years ago is not emerging.
    """
    from ..core.util import utc_now

    current_year = utc_now().year
    eligible = [s for s in stats if s.count >= min_count and s.trend != 0.0]
    emerging = sorted(
        (s for s in eligible if s.trend > 0 and (s.last_year or 0) >= current_year - 2),
        key=lambda s: -s.trend,
    )[:limit]
    fading = sorted((s for s in eligible if s.trend < 0), key=lambda s: s.trend)[:limit]
    return {
        "emerging": [
            {"term": s.term, "count": s.count, "trend": s.trend,
             "first_year": s.first_year, "last_year": s.last_year}
            for s in emerging
        ],
        "fading": [
            {"term": s.term, "count": s.count, "trend": s.trend,
             "first_year": s.first_year, "last_year": s.last_year}
            for s in fading
        ],
    }


def keyword_paper_map(
    papers: list[Paper], terms: list[str]
) -> dict[str, list[int]]:
    """Which paper indices contain each term - used to build heatmap layers."""
    wanted = {t.lower() for t in terms}
    out: dict[str, list[int]] = {t: [] for t in wanted}
    for index, paper in enumerate(papers):
        haystack = " ".join([
            paper.title.lower(), paper.abstract.lower(),
            " ".join(paper.keywords).lower(),
            " ".join(paper.fields_of_study).lower(),
        ])
        for term in wanted:
            if term in haystack:
                out[term].append(index)
    return out
