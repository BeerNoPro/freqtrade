# Vòng đời & Logic chạy (Lifecycle) — đối chiếu với code

> Mô tả toàn bộ luồng logic của Freqtrade: khởi động, vòng lặp live/dry-run, vào/thoát lệnh,
> quản lý lệnh, rủi ro, futures, API, backtest, hyperopt.
> **Mọi điểm dưới đây đã được đối chiếu trực tiếp với source** (Freqtrade **2026.9**).
> Số dòng trong link có thể lệch khi cập nhật upstream — tìm theo tên hàm.
> Liên kết: [overview.md](overview.md) · [structure.md](structure.md)

## Mục lục
- [A. Khởi động](#a-khởi-động)
- [B. Các thread đang chạy](#b-các-thread-đang-chạy)
- [C. Vòng lặp chính `_throttle`](#c-vòng-lặp-chính-_throttle)
- [D. Bên trong `process()`](#d-bên-trong-process)
- [E. Dữ liệu nến & phân tích tín hiệu](#e-dữ-liệu-nến--phân-tích-tín-hiệu)
- [F. Pairlist](#f-pairlist--chọn-coin)
- [G. Vào lệnh](#g-vào-lệnh)
- [H. Quản lý lệnh chưa khớp & xử lý khi khớp](#h-quản-lý-lệnh-chưa-khớp--xử-lý-khi-khớp)
- [I. Thoát lệnh](#i-thoát-lệnh)
- [J. Stoploss trên sàn](#j-stoploss-trên-sàn-stoploss_on_exchange)
- [K. Protections & PairLocks](#k-protections--pairlocks)
- [L. Điều chỉnh vị thế (DCA)](#l-điều-chỉnh-vị-thế-dca)
- [M. Vốn & khối lượng lệnh](#m-vốn--khối-lượng-lệnh-wallets)
- [N. Futures](#n-futures-đòn-bẩy)
- [O. State machine](#o-state-machine)
- [P. RPC / REST API / Telegram](#p-rpc--rest-api--telegram)
- [Q. Danh sách callback của Strategy](#q-danh-sách-callback-của-strategy)
- [R. Backtesting](#r-backtesting)
- [S. Hyperopt](#s-hyperopt)
- [T. Lookahead / Recursive analysis](#t-lookahead--recursive-analysis)
- [U. Producer / Consumer](#u-producer--consumer)
- [V. Những điểm hay hiểu nhầm](#v-những-điểm-hay-hiểu-nhầm)

---

## A. Khởi động

```
freqtrade trade --config user_data/config.json --strategy MyStrategy
```

```
main.py: main()                                  → Arguments → args["func"]
 └─ commands/trade_commands.py: start_trading()  → Worker(args).run()
     └─ Worker._init()
         ├─ Configuration(args).get_config()      (xem "Thứ tự ưu tiên config")
         └─ FreqtradeBot(config).__init__()       theo đúng thứ tự:
             1. ExchangeResolver.load_exchange()   kết nối sàn (ccxt), nạp markets, leverage tiers
             2. StrategyResolver.load_strategy()   nạp class strategy + ghi đè thuộc tính từ config
             3. validate_config_consistency()      kiểm tra config sau khi strategy đã ghi đè
             4. init_db(db_url)                    SQLite/SQLAlchemy
             5. Wallets                            số dư
             6. RPCManager                         Telegram/Discord/Webhook/API server (thread riêng)
             7. DataProvider, PairListManager      gắn dp + wallets vào strategy
             8. ExternalMessageConsumer            nếu bật producer/consumer
             9. _refresh_active_whitelist()        chạy pairlist lần đầu
            10. state = initial_state (config)
            11. Scheduler: futures → funding fee + giá thanh lý (phút 1 và 31 mỗi giờ)
                           00:02 reset WS, 00:07 ghi lịch sử ví
            12. strategy.ft_bot_start()            → callback bot_start() + nạp tham số hyperopt
            13. ProtectionManager                  khởi tạo SAU bot_start (để đọc được tham số)
 └─ Worker.run() → khi state chuyển sang RUNNING/PAUSED lần đầu → FreqtradeBot.startup():
             migrate DB, gửi thông báo khởi động, cập nhật precision trade cũ,
             Trade.stoploss_reinitialization() (nếu đổi stoploss), cập nhật lệnh mở từ sàn
             (chỉ live), cập nhật giá thanh lý + funding fee
```

Code: [main.py](../freqtrade/main.py) · [worker.py](../freqtrade/worker.py) · [freqtradebot.py `__init__`](../freqtrade/freqtradebot.py#L94) · [`startup()`](../freqtrade/freqtradebot.py#L264)

### Thứ tự ưu tiên config (cao → thấp)

| Nguồn | Ghi chú |
|---|---|
| Tham số CLI (`--strategy`, `--timeframe`, `--timerange`...) | Áp dụng sau cùng trong `Configuration.load_config()` |
| Biến môi trường `FREQTRADE__SECTION__KEY` | Ghi đè file config ([configuration.py](../freqtrade/configuration/configuration.py#L71)) |
| Nhiều `--config a.json --config b.json` | File sau ghi đè file trước |
| `"add_config_files": [...]` trong một file | File chứa khóa này ghi đè các file nó include ([load_config.py](../freqtrade/configuration/load_config.py#L106)) |

**Config ghi đè strategy**: các thuộc tính `timeframe`, `stoploss`, `minimal_roi`, `trailing_*`,
`order_types`, `stake_amount`, `max_open_trades`, `unfilledtimeout`, `use_exit_signal`,
`position_adjustment_enable`... nếu có trong config sẽ **thắng** giá trị trong strategy
([strategy_resolver.py](../freqtrade/resolvers/strategy_resolver.py#L58)).
Ví dụ: config có `"timeframe": "5m"` thì strategy khai báo `15m` cũng bị đổi thành `5m`.

**Tham số hyperopt** (`IntParameter`...): file `<Strategy>.json` cạnh file strategy
> dict `buy_params`/`sell_params` trong class > giá trị `default` ([hyper.py](../freqtrade/strategy/hyper.py#L86)).

---

## B. Các thread đang chạy

| Thread | Việc | Ghi chú |
|---|---|---|
| Main | `Worker` → `FreqtradeBot.process()` | Toàn bộ logic giao dịch chạy tuần tự ở đây |
| `FTUvicorn` (nếu bật `api_server`) | REST API + WebUI + WebSocket | [uvicorn_threaded.py](../freqtrade/rpc/api_server/uvicorn_threaded.py) |
| `FTTelegram` (nếu bật `telegram`) | Bot Telegram | [telegram.py](../freqtrade/rpc/telegram.py) |
| EMC (tùy chọn) | Nhận dữ liệu từ bot producer | [external_message_consumer.py](../freqtrade/rpc/external_message_consumer.py) |

API/Telegram gọi thẳng vào đối tượng bot (qua `RPC`). Các thao tác vào/thoát lệnh từ đó được đồng bộ
với vòng lặp chính bằng **`FreqtradeBot._exit_lock`**.

---

## C. Vòng lặp chính `_throttle`

[`Worker._throttle()`](../freqtrade/worker.py#L145): chạy `process()`, rồi ngủ
`min(process_throttle_secs − thời gian vừa chạy, thời điểm nến mới mở + 1s)`.

- Mặc định `process_throttle_secs = 5` → bot chạy **khoảng 5 giây/lần**, và luôn có một vòng chạy ngay sau khi nến mới mở.
- Lỗi `TemporaryError` (mạng, sàn tạm lỗi) → log cảnh báo, ngủ `RETRY_TIMEOUT` rồi chạy tiếp.
- Lỗi `OperationalException` → gửi thông báo, **chuyển state sang STOPPED**.

---

## D. Bên trong `process()`

[freqtradebot.py:306](../freqtrade/freqtradebot.py#L306)

| # | Bước | Hàm | Ghi chú |
|---|---|---|---|
| 1 | Reload markets | `exchange.reload_markets()` | Theo chu kỳ |
| 2 | Cập nhật phí còn thiếu | `update_trades_without_assigned_fees()` | Chỉ live |
| 3 | Lấy lệnh đang mở | `Trade.get_open_trades()` | Từ DB |
| 4 | Whitelist | `_refresh_active_whitelist()` | Chuỗi pairlist + **luôn thêm cặp đang có lệnh mở** |
| 5 | Tải nến | `dataprovider.refresh()` | Whitelist + informative pairs (mục E) |
| 6 | `bot_loop_start()` | callback | |
| 7 | Phân tích | `strategy.analyze(whitelist)` | Tuần tự từng cặp; cảnh báo nếu > 25% thời lượng nến |
| 8 | Lệnh chưa khớp | `manage_open_orders()` | Trong `_exit_lock` (mục H) |
| 9 | Thoát lệnh | `exit_positions()` | Trong `_exit_lock` (mục I) |
| 9b | Cảnh báo thanh lý | `check_liquidation_warnings()` | Futures: gửi cảnh báo khi giá tiến gần giá thanh lý (mới từ 2026.x) |
| 10 | DCA | `process_open_trade_positions()` | Chỉ khi `position_adjustment_enable` (mục L) |
| 11 | Vào lệnh | `enter_positions(free_trade_slots)` | Chỉ khi state **RUNNING** và còn slot (mục G) |
| 12 | Việc định kỳ | `_schedule.run_pending()` | Funding fee, giá thanh lý... |
| 13 | Thông báo | `rpc.process_msg_queue()` | Tin nhắn strategy gửi qua `dp.send_msg()` |

---

## E. Dữ liệu nến & phân tích tín hiệu

### Tải nến — `DataProvider.refresh()` → `Exchange.refresh_latest_ohlcv()`
- **Chỉ dùng nến đã đóng**: nến đang chạy bị bỏ (`drop_incomplete`).
- Nguồn dữ liệu: WebSocket (ccxt pro) nếu sàn được bật `ws_enabled`, ngược lại là REST.
  Binance **spot** dùng WebSocket; Binance **futures** dùng REST polling (`ws_enabled: False`, [binance.py](../freqtrade/exchange/binance.py#L71)).
- Số nến lần đầu dựa trên `startup_candle_count`; sàn giới hạn số nến mỗi lần gọi
  (Binance futures 499), freqtrade gọi nhiều lần nếu cần (tối đa 5 lần).

### Phân tích — `IStrategy.analyze()` → `analyze_pair()` → `_analyze_ticker_internal()`
[interface.py:1206](../freqtrade/strategy/interface.py#L1206)
```
với mỗi cặp (tuần tự):
  nếu process_only_new_candles = False HOẶC có nến mới:
     advise_indicators():  các hàm @informative (merge sẵn, không lookahead) → populate_indicators()
     advise_entry():       populate_entry_trend()   → cột enter_long / enter_short / enter_tag
     advise_exit():        populate_exit_trend()    → cột exit_long / exit_short / exit_tag
     lưu dataframe vào cache của DataProvider (dp.get_analyzed_dataframe() đọc từ đây)
  StrategyResultValidator: báo lỗi nếu strategy làm thay đổi số dòng / giá close / date cuối
```
- Mặc định `process_only_new_candles = True` → mỗi cặp chỉ tính lại **một lần mỗi nến**.
- Dataframe đã phân tích (kèm tín hiệu) được giữ trong cache suốt cây nến; API `/pair_candles` cũng đọc từ cache này.

### Đọc tín hiệu vào — `get_entry_signal()`
[interface.py:1354](../freqtrade/strategy/interface.py#L1354)
- Chỉ xét **dòng cuối** (nến đã đóng gần nhất).
- Bỏ qua nếu dữ liệu cũ: nến cuối cũ hơn `2 × timeframe + outdated_offset (5 phút)`.
- Long khi `enter_long == 1` **và không có** `exit_long` hoặc `enter_short` cùng nến.
- Short tương tự, chỉ khi futures/margin và `can_short = True`.
- `ignore_buying_expired_candle_after` (giây): bỏ tín hiệu nếu bot xử lý quá trễ sau khi nến đóng.

---

## F. Pairlist — chọn coin

[plugins/pairlistmanager.py](../freqtrade/plugins/pairlistmanager.py) chạy chuỗi trong config **tuần tự**:

```
[Generator]               →  [Filter]  →  [Filter]  → ... → whitelist
StaticPairList,              AgeFilter, PriceFilter, SpreadFilter,
VolumePairList,              VolatilityFilter, PerformanceFilter,
MarketCapPairList,           RangeStabilityFilter, ShuffleFilter, ...
RemotePairList (URL),
ProducerPairList, ...
```
- Blacklist (`pair_blacklist`, hỗ trợ regex) bị loại khỏi kết quả.
- Sau khi tạo whitelist, bot **luôn thêm các cặp đang có lệnh mở** để vẫn tải nến và quản lý thoát cho chúng.
- Thứ tự whitelist quan trọng: khi slot `max_open_trades` hạn chế, cặp đứng trước được xét vào lệnh trước (cả live lẫn backtest).

---

## G. Vào lệnh

`enter_positions()` → với từng cặp chưa có lệnh mở → `create_trade()` → `execute_entry()`
([freqtradebot.py:794-1262](../freqtrade/freqtradebot.py#L794-L1262))

```
0. Đang có global lock (protection toàn cục)? → không vào lệnh nào
1. get_entry_signal() có tín hiệu?                              (mục E)
2. Cặp bị khóa theo chiều (long/short)?  is_pair_locked          (mục K)
3. Stake mặc định: wallets.get_trade_stake_amount()              (mục M)
4. (tùy chọn) check_depth_of_market
5. get_valid_enter_price_and_stake():
     a. giá đề xuất = exchange.get_rate(entry) theo entry_pricing (orderbook / ticker)
     b. custom_entry_price()                → giá vào tùy chỉnh (bị chặn trong biên hợp lệ)
     c. leverage()                          → chỉ futures, kẹp trong [1, max của sàn]
     d. min/max stake của sàn cho cặp
     e. custom_stake_amount()               → chỉ lệnh vào đầu tiên
     f. validate_stake_amount()             → kẹp theo số dư; nhỏ hơn min sẽ được nâng lên min
                                              nếu chênh ≤ 30%, quá 30% thì bỏ lệnh
6. confirm_trade_entry()                    → chỉ lệnh vào đầu tiên; False = bỏ
7. exchange.create_order()                  → dry-run: lệnh giả lập | live: lệnh thật
8. Tạo Trade trong DB NGAY LÚC ĐẶT LỆNH (amount = 0, is_open = True),
   đặt stoploss ban đầu = strategy.stoploss, gửi thông báo "entry"
9. Nếu lệnh đã khớp ngay (market/FOK) → update_trade_state() luôn;
   nếu chưa → được kiểm tra ở các vòng sau (mục H)
```

> Thứ tự callback thực tế là **`custom_entry_price → leverage → custom_stake_amount → confirm_trade_entry`**.

---

## H. Quản lý lệnh chưa khớp & xử lý khi khớp

### `manage_open_orders()` — mỗi vòng
[freqtradebot.py:1769](../freqtrade/freqtradebot.py#L1769)
```
với mỗi lệnh đang mở của mỗi trade:
  fetch_order() (hỏi sàn — polling)  → update_trade_state()
  nếu vẫn chưa khớp:
     hết hạn? (unfilledtimeout.entry/exit HOẶC check_entry_timeout()/check_exit_timeout())
        → hủy lệnh
     ngược lại, nếu đã sang nến mới → adjust_order_price()
        (mặc định gọi adjust_entry_price / adjust_exit_price)
        trả về None → hủy | giữ giá → giữ | giá mới → hủy và đặt lại
```
- Lệnh vào bị hủy mà **chưa khớp chút nào** (và là lệnh duy nhất) → **Trade bị xóa khỏi DB**.
- Lệnh thoát hết hạn quá `unfilledtimeout.exit_timeout_count` lần → thoát khẩn cấp (`emergency_exit`, mặc định market).

### Khi một lệnh khớp — `update_trade_state()` → `_update_trade_after_fill()`
[freqtradebot.py:2506-2604](../freqtrade/freqtradebot.py#L2506-L2604)
```
cập nhật Order + Trade (amount, giá trung bình, phí)
→ order_filled()                                callback
→ nếu là lệnh vào: hủy stoploss trên sàn cũ, đặt lại stoploss ban đầu
→ cập nhật giá thanh lý (futures)
→ custom_stoploss(after_fill=True)              nếu strategy khai báo tham số after_fill
→ nếu trade đã đóng: hủy stoploss trên sàn
→ wallets.update()
→ thông báo fill; nếu là lệnh thoát và trade đã đóng → handle_protections()   (mục K)
```

### Dry-run khớp lệnh thế nào ([exchange.py](../freqtrade/exchange/exchange.py#L1206))
- Market: giá khớp tính bằng cách "đi" qua orderbook L2 (20 mức), trượt giá tối đa 5%.
- Limit: khớp khi giá **cắt qua** orderbook (mua: ask ≤ giá đặt). Nếu lúc đặt đã cắt spread hơn 1% thì chuyển thành market.

---

## I. Thoát lệnh

`exit_positions()` ([freqtradebot.py:1483](../freqtrade/freqtradebot.py#L1483))
```
với mỗi trade đang mở:
  1. stoploss_on_exchange bật → handle_stoploss_on_exchange()   (mục J); nếu đã đóng → xong
  2. handle_trade():
       đọc exit signal của nến cuối (cần use_exit_signal hoặc ignore_roi_if_entry_signal)
       exit_rate = exchange.get_rate(exit)
       should_exit() → danh sách lý do thoát
       thực thi lý do ĐẦU TIÊN đặt lệnh thành công (execute_trade_exit)
```

### `should_exit()` — thứ tự lý do thoát
[interface.py:1419-1522](../freqtrade/strategy/interface.py#L1419-L1522)
```
1. EXIT_SIGNAL (exit_long/exit_short = 1 và không có enter cùng chiều)
   hoặc CUSTOM_EXIT (custom_exit() trả về True/chuỗi)       ← chỉ khi use_exit_signal = True
2. STOP_LOSS / LIQUIDATION
3. ROI
4. TRAILING_STOP_LOSS
```
- **Exit signal/custom_exit đứng trước stoploss** trong danh sách.
- `custom_exit()` chỉ được gọi khi `use_exit_signal = True` **và** nến cuối không có exit signal.
- `exit_profit_only` chỉ áp dụng cho EXIT_SIGNAL, không áp dụng cho custom_exit.

### Kiểm tra stoploss — `ft_stoploss_reached()`
1. `ft_stoploss_adjust()`:
   - stoploss ban đầu = `strategy.stoploss`
   - nếu `use_custom_stoploss` → `custom_stoploss()`
   - nếu `trailing_stop` → trailing
   - stop **chỉ dịch theo chiều có lợi**, trừ lần gọi `after_fill`
2. Giá chạm stop → STOP_LOSS / TRAILING_STOP_LOSS. Nhưng nếu đang **live với `stoploss_on_exchange`**, bot **không** tự thoát theo stop — để lệnh stop trên sàn xử lý.
3. Giá chạm giá thanh lý → LIQUIDATION.

### ROI — `min_roi_reached()`
Lấy mức ROI theo thời gian giữ lệnh từ `minimal_roi`; nếu `use_custom_roi` thì dùng `min(bảng ROI, custom_roi())`.
Thoát khi lợi nhuận hiện tại > mức đó.

### `execute_trade_exit()` ([freqtradebot.py:2249](../freqtrade/freqtradebot.py#L2249))
```
loại lệnh: exit | stoploss (theo order_types) | emergency_exit (mặc định market)
→ custom_exit_price()        nếu lệnh limit
→ hủy stoploss trên sàn (trừ sàn mà lệnh stop không khóa tài sản, vd Binance futures)
→ confirm_trade_exit()       KHÔNG gọi cho LIQUIDATION và thoát một phần; False = hủy thoát
→ exchange.create_order()
→ ghi nhớ lý do thoát cho nến này (tránh đặt lại liên tục)
```
> ⚠️ `confirm_trade_exit()` trả về False cũng chặn được cả lệnh thoát do stoploss phía bot.

---

## J. Stoploss trên sàn (`stoploss_on_exchange`)

[`handle_stoploss_on_exchange()`](../freqtrade/freqtradebot.py#L1634):
- Lệnh vào đã khớp mà chưa có lệnh stop → đặt lệnh stop trên sàn (Binance futures: `stop_market`/`stop`).
- Lệnh stop đã khớp → đóng trade.
- Lệnh stop bị hủy → đặt lại.
- Stoploss trong bot thay đổi (trailing/custom) → mỗi `stoploss_on_exchange_interval` giây (mặc định 60) hủy và đặt lại lệnh stop.
- Đặt lệnh stop lỗi `InvalidOrderException` → thoát khẩn cấp.

Lợi ích: bot chết/mất mạng vẫn có stop trên sàn. Ở dry-run, stop trên sàn được giả lập.

---

## K. Protections & PairLocks

**Khi nào chạy** ([`handle_protections()`](../freqtrade/freqtradebot.py#L2622)):
**chỉ sau khi một lệnh thoát khớp và trade đóng**:
```
khóa cặp vừa đóng (cùng chiều) đến hết nến hiện tại   ← chống vào lại ngay
protections.stop_per_pair()  → có thể tạo lock cho cặp
protections.global_stop()    → có thể tạo lock toàn cục
```

**Khi vào lệnh**: bot chỉ **kiểm tra lock đang tồn tại** (`is_global_lock`, `is_pair_locked` trong mục G).
Lock lưu trong bảng PairLocks ([pairlock_middleware.py](../freqtrade/persistence/pairlock_middleware.py)).

| Protection | Tạo lock khi |
|---|---|
| `StoplossGuard` | Quá `trade_limit` lệnh dừng lỗ trong cửa sổ lookback |
| `MaxDrawdown` | Sụt giảm (theo lệnh đóng hoặc equity) vượt `max_allowed_drawdown` |
| `LowProfitPairs` | Lợi nhuận của cặp trong lookback < `required_profit` |
| `CooldownPeriod` | Vừa đóng lệnh trên cặp |

- Protections khai báo trong strategy (`@property protections`); khai báo trong config đã **deprecated**.
- Backtest chỉ chạy protections khi có `--enable-protections`.
- **`/forceenter` qua API/Telegram bỏ qua toàn bộ lock và protections** (mục P).

---

## L. Điều chỉnh vị thế (DCA)

Khi `position_adjustment_enable = True`, mỗi vòng `process_open_trade_positions()` gọi
`adjust_trade_position()` cho từng trade đang mở ([freqtradebot.py:934](../freqtrade/freqtradebot.py#L934)):
- Được gọi cho trade đang có vị thế **hoặc** đang có lệnh chưa khớp.
- Trả về số **dương** → mua thêm, qua `execute_entry(mode="pos_adjust")`.
  Giới hạn bởi `max_entry_position_adjustment`; bị chặn khi bot ở state PAUSED;
  **không** gọi `confirm_trade_entry`/`custom_stake_amount`.
- Trả về số **âm** → thoát một phần (`PARTIAL_EXIT`); vẫn chạy khi PAUSED.

---

## M. Vốn & khối lượng lệnh (Wallets)

[wallets.py](../freqtrade/wallets.py)
- `update()`: live → `fetch_balance` từ sàn; dry-run → tính từ `dry_run_wallet` + lãi/lỗ đã chốt − stake đang mở.
- `get_trade_stake_amount()`: `stake_amount` cố định, hoặc `"unlimited"` → chia đều vốn khả dụng cho số slot còn trống.
- Vốn dùng được = (stake đang mở + số dư rảnh) × `tradable_balance_ratio`; hoặc `available_capital` nếu cấu hình.
- `validate_stake_amount()`: kẹp theo min/max của sàn và số dư (xem mục G-5f).

---

## N. Futures (đòn bẩy)

| Chủ đề | Hành vi |
|---|---|
| Định dạng cặp | `BTC/USDT:USDT` |
| `margin_mode` | `isolated` hoặc `cross` (tùy sàn hỗ trợ; Binance có cả hai) |
| Đòn bẩy | Callback `leverage()` cho lệnh đầu, kẹp trong [1, max theo leverage tier]; Binance làm tròn xuống số nguyên |
| Stoploss | Tính trên **vốn bỏ ra (đã gồm đòn bẩy)**: stoploss −10% ở 10x = giá đi ngược 1% |
| Giá thanh lý | Tính khi lệnh khớp; stoploss bị giới hạn cách giá thanh lý một khoảng `liquidation_buffer` (mặc định 0.05) |
| Funding fee | Cập nhật theo lịch phút 1 và 31 mỗi giờ; backtest dùng dữ liệu `funding_rate` + `mark` đã tải |
| Binance live | Tài khoản phải ở One-way mode; Multi-Assets chỉ được dùng với cross margin ([binance.py](../freqtrade/exchange/binance.py#L121)) |
| Short | Cần `can_short = True` trong strategy và trading_mode khác spot |

---

## O. State machine

[enums/state.py](../freqtrade/enums/state.py) · chuyển trạng thái trong [worker.py](../freqtrade/worker.py#L83)

| State | Vào lệnh mới | Quản lý lệnh mở (thoát, stoploss) | Lệnh điều khiển |
|---|---|---|---|
| `RUNNING` | Có | Có | `/start` |
| `PAUSED` | **Không** | Có | `/pause` = `/stopentry` = `/stopbuy` (cùng một hàm) |
| `STOPPED` | Không | **Không** — chỉ hủy lệnh chờ nếu `cancel_open_orders_on_exit` | `/stop` |
| `RELOAD_CONFIG` | — | — | `/reload_config` → dựng lại bot với config mới |

> ⚠️ `/stop` khi còn lệnh mở = **không ai quản lý lệnh** (trừ stoploss đặt sẵn trên sàn). Muốn chỉ ngừng vào lệnh mới, dùng `/stopentry`.

---

## P. RPC / REST API / Telegram

[rpc.py](../freqtrade/rpc/rpc.py) chứa logic chung; Telegram và REST API chỉ là "vỏ" gọi vào đây.

**Bot → bên ngoài** (thông báo): `RPCManager.send_msg()` phát tới Telegram, Discord, Webhook, WebSocket của API
(các loại: `entry`, `entry_fill`, `exit`, `exit_fill`, `protection_trigger`, `analyzed_df`, `whitelist`...).

**Bên ngoài → bot** (REST, xác thực Basic hoặc JWT; xem [api_auth.py](../freqtrade/rpc/api_server/api_auth.py)):

| Endpoint | Luồng |
|---|---|
| `GET/POST /api/v1/pair_candles` | Đọc dataframe đã phân tích trong cache (có cột tín hiệu) — `_rpc_analysed_dataframe` |
| `GET /status, /trades, /profit, /entries, /exits, /mix_tags` | Đọc DB |
| `POST /forceenter` | `_rpc_force_entry` → `execute_entry()` **ngay trong thread API** (có `_exit_lock`) |
| `POST /forceexit` | `_rpc_force_exit` → `execute_trade_exit()` loại FORCE_EXIT |
| `POST /start, /stop, /pause, /reload_config` | Đổi state |

**`/forceenter` khác tín hiệu strategy ở đâu** ([rpc.py:1138-1222](../freqtrade/rpc/rpc.py#L1138-L1222)):
- Cần `force_entry_enable: true` và state RUNNING.
- Chỉ kiểm tra: cặp tồn tại, đúng stake currency, không short trên spot, còn slot, chưa có lệnh cùng cặp.
- **Bỏ qua PairLocks và Protections.**
- Vẫn đi qua `custom_entry_price`, `custom_stake_amount`, `confirm_trade_entry` (với `entry_tag` mặc định `force_entry`) → có thể chặn lệnh ở `confirm_trade_entry`.
- Nếu gửi kèm `leverage` thì **bỏ qua callback `leverage()`**, chỉ bị kẹp ở mức tối đa của sàn.

---

## Q. Danh sách callback của Strategy

Định nghĩa trong [strategy/interface.py](../freqtrade/strategy/interface.py). Mọi callback được bọc
`strategy_safe_wrapper`: nếu lỗi thì bot log và dùng giá trị mặc định, không crash.

| Callback | Khi nào gọi (live) | Ghi chú |
|---|---|---|
| `bot_start()` | 1 lần trong `FreqtradeBot.__init__` | Backtest: 1 lần khi nạp strategy |
| `bot_loop_start()` | Đầu mỗi `process()`, sau khi tải nến, trước analyze | Backtest: mỗi mốc thời gian |
| `informative_pairs()` / `@informative` | Khi tải nến | Cặp/khung phụ |
| `populate_indicators()` | Analyze (mỗi nến mới) | Hyperopt: **chỉ chạy 1 lần** cho cả quá trình |
| `populate_entry_trend()` | Analyze | Hyperopt: chạy lại mỗi epoch |
| `populate_exit_trend()` | Analyze | Hyperopt: chạy lại mỗi epoch |
| `custom_entry_price()` | Vào lệnh, trước leverage/stake | Không gọi khi đặt lại lệnh (replace) |
| `leverage()` | Vào lệnh đầu (futures) | Bị bỏ qua nếu `/forceenter` gửi kèm leverage |
| `custom_stake_amount()` | Vào lệnh đầu | Không gọi cho DCA |
| `confirm_trade_entry()` | Ngay trước khi đặt lệnh vào đầu | Không gọi cho DCA / replace |
| `order_filled()` | Mỗi khi một lệnh khớp | |
| `check_entry_timeout()` / `check_exit_timeout()` | Lệnh chưa khớp, mỗi vòng | Bổ sung cho `unfilledtimeout` |
| `adjust_order_price()` (`adjust_entry_price` / `adjust_exit_price`) | Lệnh chưa khớp khi có nến mới | None = hủy |
| `custom_stoploss()` | Mỗi vòng cho trade mở (khi `use_custom_stoploss`), và sau khi khớp nếu có tham số `after_fill` | Trả về khoảng cách so với giá hiện tại |
| `custom_roi()` | Khi `use_custom_roi` | Lấy min với bảng ROI |
| `custom_exit()` | Mỗi vòng, chỉ khi `use_exit_signal` và không có exit signal | |
| `custom_exit_price()` | Thoát bằng lệnh limit | |
| `confirm_trade_exit()` | Ngay trước khi đặt lệnh thoát | Không gọi cho thanh lý / thoát một phần |
| `adjust_trade_position()` | Mỗi vòng khi `position_adjustment_enable` | DCA / thoát một phần |
| `plot_annotations()` | API `/pair_candles`, plot | Vẽ chú thích |
| `version()` | Log heartbeat | |

FreqAI đã bị gỡ hoàn toàn (kể cả các hook `feature_engineering_*`, `set_freqai_targets`): config còn bật
`freqai.enabled` sẽ bị từ chối ngay khi kiểm tra config (xem [trim-log.md](trim-log.md)).

Tên cũ vẫn tương thích ngược: `populate_buy_trend`/`populate_sell_trend`, `custom_sell`, `check_buy_timeout`/`check_sell_timeout`.

---

## R. Backtesting

`freqtrade backtesting` — không có vòng lặp thời gian thực ([optimize/backtesting.py](../freqtrade/optimize/backtesting.py)):

```
1. Nạp dữ liệu theo --timerange, lùi thêm startup_candle_count nến để "làm nóng" chỉ báo
   (informative: lùi startup_candle_count nến của CHÍNH khung đó); nạp thêm dữ liệu
   --timeframe-detail nếu có
2. Pairlist chạy 1 lần (pairlist động phải tương thích backtest)
3. Mỗi cặp: ft_advise_signals() chạy MỘT LẦN trên toàn bộ dataframe (vectorized),
   cắt bỏ phần startup, rồi DỊCH tín hiệu xuống 1 nến (shift(1))
4. Duyệt theo thời gian; ở mỗi mốc, duyệt các cặp theo thứ tự whitelist → backtest_loop():
     a. quản lý lệnh chưa khớp (timeout/replace)
     b. vào lệnh nếu có tín hiệu, còn slot, cặp không bị khóa (chỉ trên nến chính)
     c. khớp lệnh vào
     d. kiểm tra thoát (should_exit với low/high của nến; nến detail nếu có)
     e. khớp lệnh thoát → nếu --enable-protections thì chạy protections
5. Funding fee (futures) tính theo dữ liệu funding_rate
6. Hết dữ liệu → các lệnh còn mở bị đóng với lý do force_exit ở giá open của nến cuối
7. Báo cáo + lưu file .zip vào user_data/backtest_results/
```

### Mô hình khớp lệnh trong backtest
| Tình huống | Giá khớp |
|---|---|
| Vào lệnh | Giá đề xuất = **open của nến sau nến tín hiệu** (hoặc `custom_entry_price`, kẹp trong [low, high]) |
| Lệnh limit | Khớp nếu giá nằm trong `[low, high]` của nến — **chạm là khớp** ([backtesting.py:787](../freqtrade/optimize/backtesting.py#L787)) |
| Stoploss | Đúng giá stop; nếu nến mở gap qua stop → giá open |
| Trailing kích hoạt ngay nến vào lệnh | Giả định bi quan nhất |
| ROI | Giá đạt mức ROI trong nến |
| Exit signal / custom exit | Open của nến kế tiếp (hoặc `custom_exit_price` kẹp trong [low, high]) |

### Backtest khác live / dry-run
| Điểm | Backtest | Live / dry-run |
|---|---|---|
| Trượt giá | **Không có** (chỉ có phí) | Dry-run: market đi qua orderbook; live: thực tế |
| Lệnh limit | Chạm giá là khớp | Dry-run: phải cắt qua orderbook; live: phụ thuộc hàng đợi |
| Thứ tự trong nến | Không biết giá đi high hay low trước → dùng `--timeframe-detail` để giảm sai lệch | Theo thời gian thực |
| Protections | Tắt, trừ khi có `--enable-protections` | Luôn bật nếu khai báo |
| Phí | Lấy từ sàn hoặc `--fee` | Thực tế |

---

## S. Hyperopt

[optimize/hyperopt/](../freqtrade/optimize/hyperopt/)
```
1. Nạp dữ liệu + chạy advise_all_indicators() MỘT LẦN → lưu pickle   (populate_indicators chỉ chạy 1 lần!)
2. Mỗi epoch (song song bằng joblib, -j):
     Optuna (mặc định NSGAIIISampler; có thể đổi sang TPE, GP, CmaEs, NSGA-II, QMC
     qua generate_estimator) đề xuất bộ tham số
     → gán tham số → populate_entry_trend / populate_exit_trend → backtest
     → hàm loss (hyperopt_loss_*.py) → báo kết quả lại cho Optuna
3. Kết thúc: in epoch tốt nhất, ghi kết quả vào .fthypt,
   và xuất tham số ra <Strategy>.json cạnh file strategy (dùng tự động cho lần chạy sau)
```
- Tham số dùng trong `populate_indicators()` **không đổi theo epoch**. Muốn tối ưu chu kỳ chỉ báo thì phải tính sẵn mọi biến thể trong `populate_indicators()` rồi chọn trong `populate_entry_trend()`.
- 12 hàm loss có sẵn ([hyperopt_loss/](../freqtrade/optimize/hyperopt_loss/)): Sharpe, SharpeDaily, Sortino, SortinoDaily, Calmar, MaxDrawDown, MaxDrawDownRelative, MaxDrawDownPerPair, OnlyProfit, ProfitDrawDown, MultiMetric, ShortTradeDur. Hàm riêng: kế thừa `IHyperOptLoss`, đặt trong `user_data/hyperopts/`.

---

## T. Lookahead / Recursive analysis

| Lệnh | Mục đích |
|---|---|
| `lookahead-analysis` | Chạy backtest đầy đủ rồi so với backtest bị cắt tại từng tín hiệu → phát hiện chỉ báo/tín hiệu "nhìn trước tương lai" |
| `recursive-analysis` | So giá trị chỉ báo khi dùng các `startup_candle_count` khác nhau → phát hiện chỉ báo chưa hội tụ (EMA, RSI...) |

Code: [optimize/analysis/](../freqtrade/optimize/analysis/)

---

## U. Producer / Consumer

- Bot **producer** phát qua WebSocket: whitelist và dataframe đã phân tích (kèm cột tín hiệu).
- Bot **consumer** ([external_message_consumer.py](../freqtrade/rpc/external_message_consumer.py)) nhận và lưu vào DataProvider. Có thể xóa cột tín hiệu bằng `remove_entry_exit_signals`.
- Consumer **không tự trade theo tín hiệu producer**. Strategy của consumer phải tự đọc `self.dp.get_producer_df(pair)` rồi đặt `enter_long`... trong `populate_entry_trend`.
- Pairlist `ProducerPairList` dùng whitelist của producer.

---

## V. Những điểm hay hiểu nhầm

| Hiểu nhầm | Thực tế trong code |
|---|---|
| Protections được kiểm tra trước mỗi lệnh vào | Protections chỉ chạy **sau khi một lệnh đóng** và tạo lock; lúc vào lệnh chỉ kiểm tra lock |
| Trade được tạo khi lệnh khớp | Trade được tạo **khi đặt lệnh** (amount = 0); lệnh vào bị hủy không khớp → Trade bị xóa |
| Stoploss được ưu tiên hơn exit signal | Danh sách lý do thoát: exit signal/custom_exit → stoploss → ROI → trailing |
| `custom_exit()` luôn chạy | Chỉ chạy khi `use_exit_signal = True` và không có exit signal |
| Callback vào lệnh: leverage → stake → giá | `custom_entry_price → leverage → custom_stake_amount → confirm_trade_entry` |
| Backtest có tính trượt giá | Không; lệnh limit chạm là khớp → kết quả lạc quan hơn thực tế |
| `/stop` vẫn quản lý lệnh mở | Không; dùng `/stopentry` (= PAUSED) |
| Strategy khai báo `timeframe` là đủ | Config có `timeframe` sẽ ghi đè strategy |
| `populate_indicators` chạy lại mỗi epoch hyperopt | Chỉ chạy 1 lần |
| `/forceenter` an toàn như tín hiệu strategy | Bỏ qua locks/protections; `leverage` gửi kèm bỏ qua callback |
| Consumer tự trade theo producer | Phải tự đọc `dp.get_producer_df()` |
| `confirm_trade_exit()` không ảnh hưởng stoploss | Trả về False chặn được cả thoát do stoploss phía bot (trừ thanh lý) |
