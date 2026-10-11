# Quant playbook: các họ mô hình có bằng chứng

Kiến thức nền cho mọi quyết định nghiên cứu/strategy trong repo. Cách làm việc: [.claude/rules/quant-mindset.md](../.claude/rules/quant-mindset.md).
Bản dễ hiểu về công cụ đo: [huong-dan-nhan-dinh-thi-truong.md](huong-dan-nhan-dinh-thi-truong.md).
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
- **Bẫy:** funding đứng riêng dự báo hướng giá yếu; quên chi phí funding khi giữ vị thế futures lâu.

## 4. Momentum cắt ngang (cross-sectional)
- **Cơ chế:** coin mạnh hơn nhóm tiếp tục mạnh hơn trong ngắn/trung hạn.
- **Bằng chứng:** Liu, Tsyvinski & Wu (2022, JF), "Common Risk Factors in Cryptocurrency": momentum là một nhân tố
  chính.
- **Cách dùng:** xếp hạng coin theo lợi suất 1–4 tuần, nghiêng tỷ trọng về coin mạnh trong danh mục trend.
- **Bẫy:** danh mục nhỏ (15 coin) cho ít độ phân tán; survivorship bias nặng nếu chọn coin theo hiện tại.

## 5. Mean-reversion ngắn hạn (ưu tiên thấp)
- **Cơ chế:** thanh khoản tạm thời mất cân bằng rồi hồi lại.
- **Bằng chứng:** yếu và không ổn định ở crypto lớn. Chủ yếu tồn tại ở tần suất rất cao, nơi cá nhân không cạnh
  tranh được.
- **Bẫy:** **bẫy winrate**: thắng 55–60% số lần nhưng lỗ tổng vì lệnh thua lớn; nguy hiểm khi có đòn bẩy.

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
- Đếm **mọi lần thử** trên IS. Dùng Deflated Sharpe Ratio (Bailey & López de Prado, 2014) và Probability of
  Backtest Overfitting (Bailey et al., 2017) khi đã thử nhiều biến thể.
- Luôn so với: **mua và giữ** (thường và có vol-target), **baseline** đơn giản, **vào trễ 1 nến**.
- Tính đủ phí, funding, trượt giá. Ghi rõ survivorship bias của danh sách coin.
- Harvey, Liu & Zhu (2016, RFS): với nhiều phép thử, ngưỡng t hợp lý > 3,0. https://www.nber.org/papers/w20592

## 9. Bài học từ cộng đồng
- Strategy Freqtrade cộng đồng dùng hàng chục chỉ báo và tối ưu dày đặc thường có backtest rất đẹp nhưng chạy thật
  kém. Nguyên nhân là overfit: càng nhiều điều kiện và tham số thì càng dễ "học thuộc" dữ liệu cũ.
- Các quỹ trend-following thành công (CTA) dùng quy tắc **đơn giản, đa dạng thị trường, định cỡ theo rủi ro**,
  và chấp nhận những năm lỗ.

## 10. Đã kiểm định trong repo (kết luận định tính)
| Hướng | Kết luận |
|---|---|
| Hook ngữ cảnh 1h (momentum 4h, nén biến động, funding cực đoan, premium, BTC shock) | Không qua cổng; phí 1h ăn hết |
| 12 chỉ báo cổ điển × 1h/4h | Không chỉ báo đứng riêng nào đạt; trend 4h nhỉnh hơn 1h; mean-reversion dính bẫy winrate |
| Regime theo luật | Tách được biên độ tương lai, gần như không tách được hướng → dùng để định cỡ |
| Trend 4h kiểu turtle, một lookback, trailing chặt | Gần hòa vốn, thua xa mua-giữ |
| Ensemble Donchian khung ngày + vol-target (tham số gốc của bài báo) | Trên danh sách coin chọn hôm nay thì trông có lợi thế. Trên danh mục point-in-time (top theo thanh khoản tại từng thời điểm, kể cả coin đã delist) phần lớn lợi thế biến mất và thua BTC mua-giữ có vol-target → bác bỏ. Vẫn hơn trend SMA đơn giản và drawdown thấp |
| Bài học chung | Danh sách coin chọn theo hiện tại làm phồng Sharpe đáng kể, chủ yếu do chọn trúng coin sống sót tăng tốt chứ không phải do né coin chết. Kiểm định danh mục phải dùng danh mục point-in-time (`mce.universe`, `mce.archive`) |
