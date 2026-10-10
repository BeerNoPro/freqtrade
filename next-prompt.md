# Prompt: Nghiên cứu "bộ máy nhận định thị trường" (Market Context Engine)

> Gợi ý: bật `/effort high` cho phiên này (nghiên cứu + thiết kế), bắt đầu bằng **plan mode**.

## Vai trò
Bạn là **quant researcher + kỹ sư Python** trong repo này (Freqtrade 2026.9 đã cắt gọn, bot tín hiệu Binance USDT-M
Futures qua Telegram, dry-run). Phiên này là **nghiên cứu và thiết kế**, kèm một prototype đo lường nhỏ.
**Chưa** viết strategy giao dịch hoàn chỉnh.

## Vấn đề người dùng đặt ra
"Code công thức tín hiệu nào cũng chỉ test được trên dữ liệu cũ, còn dữ liệu mới rất khó đoán." Người dùng muốn
chuyển sang hướng: bot **nhận định thị trường** dựa trên chỉ số, diễn biến nến và hành vi giá. Sau đó xây dựng một
**bộ hook công thức + kinh nghiệm** (các mô hình phán đoán giá có tên, có thống kê), dùng để đánh giá thị trường và
về sau làm bộ lọc/ngữ cảnh cho tín hiệu.

### Khung tư duy quant (phải giữ trong suốt phiên)
- Đổi sang "nhận định" **không** thoát được vấn đề dữ liệu mới: mọi phán đoán vẫn phải kiểm trên dữ liệu chưa thấy.
  Những gì thay đổi:
  1. Đầu ra là **trạng thái + xác suất có điều kiện + mức vô hiệu**, không phải lệnh mua/bán.
  2. Đo chất lượng bằng **độ hiệu chuẩn xác suất** (Brier, reliability, skill so với base rate), không chỉ lợi nhuận backtest.
  3. Có **nhật ký dự báo tiến (forward journal)**: mỗi nhận định được ghi lại *trước khi* biết kết quả, rồi chấm điểm
     tự động. Đây là cách trung thực duy nhất để biết bot đúng đến đâu trên dữ liệu mới.
  4. "Kinh nghiệm" = **thống kê có điều kiện** của từng hook, cập nhật cuốn chiếu, có theo dõi suy giảm và quy tắc loại bỏ.
- Ưu tiên giả thuyết có cơ sở (động lượng, co giãn biến động, quá đông vị thế qua funding/OI, thanh khoản) hơn mẫu hình
  tìm ra bằng cách quét dữ liệu. Mẫu hình "nhìn bằng mắt" (vai-đầu-vai, cờ...) chỉ đưa vào khi định nghĩa được bằng
  thuật toán và qua kiểm định như mọi hook khác.

## Luật bắt buộc
- Đọc `CLAUDE.md`, `.claude/rules/` (gồm `quant-strategy.md`). Trả lời bằng **tiếng Việt**.
- Commit trên `develop`, message tiếng Anh (Conventional Commits), **không** có `Co-Authored-By`/ghi công AI.
  **Không push**, không PR. Kiểm tra trước commit bằng `.venv/Scripts/python.exe scripts/gate.py`, commit theo skill `/commit`.
- Luôn `dry_run: true`; không secret trong code/docs; không sửa core `freqtrade/` (nếu thật cần → hỏi trước).
- **Repo công khai**: tham số/ngưỡng riêng, kết quả nghiên cứu, danh sách hook đã kiểm chứng là tài sản riêng → để trong
  `user_data/` (gitignore) trừ khi người dùng đồng ý commit. Báo cáo lưu `user_data/reports/` (không commit).
- Tiết kiệm token: chỉ đọc mục liên quan của `docs/*.md`; output lớn ghi ra file rồi đọc phần cần; dùng gate.py.

## Hiện trạng source & dữ liệu (đã kiểm tra 2026-10-10 — xác minh lại trước khi dựa vào)
- Engine chỉ còn họ Binance. Đã xóa: FreqAI, plot, orderflow (dữ liệu trades/delta), edge, external consumer.
  **Không thêm lại**. Nếu cần ML → module riêng trong `user_data/` hoặc script nghiên cứu, thư viện nhẹ.
- Dữ liệu engine có: `CandleType` = futures, mark, index, premiumIndex, funding_rate, open_interest
  (Binance chỉ giữ ~30 ngày lịch sử OI). DataProvider: `get_pair_dataframe`, `get_analyzed_dataframe`, `funding_rate`,
  `orderbook`, `ticker`, `send_msg`; khung phụ qua `@informative`. OHLCV của ccxt chỉ có 6 cột (không có taker buy volume).
- Thư viện có sẵn: pandas 3.0.6, numpy 2.4.6, scipy 1.17.1, TA-Lib 0.8.0, technical 1.7.0, optuna 5.0.0.
  **Không có** scikit-learn (đã gỡ vì không dùng), statsmodels, hmmlearn, lightgbm. Thêm thư viện: nêu lý do →
  `uv pip install --python .venv/Scripts/python.exe ...` → thêm vào requirements → `pip-audit` sạch.
  Không dùng torch/deep learning.
