"""Text embedding with a tiered backend and a persistent cache.

Everything downstream - the 3D landscape, clustering, gap detection, placing the
user's own idea - is a function of these vectors, so this module has to work on a
plain ``pip install`` with no model download, and get better when the user adds
the optional stack.

Backends, in ``auto`` preference order:

1. ``sentence-transformers`` - a real semantic model (default
   ``all-MiniLM-L6-v2``, 384-d, ~90 MB). Best quality; needs the ``analysis``
   extra and one download.
2. ``llm`` - an embedding endpoint from the configured LLM provider (OpenAI
   ``text-embedding-3-small`` and compatibles). No local model, costs money,
   needs network.
3. ``tfidf`` - scikit-learn TF-IDF reduced by truncated SVD (LSA). Genuinely
   useful: for a few hundred abstracts on one topic, LSA topic structure is
   close enough for a landscape, and it needs nothing beyond the base install.
4. ``hashing`` - hashed character n-grams. The last resort, used when even
   scikit-learn is unavailable. Weak but never fails.

Tiers 3 and 4 are *corpus-relative*: they fit on the papers being analysed, so
their vectors are not comparable across analyses. :func:`embed_papers` records
which backend produced a set of vectors, and :mod:`analysis.incremental` refuses
to place a new paper into a corpus-relative space without refitting.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..core.config import get_settings
from ..core.logging_setup import get_logger
from ..core.models import Paper
from ..core.util import sha256_text
from ..store import analyses as analyses_store

log = get_logger(__name__)

# Backends that fit on the corpus and therefore produce non-portable vectors.
# TF-IDF fits vocabulary/IDF/SVD to the current corpus. The hashing backend is
# different: fixed MD5 buckets plus per-document L2 normalisation make the same
# text produce the same 256-dimensional vector in every run and corpus.
CORPUS_RELATIVE = {"tfidf"}

_st_model: Any = None
_st_model_name = ""
_st_lock = threading.Lock()


@dataclass
class EmbeddingResult:
    vectors: np.ndarray          # (n, dim) float32, L2-normalised
    model: str                   # cache key, e.g. "st:all-MiniLM-L6-v2"
    backend: str                 # sentence-transformers | llm | tfidf | hashing
    dim: int
    corpus_relative: bool
    cache_hits: int = 0
    computed: int = 0
    warnings: list[str] | None = None

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


def _normalise(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise rows so dot product equals cosine similarity.

    Every consumer (clustering with a cosine metric, nearest-neighbour lookup,
    centroid coherence) assumes unit vectors, so this happens once here.
    """
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


# ------------------------------------------------------- sentence-transformers


def _apply_hf_environment() -> dict[str, Any]:
    """Point Hugging Face at a mirror / offline mode.

    Setting the environment variable is **not sufficient on its own**:
    ``huggingface_hub`` reads ``HF_ENDPOINT`` once, at import time, into
    ``huggingface_hub.constants.ENDPOINT``. Verified on this machine - with the
    mirror configured but applied after import, a model load still went to the
    blocked huggingface.co and spent 286 seconds exhausting its retry ladder
    before failing.

    So this does both: exports the variables (for any not-yet-imported consumer)
    and patches the already-imported module constants. It is idempotent and is
    called at import of this module, from the app factory, and again before each
    model load.
    """
    settings = get_settings().analysis
    endpoint = (settings.hf_endpoint or os.environ.get("HF_ENDPOINT") or "").rstrip("/")
    applied: dict[str, Any] = {"endpoint": endpoint or "https://huggingface.co"}

    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint
    if settings.offline_models:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    # Bound the hub's own retries. The defaults are long enough that a blocked
    # host looks like a hang rather than a failure the caller can degrade from.
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "8")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

    if endpoint:
        applied["patched"] = _patch_hub_endpoint(endpoint)
    return applied


