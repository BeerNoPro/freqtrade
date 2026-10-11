# Thiết kế Bot tín hiệu Futures (nội bộ)

> Bot chỉ phát tín hiệu từ một strategy **đã qua kiểm định** (cổng trong
> [.claude/rules/quant-strategy.md](../.claude/rules/quant-strategy.md)). Chưa có strategy đạt thì chưa có tín hiệu.
> Kiến thức mô hình: [quant-playbook.md](quant-playbook.md). Liên quan: [lifecycle.md](lifecycle.md) ·
> [structure.md](structure.md) · [market-context-engine.md](market-context-engine.md)

## 0. Các quyết định đã chốt

| Hạng mục | Quyết định |
|---|---|
| Mục đích | Bot **tính toán và phát tín hiệu**; giai đoạn 2 mới cho tự vào lệnh |
| Thị trường | Binance **USDT-M Futures** (perpetual), chỉ coin crypto (không perp cổ phiếu/hàng hóa) |
| Khung thời gian | **Ngày** (quyết định lúc đóng nến 00:00 UTC). Khung 1h/4h đã bị loại: phí ăn hết lợi thế |
| Dạng strategy | Danh mục nhiều coin, tỷ trọng mục tiêu, tái cân bằng định kỳ (trend, carry...) |
| Dữ liệu kiểm định | Chỉ dữ liệu gần, **2025 → nay**; danh mục point-in-time (chọn coin bằng thông tin có tại thời điểm đó) |
| Nền tảng | Freqtrade giữ nguyên core; code riêng nằm trong `user_data/`, công cụ nghiên cứu ở `scripts/research/` |
| Nơi chạy | VPS 24/7, dry-run (không đặt lệnh thật) |
| Nơi nhận tin | Bot Telegram riêng tạo bằng @BotFather |

---

## 1. Workflow

```
Nghiên cứu (scripts/research, user_data/research)
  giả thuyết + cơ chế + bằng chứng → fingerprint → chạy IS MỘT lần trên danh mục point-in-time
  → đạt cổng? → người dùng duyệt → holdout MỘT lần → port sang Freqtrade
                                                          │
[VPS 24/7 — Freqtrade dry-run, khung 1d]                  ▼
Binance Futures API (công khai)
 ├─ Danh mục: top coin crypto theo thanh khoản (pairlist tương đương luật point-in-time)
 ├─ Strategy: tính tỷ trọng mục tiêu mỗi coin lúc đóng nến ngày
 ├─ Cổng rủi ro: đòn bẩy tổng, isolated, liquidation buffer (luật trading-safety)
 ├─ Telegram: tỷ trọng mục tiêu + thay đổi hôm nay + lý do
 └─ Theo dõi: so lợi suất dry-run với backtest cùng kỳ
```

---

## 2. Tin nhắn Telegram (dạng danh mục)

**Hằng ngày sau 00:00 UTC, chỉ khi có thay đổi tỷ trọng:**
```
📊 Tái cân bằng 2026-10-12 (đóng nến ngày)
Mua thêm:  SOL +4% (→ 9%) · DOGE mới 5%
Giảm/đóng: XRP −6% (→ 0%)
Giữ:       BTC 12% · ETH 8%
Đòn bẩy tổng 1.4x · Mục tiêu biến động 25%/năm
Lý do: SOL vượt đỉnh 20 ngày; XRP chạm dừng trailing
```
**Tổng kết tuần:** lợi suất tuần, drawdown hiện tại, so với backtest cùng kỳ, chi phí phí + funding.

---

## 3. Ánh xạ sang Freqtrade (không sửa core)

| Thiết kế | Cài đặt trong Freqtrade |
|---|---|
| Khung ngày | `timeframe = "1d"`, `startup_candle_count` đủ cho lookback dài nhất |
| Danh mục | `pairlists`: `VolumePairList` → `DelistFilter` → `AgeFilter`; backtest dùng danh sách tương đương luật point-in-time |
| Vào / đổi tỷ trọng | `custom_stake_amount` (vị thế đầu), `adjust_trade_position` (tăng/giảm về tỷ trọng mục tiêu) |
| Đòn bẩy | `leverage()` cố định theo chính sách, isolated |
| Funding | candle type `funding_rate` qua `@informative` |
| Tin nhắn | `self.dp.send_msg(...)` |
| Theo dõi | DB dry-run + `/profit`, `/performance`, `/daily` |

Strategy đặt trong `user_data/strategies/`; khi chốt tên, cập nhật luôn `run.ps1` và `docker-compose.yml`.
Secret chỉ trong config gitignore hoặc biến môi trường `FREQTRADE__...`.

---

## 4. Kiểm chứng trước khi tin vào tín hiệu

Cổng và quy trình: [.claude/rules/quant-strategy.md](../.claude/rules/quant-strategy.md) (cổng danh mục, dải dữ liệu),
skill `/validate-strategy` (lookahead, recursive, backtest Freqtrade). Tóm tắt:
1. Backtester danh mục trên danh mục point-in-time, phí 0,06%/chiều + funding thực tế, so BTC mua-giữ có vol-target.
2. Holdout chạy **một lần** sau khi người dùng duyệt (`user_data/research/thresholds.json: holdout_runs`).
3. Port Freqtrade: `lookahead-analysis`, `recursive-analysis` sạch; lợi suất khớp backtester danh mục.
4. Dry-run VPS ≥ 4 tuần, so với backtest cùng kỳ. Không đạt → đổi giả thuyết, không chỉnh tham số theo holdout.

---

## 5. Cài đặt Telegram (BotFather)

1. Mở @BotFather → `/newbot` → đặt tên → nhận **token**.
2. Nhắn một tin bất kỳ cho bot vừa tạo.
3. Lấy **chat_id**: mở `https://api.telegram.org/bot<TOKEN>/getUpdates`, tìm `"chat":{"id": ...}`.
4. Điền vào config (không commit): `telegram.enabled = true`, `token`, `chat_id`.
5. Nếu sau này thêm bạn bè vào group nhận tin: đặt `authorized_users` = chỉ ID của bạn, để người khác không gửi được lệnh điều khiển như `/stop`, `/forceexit`.

---

## 6. Lộ trình

| Bước | Việc | Ai làm |
|---|---|---|
| 0 | Tìm strategy đạt cổng IS (dữ liệu 2025+, danh mục point-in-time). Trạng thái chi tiết trong `user_data/research/STATE.md` | Claude |
| 1 | Người dùng duyệt → holdout một lần | Bạn duyệt, Claude chạy |
| 2 | Port sang Freqtrade (khung 1d, tỷ trọng mục tiêu), lookahead/recursive, đối chiếu backtester danh mục | Claude |
| 3 | Tạo bot BotFather, thuê VPS, triển khai dry-run | Bạn (Claude hướng dẫn) |
| 4 | Theo dõi ≥ 4 tuần, đánh giá | Cùng làm |
| 5 | Giai đoạn 2: bot tự vào lệnh với vốn nhỏ | Quyết định sau |

### 6.1 Nhận định thị trường
Nhánh song song [market-context-engine.md](market-context-engine.md): các hook có tên, có thống kê, ghi vào forward
journal. Nhận định **không** tự phát tín hiệu; chỉ được dùng làm bộ lọc/định cỡ sau khi qua lộ trình P1–P5 của MCE
(event study IS → walk-forward → holdout một lần → journal ≥ 4 tuần, ≥ 300 nhận định, Brier skill > 0).
