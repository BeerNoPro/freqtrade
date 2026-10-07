---
name: trim-core
description: Safely remove an unneeded part of the upstream Freqtrade source (a module, exchange, docs, CI files) — dependency analysis, delete, fix imports, verify the bot still works, and log the change. Use when the user asks to trim/xóa bớt source code.
argument-hint: "<module hoặc đường dẫn, ví dụ freqtrade.freqai | docs | .github>"
---

# /trim-core — cắt gọn source an toàn

Làm và commit trực tiếp trên `develop` (xem `.claude/rules/git-workflow.md`). Một lần chạy = cắt **một** phần = một commit.

## Không bao giờ cắt
`LICENSE`, `NOTICE`, `freqtrade/optimize/` (cần backtest), `freqtrade/exchange/exchange.py`, `freqtrade/exchange/binance*.py`, `freqtrade/rpc/telegram.py`, `freqtrade/rpc/webhook.py`, `freqtrade/persistence/`, `freqtrade/strategy/`, `freqtrade/data/`, `freqtrade/plugins/`, `docs_vn/`, `user_data/`.

## 1. Phân tích
- Code Python: `.venv/Scripts/python.exe .claude/skills/trim-core/find_importers.py <module>`
  - Import cấp module (ngoài phần bị cắt) → phải sửa trước khi xóa, nếu không bot sẽ không khởi động.
  - Import lười (trong hàm) → sửa hoặc chặn nhánh code đó (báo lỗi rõ ràng nếu tính năng bị gọi).
- Phần không phải Python (`docs/`, `.github/`, `build_helpers/`...): `git grep -n "<tên>"` trong `pyproject.toml`, `setup.*`, `Dockerfile`, `mkdocs.yml`, `.github/`, `MANIFEST.in`, `requirements*.txt`.
- Liệt kê cho người dùng: số file/dòng sẽ xóa, các chỗ phải sửa, rủi ro. **Chờ người dùng đồng ý** rồi mới xóa.

## 2. Thực hiện
- `git rm -r <đường dẫn>` cho phần bị cắt và test tương ứng trong `tests/`.
- Sửa các chỗ import còn lại. Nếu bỏ một phụ thuộc, xóa luôn khỏi `requirements*.txt` / `pyproject.toml`.
- Xóa các mục liên quan trong `freqtrade/commands/arguments.py` (subcommand), `config_schema`, resolver nếu có.

## 3. Kiểm chứng (bắt buộc, tất cả phải qua)
```bash
.venv/Scripts/python.exe -c "import freqtrade.main, freqtrade.freqtradebot, freqtrade.optimize.backtesting"
.venv/Scripts/freqtrade.exe --version
.venv/Scripts/freqtrade.exe list-strategies --config user_data/config.json
.venv/Scripts/ruff.exe check freqtrade tests
.venv/Scripts/python.exe -m pytest tests -q -x -p no:randomly --ignore=tests/exchange_online
```
Thêm một backtest ngắn nếu đã có dữ liệu:
```bash
.venv/Scripts/freqtrade.exe backtesting --config user_data/config.json --strategy MyStrategy --timerange 20260501-20260601
```
- `mypy freqtrade` nếu đã sửa code Python trong core.

## 4. Ghi lại
- Thêm một dòng vào `docs_vn/trim-log.md`: ngày, phần đã cắt, số file/dòng, chỗ đã sửa, kết quả kiểm chứng.
- Cập nhật `docs_vn/structure.md` nếu bản đồ thư mục thay đổi.
- Commit bằng `/commit` với type `trim`, ví dụ: `trim(core): remove freqai module and its tests`.
