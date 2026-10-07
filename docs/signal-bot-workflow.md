# Thiết kế Bot tín hiệu Futures (nội bộ)

> Bản thiết kế trước khi code. Các con số trong tài liệu là **giá trị khởi đầu**,
> sẽ được kiểm chứng và tinh chỉnh bằng backtest trước khi chạy thật.
> Liên quan: [lifecycle.md](lifecycle.md) · [structure.md](structure.md)

## 0. Các quyết định đã chốt

| Hạng mục | Quyết định |
|---|---|
| Mục đích | Bot **tính toán và phát tín hiệu**; giai đoạn 2 mới cho tự vào lệnh |
| Thị trường | Binance **USDT-M Futures** (perpetual) |
| Khung thời gian | **1h** ra tín hiệu, **4h** lọc xu hướng |
| Nền tảng | Freqtrade giữ nguyên core (phương án A); code riêng nằm trong `user_data/` |
| Nơi chạy | VPS 24/7, chế độ dry-run (không đặt lệnh thật) |
| Nơi nhận tin | Bot Telegram riêng tạo bằng @BotFather |
| Phạm vi | Dùng nội bộ |
| Chi phí | Khoảng 5–15 USD/tháng (VPS) |

---

## 1. Workflow tổng thể

```
[VPS 24/7 — Freqtrade dry-run]
Binance Futures API (công khai, không cần key)
 │
 ├─ Tầng 1 — Danh sách cặp (pairlist, cập nhật 30 phút)
 │    top 60 USDT-M theo khối lượng → bỏ coin < 30 ngày, sắp delist, spread > 0.2%
 │
 ├─ Tầng 2 — Điểm hoạt động (strategy, mỗi nến 1h)
 │    ATR% trong vùng cho phép, khối lượng đột biến → cặp "đáng xem"
 │
 ├─ Tầng 3 — Công thức tín hiệu (strategy)
 │    xu hướng 4h + bộ lọc BTC + setup 1h (Pullback hoặc Breakout) → điểm tín hiệu
 │
 ├─ Tầng 4 — Cổng rủi ro (confirm_trade_entry)
 │    funding, spread, số tín hiệu cùng chiều, khóa sau chuỗi thua (protections)
 │
 ├─ Tầng 5 — Phát tín hiệu → Telegram (bot BotFather)
 │    vùng vào, SL, TP1/TP2/TP3, đòn bẩy gợi ý, hạn hiệu lực, lý do
 │
 └─ Tầng 6 — Theo dõi tín hiệu (lệnh mô phỏng)
      khớp vùng vào? → TP1 / TP2 / trailing / SL / hết hạn / đóng sớm
      → báo từng sự kiện + tổng kết hằng ngày (winrate, tổng R)

Giai đoạn 2: dry_run = false + API key → bot tự vào lệnh với đúng logic trên
```

---

## 2. Chọn họ chiến lược

| Họ chiến lược | Winrate thường gặp | Ổn định ở crypto | Rủi ro chính | Đánh giá |
|---|---|---|---|---|
| **Theo xu hướng (trend-following)** | 35–50% | **Cao nhất** — động lượng là hiện tượng được ghi nhận bền vững ở crypto | Thua liên tiếp khi thị trường đi ngang | **Chọn** |
| Đảo chiều (mean-reversion) | 60–75% | Trung bình | Một cú sập/pump làm mất nhiều lệnh thắng; nguy hiểm khi có đòn bẩy | Không chọn |
| Grid / DCA | Cao | Thấp khi có xu hướng mạnh | Kẹt lệnh lỗ lớn, dễ cháy với futures | Không chọn |
| Scalping | Biến động | Thấp | Phí + độ trễ người vào tay | Không hợp với tín hiệu thủ công |

Lựa chọn: **theo xu hướng, vào ở nhịp hồi (pullback) hoặc phá vỡ có khối lượng**, cắt lỗ theo ATR, chốt lời theo bội số R.
- Lợi nhuận đến từ việc lệnh thắng lớn gấp 2–3 lần lệnh thua, không phải winrate cao.
- Vào ở nhịp hồi để cải thiện tỷ lệ R:R và winrate so với vào đuổi giá.
- **Không có chiến lược nào chắc thắng.** Chỉ chạy thật khi backtest và dry-run qua ngưỡng ở mục 7.

---

## 3. Công thức tín hiệu (giá trị khởi đầu)

### 3.1 Xu hướng 4h (regime)
| Trạng thái | Điều kiện |
|---|---|
| Tăng | EMA50 > EMA200, close > EMA50, ADX(14) > 20 |
| Giảm | EMA50 < EMA200, close < EMA50, ADX(14) > 20 |
| Không rõ | Còn lại → **không phát tín hiệu** |

