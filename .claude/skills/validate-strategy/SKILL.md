---
name: validate-strategy
description: Validate a futures strategy end-to-end before trusting its signals — ensure data, run lookahead-analysis, recursive-analysis, in-sample backtest with strict assumptions, and compare against the project pass gates. Use after creating or changing a strategy, or when the user asks to backtest/validate.
argument-hint: "<StrategyName> [smoke|full|oos]"
---

# /validate-strategy — kiểm chứng strategy

Luật và ngưỡng: `.claude/rules/quant-strategy.md`, `.claude/rules/quant-mindset.md`. Thiết kế bot: `docs/signal-bot-workflow.md`.
Trạng thái và số lần thử: `user_data/research/STATE.md` (đọc trước, cập nhật sau).

Tham số: `$ARGUMENTS` = `<StrategyName> [mode]`
- `smoke` (mặc định khi đang code): dải 3 tháng gần nhất trong IS, chỉ để chắc strategy chạy được và có tín hiệu.
- `full`: toàn bộ dải IS + lookahead + recursive.
- `oos`: chạy dải ngoài mẫu **một lần**: dữ liệu từ ngày ghi fingerprint (sha256 file strategy, trong
  `user_data/research/fingerprints.jsonl`) tới mốc đã chốt. Chỉ chạy khi người dùng xác nhận trong lượt hiện tại.
  Holdout 2026-04 → 2026-10 đã dùng (`holdout_runs` = 1 trong `thresholds.json`): chỉ dùng lại khi người dùng duyệt
  cho giả thuyết chưa từng chạy trên dải đó.

## Hằng số
| Tên | Giá trị |
|---|---|
| Config | `user_data/config_futures.json` (hoặc config backtest riêng trong `user_data/`) |
| IS (phát triển) | `20250101-20260401` (đọc lại `user_data/research/thresholds.json`) |
| OOS | từ ngày ghi fingerprint của strategy tới mốc (kiểm định tiến) |
| Smoke | `20260101-20260401` |
| Phí | `--fee 0.0006` |
| CLI | `.venv/Scripts/freqtrade.exe` |

## Các bước
1. **Strategy nạp được:** `list-strategies --config <config>` → strategy ở trạng thái OK.
2. **Dữ liệu:** `list-data --config <config> --show-timerange`. Thiếu khung hoặc dải thời gian thì tải:
   `download-data --config <config> --timeframes 1h 4h --timerange 20240101-` (thêm `5m` nếu dùng `--timeframe-detail`).
3. **Lookahead** (mode `full`): `lookahead-analysis --config <config> --strategy <S> --timerange <IS>`. Có cột/tín hiệu bị cờ bias → **dừng**, sửa strategy.
4. **Recursive** (mode `full`): `recursive-analysis --config <config> --strategy <S> --timerange <IS> --startup-candle 400 800 1200`. Chỉ báo lệch > 0.1% → tăng `startup_candle_count` hoặc đổi chỉ báo.
5. **Backtest:**
   ```
   backtesting --config <config> --strategy <S> --timerange <dải> --fee 0.0006 \
     --enable-protections --breakdown month --export trades
   ```
   Thêm `--timeframe-detail` khung nhỏ hơn nếu có dữ liệu. Mode `full`: chạy thêm bản **vào trễ 1 nến** (subclass
   dịch `enter_*` đi 1 nến) và tính **mua-giữ** cùng danh mục, cùng dải (`mce.portfolio.simulate` với trọng số
   cố định).
6. **Đánh giá:** `.venv/Scripts/python.exe .claude/skills/validate-strategy/summarize_backtest.py --strategy <S>` → bảng PASS/FAIL theo ngưỡng.
7. **Báo cáo** cho người dùng (tiếng Việt) và lưu vào `user_data/reports/<YYYY-MM-DD>-<S>-<mode>.md`. Báo cáo gồm:
   - giả định: dải thời gian, phí, danh sách cặp, survivorship bias
   - bảng ngưỡng PASS/FAIL
   - kết quả theo tháng, theo setup (enter_tag), theo chiều long/short
   - so mốc: mua-giữ, bản vào trễ 1 nến
   - kết luận thẳng thắn và bước tiếp theo đề xuất
   Không làm đẹp kết quả. FAIL thì nói FAIL.
8. **Cập nhật STATE** (mode `full`/`oos`): thêm dòng vào bảng "Đã thử", tăng số lần thử strategy, ghi bài học và
   bước tiếp theo. Gợi ý người dùng chạy agent `strategy-reviewer` trước khi tin một kết quả PASS.

## Lưu ý
- Pairlist động (`VolumePairList`...) không backtest được: khi backtest, dùng `StaticPairList` với danh sách cặp cố định trong config (ghi rõ survivorship bias).
- Không chỉnh tham số dựa trên kết quả OOS. Nếu OOS fail: báo cáo và quay lại IS với giả thuyết mới.
