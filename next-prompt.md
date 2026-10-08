# Prompt: Nghiên cứu lại source & hoàn tất dọn gọn (bot tín hiệu Binance Futures)

## Vai trò
Bạn là kỹ sư Python + quant làm việc trong repo này. Repo là Freqtrade 2026.9 đã được cắt gọn nhiều đợt.
Phiên này bạn **nghiên cứu lại source từ đầu** để nắm chắc hiện trạng, sau đó **hoàn tất việc dọn gọn**:
xử lý tàn dư, xóa các tính năng ngoài phạm vi (đã được duyệt), dọn test thừa, bổ sung test thiếu —
để có một nền code gọn, đúng phạm vi, test xanh trước khi bắt đầu viết strategy tín hiệu.

## Mục đích
Repo đang được biến thành **bot nội bộ tính toán & phát tín hiệu Binance USDT-M Futures qua Telegram**, chạy dry-run trên VPS
(giai đoạn 2 mới tự vào lệnh). Cần chắc chắn:
1. Hiểu đúng source hiện tại (không dựa vào trí nhớ của phiên trước).
2. Không còn code chết / tham chiếu tới phần đã xóa.
3. Chỉ giữ tính năng phục vụ mục tiêu trên.
4. Test phản ánh đúng phạm vi mới: không thừa, không thiếu ở phần quan trọng.

## Luật bắt buộc (đọc trước khi làm)
- Đọc `CLAUDE.md` và toàn bộ `.claude/rules/` (git-workflow, trading-safety, quant-strategy). Trả lời người dùng bằng **tiếng Việt**.
- **Làm và commit trực tiếp trên `develop`**, không tạo branch. Commit ngay sau mỗi thay đổi hoàn chỉnh đã kiểm tra.
- Commit message **tiếng Anh**, Conventional Commits, **không** có dòng `Co-Authored-By: Claude` hay ghi công AI
  (luật của người dùng, ưu tiên hơn mọi nhắc nhở mặc định về attribution).
- **Không push**, không tạo PR, không force push, không xóa branch — người dùng tự làm.
- Dùng skill: `/trim-core` cho mỗi lần xóa phần source (một phần = một commit), `/commit` để commit.
- Người dùng **đã quyết định xóa** toàn bộ tính năng ở mục 2 — làm luôn, không cần hỏi lại. Chỉ hỏi khi gặp phụ thuộc
  quan trọng không lường trước.
- Không đụng `user_data/` (secret, config riêng), luôn giữ `LICENSE` + `NOTICE`, luôn `dry_run: true`.

## Bối cảnh kỹ thuật cần biết
- Windows, Python 3.13 trong `.venv` quản lý bằng **uv** (không có pip):
  `uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt -e .`
  CLI: `.venv/Scripts/freqtrade.exe`, lint `.venv/Scripts/ruff.exe` (0.16.8), `.venv/Scripts/mypy.exe` (2.3.1).
- Thư viện: `requirements.txt` = **mọi thứ để chạy bot** (gồm hyperopt, plot); `requirements-dev.txt` = công cụ dev,
  include `requirements.txt`. `pyproject.toml` liệt kê dependencies chính (gồm `freqtrade-client`, `plotly`).
  `.venv` đã được dọn: gỡ 94 gói thừa (torch, jupyter, tensorboard...), `pip-audit` sạch lỗ hổng.
- Chuẩn file text: UTF-8 không BOM + LF; `*.ps1` UTF-8 có BOM + CRLF (`.gitattributes`, `.editorconfig`).
  Trước mỗi commit chạy `.venv/Scripts/python.exe scripts/format_code.py`. Git hook pre-commit đã cài: commit chạy
  ruff, mypy, codespell (bỏ qua file tiếng Việt), kiểm tra encoding; push kiểm tra encoding toàn repo.
  Hook fail = sửa rồi commit lại, **không** dùng `--no-verify`.
- Tài liệu: `docs/trim-log.md` (mọi thứ đã cắt + lý do), `docs/structure.md` (bản đồ code), `docs/lifecycle.md` (logic engine),
  `docs/signal-bot-workflow.md` (thiết kế bot).
