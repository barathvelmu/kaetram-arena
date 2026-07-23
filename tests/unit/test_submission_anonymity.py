"""Fail-closed checks for the ACL review manuscript."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.audit_submission_anonymity import (
    ALLOWED_GITHUB_URLS,
    audit_pdf_metadata,
    audit_submission,
    audit_text,
)


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


def test_review_sources_pass_generic_anonymity_audit() -> None:
    assert audit_submission(
        source_paths=[MANUSCRIPT],
        bibliography_paths=[BIBLIOGRAPHY],
    ) == []


def test_generic_audit_rejects_searchable_project_history() -> None:
    examples = (
        "Resolved in PR #42.",
        "Run private-evaluation.sh after the build.",
        "The service listened on 9123.",
        "Artifact path: /home/researcher/private/run.json",
        "Contact researcher@example.org.",
        "Status updates: @research_account",
        "See https://github.com/example/private-fork.",
        f"See {next(iter(ALLOWED_GITHUB_URLS))}/issues/42.",
        "Built from commit abcdef1234567.",
        "The branch example/private-review was used.",
    )
    for example in examples:
        assert audit_text("fixture", example), example


def test_generic_audit_allows_public_project_and_model_references() -> None:
    text = (
        f"{next(iter(ALLOWED_GITHUB_URLS))}\n"
        "https://huggingface.co/collections/Qwen/qwen35"
    )
    assert audit_text("fixture", text) == []


def test_generic_audit_rejects_identifying_pdf_metadata() -> None:
    clean = (
        "Title:\nSubject:\nKeywords:\nAuthor:\n"
        "Creator: LaTeX with hyperref\nProducer: pdfTeX-1.40.24\n"
    )
    identifying = (
        "Title:\nSubject:\nKeywords:\nAuthor: Example Researcher\n"
        "Creator: LaTeX with hyperref\nProducer: pdfTeX-1.40.24\n"
    )
    unexpected_creator = (
        "Title:\nSubject:\nKeywords:\nAuthor:\n"
        "Creator: Example Researcher\nProducer: Custom PDF tool\n"
    )
    incomplete = "Creator: LaTeX\n"

    assert audit_pdf_metadata("fixture", clean) == []
    assert audit_pdf_metadata("fixture", identifying)
    assert audit_pdf_metadata("fixture", unexpected_creator)
    assert audit_pdf_metadata("fixture", incomplete)


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