### 3.2 Bộ lọc BTC (thị trường chung)
- Altcoin: chỉ LONG khi xu hướng 4h của BTC không phải "Giảm"; chỉ SHORT khi không phải "Tăng".
- Bỏ qua mọi tín hiệu nếu nến 1h gần nhất của BTC biến động > 3 × ATR(1h) của BTC (thị trường đang sốc).

### 3.3 Điểm hoạt động 1h
- ATR(14)/close trong khoảng **0,6% – 5%**: quá thấp thì phí ăn hết lợi nhuận, quá cao thì là pump/dump.
- Khối lượng nến tín hiệu ≥ 1,2 × SMA20 khối lượng.

### 3.4 Setup vào lệnh (khung 1h), ví dụ cho LONG — SHORT đối xứng
| Setup | Điều kiện |
|---|---|
| **A. Pullback** | Xu hướng 4h tăng; trong 5 nến gần nhất giá đã chạm vùng EMA20–EMA50 (1h); RSI(14) xuống dưới 45 rồi cắt lên lại trên 50; close > EMA20 |
| **B. Breakout** | Xu hướng 4h tăng; close > đỉnh cao nhất 20 nến trước; khối lượng ≥ 1,5 × trung bình; close nằm trong 30% phía trên của thân nến |

### 3.5 Điểm tín hiệu (0–100) — chỉ phát khi ≥ 60
| Thành phần | Tối đa |
|---|---|
| Độ mạnh xu hướng 4h (ADX, khoảng cách EMA50/EMA200) | 30 |
| Đồng thuận với BTC | 20 |
| Khối lượng đột biến | 20 |
| Chất lượng setup (RSI hồi đẹp / nến phá vỡ dứt khoát) | 20 |
| Khoảng trống tới vùng cản gần nhất (đỉnh/đáy 1h gần nhất) ≥ 2R | 10 |

---

## 4. Thiết lập lệnh (những gì một tín hiệu tốt cần có)

| Thành phần | Cách tính |
|---|---|
| **Giá vào** | Close của nến tín hiệu |
| **Vùng vào** | Giá vào ± 0,3 × ATR(1h) |
| **Hạn hiệu lực** | 2 nến 1h. Không khớp trong thời gian đó → báo "hết hiệu lực" |
| **Stoploss (SL)** | Ngay dưới đáy swing 10 nến − 0,2 × ATR, nhưng không gần hơn 1 × ATR và không xa hơn 2,5 × ATR. Khoảng cách SL phải nằm trong 1% – 6% giá |
| **R** | Khoảng cách từ giá vào tới SL |
| **TP1** | +1R → chốt 50%, **dời SL về giá vào** (hòa vốn) |
| **TP2** | +2R → chốt 30% |
| **TP3** | 20% còn lại chạy theo trailing 3 × ATR (chandelier) |
| **Đóng sớm** | Xu hướng 4h đổi trạng thái → báo "đóng sớm" |
| **Đòn bẩy gợi ý** | `min(5, 1 / (3 × khoảng cách SL%))`, isolated — giá thanh lý luôn xa hơn SL ≥ 3 lần |
| **Khối lượng gợi ý** | Rủi ro 1% tài khoản: giá trị vị thế = 1% vốn / khoảng cách SL% |
| **Không phát nếu** | Funding bất lợi > 0,05%; spread > 0,1%; đã có 3 tín hiệu mở cùng chiều; cặp vừa chạm SL trong 6 giờ qua |

---

## 5. Tin nhắn Telegram

**Tín hiệu mới**
```
🟢 LONG SOLUSDT · Pullback xu hướng · Điểm 78/100
Vùng vào: 142.10 – 143.20  (hiệu lực đến 15:00)
SL: 138.40 (-2.9%)
TP1: 146.60 (1R) → chốt 50%, dời SL về giá vào
TP2: 150.80 (2R) → chốt 30%
TP3: trailing 3×ATR cho 20% còn lại
Đòn bẩy gợi ý: ≤ 5x isolated · Rủi ro 1% vốn
Lý do: 4h tăng (ADX 27) · BTC 4h tăng · RSI 1h 41→52 · Volume x1.6
Funding +0.010% · Spread 0.02%
```

**Các tin theo dõi:** đã vào vùng · TP1 đạt (SL về hòa vốn) · TP2 đạt · đóng trailing · chạm SL · hết hiệu lực · đóng sớm do xu hướng đảo.
Mỗi tin ghi kết quả theo R (ví dụ `+1.8R`).

**Tổng kết hằng ngày (07:00):** số tín hiệu, tỷ lệ khớp, winrate, tổng R, R trung bình, chuỗi thua dài nhất, chi tiết theo setup A/B.

---

## 6. Ánh xạ sang Freqtrade (không sửa core)

