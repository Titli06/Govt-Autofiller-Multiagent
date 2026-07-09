#!/usr/bin/env python
"""Offline ground-truth accuracy eval (Phase 6, PRD §9, SPEC-PHASE6.md §6.7).

Runs the REAL fill pipeline (real Gemini `classify_form`/`verify_value_on_document`,
real Document AI `detect_fields`/`map_field_labels`) against a small committed,
synthetic fixture set and compares auto-filled values to hand-labeled ground truth.
This is the true accuracy PRD §9 asks for ("accuracy vs. ground truth on a test set"),
distinct from the live approved-as-is-vs-corrected proxy exposed by GET /api/metrics.

Makes real, BILLED external API calls (GEMINI_API_KEY / Document AI credentials must
be configured, same as this project's live-stack verification). Never imported by the
app; never run in CI/pytest — run it manually:

    cd backend
    python scripts/eval_accuracy.py [path/to/manifest.json]

Fixtures live in tests/fixtures/eval/ (manifest.json + form images) — synthetic data
only, no real PII, mirroring the synthetic Aadhaar/PAN fixtures used in this project's
live-stack verification sessions (see PLAN.md's Phase 1-4 entries).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.graph import build_graph  # noqa: E402
from app.agent.tools.profile_lookup_tool import CandidateView, ProfileSnapshot  # noqa: E402
from app.services.form_placement.document_ai import detect_fields  # noqa: E402
from app.services.ocr.vision_llm import (  # noqa: E402
    classify_form,
    map_field_labels,
    verify_value_on_document,
)
from app.services.preprocessing import preprocess  # noqa: E402

_DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eval" / "manifest.json"

_graph = build_graph()


def _build_snapshot(profile: dict) -> ProfileSnapshot:
    """Builds a fake ProfileSnapshot from the manifest's 'profile' section. Each
    candidate's source_snippet lets document_verification's deterministic re-ground
    pass without an LLM call in the common case (see document_verification_tool.
    deterministic_match) — the verifier callable is only reached on a miss."""
    from datetime import datetime, timezone

    snapshot: ProfileSnapshot = {}
    for field_name, cand in profile.items():
        snapshot[field_name] = [
            CandidateView(
                profile_field_id=f"eval-{field_name}",
                source_doc_id=cand.get("source_doc_id"),
                doc_type="aadhaar",
                value=cand["value"],
                confidence=cand["confidence"],
                status=cand["status"],
                created_at=datetime.now(timezone.utc),
                source_snippet=cand.get("source_snippet"),
            )
        ]
    return snapshot


def _make_verifier(source_docs: dict[str, str], base_dir: Path):
    cache: dict[str, list[bytes]] = {}

    def verifier(value: str, source_doc_id: str | None) -> bool:
        if source_doc_id is None or source_doc_id not in source_docs:
            return False
        images = cache.get(source_doc_id)
        if images is None:
            raw = (base_dir / source_docs[source_doc_id]).read_bytes()
            images, _ = preprocess(raw, "image/png")
            cache[source_doc_id] = images
        return verify_value_on_document(images, value)

    return verifier


def _run_fixture(fixture: dict, snapshot: ProfileSnapshot, verifier, base_dir: Path) -> dict:
    raw = (base_dir / fixture["image"]).read_bytes()
    images, _ = preprocess(raw, "image/png")

    result = _graph.invoke(
        {
            "user_id": "eval",
            "form_id": "eval",
            "declared_form_type": fixture["form_type"],
            "detected_form_type": None,
            "type_mismatch": False,
            "form_type": None,
            "schema_source": "template",
            "field_specs": [],
            "fields": [],
        },
        config={
            "configurable": {
                "snapshot": snapshot,
                "images": images,
                "classifier": classify_form,
                "verifier": verifier,
                "field_detector": detect_fields,
                "label_mapper": map_field_labels,
            }
        },
    )

    expected: dict[str, str] = fixture["expected"]
    per_field = []
    autofilled = 0
    autofilled_correct = 0
    for f in result["fields"]:
        if f["field_name"] not in expected:
            continue
        is_autofilled = not f["needs_review"]
        is_correct = is_autofilled and f["value"] == expected[f["field_name"]]
        if is_autofilled:
            autofilled += 1
            if is_correct:
                autofilled_correct += 1
        per_field.append(
            {
                "field_name": f["field_name"],
                "expected": expected[f["field_name"]],
                "actual": f["value"],
                "autofilled": is_autofilled,
                "correct": is_correct,
                "confidence": f["confidence"],
            }
        )

    return {
        "fixture": fixture["name"],
        "type_mismatch": result["type_mismatch"],
        "expected_total": len(expected),
        "autofilled": autofilled,
        "autofilled_correct": autofilled_correct,
        "precision": (autofilled_correct / autofilled) if autofilled else None,
        "recall": (autofilled_correct / len(expected)) if expected else None,
        "fields": per_field,
    }


def main(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    base_dir = manifest_path.parent
    snapshot = _build_snapshot(manifest["profile"])
    verifier = _make_verifier(manifest.get("source_documents", {}), base_dir)

    results = [_run_fixture(fx, snapshot, verifier, base_dir) for fx in manifest["fixtures"]]

    total_autofilled = sum(r["autofilled"] for r in results)
    total_correct = sum(r["autofilled_correct"] for r in results)
    total_expected = sum(r["expected_total"] for r in results)

    correct_confidences = [f["confidence"] for r in results for f in r["fields"] if f["correct"]]
    incorrect_confidences = [
        f["confidence"] for r in results for f in r["fields"] if f["autofilled"] and not f["correct"]
    ]

    summary = {
        "fixtures": results,
        "aggregate": {
            "precision": (total_correct / total_autofilled) if total_autofilled else None,
            "recall": (total_correct / total_expected) if total_expected else None,
            "avg_confidence_when_correct": (
                sum(correct_confidences) / len(correct_confidences) if correct_confidences else None
            ),
            "avg_confidence_when_incorrect": (
                sum(incorrect_confidences) / len(incorrect_confidences) if incorrect_confidences else None
            ),
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_MANIFEST
    main(path)
