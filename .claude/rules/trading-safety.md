# Luật an toàn giao dịch & bảo mật

- **Luôn chạy `dry_run: true`.** Chỉ chuyển sang giao dịch thật (`dry_run: false`) khi người dùng yêu cầu rõ ràng trong lượt hiện tại, sau khi strategy đã qua backtest gates và ≥ 4 tuần dry-run.
- **Không bao giờ** tự đặt lệnh thật, tự bật `force_entry_enable`, hay gọi `/forceenter` trên bot live.
- Secret (API key sàn, token Telegram, `jwt_secret_key`, `ws_token`, mật khẩu API) chỉ nằm trong `user_data/config*.json` (đã gitignore) hoặc biến môi trường `FREQTRADE__...`. Không chép secret vào code, tài liệu, log hay message commit.
- API key sàn (giai đoạn 2): chỉ quyền giao dịch, **không** quyền rút tiền, whitelist IP của VPS.
- API server chỉ nghe `127.0.0.1`; truy cập từ xa qua SSH tunnel.
- Telegram: `chat_id` là chat riêng của chủ bot. Nếu đưa vào group thì bắt buộc đặt `authorized_users` để người khác không gửi được lệnh điều khiển.
- Futures: margin `isolated`, đòn bẩy gợi ý ≤ 5x, giữ `liquidation_buffer` ≥ 0.05.
- Không sửa core `freqtrade/` trừ các việc cắt gọn có kế hoạch (`trim/*`), và luôn kiểm chứng sau khi sửa.
- Giữ nguyên file `LICENSE` (GPLv3) và `NOTICE`.