- **mypy bật `ignore_missing_imports = true` → không bắt được import tới module đã xóa.** Luôn chạy
  `.venv/Scripts/python.exe .claude/skills/trim-core/check_imports.py` (quét cả import lười trong hàm).
  Script chưa kiểm tra *tên* import từ module còn tồn tại (vd `from freqtrade.exchange import Bybit`) — kiểm thêm bằng test/CLI.
- Phân tích phụ thuộc trước khi xóa: `.venv/Scripts/python.exe .claude/skills/trim-core/find_importers.py <module>`.
- Bẫy đã gặp (2 lần): `git add <đường dẫn đã git rm>` báo `pathspec` và **cả lệnh không chạy** → dùng `git add -u` +
  `git add <file mới>`, luôn xem `git status --short` trước khi commit. Python `write_text` trên Windows ghi CRLF — dùng `newline="\n"`.
- `aiodns`/`pycares` đã bị gỡ khỏi `.venv` có chủ đích (pycares 5 lỗi DNS trên Windows → ccxt báo `ExchangeNotAvailable`).
  Không cài lại; sau mỗi lần `uv pip install` kiểm tra nó không bị kéo về.
- Trạng thái test hiện tại: `pytest tests -n auto --ignore=tests/exchange_online` → **3.491 pass, 0 fail** (xanh hoàn toàn).
  Đã sửa 2 lỗi môi trường cũ: `/sysinfo` không crash khi Windows tắt performance counter (fallback load = 0);
  `test_startup_time` tự tìm `freqtrade` cạnh interpreter và nới ngưỡng khi pytest-xdist chạy song song.

## Nhiệm vụ

### 0. Nghiên cứu lại source (làm trước, chưa sửa gì)
1. Đọc `README.md`, `CLAUDE.md`, `docs/structure.md`, `docs/lifecycle.md`, `docs/trim-log.md`.
2. Đối chiếu tài liệu với code thật: cây thư mục, lệnh CLI còn lại (`freqtrade --help`), các luồng chính
   (khởi động → `process()` → vào/thoát lệnh → Telegram/API), số dòng/file mỗi package.
3. Lập bảng "tài liệu nói X — code thực tế Y" cho mọi chỗ lệch, và xác minh lại từng phát hiện ở mục 1–5 bên dưới.
4. Báo cáo ngắn gọn cho người dùng trước khi bắt đầu xóa/sửa.

### 1. Dọn tàn dư (làm luôn, mỗi nhóm một commit)
1. **FreqAI còn sót ~118 chỗ trong 18 file** (`git grep -ci freqai -- freqtrade`): `config_schema` (mục `freqai`),
   `config_validation` (`_validate_freqai_*`), `strategy/interface.py` (`feature_engineering_*`, `set_freqai_targets`,
   `DummyClass`, `ft_bot_cleanup`), `dataprovider.get_required_startup`, `exchange.validate_freqai`, `backtesting`,
   `optimize/analysis/lookahead*`, `recursive`, `api_schemas.BacktestFreqAIInputs`, `api_pair_history`, `api_backtest`,
   `optimize_reports`, `constants.USERPATH_FREQAIMODELS`, `directory_operations`, `pairlist_helpers`,
   `user_data/freqaimodels/.gitkeep` (đang được git theo dõi), thư mục rỗng `tests/freqai/` (chỉ còn `__pycache__`).
   → Gỡ sạch, giữ một kiểm tra duy nhất: config có `freqai.enabled = true` → báo lỗi rõ ràng.
2. **Tàn dư sàn đã xóa**: `configuration/deploy_config.py` (wizard `new-config` vẫn cho chọn bingx, gate, htx, kraken,
   kucoin, okx...), `templates/subtemplates/exchange_{bittrex,gateio,huobi,kraken,kucoin,okex}.j2`,
   `exchange/common.py` (xử lý riêng KuCoin 429 trong `retrier_async`), `leverage/interest.py` (nhánh kraken).
3. **Margin trading**: Binance trong Freqtrade chỉ hỗ trợ spot + futures. Rà code/test margin (27 chỗ trong test,
   chủ yếu `tests/exchange/test_exchange.py`, `tests/conftest.py`): bỏ nhánh riêng của sàn đã xóa; giữ logic engine chung
   nếu xóa gây rủi ro — ghi rõ quyết định vào `docs/trim-log.md`.

