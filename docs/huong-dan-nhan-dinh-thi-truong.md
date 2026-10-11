# Hướng dẫn dễ hiểu: hệ thống nhận định thị trường

Tài liệu cho người **không chuyên quant**. Bản kỹ thuật đầy đủ: [market-context-engine.md](market-context-engine.md).

## 1. Hệ thống này là gì?

Hãy coi nó là một **phòng thí nghiệm kiểm tra kinh nghiệm giao dịch**, không phải một cỗ máy in tiền.

Người giao dịch hay có những câu kiểu "RSI dưới 30 là sắp hồi" hay "phá đỉnh 20 nến là đi tiếp". Hệ thống lấy từng
câu như vậy, tìm **mọi lần** nó xảy ra trong vài năm dữ liệu của 15 đồng coin, rồi xem sau đó giá thật sự đi đâu.
Kết quả là một câu trả lời có số liệu: kinh nghiệm này đúng, sai, hay chỉ là may mắn.

**Nó làm được:**
- Kiểm tra bất kỳ chỉ báo hay quy tắc nào định nghĩa được bằng công thức.
- Cho biết kết quả có ổn định qua từng năm, từng coin không, và có còn lời sau phí không.
- Ghi lại các nhận định *trước khi* biết kết quả (journal), rồi tự chấm điểm. Đây là cách trung thực nhất để biết bot
  đúng đến đâu với dữ liệu mới.

**Nó không làm được:**
- Nhìn biểu đồ rồi bình luận như người.
- Bảo đảm lợi nhuận. Đa số kinh nghiệm phổ biến sẽ bị nó "bác bỏ". Đó là chuyện bình thường: nếu một quy tắc đơn giản
  kiếm tiền dễ dàng thì cả thị trường đã dùng nó và lợi thế đã biến mất.

**Kỳ vọng thực tế:** trong crypto, thứ có bằng chứng tốt nhất là **đi theo xu hướng ở khung thời gian lớn** (4h, ngày),
quản lý rủi ro chặt. Cách này thắng ít lệnh hơn thua, nhưng lệnh thắng lớn hơn nhiều so với lệnh thua. Chỉ báo hữu ích
nhất khi dùng để **đo trạng thái thị trường** (đang có xu hướng hay đi ngang, biến động cao hay thấp), hơn là để đoán
đỉnh đáy.

## 2. Bức tranh lớn

```
 Dữ liệu nến + funding        Chỉ báo / đặc trưng          "Hook" = một kinh nghiệm
 (Binance futures)      ──►   (ATR, EMA, RSI, ...)   ──►   có tên, có điều kiện rõ ràng
                                                                   │
                                                                   ▼
 Journal: ghi nhận định  ◄──  Báo cáo: đúng / sai /  ◄──  Đo: sau mỗi lần xảy ra,
 trước, chấm điểm sau         may mắn?                     giá đi đâu trong N nến?
```

Code nằm ở `scripts/research/mce/`. Các kinh nghiệm cụ thể và kết quả nằm trong `user_data/` (riêng tư, không đưa
lên GitHub).

## 3. Từ điển thuật ngữ

| Thuật ngữ | Nghĩa bằng lời thường |
|---|---|
| **ATR** | Biên độ dao động trung bình của một cây nến. Coin "hiền" thì ATR nhỏ, coin "dữ" thì ATR lớn |
| **R** | Đơn vị đo bằng ATR. "+1R" = giá đi thêm một ATR. Dùng R để so được BTC với DOGE trên cùng thước đo |
| **Hook** | Một kinh nghiệm viết thành công thức, ví dụ "RSI cắt xuống dưới 30 → kỳ vọng tăng trong 4 nến" |
| **Horizon** | Sau bao nhiêu nến thì chấm kết quả |
| **Hit rate** | Bao nhiêu phần trăm số lần giá đi đúng hướng |
| **Baseline** | Giá "bình thường" đi đâu khi không có tín hiệu, trong cùng hoàn cảnh thị trường |
| **Lift** | Tín hiệu tốt hơn baseline bao nhiêu (tính bằng R). Lift ≈ 0 nghĩa là tín hiệu không hơn gì đoán bừa |
| **Regime** | Trạng thái thị trường: xu hướng tăng / giảm / đi ngang, biến động cao / thấp |
| **Phí theo R** | Phí giao dịch quy ra R. Ở nến 1h, phí khứ hồi chiếm khoảng 0,1–0,2R; ở nến 4h chỉ khoảng nửa mức đó |
| **Khoảng tin cậy (CI)** | Khoảng mà kết quả thật nhiều khả năng nằm trong. Nếu CI chứa số 0 thì chưa loại được khả năng do may mắn |
| **Bootstrap** | Cách tính CI bằng cách "xáo lại" dữ liệu hàng nghìn lần, theo từng tuần để không tự lừa mình |
| **BH (Benjamini–Hochberg)** | Hiệu chỉnh khi thử nhiều ý tưởng cùng lúc. Thử 100 ý tưởng thì kiểu gì cũng có vài cái "trông đẹp" do may mắn; BH loại bớt những cái đó |
| **Causal / lookahead** | Causal = chỉ dùng thông tin đã có tại thời điểm đó. Lookahead = vô tình dùng dữ liệu tương lai, làm backtest đẹp giả tạo. Hệ thống có test tự động chặn lỗi này |
| **IS / holdout** | IS (2023-01 → 2025-06) dùng để nghiên cứu. Holdout (2025-07 → nay) được khóa lại và chỉ mở **một lần** để kiểm tra cuối |
| **Fingerprint** | "Dấu vân tay" của bộ kinh nghiệm, ghi lại trước khi chạy. Nó chứng minh không ai sửa công thức sau khi đã thấy kết quả |
| **Brier skill** | Điểm cho dự báo xác suất. > 0 nghĩa là dự báo tốt hơn câu trả lời "lúc nào cũng nói tỉ lệ trung bình" |

