"""Shared fixtures. `project` builds a real git repository set up for BGL runs."""

import base64
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from bgl import start_run
from bgl.config import load_config
from bgl.home import HOME_ENV, create_machine_id, create_private_key
from bgl.ledger import append_row
from bgl.run import Run
from bgl.writers import load_identity

PROJECT_TOML = """
[project]
name = "example_project"
mode = "solo"
ledger = "bgl/ledger"
runs_dir = "bgl/runs"
witness = false
{chains}
[studies.alpha]
budget = 3
prefix = "AL"
claims = ["effect_exists", "effect_persists"]
prior_trials = 0
holdout = false
spec_files = ["docs/alpha.md"]

[studies.alpha.returns]
periods_per_year = 252
basis = "Daily close-to-close portfolio return"
risk_free = "none"
risk_free_reasoning = "Self-financing long-short book"

[studies.beta]
budget = 5
prefix = "BE"
claims = ["effect_exists"]
prior_trials = 0
holdout_start = 2025-01-01
holdout_files = ["data/holdout.csv"]
restricted_files = ["data/raw.csv"]
{custodian}spec_files = []

[studies.beta.returns]
periods_per_year = 252
basis = "Daily close-to-close portfolio return"
risk_free = "none"
risk_free_reasoning = "Self-financing long-short book"
"""

CHAIN = """
[project.chains.{code}]
person = "{person}"
key = "{key}"
"""


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


class Project:
    """A temporary BGL project: a git repository, bgl.toml, a writer and its key."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.home = home
        self.writers: dict[str, str] = {}  # code -> person
        self.keys: dict[str, str] = {}  # code -> registry key text
        self.custodian: str | None = None
        self._runs = 0

    # --- files and git -----------------------------------------------------

    def write(self, path: str, text: str = "x\n") -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        return target

    def commit(self, message: str = "change") -> str:
        run_git(self.root, "add", "-A")
        run_git(self.root, "commit", "-q", "--allow-empty", "-m", message)
        return run_git(self.root, "rev-parse", "HEAD")

    def toml(self) -> str:
        chains = "".join(
            CHAIN.format(code=code, person=person, key=self.keys[code])
            for code, person in self.writers.items()
        )
        custodian = f'custodian = "{self.custodian}"\n' if self.custodian else ""
        return PROJECT_TOML.format(chains=chains, custodian=custodian)

    def write_toml(self, edit: Callable[[str], str] | None = None) -> None:
        text = self.toml()
        self.write("bgl.toml", edit(text) if edit else text)

    def edit_toml(self, old: str, new: str) -> None:
        path = self.root / "bgl.toml"
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"{old!r} found {text.count(old)} times"
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")

    # --- writers -----------------------------------------------------------

    def add_writer(self, code: str, person: str) -> None:
        key = create_private_key(self.home, code)
        public = key.public_key().public_bytes_raw()
        self.keys[code] = "ed25519:" + base64.b64encode(public).decode("ascii")
        self.writers[code] = person

    def use_writer(self, code: str) -> None:
        self.write(".bgl/writer", code + "\n")

    # --- runs --------------------------------------------------------------

    def start(self, **overrides: Any) -> Run:
        """start_run with sensible defaults for a diagnostic design run in alpha."""
        arguments: dict[str, Any] = {
            "study": "alpha",
            "run_type": "diagnostic",
            "script": self.root / "scripts" / "run.py",
            "what_changed": "Lookback grid",
            "prediction": "Sharpe rises with lookback",
            "inputs": ["data/design.csv"],
            "config": {"lookbacks": [10, 20]},
            "sample": "design",
            "sample_start": "2020-01-01",
            "sample_end": "2024-12-31",
            "variants": 2,
        }
        arguments.update(overrides)
        return start_run(**arguments)

    def run(self, **overrides: Any) -> str:
        """Open and close one ok run; return its ID.

        Each call passes a different config, so R4 never sees a duplicate.
        """
        self._runs += 1
        overrides.setdefault("config", {"call": self._runs})
        result = overrides.pop("result", "done")
        with self.start(**overrides) as run:
            for claim in overrides.get("claims") or ():
                run.outcome(claim, "supported")
            if result is not None:
                run.result(result)
            return run.id

    def beta(self, **overrides: Any) -> dict[str, Any]:
        """Arguments for a design run in beta, the study with a holdout."""
        return {"study": "beta", **overrides}

    def holdout_arguments(self, selected: str, **overrides: Any) -> dict[str, Any]:
        """Arguments for beta's confirmatory holdout run."""
        arguments: dict[str, Any] = {
            "study": "beta",
            "run_type": "confirmatory",
            "sample": "holdout",
            "sample_start": "2025-01-01",
            "sample_end": "2025-06-30",
            "claims": ["effect_exists"],
            "selects": {"run": selected, "variant": 1},
            "variants": 1,
            "inputs": ["data/holdout.csv"],
        }
        arguments.update(overrides)
        return arguments

    def void(self, run_id: str, code: str = "CG1") -> None:
        """Append a void row for run_id (the void command arrives in step 10)."""
        config = load_config(self.root / "bgl.toml")
        identity = load_identity(self.root, config.project)
        assert identity.writer.code == code
        append_row(
            self.root,
            config.project,
            identity.writer,
            identity.private_key,
            {
                "event": "void",
                "id": run_id,
                "reason": "bug",
                "machine_id": identity.machine_id,
            },
        )

    # --- reading back ------------------------------------------------------

    def rows(self, code: str = "CG1") -> list[dict[str, Any]]:
        path = self.root / "bgl" / "ledger" / f"{code}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text("utf-8").splitlines()]

    def events(self, code: str = "CG1") -> list[str]:
        return [row["event"] for row in self.rows(code)]

    def last(self, code: str = "CG1") -> dict[str, Any]:
        return self.rows(code)[-1]

    def json(self, path: str) -> Any:
        return json.loads((self.root / path).read_text("utf-8"))