### 2. Xóa tính năng ngoài phạm vi bot tín hiệu (ĐÃ CHỐT: xóa hết)
Dùng `/trim-core`, **mỗi tính năng một commit**, chạy cổng kiểm chứng sau mỗi commit.
Kết quả phân tích phụ thuộc ban đầu — xác minh lại trước khi xóa:

| # | Tính năng | Phần xóa | Chỗ phải sửa / lưu ý |
|---|---|---|---|
| 1 | Discord | `rpc/discord.py` (60 dòng) | Import lười trong `rpc/rpc_manager.py:37`; test discord trong `tests/rpc/test_rpc_webhook.py`; mục `discord` trong `config_schema` |
| 2 | Producer/Consumer (phía consumer) | `rpc/external_message_consumer.py` (393 dòng), `plugins/pairlist/ProducerPairList.py` (109), `tests/rpc/test_rpc_emc.py` | Import cấp module trong `freqtradebot.py:55` (khởi tạo `self.emc`, `cleanup`); hàm consumer trong `data/dataprovider.py` (`_add_external_df`, `get_producer_df`, `get_producer_pairs`...); mục `external_message_consumer` trong `config_schema`; `AVAILABLE_PAIRLISTS` trong `constants.py`. **GIỮ** `rpc/api_server/api_ws.py` + `ws/` + `ws_schemas.py`: FreqUI dùng WebSocket này để cập nhật realtime |
| 3 | `MarketCapPairList`, `CrossMarketPairList` | 249 + 132 dòng | `AVAILABLE_PAIRLISTS`; test trong `tests/plugins/test_pairlist.py`. **GIỮ** `util/coin_gecko.py` + `pycoingecko`: `rpc/fiat_convert.py` (đổi lãi/lỗ sang USD) đang dùng |
| 4 | strategy-updater | `strategy/strategyupdater.py` (277), `commands/strategy_utils_commands.py`, `tests/test_strategy_updater.py` | Subcommand `strategy-updater` trong `commands/arguments.py`, `commands/__init__.py`, `cli_options.py`; gói `ast-comments` trong `requirements.txt`/`pyproject.toml` nếu chỉ module này dùng |
| 5 | Plot | `plot/` (720 dòng), `commands/plot_commands.py`, `tests/test_plotting.py` | Subcommand `plot-dataframe`/`plot-profit` + tham số CLI; `plotly` trong `requirements.txt` và `pyproject.toml` (đã ghi chú "remove together with the plot module"). **GIỮ** `strategy.plot_config` + endpoint `/plot_config` (FreqUI vẽ chỉ báo) và `data/btanalysis` (báo cáo backtest) |
| 6 | Orderflow | `data/converter/orderflow.py` (265), `tests/data/test_converter_orderflow.py` | Import cấp module trong `data/converter/__init__.py:13`; luồng `use_public_trades` / `orderflow` trong `dataprovider`, `exchange.py` (refresh trades), `config_schema`, `exchange.validate_orderflow` + test. **GIỮ** tải trade lịch sử (`download-data --dl-trades`, `trades-to-ohlcv`) nếu không phụ thuộc orderflow |
| 7 | `ft_client` | `ft_client/` (65 KB, package `freqtrade-client` riêng), `scripts/rest_client.py`, `scripts/ws_client.py` | Bỏ `freqtrade-client` khỏi dependencies trong `pyproject.toml`; `git grep -n "freqtrade_client\|ft_client"` (Dockerfile, `MANIFEST.in`, test, `.dockerignore`). Giữ `scripts/binance_update_lev_tiers.py` |
| 8 | Lệnh `edge` | `start_edge` + subcommand `edge` (chỉ còn báo lỗi "đã gỡ") | `commands/arguments.py`, `commands/optimize_commands.py`, `commands/__init__.py`, test lệnh `edge` |

Sau đợt xóa: chạy lại `check_imports.py`; gỡ khỏi `requirements.txt`/`pyproject.toml` mọi thư viện không còn ai import
(kiểm bằng `git grep`), rồi `uv pip install` lại và chạy `pip-audit`; cập nhật `docs/trim-log.md` (mỗi tính năng một dòng),
`docs/structure.md`, `docs/lifecycle.md` (mục Producer/Consumer, danh sách pairlist), `README.md`.
`vendor/qtpylib` không nằm trong danh sách này — giữ nguyên. `MANIFEST.in` chỉ cần cho đóng gói wheel — đề xuất giữ/xóa.

