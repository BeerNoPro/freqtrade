# Cấu trúc source code (Structure)

> Bản đồ source `freqtrade/`: mỗi thư mục và file quan trọng kèm vai trò, các hàm chính,
> và luồng nào đi qua đó. **Đã đối chiếu với code** (Freqtrade **2026.9**).
> Logic chạy chi tiết: [lifecycle.md](lifecycle.md) · Tổng quan: [overview.md](overview.md)

## Cây thư mục gốc

```
freqtrade/  (repo)
├── freqtrade/          # CORE — mã nguồn framework (~65,5k dòng Python, chi tiết bên dưới)
├── user_data/          # CODE CỦA BẠN — strategy, config, data, kết quả (bị gitignore)
├── tests/              # Bộ test pytest, cấu trúc giống freqtrade/ (commands, exchange, freqtradebot,
│                       #   optimize, persistence, plugins, rpc, strategy, ...) + testdata/
├── docs/               # Tài liệu tiếng Việt (overview, structure, lifecycle, signal-bot-workflow)
├── ft_client/          # Client REST API Python (package freqtrade-client riêng)
├── scripts/            # format_code.py (format trước commit), check_encoding.py (encoding/xuống dòng),
│                       #   binance_update_lev_tiers.py (bảng đòn bẩy), rest/ws client mẫu
├── .gitattributes, .editorconfig, .pre-commit-config.yaml   # Chuẩn file text + git hook
├── Dockerfile, docker-compose.yml   # Đóng gói & chạy bằng Docker (triển khai VPS)
├── requirements.txt    # Toàn bộ thư viện để chạy bot (gồm hyperopt)
├── requirements-dev.txt # Công cụ phát triển: pytest, ruff, mypy, pre-commit (gồm requirements.txt)
├── pyproject.toml      # Build + cấu hình ruff / mypy / pytest / codespell
├── run.ps1             # Launcher Windows riêng của dự án (ui/trade/backtest/hyperopt/download)
└── CLAUDE.md           # Hướng dẫn ngữ cảnh cho agent
```

---

## 1. File gốc trong `freqtrade/`

| File | Dòng | Vai trò |
|---|---|---|
| [main.py](../freqtrade/main.py) | | Entry point: parse CLI (`Arguments`) → gọi `args["func"]`; bắt exception toàn cục, trả exit code |
| [__main__.py](../freqtrade/__main__.py) | | Cho phép `python -m freqtrade` |
| [worker.py](../freqtrade/worker.py) | 239 | `Worker`: vòng lặp `_throttle`, chuyển state, heartbeat, reload config |
| [freqtradebot.py](../freqtrade/freqtradebot.py) | 2836 | **LÕI** `FreqtradeBot`: toàn bộ vòng đời trade live/dry-run |
| [wallets.py](../freqtrade/wallets.py) | 538 | `Wallets`: số dư (live: từ sàn, dry: tính từ DB), tính và kiểm tra stake |
| [constants.py](../freqtrade/constants.py) | | Hằng số, giá trị mặc định |
| [exceptions.py](../freqtrade/exceptions.py) | | Cây exception (xem dưới) |
| [misc.py](../freqtrade/misc.py) | | Tiện ích: đọc/ghi file, `deep_merge_dicts`, `remove_entry_exit_signals`... |

### Hàm chính trong `freqtradebot.py`

| Hàm | Dòng | Luồng |
|---|---|---|
| `__init__` | 94 | Khởi tạo exchange → strategy → DB → wallets → RPC → DataProvider → pairlist → protections |
| `startup()` | 264 | Việc khi bắt đầu chạy: migrate, cập nhật lệnh mở từ sàn |
| `process()` | 306 | **Một vòng lặp** của bot |
| `_refresh_active_whitelist()` | 389 | Pairlist + cặp đang có lệnh mở |
| `enter_positions()` / `create_trade()` | 794 / 852 | Đọc tín hiệu vào, kiểm tra lock |
| `process_open_trade_positions()` / `check_and_call_adjust_trade_position()` | 916 / 934 | DCA |
| `execute_entry()` | 1063 | Đặt lệnh vào, tạo Trade |
| `get_valid_enter_price_and_stake()` | 1286 | Giá vào, đòn bẩy, stake (gọi các callback) |
| `exit_positions()` / `handle_trade()` / `_check_and_execute_exit()` | 1483 / 1529 / 1564 | Kiểm tra & thực thi thoát |
| `handle_stoploss_on_exchange()` | 1634 | Stoploss đặt trên sàn |
| `manage_open_orders()` / `replace_order()` | 1769 / 1860 | Lệnh chưa khớp: timeout, đặt lại giá |
| `handle_cancel_enter()` / `handle_cancel_exit()` | 2044 / 2135 | Xử lý lệnh bị hủy |
| `execute_trade_exit()` | 2249 | Đặt lệnh thoát |
| `update_trade_state()` / `_update_trade_after_fill()` | 2506 / 2560 | Cập nhật khi lệnh khớp |
| `handle_protections()` | 2622 | Chạy protections sau khi trade đóng |
| `check_liquidation_warnings()` | 428 | Futures: cảnh báo khi giá gần giá thanh lý |

