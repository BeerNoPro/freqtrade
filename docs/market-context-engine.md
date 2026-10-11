# Market Context Engine (MCE): thiết kế

Bộ máy **nhận định thị trường**: từ nến, chỉ số và dữ liệu phái sinh, đưa ra *trạng thái + xác suất có điều kiện +
mức vô hiệu*. Không đặt lệnh. Về sau, nhận định làm tầng ngữ cảnh/bộ lọc cho tín hiệu trong
[signal-bot-workflow.md](signal-bot-workflow.md).

Mới đọc lần đầu: xem bản giải thích dễ hiểu [huong-dan-nhan-dinh-thi-truong.md](huong-dan-nhan-dinh-thi-truong.md).

Tài liệu này chỉ chứa phần generic. Hook cụ thể, ngưỡng riêng, kết quả nghiên cứu nằm trong `user_data/research/` và
`user_data/reports/` (không commit).

## 1. Nguyên tắc
- Nhận định không thoát được vấn đề dữ liệu mới. Mọi phán đoán vẫn phải kiểm trên dữ liệu chưa thấy. Khác biệt là
  đầu ra (xác suất, không phải lệnh), thước đo (độ hiệu chuẩn, không chỉ lợi nhuận) và **forward journal**: nhận định
  được ghi *trước* khi biết kết quả, rồi chấm điểm tự động.
- "Kinh nghiệm" = thống kê có điều kiện của từng hook, có phiên bản, có theo dõi suy giảm và quy tắc loại bỏ.
- Giả thuyết có cơ sở (động lượng, co giãn biến động, vị thế quá đông, lead-lag) được ưu tiên hơn mẫu hình tìm ra bằng
  cách quét dữ liệu. Mẫu hình "nhìn bằng mắt" chỉ được đưa vào khi định nghĩa được bằng thuật toán.
- Mọi thứ trước tầng nhãn phải **causal**: giá trị tại nến t chỉ dùng nến đã đóng ≤ t. Có test tự động bảo đảm điều này.

## 2. Kiến trúc

```
dữ liệu feather (futures, mark, index, premiumIndex, funding, 4h, BTC) + kho point-in-time (nến, funding, OI,
long/short) + bộ ghi thời gian thực (thanh lý, giá mua/bán, funding dự kiến, sổ lệnh, quyền chọn)
      │
 features ──► regime ──► hooks ──► event study (nghiên cứu, ngoài engine)
      │                    │
      └────────────────────┴──► assessment (xác suất + kịch bản + mức vô hiệu) ──► journal ──► chấm điểm, drift
```

Code nằm ở `scripts/research/mce/` (package `mce`):

| Module | Vai trò |
|---|---|
| `data.py` | Nạp feather qua `freqtrade.data.history`, ghép 4h bằng `merge_informative_pair`, ghép funding theo thời điểm công bố |
| `features.py` | ATR, ADX, EMA slope theo ATR, percentile/z-score cuốn chiếu, cấu trúc nến, breakout, hạng cắt ngang trung bình của nhiều tín hiệu |
| `regime.py` | Regime theo luật minh bạch + kiểm tra regime có tách biệt kết quả tương lai không |
| `hooks.py` | `Hook` (dataclass) + `HookRegistry` + fingerprint |
| `labels.py` | **Module duy nhất được nhìn tương lai**: lợi suất tương lai theo R, triple-barrier, MFE/MAE, lợi suất mua-giữ N ngày (coin delist thoát ở close cuối), tổng funding trong cửa sổ giữ, lợi suất vào trễ, funding giữa hai thời điểm |
| `stats.py` | Block bootstrap, Benjamini–Hochberg, Brier, log loss, reliability, Brier skill score, hiệu hai trung bình (bootstrap tuần chung) |
| `event_study.py` | Chạy hook × cặp × horizon, so baseline cùng regime, áp go/no-go; bỏ dòng ngoài danh mục (cột `active`); báo kết quả vào trễ 1 nến |
| `pit.py` | Khung point-in-time từ kho `pit/`: nến, regime, OI chụp **trước** thời điểm quyết định, cờ thuộc top-N; kho giả để chạy thử |
| `walkforward.py` | Chia walk-forward cuốn chiếu có purge + embargo |
| `journal.py` | Forward journal sqlite: ghi nhận định, chấm điểm khi horizon đóng |
| `recorder.py` | Ghi dữ liệu sàn không lưu lịch sử (mục 9): ghi nối thêm JSONL theo giờ, gộp parquet theo ngày, log khoảng trống |
| `archive.py` | Tải nến (1d, 4h) + funding cho **mọi** perp USDT-M từng niêm yết, kể cả đã delist (liệt kê từ kho data.binance.vision), và lịch sử OI / tỷ lệ long-short từ file `metrics` theo ngày của kho (API chỉ giữ 30 ngày) |
| `model.py` | Logistic regression L2 (numpy/scipy), walk-forward trên panel nhiều coin (purge + embargo theo mốc thời gian chung) |
| `setups.py` | Kỳ vọng R sau phí, chọn setup (1 setup mở/coin, trần số setup đồng thời), thống kê theo R |
| `universe.py` | Danh sách cố định `DEFAULT_PAIRS`; danh mục point-in-time (top-N theo trung vị quote volume, chỉ dùng dữ liệu đến ngày đó) |
| `portfolio.py` | Backtester danh mục theo trọng số mục tiêu khung ngày, phí, funding, Deflated Sharpe, bootstrap khối Sharpe; rổ long-short theo phân vị (trung lập đô la hoặc trung lập beta BTC), beta cuốn chiếu, các lớp chồng giữ N ngày, tách beta/alpha của chuỗi lợi suất |

