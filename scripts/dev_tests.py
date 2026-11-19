"""Run the test suite with Allure results, then open the report."""

import subprocess
import sys


def run_tests() -> int:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--alluredir=allure-results",
            "--clean-alluredir",
        ]
    )
    subprocess.run(["allure", "serve", "allure-results"])
    return result.returncode


if __name__ == "__main__":
    sys.exit(run_tests())
