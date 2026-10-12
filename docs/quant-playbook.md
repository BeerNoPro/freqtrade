# Quant playbook: các họ mô hình có bằng chứng

Kiến thức nền cho mọi quyết định nghiên cứu/strategy trong repo. Cách làm việc: [.claude/rules/quant-mindset.md](../.claude/rules/quant-mindset.md).
Bộ máy kiểm định: [market-context-engine.md](market-context-engine.md).
Kết quả cụ thể của từng lần thử nằm trong `user_data/research/STATE.md` (riêng tư).

Mỗi họ mô hình được mô tả theo: **cơ chế** (vì sao có lợi thế) → **bằng chứng** → **cách dùng ở đây** → **bẫy**.

## 1. Trend-following / time-series momentum (ưu tiên số 1)
- **Cơ chế:** nhà đầu tư phản ứng chậm với thông tin rồi đổ xô theo (under-reaction rồi over-reaction); dòng tiền
  theo đuổi xu hướng; người phòng hộ chấp nhận trả phí. Crypto nhiều nhà đầu tư nhỏ lẻ nên hiệu ứng mạnh.
- **Bằng chứng:**
  - Moskowitz, Ooi & Pedersen (2012, JFE), "Time Series Momentum": 58 thị trường, lợi suất 12 tháng dự báo tháng sau.
  - Hurst, Ooi & Pedersen (2017, JPM), "A Century of Evidence on Trend-Following Investing": hiệu quả qua hơn 100
    năm, định cỡ theo vol mục tiêu 10%. https://fairmodel.econ.yale.edu/ec439/hurst.pdf
  - Liu & Tsyvinski (2021, RFS), "Risks and Returns of Cryptocurrency": momentum chuỗi thời gian mạnh ở tần suất
    ngày/tuần. https://www.nber.org/papers/w24877
  - **Zarattini, Pagani & Barbon (2025)**, "Catching Crypto Trends": ensemble kênh Donchian khung ngày (lookback
    5/10/20/30/60/90/150/250/360, vào khi close > max close, trailing stop tại trung điểm kênh, chỉ LONG), mỗi vị thế
    scale theo vol mục tiêu 25%/năm (độ lệch chuẩn 90 ngày), đòn bẩy ≤ 2x, danh mục top coin thanh khoản. Tác giả báo
    Sharpe sau phí > 1,5 (số liệu của tác giả, chưa kiểm chứng độc lập).
    https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/ ,
    https://www.cxoadvisory.com/technical-trading/crypto-asset-trend-following-strategies/