| Thiết kế | Cài đặt trong Freqtrade |
|---|---|
| Tầng 1 | `pairlists`: `VolumePairList` → `DelistFilter` → `AgeFilter` → `SpreadFilter` |
| Xu hướng 4h, BTC | `@informative("4h")`, informative `BTC/USDT:USDT` |
| Setup, điểm | `populate_indicators` / `populate_entry_trend`, `enter_tag` = setup + điểm |
| Vùng vào + hạn hiệu lực | Lệnh limit mô phỏng tại giá vào (`custom_entry_price`) + `unfilledtimeout` 120 phút |
| SL, dời SL hòa vốn, trailing | `custom_stoploss` |
| TP1 / TP2 chốt một phần | `adjust_trade_position` (trả về số âm) |
| Đóng sớm | `populate_exit_trend` (xu hướng 4h đảo) |
| Cổng rủi ro | `confirm_trade_entry` + `protections` |
| Open interest (bộ lọc phụ) | `@informative(..., candle_type="open_interest")` — có từ Freqtrade 2026.x; Binance chỉ giữ 30 ngày lịch sử |
| Tin nhắn tín hiệu | `self.dp.send_msg(...)` trong `confirm_trade_entry` / `order_filled` |
| Telegram | Module `telegram` sẵn có: token từ @BotFather, `chat_id` của bạn |
| Theo dõi kết quả | DB dry-run + `/profit`, `/performance`, `/entries` |
| Tổng kết hằng ngày | Lệnh `/daily` sẵn có + tin tổng kết theo R tự gửi |

File sẽ tạo/sửa (đều trong `user_data/`, không đụng core):
- `user_data/strategies/FuturesSignalStrategy.py` — toàn bộ công thức và tin nhắn
- `user_data/config_futures.json` — config futures (cập nhật bản nháp đã có)
- Secret (token Telegram, mật khẩu API): trong file config bị gitignore hoặc biến môi trường `FREQTRADE__TELEGRAM__TOKEN`, `FREQTRADE__TELEGRAM__CHAT_ID`

---

## 7. Kiểm chứng trước khi tin vào tín hiệu

1. **Dữ liệu:** 1h + 4h futures từ 01/2023 cho khoảng 40 cặp thanh khoản cố định (pairlist động không backtest được → có survivorship bias, ghi rõ khi đọc kết quả).
2. **Kiểm tra lỗi kỹ thuật:** `lookahead-analysis`, `recursive-analysis` không báo lỗi.
3. **Chia dữ liệu:** phát triển trên 2023-01 → 2025-06; giữ 2025-07 → nay để kiểm tra một lần cuối.
4. **Giả định khắt khe:** phí 0,06%/chiều; thêm một bản backtest giả định vào lệnh trễ 1 nến (mô phỏng người vào tay chậm).
5. **Ngưỡng đạt** (trên dữ liệu kiểm tra cuối):
   - ≥ 150 tín hiệu
   - Profit factor ≥ 1,3; kỳ vọng ≥ +0,2R mỗi tín hiệu
   - Drawdown tối đa ≤ 15R
   - Không cặp nào chiếm > 30% lợi nhuận; cả setup A và B không âm
6. **Dry-run trên VPS ≥ 4 tuần**, so sánh với backtest cùng kỳ.
7. Không đạt → đổi giả thuyết và quay lại bước 3, **không** chỉnh tham số theo dữ liệu kiểm tra cuối.

---

## 8. Cài đặt Telegram (BotFather)

1. Mở @BotFather → `/newbot` → đặt tên → nhận **token**.
2. Nhắn một tin bất kỳ cho bot vừa tạo.
3. Lấy **chat_id**: mở `https://api.telegram.org/bot<TOKEN>/getUpdates`, tìm `"chat":{"id": ...}`.
4. Điền vào config (không commit): `telegram.enabled = true`, `token`, `chat_id`.
5. Nếu sau này thêm bạn bè vào group nhận tin: đặt `authorized_users` = chỉ ID của bạn, để người khác không gửi được lệnh điều khiển như `/stop`, `/forceexit`.

---

## 9. Lộ trình

| Bước | Việc | Ai làm |
|---|---|---|
| 1 | Code strategy + config + tin nhắn | Claude |
| 2 | Tải dữ liệu, chạy lookahead/recursive, backtest, báo cáo kết quả thật | Claude |
| 3 | Tinh chỉnh (ít tham số), kiểm tra lần cuối trên dữ liệu giữ lại | Claude + bạn duyệt |
| 4 | Tạo bot BotFather, thuê VPS, triển khai dry-run | Bạn (Claude hướng dẫn) |
| 5 | Theo dõi 4 tuần, đánh giá | Cùng làm |
| 6 | Giai đoạn 2: bot tự vào lệnh với vốn nhỏ | Quyết định sau |
