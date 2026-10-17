# Sổ tay nghiên cứu: hook, mô hình, thuật ngữ và bài học của bot

Tài liệu này giải thích **toàn bộ những gì bot đang có và đã thử**: từng hook/mô hình làm gì, vì sao có (hoặc không có)
lợi thế, cách repo kiểm định một ý tưởng, các thuật ngữ quant hay gặp và bài học rút ra sau các lần thử. Viết cho người
muốn hiểu bot từ gốc, không cần biết code.

Tài liệu liên quan: thiết kế sản phẩm [signal-bot-workflow.md](signal-bot-workflow.md) · các họ mô hình có bằng chứng
[quant-playbook.md](quant-playbook.md) · bộ máy nghiên cứu [market-context-engine.md](market-context-engine.md). Số liệu
cụ thể của từng lần thử (lift, CI, Sharpe, số lần thử cộng dồn) chỉ nằm trong `user_data/research/STATE.md` (riêng,
không commit). Ở đây chỉ ghi định nghĩa, cơ chế và kết luận.

## Mục lục
1. [Bức tranh tổng](#1-bức-tranh-tổng)
2. [Các thước đo bot đọc từ dữ liệu](#2-các-thước-đo-bot-đọc-từ-dữ-liệu)
3. [Hook và mô hình đang kiểm định tiến](#3-hook-và-mô-hình-đang-kiểm-định-tiến)
4. [Đã thử và đã loại](#4-đã-thử-và-đã-loại)
5. [Hướng chưa chạy và vì sao](#5-hướng-chưa-chạy-và-vì-sao)
6. [Một ý tưởng được kiểm định như thế nào](#6-một-ý-tưởng-được-kiểm-định-như-thế-nào)
7. [Lớp sản phẩm: từ luật tới tin nhắn](#7-lớp-sản-phẩm-từ-luật-tới-tin-nhắn)
8. [Thuật ngữ](#8-thuật-ngữ)
9. [Bài học của một quant](#9-bài-học-của-một-quant)
10. [Câu hỏi hay gặp](#10-câu-hỏi-hay-gặp)
11. [Tài liệu tham khảo](#11-tài-liệu-tham-khảo)

---

## 1. Bức tranh tổng

```
Dữ liệu Binance (nến, funding, OI, lệnh khớp, sổ lệnh, thanh lý)
  └─► kho point-in-time (đúng như lúc đó biết, kể cả coin đã delist)
        └─► thước đo (ATR, cú sốc, OI z, regime...)            mục 2
              └─► hook / luật (chốt trước, ≤ 3 tham số)          mục 3–5
                    └─► kiểm định: dải phát triển MỘT lần       mục 6
                          └─► kiểm định tiến (dữ liệu sau ngày chốt)
                                └─► lớp sản phẩm: ngân sách rủi ro → tin Telegram → theo dõi → tổng kết   mục 7
```

- **Hook** = một luật có tên, phiên bản và tham số cố định, trả lời câu hỏi "ở nến này có tín hiệu không, chiều nào".
  Ví dụ `liquidation_bounce_4h@v1`. Đổi tham số là một hook mới, một lần thử mới.
- **Mô hình danh mục** = luật cho ra tỷ trọng của nhiều coin mỗi ngày (ví dụ trend ensemble), chấm bằng lãi/lỗ của
  cả danh mục chứ không chấm từng lệnh.
- **Setup** = bản "sản phẩm" của một tín hiệu: coin, chiều, giá vào, SL, TP hoặc luật thoát, khối lượng, lý do.
- Bot chỉ phát setup **thật** khi luật đã qua kiểm định. Trước đó mọi tin gắn nhãn **THỬ NGHIỆM**.

## 2. Các thước đo bot đọc từ dữ liệu

Mọi thước đo đều **nhân quả**: giá trị ở nến t chỉ dùng dữ liệu đã có khi nến t đóng (code `scripts/research/mce/
features.py`, `regime.py`, `pit.py`; có test cắt dữ liệu tương lai mà quá khứ không đổi).

| Thước đo | Định nghĩa | Dùng để |
|---|---|---|
| **ATR14** | Trung bình biên độ thật 14 nến (gồm cả khoảng nhảy giữa hai nến) | Đơn vị biến động: đặt SL/TP, quy đổi lãi/lỗ ra R |
| **ATR%** | ATR / giá đóng | So biến động giữa các coin có giá khác nhau |
| **Cú sốc (shock)** | (giá đóng − giá đóng trước) / ATR14 của nến trước | Nến "lớn bất thường": ≥ 2 ATR là hiếm |
| **OI z** | Thay đổi log OI (ảnh chụp mỗi giờ phút :50) trong nến, chuẩn hóa z-score trên 180 nến 4h (~30 ngày) của chính coin | OI sụt mạnh bất thường (z ≤ −2) = vị thế bị đóng hàng loạt, dấu vết của thanh lý |
| **Funding** | Phí trao đổi giữa long và short mỗi 8h (có coin 4h/1h); dương = long trả short | Đo độ đông vị thế; là chi phí khi giữ lệnh |
| **Regime xu hướng** | up/down/range: độ dốc EMA50 trong 10 nến ≥ 0,5 ATR và ADX ≥ 20 | Chia thị trường để so sánh công bằng (mức nền cùng regime) |
| **Regime biến động** | low/normal/high: phân vị của ATR% trong cửa sổ trượt (≤ 20%, ≥ 80%) | Như trên; regime tách được biên độ tương lai, không tách được hướng |
| **Cấu trúc nến** | Tỷ lệ thân, râu trên, râu dưới trên biên độ; biên độ theo ATR; khoảng nhảy mở cửa | Mô tả hình dạng nến (dùng trong price action) |
| **Swing fractal** | Đỉnh/đáy cao (thấp) hơn k=2 nến hai bên, chỉ xác nhận sau khi đủ nến bên phải | Cấu trúc giá: phá đỉnh/đáy, vùng flip |
| **Danh mục point-in-time** | Top-20 coin theo trung vị quote volume 30 ngày, cần ≥ 90 ngày lịch sử (v2: 365), xếp hạng cuối tháng, kể cả coin đã delist | Chọn coin đúng như trader lúc đó chọn, tránh thiên lệch sống sót |

Dữ liệu chỉ có từ **bộ ghi** (không có lịch sử ở đâu): thanh lý từng lệnh, giá mua/bán tốt nhất mỗi phút, funding dự
kiến, sổ lệnh 100 mức, quyền chọn (IV). Dữ liệu này cần nhiều tháng ghi liên tục mới đủ nghiên cứu.

## 3. Hook và mô hình đang kiểm định tiến

Kiểm định tiến = luật đã chốt, dữ liệu **sau ngày chốt** chấm điểm, nhật ký setup ghi trước khi biết kết quả và không
sửa được. Kết luận chỉ ở mốc đã hẹn trước.

### (a) `donchian_ls_1d@v1`: trend ngày hai chiều
- **Ý tưởng**: giá phá lên vùng cao nhất N ngày thì có xu hướng đi tiếp (time-series momentum).
- **Cơ chế**: nhà đầu tư phản ứng chậm với thông tin rồi đổ xô theo; dòng tiền đuổi theo xu hướng; người phòng hộ
  chấp nhận trả phí. Bằng chứng mạnh nhất trong mọi thị trường, kể cả crypto (playbook mục 1).
- **Luật**: 9 kênh Donchian trên giá đóng (5, 10, 20, 30, 60, 90, 150, 250, 360 ngày). Mỗi kênh: đóng trên đỉnh kênh →
  long, dưới đáy → short; SL trượt tại trung điểm kênh, chỉ dời theo hướng có lợi. Tín hiệu = trung bình các kênh
  (−1 → +1). Tỷ trọng = tín hiệu × min(25% / biến động 90 ngày, 2) / 20 coin.
- **Setup sản phẩm**: khi tỷ trọng một coin đổi dấu; SL = mức thoát của cả ensemble; đóng khi tỷ trọng về 0.
- **Mốc**: 2027-04-01, cổng danh mục.
- **Ghi chú**: bản gốc của bài là chỉ LONG; bản hai chiều này lệch khỏi bài. Trong giai đoạn tăng, phần short kéo kết
  quả xuống. Không được đổi sang "bản chỉ long" dựa trên kết quả đã xem: bản chỉ long được chốt riêng thành (z).

### (z) `donchian_long_1d@v1` (spec `forward_v2`): bản gốc chỉ LONG của Zarattini và cộng sự (2025)
- Tham số đúng bài: cùng 9 kênh, chỉ LONG, vol mục tiêu 25%/năm, trần 2x mỗi coin, 20 slot, top-20 thanh khoản xoay
  tháng, chỉ coin đã giao dịch ≥ 365 ngày. Phí 0,06%/chiều + funding thật, tái cân bằng hằng ngày (không dùng kỹ thuật
  giảm phí của bài vì không xác minh được).
- Chấm từ 2026-10-14, **kết luận một lần sau 2 năm** (2028-10-14); sau 1 năm chỉ xem để dừng nếu vô vọng.
- Sức thống kê thấp: nếu Sharpe thật ≈ 1 thì chỉ khoảng 40% khả năng qua cổng sau 2 năm. Bài báo ghi Sharpe > 1,5, nhưng
  đó là số của tác giả trên dữ liệu quá khứ; lợi thế thường giảm sau khi công bố.

### (b) `liquidation_bounce_4h@v1`: mua sau thanh lý
- **Ý tưởng**: cú sập lớn kèm OI sụt bất thường = thanh lý bắt buộc; lệnh thanh lý bán bất chấp giá nên đẩy giá quá
  đà, sau đó giá hồi.
- **Cơ chế**: người đứng ra mua hàng bán tháo được trả phần bù khi giá hồi (Brunnermeier & Pedersen 2009; Coval &
  Stafford 2007; Nagel 2012). Tồn tại vì mua vào cú sập rất rủi ro và vốn khan hiếm đúng lúc đó.
- **Luật**: nến 4h có cú sốc ≤ −2 ATR **và** OI z ≤ −2 → LONG (cú sốc ≥ +2 và OI z ≤ −2 → SHORT, hiếm). Giữ 12h
  (3 nến); SL 1 ATR, TP 2 ATR; nghỉ 3 nến sau mỗi tín hiệu (cooldown).
- **Trạng thái**: trên dải phát triển không đạt cổng; đang kiểm định tiến tới khi đủ 200 sự kiện (hạn 2028-01-01).

### (c) `shock_no_oi_drop_short_4h@v1`: bán theo cú sập không kèm thanh lý
- **Ý tưởng**: cú sập ≥ 2 ATR nhưng OI **không** sụt bất thường = người bán tự nguyện (có thông tin), không phải bị ép;
  không có "quá đà" để hồi, nên giá tiếp tục giảm.
- **Luật**: cú sốc ≤ −2 ATR và OI z > −2 → SHORT 12h, SL 1 ATR, TP 2 ATR.
- Sinh ra từ **nhóm đối chứng** của nghiên cứu (b) trên dải phát triển, nên chỉ dữ liệu mới mới kiểm định được nó.
  Mốc như (b).

### (1b) Liều–đáp ứng cỡ cú chạy 4h: **đã kết luận FAIL**
- Câu hỏi: nến 4h chạy càng mạnh (theo ATR hoặc %) thì 12h sau có tiếp diễn (hoặc đảo chiều) mạnh hơn không?
- Kết quả: không có xu hướng nào. Đòi cú chạy lớn hơn không tạo ra lợi thế. Hệ quả: "nến to = vào lệnh" không có cơ sở.

## 4. Đã thử và đã loại

Không làm lại các hướng dưới đây, trừ khi có lý do mới rõ ràng (ghi vào STATE trước).

| Họ | Đã thử | Vì sao thất bại | Bài học |
|---|---|---|---|
| Chỉ báo cổ điển đứng riêng (1h, 4h) | Các luật kiểu RSI, EMA, MACD, Bollinger, đứng riêng (hàng chục phép thử) | Không cái nào có lợi thế sau phí; nhóm mean-reversion thắng nhiều lần nhưng lỗ tổng | Chỉ báo là thước đo trạng thái, không phải tín hiệu; coi chừng bẫy winrate |
| Hook ngữ cảnh 1h | Tín hiệu 1h kèm ngữ cảnh (regime, cú sốc của BTC kéo coin chậm chân: `btc_shock_laggard`...) | Không lợi thế sau phí; `btc_shock_laggard` còn ngược dấu kỳ vọng; phí ở 1h ≈ 0,1–0,2R | Khung nhỏ cần lợi thế rất lớn mới sống sau phí |
| Trend 4h một lookback, trailing chặt | Phá kênh N nến 4h, dừng lỗ bám sát | Nhiễu, phí cao, cắt mất sóng lớn | Trend cần khung ngày và ensemble nhiều lookback |
| Trend chỉ trên BTC | Một tài sản | Quá ít quan sát độc lập để chứng minh | Ưu tiên tín hiệu nhiều coin |
| Funding đứng riêng | Carry cắt ngang (long funding thấp, short funding cao), funding làm bộ lọc | Thu được funding nhưng giá ăn ngược: coin funding âm thường tiếp tục giảm | Funding âm là xu hướng giảm đúng, không phải đáy |
| Mô hình logistic đa yếu tố 4h | Gộp nhiều đặc trưng dự báo hướng tuyệt đối | Bị beta thị trường chi phối: năm giảm làm mọi thứ thiên short "có lãi" giả | Luôn tách long/short, so với mốc ngẫu nhiên |
| Động lượng cắt ngang top-20 | Mua coin mạnh nhất, bán coin yếu nhất, mọi biến thể | Thứ hạng không hơn danh mục ngẫu nhiên | Top-20 quá ít coin, quá hiệu quả |
| Ensemble Donchian chỉ long trên dữ liệu cũ (v3) | Bản chỉ long, 2022–2025 | Chưa chứng minh, chưa bác bỏ (CI rộng) | Thiếu dữ liệu, không phải thiếu ý tưởng |
| Thanh lý: biến thể ngưỡng/horizon trên dải phát triển | Đổi 2 ATR, 12h... | Chạy lại trên cùng dữ liệu = đào dữ liệu | Chốt trước, chạy một lần |
| Price action theo lớp (top-20, 4h) | L0 vào theo cấu trúc; L1 vào ở lần hồi đầu về vùng flip; L2 thêm nến xác nhận (từ chối giá, nhấn chìm). SL ở đỉnh/đáy swing đối diện, TP 2R, thoát sau 30 nến | Không lớp nào có lợi thế; hồi về vùng còn hơi kém hơn vào ngẫu nhiên; nến xác nhận chỉ đổi cách thoát, không thêm kỳ vọng | Đo từng lớp so với lớp trước, cùng SL/TP: biết lớp nào thêm giá trị |
| (b), (c) trên coin hạng 21–100 (4h, 2026-10-17) | Giữ nguyên hai luật thanh lý / cú sập, chỉ đổi danh mục sang hạng thanh khoản 21–100; chi phí coin nhỏ 0,18% (bảo thủ 0,41%) | (b') lift 12h −0,07R, ròng −0,19R, các quý trái dấu. (c') lift +0,46R nhưng CI chạm 0, không cùng dấu cả hai năm, gần hết lợi nhuận nằm trong đợt sập 2025Q4 | Thêm coin làm tăng số sự kiện chứ không tăng nhiều sức thống kê: coin nhỏ sập cùng lúc (~4,8 coin mỗi nến), nên số tuần mới là cỡ mẫu thật |
| Danh sách coin chọn theo hôm nay | Kiểm thử trên coin đang tồn tại | Làm phồng Sharpe đáng kể | Luôn dùng danh mục point-in-time |

## 5. Hướng chưa chạy và vì sao

| Hướng | Nội dung | Tình trạng |
|---|---|---|
| Động lượng tuần cắt ngang, coin hạng 21–100 | Ngách thanh khoản thấp hơn, ít người làm | Không chạy: với lợi thế hợp lý, xác suất chứng minh được chỉ ~7–15%; phí rổ tuần lớn |
| Short perp mới niêm yết 90 ngày | Token mới thường giảm sau niêm yết | Không chạy: cần kém rổ ≥ 20–30% mới đủ sức thống kê; short coin biến động mạnh rất lồi (lỗ đuôi lớn) |
| Giờ thanh toán funding cực đoan | Kỳ funding \|f\| ≥ 0,3% (gần như toàn squeeze), quanh giờ thanh toán | Đủ sức thống kê chỉ ở cửa sổ vài phút → cần bot tự vào lệnh (giai đoạn 2) |
| Delist | Coin sắp bị gỡ | Chỉ kiểm định tiến, ưu tiên thấp |
| Dòng lệnh (order flow) | Lệch mua/bán chủ động, sổ lệnh | Giải thích mạnh giá cùng lúc; phần dự báo tắt trong giây–phút → giai đoạn 2 |
| R1 thanh lý thật làm bộ phát hiện | Thay "OI sụt" bằng khối lượng thanh lý thật từ bộ ghi | Đã chốt trước, chờ ≥ 6 tháng dữ liệu |
| R2 funding dự kiến cực đoan | Giờ trước thanh toán | Đã chốt trước, chờ ≥ 3 tháng |
| R3 IV quyền chọn | IV dự báo biến động thực | Đã chốt trước, chờ ≥ 3 tháng |

## 6. Một ý tưởng được kiểm định như thế nào

1. **Checklist trước khi code** (`.claude/rules/quant-mindset.md`): giả thuyết một câu; cơ chế kinh tế (ai trả tiền
   cho lợi thế, vì sao chưa bị arbitrage); bằng chứng có trước; đã thử chưa; lợi thế kỳ vọng sau phí; ngưỡng bác bỏ;
   ≤ 3–5 tham số lấy từ tài liệu.
2. **Sức thống kê trước** (`mce/power.py`): cài một lợi thế biết trước vào nhiễu, xem quy trình phát hiện được bao
   nhiêu phần trăm. Không đủ sức thì không chạy (không tốn lần thử).
3. **Kiểm tra không nhìn kết quả** (`--check`): đếm sự kiện, độ phủ dữ liệu, nhân quả ở 3 mốc cắt (cắt dữ liệu sau
   mốc thì tín hiệu trước mốc không đổi), placebo (nến ngẫu nhiên phải cho lift ≈ 0), đối chiếu tay một sự kiện với
   nến Binance.
4. **Fingerprint**: băm luật + mọi module code dùng tới, ghi vào `fingerprints.jsonl`. Sửa bất kỳ file nào trong đó
   phải chứng minh đầu ra không đổi rồi đăng ký lại.
5. **Chạy MỘT lần** trên dải phát triển (2025-01 → 2026-03), so với cổng đã chốt.
6. **Kiểm định tiến**: dữ liệu sau ngày chốt chấm điểm; có thêm khối lịch sử khóa 2023–2024 (chạy một lần, đòi cùng
   dấu) cho luật chưa từng chạm dữ liệu cũ.
7. **Kết luận ở mốc** đã hẹn; dừng sớm chỉ vì vô vọng.

**Cổng sự kiện** (hook): ≥ 200 sự kiện; lift có ý nghĩa sau Benjamini–Hochberg; cùng dấu ở ≥ 2/3 số năm và ≥ 60% số
coin (có ≥ 10 sự kiện); ròng sau phí + funding > 0.

**Cổng danh mục** (mô hình tỷ trọng): Sharpe sau phí ≥ 1,0; cận dưới bootstrap khối 20 ngày (5%) > 0; hơn BTC
mua-giữ có vol-target 25%; sụt giảm tối đa ≤ 35%; ≥ 2/3 số quý dương; không coin nào > 30% lợi nhuận; vẫn dương khi phí
0,10%/chiều; Deflated Sharpe ≥ 0,5 với số lần thử cộng dồn.

**Kiểm tra kép code**: một bản tính độc lập viết lại từ đặc tả (`scripts/research/oracle.py`, không dùng chung code)
chạy song song; hai bên phải trùng từng sự kiện. Một đối chứng âm (cố tình đổi một tham số) phải báo lệch, để chắc phép
so đủ nhạy.

## 7. Lớp sản phẩm: từ luật tới tin nhắn

Chi tiết: [signal-bot-workflow.md](signal-bot-workflow.md) mục 3–7. Tóm tắt:
- **Ngân sách rủi ro**: các setup 4h cùng nến, cùng chiều là **một cược** 1% vốn chia đều (một cú sốc thị trường không
  thành nhiều cược); trend 0,25% mỗi vị thế; trần mỗi chiều 5%, tổng 8%. Vượt trần thì thu nhỏ, quá nhỏ thì chặn.
- **Nhật ký** chấm mọi setup của luật (kể cả setup bị chặn) để kết luận về luật; **sổ** chấm những gì người theo bot
  thực sự vào, theo % vốn.
- Tin setup có dòng **Kỳ vọng** = bằng chứng thật đến nay; tin đóng lệnh có R sau phí + funding; tổng kết tuần theo
  luật; cảnh báo vận hành. Telegram chỉ gửi, không nhận lệnh.

## 8. Thuật ngữ

### Lợi thế và kết quả
| Thuật ngữ | Nghĩa |
|---|---|
| **Lợi thế (edge)** | Kỳ vọng lãi dương sau mọi chi phí, lặp lại được, có lý do kinh tế |
| **R** | Đơn vị rủi ro của một lệnh = khoảng cách từ giá vào tới SL. Lãi +2R = lãi gấp đôi số tiền chịu rủi ro |
| **Kỳ vọng (expectancy)** | Lãi trung bình mỗi lệnh theo R = winrate × lãi TB − (1 − winrate) × lỗ TB. Đây là con số quyết định, không phải winrate |
| **Winrate** | Tỷ lệ lệnh thắng. Đứng riêng thì vô nghĩa: thắng 70% mà lỗ mỗi lần thua gấp 3 vẫn lỗ |
| **Bẫy winrate** | Mean-reversion hay thắng nhiều lần nhỏ, thua ít lần nhưng rất lớn; nhìn winrate tưởng tốt |
| **Profit factor** | Tổng lãi / tổng lỗ. > 1 là có lãi gộp; repo đòi ≥ 1,3 cho strategy từng lệnh |
| **Lift** | Kết quả theo hướng luật **trừ** kết quả trung bình của cùng coin, cùng regime, cùng giai đoạn. Tách lợi thế thật khỏi việc thị trường chung đi lên/xuống |
| **Mức nền (baseline)** | Kết quả của "vào bừa" trong cùng điều kiện; mọi luật phải hơn nó |
| **Ròng (net)** | Sau phí, chênh lệch giá, trượt giá và funding |
| **Drawdown (sụt giảm)** | Mức giảm từ đỉnh vốn gần nhất; max drawdown = cú giảm sâu nhất |
| **Sharpe** | Lợi suất trung bình / độ lệch chuẩn, quy năm. Lãi trên mỗi đơn vị biến động; không đổi khi tăng/giảm đòn bẩy |
| **Sortino, Calmar** | Biến thể của Sharpe: chỉ phạt biến động giảm; lợi suất năm / max drawdown |
| **Beta, alpha** | Beta = độ nhạy với thị trường (BTC); alpha = phần lợi suất còn lại sau khi trừ beta × thị trường |
| **Turnover** | Tổng giá trị giao dịch / vốn; turnover cao = phí cao |
| **Exposure** | Tổng tỷ trọng đang nắm giữ (gross = cộng trị tuyệt đối) |

### Kiểm định và thống kê
| Thuật ngữ | Nghĩa |
|---|---|
| **IS / dải phát triển** | Dữ liệu dùng để phát triển và chạy luật lần đầu (ở đây 2025-01 → 2026-03) |
| **OOS / holdout** | Dữ liệu giữ kín, chỉ chạy một lần để xác nhận. Đã dùng thì không còn "sạch" |
| **Kiểm định tiến (forward test)** | Chốt luật hôm nay, để dữ liệu tương lai chấm. Dữ liệu ngoài mẫu sạch duy nhất |
| **Khối lịch sử** | 2023–2024, khóa lại, chạy một lần cho luật đã chốt, đòi kết quả cùng dấu |
| **Pre-registration / chốt trước** | Ghi giả thuyết, tham số, cổng đạt trước khi chạy; không sửa theo kết quả |
| **Fingerprint** | Mã băm của luật + code, chứng minh luật không bị sửa sau khi chốt |
| **Lần thử (trial)** | Mỗi biến thể chạy trên IS. Càng nhiều lần thử, càng dễ "may mắn" ra kết quả đẹp |
| **Data snooping / đào dữ liệu** | Thử nhiều thứ trên cùng dữ liệu cho tới khi đẹp; kết quả không lặp lại |
| **Overfitting** | Luật "học thuộc" dữ liệu cũ (nhiều tham số, nhiều điều kiện), chạy thật thì hỏng |
| **Lookahead bias** | Dùng thông tin chưa có ở thời điểm quyết định (giá đóng của nến chưa đóng, dữ liệu sửa sau) |
| **Nhân quả (causal)** | Giá trị ở t chỉ dùng dữ liệu ≤ t; kiểm bằng cách cắt tương lai xem quá khứ có đổi không |
| **Survivorship bias** | Chỉ thử trên coin còn sống hôm nay, bỏ qua coin đã chết → kết quả đẹp giả |
| **Point-in-time** | Dữ liệu và danh mục đúng như lúc đó biết |
| **Event study** | Lấy mọi lần luật kích hoạt (sự kiện), đo kết quả sau đó so với mức nền |
| **Horizon** | Số nến giữ sau sự kiện (1, 3, 6, 12 nến) |
| **Cooldown** | Số nến bỏ qua sau một sự kiện cùng coin, tránh đếm trùng một cú sốc |
| **Triple barrier** | Lệnh thoát ở rào đầu tiên chạm: TP, SL hoặc hết giờ. Nến chạm cả hai tính là SL (thận trọng) |
| **CI (khoảng tin cậy)** | Khoảng chứa giá trị thật với độ tin 95%. CI chứa 0 = chưa chứng minh được |
| **Bootstrap** | Rút lại mẫu nhiều lần từ dữ liệu để ước CI, không cần giả định phân phối |
| **Bootstrap theo cụm tuần / khối** | Rút cả tuần hoặc cả khối 20 ngày một lần, giữ tương quan giữa các coin và các ngày; nếu rút từng sự kiện thì CI hẹp giả |
| **p-value** | Xác suất thấy kết quả này nếu thật ra không có lợi thế |
| **Kiểm định bội / multiple testing** | Thử 20 thứ thì trung bình 1 thứ "có ý nghĩa" do may. Phải hiệu chỉnh |
| **Benjamini–Hochberg (BH, FDR)** | Hiệu chỉnh p cho nhiều phép thử, khống chế tỷ lệ phát hiện sai |
| **Holm** | Hiệu chỉnh chặt hơn cho một nhóm nhỏ phép thử |
| **Deflated Sharpe (DSR)** | Sharpe đã trừ phần "may mắn" do số lần thử; DSR ≥ 0,5 = khả năng Sharpe thật vượt mức may mắn |
| **PBO** | Xác suất backtest bị overfit khi chọn biến thể tốt nhất |
| **Sức thống kê (power)** | Xác suất phát hiện được lợi thế nếu nó thật sự tồn tại. Thấp thì "không đạt" không có nghĩa là "không có gì" |
| **Kiểm tra dương tính (positive control)** | Cài lợi thế biết trước vào dữ liệu giả, quy trình phải phát hiện được |
| **Placebo / đối chứng âm** | Tín hiệu ngẫu nhiên phải cho kết quả ≈ 0; đổi tham số phải làm phép so báo lệch |
| **Nhóm đối chứng (control)** | Sự kiện giống hệt trừ một yếu tố (ví dụ cú sập không kèm OI sụt) để biết yếu tố đó có tác dụng không |
| **Liều–đáp ứng (dose-response)** | Tác động có tăng theo "liều" (cú sốc lớn hơn) không; nếu có cơ chế thật thường thấy xu hướng này |
| **t ≈ Sharpe × √năm** | Quy tắc nhanh: muốn t ≥ 2 với Sharpe 1 cần ~4 năm dữ liệu |

### Thị trường futures crypto
| Thuật ngữ | Nghĩa |
|---|---|
| **Perpetual (perp)** | Hợp đồng tương lai không đáo hạn; giá được neo với giá giao ngay bằng funding |
| **Funding** | Phí long và short trả cho nhau mỗi kỳ; dương = long trả. Funding cao = phe long đông |
| **OI (open interest)** | Tổng số hợp đồng đang mở. OI tăng = vị thế mới vào; OI sụt mạnh = vị thế bị đóng (thường do thanh lý) |
| **Thanh lý (liquidation)** | Sàn đóng cưỡng bức lệnh hết ký quỹ bằng lệnh thị trường, bất chấp giá |
| **Squeeze** | Phe đông vị thế bị ép đóng dây chuyền (short squeeze: short bị ép mua → giá vọt) |
| **Isolated margin** | Ký quỹ riêng từng lệnh: thanh lý một lệnh không kéo cả tài khoản |
| **Đòn bẩy, giá thanh lý** | Đòn bẩy càng cao, giá thanh lý càng gần; repo giữ thanh lý xa khoảng gấp đôi SL, tối đa 5x |
| **Taker / maker** | Taker khớp ngay bằng lệnh thị trường (phí cao hơn, VIP0 0,05%); maker đặt lệnh chờ (0,02%) |
| **Chênh lệch giá (spread)** | Khoảng cách giá mua tốt nhất – bán tốt nhất; lệnh thị trường trả một nửa mỗi chiều |
| **Trượt giá / tác động (slippage, impact)** | Lệnh lớn ăn sâu vào sổ lệnh nên khớp giá xấu hơn |
| **Sổ lệnh (order book), bookDepth** | Các lệnh chờ hai phía; bookDepth = tổng giá trị trong ±0,2–5% quanh giá giữa |
| **Lệnh khớp (aggTrades)** | Từng giao dịch, biết phía chủ động (taker mua hay bán) |
| **Dòng lệnh (order flow)** | Lệch giữa mua chủ động và bán chủ động; là thứ trực tiếp đẩy giá |
| **Mark price / index** | Giá dùng để tính thanh lý và funding, chống thao túng giá khớp |
| **Delist, nến giả** | Coin bị gỡ: API vẫn trả nến phẳng khối lượng 0 và funding giả; repo xóa phần đuôi này |
| **Mã đổi tên / dùng lại tên** | MATIC→POL, FTM→S...; một tên có thể bị dùng lại cho token khác. Phải đánh dấu để không nhầm |

### Mô hình
| Thuật ngữ | Nghĩa |
|---|---|
| **Trend-following / time-series momentum** | Đi theo xu hướng của chính tài sản: đang tăng thì long |
| **Momentum cắt ngang (cross-sectional)** | So sánh các coin với nhau: long coin mạnh nhất, short coin yếu nhất |
| **Mean-reversion** | Đặt cược giá quay về trung bình sau khi đi quá xa |
| **Carry** | Kiếm lời từ phần trả đều đặn (funding); rủi ro là giá đi ngược |
| **Kênh Donchian** | Đỉnh và đáy cao/thấp nhất của N nến trước; phá kênh = tín hiệu xu hướng |
| **Ensemble** | Trung bình nhiều phiên bản (nhiều lookback) thay vì chọn một "tốt nhất": ít phụ thuộc may mắn |
| **Trailing stop** | SL dời theo hướng có lợi, khóa lãi dần |
| **Volatility targeting** | Định cỡ vị thế để biến động cố định (vd 25%/năm): coin biến động mạnh thì nắm ít |
| **Tái cân bằng (rebalance)** | Đưa tỷ trọng về mục tiêu định kỳ; càng thường xuyên càng tốn phí |
| **Regime** | Trạng thái thị trường (xu hướng/đi ngang, biến động thấp/cao) |
| **Beta-neutral** | Danh mục không nhạy với thị trường chung; cần khi so momentum cắt ngang |
| **Price action** | Đọc cấu trúc đỉnh/đáy, vùng giá, mẫu nến; thực chất vẫn là hàm của giá đã qua |

### Vận hành bot
| Thuật ngữ | Nghĩa |
|---|---|
| **Nhật ký (journal)** | sqlite chỉ ghi thêm: setup ghi trước khi biết kết quả, mỗi setup một kết quả |
| **live / live_late** | Setup ghi đúng cửa sổ 15 phút sau đóng nến / ghi bù khi máy ngủ |
| **Đối chiếu (reconcile)** | So từng setup trực tiếp với bản chạy lại trên kho lưu trữ; lệch = báo lỗi |
| **Sổ rủi ro (book)** | Bản ghi những gì người theo bot vào, với rủi ro thực và lãi/lỗ theo % vốn |
| **Một cược (bet)** | Các setup cùng một cú sốc thị trường, chia chung 1% rủi ro |
| **Outbox** | Hàng đợi tin: mỗi tin một khóa, gửi đúng thứ tự, gửi lại khi lỗi mạng |
| **Bộ ghi (recorder)** | Tiến trình ghi dữ liệu không có lịch sử (thanh lý, sổ lệnh...) 24/7 |
| **Độ phủ (coverage)** | Phần trăm thời gian một ngày bộ ghi thực sự ghi được |

## 9. Bài học của một quant

### Về lợi thế
- Lợi thế trong crypto **nhỏ và hiếm**. Phần lớn ý tưởng phổ biến thất bại khi kiểm đúng cách. Đó là kết quả bình
  thường. Mục tiêu là loại ý tưởng xấu nhanh và rẻ, không phải làm backtest đẹp.
- Luật đơn giản mà dễ kiếm tiền thì thị trường đã dùng hết. Lợi thế còn sống thường ở nơi khó: ngách ít người làm, sự
  kiện có lịch, dòng lệnh nhanh, hoặc nơi phải chịu rủi ro khó chịu (mua vào lúc sập).
- Mọi lợi thế phải trả lời được: **ai đang trả tiền cho mình, và vì sao họ chưa ngừng?**
- Bằng chứng mạnh nhất: trend-following khung ngày/tuần + định cỡ theo biến động. **Dự báo biến động dễ hơn dự báo
  hướng.**
- Nến và chỉ báo **giải thích** rất tốt vì sao giá vừa chạy, nhưng gần như không **dự báo** nến sau: thông tin trên nến
  công khai đã được hàng triệu người dùng trước.

### Về dữ liệu
- Danh sách coin chọn theo hôm nay làm phồng kết quả: luôn dùng danh mục point-in-time, kể cả coin đã chết.
- Sàn trả dữ liệu bẩn: nến giả sau delist, funding của hợp đồng cũ trên tên dùng lại, mã đổi tên trông như niêm yết
  mới, ngày lệch khối lượng. **Kiểm toán kho trước khi tin bất kỳ kết quả nào.**
- Hai nguồn của cùng một dữ liệu có thể lệch nhãn thời gian (OI API trễ 5 phút so với kho): luôn đối chiếu trực tiếp
  trên nhiều coin, nhiều ngày.
- Thay đổi cách đọc dữ liệu phải chốt **trước khi** đo ảnh hưởng, rồi chứng minh bằng ảnh chụp trước/sau.
- Lỗi lặng lẽ nguy hiểm hơn lỗi ồn ào: `INSERT OR IGNORE` của SQLite bỏ qua cả dòng thiếu giá trị mà không báo.

### Về thống kê
- **Đếm mọi lần thử.** Mỗi biến thể chạy trên cùng dữ liệu làm giảm độ tin cậy của kết quả tốt nhất.
- Chạy holdout nhiều lần = không còn holdout. Chọn giai đoạn đẹp = tự lừa mình.
- **"Không đạt" khác "không có gì"**: biết trước sức thống kê để đọc đúng kết quả. 15 tháng dữ liệu ngày chỉ chứng
  minh được Sharpe rất cao; ~150 sự kiện chỉ phát hiện lợi thế ≥ 0,3–0,5R.
- Sự kiện đến theo cụm (một cú sập kéo cả thị trường): bootstrap từng sự kiện cho CI hẹp giả; phải rút theo tuần hoặc
  theo lịch.
- Dự báo hướng tuyệt đối bị beta thị trường chi phối: năm giảm làm mọi luật thiên short trông như có lãi. Luôn tách
  long/short, so với mốc ngẫu nhiên và mua-giữ có vol-target.
- Đo **trung bình số học của P&L thật**, không dùng trung vị hay lợi suất log: phần đuôi (lỗ lớn hiếm) mới quyết định.
- Bán khống tài sản biến động mạnh rất **lồi**: giữ short lâu không SL có thể lỗ dù giá trung vị giảm.

### Về chi phí
- Phí khứ hồi ở khung 1h ≈ 0,1–0,2R, đủ ăn hết lợi thế nhỏ. Khung càng dài, phí càng nhỏ so với biến động.
- Chi phí thật = phí + nửa chênh lệch giá mỗi chiều + tác động sổ lệnh + funding. Coin nhỏ đắt hơn nhiều so với coin
  top-20; lệnh lớn đắt hơn lệnh nhỏ.
- Sau thanh lý, nếu có nhịp hồi thì nó đến ngay: vào trễ một nến đã mất. Luôn thử "vào trễ 1 nến".

### Về quy trình
- Chốt giả thuyết, tham số và cổng **trước** khi chạy; ghi fingerprint; chạy một lần.
- Kiểm tra không nhìn kết quả trước (đếm sự kiện, nhân quả, placebo) rẻ hơn rất nhiều so với phát hiện lỗi sau.
- Viết lại phép tính một cách độc lập (oracle) để bắt lỗi code; một đối chứng âm chứng minh phép so đủ nhạy.
- Tham số lấy từ tài liệu hoặc mặc định, ≤ 3–5 cái. Thấy mình đang chồng điều kiện cho đẹp ("súp chỉ báo") thì dừng.
- Ưu tiên: tái tạo mô hình đã công bố → đơn giản hóa → sau cùng mới đến ý tưởng tự nghĩ.

### Về rủi ro và sản phẩm
- Nhiều tín hiệu cùng lúc thường là **một** cú sốc: gộp thành một cược, đừng đặt 1% cho mỗi cái.
- Tương quan đo lúc bình thường thấp hơn lúc sập (mọi thứ cùng rơi): đừng tin tương quan bình thường để chia rủi ro.
- Giữ trần tổng rủi ro mở; vượt trần thì thu nhỏ hoặc bỏ, không "cố vào cho đủ".
- Kết luận về luật dựa trên **mọi** setup (nhật ký), không chỉ setup đã gửi; số lãi/lỗ của sổ là chuyện khác.
- Kết quả vài tuần đầu gần như không nói lên gì; chỉ kết luận ở mốc đã hẹn.

## 10. Câu hỏi hay gặp

**Sao không dùng RSI quá bán để mua đáy?** Đã thử: mean-reversion theo chỉ báo cổ điển thắng nhiều lần nhưng lỗ tổng
sau phí (bẫy winrate). Coin "quá bán" thường đang có lý do để giảm.

**Winrate 70% có tốt không?** Chưa biết. Phải xem kỳ vọng theo R sau phí. Trend-following có winrate 30–45% vẫn có
lãi vì lệnh thắng lớn hơn nhiều lệnh thua.

**Sao kết quả backtest đẹp mà không dùng?** Vì có thể là may (nhiều lần thử), overfit, lookahead, survivorship, hoặc
quên phí. Chỉ tin khi qua dải phát triển một lần, kiểm định tiến và cổng đã chốt.

**Sao phải chờ tới 2 năm cho trend v2?** Sharpe là tỷ số nhiễu: với Sharpe thật 1, một năm dữ liệu cho sai số khoảng
±1. Muốn phân biệt "có lợi thế" với "may mắn" cần nhiều năm.

**Sao 4 lệnh short cùng lúc chỉ được 1% tổng?** Chúng đến từ cùng một cú sập thị trường: nếu thị trường hồi, cả 4 cùng
thua. Đó là một cược, không phải bốn.

**Sao không dùng mẫu nến/price action?** Đã đo từng lớp trên top-20 khung 4h: không lớp nào có lợi thế sau phí. Hình
dạng nến là hàm của giá đã qua; điều làm giá chạy là dòng lệnh (mục 5), và phần dự báo được của nó rất ngắn.

**"Chưa tới mốc, không kết luận" nghĩa là gì?** Số liệu hiện có chỉ để theo dõi. Chỉ đến ngày mốc (đã chốt trước khi có
dữ liệu) mới đánh giá đạt hay không, để tránh dừng lúc may và chạy tiếp lúc xui.

## 11. Tài liệu tham khảo
- Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum", JFE.
- Hurst, Ooi & Pedersen (2017), "A Century of Evidence on Trend-Following Investing", JPM.
- Liu & Tsyvinski (2021), "Risks and Returns of Cryptocurrency", RFS; Liu, Tsyvinski & Wu (2022), "Common Risk Factors
  in Cryptocurrency", JF.
- Zarattini, Pagani & Barbon (2025), "Catching Crypto Trends: A Tactical Approach for Bitcoin and Altcoins", SSRN
  5209907.
- Moreira & Muir (2017), "Volatility-Managed Portfolios", JF.
- Schmeling, Schrimpf & Todorov (2023), "Crypto Carry", BIS WP 1087.
- Brunnermeier & Pedersen (2009), "Market Liquidity and Funding Liquidity", RFS; Coval & Stafford (2007), "Asset Fire
  Sales", JFE; Nagel (2012), "Evaporating Liquidity", RFS.
- Cont, Kukanov & Stoikov (2014), "The Price Impact of Order Book Events", J. Financial Econometrics.
- Bailey & López de Prado (2014), "The Deflated Sharpe Ratio"; Bailey et al. (2017), "The Probability of Backtest
  Overfitting".
- Harvey, Liu & Zhu (2016), "... and the Cross-Section of Expected Returns", RFS (ngưỡng t > 3 khi thử nhiều).
- McLean & Pontiff (2016), "Does Academic Research Destroy Stock Return Predictability?", JF (lợi thế giảm sau khi công
  bố).