# Every module-level constant that pins the model host, across both libraries.
# Verified by inspection on transformers 4.44 / huggingface_hub 0.36: setting
# HF_ENDPOINT after import leaves all of these at the default, and `transformers`
# keeps its *own* copy (HUGGINGFACE_CO_RESOLVE_ENDPOINT) that is not derived from
# huggingface_hub's at call time. Missing it means downloads still go to the
# blocked host even though huggingface_hub is pointed at the mirror.
_ENDPOINT_ATTRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("huggingface_hub.constants",
     ("ENDPOINT", "HUGGINGFACE_CO_URL_TEMPLATE", "HUGGINGFACE_CO_API_URL")),
    ("huggingface_hub.file_download",
     ("ENDPOINT", "HUGGINGFACE_CO_URL_TEMPLATE")),
    ("huggingface_hub.hf_api", ("ENDPOINT", "HUGGINGFACE_CO_API_URL")),
    ("huggingface_hub._commit_api", ("ENDPOINT",)),
    ("transformers.utils.hub",
     ("HUGGINGFACE_CO_RESOLVE_ENDPOINT", "_default_endpoint",
      "HUGGINGFACE_CO_PREFIX")),
    ("transformers.utils", ("HUGGINGFACE_CO_RESOLVE_ENDPOINT",)),
    ("transformers.configuration_utils", ("HUGGINGFACE_CO_RESOLVE_ENDPOINT",)),
    ("transformers.modeling_utils", ("HUGGINGFACE_CO_RESOLVE_ENDPOINT",)),
    ("transformers.tokenization_utils_base",
     ("HUGGINGFACE_CO_RESOLVE_ENDPOINT",)),
)


def _endpoint_value(attribute: str, endpoint: str) -> str:
    """The value a given constant should hold for ``endpoint``."""
    if attribute == "HUGGINGFACE_CO_URL_TEMPLATE":
        return f"{endpoint}/{{repo_id}}/resolve/{{revision}}/{{filename}}"
    if attribute == "HUGGINGFACE_CO_API_URL":
        return f"{endpoint}/api"
    if attribute == "HUGGINGFACE_CO_PREFIX":
        return f"{endpoint}/{{model_id}}/resolve/{{revision}}/{{filename}}"
    return endpoint


def _patch_hub_endpoint(endpoint: str) -> list[str]:
    """Rewrite the frozen endpoint constants in both HF libraries, if loaded.

    Only touches modules already in ``sys.modules`` - importing them here to patch
    them would defeat the purpose, since the goal is to have set the environment
    variable before the first import. Returns the names changed, which the
    diagnostics surface so a stale endpoint is visible rather than silent.
    """
    patched: list[str] = []
    for module_name, attributes in _ENDPOINT_ATTRS:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attribute in attributes:
            if not hasattr(module, attribute):
                continue
            value = _endpoint_value(attribute, endpoint)
            if getattr(module, attribute) != value:
                setattr(module, attribute, value)
                patched.append(f"{module_name}.{attribute}")

    # The default HfApi instance caches the endpoint on itself.
    hub = sys.modules.get("huggingface_hub")
    default_api = getattr(hub, "_default_api", None) if hub else None
    if default_api is not None and getattr(default_api, "endpoint", None) != endpoint:
        default_api.endpoint = endpoint
        patched.append("huggingface_hub._default_api.endpoint")
    return patched


def sentence_transformers_package_available() -> bool:
    """Is the package importable? Says nothing about model availability.

    Applies the endpoint configuration *first*: this function is the usual place
    ``huggingface_hub`` gets imported for the first time, and once it is imported
    its endpoint constant is frozen.
    """
    _apply_hf_environment()
    try:
        import sentence_transformers  # noqa: F401
    except Exception:  # noqa: BLE001 - torch/transformers import can fail many ways
        return False
    return True


