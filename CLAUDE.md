# CLAUDE.md — Freqtrade

Hướng dẫn ngữ cảnh cho agent khi làm việc trong repo này. Mục tiêu của chủ dự án: **phát triển bot trade crypto** dựa trên Freqtrade.

## Dự án là gì

Freqtrade là **framework bot giao dịch crypto** mã nguồn mở (Python 3.11+, GPLv3), hỗ trợ Spot và Futures (đòn bẩy) qua thư viện `ccxt`. Điều khiển qua Telegram / WebUI / REST API. Lưu trạng thái bằng SQLite (SQLAlchemy 2.x). Có backtesting, hyperopt (tối ưu tham số), và FreqAI (ML).

Đây là bản upstream chuẩn trên nhánh `develop`. Logic giao dịch riêng của người dùng sẽ nằm ở **`user_data/`** (strategies, config), KHÔNG sửa core trong `freqtrade/` trừ khi thực sự cần.

## Kiến trúc & luồng chạy

Entry point: [freqtrade/main.py](freqtrade/main.py) → parse CLI subcommand (`trade`, `backtesting`, `hyperopt`, `download-data`...) tại [freqtrade/commands/](freqtrade/commands/).

Với `trade`: [Worker](freqtrade/worker.py) chạy vòng lặp `_throttle` (canh đầu nến mới theo timeframe) → gọi [`FreqtradeBot.process()`](freqtrade/freqtradebot.py) mỗi vòng. Thứ tự trong `process()`:

1. `reload_markets()` + cập nhật phí lệnh
2. Lấy open trades từ DB → refresh whitelist (pairlist) → tải nến OHLCV (`dataprovider.refresh`)
3. `strategy.analyze()` — chạy indicators + sinh tín hiệu vào/ra
4. `manage_open_orders()` — hủy lệnh treo/timeout
5. `exit_positions()` — xử lý thoát lệnh
6. `process_open_trade_positions()` — DCA / điều chỉnh vị thế (nếu bật)
7. `enter_positions()` — vào lệnh mới nếu còn slot (`max_open_trades`)

State machine: `RUNNING / PAUSED / STOPPED / RELOAD_CONFIG`.

## Bản đồ thư mục `freqtrade/`

| Thư mục | Vai trò |
|---------|---------|
| `freqtradebot.py` | **Lõi**: vòng đời trade live/dry-run (file ~110KB) |
| `worker.py` / `main.py` | Vòng lặp throttle + entry point CLI |
| `strategy/` | Lớp `IStrategy` — **nơi viết logic giao dịch**. Xem [freqtrade/strategy/CLAUDE.md](freqtrade/strategy/CLAUDE.md) |
| `optimize/` | Backtesting + Hyperopt + báo cáo. Xem [freqtrade/optimize/CLAUDE.md](freqtrade/optimize/CLAUDE.md) |
| `exchange/` | Wrapper ccxt cho từng sàn (binance, bybit, okx...) |
| `freqai/` | Module ML dự đoán thị trường (tùy chọn) |
| `plugins/` | `pairlist/` (chọn coin động) + `protections/` (bảo vệ vốn) |
| `rpc/` | Telegram, WebUI, REST API (`api_server/`), webhook, discord |
| `persistence/` | Model `Trade` / `Order` / DB (SQLAlchemy) |
| `data/` | Tải & quản lý dữ liệu OHLCV, dataprovider |
| `configuration/` | Load + validate config JSON, biến môi trường |
| `resolvers/` | Nạp động strategy / pairlist / protection theo tên |
| `wallets.py` | Quản lý số dư / vốn khả dụng |
| `templates/` | Strategy & config mẫu (`sample_strategy.py`, `base_config.json.j2`) |

## Lệnh phát triển thường dùng

```bash
# Chạy bot (mặc định dry-run nếu config dry_run=true)
freqtrade trade --config user_data/config.json --strategy MyStrategy

# Tạo strategy / config mẫu
freqtrade new-strategy --strategy MyStrategy
freqtrade new-config --config user_data/config.json

# Tải dữ liệu lịch sử để backtest
freqtrade download-data --pairs BTC/USDT --timeframe 5m --days 90

# Backtest & tối ưu
freqtrade backtesting --strategy MyStrategy --timeframe 5m
freqtrade hyperopt --strategy MyStrategy --hyperopt-loss SharpeHyperOptLoss

# Chất lượng code (chạy trước khi commit)
ruff check freqtrade
ruff format freqtrade
mypy freqtrade
pytest                      # hoặc: pytest tests/strategy -q
```

Test framework: pytest (asyncio_mode=auto), config trong [pyproject.toml](pyproject.toml). Lint: **ruff** (line-length 100, max-complexity 12). Type check: mypy.

## Quy ước quan trọng

- **An toàn trước tiên**: luôn để `dry_run: true` khi thử nghiệm. Không bao giờ commit API key/secret — dùng biến môi trường hoặc config riêng (xem `configuration/config_secrets.py`).
- Khi phát triển chiến lược: đặt file trong `user_data/strategies/`, KHÔNG sửa `freqtrade/templates/`.
- Tuân thủ ruff/mypy trước khi commit; CI sẽ chặn nếu fail.
- Code style: theo đúng phong cách file xung quanh; type hints bắt buộc ở core.
- Tài liệu chính thức: https://www.freqtrade.io
