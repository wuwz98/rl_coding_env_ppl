#!/usr/bin/env python3
"""Lab 02 MVP: compile one <GitHub repo, PR, issue> into a verified task."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath


HERE = Path(__file__).resolve().parent
BASE_IMAGE = (
    "hub.byted.org/alpine@"
    "sha256:0a4eaa0eecf5f8c050e5bba433f58c052be7587ee8af3e8b3910ef9ab5fbe9f5"
)
WORKSPACE = "/workspace/repo"
TEST_DIR_NAMES = {"test", "tests", "testing", "spec", "specs"}


class Reject(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def run(command, *, cwd=None, check=True, timeout=180):
    """Run one local command and keep stdout/stderr for evidence."""
    result = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "{}\n{}".format(" ".join(command), result.stdout + result.stderr)
        )
    return result


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_repo(value):
    """Return (owner/name, clone URL) for a GitHub repo name or URL."""
    value = value.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    match = re.fullmatch(
        r"(?:https?://github\.com/|git@github\.com:)?"
        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
        value,
    )
    if not match:
        raise Reject("UNSUPPORTED_REPOSITORY", "Expected a GitHub owner/repo.")
    full_name = "{}/{}".format(*match.groups())
    return full_name, "https://github.com/{}.git".format(full_name)


def pr_links_issue(body, issue_number):
    """Require an explicit closes/fixes/resolves #N relationship."""
    pattern = (
        r"(?is)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b"
        r"[^#\n]{{0,80}}#{}\b"
    ).format(issue_number)
    return re.search(pattern, body or "") is not None


def is_test_path(path):
    """The deliberately small P0 policy used to split D into G and T."""
    pure = PurePosixPath(path)
    directories = {part.lower() for part in pure.parts[:-1]}
    filename = pure.name.lower()
    return (
        bool(directories & TEST_DIR_NAMES)
        or filename.startswith("test_")
        or filename.endswith(("_test.py", "_tests.py"))
    )


