"""Audit-hook spike (build step 5): what can sys.addaudithook see and block?

Run with: uv run python spikes/audit_hook/spike.py
Optional libraries are used if installed: pandas, pyarrow, polars, numpy.

Each case runs in a fresh interpreter so hooks, imports and caches never leak
between cases. Prints a Markdown table. Not part of the bgl package.
"""

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

# Installed in every child. Records every event whose arguments mention a
# watched path, and optionally refuses opens of the target.
HOOK = textwrap.dedent(
    """
    import os, sys, json
    _WATCH = json.loads(os.environ["SPIKE_WATCH"])
    _BLOCK = os.environ.get("SPIKE_BLOCK") == "1"
    _SEEN = []

    def _hook(event, args):
        if event in ("open", "os.open") and args:
            path = args[0]
            if isinstance(path, (str, bytes, os.PathLike)):
                text = os.fsdecode(path)
                for watched in _WATCH:
                    if watched in os.path.basename(text):
                        _SEEN.append([event, watched])
                        if _BLOCK:
                            raise PermissionError("blocked by audit hook")
        elif event == "exec" and args and hasattr(args[0], "co_filename"):
            # Module bodies run through exec(code); co_filename names the file.
            for watched in _WATCH:
                if watched in os.path.basename(args[0].co_filename):
                    _SEEN.append(["exec(co_filename)", watched])
        elif event == "sqlite3.connect" and args:
            text = os.fsdecode(args[0]) if isinstance(args[0], (str, bytes)) else ""
            for watched in _WATCH:
                if watched in os.path.basename(text):
                    _SEEN.append([event, watched])
                    if _BLOCK:
                        raise PermissionError("blocked by audit hook")

    sys.addaudithook(_hook)
    """
)

FOOTER = textwrap.dedent(
    """
    import atexit
    def _report():
        repo = os.environ.get("SPIKE_REPO")
        scanned = []
        if repo:
            for name, module in list(sys.modules.items()):
                f = getattr(module, "__file__", None)
                if f and os.path.abspath(f).startswith(repo):
                    scanned.append(name)
        print("SPIKE_RESULT " + json.dumps({"seen": _SEEN, "scan": sorted(scanned)}))
    atexit.register(_report)
    """
)


def run_case(
    body: str, watch: list[str], *, block: bool = False, repo: Path | None = None
) -> dict[str, object]:
    env = {
        "SPIKE_WATCH": json.dumps(watch),
        "SPIKE_BLOCK": "1" if block else "0",
        "PYTHONDONTWRITEBYTECODE": "0",
    }
    if repo is not None:
        env["SPIKE_REPO"] = str(repo)
    script = HOOK + FOOTER + "\n" + textwrap.dedent(body)
    import os

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        cwd=str(repo) if repo else None,
        timeout=120,
    )
    record: dict[str, object] = {"ok": result.returncode == 0, "seen": [], "scan": []}
    for line in result.stdout.splitlines():
        if line.startswith("SPIKE_RESULT "):
            record.update(json.loads(line[len("SPIKE_RESULT ") :]))
    if result.returncode != 0:
        record["error"] = result.stderr.strip().splitlines()[-1:] or ["?"]
    return record


def make_data(folder: Path) -> dict[str, Path]:
    import sqlite3

    files = {
        "csv": folder / "prices.csv",
        "txt": folder / "notes.txt",
        "sqlite": folder / "db.sqlite",
    }
    files["csv"].write_text("a,b\n1,2\n3,4\n")
    files["txt"].write_text("hello\n")
    con = sqlite3.connect(files["sqlite"])
    with con:
        con.execute("create table t (x)")
        con.execute("insert into t values (1)")
    con.close()  # "with" commits but does not close; Windows then locks the file
    try:
        import numpy as np

        files["npy"] = folder / "arr.npy"
        np.save(files["npy"], np.arange(4))
        files["raw"] = folder / "arr.bin"
        np.arange(4, dtype="int64").tofile(files["raw"])
    except ImportError:
        pass
    try:
        import pyarrow as pa
        import pyarrow.feather as feather
        import pyarrow.parquet as pq

        table = pa.table({"a": [1, 3], "b": [2, 4]})
        files["parquet"] = folder / "prices.parquet"
        pq.write_table(table, files["parquet"])
        files["feather"] = folder / "prices.feather"
        feather.write_feather(table, files["feather"])
    except ImportError:
        pass
    return files