def local_model_path(model_name: str) -> Path | None:
    """Locate an already-downloaded model directory, or ``None``.

    Three layouts are recognised, because users arrive at a local model three
    different ways:

    * ``<models>/all-MiniLM-L6-v2/`` - manually downloaded and unzipped. This is
      the standard workaround when the model host is blocked, so it is supported
      as a first-class path rather than only as an accident.
    * ``<models>/sentence-transformers_all-MiniLM-L6-v2/`` - older
      sentence-transformers cache layout.
    * ``<cache>/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/<sha>/``
      - the current Hugging Face hub layout.

    A directory only counts when it actually contains ``config.json``, so a
    half-finished download is not mistaken for a usable model.
    """
    from ..core.paths import get_paths

    if os.path.isabs(model_name):
        candidate = Path(model_name)
        return candidate if (candidate / "config.json").is_file() else None

    roots = [get_paths().models_dir]
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        roots.append(Path(hf_home) / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    bare = model_name.split("/")[-1]
    org_name = model_name if "/" in model_name else f"sentence-transformers/{model_name}"
    flat = org_name.replace("/", "_")
    hub_style = "models--" + org_name.replace("/", "--")

    for root in roots:
        if not root.is_dir():
            continue
        for probe in (root / bare, root / model_name, root / flat):
            if (probe / "config.json").is_file():
                return probe
        snapshots = root / hub_style / "snapshots"
        if snapshots.is_dir():
            for snapshot in sorted(snapshots.iterdir(), reverse=True):
                if (snapshot / "config.json").is_file():
                    return snapshot
    return None


def model_is_cached(model_name: str) -> bool:
    """Is this sentence-transformer already on disk?

    Checked because "package installed" and "model usable" are different things:
    the first model load triggers a download, which fails on a restricted network
    and would otherwise abort a whole analysis.
    """
    return local_model_path(model_name) is not None


# Endpoint reachability, cached per process: (endpoint, reachable, checked_at).
_endpoint_probe: tuple[str, bool, float] | None = None
_PROBE_TTL_S = 300.0


def hf_endpoint() -> str:
    settings = get_settings().analysis
    return (
        settings.hf_endpoint or os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
    ).rstrip("/")


def endpoint_reachable(*, timeout: float = 4.0, force: bool = False) -> bool:
    """Short-timeout probe of the model host.

    Necessary because ``huggingface_hub`` retries a blocked host with long
    timeouts: measured here, a single model load against an unreachable
    huggingface.co took over four minutes before failing. From the user's side
    that is indistinguishable from a hang. A 4-second probe converts it into an
    immediate, explained fallback.

    The result is cached for :data:`_PROBE_TTL_S` so repeated analyses and the
    settings panel do not re-probe on every call.
    """
    global _endpoint_probe
    endpoint = hf_endpoint()
    now = time.monotonic()
    if (
        not force
        and _endpoint_probe is not None
        and _endpoint_probe[0] == endpoint
        and now - _endpoint_probe[2] < _PROBE_TTL_S
    ):
        return _endpoint_probe[1]
    reachable = False
    try:
        import httpx

        response = httpx.head(
            f"{endpoint}/api/models/sentence-transformers/all-MiniLM-L6-v2",
            timeout=timeout, follow_redirects=True,
        )
        reachable = response.status_code < 500
    except Exception as exc:  # noqa: BLE001 - unreachable is the answer
        log.info("model endpoint %s is not reachable: %s", endpoint, exc)
    _endpoint_probe = (endpoint, reachable, now)
    return reachable


def sentence_transformers_available(*, probe: bool = True) -> bool:
    """Can this backend actually produce vectors right now?

    Three conditions: the package imports, and the configured model is either
    already cached or downloadable from a reachable endpoint. ``auto`` selection
    uses this, so a blocked network makes ``auto`` pick TF-IDF up front instead
    of stalling and then failing mid-analysis.
    """
    if not sentence_transformers_package_available():
        return False
    settings = get_settings().analysis
    if model_is_cached(settings.sentence_transformer_model):
        return True
    if settings.offline_models:
        return False
    return endpoint_reachable() if probe else True


def _effective_hub_endpoint() -> str:
    """The endpoint the libraries will actually use, or ``""`` if none is loaded.

    Read back from the module constants rather than from the environment, because
    the frozen constant is what governs the request. If the two libraries
    disagree, the *worst* one is returned so the caller refuses rather than
    half-succeeding.
    """
    # Only the constants that actually govern a request. Notably NOT
    # `transformers.utils.hub._default_endpoint`: that is the hard-coded fallback
    # and stays at huggingface.co even when HF_ENDPOINT is being honoured, so
    # reading it produces a false "wrong host" verdict that blocks a working
    # mirror configuration.
    effective_attrs = ("ENDPOINT", "HUGGINGFACE_CO_RESOLVE_ENDPOINT")
    found: list[str] = []
    for module_name, attributes in _ENDPOINT_ATTRS:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attribute in attributes:
            if attribute not in effective_attrs:
                continue
            value = getattr(module, attribute, "")
            if value:
                found.append(str(value).rstrip("/"))
    if not found:
        return ""
    # A disagreement means some code path still points elsewhere. Report the
    # default in that case, so the caller refuses rather than half-succeeding.
    default = "https://huggingface.co"
    if len(set(found)) > 1 and default in found:
        return default
    return found[0]


class ModelUnavailable(RuntimeError):
    """The sentence-transformer could not be loaded (download blocked, corrupt
    cache, incompatible torch). Signals the caller to degrade, not to abort."""


def load_sentence_transformer(model_name: str) -> Any:
    """Load and cache the model process-wide.

    Loading costs seconds and ~400 MB of RAM, so it happens once behind a lock
    (two concurrent analyses would otherwise both load it). Any failure is
    wrapped in :class:`ModelUnavailable` with an actionable message.
    """
    global _st_model, _st_model_name
    with _st_lock:
        if _st_model is not None and _st_model_name == model_name:
            return _st_model
        environment = _apply_hf_environment()
        from ..core.paths import get_paths

        cache_folder = str(get_paths().models_dir)
        endpoint = environment["endpoint"]
        cached = model_is_cached(model_name)

        # Fail fast on an unreachable host rather than letting the hub exhaust its
        # retry ladder, which takes minutes. Skipped when the model is already
        # local, since no network is needed then.
        if not cached and not endpoint_reachable():
            raise ModelUnavailable(
                f"the embedding model '{model_name}' is not in the local cache and "
                f"{endpoint} is not reachable. Set a mirror in Settings > Analysis "
                f"(for example https://hf-mirror.com), or use the TF-IDF backend."
            )
        # Verify the configured endpoint is the one the hub will actually use. If
        # huggingface_hub was imported before the mirror was applied and could not
        # be patched, a download would silently go to the blocked default host.
        if not cached:
            effective = _effective_hub_endpoint()
            if effective and effective.rstrip("/") != endpoint:
                raise ModelUnavailable(
                    f"the configured model endpoint is {endpoint} but "
                    f"huggingface_hub is using {effective}, so the download would "
                    f"go to the wrong host. Restart the backend after changing the "
                    f"mirror, or use the TF-IDF backend."
                )
        # A local directory is loaded by path, which bypasses the hub entirely.
        # That matters: it is the only reliable route on a network where the
        # model host is blocked, and it makes a manual download a supported
        # workflow rather than a lucky accident.
        local = local_model_path(model_name)
        load_target = str(local) if local is not None else model_name
        log.info(
            "loading sentence-transformer '%s' from %s (endpoint: %s)",
            model_name,
            f"local directory {local}" if local else f"hub cache {cache_folder}",
            endpoint,
        )
        try:
            from sentence_transformers import SentenceTransformer

            _st_model = SentenceTransformer(load_target, cache_folder=cache_folder)
        except Exception as exc:  # noqa: BLE001 - many unrelated failure modes
            hint = (
                f"Could not load the embedding model '{model_name}'. "
                f"Downloads go to {endpoint}."
            )
            text = str(exc).lower()
            if "couldn't connect" in text or "connection" in text or "offline" in text:
                hint += (
                    " That host appears unreachable. Set a mirror in Settings > "
                    "Analysis (for example https://hf-mirror.com), or switch the "
                    "embedding backend to TF-IDF."
                )
            raise ModelUnavailable(f"{hint} Original error: {exc}") from exc
        _st_model_name = model_name
        return _st_model


def _embed_sentence_transformers(
    texts: list[str], model_name: str
) -> tuple[np.ndarray, int]:
    model = load_sentence_transformer(model_name)
    vectors = model.encode(
        texts,
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=False,  # normalised centrally by _normalise
    )
    array = np.asarray(vectors, dtype=np.float32)
    return _normalise(array), int(array.shape[1])


# ------------------------------------------------------------------ TF-IDF/LSA


def _embed_tfidf(texts: list[str], dim: int = 128) -> tuple[np.ndarray, int]:
    """TF-IDF followed by truncated SVD (latent semantic analysis).

    ``dim`` is capped by the data: SVD cannot produce more components than
    ``min(n_samples, n_features) - 1``. For a 20-paper corpus that means a much
    smaller space, which is correct - there is no more structure to extract.
    """
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        # Bigrams capture the compound terms that dominate paper titles
        # ("graph neural", "property prediction").
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.85,
        max_features=60000,
        sublinear_tf=True,
        strip_accents="unicode",
    )
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        # Every document was stopwords/empty - fall back to raw counts.
        vectorizer = TfidfVectorizer(lowercase=True, min_df=1, analyzer="char_wb",
                                     ngram_range=(3, 4), max_features=20000)
        matrix = vectorizer.fit_transform(texts)

    max_components = max(2, min(matrix.shape[0] - 1, matrix.shape[1] - 1))
    components = max(2, min(dim, max_components))
    if matrix.shape[0] <= 2 or components >= min(matrix.shape):
        # Too small for SVD; use the sparse matrix densified and truncated.
        dense = matrix.toarray().astype(np.float32)
        if dense.shape[1] > dim:
            dense = dense[:, :dim]
        return _normalise(dense), dense.shape[1]

    svd = TruncatedSVD(n_components=components, random_state=42, algorithm="randomized")
    reduced = svd.fit_transform(matrix)
    explained = float(svd.explained_variance_ratio_.sum())
    log.info(
        "TF-IDF/LSA: %s docs, %s terms -> %s dims (%.1f%% variance explained)",
        matrix.shape[0], matrix.shape[1], components, explained * 100,
    )
    return _normalise(reduced), components