def github_json(path):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "rl-coding-env-lab02",
    }
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer {}".format(os.environ["GITHUB_TOKEN"])
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_metadata(repo, pr_number, issue_number):
    """Fetch and check the minimum provenance needed by this MVP."""
    full_name, clone_url = parse_repo(repo)
    pull = github_json("/repos/{}/pulls/{}".format(full_name, pr_number))
    issue = github_json("/repos/{}/issues/{}".format(full_name, issue_number))
    if not pull.get("merged"):
        raise Reject("PR_NOT_MERGED", "The pull request is not merged.")
    if "pull_request" in issue:
        raise Reject("ISSUE_IS_PULL_REQUEST", "The issue number is another PR.")
    if not pr_links_issue(pull.get("body"), issue_number):
        raise Reject("NO_LINKED_ISSUE", "The PR body does not close the issue.")
    base = pull["base"]["sha"]
    head = pull["head"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", base or ""):
        raise Reject("AMBIGUOUS_BASE", "GitHub did not return a full base SHA.")
    if not re.fullmatch(r"[0-9a-f]{40}", head or ""):
        raise Reject("AMBIGUOUS_HEAD", "GitHub did not return a full head SHA.")
    return {
        "repo": full_name,
        "clone_url": clone_url,
        "base_commit": base,
        "head_commit": head,
        "pr": {
            "number": pr_number,
            "url": pull["html_url"],
            "title": pull["title"],
            "body": pull.get("body") or "",
        },
        "issue": {
            "number": issue_number,
            "url": issue["html_url"],
            "title": issue["title"],
            "body": issue.get("body") or "",
        },
    }


def extract_bgt(metadata, task_dir, temporary_root):
    """Fetch B/head, split D by path, and write B/G/T."""
    repo_dir = temporary_root / "repo"
    repo_dir.mkdir(parents=True)
    run(["git", "init", "--quiet"], cwd=repo_dir)
    run(["git", "remote", "add", "origin", metadata["clone_url"]], cwd=repo_dir)
    for sha in (metadata["base_commit"], metadata["head_commit"]):
        run(
            ["git", "fetch", "--quiet", "--no-tags", "--depth=1", "origin", sha],
            cwd=repo_dir,
        )

    base = metadata["base_commit"]
    head = metadata["head_commit"]
    changed = run(
        ["git", "diff", "--name-only", "-z", base, head],
        cwd=repo_dir,
    ).stdout.rstrip("\0").split("\0")
    changed = [path for path in changed if path]
    source_paths = [path for path in changed if not is_test_path(path)]
    test_paths = [path for path in changed if is_test_path(path)]
    if not source_paths or not test_paths:
        raise Reject(
            "PATCH_NOT_SEPARABLE",
            "The PR must contain both source paths (G) and test paths (T).",
        )

    private = task_dir / "private"
    build = task_dir / "build"
    private.mkdir()
    build.mkdir()
    gold_patch = private / "gold.patch"
    test_patch = private / "test.patch"
    gold_patch.write_text(
        run(
            ["git", "diff", "--binary", base, head, "--", *source_paths],
            cwd=repo_dir,
        ).stdout,
        encoding="utf-8",
    )
    test_patch.write_text(
        run(
            ["git", "diff", "--binary", base, head, "--", *test_paths],
            cwd=repo_dir,
        ).stdout,
        encoding="utf-8",
    )

    run(["git", "checkout", "--quiet", "--detach", base], cwd=repo_dir)
    run(["git", "apply", "--check", str(gold_patch)], cwd=repo_dir)
    run(["git", "apply", "--check", str(test_patch)], cwd=repo_dir)
    run(["git", "apply", str(gold_patch)], cwd=repo_dir)
    run(["git", "apply", "--check", str(test_patch)], cwd=repo_dir)
    run(["git", "reset", "--hard", base], cwd=repo_dir)

    run(
        [
            "git",
            "archive",
            "--format=tar.gz",
            "--output={}".format(build / "source.tar.gz"),
            base,
        ],
        cwd=repo_dir,
    )
    return {
        "changed_paths": changed,
        "source_paths": source_paths,
        "test_paths": test_paths,
    }


def write_dockerfile(task_dir, base_commit):
    """The build context contains B, never private/G/T."""
    text = """FROM {base_image}
RUN apk add --no-cache bash git python3
WORKDIR {workspace}
COPY source.tar.gz /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz -C {workspace} \\
    && rm /tmp/source.tar.gz \\
    && git init --quiet \\
    && git config user.name "RL Task Builder" \\
    && git config user.email "task-builder@example.invalid" \\
    && git add -A \\
    && git commit --quiet -m "answer-free base snapshot"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH={workspace}
LABEL org.rl-coding-env.upstream-base="{base_commit}"
CMD ["sleep", "infinity"]
""".format(
        base_image=BASE_IMAGE,
        workspace=WORKSPACE,
        base_commit=base_commit,
    )
    (task_dir / "build" / "Dockerfile").write_text(text, encoding="utf-8")


def build_image(task_dir, image_tag):
    """Build only from task/build and return the immutable image ID."""
    result = run(
        ["docker", "build", "--tag", image_tag, str(task_dir / "build")],
        timeout=600,
    )
    (task_dir / "validation" / "docker-build.log").write_text(
        result.stdout + result.stderr,
        encoding="utf-8",
    )
    inspected = json.loads(
        run(["docker", "image", "inspect", image_tag]).stdout
    )[0]
    return inspected["Id"]


def apply_patch(container, patch, remote_name):
    run(["docker", "cp", str(patch), "{}:/tmp/{}".format(container, remote_name)])
    result = run(
        [
            "docker",
            "exec",
            "--workdir",
            WORKSPACE,
            container,
            "git",
            "apply",
            "/tmp/{}".format(remote_name),
        ],
        check=False,
    )
    if result.returncode:
        raise Reject("PATCH_APPLY_FAILED", result.stderr.strip())


def run_case(task_dir, image_tag, name, apply_gold, apply_tests):
    """Run one case in a new network-disabled container."""
    container = "rl-lab02-{}".format(uuid.uuid4().hex[:12])
    case_dir = task_dir / "validation" / name
    case_dir.mkdir()
    try:
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container,
                "--network",
                "none",
                image_tag,
                "sleep",
                "infinity",
            ]
        )
        if apply_gold:
            apply_patch(container, task_dir / "private" / "gold.patch", "gold.patch")
        if apply_tests:
            apply_patch(container, task_dir / "private" / "test.patch", "test.patch")
        run(
            [
                "docker",
                "cp",
                str(task_dir / "private" / "unittest_runner.py"),
                "{}:/tmp/unittest_runner.py".format(container),
            ]
        )
        result = run(
            [
                "docker",
                "exec",
                "--workdir",
                WORKSPACE,
                container,
                "python3",
                "/tmp/unittest_runner.py",
                "--output",
                "/tmp/results.json",
            ],
            check=False,
        )
        run(
            [
                "docker",
                "cp",
                "{}:/tmp/results.json".format(container),
                str(case_dir / "results.json"),
            ]
        )
        (case_dir / "test.log").write_text(
            result.stdout + result.stderr,
            encoding="utf-8",
        )
        report = json.loads((case_dir / "results.json").read_text())
        return {
            "name": name,
            "exit_code": result.returncode,
            "tests_run": report["tests_run"],
            "counts": report["counts"],
            "statuses": {
                item["id"]: item["status"] for item in report["tests"]
            },
        }
    finally:
        run(["docker", "rm", "--force", container], check=False)