CLI: `scripts/research/run_event_study.py`, `scripts/research/download_pit_universe.py` (dữ liệu cho danh mục
point-in-time, ghi vào `user_data/research/pit/`), `scripts/research/record_market.py` (bộ ghi, mục 9).

### Quy ước căn thời gian (row t = nến 1h mở lúc t, quyết định lúc đóng t+1h)
| Nguồn | Cách ghép |
|---|---|
| mark / index / premiumIndex 1h | cùng `date` (đóng cùng lúc với nến t) |
| nến 4h | `merge_informative_pair`: chỉ thấy nến 4h đã đóng |
| funding_rate (in mỗi 8h tại thời điểm thanh toán T) | thấy được khi T ≤ t + 1h |
| BTC 1h | cùng `date` |
| OI / long-short / taker (kho `metrics`, ảnh chụp 5 phút) | chỉ dùng ảnh chụp **trước** thời điểm quyết định |

## 3. Interface hook

```python
Hook(
    name="...", version=1,
    hypothesis="một câu giả thuyết kinh tế",
    condition=fn,                # (df, params) -> Series {-1, 0, +1}: hướng kỳ vọng
    params={...},                # tối đa 3 tham số, cố định trước khi chạy
    horizon=12,                  # horizon chính (số nến), dùng cho cổng và cooldown
    timeframes=("1h", "4h"), features=(...),
    invalidation=fn,             # khoảng cách giá tính từ entry làm nhận định vô hiệu
    status="candidate",          # candidate | active | retired
)
```
- Id = `name@vN`. **Đổi logic hoặc tham số = phiên bản mới**; thống kê cũ giữ theo id cũ.
- `fingerprint()` = sha256 của mã nguồn hàm + params + horizon. `HookRegistry.fingerprint()` gộp toàn bộ, ghi lại
  trước khi chạy holdout.
- `condition` chỉ dùng cột của nến đã đóng; không `shift(-n)`, không thống kê toàn chuỗi.

## 4. Event study (đo "kinh nghiệm")
1. Kích hoạt: hàng có `signal != 0` trong dải nghiên cứu; bỏ kích hoạt lặp trong `cooldown` nến (mặc định = horizon).
2. Nhãn (vào lệnh ở close[t]): lợi suất tương lai 1/4/12/24 nến theo **R = biến động giá / ATR14[t]**;
   triple-barrier (TP k·ATR, SL m·ATR, giới hạn = horizon; cùng nến chạm cả hai → tính SL); MFE/MAE.
   Dữ liệu bị cắt tại cuối dải nên nhãn không bao giờ đọc sang dải sau.
3. **Lift** = lợi suất theo hướng hook − trung bình lợi suất của cùng cặp, cùng regime, trong cùng dải.
4. Suy diễn: block bootstrap theo **tuần lịch** (gộp mọi cặp trong cùng tuần) → giữ tự tương quan, nhãn chồng lấp và
   đồng biến giữa các cặp. p hai phía; Benjamini–Hochberg trên toàn bộ hook × horizon.
5. Ổn định: tỉ lệ năm và tỉ lệ cặp (≥ 10 sự kiện) có lift cùng dấu với tổng.
6. Sau phí: trừ phí khứ hồi quy ra R tại giá vào.