- Dữ liệu local: chỉ BTC/ETH **spot** 1h/5m. **Chưa có dữ liệu futures** → cần tải
  (`download-data --config user_data/config_futures.json --timeframes 1h 4h --timerange 20221101-`, thêm
  `--candle-types funding_rate mark index` nếu cần).
- Thiết kế hiện có: `docs/signal-bot-workflow.md` (trend-following 1h/4h, điểm tín hiệu 0–100, cổng rủi ro, ngưỡng
  kiểm chứng mục 7). `FuturesSignalStrategy` chưa viết. Công cụ: `lookahead-analysis`, `recursive-analysis`,
  backtesting, skill `/validate-strategy`.

## Câu hỏi nghiên cứu phải trả lời
1. **Kiến trúc**: đề xuất các lớp và ranh giới:
   - Features: chỉ số; cấu trúc nến (thân/râu, range so với ATR, gap); hành vi giá (swing, phá vỡ, nhịp hồi,
     co giãn biến động); phái sinh (funding, basis mark-index/premiumIndex, OI); bối cảnh BTC.
   - Regime: trạng thái thị trường (xu hướng/đi ngang/biến động cao/sốc) cho BTC và từng cặp.
   - Hooks/Playbook: các "kinh nghiệm" có tên.
   - Assessment: tổng hợp → nhận định xác suất + kịch bản + mức vô hiệu.
   - Journal: ghi và chấm điểm.
2. **Định nghĩa hook**: interface Python gồm tên, phiên bản, giả thuyết, feature/khung cần dùng, `condition(df)` chỉ
   dùng nến đã đóng, hướng, horizon, điều kiện vô hiệu, trạng thái (`candidate`/`active`/`retired`), ≤ 3 tham số.
   Registry và cách đánh phiên bản (đổi logic = phiên bản mới, giữ thống kê cũ).
3. **Đo "kinh nghiệm" (event study)**:
   - Với mỗi lần hook kích hoạt: phân phối lợi suất tương lai ở nhiều horizon (ví dụ 1/4/12/24 nến 1h), chuẩn hóa theo
     ATR (R); nhãn triple-barrier (TP = k·ATR, SL = m·ATR, giới hạn thời gian); MFE/MAE.
   - So với baseline cùng regime → **lift**. Khoảng tin cậy bằng block bootstrap (vì tự tương quan và nhãn chồng lấp).
   - Đặt số mẫu tối thiểu; hiệu chỉnh kiểm định đa giả thuyết (Benjamini–Hochberg hoặc tương đương); kiểm tra
     độ ổn định theo năm, theo cặp, theo regime; tính sau phí 0,06%/chiều.
4. **Regime**:
   - Bắt đầu bằng luật minh bạch: độ dốc EMA/ADX, percentile biến động thực, trạng thái BTC. Kiểm tra các regime có
     thật sự khác nhau về lợi suất/biến động tương lai không.
   - Chỉ thử mô hình thống kê (HMM 2–3 trạng thái, clustering) nếu luật không đủ.
   - **Bắt buộc causal**: chỉ lọc tiến (filtering). Không dùng Viterbi/smoothing trên toàn chuỗi, vì đó là lookahead.
5. **Tổng hợp nhận định**:
   - Cách kết hợp nhiều hook thành xác suất: đếm có trọng số, logistic đơn giản hoặc cập nhật Bayes.
   - Hiệu chuẩn bằng Brier, log loss, reliability diagram và **skill score so với base rate**.
   - Meta-labeling (mô hình thứ hai quyết định có tin hook hay không) chỉ là bước sau.
6. **Kiểm chứng trên dữ liệu mới**:
   - Walk-forward cuốn chiếu có purge + embargo theo horizon của nhãn.
   - Holdout cuối chỉ chạy **một lần**; ghi hash của danh sách hook và tham số trước khi chạy.
   - Thiết kế forward journal: schema, chỗ lưu (sqlite/parquet trong `user_data/`), job chấm điểm.
   - Theo dõi drift: hit rate và Brier cuốn chiếu; quy tắc retire hook.
7. **Tích hợp Freqtrade** (không sửa core): so sánh hai cách rồi chọn một.
   - (a) Tính feature/regime trong `populate_indicators` (informative 4h + BTC), gửi nhận định qua `dp.send_msg`
     chỉ khi có nến mới và chỉ ở live/dry-run.
   - (b) Chạy nghiên cứu ngoài engine bằng script đọc file feather; engine chỉ dùng hook đã được duyệt.
   - Quan hệ với tín hiệu: nhận định trở thành tầng ngữ cảnh/bộ lọc cho `signal-bot-workflow.md`.
