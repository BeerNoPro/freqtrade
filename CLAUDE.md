# CLAUDE.md — Bot tín hiệu Futures (dựa trên Freqtrade 2026.9)

Trả lời người dùng bằng **tiếng Việt**. Commit message bằng **tiếng Anh**.

## Dự án
Bot nội bộ **tính toán và phát tín hiệu** cho Binance USDT-M Futures, gửi qua bot Telegram (@BotFather), chạy dry-run 24/7 trên VPS. Giai đoạn 2 (sau này): bot tự vào lệnh bằng `dry_run: false`.

Engine là Freqtrade 2026.9 (GPLv3, giữ `LICENSE` + `NOTICE`). Logic riêng nằm trong `user_data/` (strategy, config). Core `freqtrade/` chỉ sửa trong các việc cắt gọn có kế hoạch.

## Tài liệu (đọc trước khi làm)
| File | Nội dung |
|---|---|
| [docs/signal-bot-workflow.md](docs/signal-bot-workflow.md) | **Thiết kế bot**: workflow, công thức, tin nhắn, ngưỡng kiểm chứng, lộ trình |
| [docs/lifecycle.md](docs/lifecycle.md) | Logic chạy của engine (đã đối chiếu code) |
| [docs/structure.md](docs/structure.md) | Bản đồ source, luồng → file |
| [freqtrade/strategy/CLAUDE.md](freqtrade/strategy/CLAUDE.md), [freqtrade/optimize/CLAUDE.md](freqtrade/optimize/CLAUDE.md) | Ghi chú theo module |

## Luật bắt buộc
@.claude/rules/git-workflow.md
@.claude/rules/trading-safety.md
@.claude/rules/quant-strategy.md

## Skill dự án (gọi bằng `/tên`)
| Skill | Khi nào dùng |
|---|---|
| `/commit` | Commit trên `develop` theo đúng luật: lint, test, quét secret, message tiếng Anh. Không push |
| `/validate-strategy <Strategy>` | Tải dữ liệu thiếu → lookahead → recursive → backtest → so với ngưỡng đạt |
| `/trim-core <module>` | Cắt bớt một phần source upstream an toàn: phân tích phụ thuộc → xóa → kiểm chứng → ghi log |

## Môi trường & lệnh
- Windows, Python 3.13 trong `.venv` (quản lý bằng **uv**, không có pip): `uv pip install --python .venv/Scripts/python.exe ...`
- Chạy CLI: `.venv/Scripts/freqtrade.exe <lệnh>`; launcher: `.\run.ps1`
- Config: `user_data/config.json` (spot, gitignore) và `user_data/config_futures.json` (futures, kế thừa qua `add_config_files`)
```bash
.venv/Scripts/freqtrade.exe download-data --config user_data/config_futures.json --timeframes 1h 4h --timerange 20221101-
.venv/Scripts/freqtrade.exe backtesting --config user_data/config_futures.json --strategy <S> --fee 0.0006 --enable-protections --breakdown month
.venv/Scripts/ruff.exe check <file> && .venv/Scripts/ruff.exe format <file>
.venv/Scripts/python.exe -m pytest tests/<thư mục> -q
```
- Lint: ruff (line-length 100, max-complexity 12); type check: mypy; test: pytest.
