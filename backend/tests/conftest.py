"""Shared test fixtures.

Every test runs against a temporary ``PAPERCREATOR_HOME``, so no test can touch a
real database, workspace or settings file. The isolation is per-module rather than
per-test because creating a project involves real directory scaffolding and a
migration run, and paying that per test would make the suite slow enough that it
stops being run.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: hits real scholarly APIs; needs network. Excluded unless --live.",
    )


@pytest.fixture(scope="session")
def temp_home() -> Iterator[Path]:
    """An isolated PAPERCREATOR_HOME for the whole session."""
    home = Path(tempfile.mkdtemp(prefix="pc_pytest_"))
    previous = os.environ.get("PAPERCREATOR_HOME")
    os.environ["PAPERCREATOR_HOME"] = str(home)
    # Keep tests off the model host: an unreachable one costs a probe timeout per
    # call and makes failures look like network problems.
    os.environ.setdefault("PC_OFFLINE_MODELS", "1")

    from papercreator.core import config, db, logging_setup, paths

    paths.reset_paths()
    config.reload_settings()
    logging_setup.setup_logging("WARNING", home / "logs")
    paths.get_paths().ensure()
    db.set_db_path(None)
    db.init_db()

    yield home

    db.close_connection()
    if previous is None:
        os.environ.pop("PAPERCREATOR_HOME", None)
    else:
        os.environ["PAPERCREATOR_HOME"] = previous
    paths.reset_paths()
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture()
def project(temp_home: Path):
    """A fresh project, removed afterwards so tests do not see each other's."""
    from papercreator.store import projects as projects_store

    created = projects_store.create(
        title="Test Paper Project",
        idea="Using multi-agent LLM systems to draft survey papers automatically.",
        research_field="machine learning",
        language="en",
        bilingual=True,
        git_enabled=False,
    )
    yield created
    projects_store.delete(created.id, remove_files=True)


@pytest.fixture()
def sample_papers():
    """Ten synthetic papers across three clear topics.

    Synthetic rather than fetched so the analysis tests are deterministic and do
    not depend on the network; the topics are deliberately separable so a
    clustering failure is a real failure and not noise.
    """
    from papercreator.core.models import Author, Paper

    specs = [
        ("Graph neural networks for molecular property prediction",
         "We predict molecular properties using message passing neural networks "
         "over molecular graphs, evaluated on QM9 and MoleculeNet.", 2021, 120),
        ("Attentive graph networks for chemistry",
         "Graph attention layers improve molecular property prediction accuracy "
         "on chemical benchmarks.", 2022, 45),
        ("Molecular representation learning with contrastive objectives",
         "Contrastive pretraining of molecular graph encoders improves downstream "
         "property prediction.", 2023, 30),
        ("Transformers for natural language understanding",
         "Self-attention architectures achieve strong results on language "
         "understanding benchmarks.", 2019, 900),
        ("Pretrained language models for text classification",
         "Masked language model pretraining transfers to text classification "
         "tasks with limited labelled data.", 2020, 400),
        ("Instruction tuning of large language models",
         "Fine-tuning language models on instructions improves zero-shot task "
         "generalisation.", 2023, 210),
        ("Convolutional networks for image recognition",
         "Deep convolutional architectures classify natural images with residual "
         "connections on ImageNet.", 2016, 2000),
        ("Vision transformers for image classification",
         "Patch-based transformer encoders match convolutional networks on image "
         "recognition benchmarks.", 2021, 800),
        ("Self-supervised visual representation learning",
         "Contrastive learning of visual features without labels transfers to "
         "image classification.", 2020, 600),
        ("Data augmentation for image models",
         "Automated augmentation policies improve image classification accuracy "
         "and robustness.", 2019, 300),
    ]
    papers = []
    for index, (title, abstract, year, citations) in enumerate(specs):
        papers.append(
            Paper(
                title=title,
                abstract=abstract,
                authors=[Author(name=f"Author {chr(65 + index)}")],
                year=year,
                venue="Test Venue",
                doi=f"10.9999/test.{index}",
                citation_count=citations,
                source_providers=["test"],
            ).ensure_id()
        )
    return papers


@pytest.fixture()
def stored_papers(temp_home: Path, sample_papers):
    """``sample_papers`` persisted into the library."""
    from papercreator.store import papers as papers_store

    stored, _, _ = papers_store.upsert_many(sample_papers)
    yield stored
    papers_store.delete_many([paper.id for paper in stored])
