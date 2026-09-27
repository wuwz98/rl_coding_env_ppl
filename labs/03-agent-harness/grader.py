#!/usr/bin/env python3
"""Independent Lab 03 candidate grader: fresh B + C + T -> reward."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


WORKSPACE_FALLBACK = "/workspace/repo"


class GradeInfraError(RuntimeError):
    pass


class InvalidCandidate(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def run_command(
    command: List[str],
    *,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            [str(part) for part in command],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise GradeInfraError(
            "host command timed out after {} seconds: {}".format(
                timeout,
                " ".join(command),
            )
        ) from error
    except OSError as error:
        raise GradeInfraError(
            "cannot execute {}: {}".format(command[0], error)
        ) from error
    if check and result.returncode != 0:
        raise GradeInfraError(
            "command failed with status {}: {}\n{}".format(
                result.returncode,
                " ".join(command),
                result.stdout + result.stderr,
            )
        )
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def patch_paths(patch: Path) -> List[str]:
    if patch.stat().st_size == 0:
        return []
    # Parse outside the outer project worktree. Otherwise git apply may skip
    # paths that are not below the current working directory.
    with tempfile.TemporaryDirectory(prefix="lab03-patch-parse-") as temporary:
        result = subprocess.run(
            ["git", "apply", "--numstat", str(patch.resolve())],
            cwd=temporary,
            text=True,
            capture_output=True,
        )
    if result.returncode != 0:
        raise InvalidCandidate(
            "INVALID_PATCH",
            "candidate is not a parseable Git patch: {}".format(
                result.stderr.strip()
            ),
        )
    paths = []
    for line in result.stdout.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3:
            raise InvalidCandidate(
                "INVALID_PATCH",
                "unexpected git numstat record: {}".format(line),
            )
        paths.append(fields[2])
    return sorted(set(paths))


def load_contract(task_dir: Path) -> Dict[str, Any]:
    public = read_json(task_dir / "public" / "task.json")
    source = read_json(task_dir / "private" / "source.json")
    validation = read_json(task_dir / "validation" / "summary.json")
    gold_results_paths = sorted(
        (task_dir / "validation").glob("gold-*/results.json")
    )
    if not gold_results_paths:
        raise GradeInfraError("Lab 02 task has no Gold results.json")
    gold_results = read_json(gold_results_paths[0])
    expected = {
        item["id"]: item["status"]
        for item in gold_results.get("tests") or []
    }
    if not expected or any(status != "pass" for status in expected.values()):
        raise GradeInfraError("Lab 02 Gold result is not a complete passing oracle")
    f2p = sorted(validation.get("fail_to_pass") or [])
    missing_f2p = sorted(set(f2p) - set(expected))
    if missing_f2p:
        raise GradeInfraError(
            "F2P IDs are absent from the Gold result: {}".format(missing_f2p)
        )
    p2p = sorted(set(expected) - set(f2p))
    recorded_p2p_count = validation.get("pass_to_pass_count")
    if recorded_p2p_count != len(p2p):
        raise GradeInfraError(
            "P2P count disagrees with Gold result: {} != {}".format(
                recorded_p2p_count,
                len(p2p),
            )
        )
    extraction = source.get("extraction") or {}
    allowed_paths = sorted(extraction.get("source_paths") or [])
    test_paths = sorted(extraction.get("test_paths") or [])
    if not allowed_paths or not test_paths:
        raise GradeInfraError("Lab 02 source/test path policy is missing")
    return {
        "task_id": public["task_id"],
        "image": public["image"],
        "workspace": public.get("workspace") or WORKSPACE_FALLBACK,
        "allowed_paths": allowed_paths,
        "test_paths": test_paths,
        "expected_ids": sorted(expected),
        "f2p": f2p,
        "p2p": p2p,
    }


def docker_exec(
    container: str,
    workspace: str,
    command: List[str],
    *,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return run_command(
        [
            "docker",
            "exec",
            "--workdir",
            workspace,
            container,
            *command,
        ],
        timeout=timeout,
        check=check,
    )


def reset_test_paths(
    container: str,
    workspace: str,
    test_paths: List[str],
) -> None:
    existing = []
    for path in test_paths:
        check = docker_exec(
            container,
            workspace,
            ["git", "cat-file", "-e", "HEAD:{}".format(path)],
            check=False,
        )
        if check.returncode == 0:
            existing.append(path)
        else:
            docker_exec(
                container,
                workspace,
                ["rm", "-rf", "--", path],
            )
    if existing:
        docker_exec(
            container,
            workspace,
            ["git", "checkout", "HEAD", "--", *existing],
        )


def apply_patch(
    container: str,
    workspace: str,
    local_patch: Path,
    remote_path: str,
    *,
    candidate: bool,
) -> None:
    copied = run_command(
        ["docker", "cp", str(local_patch), "{}:{}".format(container, remote_path)],
        check=False,
    )
    if copied.returncode != 0:
        raise GradeInfraError(
            "cannot copy {} into grader: {}".format(
                local_patch.name,
                copied.stderr.strip(),
            )
        )
    applied = docker_exec(
        container,
        workspace,
        ["git", "apply", remote_path],
        check=False,
    )
    if applied.returncode != 0:
        if candidate:
            raise InvalidCandidate(
                "PATCH_APPLY_FAILED",
                "candidate does not apply to B: {}".format(
                    applied.stderr.strip()
                ),
            )
        raise GradeInfraError(
            "trusted test patch does not apply: {}".format(
                applied.stderr.strip()
            )
        )


def evaluate_report(
    contract: Dict[str, Any],
    report: Dict[str, Any],
    test_exit_code: int,
) -> Dict[str, Any]:
    statuses = {
        item["id"]: item["status"]
        for item in report.get("tests") or []
    }
    expected = set(contract["expected_ids"])
    missing = sorted(expected - set(statuses))
    f2p_not_passing = sorted(
        test_id
        for test_id in contract["f2p"]
        if statuses.get(test_id) != "pass"
    )
    p2p_not_passing = sorted(
        test_id
        for test_id in contract["p2p"]
        if statuses.get(test_id) != "pass"
    )
    complete = (
        report.get("complete") is True
        and report.get("tests_run") == len(statuses)
        and not missing
    )
    passed = (
        test_exit_code == 0
        and complete
        and not f2p_not_passing
        and not p2p_not_passing
    )
    return {
        "reward": 1 if passed else 0,
        "verdict": "PASS" if passed else "FAIL",
        "reason_code": "ALL_REQUIRED_TESTS_PASS" if passed else "TESTS_FAILED",
        "test_exit_code": test_exit_code,
        "tests_run": report.get("tests_run", 0),
        "complete": complete,
        "counts": report.get("counts") or {},
        "expected_test_count": len(expected),
        "f2p_count": len(contract["f2p"]),
        "p2p_count": len(contract["p2p"]),
        "missing_required_tests": missing,
        "f2p_not_passing": f2p_not_passing,
        "p2p_not_passing": p2p_not_passing,
    }


def grade_candidate(
    *,
    task_dir: Path,
    candidate_patch: Path,
    output_dir: Path,
    timeout: int = 180,
) -> Dict[str, Any]:
    task_dir = task_dir.resolve()
    candidate_patch = candidate_patch.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    container: Optional[str] = None
    container_removed = True
    result: Dict[str, Any]
    patch_bytes = candidate_patch.read_bytes()
    candidate_info = {
        "path": str(candidate_patch),
        "bytes": len(patch_bytes),
        "sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "touched_paths": [],
        "allowed_paths": [],
    }

    try:
        contract = load_contract(task_dir)
        candidate_info["allowed_paths"] = contract["allowed_paths"]
        touched = patch_paths(candidate_patch)
        candidate_info["touched_paths"] = touched
        disallowed = sorted(set(touched) - set(contract["allowed_paths"]))
        if disallowed:
            raise InvalidCandidate(
                "CANDIDATE_PATH_REJECTED",
                "candidate modifies paths outside the P0 source allowlist: {}".format(
                    disallowed
                ),
            )

        inspected = run_command(
            ["docker", "image", "inspect", contract["image"]],
            check=False,
        )
        if inspected.returncode != 0:
            raise GradeInfraError(
                "task image is missing: {}".format(contract["image"])
            )
        container = "rl-lab03-grade-{}".format(uuid.uuid4().hex[:12])
        run_command(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container,
                "--network",
                "none",
                "--cpus",
                "1",
                "--memory",
                "1g",
                contract["image"],
                "sleep",
                "infinity",
            ]
        )
        container_removed = False

        if patch_bytes:
            apply_patch(
                container,
                contract["workspace"],
                candidate_patch,
                "/tmp/candidate.patch",
                candidate=True,
            )
        reset_test_paths(
            container,
            contract["workspace"],
            contract["test_paths"],
        )
        apply_patch(
            container,
            contract["workspace"],
            task_dir / "private" / "test.patch",
            "/tmp/test.patch",
            candidate=False,
        )
        runner = task_dir / "private" / "unittest_runner.py"
        copied = run_command(
            ["docker", "cp", str(runner), "{}:/tmp/unittest_runner.py".format(container)],
            check=False,
        )
        if copied.returncode != 0:
            raise GradeInfraError(
                "cannot copy trusted test runner: {}".format(copied.stderr.strip())
            )

        test_result = docker_exec(
            container,
            contract["workspace"],
            [
                "timeout",
                "-s",
                "KILL",
                "{}s".format(timeout),
                "python3",
                "/tmp/unittest_runner.py",
                "--output",
                "/tmp/results.json",
            ],
            timeout=timeout + 15,
            check=False,
        )
        (output_dir / "test.log").write_text(
            test_result.stdout + test_result.stderr,
            encoding="utf-8",
        )
        copied_results = run_command(
            [
                "docker",
                "cp",
                "{}:/tmp/results.json".format(container),
                str(output_dir / "results.json"),
            ],
            check=False,
        )
        if copied_results.returncode != 0:
            if test_result.returncode in {124, 137}:
                result = {
                    "reward": 0,
                    "verdict": "FAIL",
                    "reason_code": "TEST_TIMEOUT",
                    "test_exit_code": test_result.returncode,
                    "tests_run": 0,
                    "complete": False,
                    "counts": {},
                    "expected_test_count": len(contract["expected_ids"]),
                    "f2p_count": len(contract["f2p"]),
                    "p2p_count": len(contract["p2p"]),
                    "missing_required_tests": contract["expected_ids"],
                    "f2p_not_passing": contract["f2p"],
                    "p2p_not_passing": contract["p2p"],
                }
            else:
                raise GradeInfraError(
                    "test runner produced no results.json: {}".format(
                        copied_results.stderr.strip()
                    )
                )
        else:
            report = read_json(output_dir / "results.json")
            result = evaluate_report(
                contract,
                report,
                test_result.returncode,
            )
    except InvalidCandidate as error:
        result = {
            "reward": 0,
            "verdict": "INVALID_CANDIDATE",
            "reason_code": error.code,
            "message": str(error),
        }
    except Exception as error:
        result = {
            "reward": None,
            "verdict": "INFRA_ERROR",
            "reason_code": "GRADER_INFRA_ERROR",
            "message": "{}: {}".format(type(error).__name__, error),
        }
    finally:
        if container:
            removed = run_command(
                ["docker", "rm", "--force", container],
                check=False,
                timeout=60,
            )
            container_removed = removed.returncode == 0

    grade = {
        "schema_version": "lab03-grade-v1",
        "task_id": (
            contract["task_id"]
            if "contract" in locals()
            else task_dir.name
        ),
        "candidate": candidate_info,
        **result,
        "container_removed": container_removed,
    }
    write_json(output_dir / "grade.json", grade)
    return grade


def attach_grade(run_dir: Path, grade: Dict[str, Any]) -> None:
    for name in ("summary.json", "trajectory.json"):
        path = run_dir / name
        if not path.is_file():
            continue
        value = read_json(path)
        if name == "trajectory.json":
            value["schema_version"] = "lab03-trajectory-v2"
            value["grading"] = grade
        else:
            value["grading"] = {
                "path": "grading/grade.json",
                "reward": grade["reward"],
                "verdict": grade["verdict"],
                "reason_code": grade["reason_code"],
            }
            value["reward"] = grade["reward"]
            value["verdict"] = grade["verdict"]
        write_json(path, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grade candidate.patch in a fresh Lab 02 task container."
    )
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("candidate_patch", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--attach-run", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        grade = grade_candidate(
            task_dir=args.task_dir,
            candidate_patch=args.candidate_patch,
            output_dir=args.output,
            timeout=args.timeout,
        )
        if args.attach_run:
            attach_grade(args.attach_run, grade)
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "error": "{}: {}".format(type(error).__name__, error),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(grade, indent=2, ensure_ascii=False))
    if grade["reward"] is None:
        return 2
    return 0 if grade["reward"] == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
