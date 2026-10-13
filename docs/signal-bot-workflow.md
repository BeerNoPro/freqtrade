# Thiết kế bot tín hiệu Futures (nội bộ)

> Bot chỉ phát setup thật từ luật **đã qua kiểm định**. Chưa có luật đạt thì setup chỉ là thử nghiệm, gắn nhãn rõ.
> Bộ máy nghiên cứu: [market-context-engine.md](market-context-engine.md). Kiến thức mô hình: [quant-playbook.md](quant-playbook.md).
> Engine Freqtrade (dùng ở giai đoạn tự vào lệnh): [lifecycle.md](lifecycle.md) · [structure.md](structure.md).

## 0. Quyết định đã chốt

| Hạng mục | Quyết định |
|---|---|
| Mục đích | Bot là một quant/trader có kỷ luật: tự đọc dữ liệu, **tự quyết setup LONG/SHORT**, gửi qua Telegram. Giai đoạn 2 mới tự vào lệnh |
| Sản phẩm | Setup hoàn chỉnh: coin, chiều, giá vào, SL, TP hoặc luật thoát, khối lượng theo ngân sách rủi ro, đòn bẩy tối đa ≤ 5x isolated, kỳ vọng (bằng chứng thật của luật), lý do; theo dõi tới khi đóng; tổng kết tuần |
| Ngân sách rủi ro | Vốn mẫu 10.000 USDT. Một cược sự kiện 1% vốn (setup cùng nến, cùng chiều chia nhau), trend 0,25%/vị thế, trần mỗi chiều 5%, trần tổng 8% (mục 4) |
| Thị trường | Binance USDT-M perpetual, chỉ coin crypto (không perp cổ phiếu, hàng hóa, stablecoin) |
| Dữ liệu | Phát triển trên **2025 → nay**; khối lịch sử khóa 2023–2024 chỉ chạy một lần luật đã chốt; danh mục point-in-time |
| Kiểm định | Chốt luật trước (fingerprint) → chạy dải phát triển một lần → kiểm định tiến trên dữ liệu sau ngày chốt → dry-run ≥ 4 tuần |
| Khung thời gian | Theo luật được kiểm định (4h cho sự kiện, ngày cho xu hướng). Chi phí phải nhỏ so với 1R: setup có phí + trượt giá > 0,20R bị bỏ |
| Nơi chạy | VPS 24/7 (docker) hoặc máy local (task Windows) |
| Nơi nhận tin | Bot Telegram riêng, **chỉ gửi**, không nhận lệnh điều khiển. Nhãn "THỬ NGHIỆM" theo từng luật cho tới khi luật đạt mốc |
| Giao diện | Giữ API server + WebSocket của Freqtrade (FreqUI); nhật ký setup, sổ rủi ro và độ phủ dữ liệu là nguồn cho màn hình UI sau này |

## 1. Giải thích dễ hiểu
- Bot giống một **phòng thí nghiệm + một người vào lệnh có kỷ luật**. Mỗi ý tưởng vào lệnh ("sập mạnh rồi hồi",
  "phá đỉnh thì đi tiếp"...) được viết thành luật cụ thể, kiểm trên dữ liệu thật, rồi mới được phép phát setup.
- Phần lớn ý tưởng phổ biến không có lãi sau phí. Đó là kết quả bình thường: luật đơn giản mà dễ kiếm tiền thì cả thị
  trường đã dùng và lợi thế đã biến mất. Bot phải nói thẳng khi không có lợi thế.
- **Kiểm định tiến**: chốt luật hôm nay, để dữ liệu từ hôm nay trở đi chấm điểm. Không ai (kể cả agent) sửa được luật
  hay nhật ký sau khi chốt, nên không thể "làm đẹp" kết quả.
- Lợi thế nhỏ cần nhiều thời gian mới chứng minh được: 6 tháng chỉ phát hiện được lợi thế rất lớn. Vì vậy kết luận chỉ
  đưa ra ở các mốc đã chốt.
- **Ngân sách rủi ro** là phần "người vào lệnh": nhiều tín hiệu cùng lúc thường là một cú sốc thị trường duy nhất, nên
  bot gộp chúng thành một cược và giữ tổng rủi ro mở trong trần, thay vì đặt 1% cho mỗi tín hiệu.

## 2. Workflow

