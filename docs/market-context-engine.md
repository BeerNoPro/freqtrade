# Market Context Engine (MCE): bộ máy nghiên cứu

Phần nghiên cứu của bot: kiểm định một luật vào lệnh **trước** khi cho nó phát setup. Code chung ở
`scripts/research/mce/` (package `mce`, có test), script chạy ở `scripts/research/`. Luật cụ thể, ngưỡng, kết quả nằm
trong `user_data/research/` và `user_data/reports/` (riêng, không commit). Thiết kế bot: [signal-bot-workflow.md](signal-bot-workflow.md).
Kiến thức mô hình: [quant-playbook.md](quant-playbook.md).

## 1. Nguyên tắc
- **Nhân quả:** giá trị tại nến t chỉ dùng dữ liệu đã có lúc quyết định (đóng nến t). `labels.py` là nơi duy nhất
  nhìn tương lai. Test tự động: cắt dữ liệu tương lai thì quá khứ không đổi.
- **Danh mục point-in-time:** chọn coin theo thanh khoản tại từng thời điểm, kể cả coin đã delist. Không dùng danh sách
  coin chọn theo hôm nay (survivorship bias).
- **Chốt trước, chạy một lần:** giả thuyết, cơ chế, ≤ 3 tham số lấy từ tài liệu, cổng đạt; ghi fingerprint vào
  `user_data/research/fingerprints.jsonl` rồi mới chạy. Không chỉnh theo kết quả rồi chạy lại cùng dữ liệu.
- **Kiểm tra dương tính:** cài sẵn một lợi thế đã biết vào kho giả, quy trình phải phát hiện được. Với ~150 sự kiện,
  ngưỡng phát hiện ≈ 0,3R; trên dữ liệu thật (sự kiện dồn theo ngày) rộng gấp đôi. Biết trước ngưỡng này để không
  đọc nhầm "không đạt" thành "không có gì".

## 2. Luồng
```
kho point-in-time (archive, universe, pit, live) ──► features ──► regime ──► hook / luật (chốt trước)
                                                                                    │
bộ ghi thời gian thực (recorder): dữ liệu sàn không lưu lịch sử         event study IS (một lần)
                                                                                    │
                                  kiểm định tiến: forward (setup đầy đủ) ──► journal (ghi trước, chấm sau)
                                                                                    │ đạt mốc
                                                                      Telegram (nhãn thử nghiệm → thật)
```

| Module | Vai trò |
|---|---|
| `archive.py` | Tải nến (1d, 4h), funding cho **mọi** perp USDT-M từng niêm yết (kể cả đã delist) và OI / long-short từ file `metrics` theo ngày của kho `data.binance.vision` |
| `universe.py` | Lọc coin crypto (bỏ stablecoin, TradFi, index); danh mục top-N point-in-time theo trung vị quote volume |
| `pit.py` | Khung point-in-time cho mỗi coin: nến, chỉ báo, regime, OI chụp **trước** thời điểm quyết định, cờ thuộc danh mục; kho giả để chạy thử |
| `live.py` | Cập nhật kho cho kiểm định tiến: OI từ API (API ghi nhãn trễ 5 phút so với kho: ảnh chụp kho 15:50 = dòng API 15:55), thử lại khi lỗi mạng, giá mua/bán và trượt giá theo sổ lệnh |
| `features.py`, `regime.py` | ATR, ADX, EMA slope, z-score, percentile; regime theo luật (xu hướng EMA50 + ADX, biến động theo percentile ATR%) |
| `hooks.py` | `Hook` (luật có tên, phiên bản, ≤ 3 tham số) + `HookRegistry` + fingerprint |
| `labels.py` | Lợi suất tương lai theo R, triple barrier, funding giữa hai thời điểm |
| `stats.py` | Bootstrap theo cụm tuần, Benjamini–Hochberg, phí quy ra R |
| `event_study.py` | Chạy hook × coin × horizon, so nền cùng coin cùng regime, cổng go/no-go, vào trễ 1 nến |
| `forward.py` | Setup đầy đủ (rủi ro 1%, trần đòn bẩy, chi phí theo R, luật bỏ setup), sự kiện hook, ensemble Donchian hai chiều, kiểm xu hướng liều–đáp ứng |
| `journal.py` | Nhật ký setup sqlite chỉ ghi thêm (trigger chặn sửa/xóa), chấm một lần, đối chiếu live với bản chạy lại |
| `portfolio.py` | Backtester danh mục tỷ trọng mục tiêu khung ngày (phí, funding, vol-target, bootstrap khối, Deflated Sharpe) |
| `recorder.py` | Bộ ghi dữ liệu không có lịch sử (mục 3) + báo cáo độ phủ |
| `audit.py` | Kiểm toán kho: nến (trùng, sai thứ tự, OHLC vô lý, khoảng trống, nến giả sau delist, mã dùng lại tên), funding, metrics, nến ngày khớp nến 4h; đối chiếu mẫu với API fapi và data.binance.vision qua `Sources` (không gọi mạng trong module) |