# ----------------------------------------------------------------- hashing


def _embed_hashing(texts: list[str], dim: int = 256) -> tuple[np.ndarray, int]:
    """Hashed word + character-trigram counts.

    Dependency-free and deterministic. Captures lexical overlap only - no
    synonymy - so the resulting map groups papers that literally share
    vocabulary. Adequate to render *something* useful when nothing else is
    installed, and honest about being the weakest option.
    """
    vectors = np.zeros((len(texts), dim), dtype=np.float32)
    for row, text in enumerate(texts):
        lowered = (text or "").lower()
        tokens = [t for t in re.split(r"\W+", lowered) if len(t) > 2]
        for token in tokens:
            index = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16) % dim
            # Sublinear weighting mirrors TF-IDF's sublinear_tf.
            vectors[row, index] += 1.0
        for token in tokens:
            for i in range(len(token) - 2):
                trigram = token[i: i + 3]
                index = int(
                    hashlib.md5(trigram.encode("utf-8")).hexdigest()[:8], 16
                ) % dim
                vectors[row, index] += 0.3
    vectors = np.log1p(vectors)
    return _normalise(vectors), dim


# --------------------------------------------------------------------- LLM


async def _embed_llm(texts: list[str], model_spec: str) -> tuple[np.ndarray, int]:
    from ..llm.client import embed_texts

    vectors = await embed_texts(texts, model=model_spec)
    array = np.asarray(vectors, dtype=np.float32)
    return _normalise(array), int(array.shape[1])


