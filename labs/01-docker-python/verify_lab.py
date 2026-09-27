"""Replay the documented Docker exercise. Run AFTER the manual walkthrough."""

import json
import subprocess
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
IMAGE = "rl-env-lab:v1"
PUBLIC = {
    "test_stats.LargestTests.test_empty": "pass",
    "test_stats.LargestTests.test_mixed": "pass",
    "test_stats.LargestTests.test_positive": "pass",
}
HIDDEN = {
    "test_regression.RegressionTests.test_all_negative": "fail",
    "test_regression.RegressionTests.test_single_negative": "fail",
}


def docker(*args, check=True, timeout=60):
    return subprocess.run(
        ["docker", *args], cwd=ROOT, text=True, capture_output=True,
        timeout=timeout, check=check,
    )


def main():
    run_id = uuid.uuid4().hex[:12]
    output = ROOT / "artifacts" / run_id
    output.mkdir(parents=True)
    image = json.loads(docker("image", "inspect", IMAGE).stdout)[0]
    (output / "image.json").write_text(json.dumps({
        "id": image["Id"], "architecture": image["Architecture"],
        "os": image["Os"], "repo_digests": image["RepoDigests"],
    }, indent=2) + "\n")
    cases = ["baseline"] + [name for _ in range(3) for name in ("bug", "gold")]
    summary = []
    for index, case in enumerate(cases):
        name = f"rl-lab-check-{run_id}-{index}"
        try:
            docker("run", "-d", "--name", name, "--network", "none",
                   "--cpus", "1", "--memory", "256m", IMAGE, "sleep", "infinity")
            docker("exec", name, "sh", "-c",
                   "test ! -e /app/tests/test_regression.py && test ! -e /tmp/prior_run")
            docker("exec", name, "touch", "/tmp/prior_run")
            expected = dict(PUBLIC)
            if case != "baseline":
                docker("cp", str(ROOT / "private_tests") + "/.", f"{name}:/app/tests/")
                expected.update(HIDDEN)
            if case == "gold":
                docker("cp", str(ROOT / "solution/stats.py"), f"{name}:/app/stats.py")
                expected = dict.fromkeys(expected, "pass")
            result = docker("exec", name, "python", "run_tests.py", "--expect",
                            str(len(expected)), check=False)
            stem = f"{index}-{case}"
            (output / f"{stem}.log").write_text(result.stdout + result.stderr)
            docker("cp", f"{name}:/tmp/results.json", str(output / f"{stem}.json"))
            report = json.loads((output / f"{stem}.json").read_text())
            actual = {item["id"]: item["status"] for item in report["tests"]}
            expected_rc = 1 if case == "bug" else 0
            ok = (actual == expected and report["complete"]
                  and report["tests_run"] == len(expected)
                  and result.returncode == expected_rc
                  and report["reward"] == int(case != "bug"))
            summary.append({"case": case, "matched": bool(ok), "exit_code": result.returncode})
            if not ok:
                raise RuntimeError(f"Unexpected outcome: {stem}; see {output}")
        finally:
            docker("rm", "-f", name, check=False)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Verified baseline + 3 bug/gold pairs. Evidence: {output}")


if __name__ == "__main__":
    main()
