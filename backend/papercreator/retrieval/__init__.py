"""Retrieval subsystem: pluggable scholarly search.

Public surface used by the API and the agents::

    from papercreator.retrieval import pipeline, registry

    registry.describe_all()                  # provider catalogue for the UI
    await pipeline.search_async(request)     # run a search
    pipeline.submit_search(request)          # run it as a background job
    await pipeline.resolve_identifier(doi)   # single-record lookup

Layout:

* :mod:`base` - the ``Provider`` contract every source implements
* :mod:`http_client` - shared rate limiting, retries, response cache
* :mod:`providers` - one module per source (add new sources here)
* :mod:`registry` - instantiation and provider selection
* :mod:`query_expand` - idea/abstract -> effective queries
* :mod:`dedupe` - cross-provider duplicate merging
* :mod:`rank` - reciprocal rank fusion + quality signals
* :mod:`pipeline` - the orchestration that ties it together

See ``docs/systems/retrieval_system.md``.
"""

from . import dedupe, pipeline, query_expand, rank, registry  # noqa: F401
from .base import (  # noqa: F401
    Provider,
    ProviderCapabilities,
    ProviderMeta,
    RateLimit,
)

__all__ = [
    "Provider",
    "ProviderCapabilities",
    "ProviderMeta",
    "RateLimit",
    "dedupe",
    "pipeline",
    "query_expand",
    "rank",
    "registry",
]
