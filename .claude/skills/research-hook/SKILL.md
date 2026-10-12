---
name: research-hook
description: Test a trading rule, event or indicator as a named hook on the point-in-time universe — pre-register the hypothesis, run a positive control and the outcome-blind checks, record a fingerprint, run the development event study exactly once and update the research state. Use when the user wants to check whether a rule/pattern/kinh nghiệm actually predicts price.
argument-hint: "<user_data/research/<tên>_v1.py>"
---

# /research-hook — kiểm định một luật / sự kiện

Luật: `.claude/rules/quant-mindset.md`. Bộ máy: `docs/market-context-engine.md` (mục 4–5). Trạng thái:
`user_data/research/STATE.md`. Mẫu script (riêng tư): `user_data/research/liquidation_events_v1.py` (event study 4h
trên top-20 point-in-time, có `--check`, `--synthetic OUT`, `--fingerprint`, chặn chạy lại khi báo cáo đã có).

Tham số: `$ARGUMENTS` = file script luật. Không có → tạo mới theo mẫu trên.

## Các bước
1. **Kiểm tra STATE** (mục 7 "Đã loại", mục 5): ý tưởng đã bị bác bỏ chưa? Có → dừng, báo lý do.
2. **Phiếu giả thuyết** (docstring đầu file): giả thuyết một câu; cơ chế (ai trả tiền cho lợi thế, vì sao chưa bị
   arbitrage); bằng chứng (playbook/bài báo); chiều; horizon chính; ≤ 3 tham số lấy từ tài liệu; danh mục (top-N
   point-in-time hoặc dải hạng thanh khoản); dải dữ liệu (chỉ 2025+); cổng `thresholds.json` → `hook_gate`. Đổi
   logic hoặc tham số = tăng `version` và tính thêm lần thử.
3. **Dữ liệu:** kho `user_data/research/pit/` qua `mce.pit.load_frames`. Thiếu coin/khung → chạy
   `scripts/research/download_pit_universe.py` (`--top`, `--spare`, `--intervals`).
4. **Kiểm tra dương tính** trên kho giả (`mce.pit.synthetic_store`, cài sẵn lợi thế đã biết): quy trình phải phát hiện
   được; ghi ngưỡng phát hiện với số sự kiện dự kiến.
5. **`--check`** (không nhìn kết quả): độ phủ dữ liệu, đếm sự kiện theo quý/tuần (đủ ≥ 200? dồn vào một tuần?), nhân quả
   ở 3 mốc cắt, placebo (nến ngẫu nhiên → lift ≈ 0), đối chiếu tay một sự kiện với nến Binance.
6. **Ghi fingerprint** vào `user_data/research/fingerprints.jsonl` (registry, từng hook, file, kết quả check), rồi
   **chạy một lần**. Báo cáo `user_data/reports/<tên>.md` bằng lời thường, cả khi FAIL: bảng horizon chính, đạt/không
   theo cổng, điều **không** được kết luận.
7. **Đạt** → hỏi người dùng về khối xác nhận và kiểm định tiến (spec mới cho `scripts/research/run_forward.py`; không sửa
   spec đang chạy vì fingerprint đã đăng ký).
8. **Cập nhật STATE** (mục 4 số lần thử, 5, 7, 8, 11) và xóa script/báo cáo bị thay thế.

## Không làm
- Không chạy lại cùng dữ liệu sau khi chỉnh theo kết quả; không chọn horizon/ngưỡng "đẹp nhất" sau khi xem.
- Không dùng danh sách coin chọn theo hôm nay; không dùng dữ liệu trước 2025 để đánh giá.
