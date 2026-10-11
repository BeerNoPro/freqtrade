---
paths:
  - "user_data/strategies/**"
  - "user_data/hyperopts/**"
  - "docs/signal-bot-workflow.md"
  - "freqtrade/strategy/**"
  - "freqtrade/optimize/**"
---

# Luật phát triển strategy (quant)

Thiết kế gốc: [docs/signal-bot-workflow.md](../../docs/signal-bot-workflow.md). Logic engine: [docs/lifecycle.md](../../docs/lifecycle.md).

## Viết strategy
- File strategy nằm trong `user_data/strategies/`, kế thừa `IStrategy`, `INTERFACE_VERSION = 3`.
- Chỉ dùng dữ liệu nến **đã đóng**; không dùng `shift(-n)`, không dùng giá trị của cả dataframe (`df["x"].max()`, `.mean()` toàn cục) trong tín hiệu.
- Khung khác (4h, BTC, open interest) đưa vào bằng `@informative(...)`, không tự merge thủ công.
- Import qtpylib từ `technical`, không dùng `freqtrade.vendor.qtpylib` (đã cũ).
- Tham số hyperopt tối đa 5 cái; đòn bẩy và rủi ro mỗi lệnh là chính sách cố định, **không** hyperopt.
- Mọi callback có gọi mạng (`dp.orderbook`, `dp.funding_rate`) chỉ chạy khi `self.dp.runmode` là `live`/`dry_run`, và phải tự bắt lỗi (trả về giá trị an toàn).
- Nhớ: config ghi đè thuộc tính strategy (`timeframe`, `stoploss`, `minimal_roi`...).

## Kiểm chứng (bắt buộc trước khi merge vào `develop`)
1. `lookahead-analysis` và `recursive-analysis` không báo lỗi.
2. Backtest với `--fee 0.0006`, `--timeframe-detail` (nếu có dữ liệu), `--enable-protections`, có `--breakdown month`.
3. Phát triển trên dải IS; dải holdout (OOS) chỉ chạy **một lần** khi người dùng duyệt bộ tham số cuối. Không chỉnh tham số theo kết quả OOS.
4. Ngưỡng đạt trên OOS: ≥ 150 tín hiệu, profit factor ≥ 1.3, kỳ vọng ≥ +0.2R/tín hiệu, drawdown ≤ 15R, không cặp nào > 30% lợi nhuận.

## Dải dữ liệu
- Chỉ đánh giá trên dữ liệu gần: **2025-01-01 → nay**. Dữ liệu cũ hơn chỉ dùng để làm nóng chỉ báo hoặc cho điều kiện
  lịch sử của danh mục. Dải IS/holdout hiện hành nằm trong `user_data/research/thresholds.json`.
- Mẫu ngắn → luôn báo CI bootstrap và nói rõ độ tin cậy thấp.

## Strategy cấp danh mục (tỷ trọng theo ngày, vol-targeting, long-short)
Cổng R/lệnh ở trên không áp dụng được cho strategy giữ vị thế dài và tái cân bằng. Dùng cổng danh mục (backtester
`scripts/research/mce/portfolio.py` trên danh mục point-in-time `mce.universe.point_in_time_members`, sau đó port sang
Freqtrade và đối chiếu):
- Sharpe sau phí + funding ≥ 1,0, **cận dưới CI bootstrap khối 20 ngày (5%) > 0**, và cao hơn BTC mua-giữ có
  vol-target 25%.
- Max drawdown ≤ 35%; dương ở ≥ 2/3 số quý; không coin nào > 30% lợi nhuận.
- Vẫn dương khi phí 0,10%/chiều; Deflated Sharpe ≥ 0,5 với số lần thử cộng dồn trong `user_data/research/STATE.md`.
- Không dùng danh sách coin chọn theo hiện tại (survivorship bias làm phồng Sharpe khoảng 0,5 trong lần kiểm tra trước).
- Luôn báo kèm: mua-giữ thường, mua-giữ có vol-target, turnover, exposure trung bình.

## Báo cáo
- Luôn báo **kết quả thật**, kể cả khi không đạt. Không chọn lọc giai đoạn đẹp.
- Ghi rõ giả định: phí, khoảng thời gian, danh sách cặp (có survivorship bias hay không).
- Lưu báo cáo vào `user_data/reports/` (không commit).