# ------------------------------------------------------------------ dispatch


def sentence_transformers_blocker() -> str:
    """Why the local-model backend cannot be used, or ``""`` if it can.

    Distinguishing the reasons matters: "not installed" and "the model host is
    blocked" need completely different actions from the user, and reporting the
    wrong one sends them to reinstall a package that is already there.
    """
    if not sentence_transformers_package_available():
        return (
            "the sentence-transformers package is not installed. Install with: "
            'pip install "papercreator[analysis]"'
        )
    settings = get_settings().analysis
    model_name = settings.sentence_transformer_model
    if model_is_cached(model_name):
        return ""
    if settings.offline_models:
        return (
            f"offline model mode is on and '{model_name}' is not in the local "
            f"cache. Download it once with offline mode off, or choose another "
            f"embedding backend."
        )
    if not endpoint_reachable():
        return (
            f"'{model_name}' is not cached locally and the model host "
            f"{hf_endpoint()} is unreachable. Set a mirror in Settings > Analysis "
            f"- https://hf-mirror.com serves the same files when huggingface.co "
            f"is blocked."
        )
    return ""


def resolve_backend(requested: str = "auto") -> tuple[str, str, list[str]]:
    """Pick a backend. Returns ``(backend, model_key, warnings)``.

    ``model_key`` is the embedding-cache namespace, so switching model never
    mixes incompatible vectors.
    """
    settings = get_settings()
    choice = (requested or settings.analysis.embedding_backend or "auto").lower()
    warnings: list[str] = []

    if choice in ("sentence-transformers", "st"):
        blocker = sentence_transformers_blocker()
        if not blocker:
            name = settings.analysis.sentence_transformer_model
            return "sentence-transformers", f"st:{name}", warnings
        warnings.append(f"{blocker} Falling back to TF-IDF embeddings.")
        choice = "auto"

    if choice == "llm":
        from ..llm import registry as llm_registry

        model_spec = settings.llm.default_embedding
        if model_spec and llm_registry.has_any_provider():
            return "llm", f"llm:{model_spec}", warnings
        warnings.append(
            "no LLM embedding model configured (Settings > Models > embedding); "
            "falling back to TF-IDF"
        )
        choice = "auto"

    if choice == "tfidf":
        return "tfidf", "tfidf:svd128", warnings
    if choice == "hashing":
        return "hashing", "hashing:256", warnings

    # auto
    blocker = sentence_transformers_blocker()
    if not blocker:
        name = settings.analysis.sentence_transformer_model
        return "sentence-transformers", f"st:{name}", warnings
    try:
        import sklearn  # noqa: F401
    except ImportError:
        warnings.append(
            "neither sentence-transformers nor scikit-learn is available; using "
            "the hashing backend, which captures word overlap but not meaning"
        )
        return "hashing", "hashing:256", warnings
    warnings.append(
        f"using TF-IDF/LSA embeddings (lexical, fitted per corpus) because "
        f"semantic embeddings are unavailable: {blocker}"
    )
    return "tfidf", "tfidf:svd128", warnings


