"""Run unittest discovery and write one normalized JSON record per test ID."""

import argparse
import json
import sys
import traceback
import unittest
from pathlib import Path


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}

    def startTest(self, test):
        self.records.setdefault(test.id(), {"id": test.id(), "status": "running"})
        super().startTest(test)

    def stopTest(self, test):
        record = self.records[test.id()]
        if record["status"] == "running":
            record["status"] = "pass"
        super().stopTest(test)

    def addSuccess(self, test):
        self.records[test.id()]["status"] = "pass"
        super().addSuccess(test)

    def addFailure(self, test, error):
        self._record_error(test, "fail", error)
        super().addFailure(test, error)

    def addError(self, test, error):
        self._record_error(test, "error", error)
        super().addError(test, error)

    def addSkip(self, test, reason):
        self.records[test.id()].update({"status": "skip", "detail": reason})
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, error):
        if error:
            status = (
                "fail"
                if issubclass(error[0], test.failureException)
                else "error"
            )
            self._record_error(test, status, error)
        super().addSubTest(test, subtest, error)

    def _record_error(self, test, status, error):
        self.records[test.id()].update(
            {
                "status": status,
                "detail": "".join(traceback.format_exception(*error)),
            }
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    suite = unittest.defaultTestLoader.discover(
        start_dir="tests",
        pattern="test*.py",
        top_level_dir=".",
    )
    result = unittest.TextTestRunner(
        stream=sys.stdout,
        verbosity=2,
        resultclass=Result,
    ).run(suite)
    tests = sorted(result.records.values(), key=lambda item: item["id"])
    counts = {}
    for test in tests:
        counts[test["status"]] = counts.get(test["status"], 0) + 1
    report = {
        "tests_run": result.testsRun,
        "complete": result.testsRun == len(tests) and result.testsRun > 0,
        "counts": counts,
        "tests": tests,
    }
    Path(args.output).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    success = (
        report["complete"]
        and not result.failures
        and not result.errors
        and not result.unexpectedSuccesses
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