```
Nghiên cứu (scripts/research, user_data/research)
  giả thuyết + cơ chế + bằng chứng → fingerprint → dải phát triển MỘT lần → đạt cổng?
                                                                            │ đạt
Runner kiểm định tiến 24/7 (run_forward.py)                                ▼
 ├─ Mỗi 4h / mỗi ngày: cập nhật dữ liệu công khai Binance (nến, OI, funding, giá mua/bán, sổ lệnh)
 ├─ Luật đã chốt → setup đầy đủ → ghi nhật ký TRƯỚC khi biết kết quả (không sửa được)
 ├─ Chấm điểm khi setup đóng (phí + funding), đối chiếu với bản chạy lại trên kho lưu trữ
 └─ Lớp sản phẩm: ngân sách rủi ro → tin setup → theo dõi tới khi đóng → tổng kết tuần → Telegram (mục 3–7)
                                                                            │ đạt mốc + dry-run ≥ 4 tuần
Giai đoạn 2: strategy Freqtrade gọi lại chính các hàm `mce` (một nguồn code), tự vào lệnh vốn nhỏ
```

## 3. Luồng thử nghiệm: trạng thái của một setup

```
nến đóng → runner cập nhật kho → luật đã chốt
  → [MỚI] journal.sqlite: chỉ ghi thêm, trước kết quả; nguồn chấm luật, lớp sản phẩm chỉ đọc
  → lớp sản phẩm (sổ riêng product.sqlite), xét theo thứ tự:
       quyết định trước ngày mở sổ             → [NGOÀI SỔ]
       chi phí > 0,20R (nhật ký: skipped_cost)  → [BỎ QUA]
       ghi trễ > 15 phút sau đóng nến (live_late) → [TRỄ]     (không vào lệnh)
       trend: coin đã có vị thế trend trong sổ  → [TRÙNG]
       ngân sách: một cược → trần chiều → trần tổng
                                                → [ĐÃ GỬI] | [GỬI GIẢM] | [CHẶN: hết ngân sách]
  [ĐÃ GỬI], [GỬI GIẢM] = [ĐANG MỞ]: chiếm ngân sách tới khi đóng
  → SL / TP / hết giờ / luật trend thoát / coin ngừng giao dịch
  → [ĐÃ ĐÓNG]: R ròng (sau phí + funding); lãi/lỗ % vốn = rủi ro thực × R ròng → tin đóng lệnh
  → tổng kết tuần (thứ Hai 00:00 UTC)
```
- Nhật ký chấm **mọi** setup của luật, kể cả setup bị sổ chặn: kết luận về luật không phụ thuộc ngân sách. Sổ trả lời
  câu hỏi khác: người theo bot vào lệnh nào, cỡ bao nhiêu, lãi/lỗ bao nhiêu % vốn.
- Mỗi lớp quyết định một lần lúc runner xử lý setup; sổ cũng chỉ ghi thêm, chạy lại trên cùng nhật ký cho kết quả y hệt.
- Coin ngừng giao dịch khi lệnh còn mở: nhật ký không có kết quả; sổ đóng vị thế không giá (`no_price`), lãi/lỗ để
  trống.
- Vị thế ngược chiều ở hai mảng khác nhau trên cùng coin (trend LONG, sự kiện SHORT) được phép; tin nhắn ghi chú cần
  chế độ hedge của Binance.

## 4. Ngân sách rủi ro danh mục
Đơn vị: phần vốn mẫu mất nếu giá chạm SL. Con số chốt 2026-10-13 (gói "Cân bằng"):

| Luật | Giá trị |
|---|---|
| Một cược sự kiện (luật khung 4h) | Setup cùng thời điểm quyết định và cùng chiều là **một cược**: cả cược 1%, chia đều (4 short cùng nến → 0,25% mỗi lệnh) |
| Trend (luật khung ngày) | 0,25% mỗi vị thế (20 coin → tối đa 5%); mỗi coin một vị thế trend, dùng chung cho mọi luật trend |
| Trần theo chiều | Tổng rủi ro mở LONG ≤ 5%, SHORT ≤ 5% |
| Trần tổng | ≤ 8% |
| Vượt trần | Cược mới thu nhỏ cho vừa phần còn lại; dưới 0,1% mỗi setup thì chặn cả cược |
| Thứ tự trong một thời điểm | Trend trước (theo luật, cặp), rồi cược LONG, rồi cược SHORT |
| Khối lượng | min(vốn × rủi ro cấp / \|vào − SL\|, vốn × đòn bẩy tối đa / vào); sổ ghi rủi ro thực |

