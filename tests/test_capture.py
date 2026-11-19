import sys
from pathlib import Path

from bgl.capture import OutputCapture


def capture(tmp_path: Path) -> OutputCapture:
    return OutputCapture(tmp_path / "out" / "r.stdout", tmp_path / "out" / "r.stderr")


def test_copies_both_streams_and_still_prints(tmp_path: Path, capsys: object) -> None:
    captured = capture(tmp_path)
    captured.start()
    print("hello")
    print("oops", file=sys.stderr)
    captured.stop()
    assert captured.paths["stdout"].read_bytes() == b"hello\n"
    assert captured.paths["stderr"].read_bytes() == b"oops\n"
    out = capsys.readouterr()  # type: ignore[attr-defined]
    assert (out.out, out.err) == ("hello\n", "oops\n")


def test_positions_count_utf8_bytes(tmp_path: Path) -> None:
    captured = capture(tmp_path)
    captured.start()
    try:
        assert captured.positions() == {"stdout": 0, "stderr": 0}
        print("é", end="")
        assert captured.positions() == {"stdout": 2, "stderr": 0}
    finally:
        captured.stop()


def test_streams_are_restored(tmp_path: Path) -> None:
    before = sys.stdout, sys.stderr
    captured = capture(tmp_path)
    captured.start()
    assert sys.stdout is not before[0]
    captured.stop()
    assert (sys.stdout, sys.stderr) == before


def test_nothing_after_stop_is_captured(tmp_path: Path) -> None:
    captured = capture(tmp_path)
    captured.start()
    print("in")
    captured.stop()
    print("out")
    assert captured.paths["stdout"].read_bytes() == b"in\n"


def test_stream_replaced_by_the_script_is_left_alone(tmp_path: Path) -> None:
    captured = capture(tmp_path)
    original = sys.stdout
    captured.start()
    replacement = open(tmp_path / "other.txt", "w")  # noqa: SIM115
    sys.stdout = replacement
    try:
        captured.stop()
        assert sys.stdout is replacement
    finally:
        sys.stdout = original
        replacement.close()
