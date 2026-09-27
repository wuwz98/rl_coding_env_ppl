#!/usr/bin/env python3
"""Offline verification for the checked-in Lab 04 example."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXAMPLE = HERE / "runs" / "more-itertools-pr-720-pro"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "-v", "test_discover_candidates.py"],
        cwd=HERE,
        text=True,
    )
    if tests.returncode:
        return tests.returncode

    candidates = [
        json.loads(line)
        for line in (EXAMPLE / "candidates.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line
    ]
    summary = json.loads((EXAMPLE / "summary.json").read_text(encoding="utf-8"))
    require(len(candidates) == 1, "expected exactly one example candidate")
    candidate = candidates[0]
    require(candidate["status"] == "DISCOVERED", "candidate status must be DISCOVERED")
    require(
        candidate["verification_status"] == "NOT_RUN",
        "Lab 04 must not claim Lab 02 verification",
    )
    require(candidate["pr"]["number"] == 720, "expected PR #720")
    require(candidate["issue"]["number"] == 719, "expected issue #719")
    require(candidate["patch"]["source"]["changed_lines"] == 21, "source lines")
    require(candidate["patch"]["tests"]["changed_lines"] == 7, "test lines")
    require(
        candidate["features"]["issue_comments_before_pr_count"] == 1,
        "expected one pre-PR issue clarification",
    )

    assessment = candidate["semantic_review"]["assessment"]
    require(assessment["original_issue"]["label"] == "AMBIGUOUS", "original clarity")
    require(
        assessment["with_pre_pr_comments"]["label"] == "CLEAR",
        "clarified issue clarity",
    )
    require(assessment["pr_issue_alignment"]["label"] == "ALIGNED", "PR alignment")
    require(
        "input iterable" in assessment["behavioral_spec"],
        "behavioral spec must select the input-window interpretation",
    )
    require(summary["candidate_count"] == 1, "summary candidate count")
    require(summary["rejected_record_count"] == 0, "summary rejected count")
    require(summary["scan_scope"] == "TARGETED", "summary scan scope")

    print("Lab 04 verification passed")
    print("candidate: {}".format(candidate["candidate_id"]))
    print("source changed lines: 21")
    print("test changed lines: 7")
    print("original issue: AMBIGUOUS")
    print("with pre-PR comments: CLEAR")
    print("verification status: NOT_RUN (Lab 02 remains authoritative)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