def quality_gate(cases, repeats):
    """Reject unhealthy, unsolved, regressing, or flaky candidates."""
    baseline = cases[0]
    noops = [case for case in cases if case["name"].startswith("noop-")]
    golds = [case for case in cases if case["name"].startswith("gold-")]
    if baseline["exit_code"] != 0:
        raise Reject("BASELINE_FAILED", "B does not pass its original tests.")
    if any(case["exit_code"] != 1 for case in noops):
        raise Reject("NO_F2P", "B+T must fail with ordinary test failures.")
    if any("error" in case["counts"] for case in noops):
        raise Reject("INVALID_TEST_FAILURE", "B+T contains test runner errors.")
    if any(case["exit_code"] != 0 for case in golds):
        raise Reject("GOLD_FAILED", "B+G+T must pass.")
    if len(noops) != repeats or len(golds) != repeats:
        raise Reject("VALIDATION_INCOMPLETE", "Not every repeat ran.")
    if any(case["statuses"] != noops[0]["statuses"] for case in noops[1:]):
        raise Reject("FLAKY", "B+T changed across clean repeats.")
    if any(case["statuses"] != golds[0]["statuses"] for case in golds[1:]):
        raise Reject("FLAKY", "B+G+T changed across clean repeats.")

    noop = noops[0]["statuses"]
    gold = golds[0]["statuses"]
    f2p = sorted(
        test_id
        for test_id, status in noop.items()
        if status == "fail" and gold.get(test_id) == "pass"
    )
    if not f2p:
        raise Reject("NO_F2P", "No failing B+T test became passing with G.")
    p2p = sorted(
        test_id
        for test_id, status in noop.items()
        if status == "pass" and gold.get(test_id) == "pass"
    )
    p2f = sorted(
        test_id
        for test_id, status in baseline["statuses"].items()
        if status == "pass" and gold.get(test_id) != "pass"
    )
    if p2f:
        raise Reject("P2F_REGRESSION", "Gold broke original passing tests.")
    return {
        "status": "VERIFIED",
        "repeats": repeats,
        "fail_to_pass": f2p,
        "pass_to_pass_count": len(p2p),
        "pass_to_fail": p2f,
        "cases": [
            {
                "name": case["name"],
                "exit_code": case["exit_code"],
                "tests_run": case["tests_run"],
                "counts": case["counts"],
            }
            for case in cases
        ],
    }


