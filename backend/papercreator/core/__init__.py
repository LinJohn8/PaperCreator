"""Core infrastructure: paths, config, logging, db, events, jobs, models.

Nothing in ``core`` may import from a feature package (retrieval, analysis,
llm, agents, writing, convert, skills, vcs, api). Dependencies point inward
only, which is what keeps the store and the API testable in isolation.
"""

from .errors import (  # noqa: F401
    AppError,
    ConfigurationError,
    ConflictError,
    DependencyMissingError,
    NotFoundError,
    ProviderError,
    ValidationError,
)
from .logging_setup import get_logger  # noqa: F401
from .paths import get_paths  # noqa: F401

__all__ = [
    "AppError",
    "ConfigurationError",
    "ConflictError",
    "DependencyMissingError",
    "NotFoundError",
    "ProviderError",
    "ValidationError",
    "get_logger",
    "get_paths",
]