READERS: dict[str, tuple[str, str]] = {
    # name: (data file key, code using PATH)
    "open()": ("txt", "open(PATH).read()"),
    "pathlib read_text": ("txt", "import pathlib; pathlib.Path(PATH).read_text()"),
    "os.open": ("txt", "import os; os.close(os.open(PATH, os.O_RDONLY))"),
    "csv module": ("csv", "import csv; list(csv.reader(open(PATH)))"),
    "mmap": (
        "txt",
        "import mmap; f = open(PATH, 'rb'); "
        "mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)",
    ),
    "sqlite3.connect": (
        "sqlite",
        "import sqlite3; sqlite3.connect(PATH).execute('select * from t').fetchall()",
    ),
    "pandas.read_csv (C engine)": ("csv", "import pandas as pd; pd.read_csv(PATH)"),
    "pandas.read_csv (pyarrow engine)": (
        "csv",
        "import pandas as pd; pd.read_csv(PATH, engine='pyarrow')",
    ),
    "pandas.read_parquet": ("parquet", "import pandas as pd; pd.read_parquet(PATH)"),
    "pyarrow.parquet.read_table": (
        "parquet",
        "import pyarrow.parquet as pq; pq.read_table(PATH)",
    ),
    "pyarrow.feather.read_table": (
        "feather",
        "import pyarrow.feather as f; f.read_table(PATH)",
    ),
    "pyarrow.csv.read_csv": ("csv", "import pyarrow.csv as c; c.read_csv(PATH)"),
    "polars.read_csv": ("csv", "import polars as pl; pl.read_csv(PATH)"),
    "polars.read_parquet": ("parquet", "import polars as pl; pl.read_parquet(PATH)"),
    "polars.scan_parquet().collect()": (
        "parquet",
        "import polars as pl; pl.scan_parquet(PATH).collect()",
    ),
    "numpy.load": ("npy", "import numpy as np; np.load(PATH)"),
    "numpy.fromfile": ("raw", "import numpy as np; np.fromfile(PATH, dtype='int64')"),
    "numpy.loadtxt": (
        "csv",
        "import numpy as np; np.loadtxt(PATH, delimiter=',', skiprows=1)",
    ),
}


def reader_cases(files: dict[str, Path]) -> list[dict[str, object]]:
    rows = []
    for name, (key, code) in READERS.items():
        if key not in files:
            rows.append({"case": name, "note": "library not installed"})
            continue
        path = files[key]
        body = code.replace("PATH", repr(str(path)))
        seen = run_case(body, [path.name])
        blocked = run_case(body, [path.name], block=True)
        if not seen["ok"] and "No module named" in str(seen.get("error")):
            rows.append({"case": name, "note": "library not installed"})
            continue
        events = sorted({event for event, _ in seen["seen"]})  # type: ignore[attr-defined]
        rows.append(
            {
                "case": name,
                "seen": bool(seen["seen"]),
                "events": ", ".join(events) or "-",
                "blocked": not blocked["ok"],
            }
        )
    return rows


MODULE_CASES: dict[str, str] = {
    "plain import": "import untracked_mod",
    "import, then del sys.modules": (
        "import sys, untracked_mod; del sys.modules['untracked_mod']"
    ),
    "importlib spec_from_file_location (not in sys.modules)": """
        import importlib.util
        spec = importlib.util.spec_from_file_location('x', 'untracked_mod.py')
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    """,
    "exec(open(file).read())": "exec(open('untracked_mod.py').read())",
    "runpy.run_path": "import runpy; runpy.run_path('untracked_mod.py')",
    "import from cached .pyc only (source deleted)": "import cached_only",
}


