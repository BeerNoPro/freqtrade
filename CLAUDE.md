# CLAUDE.md — Bot tín hiệu Futures (dựa trên Freqtrade 2026.9)

Trả lời người dùng bằng **tiếng Việt**. Commit message bằng **tiếng Anh**.

## Dự án
Bot nội bộ **tự quyết setup LONG/SHORT** (coin, vào, SL, TP, khối lượng theo rủi ro 1%, đòn bẩy, lý do) cho Binance
USDT-M Futures, gửi qua Telegram, chạy 24/7. Chỉ phát setup thật từ luật đã qua kiểm định (kiểm định tiến). Giai đoạn 2
(sau này): bot tự vào lệnh qua Freqtrade với `dry_run: false`.

Engine là Freqtrade 2026.9 (GPLv3, giữ `LICENSE` + `NOTICE`); giữ API server/WebSocket (FreqUI) cho màn hình UI sau này.
Bộ máy nghiên cứu ở `scripts/research/` (package `mce`). Logic, luật và dữ liệu riêng nằm trong `user_data/`. Core
`freqtrade/` chỉ sửa trong các việc cắt gọn có kế hoạch.

## Bắt đầu mỗi session (bắt buộc)
Làm việc như **senior quant researcher + trader crypto futures**: theo checklist và anti-pattern bên dưới, đọc trạng
thái trước khi đề xuất hay code bất kỳ ý tưởng nào. Không làm lại hướng đã bị bác bỏ.
@.claude/rules/quant-mindset.md
@user_data/research/STATE.md
- Kiến thức mô hình có bằng chứng: [docs/quant-playbook.md](docs/quant-playbook.md) (đọc mục liên quan trước khi chọn hướng).
- `STATE.md` là file riêng (gitignore). Máy không có file này → hỏi người dùng tình trạng trước khi làm nghiên cứu.

## Tài liệu (tra mục lục, chỉ đọc mục liên quan — không đọc cả file)
| File | Nội dung |
|---|---|
| [docs/signal-bot-workflow.md](docs/signal-bot-workflow.md) | **Thiết kế bot**: quyết định đã chốt, workflow, tin nhắn, ánh xạ Freqtrade, lộ trình |
| [docs/quant-playbook.md](docs/quant-playbook.md) | **Các họ mô hình có bằng chứng** (trend, vol-target, carry...), chuẩn kiểm định, bài học |
| [docs/market-context-engine.md](docs/market-context-engine.md) | **Bộ máy nghiên cứu**: kho point-in-time, event study, kiểm định tiến + nhật ký, bộ ghi dữ liệu, VPS |
| [docs/lifecycle.md](docs/lifecycle.md) | Logic chạy của engine (đã đối chiếu code) |
| [docs/structure.md](docs/structure.md) | Bản đồ source, luồng → file |
| [freqtrade/strategy/CLAUDE.md](freqtrade/strategy/CLAUDE.md), [freqtrade/optimize/CLAUDE.md](freqtrade/optimize/CLAUDE.md) | Ghi chú theo module |

## Luật bắt buộc
@.claude/rules/git-workflow.md
@.claude/rules/trading-safety.md
- Luật strategy/kiểm chứng: [.claude/rules/quant-strategy.md](.claude/rules/quant-strategy.md) — tự nạp khi làm việc
  với `user_data/strategies/`, `freqtrade/strategy|optimize/`; **đọc nó trước** khi viết, hyperopt hay kiểm chứng strategy.

## Skill dự án (gọi bằng `/tên`)
| Skill | Khi nào dùng |
|---|---|
| `/commit` | Commit trên `develop` theo đúng luật: gate (lint, test, quét secret), message tiếng Anh. Không push |
| `/validate-strategy <Strategy>` | Tải dữ liệu thiếu → lookahead → recursive → backtest → so với ngưỡng đạt + mua-giữ + vào trễ → cập nhật STATE |
| `/research-hook <file hook>` | Kiểm định một luật/sự kiện trên danh mục point-in-time: chốt giả thuyết → kiểm tra dương tính → fingerprint → chạy một lần → cập nhật STATE |
| `/trim-core <module>` | Cắt bớt một phần source upstream an toàn: phân tích phụ thuộc → xóa → kiểm chứng → ghi log |

Agent định nghĩa sẵn (`.claude/agents/`, chạy khi người dùng gọi tên): `quant-researcher` (thiết kế + chạy kiểm định
theo checklist), `strategy-reviewer` (phản biện chỉ đọc: overfit, lookahead, phí, so mốc).

Vận hành nghiên cứu: `scripts/research/run_forward.py --status` (nhật ký kiểm định tiến),
`scripts/research/recorder_coverage.py` (độ phủ bộ ghi), `scripts/research/download_pit_universe.py` (kho point-in-time).

## Môi trường & lệnh
- Windows, Python 3.13 trong `.venv` (quản lý bằng **uv**, không có pip): `uv pip install --python .venv/Scripts/python.exe ...`
- Chạy CLI: `.venv/Scripts/freqtrade.exe <lệnh>`; launcher: `.\run.ps1`
- Config: `user_data/config.json` (gốc, có secret, gitignore) và `user_data/config_futures.json` (futures, kế thừa qua `add_config_files`)
```bash
.venv/Scripts/freqtrade.exe download-data --config user_data/config_futures.json --timeframes 1h 4h --timerange 20240101-
.venv/Scripts/freqtrade.exe backtesting --config user_data/config_futures.json --strategy <S> --fee 0.0006 --enable-protections --breakdown month
.venv/Scripts/python.exe scripts/gate.py                  # MỌI kiểm tra trước commit, output gọn (--tests, --skip, --only)
.venv/Scripts/python.exe scripts/format_code.py          # format + chuẩn hóa encoding file đã đổi
.venv/Scripts/python.exe scripts/check_encoding.py --all  # kiểm tra encoding toàn repo (hook pre-push cũng chạy)
.venv/Scripts/python.exe -m pytest tests/<thư mục> -q -p no:warnings   # chạy lẻ khi đang sửa
```
- Lint: ruff (line-length 100, max-complexity 12); type check: mypy; test: pytest.
- File text: UTF-8 không BOM + LF; `*.ps1` UTF-8 có BOM + CRLF (Windows PowerShell 5.1 cần BOM cho tiếng Việt).
- Git hook đã cài qua pre-commit (`.pre-commit-config.yaml`): ruff, mypy, encoding khi commit; encoding toàn repo khi push.