Script: `download_pit_universe.py` (kho point-in-time; `--plan-only` ước lượng khối lượng tải), `record_market.py` +
`schedule_recorder.ps1` (bộ ghi), `recorder_coverage.py` (độ phủ), `run_forward.py` + `schedule_forward.ps1` (runner
kiểm định tiến), `audit_store.py` + `schedule_audit.ps1` (kiểm toán kho).

## 3. Dữ liệu
**Kho point-in-time** `user_data/research/pit/` (`download_pit_universe.py`, cập nhật hằng ngày bởi runner): nến ngày
mọi coin ứng viên; nến 4h, funding, OI (ảnh chụp mỗi giờ phút :50) cho coin từng vào top-25, từ 100 ngày trước lần đầu.
Từ 2024-01 (2024 chỉ để làm nóng chỉ báo; đánh giá từ 2025).

**Kho lịch sử** `user_data/research/pit_hist/`: cùng cấu trúc, từ 2022-01 (làm nóng) cho khối lịch sử khóa 2023–2024
(`thresholds.json` → `history_block`: chỉ chạy một lần luật đã chốt). Runner không đồng bộ kho này; tải lại bằng
`download_pit_universe.py --outdir user_data/research/pit_hist --start 2022-01-01` (ghi nối, chạy tiếp được).

**Kiểm toán kho** (`audit_store.py`, task `freqtrade-data-audit` 03:00 UTC): quét offline mọi file, đối chiếu mẫu với
API và kho lưu trữ (`--verify N`, phân tầng theo năm, tất định theo `--seed`). Báo cáo
`user_data/reports/audit/latest/<kho>/` (`summary.md`, `issues.csv`, `symbols.csv`, `zombie_members.csv`,
`verify.csv`); mã thoát 1 khi có lỗi critical mới. Lỗi đã đối chiếu tay ghi ở `user_data/research/audit_known.csv`
(mức `known`). Bẫy đã gặp: API trả nến giả (giá đứng yên, khối lượng 0) và funding cho mã đã delist đến tận hôm nay,
nên ngày delist = ngày cuối có khối lượng > 0; mã đổi tên trông như niêm yết mới; tên mã có thể bị dùng lại.

**Kho `data.binance.vision`** có lịch sử (tải khi cần): nến mọi khung (cả mark, index, premium), funding, lệnh khớp,
OI + long/short + taker mỗi 5 phút (`metrics`), độ dày sổ lệnh ±0,2–5% mỗi 30 giây (`bookDepth`).

**Không có lịch sử ở đâu** → bộ ghi `record_market.py` ghi từ khi bật vào `user_data/research/live/`:

| Luồng | Nguồn | Tần suất |
|---|---|---|
| `liquidations` | WebSocket `!forceOrder@arr` (sàn chỉ đẩy ≤ 1 lệnh/giây/coin) | mọi tin |
| `book_ticker` | `/fapi/v1/ticker/bookTicker`, top-100 theo quote volume 24h | 1 phút |
| `premium_index` | `/fapi/v1/premiumIndex`: mark, index, funding dự kiến, mọi coin | 5 phút |
| `depth` | `/fapi/v1/depth` 100 mức mỗi phía, top-20 | 5 phút |
| `options_mark` | `/eapi/v1/mark`: IV, delta, gamma, theta, vega | 15 phút |
| `options_ticker`, `options_oi` | `/eapi/v1/ticker`, `/eapi/v1/openInterest` | 1 giờ |
| `meta` | exchangeInfo (futures, options), fundingInfo, ticker 24h | hằng ngày |

- Ghi nối thêm `<luồng>/<ngày>/part-<giờ>.jsonl` (UTC); ngày đã qua gộp thành `<luồng>/<ngày>.parquet`. Mất kết nối,
  poll lỗi, máy ngủ → ghi vào `live/_gaps.jsonl`. Khoảng 40 MB/ngày. Một bộ ghi mỗi máy (khóa `live/.recorder.lock`).
- Lịch poll ngủ từng bước ≤ 30 giây theo đồng hồ thực: đồng hồ monotonic của Windows dừng khi máy ngủ.
- Độ phủ: `recorder_coverage.py` (mỗi luồng × ngày: phần ngày đã ghi, giờ có dữ liệu, phút trống, dung lượng, ngày
  chưa gộp). Luồng thanh lý: độ phủ = 1 − phút trống / ngày.

