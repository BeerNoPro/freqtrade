import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_encoding.py"
_spec = importlib.util.spec_from_file_location("check_encoding", SCRIPT)
check_encoding = importlib.util.module_from_spec(_spec)
sys.modules["check_encoding"] = check_encoding  # required by @dataclass during exec
_spec.loader.exec_module(check_encoding)

BOM = b"\xef\xbb\xbf"


@pytest.mark.parametrize(
    "path,data,expected",
    [
        ("a.py", b"x = 1\n", []),
        ("a.py", b"", []),
        ("a.py", b"x = 1\r\ny = 2\r\n", ["CRLF line endings (2 lines, expected LF)"]),
        ("a.py", b"x = 1", ["no newline at end of file"]),
        ("a.py", BOM + b"x = 1\n", ["has UTF-8 BOM (expected none)"]),
        ("a.py", b"x = 1\ry = 2\n", ["contains bare CR line endings"]),
        ("a.md", "tiếng Việt\n".encode(), []),
        ("run.ps1", BOM + b"a\r\nb\r\n", []),
        ("run.ps1", b"a\r\n", ["missing UTF-8 BOM"]),
        ("run.ps1", BOM + b"a\nb\n", ["LF line endings (2 lines, expected CRLF)"]),
        ("x.bat", b"a\r\n", []),
        ("tests/testdata/x.json", b"{}", []),
    ],
)
def test_check_bytes(path, data, expected):
    assert check_encoding.check_bytes(path, data) == expected


def test_check_bytes_invalid_utf8():
    problems = check_encoding.check_bytes("a.txt", b"abc\xff\n")
    assert len(problems) == 1
    assert problems[0].startswith("not valid UTF-8")


@pytest.mark.parametrize(
    "path,data,expected",
    [
        ("a.py", b"x = 1\r\ny = 2", b"x = 1\ny = 2\n"),
        ("a.py", BOM + b"x\r\n", b"x\n"),
        ("a.py", b"a\rb\n", b"a\nb\n"),
        ("run.ps1", b"a\nb", BOM + b"a\r\nb\r\n"),
        ("run.ps1", BOM + b"a\r\nb\n", BOM + b"a\r\nb\r\n"),
        ("x.cmd", b"a\n", b"a\r\n"),
        ("tests/testdata/x.json", b"{}\r\n{}", b"{}\n{}"),
    ],
)
def test_fix_bytes(path, data, expected):
    fixed = check_encoding.fix_bytes(path, data)
    assert fixed == expected
    assert check_encoding.check_bytes(path, fixed) == []


@pytest.mark.parametrize(
    "path,data,binary",
    [
        ("data.feather", b"abc", True),
        ("img.png", b"abc", True),
        ("blob.txt", b"ab\x00c", True),
        ("code.py", b"print(1)\n", False),
    ],
)
def test_is_binary(path, data, binary):
    assert check_encoding.is_binary(path, data) is binary


def test_main_fix_explicit_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check_encoding, "ROOT", tmp_path)
    (tmp_path / "a.py").write_bytes(b"x = 1\r\n")
    (tmp_path / "b.ps1").write_bytes(b"Write-Host 'hi'\n")
    (tmp_path / "ok.py").write_bytes(b"y = 2\n")

    assert check_encoding.main(["a.py", "b.ps1", "ok.py"]) == 1
    assert "CRLF line endings" in capsys.readouterr().out

    assert check_encoding.main(["--fix", "a.py", "b.ps1", "ok.py"]) == 0
    assert (tmp_path / "a.py").read_bytes() == b"x = 1\n"
    assert (tmp_path / "b.ps1").read_bytes() == BOM + b"Write-Host 'hi'\r\n"
    assert check_encoding.main(["a.py", "b.ps1", "ok.py"]) == 0


def test_main_fix_skips_invalid_utf8(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check_encoding, "ROOT", tmp_path)
    (tmp_path / "bad.txt").write_bytes(b"abc\xff\n")
    assert check_encoding.main(["--fix", "bad.txt"]) == 1
    assert (tmp_path / "bad.txt").read_bytes() == b"abc\xff\n"
    assert "not valid UTF-8" in capsys.readouterr().out
