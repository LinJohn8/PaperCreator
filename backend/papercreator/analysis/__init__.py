"""Analysis subsystem: the 3D research landscape.

Turns a set of retrieved papers into a map the user can read, navigate, and
position their own idea inside.

Public surface::

    from papercreator.analysis import pipeline, incremental, graph

    pipeline.build_analysis(papers)          # full landscape
    pipeline.submit_analysis(project_id=...) # as a background job
    incremental.place_idea(analysis_id, ...) # where does my idea sit?
    incremental.remove_from_analysis(...)    # take it back out
    graph.analyse_graph(papers)              # citation / co-authorship view

Layout:

* :mod:`embeddings` - tiered embedding backends + persistent vector cache
* :mod:`reduce` - UMAP / PCA / t-SNE / MDS projection to 3D
* :mod:`cluster` - HDBSCAN / KMeans / agglomerative topic clustering
* :mod:`keywords` - c-TF-IDF cluster labels, corpus stats, trends
* :mod:`heatmap` - Gaussian KDE density grids, keyword layers, z slices
* :mod:`gaps` - five complementary gap detectors
* :mod:`graph` - citation, co-citation, coupling, co-authorship
* :mod:`incremental` - place/remove single papers without moving the map
* :mod:`pipeline` - orchestration and persistence

Every stage works on a plain ``pip install`` and improves when the optional
``analysis`` extra (umap-learn, hdbscan, sentence-transformers) is present.
See ``docs/systems/analysis_system.md``.
"""

from . import (  # noqa: F401
    cluster,
    embeddings,
    gaps,
    graph,
    heatmap,
    incremental,
    keywords,
    pipeline,
    reduce,
)

__all__ = [
    "cluster",
    "embeddings",
    "gaps",
    "graph",
    "heatmap",
    "incremental",
    "keywords",
    "pipeline",
    "reduce",
]
