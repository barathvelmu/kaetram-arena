"""Fail-closed checks for the ACL review manuscript."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANUSCRIPT = REPO_ROOT / "reference" / "naacl_submission.tex"
BIBLIOGRAPHY = REPO_ROOT / "reference" / "submission.bib"


def test_review_manuscript_is_anonymous_and_has_abstract_headroom() -> None:
    source = MANUSCRIPT.read_text()

    assert r"\usepackage[review]{acl}" in source
    assert r"\author{Anonymous submission}" in source
    abstract = re.search(
        r"\\begin\{abstract\}(.*?)\\end\{abstract\}", source, flags=re.DOTALL
    )
    assert abstract is not None
    words = re.findall(
        r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*|[0-9]+/[0-9]+",
        abstract.group(1),
    )
    assert 120 <= len(words) <= 175


def test_review_manuscript_omits_searchable_project_history() -> None:
    source = MANUSCRIPT.read_text()
    forbidden = {
        "public pull-request number": r"\bPR(?:~| )?\\?#?\d+",
        "project-specific evaluation script": r"run-eval\.sh",
        "project-specific database lane": r"kaetram_(?:eval|devlopment)",
        "historical orchestrator ports": r"\b(?:9001|9011|9021)\b",
        "author fork or username": r"(?:barath|patnir)",
        "author repository URL": r"github\.com/(?!Kaetram/Kaetram-Open)",
        "searchable month-day history": (
            r"\b(?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+\d{1,2}\b"
        ),
    }
    for label, pattern in forbidden.items():
        assert re.search(pattern, source, flags=re.IGNORECASE) is None, label


def test_causal_figure_separates_parallel_training_from_runtime_factorial() -> None:
    source = MANUSCRIPT.read_text()

    assert r"\textbf{Parallel training arms}" in source
    assert "they do not form a sequential curriculum" in source
    assert "cannot estimate this intervention" in source


def test_orak_is_cited_as_arxiv_not_unverified_conference_acceptance() -> None:
    bibliography = BIBLIOGRAPHY.read_text()

    entry = re.search(
        r"@article\{park2025orak,(.*?)(?=\n\})",
        bibliography,
        flags=re.DOTALL,
    )
    assert entry is not None
    assert "arXiv:2506.03610" in entry.group(1)
    assert "International Conference on Learning Representations" not in entry.group(1)
