"""Run the test suite with Allure results, then open the report if Allure is installed.

Usage: uv run python scripts/dev_tests.py [extra pytest arguments]

The Allure CLI is a separate install (Allure 3 needs Node.js, not Java):
    npm install -g allure
Without it, the tests still run and the exit code is still pytest's.
"""

import shutil
import subprocess
import sys

RESULTS_DIR = "allure-results"
INSTALL_HINT = "npm install -g allure"


def open_report(allure: str) -> None:
    """Serve the report from RESULTS_DIR in the browser until Ctrl+C.

    Allure 3 calls this command "open"; Allure 2 called it "serve".
    """
    version = subprocess.run(
        [allure, "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    command = "serve" if version.startswith("2.") else "open"
    try:
        subprocess.run([allure, command, RESULTS_DIR], check=False)
    except KeyboardInterrupt:
        pass


def run_tests() -> int:
    """Run pytest, open the report if possible, and return pytest's exit code."""
    tests = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"--alluredir={RESULTS_DIR}",
            "--clean-alluredir",
            *sys.argv[1:],
        ],
        check=False,
    )
    # shutil.which finds allure.cmd on Windows (npm's launcher), which a bare
    # "allure" passed to subprocess does not.
    allure = shutil.which("allure")
    if allure is None:
        print(
            f"\nAllure CLI not found, so no report was opened. Install it with: "
            f"{INSTALL_HINT}",
            file=sys.stderr,
        )
    else:
        open_report(allure)
    return tests.returncode


if __name__ == "__main__":
    sys.exit(run_tests())