@pytest.fixture
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own git config (signing, hooks, user) out of tests."""
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_env: None) -> Project:
    """A committed BGL project with writer CG1 (researcher_a) on this machine."""
    home = tmp_path / "home"
    monkeypatch.setenv(HOME_ENV, str(home))
    create_machine_id(home)
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")
    run_git(root, "config", "commit.gpgsign", "false")
    made = Project(root, home)
    made.add_writer("CG1", "researcher_a")
    made.write_toml()
    made.write(".gitignore", ".bgl/\nscratch/\nbgl/runs/returns/\nbgl/runs/output/\n")
    made.write("scripts/run.py", "# the research script\n")
    made.write("docs/alpha.md", "# Alpha design\n")
    made.write("data/design.csv", "a,b\n1,2\n")
    made.write("data/holdout.csv", "a,b\n9,9\n")
    made.write("data/raw.csv", "a,b\n1,2\n9,9\n")
    made.use_writer("CG1")
    made.commit("first")
    return made


class Services:
    """Fake Rekor, two calendars and two explorers, reachable at public URLs."""

    REKOR = "https://rekor.example"
    CALENDARS = ("https://cal-a.example", "https://cal-b.example")
    EXPLORERS = ("https://explorer-a.example/api", "https://explorer-b.example/api")

    def __init__(self) -> None:
        from fakes import FakeCalendar, FakeExplorer, FakeRekor

        self.rekor = FakeRekor()
        self.calendars = [FakeCalendar(url) for url in self.CALENDARS]
        self.explorers = [FakeExplorer(self.calendars) for _ in self.EXPLORERS]
        self.routes = {self.REKOR: self.rekor.url}
        self.routes.update(
            {
                url: fake.url
                for url, fake in zip(self.CALENDARS, self.calendars, strict=True)
            }
        )
        self.routes.update(
            {
                url: fake.url
                for url, fake in zip(self.EXPLORERS, self.explorers, strict=True)
            }
        )

    def resolve(self, url: str) -> str:
        for public, local in self.routes.items():
            if url == public or url.startswith(public.rstrip("/") + "/"):
                return local + url[len(public.rstrip("/")) :]
        raise AssertionError(f"a test tried to reach the network: {url}")

    def confirm_all(self) -> None:
        for calendar in self.calendars:
            calendar.confirmed = True

    def stop(self) -> None:
        for server in [self.rekor, *self.calendars, *self.explorers]:
            server.stop()


WITNESS_TABLE = (
    '\n[project.witness]\nrekor_url = "https://rekor.example"\n'
    'ots_calendars = ["https://cal-a.example", "https://cal-b.example"]\n'
)


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> Any:
    from bgl import netio

    made = Services()
    monkeypatch.setattr(netio, "resolve", made.resolve)
    yield made
    made.stop()


@pytest.fixture
def witnessed(project: Project, services: Services) -> Project:
    """The project fixture with witnessing on, against the fake services."""
    project.edit_toml("witness = false\n", WITNESS_TABLE)
    project.commit("witness on")
    project.services = services  # type: ignore[attr-defined]
    return project
