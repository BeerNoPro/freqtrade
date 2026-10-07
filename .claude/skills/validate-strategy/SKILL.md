---
name: validate-strategy
description: Validate a futures strategy end-to-end before trusting its signals — ensure data, run lookahead-analysis, recursive-analysis, in-sample backtest with strict assumptions, and compare against the project pass gates. Use after creating or changing a strategy, or when the user asks to backtest/validate.
argument-hint: "<StrategyName> [smoke|full|oos]"
---

# /validate-strategy — kiểm chứng strategy

Luật: `.claude/rules/quant-strategy.md`. Thiết kế và ngưỡng: `docs_vn/signal-bot-workflow.md` (mục 7).

Tham số: `$ARGUMENTS` = `<StrategyName> [mode]`
- `smoke` (mặc định khi đang code): dải 3 tháng gần nhất trong IS, chỉ để chắc strategy chạy được và có tín hiệu.
- `full`: toàn bộ dải IS + lookahead + recursive.
- `oos`: chạy dải holdout **một lần**. Chỉ chạy khi người dùng xác nhận đã chốt tham số.

## Hằng số
| Tên | Giá trị |
|---|---|
| Config | `user_data/config_futures.json` |
| IS (phát triển) | `20230101-20250701` |
| OOS (holdout) | `20250701-` (tới hiện tại) |
| Smoke | `20250301-20250601` |
| Phí | `--fee 0.0006` |
| CLI | `.venv/Scripts/freqtrade.exe` |

## Các bước
1. **Strategy nạp được:** `list-strategies --config <config>` → strategy ở trạng thái OK.
2. **Dữ liệu:** `list-data --config <config> --show-timerange`. Thiếu khung hoặc dải thời gian thì tải:
   `download-data --config <config> --timeframes 1h 4h --timerange 20221101-` (thêm `5m` nếu dùng `--timeframe-detail`).
3. **Lookahead** (mode `full`): `lookahead-analysis --config <config> --strategy <S> --timerange <IS>`. Có cột/tín hiệu bị cờ bias → **dừng**, sửa strategy.
4. **Recursive** (mode `full`): `recursive-analysis --config <config> --strategy <S> --timerange <IS> --startup-candle 400 800 1200`. Chỉ báo lệch > 0.1% → tăng `startup_candle_count` hoặc đổi chỉ báo.
5. **Backtest:**
   ```
   backtesting --config <config> --strategy <S> --timerange <dải> --fee 0.0006 \
     --enable-protections --breakdown month --export trades
   ```
   Thêm `--timeframe-detail 5m` nếu có dữ liệu 5m.
6. **Đánh giá:** `.venv/Scripts/python.exe .claude/skills/validate-strategy/summarize_backtest.py --strategy <S>` → bảng PASS/FAIL theo ngưỡng.
7. **Báo cáo** cho người dùng (tiếng Việt) và lưu vào `user_data/reports/<YYYY-MM-DD>-<S>-<mode>.md`. Báo cáo gồm:
   - giả định: dải thời gian, phí, danh sách cặp, survivorship bias
   - bảng ngưỡng PASS/FAIL
   - kết quả theo tháng, theo setup (enter_tag), theo chiều long/short
   - kết luận thẳng thắn và bước tiếp theo đề xuất
   Không làm đẹp kết quả. FAIL thì nói FAIL.

## Lưu ý
- Pairlist động (`VolumePairList`...) không backtest được: khi backtest, dùng `StaticPairList` với danh sách cặp cố định trong config (ghi rõ survivorship bias).
- Không chỉnh tham số dựa trên kết quả OOS. Nếu OOS fail: báo cáo và quay lại IS với giả thuyết mới.
