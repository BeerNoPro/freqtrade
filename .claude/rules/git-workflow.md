# Luật Git & Commit

## Branch
- Repo chỉ có **2 branch**: `develop` (phát triển) và `main` (bản ổn định, chạy trên VPS).
- **Luôn làm việc và commit trực tiếp trên `develop`.** Không tạo branch mới.
- **Agent không push, không tạo PR.** Người dùng tự push `develop` và tự tạo PR `develop` → `main`.
- Không commit lên `main`. Không force push, không xóa branch, trừ khi người dùng yêu cầu rõ ràng.

## Khi nào commit
- Commit ngay sau mỗi thay đổi hoàn chỉnh đã qua kiểm tra (mục dưới), không cần chờ hỏi.
- Mỗi commit một thay đổi logic; không gộp việc không liên quan.

## Commit message
- **Không thêm dòng `Co-Authored-By: Claude ...` hay bất kỳ ghi công AI nào.** PR cũng không thêm dòng "Generated with Claude Code".
- Viết bằng **tiếng Anh**, mô tả code làm gì, theo Conventional Commits:
  ```
  <type>(<scope>): <tóm tắt ngắn, thì hiện tại, ≤ 72 ký tự>

  <thân: code này làm gì và vì sao, mỗi dòng ≤ 72 ký tự>
  ```
- `type`: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `ci`, `trim` (xóa bớt code upstream).
- `scope`: `strategy`, `scanner`, `signal`, `telegram`, `config`, `core`, `deploy`, `docs`, `tools`.
- Ví dụ:
  ```
  feat(strategy): add 1h pullback setup with 4h trend filter

  Generate long/short entries when price pulls back to the EMA20-EMA50
  zone in the direction of the 4h regime and RSI recovers above 50.
  ```

## Trước khi commit (bắt buộc)
1. `ruff check` + `ruff format --check` trên file `.py` đã sửa.
2. Đã sửa `freqtrade/` (core) → chạy `pytest` cho thư mục test tương ứng.
3. Đã sửa strategy → chạy skill `/validate-strategy` (ít nhất bản smoke).
4. Quét secret trong `git diff --cached`: không được có API key, secret, token Telegram, mật khẩu, `jwt_secret_key`, `ws_token`.
5. Không commit `user_data/config*.json`, file `.sqlite`, dữ liệu nến, kết quả backtest.

## Lưu ý repo công khai
Repo `BeerNoPro/freqtrade` là fork công khai: **mọi thứ được push đều công khai**. Strategy và config chứa thông số riêng chỉ được commit khi người dùng đồng ý.
