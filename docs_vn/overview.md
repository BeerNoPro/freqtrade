# Tổng quan dự án (Overview)

> Tài liệu tiếng Việt tóm tắt. Tài liệu gốc đầy đủ: https://www.freqtrade.io

## Freqtrade là gì?

**Freqtrade** là một **framework bot giao dịch crypto** miễn phí, mã nguồn mở (Python 3.11+, giấy phép GPLv3).

- Hỗ trợ giao dịch **Spot** và **Futures** (đòn bẩy) qua thư viện [`ccxt`](https://github.com/ccxt/ccxt) trên hầu hết các sàn lớn (Binance, Bybit, OKX, Kraken, Gate.io...).
- Điều khiển qua **Telegram**, **WebUI (FreqUI)** hoặc **REST API**.
- Lưu trạng thái bằng **SQLite** (qua SQLAlchemy 2.x) — không cần cài database server riêng.
- Bao gồm các công cụ: **backtesting** (kiểm thử trên dữ liệu lịch sử), **hyperopt** (tối ưu tham số), **FreqAI** (machine learning), và **plotting** (vẽ biểu đồ).

## Triết lý sử dụng

```
┌─────────────────────────────────────────────────────────┐
│  freqtrade/   → CORE (upstream chuẩn, KHÔNG sửa)         │
│  user_data/   → CODE CỦA BẠN (strategy, config, data)   │
└─────────────────────────────────────────────────────────┘
```

Logic giao dịch riêng của bạn nằm trong **`user_data/`** (strategies, config). **Không sửa core trong `freqtrade/`** trừ khi thực sự cần — để dễ cập nhật bản upstream sau này.

## Các chế độ chạy (run modes)

| Chế độ | Lệnh | Mục đích | Rủi ro |
|---|---|---|---|
| **Dry-run** | `trade` (với `dry_run: true`) | Chạy bot bằng tiền ảo theo giá thật | Không có (an toàn) |
| **Live** | `trade` (với `dry_run: false`) | Giao dịch tiền thật | ⚠️ Cao — cần API key sàn |
| **Backtesting** | `backtesting` | Mô phỏng chiến lược trên dữ liệu lịch sử | Không có |
| **Hyperopt** | `hyperopt` | Tự động tối ưu tham số chiến lược | Không có (tốn CPU) |
| **Webserver** | `webserver` | Chỉ chạy WebUI + API (xem/backtest) | Không có |

> **An toàn trước tiên**: luôn bắt đầu bằng `dry_run: true`. Không bao giờ commit API key/secret vào source code.

## Quy trình phát triển điển hình

```
1. download-data   → tải dữ liệu OHLCV (public, không cần API key)
2. new-strategy    → tạo chiến lược trong user_data/strategies/
3. backtesting     → kiểm thử chiến lược trên dữ liệu lịch sử
4. hyperopt        → tối ưu tham số (tùy chọn)
5. trade (dry-run) → chạy thử thời gian thực bằng tiền ảo
6. trade (live)    → giao dịch thật (chỉ khi đã tự tin)
```

## Toàn cảnh các hệ thống con (subsystems)

Liệt kê đầy đủ các khối chức năng có sẵn trong source (chi tiết file: [structure.md](structure.md)):

| Hệ thống con | Module | Chức năng |
|---|---|---|
| **Lõi giao dịch** | `freqtradebot.py`, `worker.py`, `wallets.py` | Vòng đời trade live/dry-run, vòng lặp throttle, quản lý vốn |
| **Chiến lược** | `strategy/` (`IStrategy`) | Indicators, tín hiệu vào/ra, ~17 callback tùy biến |
| **Sàn giao dịch** | `exchange/` | Wrapper ccxt + lớp riêng ~20 sàn (spot & futures) |
| **Backtesting** | `optimize/backtesting.py` | Mô phỏng chiến lược trên dữ liệu lịch sử |
| **Hyperopt** | `optimize/hyperopt*.py` | Tối ưu tham số (Optuna) + 14 hàm loss |
| **FreqAI (ML)** | `freqai/` | LightGBM, XGBoost, PyTorch, Reinforcement Learning |
| **Pairlist** | `plugins/pairlist/` | 7 generator + 12 filter chọn coin động/tĩnh |
| **Protections** | `plugins/protections/` | 4 cơ chế bảo vệ vốn |
| **Lưu trữ** | `persistence/` | Model `Trade`/`Order`, DB SQLite, pairlock |
| **Dữ liệu** | `data/` | DataProvider, tải/chuyển đổi OHLCV, metrics |
| **Điều khiển (RPC)** | `rpc/` | Telegram, REST API + WebUI, webhook, discord |
| **Phân tích an toàn** | `optimize/lookahead*`, `recursive*` | Phát hiện look-ahead bias & lỗi đệ quy |
| **Hạ tầng** | `configuration/`, `resolvers/`, `enums/`, `util/`... | Config, nạp động, enum, tiện ích |

> Logic chạy chi tiết từng hệ thống: xem [lifecycle.md](lifecycle.md).

## Yêu cầu hệ thống

- Python >= 3.11, pip, git
- **TA-Lib** (thư viện chỉ báo kỹ thuật, native)
- Khuyến nghị: virtualenv (hoặc Docker)
- Phần cứng tối thiểu: 2GB RAM, 1GB đĩa, 2 vCPU (dry-run/backtest nhẹ; hyperopt/FreqAI nặng hơn)

## Tài liệu liên quan

- [structure.md](structure.md) — Bản đồ cấu trúc source code
- [lifecycle.md](lifecycle.md) — Vòng đời chạy của bot
- [Tài liệu chính thức](https://www.freqtrade.io) — tham khảo đầy đủ
- [bot-basics.md](bot-basics.md), [strategy-101.md](strategy-101.md) — doc gốc tiếng Anh
