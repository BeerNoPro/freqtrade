# Thiết kế bot tín hiệu Futures (nội bộ)

> Bot chỉ phát setup thật từ luật **đã qua kiểm định**. Chưa có luật đạt thì setup chỉ là thử nghiệm, gắn nhãn rõ.
> Bộ máy nghiên cứu: [market-context-engine.md](market-context-engine.md). Kiến thức mô hình: [quant-playbook.md](quant-playbook.md).
> Engine Freqtrade (dùng ở giai đoạn tự vào lệnh): [lifecycle.md](lifecycle.md) · [structure.md](structure.md).

## 0. Quyết định đã chốt

| Hạng mục | Quyết định |
|---|---|
| Mục đích | Bot là một quant/trader có kỷ luật: tự đọc dữ liệu, **tự quyết setup LONG/SHORT**, gửi qua Telegram. Giai đoạn 2 mới tự vào lệnh |
| Sản phẩm | Setup hoàn chỉnh: coin, chiều, giá vào, SL, TP hoặc luật thoát, khối lượng theo rủi ro 1% vốn, đòn bẩy tối đa ≤ 5x isolated, kỳ vọng, lý do |
| Thị trường | Binance USDT-M perpetual, chỉ coin crypto (không perp cổ phiếu, hàng hóa, stablecoin) |
| Dữ liệu | Chỉ đánh giá trên **2025 → nay**; danh mục point-in-time; không dùng dữ liệu cũ hơn để đánh giá |
| Kiểm định | Chốt luật trước (fingerprint) → chạy dải phát triển một lần → kiểm định tiến trên dữ liệu sau ngày chốt → dry-run ≥ 4 tuần |
| Khung thời gian | Theo luật được kiểm định (4h cho sự kiện, ngày cho xu hướng). Chi phí phải nhỏ so với 1R: setup có phí + trượt giá > 0,20R bị bỏ |
| Nơi chạy | VPS 24/7 (docker) hoặc máy local (task Windows) |
| Nơi nhận tin | Bot Telegram riêng (@BotFather); nhãn "THỬ NGHIỆM" cho tới khi luật qua kiểm định |
| Giao diện | Giữ API server + WebSocket của Freqtrade (FreqUI) và nhật ký setup/độ phủ dữ liệu làm nguồn cho màn hình UI sau này |

## 1. Giải thích dễ hiểu
- Bot giống một **phòng thí nghiệm + một người vào lệnh có kỷ luật**. Mỗi ý tưởng vào lệnh ("sập mạnh rồi hồi",
  "phá đỉnh thì đi tiếp"...) được viết thành luật cụ thể, kiểm trên dữ liệu thật, rồi mới được phép phát setup.
- Phần lớn ý tưởng phổ biến không có lãi sau phí. Đó là kết quả bình thường: luật đơn giản mà dễ kiếm tiền thì cả thị
  trường đã dùng và lợi thế đã biến mất. Bot phải nói thẳng khi không có lợi thế.
- **Kiểm định tiến**: chốt luật hôm nay, để dữ liệu từ hôm nay trở đi chấm điểm. Không ai (kể cả agent) sửa được luật
  hay nhật ký sau khi chốt, nên không thể "làm đẹp" kết quả.
- Lợi thế nhỏ cần nhiều thời gian mới chứng minh được: 6 tháng chỉ phát hiện được lợi thế rất lớn. Vì vậy kết luận chỉ
  đưa ra ở các mốc đã chốt.

## 2. Workflow

```
Nghiên cứu (scripts/research, user_data/research)
  giả thuyết + cơ chế + bằng chứng → fingerprint → dải phát triển MỘT lần → đạt cổng?
                                                                            │ đạt
Runner kiểm định tiến 24/7 (run_forward.py)                                ▼
 ├─ Mỗi 4h / mỗi ngày: cập nhật dữ liệu công khai Binance (nến, OI, funding, giá mua/bán, sổ lệnh)
 ├─ Luật đã chốt → setup đầy đủ → ghi nhật ký TRƯỚC khi biết kết quả (không sửa được)
 ├─ Chấm điểm khi setup đóng (phí + funding), đối chiếu với bản chạy lại trên kho lưu trữ
 └─ Telegram (khi người dùng duyệt): tin setup, nhãn thử nghiệm cho tới khi đạt mốc
                                                                            │ đạt mốc + dry-run ≥ 4 tuần
Giai đoạn 2: strategy Freqtrade gọi lại chính các hàm `mce` (một nguồn code), tự vào lệnh vốn nhỏ
```