Vì sao:
- Tín hiệu sự kiện đến theo cụm: hơn một nửa setup 4h xuất hiện cùng nến, cùng chiều với setup khác (một cú sập
  chung). Tương quan đo lúc bình thường (trung vị ~0,5) thấp hơn lúc sập, nên gộp theo "cùng cú sốc" thay cho ngưỡng
  tương quan.
- Luật trend giữ vị thế ở gần như mọi coin top-20 cùng lúc. Mỗi vị thế 1% sẽ thành ~20% rủi ro mở; 0,25% mỗi vị thế
  gần với tỷ trọng 1/20 của chính luật.
- Mô phỏng trên setup từ 2026-04 (chỉ đếm, không dùng lãi/lỗ): trend vào đủ 97%; sự kiện vào đủ 85%, giảm 10%, chặn
  5%; rủi ro mở trung vị 5%, p90 6%. Giữ 1% mỗi setup, không gộp thì trần chặn ~2/3 số setup.

## 5. Tin nhắn
Mọi tin ghi vào `user_data/research/forward/messages.log`. Telegram chỉ nhận **tin cần làm**: setup được vào (đủ hoặc
giảm), đóng lệnh, tổng kết tuần, cảnh báo vận hành. Setup bị chặn/trễ/bỏ qua chỉ ghi log và được đếm trong tổng kết.
Ví dụ (số minh họa):

```
THỬ NGHIỆM - luật chưa đạt kiểm định, không giao dịch theo
SHORT SOLUSDT · (c) bán theo cú sập 4h [shock_no_oi_drop_short_4h@v1]
Nến 4h đóng 2026-10-12 16:00 UTC
Vào 140 (giá đóng nến) · SL 143.5 (+2.5%) · TP 133 (-5.0%) · thoát sau 12h nếu chưa chạm
Khối lượng 7.14286 (1,000 USDT): rủi ro 0.25% vốn mẫu 10,000 USDT (1/4 của một cược 1%: 4 lệnh SHORT cùng nến) · đòn bẩy tối đa 5x isolated
Chi phí ước tính 0.05R
Kỳ vọng: chưa tới mốc, không kết luận.
- Khối 1 (2026-04→10): <n> setup, <R TB>/setup (CI <thấp>→<cao>)
- Trực tiếp: <n> setup đã đóng, TB <R>
- Mốc: 200 sự kiện sau IS (<đã có>/200), hạn 2028-01-01
Rủi ro mở sau chu kỳ: LONG 0.50%/5% · SHORT 1.00%/5% · tổng 1.50%/8%
Lý do: Bán theo cú sập không kèm thanh lý: nến 4h -2.3 ATR, OI z -1.2
```
```
THỬ NGHIỆM · ĐÓNG SHORT SOLUSDT · (c) bán theo cú sập 4h [shock_no_oi_drop_short_4h@v1]
Chạm TP · vào 140 (2026-10-12 16:00 UTC) → ra 133 (2026-10-13 00:00 UTC), giữ 8h
R gộp +2.00 · phí -0.05 · funding +0.01 → R ròng +1.96
Sổ: rủi ro 0.25% → +0.49% vốn (+49 USDT)
```
- **Kỳ vọng** = bằng chứng thật của luật đến nay: dải phát triển và khối 1 như báo cáo đã công bố, số trực tiếp từ nhật
  ký (n, R ròng trung bình, CI theo tuần khi n ≥ 10), tiến độ tới mốc kết luận. Trước mốc luôn ghi "không kết luận".
- **Tổng kết tuần** (thứ Hai 00:00 UTC): theo luật (setup mới theo trạng thái, đã đóng, R, tiến độ mốc); sổ (lãi/lỗ
  tuần và lũy kế, sụt giảm lớn nhất, vị thế đang mở); vận hành (chu kỳ đúng giờ, ngày bộ ghi đủ ≥ 90%).
- **Bỏ nhãn**: khi một luật đạt mốc, đặt `validated: true` cho luật đó trong `user_data/research/product.json`.

## 6. Telegram: chỉ gửi
1. Mở @BotFather → `/newbot` → đặt tên → nhận **token**.
2. Nhắn một tin bất kỳ cho bot vừa tạo.
3. Lấy **chat_id**: mở `https://api.telegram.org/bot<TOKEN>/getUpdates`, tìm `"chat":{"id": ...}`.
4. Ghi vào file `.env` ở gốc repo (git bỏ qua, không commit): `TELEGRAM_BOT_TOKEN=<token>` và
   `TELEGRAM_ADMIN_CHAT_IDS=<chat_id>` (lấy id đầu tiên). Runner tìm lần lượt: biến môi trường của tiến trình
   (`FREQTRADE__TELEGRAM__TOKEN` / `..._CHAT_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_CHAT_IDS`),
   rồi `.env`, rồi `telegram.token` / `telegram.chat_id` trong `user_data/config.json`. **Giữ
   `telegram.enabled = false`**: đó là công tắc bot điều khiển của Freqtrade (nhận lệnh), runner không dùng nó.
