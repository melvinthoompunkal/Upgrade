"""Controller-owned native unittest discovery with complete load_tests and JUnit capture."""

import signal
import sys
import time
import unittest
from xml.etree.ElementTree import Element, ElementTree, SubElement


def timed_out(signum, frame):
    raise TimeoutError("Native test exceeded 10 seconds.")


class CapturedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
        self.case = None

    def record(self, test, outcome, detail):
        if self.case is None:
            self.cases.append({"id": test.id(), "outcome": outcome, "detail": detail, "elapsed": 0})
        else:
            self.case.update(outcome=outcome, detail=detail)

    def startTest(self, test):
        super().startTest(test)
        self.started = time.monotonic()
        self.case = {"id": test.id(), "outcome": "passed", "detail": ""}
        signal.alarm(10)

    def stopTest(self, test):
        signal.alarm(0)
        self.case["elapsed"] = time.monotonic() - self.started
        self.cases.append(self.case)
        self.case = None
        super().stopTest(test)

    def addFailure(self, test, err):
        self.record(test, "failure", self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.record(test, "error", self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.record(test, "skipped", reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.record(test, "skipped", "Expected failures do not establish a green baseline.")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.record(test, "error", "Unexpected success in an expected-failure test.")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.record(
                test,
                "failure" if issubclass(err[0], test.failureException) else "error",
                self._exc_info_to_string(err, test),
            )
        super().addSubTest(test, subtest, err)


def main():
    signal.signal(signal.SIGALRM, timed_out)
    suite = unittest.defaultTestLoader.discover("/snapshot/tests", top_level_dir="/snapshot")
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2, resultclass=CapturedResult).run(suite)
    root = Element("testsuite", name="native-unittest", tests=str(len(result.cases)))
    for case in result.cases:
        node = SubElement(root, "testcase", name=case["id"], time=str(case["elapsed"]))
        if case["outcome"] != "passed":
            SubElement(node, case["outcome"], message=case["detail"][:500]).text = case["detail"]
    if not expected or len(result.cases) != expected:
        node = SubElement(root, "testcase", name="controller.collection_integrity")
        SubElement(node, "error", message="Native suite did not execute all collected cases.")
    ElementTree(root).write("/results/junit.xml", encoding="utf-8", xml_declaration=True)
    return 0 if expected and len(result.cases) == expected and result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
