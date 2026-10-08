# CLAUDE.md — `freqtrade/optimize/`

Mô phỏng & tối ưu chiến lược dựa trên dữ liệu lịch sử. Không đặt lệnh thật.

## File / thư mục chính

- [backtesting.py](backtesting.py) — engine **backtesting** (~78KB): chạy lại strategy trên OHLCV lịch sử, mô phỏng vào/ra lệnh và phí (không mô phỏng trượt giá). Đây là lõi của `freqtrade backtesting`.
- `hyperopt/` — **hyperopt** dựa trên Optuna: tìm bộ tham số tối ưu cho strategy (các `*Parameter` khai báo trong strategy).
- `hyperopt_loss/` — hàm mục tiêu (Sharpe, Sortino, profit...). Người dùng có thể viết loss riêng.
- `optimize_reports/` — sinh bảng kết quả (lợi nhuận, drawdown, win rate...).
- `analysis/` — phân tích chi tiết entry/exit, lookahead & recursive bias check.
- [backtest_caching.py](backtest_caching.py) — cache kết quả để tránh chạy lại.
- `space/` — định nghĩa không gian tìm kiếm tham số cho hyperopt.

## Khái niệm quan trọng

- **Backtest phải khớp live**: logic ở đây cần phản ánh đúng `freqtradebot.py`. Khi sửa hành vi giao dịch, kiểm tra cả hai khớp nhau (tests/optimize so sánh điều này).
- **Lookahead / recursive bias**: dùng `freqtrade lookahead-analysis` và `recursive-analysis` để phát hiện strategy "nhìn tương lai" — nguyên nhân phổ biến khiến backtest đẹp nhưng live thua.
- Cần dữ liệu OHLCV đã tải trước (`freqtrade download-data`).
- Hyperopt dùng `optuna` (+ `cmaes` cho sampler CmaEs), `scipy`, `filelock` — đã nằm trong `requirements.txt`.

## Lệnh liên quan

```bash
freqtrade backtesting --strategy MyStrategy --timeframe 5m --timerange 20240101-20240601
freqtrade hyperopt --strategy MyStrategy --hyperopt-loss SharpeHyperOptLoss --epochs 100
freqtrade lookahead-analysis --strategy MyStrategy
freqtrade backtesting-analysis    # phân tích entry/exit chi tiết
```