- **Cách dùng:** khung **ngày** (phí nhỏ so với biên độ), **ensemble nhiều lookback** (không chọn một lookback "tốt
  nhất"), danh mục nhiều coin, định cỡ theo biến động. Kỳ vọng: winrate 30–45%, lợi nhuận đến từ vài sóng lớn, có giai
  đoạn sụt giảm kéo dài khi thị trường đi ngang.
- **Bẫy:** khung nhỏ (1h–4h) bị phí ăn mòn; trailing quá chặt cắt mất sóng lớn; tối ưu lookback theo dữ liệu;
  so sánh với mua-và-giữ trong thị trường tăng mà quên so rủi ro (drawdown, vol).

## 2. Định cỡ theo biến động (volatility targeting)
- **Cơ chế:** biến động tụ cụm và dự báo được tốt hơn hẳn lợi suất. Giảm vị thế khi biến động cao giúp giữ rủi ro ổn
  định, và thường cắt bớt rủi ro trong các cú sập (biến động tăng khi giá giảm).
- **Bằng chứng:** Moreira & Muir (2017, JF), "Volatility-Managed Portfolios"; Harvey et al. (2018, JPM), "The Impact
  of Volatility Targeting".
- **Cách dùng:** tỷ trọng = vol mục tiêu / vol thực hiện (ví dụ độ lệch chuẩn 90 ngày, annualized), có trần đòn bẩy.
  Áp dụng cho cả mốc so sánh (mua-giữ có vol-target) để so công bằng.
- **Bẫy:** dùng vol tính cả dữ liệu tương lai; trần đòn bẩy quá cao.

## 3. Carry / funding / basis
- **Cơ chế:** người muốn đòn bẩy long trả funding cho người cung cấp. Funding dương kéo dài là phần bù cho bên bán.
- **Bằng chứng:** Schmeling, Schrimpf & Todorov (2023, BIS WP 1087), "Crypto Carry": carry crypto cao, biến động mạnh,
  dự báo được rủi ro sập.
- **Cách dùng:** chủ yếu là **thước đo đông vị thế** (funding cực đoan → rủi ro đảo chiều/thanh lý). Chiến lược
  delta-neutral (long spot, short perp) là hướng khác, không phải bot tín hiệu.
- **Bẫy:** funding đứng riêng dự báo hướng giá yếu; quên chi phí funding khi giữ vị thế futures lâu. Long-short cắt
  ngang theo funding (long coin funding thấp, short coin funding cao) thu được funding thật nhưng bị giá ăn ngược:
  coin funding âm thường tiếp tục giảm (xem mục 10).

## 4. Momentum cắt ngang (cross-sectional)
- **Cơ chế:** coin mạnh hơn nhóm tiếp tục mạnh hơn trong ngắn/trung hạn.
- **Bằng chứng:** Liu, Tsyvinski & Wu (2022, JF), "Common Risk Factors in Cryptocurrency": momentum là một nhân tố
  chính; momentum 1/2/3/4 tuần đều có ý nghĩa trên mẫu 2014–2018 gồm cả coin nhỏ; nhân tố CMOM = top 30% − bottom 30%
  theo lợi suất 3 tuần, tái cân bằng tuần. Fičura (2023, working paper): coin lớn, thanh khoản có momentum tuần, coin
  nhỏ thì đảo chiều. Phản chứng: Grobys & Huhta-Halkola thấy momentum cắt ngang yếu khi bỏ coin nhỏ, kém thanh khoản.
- **Cách dùng:** xếp hạng coin theo lợi suất 1–4 tuần. Luôn **trung lập beta** (trong thị trường giảm, coin thắng
  thường beta thấp, coin thua beta cao, nên rổ trung lập đô la thực chất đang short thị trường) và tính funding.
- **Bẫy:** danh mục nhỏ (15–20 coin) cho ít độ phân tán; survivorship bias nặng nếu chọn coin theo hiện tại; chân short
  trên coin thua thường phải trả funding âm và dễ bị squeeze khi đông người short. Kiểm định trong repo: xem mục 10.

## 5. Mean-reversion ngắn hạn (ưu tiên thấp)
- **Cơ chế:** thanh khoản tạm thời mất cân bằng rồi hồi lại.
- **Bằng chứng:** yếu và không ổn định ở crypto lớn. Chủ yếu tồn tại ở tần suất rất cao, nơi cá nhân không cạnh
  tranh được.
- **Bẫy:** **bẫy winrate**: thắng 55–60% số lần nhưng lỗ tổng vì lệnh thua lớn; nguy hiểm khi có đòn bẩy.
- **Biến thể có cơ chế: hồi sau bán cưỡng bức.** Lệnh thanh lý không quan tâm giá, đẩy giá quá đà khi thanh khoản mỏng
  (Brunnermeier & Pedersen 2009); người đứng ra nhận được trả phần bù khi giá hồi (Coval & Stafford 2007, quỹ bị rút vốn
  phải bán; Nagel 2012, lợi suất hồi quy cao nhất khi thanh khoản cạn). Nhận diện bằng cú chạy lớn kèm OI sụt bất
  thường. Bằng chứng trực tiếp trên crypto còn thiếu; sự kiện hiếm nên cần mẫu dài. Kiểm định trong repo: mục 10.

## 6. Lead-lag và ngữ cảnh BTC
- **Cơ chế:** BTC dẫn dắt; coin nhỏ phản ứng trễ.
- **Bằng chứng:** có ở coin vốn hóa nhỏ, tần suất cao. Với coin lớn ở 1h, hiệu ứng đã bị arbitrage.
- **Cách dùng:** BTC làm bộ lọc trạng thái thị trường chung (risk-on/risk-off), không làm tín hiệu vào lệnh.

## 7. Chỉ báo kỹ thuật = thước đo trạng thái
| Chỉ báo | Đo cái gì | Dùng đúng cách |
|---|---|---|
| EMA, độ dốc EMA, ADX | Có xu hướng không, mạnh cỡ nào | Bộ lọc regime, tham gia vào trọng số xu hướng |
| Donchian, phá đỉnh/đáy | Giá ở đâu so với vùng N ngày | Lõi của trend-following khung ngày (ensemble lookback) |
| ATR, độ lệch chuẩn, Bollinger width | Biến động | Định cỡ vị thế, đặt SL/TP, nhận biết nén/giãn |
| RSI, Stochastic | Đà ngắn hạn, quá mua/bán | Không dùng để bắt đáy trên coin lớn; có thể làm thước đo "đuối sức" phụ |
| MACD | Chênh lệch hai EMA (một dạng momentum) | Trùng thông tin với EMA/momentum; không thêm nhiều |
| Volume | Mức tham gia | Bộ lọc thanh khoản; tín hiệu đứng riêng yếu |
| Mẫu nến | Hình dạng một/hai nến | Gần như không có giá trị dự báo đứng riêng |

## 8. Chuẩn kiểm định
- Dải IS để phát triển, **holdout chạy một lần**, walk-forward có purge/embargo cho mô hình có fit.
- Dữ liệu phát triển: **2025 → nay** (chế độ thị trường gần nhất); thêm **khối lịch sử khóa 2023–2024**, chỉ để
  chạy một lần luật đã chốt và đòi cùng dấu ở cả hai khối. Mẫu vẫn ngắn: một năm dữ liệu ngày cho sai số chuẩn của
  Sharpe khoảng 1,0, nên luôn báo CI bootstrap và ưu tiên tín hiệu cắt ngang nhiều coin (nhiều quan sát độc lập hơn)
  hơn tín hiệu trên một tài sản.
- Danh mục coin phải **point-in-time** (chọn theo thanh khoản tại từng thời điểm, kể cả coin đã delist), không dùng
  danh sách chọn theo hiện tại.
- Đếm **mọi lần thử** trên IS. Dùng Deflated Sharpe Ratio (Bailey & López de Prado, 2014) và Probability of
  Backtest Overfitting (Bailey et al., 2017) khi đã thử nhiều biến thể.
- Luôn so với: **mua và giữ** (thường và có vol-target), **baseline** đơn giản, **vào trễ 1 nến**.
- Tính đủ phí, funding, trượt giá.
- Harvey, Liu & Zhu (2016, RFS): với nhiều phép thử, ngưỡng t hợp lý > 3,0. https://www.nber.org/papers/w20592

## 9. Bài học từ cộng đồng
- Strategy Freqtrade cộng đồng dùng hàng chục chỉ báo và tối ưu dày đặc thường có backtest rất đẹp nhưng chạy thật
  kém. Nguyên nhân là overfit: càng nhiều điều kiện và tham số thì càng dễ "học thuộc" dữ liệu cũ.
- Các quỹ trend-following thành công (CTA) dùng quy tắc **đơn giản, đa dạng thị trường, định cỡ theo rủi ro**,
  và chấp nhận những năm lỗ.

## 10. Đã kiểm định trong repo (kết luận định tính, chi tiết trong `user_data/research/STATE.md`)
- Chỉ báo cổ điển đứng riêng (1h/4h), hook ngữ cảnh 1h, trend 4h một lookback, mô hình logistic đa yếu tố 4h, carry
  funding cắt ngang, động lượng cắt ngang trung lập beta (top-20): **không có lợi thế sau phí**. Mean-reversion dính
  bẫy winrate; gộp nhiều tín hiệu yếu không tạo ra dự báo hướng; dữ liệu vị thế (OI, long/short) gần như không đóng góp.
- Regime theo luật tách được **biên độ** tương lai, không tách được hướng → dùng để định cỡ, không để đoán chiều.
- Funding âm phản ánh xu hướng giảm đúng chứ không phải đáy; short coin thua trả funding và dễ bị squeeze.
- Danh sách coin chọn theo hôm nay làm phồng Sharpe đáng kể → luôn dùng danh mục point-in-time.
- Cỡ cú chạy 4h (theo ATR hay %) không phân biệt kết quả 12h sau đó: đòi cú chạy lớn hơn không tạo ra lợi thế.
- Hook thanh lý (sập kèm OI sụt → mua) và ensemble Donchian hai chiều đang được **kiểm định tiến**; khối đầu (6 tháng)
  chưa đủ để kết luận. Trong giai đoạn tăng, phần short của trend kéo kết quả xuống.
- Với 15 tháng dữ liệu 2025+, kiểm tra dương tính cho thấy chỉ phát hiện được lợi thế rất lớn (Sharpe ≈ 2 mới đạt
  ~75% số lần; ≈ 0,3–0,5R/lệnh với ~200 sự kiện). Khối lịch sử 2023–2024 (chạy một lần) hạ ngưỡng này. Lợi thế phải
  tìm ở chỗ ít người làm (ngách thanh khoản thấp hơn, sự kiện có lịch, dòng lệnh), không ở chỉ báo trên nến công
  khai của coin lớn.
- Dữ liệu sàn có bẫy: API trả nến giả (giá đứng yên, khối lượng 0) và cả funding cho mã đã delist; mã đổi tên (FTM→S,
  MATIC→POL, RNDR→RENDER, BNX→FORM) trông như niêm yết mới; tên mã có thể được dùng lại cho token khác. Kiểm toán kho
  (`scripts/research/audit_store.py`) trước khi tin một kết quả.