def embed_papers(
    papers: list[Paper],
    *,
    backend: str = "auto",
    use_cache: bool = True,
    progress: Any = None,
) -> EmbeddingResult:
    """Embed papers, using and filling the persistent vector cache.

    Corpus-relative TF-IDF deliberately skips the cache: its vectors depend on
    the whole input set. Fixed hashing, sentence-transformers and LLM embeddings
    are portable and cached per paper/text hash.
    """
    if not papers:
        return EmbeddingResult(
            vectors=np.zeros((0, 0), dtype=np.float32), model="", backend="none",
            dim=0, corpus_relative=False,
        )

    backend_name, model_key, warnings = resolve_backend(backend)
    texts = [paper.embedding_text() for paper in papers]

    # A portable backend can still fail at first use (model download blocked,
    # embedding endpoint down). Degrading here rather than propagating means the
    # user gets a usable landscape plus an explanation, instead of an error.
    if backend_name in ("sentence-transformers", "llm", "hashing"):
        try:
            return _embed_portable(
                papers, texts, backend_name, model_key, warnings,
                use_cache=use_cache, progress=progress,
            )
        except ModelUnavailable as exc:
            warnings.append(f"{exc} Falling back to TF-IDF embeddings.")
            log.warning("embedding backend %s unavailable: %s", backend_name, exc)
            backend_name, model_key = "tfidf", "tfidf:svd128"
        except Exception as exc:  # noqa: BLE001 - any backend failure degrades
            warnings.append(
                f"the {backend_name} embedding backend failed ({exc}); using "
                f"TF-IDF instead"
            )
            log.warning("embedding backend %s failed: %s", backend_name, exc)
            backend_name, model_key = "tfidf", "tfidf:svd128"

    if progress is not None:
        progress(f"computing {backend_name} embeddings for {len(papers)} papers")
    vectors, dim = _embed_tfidf(texts)
    return EmbeddingResult(
        vectors=vectors, model=model_key, backend=backend_name, dim=dim,
        corpus_relative=True, computed=len(papers), warnings=warnings,
    )


