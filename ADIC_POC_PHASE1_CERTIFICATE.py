#!/usr/bin/env python3
"""ADIC PoC Phase 1 certificate, single-file edition.

This file is both the certificate and the verifier for this repository.

Usage:
  python ADIC_POC_PHASE1_CERTIFICATE.py
  python ADIC_POC_PHASE1_CERTIFICATE.py --show
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any


CERTIFICATE_ID = "ADIC-COLDCHAIN-POC-PHASE1-SINGLEFILE-001"
SYSTEM_ID = "coldchain-handoff-assurance-poc"

# ADIC target files. This is the certificate scope. Nothing else is certified.
ADIC_TARGET_FILES = [
    {
        "path": "poc_handoff_pde.py",
        "role": "judgement_logic",
        "sha256": "8477a515eddee532bfffa2bacb7b73862252c5ac9bd5d6b979b5fbe9e7ae03b9",
        "size_bytes": 40974,
    },
    {
        "path": "sample_beta_breach_reject.json",
        "role": "replay_input_reject",
        "sha256": "50bd7627e2cc38e726f419ada7bce3b02f3e0654e4fa1593fab706bceefa1e4e",
        "size_bytes": 1092,
    },
    {
        "path": "sample_chain_stop_demo.json",
        "role": "replay_input_chain_stop",
        "sha256": "fec016a13a979f820ff292349d432e6d806c2eabbb3f901d7f02c8e2485350f6",
        "size_bytes": 2992,
    },
    {
        "path": "test_poc_handoff_pde.py",
        "role": "test_evidence",
        "sha256": "cfd6ee47e1bc9c0d2bae33a47ac635fc5befd051486d0ad424bf0b6872df2813",
        "size_bytes": 40199,
    },
    {
        "path": "SECURITY_DESIGN.md",
        "role": "security_scope",
        "sha256": "d303bc6dfb9210fc27774ac93b4df7b44a3d36c3305a61d56752efd2b1a146c2",
        "size_bytes": 8409,
    },
]

CERTIFICATE = {
    "certificate_id": CERTIFICATE_ID,
    "system_id": SYSTEM_ID,
    "schema": "adic-poc-phase1-single-file.v1",
    "scope": {
        "certified_object": (
            "input JSON -> validation -> profile/ruleset digest -> verdict "
            "-> chain stop/trace -> certificate"
        ),
        "target_files": [item["path"] for item in ADIC_TARGET_FILES],
        "covers": [
            "input_validation",
            "profile_binding",
            "ruleset_binding",
            "metric_computation",
            "verdict_generation",
            "chain_stop",
            "trace_anchor",
            "digest_record",
        ],
        "does_not_cover": [
            "regulatory_certification",
            "product_specific_threshold_validation",
            "sensor_or_logger_authenticity",
            "production_onzalinks_integration",
            "sla_or_liability_framework",
        ],
    },
    "artifacts": ADIC_TARGET_FILES,
    "profile": {
        "profile_id": "lifescience_2to8c_standard_handoff",
        "profile_version": "v1",
        "profile_digest": "bed4948bf11ae4383874acdbb18da5497bfb5e70f9b37ba18699cd0bf7bfa49c",
        "ruleset_digest": "27a50d185514658cedc63b76e0c9d683ce2137083f293a493d419c6fe70fe29e",
        "comparison_rule": "observed > threshold",
    },
    "claims": [
        {
            "id": "C1",
            "name": "replay_determinism",
            "statement": "Same target files and replay inputs produce the recorded verdicts.",
        },
        {
            "id": "C2",
            "name": "evidence_gap_dominance",
            "statement": "Required evidence gaps prevent release and produce HOLD.",
        },
        {
            "id": "C3",
            "name": "fatal_breach_rejection",
            "statement": "A beta threshold breach with complete evidence produces REJECT.",
        },
        {
            "id": "C4",
            "name": "chain_stop_soundness",
            "statement": "Stop-on-fail halts at the first non-RELEASE leg.",
        },
        {
            "id": "C5",
            "name": "trace_anchor_correctness",
            "statement": "Replay records last-good and first-fail anchors.",
        },
    ],
    "non_claims": [
        "No regulatory certification claim.",
        "No product-specific medical-threshold validation claim.",
        "No sensor, logger, or upstream data authenticity claim.",
        "No production deployment readiness claim.",
        "No completed OnzaLinks integration claim.",
    ],
    "expected_replay": {
        "sample_beta_breach_reject.json": {
            "labels": ["REJECT"],
            "stopped_early": False,
            "total_legs": 1,
            "evaluated_legs": 1,
            "first_fail_seq": 1,
            "decision_digests": [
                "7f6316887758c1a358904854d215f13765c276df8f3d1852dfce6664203d053c",
            ],
        },
        "sample_chain_stop_demo.json": {
            "labels": ["RELEASE", "HOLD"],
            "stopped_early": True,
            "total_legs": 3,
            "evaluated_legs": 2,
            "skipped_legs": 1,
            "last_good_seq": 1,
            "first_fail_seq": 2,
            "decision_digests": [
                "679fb96d8f75698c42332a14a9e1c10a8eddb94c16319f158f9e33448edb1365",
                "ed89119cce5e91c0463c38b288eb5089d7fce7fd7cf24116dcd5696071cd057e",
            ],
        },
    },
}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_obj(obj: Any) -> str:
    return sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_poc(project_dir: Path):
    script = project_dir / "poc_handoff_pde.py"
    spec = importlib.util.spec_from_file_location("poc_handoff_pde_adic_single", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def decision_digest(decision: Any) -> str:
    return digest_obj(decision.canonical_payload())


def verify_artifacts(project_dir: Path) -> None:
    for artifact in ADIC_TARGET_FILES:
        path = project_dir / artifact["path"]
        if not path.exists():
            raise AssertionError(f"missing target file: {artifact['path']}")
        actual_size = path.stat().st_size
        if actual_size != artifact["size_bytes"]:
            raise AssertionError(
                f"size mismatch for {artifact['path']}: "
                f"expected={artifact['size_bytes']} actual={actual_size}"
            )
        actual_hash = file_sha256(path)
        if actual_hash != artifact["sha256"]:
            raise AssertionError(
                f"sha256 mismatch for {artifact['path']}: "
                f"expected={artifact['sha256']} actual={actual_hash}"
            )


def verify_profile(poc: Any) -> None:
    profile = poc._build_profile()
    expected = CERTIFICATE["profile"]
    if poc._pd(profile) != expected["profile_digest"]:
        raise AssertionError("profile_digest mismatch")
    if poc._rd(profile) != expected["ruleset_digest"]:
        raise AssertionError("ruleset_digest mismatch")


def verify_replay(project_dir: Path, poc: Any, filename: str) -> None:
    expected = CERTIFICATE["expected_replay"][filename]
    _, handoffs = poc._load(str(project_dir / filename))
    results, stopped_early = poc.run_chain(
        handoffs,
        charter_id="CHARTER-LS-2TO8-POC-001",
        stop_after_first_nonrelease=True,
    )
    labels = [r.decision.label.value for r in results]
    digests = [decision_digest(r.decision) for r in results]
    if labels != expected["labels"]:
        raise AssertionError(f"{filename}: labels mismatch: {labels} != {expected['labels']}")
    if digests != expected["decision_digests"]:
        raise AssertionError(f"{filename}: decision digest mismatch")
    if stopped_early != expected["stopped_early"]:
        raise AssertionError(f"{filename}: stopped_early mismatch")
    if len(handoffs) != expected["total_legs"]:
        raise AssertionError(f"{filename}: total_legs mismatch")
    if len(results) != expected["evaluated_legs"]:
        raise AssertionError(f"{filename}: evaluated_legs mismatch")

    last_good = poc.last_good(results)
    first_fail = poc.first_fail(results)
    last_good_seq = None if last_good is None else last_good.seq
    first_fail_seq = None if first_fail is None else first_fail.seq
    if "last_good_seq" in expected and last_good_seq != expected["last_good_seq"]:
        raise AssertionError(f"{filename}: last_good_seq mismatch")
    if "first_fail_seq" in expected and first_fail_seq != expected["first_fail_seq"]:
        raise AssertionError(f"{filename}: first_fail_seq mismatch")
    if "skipped_legs" in expected:
        skipped = len(handoffs) - len(results)
        if skipped != expected["skipped_legs"]:
            raise AssertionError(f"{filename}: skipped_legs mismatch")


def show_certificate() -> None:
    print(json.dumps(CERTIFICATE, ensure_ascii=False, sort_keys=True, indent=2))


def verify(project_dir: Path) -> None:
    verify_artifacts(project_dir)
    poc = load_poc(project_dir)
    verify_profile(poc)
    verify_replay(project_dir, poc, "sample_beta_breach_reject.json")
    verify_replay(project_dir, poc, "sample_chain_stop_demo.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="ADIC PoC Phase 1 single-file certificate")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="directory containing the ADIC target files; default is this file's directory",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="print certificate data instead of verifying target files",
    )
    args = parser.parse_args()

    if args.show:
        show_certificate()
        return

    verify(args.project_dir.resolve())
    print("ADIC single-file certificate verification: PASS")
    print(f"certificate_id={CERTIFICATE_ID}")
    print(f"target_files={','.join(item['path'] for item in ADIC_TARGET_FILES)}")


if __name__ == "__main__":
    main()
