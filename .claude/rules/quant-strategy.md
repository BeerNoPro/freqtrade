# Luật phát triển strategy (quant)

Thiết kế gốc: [docs_vn/signal-bot-workflow.md](../../docs_vn/signal-bot-workflow.md). Logic engine: [docs_vn/lifecycle.md](../../docs_vn/lifecycle.md).

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

## Báo cáo
- Luôn báo **kết quả thật**, kể cả khi không đạt. Không chọn lọc giai đoạn đẹp.
- Ghi rõ giả định: phí, khoảng thời gian, danh sách cặp (có survivorship bias hay không).
- Lưu báo cáo vào `user_data/reports/` (không commit).