def _embed_portable(
    papers: list[Paper],
    texts: list[str],
    backend_name: str,
    model_key: str,
    warnings: list[str],
    *,
    use_cache: bool,
    progress: Any,
) -> EmbeddingResult:
    """Cached embedding for backends whose vectors are comparable across runs."""

    # Portable backend: cache per (paper, model), invalidated by text hash.
    hashes = [sha256_text(text)[:32] for text in texts]
    cached = (
        analyses_store.get_embeddings_bulk([p.id for p in papers], model_key)
        if use_cache else {}
    )
    vectors_by_index: dict[int, np.ndarray] = {}
    missing: list[int] = []
    for index, paper in enumerate(papers):
        entry = cached.get(paper.id)
        if entry is not None and entry[1] == hashes[index]:
            vectors_by_index[index] = np.frombuffer(entry[0], dtype=np.float32)
        else:
            missing.append(index)

    dim = 0
    if vectors_by_index:
        dim = len(next(iter(vectors_by_index.values())))

    if missing:
        if progress is not None:
            progress(
                f"embedding {len(missing)} papers with {backend_name} "
                f"({len(vectors_by_index)} from cache)"
            )
        missing_texts = [texts[i] for i in missing]
        if backend_name == "sentence-transformers":
            new_vectors, new_dim = _embed_sentence_transformers(
                missing_texts, get_settings().analysis.sentence_transformer_model
            )
        elif backend_name == "hashing":
            new_vectors, new_dim = _embed_hashing(missing_texts)
        else:  # llm
            import asyncio

            new_vectors, new_dim = asyncio.run(
                _embed_llm(missing_texts, get_settings().llm.default_embedding)
            )
        # A cache written by an older model version can have a different width;
        # trust the freshly computed dimension and drop stale entries.
        if dim and new_dim != dim:
            warnings.append(
                f"cached vectors have dim {dim} but the model now returns "
                f"{new_dim}; recomputing all {len(papers)} embeddings"
            )
            vectors_by_index.clear()
            missing = list(range(len(papers)))
            if backend_name == "sentence-transformers":
                new_vectors, new_dim = _embed_sentence_transformers(
                    texts, get_settings().analysis.sentence_transformer_model
                )
            elif backend_name == "hashing":
                new_vectors, new_dim = _embed_hashing(texts)
            else:
                import asyncio

                new_vectors, new_dim = asyncio.run(
                    _embed_llm(texts, get_settings().llm.default_embedding)
                )
        dim = new_dim
        to_store: list[tuple[str, str, bytes, int, str]] = []
        for offset, index in enumerate(missing):
            vector = new_vectors[offset]
            vectors_by_index[index] = vector
            to_store.append(
                (papers[index].id, model_key, vector.tobytes(), dim, hashes[index])
            )
        if use_cache and to_store:
            # A cache write must never fail the embedding itself. The vectors are
            # already computed and correct; losing the cache only costs time on
            # the next run, whereas propagating the error would make the caller
            # degrade to a weaker backend and silently change analysis quality.
            try:
                analyses_store.put_embeddings_bulk(to_store)
            except Exception as exc:  # noqa: BLE001 - caching is best-effort
                log.warning(
                    "could not cache %s embedding(s): %s", len(to_store), exc
                )
                warnings.append(
                    f"embeddings were computed but could not be cached ({exc}); "
                    f"the next analysis will recompute them"
                )

    matrix = np.zeros((len(papers), dim), dtype=np.float32)
    for index in range(len(papers)):
        vector = vectors_by_index.get(index)
        if vector is not None and len(vector) == dim:
            matrix[index] = vector
    return EmbeddingResult(
        vectors=_normalise(matrix), model=model_key, backend=backend_name, dim=dim,
        corpus_relative=False, cache_hits=len(papers) - len(missing),
        computed=len(missing), warnings=warnings,
    )


def embed_query(text: str, *, backend: str = "auto") -> tuple[np.ndarray, str, str]:
    """Embed a single free-text query for similarity search.

    TF-IDF cannot embed a lone string in the fitted corpus space. Hashing is
    portable because its buckets and weighting are fixed, so it is valid for
    similarity queries and incremental placement.
    """
    backend_name, model_key, _ = resolve_backend(backend)
    if backend_name == "sentence-transformers":
        vectors, _ = _embed_sentence_transformers(
            [text], get_settings().analysis.sentence_transformer_model
        )
        return vectors[0], model_key, backend_name
    if backend_name == "llm":
        import asyncio

        vectors, _ = asyncio.run(
            _embed_llm([text], get_settings().llm.default_embedding)
        )
        return vectors[0], model_key, backend_name
    vectors, _ = _embed_hashing([text])
    return vectors[0], "hashing:256", "hashing"