8. **Dữ liệu phái sinh lịch sử ngắn**:
   - Binance chỉ giữ ~30 ngày cho OI, long/short ratio, taker buy/sell volume (endpoint public futures data, gọi qua
     ccxt implicit API, không cần key). Xác minh endpoint và giới hạn.
   - Đề xuất **collector** tự lưu dữ liệu này từ bây giờ, vì càng bắt đầu muộn càng ít lịch sử để kiểm định.
   - Nghiên cứu cho thấy các số liệu này yếu khi đứng riêng, có ích hơn khi kết hợp (funding + OI + giá) → kiểm định,
     không tin mặc định.
9. **Rủi ro thống kê**: data snooping, survivorship (danh sách cặp cố định), không dừng (non-stationary), nhãn chồng
   lấp, look-ahead, phí/trượt giá, đứt gãy regime. Mỗi rủi ro → biện pháp cụ thể trong thiết kế.

## Kết quả cần giao (deliverables)
1. **Báo cáo nghiên cứu** (`user_data/reports/market-context-research.md`): tóm tắt tài liệu kèm nguồn (regime
   detection, triple-barrier/meta-labeling, purged CV, calibration, bằng chứng về funding/OI/basis), và kết luận
   áp dụng vào bot này.
2. **Bảng kiểm kê feature**: feature → nguồn dữ liệu → độ dài lịch sử → độ trễ → dùng được trong backtest không →
   chi phí tính.
3. **Tài liệu thiết kế** `docs/market-context-engine.md`: kiến trúc, interface hook, event study, regime, assessment,
   journal, kiểm chứng, tích hợp, lộ trình. Chỉ phần generic; ngưỡng riêng để trong `user_data/`.
4. **Prototype tối thiểu** (vị trí do người dùng chọn):
   - Engine event study + registry hook + **3–5 hook mẫu chọn từ lý thuyết** (không mò từ dữ liệu), chạy trên dải IS
     2023-01 → 2025-06 cho 10–20 cặp thanh khoản cố định.
   - Báo cáo kết quả thật, kể cả âm.
   - Test tự động cho tính causal: giá trị hook tại t không đổi khi thêm dữ liệu sau t.
5. **Plan các phase tiếp theo** với tiêu chí go/no-go rõ ràng; cập nhật `docs/signal-bot-workflow.md` (lộ trình,
   quan hệ nhận định ↔ tín hiệu).

## Ngưỡng khởi điểm (đề xuất lại nếu có lý do)
- Hook được `active` khi:
  - ≥ 200 sự kiện IS;
  - lift so với baseline có CI 95% không chứa 0 sau hiệu chỉnh đa kiểm định;
  - cùng dấu ở ≥ 2/3 số năm và ≥ 60% số cặp;
  - vẫn dương sau phí 0,12% khứ hồi.
- Assessment: Brier skill score > 0 so với base rate trên walk-forward OOS; reliability lệch ≤ 5 điểm %.
- Forward journal: ≥ 4 tuần và ≥ 300 nhận định trước khi dùng làm bộ lọc tín hiệu.
- Retire hook: skill cuốn chiếu 60 ngày < 0 có ý nghĩa thống kê.

## Quyết định cần hỏi người dùng (AskUserQuestion, trong plan mode)
- Code nghiên cứu và knowledge base đặt ở đâu: `user_data/` (riêng tư) hay `scripts/research/` (công khai)?
- Thư viện được thêm (ví dụ statsmodels/scikit-learn/hmmlearn) hay chỉ dùng numpy/scipy/pandas?
- Danh sách cặp, khoảng dữ liệu, và có dựng collector OI/long-short/taker volume ngay không?
- Kênh xuất nhận định: tin Telegram riêng, tổng hợp theo giờ, hay chỉ ghi journal ở giai đoạn đầu?

## Không làm trong phiên này
- Không viết `FuturesSignalStrategy` hoàn chỉnh, không bật Telegram thật, không đổi `dry_run`.
- Không thêm lại FreqAI, không deep learning, không hyperopt hàng loạt tham số hook.
- Không chạy holdout OOS nhiều lần; không chọn giai đoạn đẹp để báo cáo.

## Tài liệu tham khảo khởi đầu (đã tra 2026-10-10, kiểm lại khi dùng)
- López de Prado, *Advances in Financial Machine Learning*: triple-barrier, meta-labeling, purged/embargoed CV —
  tóm tắt: https://www.tradinginterview.com/courses/machine-learning/lessons/labeling-the-triple-barrier-method-and-meta-labeling/ ,
  https://www.luxalgo.com/library/concept/purged-cross-validation/
- Regime crypto bằng path signatures trên basis perp-spot + funding (2026):
  https://nhsjs.com/wp-content/uploads/2026/08/Path-Signatures-for-Regime-Detection-in-Cryptocurrency-Markets-A-Rough-Path-Framework-Using-Spot-Perpetual-Basis-and-Funding-Rates.pdf
- Dự báo giá crypto bằng ML (tổng quan 2025): https://arxiv.org/pdf/2508.01419
- Calibration / Brier decomposition: https://api.emergentmind.com/topics/brierlm
- Long/short ratio + funding + OI chỉ hữu ích khi kết hợp: https://mudrex.com/learn/binance-ratio-avoid-bull-bear-traps/
