#!/usr/bin/env python3
"""Build a deterministic anonymous TMLR review supplement."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "research" / "artifacts" / "local-trigger-incidence-v2"
RESULTS = ROOT / "research" / "results" / "local-trigger-incidence-v2"
PAPER = ROOT / "output" / "pdf" / "kaetram-tool-routing-tmlr-draft.pdf"
OUTPUT = ROOT / "output" / "supplement" / "kaetram-tmlr-anonymous-supplement.zip"
TRUST_ROOT = "04a26f53ce24fa9578c0e49d55b946321347f9de2a1dd81e0739822d57978562"
FORBIDDEN = (
    b"/Users/",
    b"/home/",
    b"github.com/barath",
    b"github.com/patnir",
    b"huggingface.co/patnir",
    b"modal.run",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def main() -> int:
    if not ARTIFACT.is_dir() or not PAPER.is_file():
        raise SystemExit("build the sealed artifact and TMLR PDF first")
    if sha256_file(ARTIFACT / "artifact-index.json") != TRUST_ROOT:
        raise SystemExit("sealed artifact differs from the review trust root")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kaetram-tmlr-supplement-") as temporary:
        stage = Path(temporary) / "kaetram-tmlr-anonymous-supplement"
        shutil.copytree(ARTIFACT, stage / "artifact")
        copy_file(PAPER, stage / "paper.pdf")
        for name in (
            "README.md",
            "artifact-trust-root.json",
            "paper-table-public.md",
            "structured-call-validity-posthoc.json",
        ):
            copy_file(RESULTS / name, stage / "results" / name)
        for name in (
            "tool_surface.py",
            "scripts/opd/audit_trigger_incidence_artifact.py",
            "scripts/opd/verify_trigger_incidence_review_bundle.py",
        ):
            copy_file(ROOT / name, stage / name)
        copy_file(ROOT / "LICENSE", stage / "LICENSE")
        readme = f"""# Anonymous TMLR review supplement

This package contains the review manuscript, the complete sealed V2 public
artifact, concise result projections, and a standalone primary-outcome verifier.

Run from this directory:

```bash
python3 scripts/opd/verify_trigger_incidence_review_bundle.py \\
  --artifact-dir artifact \\
  --expected-index-sha256 {TRUST_ROOT}
```

The command verifies every artifact byte against the expected digest recorded
in the review manuscript, rejects duplicate or non-finite JSON, checks the
complete registered request schedule, reclassifies the primary
parser-recoverable outcome from raw response messages, and recomputes all
registered contrasts, the
directional verdict, and seed heterogeneity.

Double-blind boundary: this standalone package intentionally excludes private
source history and the full identity-bearing model lock. The public artifact
contains their hashes and sanitized projections. Full Git-blob and model-lock
authentication is available from the repository verifier after deanonymization;
the anonymous verifier reports that tier as deferred rather than silently
pretending it has authenticated hidden history.
"""
        (stage / "README.md").write_text(readme)
        records = []
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            payload = path.read_bytes()
            lowered = payload.lower()
            for forbidden in FORBIDDEN:
                if forbidden.lower() in lowered:
                    raise SystemExit(f"identity or endpoint fragment in supplement: {path}")
            records.append(
                {
                    "path": path.relative_to(stage).as_posix(),
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        manifest = {
            "schema_version": "kaetram.tmlr-anonymous-supplement.v1",
            "artifact_index_sha256": TRUST_ROOT,
            "files": records,
        }
        manifest["tree_sha256"] = hashlib.sha256(
            json.dumps(
                records,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        (stage / "package-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(item for item in stage.rglob("*") if item.is_file()):
                relative = Path(stage.name) / path.relative_to(stage)
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    print(f"built {OUTPUT} ({OUTPUT.stat().st_size} bytes, sha256={sha256_file(OUTPUT)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