5. Kiểm tra bằng `run_forward.py --telegram-test` (gửi 1 tin), rồi bật gửi: `telegram_send: true` trong
   `user_data/research/product.json` và khởi động lại runner. Trên VPS: đưa các biến trên vào môi trường của service
   `forward` (ví dụ `env_file: .env` trong docker-compose).

Runner chỉ gọi `sendMessage`, không đọc tin đến (`getUpdates`) nên không ai điều khiển được bot. Token bị che trong mọi
log và lỗi. Tin chờ gửi quá 6 giờ (mất mạng lâu) thì bỏ, vẫn còn trong `messages.log`; khi tắt gửi, tin chỉ lưu cục bộ
và bật lại không xả tin cũ. Nếu đưa bot vào group: đặt quyền chỉ admin được nhắn.

## 7. Cảnh báo vận hành
| Cảnh báo | Khi nào |
|---|---|
| Chu kỳ lỗi | Exception trong chu kỳ (gộp cùng loại lỗi trong 6 giờ); runner thử lại ở chu kỳ sau |
| Runner trễ / nghỉ | Một chu kỳ chạy trễ > 15 phút sau đóng nến lần đầu, hoặc khi khởi động thấy đã nghỉ > 4h15 |
| Bộ ghi ngừng | Nhịp tim `live/.heartbeat` cũ hơn 15 phút |
| Bộ ghi thiếu dữ liệu | Ngày UTC hôm trước có luồng phủ < 90% |
| Lệch đối chiếu | Nhật ký khác bản chạy lại trên kho (`reconcile_issues.csv`) |
| Fingerprint | Luật trên đĩa khác bản đã đăng ký: runner không ghi setup |

## 8. Ánh xạ sang Freqtrade (giai đoạn 2, không sửa core)

| Thiết kế | Cài đặt trong Freqtrade |
|---|---|
| Luật vào | `populate_indicators` gọi các hàm `mce` (features, regime, hook) → `populate_entry_trend` |
| Khối lượng theo ngân sách | `custom_stake_amount` gọi `mce.riskbook.allocate` với vị thế đang mở |
| SL / TP / thoát theo thời gian | `stoploss` / `custom_stoploss`, `custom_exit` |
| Đòn bẩy | `leverage()` cố định theo chính sách, isolated, `liquidation_buffer` ≥ 0,05 |
| Danh mục | `pairlists` tương đương luật point-in-time |
| Tin nhắn | `self.dp.send_msg(...)`; theo dõi qua `/profit`, `/performance`, FreqUI |

Strategy đặt trong `user_data/strategies/`; khi chốt tên, cập nhật `run.ps1` và `docker-compose.yml`. Kiểm chứng:
skill `/validate-strategy` (lookahead, recursive, backtest). Secret chỉ trong config gitignore hoặc `FREQTRADE__...`.

## 9. Lộ trình

| Bước | Việc | Ai làm |
|---|---|---|
| 0 | Tìm luật có lợi thế: chốt trước, dải phát triển một lần, kiểm định tiến. Trạng thái: `user_data/research/STATE.md` | Claude |
| 1 | Luồng thử nghiệm: ngân sách rủi ro, tin đầy đủ, theo dõi tới khi đóng, tổng kết, cảnh báo (mục 3–7). Xong 2026-10-13 | Claude |
| 2 | Bật Telegram thử nghiệm (nhãn rõ). Bật 2026-10-13 | Bạn duyệt |
| 3 | Chạy VPS 24/7 (`docker compose up -d --build recorder forward`) | Bạn (Claude hướng dẫn) |
| 4 | Luật đạt mốc → bỏ nhãn luật đó, dry-run ≥ 4 tuần, so với bản chạy lại | Cùng làm |
| 5 | Giai đoạn 2: port Freqtrade, tự vào lệnh với vốn nhỏ | Quyết định sau |
| — | Màn hình UI (setup, nhật ký, sổ rủi ro, độ phủ dữ liệu) trên API server / FreqUI | Khi cần |