def module_cases(repo: Path) -> list[dict[str, object]]:
    (repo / "untracked_mod.py").write_text("VALUE = 1\n")
    (repo / "cached_only.py").write_text("VALUE = 2\n")
    # Build cached_only's .pyc, then delete the source: a sourceless import.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import compileall, sys; "
            "compileall.compile_file('cached_only.py', legacy=True, quiet=1)",
        ],
        cwd=repo,
        check=True,
    )
    (repo / "cached_only.py").unlink()
    rows = []
    for name, code in MODULE_CASES.items():
        result = run_case(
            "import sys; sys.path.insert(0, '.')\n" + textwrap.dedent(code),
            ["untracked_mod", "cached_only"],
            repo=repo,
        )
        events = sorted({event for event, _ in result["seen"]})  # type: ignore[attr-defined]
        rows.append(
            {
                "case": name,
                "ok": result["ok"],
                "scan": bool(result["scan"]),
                "events": ", ".join(events) or "-",
            }
        )
    return rows


WORKER = textwrap.dedent(
    """
    import json, multiprocessing as mp, os, sys

    def work(p):
        return open(p).read()

    if __name__ == '__main__':
        target = sys.argv[1]
        method = sys.argv[2]
        block = sys.argv[3] == "1"
        seen = []
        def hook(event, args):
            if event == "open" and args and isinstance(args[0], str) \\
                    and args[0] == target:
                seen.append(os.getpid())
                if block:
                    raise PermissionError("blocked")
        sys.addaudithook(hook)  # installed by the parent only, as BGL would
        ctx = mp.get_context(method)
        try:
            with ctx.Pool(1) as pool:
                pool.map(work, [target])
            ok = True
        except Exception:
            ok = False
        print("SPIKE_RESULT " + json.dumps({"parent_saw": bool(seen), "read_ok": ok}))
    """
)


def worker_cases(files: dict[str, Path]) -> list[dict[str, object]]:
    import multiprocessing
    import os

    script = Path(tempfile.mkdtemp()) / "worker.py"
    script.write_text(WORKER)
    rows = []
    for method in multiprocessing.get_all_start_methods():
        for block in (False, True):
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(files["txt"]),
                    method,
                    "1" if block else "0",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                env=dict(os.environ),
            )
            record = {"method": method, "block": block}
            for line in result.stdout.splitlines():
                if line.startswith("SPIKE_RESULT "):
                    record.update(json.loads(line[len("SPIKE_RESULT ") :]))
            rows.append(record)
    return rows


def main() -> None:
    print(f"Python {sys.version.split()[0]} on {sys.platform}\n")
    # The sqlite case leaves a connection open in this process; on Windows the
    # file stays locked, so cleanup errors must not hide the printed results.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        folder = Path(tmp)
        files = make_data(folder)
        print("## File reads\n")
        print("| Reader | Hook sees it | Events | Hook can block it |")
        print("| --- | --- | --- | --- |")
        for row in reader_cases(files):
            if "note" in row:
                print(f"| {row['case']} | {row['note']} | | |")
            else:
                print(
                    f"| {row['case']} | {'yes' if row['seen'] else 'NO'} | "
                    f"{row['events']} | {'yes' if row['blocked'] else 'NO'} |"
                )
        repo = folder / "repo"
        repo.mkdir()
        print("\n## Untracked code\n")
        print(
            "| How the code is loaded | Module scan finds it | Hook events naming it |"
        )
        print("| --- | --- | --- |")
        for row in module_cases(repo):
            scan = "yes" if row["scan"] else "NO"
            print(f"| {row['case']} | {scan} | {row['events']} |")
        print("\n## Worker processes (hook installed in the parent only)\n")
        print(
            "| Start method | Hook blocks reads | Parent's hook saw the read "
            "| Read succeeded |"
        )
        print("| --- | --- | --- | --- |")
        for row in worker_cases(files):
            print(
                f"| {row['method']} | {'yes' if row['block'] else 'no'} | "
                f"{'yes' if row.get('parent_saw') else 'NO'} | "
                f"{'yes' if row.get('read_ok') else 'no'} |"
            )


if __name__ == "__main__":
    main()
