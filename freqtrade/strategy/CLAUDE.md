# CLAUDE.md — `freqtrade/strategy/`

Đây là **trái tim logic giao dịch**. Người dùng phát triển bot chủ yếu kế thừa `IStrategy` và override các hook ở đây.

## File chính

- [interface.py](interface.py) — lớp `IStrategy` (~78KB), định nghĩa toàn bộ hook & contract. **Đọc file này để biết signature chính xác.**
- [hyper.py](hyper.py), [parameters.py](parameters.py) — tham số tối ưu được (`IntParameter`, `DecimalParameter`...) dùng cho hyperopt.
- [informative_decorator.py](informative_decorator.py) — decorator `@informative()` lấy dữ liệu timeframe/cặp khác.
- [strategy_helper.py](strategy_helper.py) — helper như `merge_informative_pair`, `stoploss_from_open`.
- [strategy_wrapper.py](strategy_wrapper.py) — `strategy_safe_wrapper` bọc lỗi callback của user.

## Các hook quan trọng (override trong strategy của bạn)

**Bắt buộc / cốt lõi:**
- `populate_indicators(dataframe, metadata)` — tính chỉ báo (RSI, EMA, MACD...) thêm vào dataframe.
- `populate_entry_trend(dataframe, metadata)` — set cột `enter_long` / `enter_short`.
- `populate_exit_trend(dataframe, metadata)` — set cột `exit_long` / `exit_short`.

**Callback tùy biến (tùy chọn, nâng cao):**
- `custom_stoploss(...)` — stoploss động theo lợi nhuận/thời gian.
- `custom_exit(...)` — điều kiện thoát tùy biến (ngoài tín hiệu trend).
- `custom_entry_price(...)` / `custom_exit_price(...)` — giá đặt lệnh tùy biến.
- `confirm_trade_entry(...)` / `confirm_trade_exit(...)` — chốt chặn cuối trước khi đặt lệnh.
- `adjust_trade_position(...)` — DCA / position adjustment (cần `position_adjustment_enable = True`).
- `leverage(...)` — đặt đòn bẩy cho futures.
- `bot_loop_start(...)` — chạy đầu mỗi vòng lặp.

## Lưu ý khi viết / sửa strategy

- Thuộc tính lớp quan trọng: `timeframe`, `minimal_roi`, `stoploss`, `trailing_stop`, `can_short`, `process_only_new_candles`, `startup_candle_count`.
- **Vector hóa**: populate_* chạy trên cả dataframe — dùng phép toán pandas/numpy, tránh vòng lặp từng dòng.
- **Tránh lookahead bias**: không dùng dữ liệu nến tương lai; cẩn thận với `.shift()`, indicator dùng dữ liệu chưa đóng nến.
- File strategy của user nằm ở `user_data/strategies/`, kế thừa `IStrategy`. Mẫu tham khảo: [../templates/sample_strategy.py](../templates/sample_strategy.py).
- Sau khi đổi interface, kiểm tra test ở `tests/strategy/`.
