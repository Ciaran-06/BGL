# Audit-hook spike (build step 5)

**Question.** What can `sys.addaudithook` see and block during a run, and what
does the loaded-module scan catch? R8, R17, R20 and R25 depend on the answer.

**How.** `spikes/audit_hook/spike.py` runs each case in a fresh interpreter with
a hook installed, then reports what the hook saw and whether raising from it
stopped the read. Re-run it on any machine with:

    uv run python spikes/audit_hook/spike.py

## Results (Linux, pyarrow 26.0.1, polars 2.1.0, pandas 3.0.5, numpy 2.5.3)

Python 3.13.18 on linux

## File reads

| Reader | Hook sees it | Events | Hook can block it |
| --- | --- | --- | --- |
| open() | yes | open | yes |
| pathlib read_text | yes | open | yes |
| os.open | yes | open | yes |
| csv module | yes | open | yes |
| mmap | yes | open | yes |
| sqlite3.connect | yes | sqlite3.connect | yes |
| pandas.read_csv (C engine) | yes | open | yes |
| pandas.read_csv (pyarrow engine) | yes | open | yes |
| pandas.read_parquet | yes | open | yes |
| pyarrow.parquet.read_table | NO | - | NO |
| pyarrow.feather.read_table | NO | - | NO |
| pyarrow.csv.read_csv | NO | - | NO |
| polars.read_csv | NO | - | NO |
| polars.read_parquet | NO | - | NO |
| polars.scan_parquet().collect() | NO | - | NO |
| numpy.load | yes | open | yes |
| numpy.fromfile | yes | open | yes |
| numpy.loadtxt | yes | open | yes |

## Untracked code

| How the code is loaded | Module scan finds it | Hook events naming it |
| --- | --- | --- |
| plain import | yes | exec(co_filename), open |
| import, then del sys.modules | NO | exec(co_filename), open |
| importlib spec_from_file_location (not in sys.modules) | NO | exec(co_filename), open |
| exec(open(file).read()) | NO | open |
| runpy.run_path | NO | exec(co_filename), open |
| import from cached .pyc only (source deleted) | yes | exec(co_filename), open |

## Worker processes (hook installed in the parent only)

| Start method | Hook blocks reads | Parent's hook saw the read | Read succeeded |
| --- | --- | --- | --- |
| fork | no | NO | yes |
| fork | yes | NO | no |
| spawn | no | NO | yes |
| spawn | yes | NO | yes |
| forkserver | no | NO | yes |
| forkserver | yes | NO | yes |

## Findings

1. **Reads made through Python are seen and can be blocked.** That covers
   `open`, `pathlib`, `os.open`, `mmap`, the `csv` module, numpy, sqlite (via
   its own `sqlite3.connect` event) and pandas, including `read_parquet` and
   the pyarrow CSV engine, because pandas opens the file itself before handing
   it on. That pandas behaviour belongs to this pandas version, not to a
   guarantee.
2. **Native readers are invisible.** pyarrow's own readers (parquet, feather,
   CSV) and every polars reader open files in C++ or Rust. The hook sees
   nothing and cannot block them. This is not a bug to work around: audit
   hooks only see what goes through the Python runtime.
3. **The module scan misses most ways of running untracked code.** It finds a
   plain import and nothing else: deleting the module from `sys.modules`,
   loading it with `importlib` without registering it, `exec` of its source
   and `runpy.run_path` all escape it.
4. **The hook catches every one of those.** Running a module body goes through
   an `exec` event whose code object names the file (`co_filename`), and
   reading the source or cached `.pyc` goes through an `open` event.
   `exec(open(path).read())` shows only the `open`, because the code object is
   named `<string>`.
5. **Worker processes are outside the parent's hook.** With `spawn` and
   `forkserver` (the defaults on Windows, macOS and, from Python 3.14, Linux)
   the parent sees nothing and blocks nothing. With `fork` the copied hook
   still blocks in the child, but the parent never hears about the read.

## Still to confirm

These results are from Linux. Audit hooks are part of the interpreter, so the
same split is expected on Windows and macOS, but run the script there before
the spec wording is final.