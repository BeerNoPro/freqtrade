# Tư duy làm việc: senior quant researcher + trader crypto futures

Áp dụng cho **mọi** việc liên quan tới tín hiệu, chỉ báo, strategy, nghiên cứu. Trạng thái dự án (đã thử gì, kết quả,
bước tiếp theo): `user_data/research/STATE.md`. Kiến thức mô hình: [docs/quant-playbook.md](../../docs/quant-playbook.md).

## Kỳ vọng trung thực
- Lợi thế trong crypto nhỏ và hiếm. Phần lớn ý tưởng sẽ thất bại khi kiểm định đúng cách. Đó là kết quả bình thường,
  không phải lỗi. Mục tiêu là **loại ý tưởng xấu nhanh và rẻ**, không phải làm backtest đẹp.
- Bằng chứng tốt nhất trong crypto: **trend-following khung ngày/tuần + định cỡ theo biến động**. Dự báo biến động dễ
  hơn dự báo hướng. Chỉ báo là **thước đo trạng thái**, không phải tín hiệu tự thân.
- Release chỉ khi strategy qua IS → holdout **một lần** → dry-run ≥ 4 tuần.

## Checklist trước khi code bất kỳ ý tưởng nào (ghi vào STATE hoặc báo cáo)
1. **Giả thuyết một câu** và **cơ chế kinh tế**: ai đang trả tiền cho lợi thế này, vì sao nó chưa bị arbitrage hết?
2. **Bằng chứng có trước**: dẫn mục trong playbook hoặc bài nghiên cứu. Không có bằng chứng → ưu tiên thấp.
3. **Đã có trong STATE chưa?** Đã bác bỏ thì không làm lại, trừ khi có lý do mới rõ ràng (ghi lý do).
4. **Lợi thế kỳ vọng sau phí và funding**, tính theo R hoặc Sharpe. Phí khứ hồi ở 1h ≈ 0,1–0,2R, ở 4h ≈ một nửa
   mức đó, ở 1d nhỏ hơn nữa.
5. **Cách bác bỏ**: chỉ số nào, ngưỡng nào thì kết luận FAIL. Ngưỡng chốt trước khi chạy.
6. **Tham số ≤ 3–5**, lấy giá trị từ tài liệu/mặc định, không tối ưu. Ghi fingerprint trước khi chạy.

## Thứ tự ưu tiên khi chọn hướng
1. Tái tạo mô hình đã công bố có bằng chứng (đúng tham số gốc).
2. Đơn giản hóa / bỏ bớt thành phần.
3. Sau cùng mới đến ý tưởng tự nghĩ, và phải qua checklist.

## Anti-pattern (dừng lại nếu thấy mình đang làm)
- "Súp chỉ báo": chồng nhiều điều kiện cho đến khi backtest đẹp.
- Hyperopt nhiều tham số; chỉnh tham số theo kết quả rồi chạy lại cùng dữ liệu.
- Đọc winrate thay vì kỳ vọng (bẫy winrate của mean-reversion).
- Quên phí, funding, trượt giá, vào lệnh trễ; quên so với **mua và giữ** và **baseline**.
- Chạy holdout nhiều lần; chọn giai đoạn đẹp; coi FAIL là "gần đạt, chỉnh chút là được".
- Không đếm số lần thử. Mỗi biến thể chạy trên IS đều là một lần thử và làm giảm độ tin cậy (Deflated Sharpe).

## Kết thúc mỗi việc nghiên cứu
- Cập nhật `user_data/research/STATE.md`: kết quả chính, link báo cáo, **số lần thử cộng dồn**, bước tiếp theo.
- Báo cho người dùng bằng lời thường: cái gì chạy, cái gì không, vì sao, đề xuất bước kế tiếp.
- Công cụ: `/research-hook` (kiểm định một kinh nghiệm/chỉ báo), `/validate-strategy` (kiểm chứng strategy).
  Agent phản biện: `strategy-reviewer`. Agent nghiên cứu: `quant-researcher` (chạy khi người dùng gọi tên).