### Cây exception ([exceptions.py](../freqtrade/exceptions.py))
```
FreqtradeException
├── OperationalException          → lỗi nghiêm trọng: Worker chuyển bot sang STOPPED
│   └── ConfigurationError
├── DependencyException           → lỗi tạm thời của một lệnh/cặp: log rồi bỏ qua
│   ├── PricingError
│   └── ExchangeError
│       ├── InvalidOrderException
│       │   ├── RetryableOrderError
│       │   └── InsufficientFundsError
│       └── TemporaryError        → Worker ngủ RETRY_TIMEOUT rồi chạy tiếp
│           └── DDosProtection
└── StrategyError                 → lỗi trong code strategy (bị strategy_safe_wrapper bắt)
```

---

## 2. `commands/` — Lệnh CLI

[arguments.py](../freqtrade/commands/arguments.py) khai báo subparser; [cli_options.py](../freqtrade/commands/cli_options.py) định nghĩa mọi tham số.

| File | Hàm `start_*` | Subcommand |
|---|---|---|
| trade_commands.py | `start_trading` | `trade` |
| optimize_commands.py | `start_backtesting`, `start_backtesting_show`, `start_hyperopt`, `start_lookahead_analysis`, `start_recursive_analysis`, `start_edge` | `backtesting`, `backtesting-show`, `hyperopt`, `lookahead-analysis`, `recursive-analysis`, `edge` (**đã bị gỡ từ 2025.6**, chỉ còn báo lỗi) |
| hyperopt_commands.py | `start_hyperopt_list`, `start_hyperopt_show` | `hyperopt-list`, `hyperopt-show` |
| data_commands.py | `start_download_data`, `start_convert_data`, `start_convert_trades`, `start_list_data`, `start_list_trades_data` | `download-data`, `convert-data`, `convert-trade-data`, `trades-to-ohlcv`, `list-data` |
| list_commands.py | `start_list_exchanges`, `start_list_markets`, `start_list_strategies`, `start_list_hyperopt_loss_functions`, `start_list_timeframes`, `start_show_trades` | `list-exchanges`, `list-markets`, `list-pairs`, `list-strategies`, `list-hyperoptloss`, `list-timeframes`, `show-trades` |
| deploy_commands.py (+ deploy_ui.py) | `start_create_userdir`, `start_new_strategy`, `start_install_ui` | `create-userdir`, `new-strategy`, `install-ui` |
| build_config_commands.py | `start_new_config`, `start_show_config` | `new-config`, `show-config` |
| analyze_commands.py | `start_analysis_entries_exits` | `backtesting-analysis` |
| pairlist_commands.py | `start_test_pairlist` | `test-pairlist` |
| db_commands.py | `start_convert_db` | `convert-db` |
| webserver_commands.py | `start_webserver` | `webserver` |

---

## 3. `configuration/` + `config_schema/` — Nạp và kiểm tra config