## 3. Tin nhắn Telegram (setup)
```
THỬ NGHIỆM - chưa kiểm định, không giao dịch theo
LONG SOLUSDT (liquidation_bounce_4h@v1) - nến 4h đóng 2026-10-12T16:00:00+00:00
Vào: 142.3 (giá đóng nến) · SL 136.9 (-3.8%) · TP 153.1 (+7.6%), thoát sau 12h nếu chưa chạm
Khối lượng: 18.5 (2,633 USDT) cho rủi ro 1% vốn mẫu · đòn bẩy tối đa 5x isolated
Chi phí ước tính 0.04R · Kỳ vọng: chưa kiểm định
Lý do: Mua sau thanh lý: nến 4h sập -2.4 ATR, OI z -2.6 (bán cưỡng bức)
```
Định dạng do `scripts/research/run_forward.py::message` tạo; hiện chỉ ghi ra `user_data/research/forward/messages.log`.

## 4. Cài đặt Telegram (BotFather)
1. Mở @BotFather → `/newbot` → đặt tên → nhận **token**.
2. Nhắn một tin bất kỳ cho bot vừa tạo.
3. Lấy **chat_id**: mở `https://api.telegram.org/bot<TOKEN>/getUpdates`, tìm `"chat":{"id": ...}`.
4. Điền vào config (không commit): `telegram.enabled = true`, `token`, `chat_id`.
5. Nếu đưa vào group: đặt `authorized_users` = chỉ ID của bạn, để người khác không gửi được lệnh điều khiển.

## 5. Ánh xạ sang Freqtrade (giai đoạn 2, không sửa core)

| Thiết kế | Cài đặt trong Freqtrade |
|---|---|
| Luật vào | `populate_indicators` gọi các hàm `mce` (features, regime, hook) → `populate_entry_trend` |
| Khối lượng 1% rủi ro | `custom_stake_amount` theo khoảng cách SL |
| SL / TP / thoát theo thời gian | `stoploss` / `custom_stoploss`, `custom_exit` |
| Đòn bẩy | `leverage()` cố định theo chính sách, isolated, `liquidation_buffer` ≥ 0,05 |
| Danh mục | `pairlists` tương đương luật point-in-time |
| Tin nhắn | `self.dp.send_msg(...)`; theo dõi qua `/profit`, `/performance`, FreqUI |

Strategy đặt trong `user_data/strategies/`; khi chốt tên, cập nhật `run.ps1` và `docker-compose.yml`. Kiểm chứng:
skill `/validate-strategy` (lookahead, recursive, backtest). Secret chỉ trong config gitignore hoặc `FREQTRADE__...`.

## 6. Lộ trình

| Bước | Việc | Ai làm |
|---|---|---|
| 0 | Tìm luật có lợi thế: chốt trước, dải phát triển một lần, kiểm định tiến. Trạng thái: `user_data/research/STATE.md` | Claude |
| 1 | Bật Telegram thử nghiệm (nhãn rõ) | Bạn duyệt |
| 2 | Chạy VPS 24/7 (`docker compose up -d --build recorder forward`) | Bạn (Claude hướng dẫn) |
| 3 | Luật đạt mốc → dry-run ≥ 4 tuần, so với bản chạy lại | Cùng làm |
| 4 | Giai đoạn 2: port Freqtrade, tự vào lệnh với vốn nhỏ | Quyết định sau |
| — | Màn hình UI (setup, nhật ký, độ phủ dữ liệu) trên API server / FreqUI | Khi cần |