### Cổng go/no-go cho hook (giá trị khởi điểm, chỉnh trong `user_data/research/`)
Ở horizon chính: ≥ 200 sự kiện IS; lift có ý nghĩa sau BH; cùng dấu ≥ 2/3 số năm và ≥ 60% số cặp; lợi suất trung bình
sau phí 0,12% khứ hồi > 0. Đạt → `active` (vẫn phải qua walk-forward và forward journal).

## 5. Regime
- Luật minh bạch: trend = độ dốc EMA50 (theo ATR, 10 nến) + ADX; vol = percentile cuốn chiếu 30 ngày của ATR%;
  shock = nến vừa đóng > 3 ATR trước đó. Có bản 1h, 4h và của BTC.
- Kiểm tra: regime có tách biệt |lợi suất| và lợi suất tương lai không (Kruskal–Wallis + cỡ hiệu ứng ε²; p lạc quan
  vì nhãn chồng lấp nên đọc cỡ hiệu ứng trước).
- Chỉ thử mô hình thống kê (HMM 2–3 trạng thái, tự viết bằng numpy/scipy) khi luật không đủ. **Chỉ lọc tiến**
  (forward filtering); không Viterbi/smoothing trên cả chuỗi.

## 6. Assessment (tổng hợp nhận định)
Đầu ra cho mỗi cặp mỗi nến: hướng, `prob` (xác suất đi đúng hướng sau horizon), kịch bản (hook nào kích hoạt, regime),
mức vô hiệu.
1. Bước 1: đếm có trọng số hook `active` theo lift; `prob` = hit rate có điều kiện (co về base rate theo số mẫu).
2. Bước 2: logistic đơn giản trên các cờ hook + regime (scipy, điều chuẩn L2), fit trên walk-forward train.
3. Bước 3 (sau): cập nhật Bayes / meta-labeling quyết định có tin hook hay không.
- Đo: Brier, log loss, reliability (lệch ≤ 5 điểm % mỗi bin ≥ 30 mẫu), **Brier skill score > 0 so với base rate**
  (base rate lấy từ dữ liệu trước thời điểm dự báo).

## 7. Kiểm chứng trên dữ liệu mới
- Walk-forward cuốn chiếu (`purged_walk_forward`): purge các mẫu train có cửa sổ nhãn chạm vào test, cộng embargo.
- **Holdout cuối chạy đúng một lần**, sau khi ghi `HookRegistry.fingerprint()` và tham số assessment.
- Forward journal (sqlite `user_data/research/journal.sqlite`):
  - `assessments`: thời điểm tạo, `candle_date`, cặp, `hook_id`, fingerprint, regime, hướng, `prob`, horizon,
    timeframe, entry, ATR, mức vô hiệu. Duy nhất theo (nến, cặp, hook) → ghi lại không trùng.
  - `outcomes`: lợi suất R, hit, có chạm mức vô hiệu không, Brier. Chấm một lần khi horizon đã đóng.
- Drift: hit rate và Brier skill cuốn chiếu 60 ngày theo hook. **Retire** khi skill < 0 có ý nghĩa (bootstrap).

## 8. Tích hợp Freqtrade (không sửa core)
Chọn **(b) nghiên cứu ngoài engine**: script đọc feather, chạy event study/walk-forward; engine chỉ dùng hook đã duyệt.
- Strategy tương lai gọi lại chính các hàm `mce.features/regime/hooks` trong `populate_indicators` để tạo cột ngữ cảnh
  (một nguồn code cho cả nghiên cứu và bot → không lệch công thức).
- Ghi journal (và về sau gửi Telegram qua `dp.send_msg`) chỉ khi có nến mới và `runmode` là `live`/`dry_run`.
- Lý do không chọn (a) cho nghiên cứu: backtest engine không phục vụ event study / bootstrap, và tính nặng trong vòng
  lặp bot làm chậm phát tín hiệu.

## 9. Nguồn dữ liệu: kho lưu trữ và bộ ghi
**Có lịch sử trên kho công khai `data.binance.vision`** (tải khi cần, `mce.archive` / `download_pit_universe.py`):
nến mọi khung (kể cả mark, index, premium), funding, lệnh khớp (`trades`, `aggTrades`), OI + tỷ lệ long/short +
taker mỗi 5 phút (`metrics`, từ 2020), độ dày sổ lệnh trong ±0,2/1/2/3/4/5% quanh giá mỗi 30 giây (`bookDepth`, từ
2023). API `futures/data/*` chỉ giữ ~30 ngày nhưng kho có cùng số liệu (đã đối chiếu) nên không cần collector riêng.

