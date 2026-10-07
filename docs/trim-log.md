# Nhật ký cắt gọn source (trim log)

Ghi lại mọi phần đã xóa khỏi code Freqtrade 2026.9, để biết vì sao code khác bản gốc và lấy lại khi cần
(bản đầy đủ: tag/commit Freqtrade 2026.9 trên GitHub, hoặc file backup `freqtrade-backup-2026-10-07.bundle`).

Kiểm chứng chuẩn sau mỗi lần cắt: import core, `freqtrade --version`, `list-strategies`, `pytest tests`
(bỏ qua `tests/exchange_online`), backtest ngắn. 4 test luôn fail trên Windows ở cả bản gốc, không liên quan:
`test_startup_time`, `test_sysinfo`, `test_pip_audit_*` (2 test).

| Ngày | Phần đã cắt | Quy mô | Chỗ phải sửa | Kiểm chứng |
|---|---|---|---|---|
| 2026-10-07 | `.github/dependabot.yml` | 1 file | — | — |
| 2026-10-07 | Tài liệu tiếng Anh `docs/`, `mkdocs.yml`, `.readthedocs.yml`, `CONTRIBUTING.md`, job CI build docs, `build_helpers/create_command_partials.py`, `tests/test_docs.sh`; gộp `docs_vn/` vào `docs/` | 140 file | `requirements-dev.txt`, `.dockerignore`, devcontainer Dockerfile, `ci.yml`, các link tài liệu | CI yaml hợp lệ |
| 2026-10-07 | `config_examples/` | 4 file | Chuyển `config_full` và `config_freqai` vào `tests/testdata/` (test dùng làm fixture), sửa đường dẫn trong test và `.gitignore` | `test_configuration.py` 74 pass, test freqai liên quan pass |
| 2026-10-07 | Mọi sàn trừ Binance: 19 module trong `freqtrade/exchange/` (bingx, bitget, bitpanda, bitvavo, bybit, coinex, cryptocom, gate, hitbtc, htx, hyperliquid, idex, kraken, krakenfutures, kucoin, lbank, luno, modetrade, okx), bộ chuyển `kraken_csv`, test riêng từng sàn, dữ liệu test Kraken | ~19 module core + 11 file test | `exchange/__init__.py`; `SUPPORTED_EXCHANGES` chỉ còn họ Binance; bỏ lựa chọn `kraken_csv`; `tests/conftest.py` (patch chế độ giao dịch rơi về lớp `Exchange` chung); bỏ tham số test của sàn đã xóa; test logic chung chuyển sang Binance hoặc giả lập cờ `_ft_has` | 3.548 pass (chỉ còn 4 lỗi môi trường); backtest MyStrategy chạy được |
| 2026-10-07 | `.github/` của upstream (9 workflow CI/Docker/PyPI/docs, mẫu issue/PR, FUNDING, devcontainer) và `.devcontainer/` | 21 file | Thay bằng `.github/workflows/ci.yml` tối giản: ruff + pytest trên Ubuntu/Python 3.13 cho `develop`/`main` | CI yaml hợp lệ; ruff 0.16.8 sạch toàn repo |

## Ghi chú môi trường
- 2026-10-07: gỡ `aiodns`/`pycares` khỏi `.venv` — `pycares 5.0.1` không phân giải DNS được trên Windows làm ccxt báo `ExchangeNotAvailable`. Không gói nào bắt buộc `aiodns`; aiohttp tự dùng DNS hệ thống khi không có nó.
