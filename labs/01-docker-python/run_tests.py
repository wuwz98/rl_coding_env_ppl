"""Small unittest reporter for a trusted teaching exercise, not a secure grader."""

import argparse
import json
import sys
import unittest
from pathlib import Path


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({"id": test.id(), "status": "pass"})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({"id": test.id(), "status": "fail"})

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({"id": test.id(), "status": "error"})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append({"id": test.id(), "status": "skip", "reason": reason})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect", type=int, required=True)
    parser.add_argument("--report", default="/tmp/results.json")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    complete = (
        result.testsRun == args.expect
        and args.expect > 0
        and len(result.records) == args.expect
        and not result.skipped
    )
    resolved = complete and result.wasSuccessful()
    report = {
        "expected": args.expect,
        "tests_run": result.testsRun,
        "complete": complete,
        "resolved": resolved,
        "reward": int(resolved),
        "tests": result.records,
    }
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    return 0 if resolved else 1


if __name__ == "__main__":
    sys.exit(main())