def cosine_similarity_matrix(vectors: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity. Assumes L2-normalised rows."""
    if vectors.size == 0:
        return np.zeros((0, 0), dtype=np.float32)
    return np.clip(vectors @ vectors.T, -1.0, 1.0)


def nearest_neighbours(
    query: np.ndarray, vectors: np.ndarray, k: int = 10
) -> list[tuple[int, float]]:
    """Top-``k`` (index, cosine) pairs for a query vector."""
    if vectors.size == 0:
        return []
    query_vector = np.asarray(query, dtype=np.float32).ravel()
    norm = np.linalg.norm(query_vector) or 1.0
    similarities = vectors @ (query_vector / norm)
    count = min(k, len(similarities))
    # argpartition is O(n) versus a full O(n log n) sort; k is small and n can be
    # tens of thousands.
    top = np.argpartition(-similarities, count - 1)[:count]
    ordered = top[np.argsort(-similarities[top])]
    return [(int(i), float(similarities[i])) for i in ordered]


def model_environment() -> dict[str, Any]:
    """Apply and report the Hugging Face endpoint configuration.

    Exposed so the diagnostics and the settings panel can show which endpoint is
    actually in force, including whether an already-imported ``huggingface_hub``
    had to be patched.
    """
    return _apply_hf_environment()


def describe_backends() -> list[dict[str, Any]]:
    """Backend availability for the analysis settings panel."""
    settings = get_settings()
    from ..llm import registry as llm_registry

    try:
        import sklearn  # noqa: F401

        sklearn_ok = True
    except ImportError:
        sklearn_ok = False
    model_name = settings.analysis.sentence_transformer_model
    package_ok = sentence_transformers_package_available()
    cached = model_is_cached(model_name) if package_ok else False
    endpoint = hf_endpoint()
    reachable = None
    blocker = ""
    if package_ok and not cached:
        if settings.analysis.offline_models:
            blocker = (
                "offline mode is on and the model is not cached; download it once "
                "or switch backend"
            )
        else:
            reachable = endpoint_reachable()
            if not reachable:
                blocker = (
                    f"{endpoint} is unreachable, so the model cannot be "
                    f"downloaded. Set a mirror in Settings > Analysis "
                    f"(https://hf-mirror.com works when huggingface.co is blocked)."
                )
    elif not package_ok:
        blocker = "the sentence-transformers package is not installed"

    return [
        {
            "id": "sentence-transformers",
            "name": "Sentence-Transformers (local)",
            "available": sentence_transformers_available(),
            "quality": "high",
            "portable": True,
            "model": model_name,
            "model_cached": cached,
            "endpoint": endpoint,
            "endpoint_reachable": reachable,
            "blocker": blocker,
            "requirement": 'pip install "papercreator[analysis]"',
            "note": "true semantic embeddings; downloads ~90MB once, then runs "
                    "fully offline",
        },
        {
            "id": "llm",
            "name": "LLM embedding API",
            "available": bool(settings.llm.default_embedding)
                         and llm_registry.has_any_provider(),
            "quality": "high",
            "portable": True,
            "model": settings.llm.default_embedding,
            "requirement": "configure an embedding model in Settings > Models",
            "note": "no local model needed; costs money and needs network",
        },
        {
            "id": "tfidf",
            "name": "TF-IDF + LSA",
            "available": sklearn_ok,
            "quality": "medium",
            "portable": False,
            "model": "tfidf:svd128",
            "requirement": "included in the base install",
            "note": "lexical, fitted per corpus; good enough for one-topic maps",
        },
        {
            "id": "hashing",
            "name": "Hashed n-grams",
            "available": True,
            "quality": "low",
            "portable": True,
            "model": "hashing:256",
            "requirement": "none",
            "note": "always works and supports incremental placement; captures "
                    "word overlap, not meaning",
        },
    ]
