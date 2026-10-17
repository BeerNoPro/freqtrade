# Nhật ký cắt gọn source (trim log)

Những phần đã xóa khỏi Freqtrade 2026.9 và quyết định giữ lại có chủ ý. Lấy lại bản gốc: tag/commit Freqtrade 2026.9
trên GitHub, hoặc `freqtrade-backup-2026-10-07.bundle`. Mỗi lần cắt kiểm chứng bằng `scripts/gate.py` (toàn bộ test
xanh) và thêm một dòng vào bảng.

| Ngày | Đã cắt | Giữ lại có chủ ý / chỗ đã sửa |
|---|---|---|
| 2026-10-07 | Tài liệu tiếng Anh, mkdocs, CI docs, `dependabot.yml`, `.github/` upstream, `.devcontainer/` | CI tối giản `.github/workflows/ci.yml` (ruff + pytest) |
| 2026-10-07 | `config_examples/` | `config_full`, `config_freqai` chuyển vào `tests/testdata/` làm fixture |
| 2026-10-07 | Mọi sàn trừ Binance (19 module + bộ chuyển `kraken_csv` + test) | `SUPPORTED_EXCHANGES` chỉ họ Binance; logic chung test bằng Binance hoặc cờ `_ft_has` giả lập |
| 2026-10-07 | FreqAI (package, resolver, lệnh, tham số, endpoint, requirements) | `_validate_freqai_removed`: config bật `freqai.enabled` báo lỗi rõ |
| 2026-10-07 | `build_helpers/`, `docker/` phụ | `scripts/binance_update_lev_tiers.py`; `Dockerfile` + `docker-compose.yml` ở gốc cho VPS |
| 2026-10-08 | Gộp requirements hyperopt/plot vào `requirements.txt`; file lẻ ở gốc (`.pylintrc`, `setup.*`, `freqtrade.service`) | `requirements-dev.txt` riêng (image VPS không cài công cụ test) |
| 2026-10-08 | Tàn dư sàn đã xóa (wizard `new-config`, template, nhánh KuCoin/Kraken), lãi vay Kraken | Logic margin chung (Binance không bật margin nên `trading_mode: margin` bị từ chối) |
| 2026-10-08 | Discord, phía consumer của Producer/Consumer, `MarketCapPairList`, `CrossMarketPairList`, `strategy-updater`, lệnh `edge` | Phía producer (`api_ws.py`, `ws/`) cho FreqUI; `coin_gecko` cho quy đổi fiat; `_validate_edge` |
| 2026-10-08 | Plot (`freqtrade/plot/`, lệnh plot, notebook), orderflow + luồng trade realtime, `ft_client/` | `plot_config`, endpoint `/plot_config`, `plot_annotations` (FreqUI vẽ chỉ báo); tải trade lịch sử `--dl-trades` |
| 2026-10-08 | Thư viện không còn ai import (`janus`, `aiofiles`, `pytz`, `scikit-learn`, `packaging`...) | Gói cần gián tiếp: `bottleneck`, `numexpr`, `certifi`, `urllib3`, `cryptography`, `httpx`, `prompt-toolkit`, `cmaes`, `ft-pandas-ta` |
| 2026-10-17 | `Dockerfile`, `docker-compose.yml`, `.dockerignore` (service `freqtrade` trỏ strategy chưa có; image ~1,3 GB và daemon tốn RAM trên VPS 2 GB) | VPS chạy như laptop: venv + systemd user service, deploy tự động `deploy/` ([deploy-vps.md](deploy-vps.md)). Phần nhận biết môi trường docker trong core (`detect_environment`) giữ nguyên |

## Ghi chú môi trường (còn hiệu lực)
- Không cài `aiodns`/`pycares`: `pycares 5.0.1` không phân giải DNS trên Windows làm ccxt báo `ExchangeNotAvailable`.
- `ccxt==4.5.85` ghim `multidict==6.7.1` (CVE-2026-104874, bot không dùng phần lỗi) → tạm bỏ qua trong
  `tests/test_pip_audit.py`; gỡ khi ccxt nâng pin `multidict` ≥ 6.9.1.