**Không có lịch sử ở đâu** → bộ ghi `scripts/research/record_market.py` ghi từ khi bật (`user_data/research/live/`):

| Luồng | Nguồn | Tần suất |
|---|---|---|
| `liquidations` | WebSocket `wss://fstream.binance.com/market/ws/!forceOrder@arr` (sàn chỉ đẩy ≤ 1 lệnh/giây/coin) | mọi tin |
| `book_ticker` | `/fapi/v1/ticker/bookTicker`, top-100 theo quote volume 24h | 1 phút |
| `premium_index` | `/fapi/v1/premiumIndex`: mark, index, funding dự kiến, giờ funding kế, mọi coin | 5 phút |
| `depth` | `/fapi/v1/depth` 100 mức mỗi phía, top-20 | 5 phút |
| `options_mark` | `/eapi/v1/mark`: mark, IV mua/bán/mark, delta, gamma, theta, vega | 15 phút |
| `options_ticker`, `options_oi` | `/eapi/v1/ticker`, `/eapi/v1/openInterest` | 1 giờ |
| `meta` | exchangeInfo (futures, options), fundingInfo, ticker 24h (JSON nén) | hằng ngày |

- Mỗi luồng chụp một lần khi khởi động rồi theo lưới đồng hồ. Ghi nối thêm `<luồng>/<ngày>/part-<giờ>.jsonl` (UTC),
  ngày đã qua được gộp thành `<luồng>/<ngày>.parquet`. Mất kết nối hoặc poll lỗi được ghi vào `live/_gaps.jsonl`.
- Trên máy Windows: `scripts/research/schedule_recorder.ps1` (Task Scheduler, chạy ẩn, tự khởi động lại). Máy tắt/ngủ
  thì có khoảng trống. Trên VPS: `docker compose up -d --build recorder`; chép dữ liệu về máy nghiên cứu bằng
  `scp -r <vps>:<repo>/user_data/research/live user_data/research/` (hoặc rsync). Chỉ chạy một bộ ghi mỗi máy (khóa
  `live/.recorder.lock`).
- Dung lượng khoảng 40 MB/ngày. Các số liệu vị thế được giả định yếu khi đứng riêng; chỉ kiểm định kết hợp với giá.

## 10. Rủi ro thống kê → biện pháp
| Rủi ro | Biện pháp |
|---|---|
| Data snooping | Hook từ lý thuyết, ≤ 3 tham số cố định trước; BH trên mọi phép thử; ghi fingerprint; không hyperopt hàng loạt |
| Survivorship | Danh sách cặp cố định, niêm yết trước dải nghiên cứu; báo cáo ghi rõ bias còn lại |
| Không dừng | Chuẩn hóa theo ATR, percentile cuốn chiếu; kiểm tra ổn định theo năm/regime; walk-forward |
| Nhãn chồng lấp | Cooldown, block bootstrap theo tuần, purge + embargo |
| Look-ahead | Test causal tự động (cắt dữ liệu tại t, so kết quả); `labels.py` là nơi duy nhất nhìn tương lai |
| Phí / trượt giá | Trừ phí khứ hồi theo R; triple-barrier chọn SL khi cùng nến |
| Đứt gãy regime | Thống kê theo regime; drift cuốn chiếu; quy tắc retire |

## 11. Lộ trình và go/no-go
| Phase | Việc | Go khi |
|---|---|---|
| P1 | Kho dữ liệu point-in-time + bộ ghi chạy liên tục | Đủ dữ liệu danh mục; khoảng trống của bộ ghi được ghi lại |
| P2 | Event study IS cho bộ hook v1, v2… | ≥ 1 hook qua cổng mục 4 |
| P3 | Assessment + walk-forward OOS trong IS | BSS > 0, reliability lệch ≤ 5 điểm % |
| P4 | Holdout một lần (đã ghi fingerprint) | Giữ BSS > 0 và lift cùng dấu |
| P5 | Forward journal dry-run | ≥ 4 tuần và ≥ 300 nhận định, BSS > 0 |
| P6 | Dùng làm bộ lọc tín hiệu / Telegram | Bộ lọc cải thiện kỳ vọng tín hiệu trên OOS |

No-go ở bất kỳ phase nào → quay lại giả thuyết (phiên bản hook mới), không nới cổng theo kết quả.