| File | Vai trò |
|---|---|
| configuration.py | `Configuration.load_config()`: file → biến môi trường → tham số CLI ([thứ tự ưu tiên](lifecycle.md#thứ-tự-ưu-tiên-config-cao--thấp)) |
| load_config.py | Đọc JSON (cho phép comment), gộp nhiều file, `add_config_files` |
| environment_vars.py | Đọc `FREQTRADE__SECTION__KEY` |
| config_validation.py | Kiểm tra theo schema + kiểm tra tính nhất quán (`validate_config_consistency`) |
| config_secrets.py | Xóa/ẩn credential khi in config |
| deprecated_settings.py | Cảnh báo/chuyển đổi khóa cũ (vd protections trong config) |
| timerange.py | Parse `--timerange` |
| config_setup.py, deploy_config.py, directory_operations.py, detect_environment.py | Thiết lập, tạo config mới, thư mục user_data, nhận diện môi trường |
| [config_schema/config_schema.py](../freqtrade/config_schema/config_schema.py) | JSON schema đầy đủ của config (1554 dòng) |

---

## 4. `strategy/` — Nơi viết logic giao dịch

| File | Vai trò |
|---|---|
| [interface.py](../freqtrade/strategy/interface.py) (1908 dòng) | **`IStrategy`**: callback cho người dùng + các hàm nội bộ `ft_*` mà bot gọi |
| parameters.py | `IntParameter`, `DecimalParameter`, `RealParameter`, `CategoricalParameter`, `BooleanParameter` |
| hyper.py | `HyperStrategyMixin`: phát hiện tham số, nạp `<Strategy>.json` |
| informative_decorator.py | `@informative()`: merge khung/cặp phụ, cột dạng `{column}_{timeframe}` |
| strategy_helper.py | `merge_informative_pair`, `stoploss_from_open`, `stoploss_from_absolute` |
| strategy_validation.py | `StrategyResultValidator`: chặn strategy làm sai lệch dataframe |
| strategy_wrapper.py | `strategy_safe_wrapper`: lỗi trong callback → log + giá trị mặc định |

### Hàm nội bộ quan trọng trong `IStrategy`

| Hàm | Dòng | Ai gọi |
|---|---|---|
| `analyze()` → `analyze_pair()` → `_analyze_ticker_internal()` | 1265 / 1241 / 1206 | `process()` mỗi vòng |
| `get_entry_signal()` / `get_exit_signal()` | 1354 / 1321 | `create_trade()` / `handle_trade()` |
| `should_exit()` | 1419 | `_check_and_execute_exit()` + backtest |
| `ft_stoploss_adjust()` / `ft_stoploss_reached()` | 1524 / 1605 | Trong `should_exit()` và sau khi lệnh khớp |
| `min_roi_reached()` | 1719 | Trong `should_exit()` |
| `ft_check_timed_out()` | 1734 | `manage_open_orders()` |
| `advise_all_indicators()` | 1759 | Backtest/hyperopt (vectorized) |

Danh sách callback người dùng: [lifecycle.md mục Q](lifecycle.md#q-danh-sách-callback-của-strategy).

---

## 5. `exchange/` — Wrapper sàn (ccxt)

| File | Vai trò |
|---|---|
| [exchange.py](../freqtrade/exchange/exchange.py) (4380 dòng) | **Lớp `Exchange`**: markets, OHLCV, đặt/hủy/lấy lệnh, số dư, phí, leverage tiers, funding fee, giả lập dry-run (`create_dry_run_order`, `check_dry_limit_order_filled`) |
| exchange_ws.py | `ExchangeWS`: nhận nến qua WebSocket (ccxt pro) khi sàn bật `ws_enabled` |
| exchange_types.py | Kiểu dữ liệu: `FtHas` (khả năng sàn), `Ticker`, `OrderBook`, `CcxtBalance`, `CcxtPosition`, `LeverageTier`, `CcxtOrder` |
| exchange_utils.py / exchange_utils_timeframe.py | Tiện ích: làm tròn precision, `timeframe_to_*` |
| common.py | Retry decorator (`retrier`), danh sách sàn, hằng số |
| check_exchange.py | Kiểm tra sàn có được hỗ trợ |
| binance_public_data.py | Tải data lịch sử nhanh từ data.binance.vision |
| **binance.py** | Lớp riêng **duy nhất** còn lại: `Binance`, `Binanceus`, `Binanceusdm` (các sàn khác đã bị cắt — xem [trim-log.md](trim-log.md)) |
| binance_leverage_tiers.json | Bảng leverage tier Binance futures (dùng khi không gọi được API) |

Sàn khác vẫn có thể khai báo trong config nhưng sẽ chạy bằng lớp `Exchange` chung (không được hỗ trợ chính thức). Lớp sàn ghi đè `_ft_has` / `_ft_has_futures` (khả năng sàn: loại lệnh stop, giới hạn nến, WebSocket...)
và `_supported_trading_mode_margin_pairs`. Ví dụ Binance: futures hỗ trợ cross + isolated, giới hạn 499 nến/lần,
không dùng WebSocket cho futures, bắt buộc One-way mode khi chạy live.

---

## 6. `data/` — Dữ liệu nến & phân tích kết quả

| File | Vai trò |
|---|---|
| [dataprovider.py](../freqtrade/data/dataprovider.py) | **`DataProvider`** (`self.dp` trong strategy): `ohlcv`, `get_pair_dataframe`, `get_analyzed_dataframe`, `orderbook`, `ticker`, `funding_rate`, `send_msg`, `current_whitelist`, `runmode` |
| history/history_utils.py | Tải/cập nhật/nạp dữ liệu lịch sử (`download-data`, backtest) |
| history/datahandlers/ | `IDataHandler` + định dạng feather (mặc định), json, parquet, arrow |
| converter/converter.py | OHLCV ↔ DataFrame, bỏ nến chưa đóng, `trim_dataframe` |
| [candle_columns.py](../freqtrade/candle_columns.py) | Định nghĩa cột theo loại nến (OHLCV, funding_rate, **open_interest**) |
| converter/trade_converter.py, orderflow.py | Dữ liệu trades → OHLCV, phân tích order flow |
| btanalysis/bt_fileutils.py | Đọc kết quả backtest (`load_backtest_stats`, `load_backtest_data`) |
| btanalysis/historic_precision.py, trade_parallelism.py | Precision lịch sử, số lệnh song song |
| metrics.py | Drawdown, Sharpe, Sortino, Calmar, CAGR, expectancy, market change — dùng cho báo cáo và hàm loss |
| entryexitanalysis.py | `backtesting-analysis`: phân tích tín hiệu theo tag |

---

## 7. `optimize/` — Backtesting, Hyperopt, phân tích

| Nhóm | File | Vai trò |
|---|---|---|
| Backtest | backtesting.py (1977 dòng) | Engine mô phỏng: `_get_ohlcv_as_lists` (dịch tín hiệu), `backtest_loop`, mô hình khớp lệnh |
| | backtest_caching.py | Cache kết quả backtest |
| | optimize_reports/ (optimize_reports.py, bt_output.py, bt_storage.py) | Tạo bảng/thống kê, in kết quả, lưu file .zip |
| Hyperopt | hyperopt/hyperopt.py | Vòng lặp epoch, chạy song song (joblib), lưu .fthypt |
| | hyperopt/hyperopt_optimizer.py | Chuẩn bị dữ liệu (indicators 1 lần), Optuna sampler, `generate_optimizer` mỗi epoch |
| | hyperopt/hyperopt_interface.py, hyperopt_auto.py | `IHyperOpt`: không gian tham số, sampler mặc định NSGAIIISampler |
| | hyperopt/hyperopt_logger.py, hyperopt_output.py | Log + hiển thị |
| | hyperopt_tools.py, hyperopt_epoch_filters.py | Xuất `<Strategy>.json`, lọc epoch |
| | space/ (optunaspaces.py, decimalspace.py) | Kiểu không gian tìm kiếm |
| Loss | hyperopt_loss/ | `IHyperOptLoss` + 12 hàm: Sharpe(+Daily), Sortino(+Daily), Calmar, MaxDrawDown (+Relative, +PerPair), OnlyProfit, ProfitDrawDown, MultiMetric, ShortTradeDur |
| Phân tích | analysis/lookahead*.py | `lookahead-analysis`: phát hiện look-ahead bias |
| | analysis/recursive*.py | `recursive-analysis`: kiểm tra chỉ báo hội tụ theo số nến khởi động |

---

## 8. `plugins/` — Pairlist + Protections

| File | Vai trò |
|---|---|
| [pairlistmanager.py](../freqtrade/plugins/pairlistmanager.py) | Chạy chuỗi pairlist, áp blacklist |
| [protectionmanager.py](../freqtrade/plugins/protectionmanager.py) | `stop_per_pair()` / `global_stop()` — gọi sau khi trade đóng |

### `pairlist/`
| Loại | Plugin |
|---|---|
| Generator (`is_pairlist_generator`) | `StaticPairList`, `VolumePairList`, `PercentChangePairList`, `RemotePairList` |
| Filter | `AgeFilter`, `DelistFilter`, `FullTradesFilter`, `OffsetFilter`, `PairInformationFilter`, `PerformanceFilter`, `PrecisionFilter`, `PriceFilter`, `RangeStabilityFilter`, `ShuffleFilter`, `SpreadFilter`, `VolatilityFilter` |
| Cơ sở | `IPairList`, pairlist_helpers.py |

### `protections/`
| Plugin | Tác dụng |
|---|---|
| `StoplossGuard` | Khóa khi có quá nhiều lệnh dừng lỗ trong cửa sổ |
| `MaxDrawdown` | Khóa khi sụt giảm vượt ngưỡng |
| `LowProfitPairs` | Khóa cặp lời thấp |
| `CooldownPeriod` | Khóa cặp một thời gian sau khi đóng lệnh |
| `IProtection` | Lớp cơ sở |

---

## 9. `persistence/` — Database (SQLAlchemy)

| File | Vai trò |
|---|---|
| [trade_model.py](../freqtrade/persistence/trade_model.py) (2197 dòng) | **`Trade`/`LocalTrade` + `Order`**: trạng thái lệnh, tính lãi lỗ, stoploss (`adjust_stop_loss`), funding, liquidation. `LocalTrade` dùng trong backtest (không ghi DB) |
| pairlock.py / pairlock_middleware.py | Bảng `PairLock` + API `PairLocks` (khóa cặp/toàn cục) |
| custom_data.py | `trade.set_custom_data()` / `get_custom_data()` — lưu dữ liệu riêng cho từng trade (cả trong backtest) |
| key_value_store.py | Lưu khóa–giá trị dùng chung (vd thời điểm bot bắt đầu) |
| wallet_history.py | Lịch sử số dư (ghi hằng ngày) |
| migrations.py / db_migration.py | Nâng cấp schema DB |
| models.py / base.py / usedb_context.py | `init_db`, base SQLAlchemy, ngữ cảnh tắt DB (backtest/webserver) |

---

## 10. `rpc/` — Điều khiển & thông báo

| File | Vai trò |
|---|---|
| [rpc.py](../freqtrade/rpc/rpc.py) (1839 dòng) | **Logic chung** cho mọi kênh: status, profit, `_rpc_force_entry`, `_rpc_force_exit`, `_rpc_analysed_dataframe`, start/stop/pause... |
| rpc_manager.py | Đăng ký kênh (Telegram, Webhook, API) và phát `send_msg()` |
| rpc_types.py | Kiểu message gửi đi |
| telegram.py (2336 dòng) | Bot Telegram (thread `FTTelegram`) |
| webhook.py | Gửi sự kiện ra URL ngoài (có retry/timeout) |
| fiat_convert.py | Quy đổi tiền pháp định (CoinGecko) |

### `rpc/api_server/` — REST API + WebUI (FastAPI, thread `FTUvicorn`)
| File | Vai trò |
|---|---|
| webserver.py / uvicorn_threaded.py / webserver_bgwork.py | Khởi tạo FastAPI, **gắn tất cả router** dưới `/api/v1`, chạy server trong thread, việc chạy nền |
| api_auth.py | HTTP Basic / JWT (HS256), token WebSocket |
| api_v1.py | Endpoint hệ thống: `/ping` (công khai), `/version`, `/show_config`, `/logs`, `/health`, `/sysinfo`, `/markets`, `/plot_config`, `/strategy/{name}` |
| api_trading.py | Trade: `/status`, `/trades`, `/profit`, `/forceenter`, `/forceexit`, `/pair_candles`, `/start`, `/stop`, `/pause`... |
| api_backtest.py, api_analysis.py, api_background_tasks.py | Chạy backtest, lookahead/recursive analysis qua API (chế độ webserver) |
| api_pair_history.py, api_pairlists.py, api_download_data.py | Lịch sử cặp (phân tích lại toàn bộ dữ liệu), thử pairlist, tải data |
| api_webserver.py | Chỉ ở chế độ `webserver`: liệt kê strategies, exchanges, hàm loss |
| api_ws.py, ws/ (channel, message_stream, proxy, serializer, ws_types), ws_schemas.py | WebSocket `/message/ws`: phát sự kiện + dataframe (producer) cho FreqUI |
| api_schemas.py | Pydantic schema request/response |
| deps.py | Dependency injection (`get_rpc`, `get_config`...) |
| web_ui.py | Phục vụ FreqUI (`ui/installed/`) |

---

## 11. Module hạ tầng

| Thư mục | Vai trò |
|---|---|
| [resolvers/](../freqtrade/resolvers/) | Nạp class theo tên: strategy (kèm ghi đè thuộc tính từ config), exchange, pairlist, protection, hyperopt loss |
| [enums/](../freqtrade/enums/) | `State`, `RunMode`, `ExitType`, `ExitCheckTuple`, `SignalType`/`SignalTagType`/`SignalDirection`, `TradingMode`, `MarginMode`, `CandleType`, `PriceType`, `RPCMessageType`, `OrderTypeValues`, `BacktestState`, `HyperoptState`, `MarketStateType` |
| [leverage/](../freqtrade/leverage/) | Lãi vay margin (interest.py), giá thanh lý (liquidation_price.py) |
| [util/](../freqtrade/util/) | ft_scheduler (lịch chạy định kỳ), datetime_helpers, ft_precise, measure_time, periodic_cache, ft_ttlcache, dry_run_wallet, formatters, rich_tables/progress, coin_gecko, template_renderer, migrations/ |
| [loggers/](../freqtrade/loggers/) | Cấu hình logging (rich, json, buffer cho API `/logs`) |
| [mixins/](../freqtrade/mixins/) | `LoggingMixin` (`log_once`) |
| [system/](../freqtrade/system/) | Cấu hình asyncio, gc, multiprocessing, in version |
| [ft_types/](../freqtrade/ft_types/) | TypedDict dùng chung: kết quả backtest, plot annotation, danh sách sàn |
| [templates/](../freqtrade/templates/) | Mẫu cho `new-strategy`/`new-config`: sample_strategy, sample_hyperopt_loss, subtemplates |
| [vendor/qtpylib/](../freqtrade/vendor/qtpylib/) | Chỉ báo qtpylib nhúng kèm |

---

## 12. Luồng → file (tra nhanh)

| Luồng | Đi qua |
|---|---|
| Khởi động bot | `main.py` → `commands/trade_commands.py` → `worker.py` → `configuration/` → `resolvers/` → `freqtradebot.py:__init__` |
| Một vòng lặp | `worker.py:_throttle` → `freqtradebot.py:process` |
| Tải nến | `data/dataprovider.py:refresh` → `exchange/exchange.py:refresh_latest_ohlcv` (+ `exchange_ws.py`) |
| Sinh tín hiệu | `strategy/interface.py:analyze` → strategy của bạn (`populate_*`) |
| Vào lệnh | `freqtradebot.py:enter_positions/create_trade/execute_entry` → `wallets.py` → `exchange.py:create_order` → `persistence/trade_model.py` |
| Lệnh chưa khớp | `freqtradebot.py:manage_open_orders/update_trade_state` |
| Thoát lệnh | `freqtradebot.py:exit_positions/handle_trade` → `interface.py:should_exit` → `freqtradebot.py:execute_trade_exit` |
| Protections | `freqtradebot.py:handle_protections` → `plugins/protectionmanager.py` → `persistence/pairlock_middleware.py` |
| Lệnh từ API | `rpc/api_server/api_trading.py` → `rpc/rpc.py` → `freqtradebot.py` |
| Thông báo | `rpc/rpc_manager.py:send_msg` → telegram / webhook / api ws |
| Backtest | `commands/optimize_commands.py` → `optimize/backtesting.py` → `optimize/optimize_reports/` |
| Hyperopt | `optimize/hyperopt/hyperopt.py` → `hyperopt_optimizer.py` → `backtesting.py` → `hyperopt_loss/` |
| Tải dữ liệu | `commands/data_commands.py` → `data/history/history_utils.py` → `exchange.py` / `binance_public_data.py` → `datahandlers/` |

---

## Bản đồ `user_data/` (code của bạn — hiện trạng)

```
user_data/
├── config.json              # Config chính: Binance spot, dry-run, 5m, API server 127.0.0.1:8080
├── config_futures.json      # (nháp) config futures kế thừa config.json qua add_config_files — chưa dùng
├── strategies/
│   └── MyStrategy.py        # Bản sao sample strategy (RSI + TEMA + Bollinger), chưa backtest
├── data/binance/            # OHLCV đã tải: BTC/USDT, ETH/USDT (5m, 1h) — spot
├── tradesv3.dryrun.sqlite   # DB dry-run
├── backtest_results/        # Kết quả backtest (.zip)
├── hyperopt_results/        # Kết quả hyperopt (.fthypt)
├── hyperopts/               # Hàm loss tùy chỉnh
├── logs/
└── CLAUDE.md
```
> Toàn bộ `user_data/*` nằm trong `.gitignore` (chỉ giữ `.gitkeep`) → strategy/config không vào git trừ khi thêm ngoại lệ.

## Công cụ chất lượng code

```bash
ruff check freqtrade     # lint (line-length 100, max-complexity 12)
ruff format freqtrade    # format
mypy freqtrade           # type check
pytest                   # test (asyncio_mode=auto); CI chạy trên Linux/macOS/Windows × Python 3.11–3.14
```