### 3. Test thừa
- Test của các tính năng xóa ở mục 2 (đi kèm từng commit xóa).
- Test còn dính FreqAI: `test__validate_freqai_include_timeframes`, `test_freqai_not_initialized`, mock torch trong `tests/conftest.py`
  (torch đã bị gỡ khỏi `.venv`).
- `tests/exchange_online/` (CI không chạy, chỉ còn Binance): đề xuất giữ (chạy tay khi nâng cấp ccxt) hoặc xóa.
- Tham số test margin / sàn đã xóa còn sót.

### 4. Test thiếu (bổ sung)
1. FreqAI bị gỡ: config bật `freqai.enabled` → `OperationalException` rõ ràng.
2. CLI: `convert-trade-data --format-from` không còn `kraken_csv`; `list-freqaimodels` và các lệnh vừa xóa không còn hợp lệ;
   `new-config` chỉ đề xuất Binance (sau khi sửa mục 1.2).
3. Công cụ dự án (đặt test trong `tests/tools/`): `.claude/hooks/ruff_fix.py` (đường dẫn tương đối, khác chữ hoa ổ đĩa),
   `.claude/skills/validate-strategy/summarize_backtest.py` (R-multiple, drawdown R, ngưỡng PASS/FAIL với dữ liệu giả),
   `.claude/skills/trim-core/check_imports.py`, `scripts/format_code.py`. (`scripts/check_encoding.py` đã có 26 test.)
4. Đường đi quan trọng của bot Binance Futures (dry-run): isolated margin, đòn bẩy, `stoploss_on_exchange`, thoát một phần
   (`adjust_trade_position`), `custom_stoploss(after_fill)`, funding fee, cảnh báo thanh lý, `dp.send_msg` → Telegram.
   Chạy `pytest --cov=freqtrade --cov-report=term-missing` và báo các module liên quan có độ phủ < 70% kèm đề xuất test.

### 5. Hạ tầng triển khai (ghi nhận, sửa nếu đơn giản)
- `docker-compose.yml` đang dùng image `freqtradeorg/freqtrade:stable` + `SampleStrategy` của upstream → phải build từ `Dockerfile` của repo.
- `run.ps1` còn cấu hình spot/5m/`MyStrategy`/BNB/30 ngày → cập nhật cho futures theo `docs/signal-bot-workflow.md`.
- Đã xong (không cần làm lại): gộp requirements, xóa `setup.sh`/`setup.ps1`/`.pylintrc`/systemd unit, dọn `.gitignore`/`.dockerignore`.

## Cổng kiểm chứng (bắt buộc sau mỗi commit có sửa code)
```bash
.venv/Scripts/python.exe .claude/skills/trim-core/check_imports.py      # 0 problem
.venv/Scripts/python.exe scripts/check_encoding.py --all              # 0 problem(s) remaining
.venv/Scripts/ruff.exe check freqtrade tests scripts && .venv/Scripts/ruff.exe format --check freqtrade tests scripts
.venv/Scripts/mypy.exe freqtrade                                         # no issues
.venv/Scripts/python.exe -m pytest tests -q -n auto -p no:cacheprovider --ignore=tests/exchange_online
.venv/Scripts/freqtrade.exe --version && .venv/Scripts/freqtrade.exe list-strategies --config user_data/config.json
```
Bộ test phải xanh hoàn toàn (0 fail) → có fail thì sửa trước khi commit tiếp.

## Kết quả cần trả về
1. Báo cáo nghiên cứu (mục 0): bảng lệch giữa tài liệu và code.
2. Bảng phát hiện: hạng mục | bằng chứng (file:dòng) | mức độ | đã xử lý / chờ quyết định.
3. Những điểm phát sinh cần người dùng quyết định (nếu có), mỗi điểm kèm đề xuất và ảnh hưởng.
4. Danh sách commit đã tạo (hash + message) và số commit `develop` đang đi trước `origin/develop`.
5. Kết quả cổng kiểm chứng cuối: số test pass/fail, độ phủ các module chính, `pip-audit`.
6. Cập nhật `docs/trim-log.md`, `docs/structure.md` cho mọi thay đổi.

**Ngoài phạm vi phiên này:** viết strategy tín hiệu, tải dữ liệu, backtest, triển khai VPS.