def compile_task(repo, pr_number, issue_number, output_root, repeats):
    try:
        full_name, _clone_url = parse_repo(repo)
    except Reject:
        full_name = re.sub(r"[^a-z0-9._-]+", "-", repo.lower()).strip("-")
        full_name = full_name or "invalid-repository"
    task_id = "{}-issue-{}-pr-{}".format(
        full_name.replace("/", "__"),
        issue_number,
        pr_number,
    ).lower()
    task_dir = output_root / task_id
    shutil.rmtree(task_dir, ignore_errors=True)
    task_dir.mkdir(parents=True)
    try:
        metadata = collect_metadata(repo, pr_number, issue_number)
        (task_dir / "public").mkdir()
        (task_dir / "validation").mkdir()
        with tempfile.TemporaryDirectory(prefix="lab02-") as temporary:
            extraction = extract_bgt(metadata, task_dir, Path(temporary))
        shutil.copy2(HERE / "unittest_runner.py", task_dir / "private")
        write_dockerfile(task_dir, metadata["base_commit"])

        image_tag = "rl-task:{}".format(task_id)
        image_id = build_image(task_dir, image_tag)
        cases = [
            run_case(task_dir, image_tag, "baseline", False, False),
        ]
        for repeat in range(1, repeats + 1):
            cases.append(
                run_case(
                    task_dir,
                    image_tag,
                    "noop-{}".format(repeat),
                    False,
                    True,
                )
            )
            cases.append(
                run_case(
                    task_dir,
                    image_tag,
                    "gold-{}".format(repeat),
                    True,
                    True,
                )
            )
        summary = quality_gate(cases, repeats)
        write_json(task_dir / "validation" / "summary.json", summary)
        write_json(
            task_dir / "private" / "source.json",
            {**metadata, "extraction": extraction},
        )
        write_json(
            task_dir / "build" / "manifest.json",
            {
                "base_image": BASE_IMAGE,
                "image": image_tag,
                "image_id": image_id,
            },
        )
        statement = "{}\n\n{}".format(
            metadata["issue"]["title"],
            metadata["issue"]["body"],
        ).strip()
        write_json(
            task_dir / "public" / "task.json",
            {
                "status": "VERIFIED",
                "task_id": task_id,
                "repo": metadata["repo"],
                "issue": {
                    "number": metadata["issue"]["number"],
                    "url": metadata["issue"]["url"],
                },
                "base_commit": metadata["base_commit"],
                "problem_statement": statement,
                "image": image_tag,
                "workspace": WORKSPACE,
            },
        )
        return {
            "status": "VERIFIED",
            "task_dir": str(task_dir),
            "image_id": image_id,
            "fail_to_pass": summary["fail_to_pass"],
            "pass_to_pass_count": summary["pass_to_pass_count"],
        }
    except Exception as error:
        code = error.code if isinstance(error, Reject) else "PIPELINE_ERROR"
        shutil.rmtree(task_dir, ignore_errors=True)
        task_dir.mkdir(parents=True)
        (task_dir / "_REJECTED.md").write_text(
            "# Rejected task\n\n"
            "- Reason code: `{}`\n"
            "- Input: `<{}, PR #{}, issue #{}>`\n\n"
            "## Reason\n\n{}\n".format(
                code,
                repo,
                pr_number,
                issue_number,
                str(error),
            ),
            encoding="utf-8",
        )
        return {
            "status": "REJECTED",
            "task_dir": str(task_dir),
            "reason_code": code,
            "reason": str(error),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=HERE / "tasks")
    args = parser.parse_args()
    result = compile_task(
        args.repo,
        args.pr,
        args.issue,
        args.output,
        args.repeats,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "VERIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
