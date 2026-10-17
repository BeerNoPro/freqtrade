"""Deploy preflight: a new checkout is refused when a rule fingerprint changed, a private file
is missing or Telegram is not configured."""

from pathlib import Path

import deploy_preflight as pf


def _research(tmp_path: Path, name: str, fp: str, registered: str) -> Path:
    research = tmp_path / "research"
    (research / "pit").mkdir(parents=True)
    for f in ("product.json", "thresholds.json", "fingerprints.jsonl", "pit/symbols.json"):
        (research / f).write_text("{}", encoding="utf-8")
    (research / f"{name}.py").write_text(
        f"def fingerprint():\n    return {fp!r}\n\n"
        f"def registered_fingerprint():\n    return {registered!r}\n",
        encoding="utf-8",
    )
    return research


def _env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_pass_when_everything_is_in_place(tmp_path: Path, capsys) -> None:
    research = _research(tmp_path, "spec_pf_ok", "abc", "abc")
    env = _env(tmp_path, "TELEGRAM_BOT_TOKEN=123:secret\nTELEGRAM_ADMIN_CHAT_IDS=42\n")
    code = pf.main(["--research", str(research), "--env-file", str(env), "--specs", "spec_pf_ok"])
    out = capsys.readouterr().out
    assert code == 0 and out.strip().endswith("preflight: pass")
    assert "secret" not in out  # the token never reaches the (public) deploy log


def test_fail_on_a_changed_fingerprint_a_missing_rule_or_file(tmp_path: Path, capsys) -> None:
    research = _research(tmp_path, "spec_pf_changed", "new", "old")
    (research / "product.json").unlink()
    env = _env(tmp_path, "TELEGRAM_BOT_TOKEN=1\nTELEGRAM_CHAT_ID=2\n")
    specs = "spec_pf_changed,spec_pf_absent"
    code = pf.main(["--research", str(research), "--env-file", str(env), "--specs", specs])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL spec_pf_changed: fingerprint new, registered old" in out
    assert "FAIL spec_pf_absent: rule file missing" in out
    assert "FAIL product.json" in out and "preflight: FAIL" in out


def test_fail_without_telegram(tmp_path: Path) -> None:
    research = _research(tmp_path, "spec_pf_tg", "x", "x")
    rows = pf.check_telegram(_env(tmp_path, "OTHER=1\n"))
    assert rows == [("telegram", False, f"no token / chat id in {tmp_path / '.env'}")]
    assert pf.check_telegram(tmp_path / "missing.env")[0][1] is False
    assert all(ok for _, ok, _ in pf.check_specs(research, "spec_pf_tg"))
