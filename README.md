# Bot tín hiệu Futures (Binance)

Bot nội bộ **tính toán và phát tín hiệu** giao dịch cho Binance USDT-M Futures, gửi qua Telegram.
Chạy 24/7 trên VPS ở chế độ dry-run (không đặt lệnh thật). Giai đoạn sau có thể bật tự vào lệnh.

Engine dựa trên [Freqtrade](https://github.com/freqtrade/freqtrade) 2026.9 (GPLv3) — xem [NOTICE](NOTICE) và [LICENSE](LICENSE).

## Tài liệu

| File | Nội dung |
|---|---|
| [docs/signal-bot-workflow.md](docs/signal-bot-workflow.md) | Thiết kế bot: workflow, công thức tín hiệu, tin nhắn Telegram, kiểm chứng, lộ trình |
| [docs/overview.md](docs/overview.md) | Tổng quan engine và các chế độ chạy |
| [docs/lifecycle.md](docs/lifecycle.md) | Logic chạy chi tiết (đã đối chiếu với code) |
| [docs/structure.md](docs/structure.md) | Bản đồ source code |

## Chạy ở máy local (Windows)

Môi trường: Python 3.13 trong `.venv` (quản lý bằng [uv](https://docs.astral.sh/uv/)), TA-Lib và các thư viện đã cài,
database SQLite tự tạo. Mọi lệnh gọi qua `.venv\Scripts\freqtrade.exe`, hoặc dùng launcher `.\run.ps1`.

```powershell
# Cài / cập nhật thư viện
uv pip install --python .venv\Scripts\python.exe -r requirements.txt -r requirements-hyperopt.txt -r requirements-plot.txt -e .

# Kiểm tra cài đặt
.venv\Scripts\freqtrade.exe --version

# Tải dữ liệu futures (công khai, không cần API key)
.venv\Scripts\freqtrade.exe download-data --config user_data\config_futures.json --timeframes 1h 4h --timerange 20221101-

# Backtest
.venv\Scripts\freqtrade.exe backtesting --config user_data\config_futures.json --strategy <Strategy> `
  --fee 0.0006 --enable-protections --breakdown month

# Chạy bot dry-run (phát tín hiệu)
.venv\Scripts\freqtrade.exe trade --config user_data\config_futures.json --strategy <Strategy>

# WebUI tại http://127.0.0.1:8080 (mật khẩu trong mục api_server của config)
.venv\Scripts\freqtrade.exe webserver --config user_data\config_futures.json
```

Telegram: tạo bot bằng @BotFather, điền `token` và `chat_id` vào config (xem mục 8 trong [docs/signal-bot-workflow.md](docs/signal-bot-workflow.md)).

## Kiểm tra chất lượng code

```powershell
.venv\Scripts\ruff.exe check freqtrade tests
.venv\Scripts\ruff.exe format freqtrade tests
.venv\Scripts\mypy.exe freqtrade
.venv\Scripts\python.exe -m pytest tests -q
```

## An toàn

- Luôn để `"dry_run": true` khi thử nghiệm.
- Không commit API key, token Telegram, mật khẩu. `user_data/` đã được gitignore.
- Repo là fork công khai: mọi thứ được push đều công khai.

## Miễn trừ trách nhiệm

Phần mềm dùng cho mục đích nội bộ và học tập. Tín hiệu không phải lời khuyên đầu tư.
Giao dịch futures có đòn bẩy có thể mất toàn bộ vốn. Chỉ dùng số tiền bạn chấp nhận mất.