**Chạy trên VPS** (dữ liệu liên tục; người dùng tự làm): `git clone` repo, chép `user_data/research/` (kho `pit/`,
luật, `thresholds.json`, `fingerprints.jsonl`) lên; `docker compose up -d --build recorder forward`; log:
`docker compose logs -f --tail 50 recorder forward`. Chỉ một bộ ghi và một runner cho mỗi thư mục dữ liệu (gỡ task
Windows tương ứng bằng `-Remove`). Chép về: `rsync -av <vps>:<repo>/user_data/research/live/ user_data/research/live/`.
**Nếu chạy trên laptop:** tắt chế độ ngủ khi cắm sạc (`powercfg /change standby-timeout-ac 0`,
`powercfg /change hibernate-timeout-ac 0`, đóng nắp = Do nothing).

## 4. Event study (đo một luật)
1. Kích hoạt: nến có `signal != 0`, coin thuộc danh mục tại thời điểm đó; bỏ kích hoạt lặp trong `cooldown` nến.
2. Nhãn (vào ở close[t]): lợi suất tương lai theo **R = biến động giá / ATR14[t]**; triple barrier (cùng nến chạm cả
   hai → tính SL). Dữ liệu cắt tại cuối dải nên nhãn không đọc sang dải sau.
3. **Lift** = lợi suất theo hướng luật − trung bình cùng coin, cùng regime, cùng dải (tách khỏi beta thị trường).
4. Suy diễn: bootstrap theo **tuần lịch** (giữ tự tương quan và đồng biến giữa các coin); Benjamini–Hochberg trên mọi
   hook × horizon. Ổn định: cùng dấu theo năm và theo coin. Sau phí 0,12% khứ hồi và funding thực tế.
5. Cổng (`thresholds.json` → `hook_gate`): ≥ 200 sự kiện; lift có ý nghĩa sau BH; cùng dấu ≥ 2/3 năm và ≥ 60% coin
   (≥ 10 sự kiện); ròng sau phí + funding > 0.
6. Trước khi chạy (`--check`, không nhìn kết quả): độ phủ dữ liệu, đếm sự kiện, nhân quả ở 3 mốc cắt, placebo (nến
   ngẫu nhiên → lift ≈ 0), đối chiếu tay một sự kiện với nến Binance.

## 5. Kiểm định tiến
- Nguồn sự thật để chấm: **chạy lại bằng code đã đóng băng** trên kho lưu trữ (nến, funding, OI đều có lịch sử), nên
  máy tắt/ngủ không làm mất mẫu. Fingerprint = luật + mọi module `mce` dùng tới; sửa bất kỳ file nào trong đó phải
  chứng minh kết quả không đổi rồi đăng ký lại.
- Runner `run_forward.py` (24/7): mỗi 4h đóng nến + 90 giây cập nhật kho (nến, OI API, funding), ghi setup mới vào
  `user_data/research/forward/journal.sqlite` **trước** khi biết kết quả, chấm setup đã đóng, đối chiếu từng setup với
  bản chạy lại (lệch → `reconcile_issues.csv`). 00:00 UTC: setup khung ngày; 01:30 UTC: đồng bộ kho đầy đủ. Lỡ nến
  khi máy ngủ → ghi bù `live_late`. Fingerprint khác bản đăng ký → không ghi setup. `--status`: tóm tắt nhật ký.
- Bảng `setups`: chiến lược, fingerprint, nguồn, cặp, nến quyết định, chiều, giá vào, SL, TP, số nến tối đa, khối
  lượng, notional, đòn bẩy tối đa, chi phí theo R, trạng thái (`setup` / `skipped_cost`), giá mua/bán và trượt giá lúc
  ghi, lý do. Bảng `outcomes`: lý do thoát, giá thoát, R gộp, phí, funding, R ròng.
- Tin nhắn sản phẩm ghi ra `forward/messages.log` (nhãn "THỬ NGHIỆM - chưa kiểm định"); chỉ gửi Telegram khi người
  dùng duyệt. Nhật ký và độ phủ bộ ghi là nguồn dữ liệu cho màn hình UI sau này.

## 6. Rủi ro thống kê → biện pháp
| Rủi ro | Biện pháp |
|---|---|
| Data snooping | Luật từ lý thuyết/tài liệu, ≤ 3 tham số chốt trước; BH; fingerprint; đếm mọi lần thử (Deflated Sharpe) |
| Survivorship | Danh mục point-in-time, kể cả coin đã delist |
| Không dừng | Chuẩn hóa theo ATR, percentile cuốn chiếu; kiểm tra ổn định theo năm/coin/quý |
| Nhãn chồng lấp | Cooldown, bootstrap theo tuần |
| Look-ahead | Test nhân quả tự động; `labels.py` là nơi duy nhất nhìn tương lai; OI chụp trước thời điểm quyết định |
| Phí / trượt giá | Phí khứ hồi theo R; SL trước khi cùng nến chạm cả hai; luật bỏ setup khi chi phí > 0,20R; spread thật từ bộ ghi |
| Mẫu nhỏ | Báo CI, ngưỡng phát hiện; kết luận chỉ ở mốc chốt trước |