## 4. Ví dụ từng bước: "RSI < 30 thì sắp hồi"

1. **Viết thành công thức:** khi RSI(14) cắt xuống dưới 30 → dự đoán giá tăng trong 4 nến tới.
2. **Tìm mọi lần xảy ra:** trên 15 coin, nến 1h, từ 2023 đến giữa 2025. Ví dụ tìm được 8.000 lần.
3. **Đo mỗi lần:** giá sau 4 nến đi lên hay xuống, bao nhiêu R. Có trừ phí.
4. **So với baseline:** trong cùng hoàn cảnh (ví dụ thị trường đang giảm, biến động cao), giá thường đi đâu?
5. **Đọc kết quả.** Một dòng trong bảng trông như sau (số **minh họa**):

| hook | n | hit_rate | lift_r | lift_lo … lift_hi | net_r | year_share |
|---|---|---|---|---|---|---|
| rsi_extreme_1h | 8000 | 0.55 | −0.02 | −0.10 … +0.06 | −0.18 | 1.0 |

   Đọc là: thắng 55% số lần (nghe có vẻ tốt), nhưng tính trung bình không hơn đoán bừa (lift ≈ 0, CI chứa 0). Sau phí
   thì lỗ 0,18R mỗi lần. Đây là **bẫy winrate**: thắng nhiều lần nhỏ, thua ít lần nhưng lớn.
6. **Kết luận:** kinh nghiệm này không dùng được ở dạng đứng riêng.

Một kinh nghiệm chỉ được coi là "đạt" khi đủ cả bốn điều: ≥ 200 lần xảy ra; lift có ý nghĩa sau hiệu chỉnh BH; cùng
chiều ở ít nhất 2/3 số năm và 60% số coin; còn lời sau phí.

## 5. Tự thêm một chỉ báo

1. Mở (hoặc copy) file hook mẫu trong `user_data/research/`, ví dụ `hooks_indicators_v1.py`.
2. Viết hàm điều kiện, trả về +1 (kỳ vọng tăng), −1 (kỳ vọng giảm) hoặc 0:
   ```python
   def my_idea(df, p):
       rsi = pd.Series(ta.RSI(df, timeperiod=14), index=df.index)
       return _sig(long=rsi < 25, short=rsi > 75)
   ```
3. Đăng ký kèm tên, phiên bản, giả thuyết một câu, horizon. **Chốt tham số trước khi chạy.**
4. Chạy (từ thư mục `scripts/research`):
   ```bash
   ../../.venv/Scripts/python.exe run_event_study.py --hooks ../../user_data/research/<file>.py \
       --datadir ../../user_data/data/binance --out ../../user_data/reports --timeframe 4h --horizons 1 3 6 12 --tag <tên>
   ```
5. Đọc `user_data/reports/event-study-<tên>.md` theo cách ở mục 4.

**Luật vàng:** nếu kết quả xấu, **không** sửa tham số rồi chạy lại trên cùng dữ liệu cho đến khi đẹp. Làm vậy chỉ là
tìm ra sự trùng hợp. Muốn thử ý mới thì tạo phiên bản mới (v2), ghi fingerprint, chạy một lần.

## 6. Câu hỏi thường gặp

**Vì sao hệ thống hầu như không tìm ra kinh nghiệm nào "đạt"?**
Vì nó khắt khe đúng mức cần thiết. Phần lớn kinh nghiệm phổ biến chỉ là biến đổi của chính giá, và mọi người đều nhìn
thấy chúng. Một bộ lọc nói "không" với 95% ý tưởng là bộ lọc đang bảo vệ tiền của bạn.

**Vì sao khung 1h khó hơn 4h?**
Phí tính theo phần trăm giá, còn một nến 1h dao động ít. Vì vậy phí "ăn" phần lớn lợi nhuận nhỏ của từng tín hiệu.
Ở 4h, mỗi nến đi xa hơn nên phí chiếm tỉ lệ nhỏ hơn. Các nghiên cứu trong repo cho thấy chỉ báo theo xu hướng ở 4h
nhỉnh hơn hẳn chính chỉ báo đó ở 1h.

**Vì sao không tối ưu tham số cho đẹp?**
Tối ưu trên dữ liệu cũ là "học thuộc bài cũ". Trên dữ liệu mới, tham số tối ưu thường thua tham số mặc định. Hệ thống
ưu tiên ít tham số, chốt trước, kiểm tra trên dữ liệu chưa thấy.

**Thế thì dùng hệ thống này để làm gì?**
- Loại nhanh các ý tưởng không có giá trị, trước khi mất tiền thật vì chúng.
- Tìm ra số ít điều kiện thật sự có ích, dùng làm **bộ lọc** cho tín hiệu của chiến lược chính.
- Đo trạng thái thị trường (biến động cao/thấp) để đặt SL/TP và khối lượng hợp lý. Đây là điều hệ thống làm tốt nhất.

**Bot tín hiệu chính dựa vào đâu?**
Một chiến lược nền đi theo xu hướng ở khung 4h. Mọi cải tiến về sau (setup 1h, bộ lọc nhận định) chỉ được giữ lại nếu
thắng chiến lược nền trên cùng dữ liệu. Xem [signal-bot-workflow.md](signal-bot-workflow.md).
