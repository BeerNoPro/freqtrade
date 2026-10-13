"""Outbox delivery and the send-only Telegram client (fake HTTP, no network)."""

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from mce import outbox
from mce.outbox import SendError, TelegramSender


T0 = pd.Timestamp("2026-10-13 16:01", tz="UTC")
TOKEN = "123456:SECRET-test-token"  # gate: allow-secret


class FakeSender:
    def __init__(self, fail: int = 0) -> None:
        self.sent: list[str] = []
        self.fail = fail  # number of calls that fail first

    def send(self, text: str) -> None:
        if self.fail:
            self.fail -= 1
            raise SendError("network down")
        self.sent.append(text)


def _box(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "product.sqlite")
    outbox.init(conn)
    return conn


def _states(conn) -> dict[str, str]:
    return outbox.rows(conn).set_index("key")["state"].to_dict()


def test_flush_sends_pending_in_order_and_leaves_local_ones(tmp_path: Path) -> None:
    conn = _box(tmp_path)
    outbox.enqueue(conn, "a", "setup", "first", True, T0)
    outbox.enqueue(conn, "b", "setup_info", "log only", False, T0)
    outbox.enqueue(conn, "c", "close", "second", True, T0 + pd.Timedelta(seconds=1))
    sender, naps = FakeSender(), []
    done = outbox.flush(conn, sender, T0 + pd.Timedelta(minutes=1), sleep=naps.append)
    assert done == {"sent": 2, "expired": 0, "failed": 0}
    assert sender.sent == ["first", "second"] and naps == [1.0]
    assert _states(conn) == {"a": "sent", "b": "local", "c": "sent"}
    assert outbox.flush(conn, sender, T0 + pd.Timedelta(minutes=2))["sent"] == 0


def test_flush_stops_at_the_first_failure_and_retries_in_order(tmp_path: Path) -> None:
    conn = _box(tmp_path)
    outbox.enqueue(conn, "a", "setup", "first", True, T0)
    outbox.enqueue(conn, "b", "setup", "second", True, T0 + pd.Timedelta(seconds=1))
    sender = FakeSender(fail=1)
    done = outbox.flush(conn, sender, T0, sleep=lambda s: None)
    assert done == {"sent": 0, "expired": 0, "failed": 1} and sender.sent == []
    row = outbox.rows(conn).set_index("key").loc["a"]
    assert (
        row["state"] == "pending" and row["attempts"] == 1 and row["last_error"] == "network down"
    )
    assert outbox.flush(conn, sender, T0, sleep=lambda s: None)["sent"] == 2
    assert sender.sent == ["first", "second"]


def test_messages_waiting_too_long_expire(tmp_path: Path) -> None:
    conn = _box(tmp_path)
    outbox.enqueue(conn, "old", "setup", "stale", True, T0)
    outbox.enqueue(conn, "new", "setup", "fresh", True, T0 + pd.Timedelta(hours=6))
    sender = FakeSender()
    done = outbox.flush(conn, sender, T0 + pd.Timedelta(hours=6, minutes=1), sleep=lambda s: None)
    assert done == {"sent": 1, "expired": 1, "failed": 0} and sender.sent == ["fresh"]
    assert _states(conn) == {"old": "expired", "new": "sent"}


def test_long_messages_are_split_at_line_ends() -> None:
    text = "\n".join(f"line {i:04d} " + "x" * 90 for i in range(100))  # ~10 000 characters
    parts = outbox.split_text(text, limit=4000)
    assert len(parts) == 3 and all(len(p) <= 4000 for p in parts)
    assert "\n".join(parts) == text
    assert [len(p) for p in outbox.split_text("y" * 9000, limit=4000)] == [4000, 4000, 1000]
    assert outbox.split_text("short") == ["short"]


class Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code, self._body = status, body

    def json(self) -> dict:
        return self._body


def test_sender_posts_send_message_only_and_hides_the_token() -> None:
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return Response(200, {"ok": True})

    sender = TelegramSender(TOKEN, "42", post=post)
    sender.send("xin chào")
    url, body, timeout = calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage" and timeout == 15
    assert body == {"chat_id": "42", "text": "xin chào", "disable_web_page_preview": True}
    assert TOKEN not in repr(sender)

    def broken(url, json, timeout):
        raise ConnectionError(f"Max retries exceeded with url: {url}")

    with pytest.raises(SendError) as err:
        TelegramSender(TOKEN, "42", post=broken).send("x")
    assert TOKEN not in str(err.value) and "***" in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__

    def refused(url, json, timeout):
        return Response(401, {"ok": False, "description": "Unauthorized"})

    with pytest.raises(SendError, match="HTTP 401: Unauthorized"):
        TelegramSender(TOKEN, "42", post=refused).send("x")


def test_credentials_from_the_config_or_the_environment(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"telegram": {"enabled": False, "token": TOKEN, "chat_id": "42"}}),
        encoding="utf-8",
    )
    assert outbox.credentials(config, env={}) == (TOKEN, "42")
    env = {"FREQTRADE__TELEGRAM__TOKEN": "9:other", "FREQTRADE__TELEGRAM__CHAT_ID": "7"}
    assert outbox.credentials(config, env=env) == ("9:other", "7")
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"telegram": {"enabled": False, "token": "", "chat_id": ""}}))
    assert outbox.credentials(empty, env={}) is None
    assert outbox.credentials(tmp_path / "missing.json", env={}) is None
