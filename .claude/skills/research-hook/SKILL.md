---
name: research-hook
description: Test a trading rule of thumb or indicator as a named hook with the market context engine — pre-register the hypothesis, record a fingerprint, run the in-sample event study exactly once, build a scoreboard and update the research state. Use when the user wants to check whether an indicator/pattern/kinh nghiệm actually predicts price.
argument-hint: "<user_data/research/hooks_xxx.py> [1h|4h]"
---

# /research-hook — kiểm định một kinh nghiệm / chỉ báo

Luật: `.claude/rules/quant-mindset.md`. Thiết kế engine: `docs/market-context-engine.md`. Bản dễ hiểu:
`docs/huong-dan-nhan-dinh-thi-truong.md`. Trạng thái: `user_data/research/STATE.md`.

Tham số: `$ARGUMENTS` = `<file hook> [khung]`. Không có file → tạo mới từ mẫu `user_data/research/hooks_indicators_v1.py`.

## Các bước
1. **Kiểm tra STATE:** ý tưởng đã bị bác bỏ chưa? Có thì dừng, báo lý do (trừ khi người dùng đưa lý do mới).
2. **Phiếu giả thuyết** (đầu file hook, docstring): giả thuyết, cơ chế, bằng chứng (playbook/bài báo), hướng
   (+1/−1), horizon chính, tham số (≤ 3, từ tài liệu/mặc định). Đổi logic hoặc tham số = tăng `version`.
3. **Kiểm tra nhanh trên dữ liệu tổng hợp** (không phải dữ liệu thật) để chắc hook chạy và có kích hoạt:
   `tests/research/conftest.py::make_ohlcv` + `mce.features.add_core_features`.
4. **Ghi fingerprint trước khi chạy** vào `user_data/research/fingerprints.jsonl`
   (`REGISTRY.fingerprint()` và `hook.fingerprint()` từng hook, kèm `recorded_at`, `stage`, `file`).
5. **Chạy một lần** trên IS (từ `scripts/research`):
   ```bash
   ../../.venv/Scripts/python.exe run_event_study.py --hooks ../../<file> --datadir ../../user_data/data/binance \
     --out ../../user_data/reports --timeframe 1h --horizons 1 4 12 24 --tag <tên>     # 4h: --horizons 1 3 6 12
   ```
   Thiếu dữ liệu → `download-data --config user_data/config_futures.json --timeframes 1h 4h --timerange 20240101-
   --candle-types futures funding_rate mark index premiumIndex`.
6. **Scoreboard** nếu có nhiều khung: `user_data/research/indicator_scoreboard.py <tag> <các file summary.csv>`,
   chạy với `PYTHONIOENCODING=utf-8 PYTHONPATH=.`. Hiệu chỉnh BH trên **toàn bộ** phép thử.
7. **Báo cáo** `user_data/reports/<tên>.md` bằng lời thường: bảng horizon chính, đạt/không theo cổng (≥ 200 sự kiện,
   lift có ý nghĩa sau BH, cùng dấu ≥ 2/3 năm và ≥ 60% cặp, dương sau phí 0,12%; IS mặc định là dải trong
   `thresholds.json`), và điều **không** được kết luận.
8. **Cập nhật STATE:** dòng mới trong "Đã thử" (fingerprint, kết quả, báo cáo), cộng số hook vào "số lần thử",
   bài học, hàng đợi.

## Không làm
- Không chạy lại cùng hook sau khi chỉnh tham số theo kết quả (phải tạo phiên bản mới và tính thêm lần thử).
- Không chạy trên holdout. Không chọn horizon "đẹp nhất" sau khi xem kết quả.
