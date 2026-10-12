# Bot tín hiệu Futures (Binance)

Bot nội bộ **tính toán và phát tín hiệu** giao dịch cho Binance USDT-M Futures, gửi qua Telegram.
Chạy 24/7 trên VPS ở chế độ dry-run (không đặt lệnh thật). Giai đoạn sau có thể bật tự vào lệnh.

Engine dựa trên [Freqtrade](https://github.com/freqtrade/freqtrade) 2026.9 (GPLv3) — xem [NOTICE](NOTICE) và [LICENSE](LICENSE).

## Tài liệu

| File | Nội dung |
|---|---|
| [docs/signal-bot-workflow.md](docs/signal-bot-workflow.md) | Thiết kế bot: quyết định đã chốt, workflow, tin nhắn Telegram, ánh xạ Freqtrade, lộ trình |
| [docs/market-context-engine.md](docs/market-context-engine.md) | Bộ máy nghiên cứu: kho dữ liệu, kiểm định luật, kiểm định tiến, bộ ghi |
| [docs/quant-playbook.md](docs/quant-playbook.md) | Các họ mô hình có bằng chứng và bài học đã kiểm |
| [docs/lifecycle.md](docs/lifecycle.md) | Logic chạy chi tiết (đã đối chiếu với code) |
| [docs/structure.md](docs/structure.md) | Bản đồ source code |
| [docs/trim-log.md](docs/trim-log.md) | Nhật ký các phần đã cắt khỏi Freqtrade gốc |

## Chạy ở máy local (Windows)

Môi trường: Python 3.13 trong `.venv` (quản lý bằng [uv](https://docs.astral.sh/uv/)), TA-Lib và các thư viện đã cài,
database SQLite tự tạo. Mọi lệnh gọi qua `.venv\Scripts\freqtrade.exe`, hoặc dùng launcher `.\run.ps1`.

```powershell
# Cài / cập nhật thư viện
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt -e .   # requirements-dev.txt đã gồm requirements.txt

# Kiểm tra cài đặt
.venv\Scripts\freqtrade.exe --version

# Tải dữ liệu futures (công khai, không cần API key)
.venv\Scripts\freqtrade.exe download-data --config user_data\config_futures.json --timeframes 1h 4h --timerange 20240101-

# Backtest
.venv\Scripts\freqtrade.exe backtesting --config user_data\config_futures.json --strategy <Strategy> `
  --fee 0.0006 --enable-protections --breakdown month

# Chạy bot dry-run (phát tín hiệu)
.venv\Scripts\freqtrade.exe trade --config user_data\config_futures.json --strategy <Strategy>

# WebUI tại http://127.0.0.1:8080 (mật khẩu trong mục api_server của config)
.venv\Scripts\freqtrade.exe webserver --config user_data\config_futures.json
```

Telegram: tạo bot bằng @BotFather, điền `token` và `chat_id` vào config (xem mục 4 trong [docs/signal-bot-workflow.md](docs/signal-bot-workflow.md)).

## Nghiên cứu và kiểm định tiến

```powershell
# Kho dữ liệu point-in-time (nến, funding, OI của mọi perp, kể cả đã delist)
.venv\Scripts\python.exe scripts\research\download_pit_universe.py
# Runner kiểm định tiến và bộ ghi dữ liệu sàn chạy 24/7 (task Windows); VPS: docker compose up -d --build recorder forward
powershell -ExecutionPolicy Bypass -File scripts\research\schedule_forward.ps1
powershell -ExecutionPolicy Bypass -File scripts\research\schedule_recorder.ps1
# Tình trạng: nhật ký setup, độ phủ bộ ghi
.venv\Scripts\python.exe scripts\research\run_forward.py --status
.venv\Scripts\python.exe scripts\research\recorder_coverage.py
```
Chi tiết: [docs/market-context-engine.md](docs/market-context-engine.md).

## Kiểm tra chất lượng code

```powershell
.venv\Scripts\python.exe scripts\format_code.py           # trước khi commit: ruff fix + format + encoding
.venv\Scripts\python.exe scripts\check_encoding.py --all  # kiểm tra encoding / xuống dòng toàn repo
.venv\Scripts\mypy.exe freqtrade
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\pre-commit.exe install -t pre-commit -t pre-push   # cài git hook (một lần mỗi máy)
```

Chuẩn file text: UTF-8 không BOM, xuống dòng LF; riêng `*.ps1` dùng UTF-8 có BOM + CRLF.

## An toàn

- Luôn để `"dry_run": true` khi thử nghiệm.
- Không commit API key, token Telegram, mật khẩu. `user_data/` đã được gitignore.
- Repo là fork công khai: mọi thứ được push đều công khai.

## Miễn trừ trách nhiệm

Phần mềm dùng cho mục đích nội bộ và học tập. Tín hiệu không phải lời khuyên đầu tư.
Giao dịch futures có đòn bẩy có thể mất toàn bộ vốn. Chỉ dùng số tiền bạn chấp nhận mất.
