"""Retrieval provider implementations.

Each module here holds exactly one :class:`~papercreator.retrieval.base.Provider`
subclass. Adding a source:

1. write ``providers/<name>.py`` with a ``Provider`` subclass;
2. add its class to :data:`PROVIDER_CLASSES` below;
3. if it needs a key, add the field to
   :class:`~papercreator.core.config.ProviderCredentials` and set
   ``ProviderMeta.key_setting`` to that field name.

Nothing else changes - the registry, pipeline, UI provider picker and settings
screen are all driven from this list and from each provider's declared metadata.

Currently registered (all free; ``freemium`` means a key only raises limits):

===============  =======  ====================================================
id               tier     strength
===============  =======  ====================================================
arxiv            free     full abstracts, CS/physics/maths preprints
openalex         freemium broadest coverage, citations + reference lists
crossref         free     authoritative publication metadata for BibTeX
pubmed           freemium biomedical, curated MeSH terms
europepmc        free     life sciences in one request, no key
semanticscholar  freemium true semantic search from an idea or paper
dblp             free     precise CS conference/journal names
doaj             free     guaranteed open-access full text, all disciplines
local            free     your own .bib/.ris/.csv exports
===============  =======  ====================================================
"""

from .arxiv import ArxivProvider
from .crossref import CrossrefProvider
from .dblp import DblpProvider
from .doaj import DoajProvider
from .europepmc import EuropePmcProvider
from .local_files import LocalFilesProvider
from .openalex import OpenAlexProvider
from .pubmed import PubMedProvider
from .semantic_scholar import SemanticScholarProvider

PROVIDER_CLASSES = [
    ArxivProvider,
    OpenAlexProvider,
    CrossrefProvider,
    SemanticScholarProvider,
    EuropePmcProvider,
    PubMedProvider,
    DblpProvider,
    DoajProvider,
    LocalFilesProvider,
]

__all__ = [
    "PROVIDER_CLASSES",
    "ArxivProvider",
    "CrossrefProvider",
    "DblpProvider",
    "DoajProvider",
    "EuropePmcProvider",
    "LocalFilesProvider",
    "OpenAlexProvider",
    "PubMedProvider",
    "SemanticScholarProvider",
]
