# Chạy bot trên VPS và deploy tự động

Bot không dùng Docker: trên VPS nó chạy y như trên laptop, gồm hai tiến trình Python (runner kiểm định tiến và bộ ghi
dữ liệu) trong một venv. Laptop dùng Task Scheduler, VPS dùng **systemd user service** (tự chạy lại khi lỗi, khi khởi
động lại máy, không cần đăng nhập).

## 1. Kiến trúc

| Thành phần | Ở đâu trên VPS |
|---|---|
| Code (repo công khai, nhánh `main`) | `~/signal-bot` (git checkout) |
| Python 3.13 + thư viện (`requirements.txt`) | `~/signal-bot/.venv` (uv) |
| Luật riêng, kho dữ liệu, nhật ký, sổ rủi ro, log | `~/signal-bot/user_data/` (**không bao giờ** đi qua git hay deploy) |
| Token Telegram | `~/signal-bot/.env` (chmod 600) |
| Runner | `signal-forward.service` → `scripts/research/run_forward.py` |
| Bộ ghi | `signal-recorder.service` → `scripts/research/record_market.py` |
| Kiểm toán kho 03:00 UTC | `signal-audit.timer` → `audit_store.py --verify 30` |
| Sao lưu 03:30 UTC | `signal-backup.timer` → `deploy/backup.sh` |
| Đẩy lên Google Drive 03:45 UTC | `signal-offload.timer` → `deploy/offload.sh` (rclone) |

**Cỡ máy: chạy trên gói nhỏ nhất (512 MB RAM, 10 GB SSD), có swap 1 GB.** Đo trên laptop:
- RAM: runner tối đa ~350 MB lúc chạy chu kỳ, bộ ghi ~100 MB khi chạy thường.
- Bộ ghi gộp file ngày cũ vào **01:00 UTC**, tốn ~300 MB (bản sửa `742b204`; trước đó 1,6 GB). Giờ này lệch khỏi chu
  kỳ ngày của runner (00:01 UTC) và đợt đồng bộ kho (01:30 UTC), nên mỗi lúc chỉ có một đỉnh.
- RAM trống của máy 512 MB khoảng 280 MB; phần vượt dùng swap, lúc cao điểm chậm hơn chứ không chết.
- Đo trên VPS ngày 2026-10-17, chu kỳ đầu: RSS runner tối đa 236 MB, bộ ghi 30 MB; RAM trống thấp nhất 112 MB; swap
  dùng ~250 MB; cả chu kỳ chưa tới 2 phút.
- Ổ: hệ điều hành ~2,8 GB, venv ~0,7 GB, swap 1 GB, kho ~0,2 GB; bộ ghi thêm **~30 MB/ngày** (~11 GB/năm). Vì vậy
  ngày dữ liệu bộ ghi cũ hơn 30 ngày được chuyển lên Google Drive (mục 5), VPS chỉ giữ khoảng 1 GB.
- Khi bot chạy ổn định và cần thêm (UI, giao dịch thật) thì mới nâng máy.

## 2. Cài lần đầu (VPS Ubuntu 24.04 trống)

```bash
ssh ubuntu@159.65.140.79 'bash -s' < deploy/setup_vps.sh
```

Script cài git, sqlite3, rclone, uv 0.10.9, Python 3.13; tạo swap 1 GB; giới hạn journald 100 MB; tắt ModemManager và fwupd (~40 MB RAM); bật tường lửa chỉ
mở SSH; clone `main` vào `~/signal-bot` (không tải nội dung file cũ); tạo venv (không giữ cache); cài unit systemd;
bật timer kiểm toán, sao lưu và đẩy Drive. Runner và bộ ghi được bật nhưng **chưa chạy**, chờ chép dữ liệu (mục 3).

## 3. Chuyển từ laptop (một lần)

Điều kiện: code trên laptop trùng commit `main` mà VPS đang checkout (merge `develop` → `main` trước), vì fingerprint
của luật tính trên từng byte của code.

1. Chọn lúc rảnh: ngoài 01:25–03:10 UTC và cách mỗi lần đóng nến 4h ít nhất 12 phút; dòng log cuối của
   `user_data/logs/forward.log` là `product: ...`.
2. Trên laptop, dừng và tắt task (chưa gỡ):
   ```powershell
   'freqtrade-forward-runner','freqtrade-market-recorder','freqtrade-data-audit' | ForEach-Object { Disable-ScheduledTask $_ }
   Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'run_forward|record_market' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
   ```
3. Chép dữ liệu riêng và `.env` (dùng `tar.exe` có sẵn trên Windows):
   ```powershell
   tar -czf - -C user_data/research pit forward live product.json thresholds.json fingerprints.jsonl audit_known.csv forward_v1.py forward_v2.py liquidation_events_v1.py | ssh ubuntu@159.65.140.79 "tar -xzf - -C signal-bot/user_data/research"
   scp .env ubuntu@159.65.140.79:signal-bot/.env; ssh ubuntu@159.65.140.79 "chmod 600 signal-bot/.env"
   ```
4. Trên VPS, kiểm tra rồi chạy:
   ```bash
   cd ~/signal-bot/scripts/research && ../../.venv/bin/python deploy_preflight.py --research ../../user_data/research --env-file ../../.env
   systemctl --user start signal-recorder signal-forward
   tail -f ~/signal-bot/user_data/logs/forward.log   # chờ dòng "product: ..." của chu kỳ đầu
   ```
5. Chỉ một runner và một bộ ghi cho mỗi bộ dữ liệu: giữ task Windows ở trạng thái tắt. Sau 1 tuần chạy ổn trên VPS mới
   gỡ hẳn (`schedule_forward.ps1 -Remove`, `schedule_recorder.ps1 -Remove`, `schedule_audit.ps1 -Remove`).

## 4. Deploy tự động: PR vào `main` → VPS

Luồng: commit trên `develop` → PR `develop` → `main` → merge → workflow **CI** (`.github/workflows/ci.yml`) chạy
trên `main`: job `tests` đạt thì job `deploy` SSH vào VPS và gọi `deploy/deploy.sh <sha>` (run của `main` không bao
giờ bị hủy giữa chừng; trên VPS deploy chạy tách khỏi phiên SSH nên rớt mạng cũng không dừng nửa vời). Mọi kiểm tra
nằm trên VPS:

1. Commit phải có trên `origin/main`.
2. **Preflight trên code mới** (`scripts/research/deploy_preflight.py`, trong một worktree tạm): fingerprint của từng
   luật đang chạy phải trùng bản đã đăng ký; đủ file riêng; có token Telegram. Đổi `requirements.txt` thì dựng venv
   mới trước. Sai → giữ nguyên bản đang chạy, job báo đỏ.
3. Chỉ khởi động lại service có code thay đổi (docs-only thì không đụng gì), và chỉ vào **lúc an toàn**: ngoài
   01:25–03:10 UTC, không trong 12 phút sau mỗi lần đóng nến 4h, runner đã xong chu kỳ. Chờ tối đa 90 phút.
4. Sau khi đổi: runner phải ghi dòng `product:` mới (nó chạy một chu kỳ ngay khi khởi động), bộ ghi phải có nhịp
   tim mới, trong 10 phút. Không đạt → **tự quay về commit cũ** (cả venv cũ), job báo đỏ.

Mỗi lần deploy ghi một dòng vào `~/signal-bot/user_data/logs/deploys.log`.

### Cài đặt GitHub (một lần)

1. Tạo khóa riêng cho deploy trên laptop (không đặt mật khẩu):
   `ssh-keygen -t ed25519 -N '""' -C github-deploy -f $HOME\.ssh\signal_bot_deploy`
2. Thêm khóa công khai vào `~/.ssh/authorized_keys` của VPS, **có giới hạn lệnh**: khóa này chỉ chạy được
   `deploy <sha>` hoặc `status`, không mở được shell:
   ```
   command="/home/ubuntu/signal-bot/deploy/ssh_command.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-deploy
   ```
3. Lấy dấu vân tay VPS: `ssh-keyscan -t ed25519 159.65.140.79`.
4. GitHub → repo → Settings → Secrets and variables → Actions → New repository secret:
   `VPS_HOST` = `159.65.140.79`, `VPS_USER` = `ubuntu`, `VPS_SSH_KEY` = nội dung file khóa riêng,
   `VPS_KNOWN_HOSTS` = dòng của bước 3.
5. Thử: Actions → CI → Run workflow (nhánh `main`): chạy test rồi deploy commit hiện tại của `main`.

Repo công khai nên log của Actions ai cũng xem được: workflow không in gì riêng tư (IP, khóa là secret, bị che).

### Deploy tay, xem trạng thái, quay lại bản cũ

```bash
~/signal-bot/deploy/deploy.sh <40-char sha>     # deploy một commit của main
tail ~/signal-bot/user_data/logs/deploys.log     # lịch sử deploy
~/signal-bot/deploy/deploy.sh <sha cũ>           # quay lại: cùng các bước kiểm tra
```

## 5. Sao lưu, Google Drive, kéo về laptop

**Trên VPS** (`signal-backup.timer`, 03:30 UTC): bản sao an toàn của `journal.sqlite` và `product.sqlite` (lệnh
`.backup` của sqlite, kiểm tra `integrity_check`), `messages.log`, luật và file riêng →
`user_data/backups/<ngày>.tar.gz`, giữ 14 ngày (mỗi bản chỉ vài trăm KB).

**Lên Google Drive** (`signal-offload.timer`, 03:45 UTC, `deploy/offload.sh`): VPS tự đẩy, không cần laptop bật.
- Đẩy bản sao lưu (giữ 90 ngày trên Drive) và mọi ngày dữ liệu bộ ghi đã gộp vào thư mục `signal-bot/` trên Drive.
- Ngày bộ ghi cũ hơn 30 ngày được **chuyển** hẳn lên Drive (rclone kiểm tra từng file đã lên đủ rồi mới xóa trên VPS).
- Chưa cài Drive thì job chỉ ghi dung lượng ổ vào `user_data/logs/offload.log`, không làm gì khác.
- Dung lượng Drive: ~11 GB/năm cho dữ liệu bộ ghi; gói Drive miễn phí 15 GB dùng chung với Gmail.

Cài Drive một lần (rclone, quyền `drive.file`: rclone chỉ thấy file do chính nó tạo, không đọc được phần còn lại của
Drive):
1. Trên laptop cài rclone: `winget install Rclone.Rclone`.
2. Trên VPS: `rclone config` → `n` (remote mới) → tên **`gdrive`** → loại `drive` → để trống client id/secret →
   scope chọn **`drive.file`** → các câu sau để mặc định → "Use web browser to automatically authenticate?" chọn
   **`n`**. rclone in ra một lệnh `rclone authorize "drive" "..."`.
3. Chạy lệnh đó trên laptop: trình duyệt mở, đăng nhập Google, cho phép; laptop in ra một đoạn token.
4. Dán token vào VPS, xác nhận. Thử: `~/signal-bot/deploy/offload.sh`, rồi xem thư mục `signal-bot` trên Drive.

Token nằm trong `~/.config/rclone/rclone.conf` của VPS (chỉ user `ubuntu` đọc được). Thu hồi bất cứ lúc nào ở
myaccount.google.com → Security → Third-party apps.

**Kéo về laptop:**
- Bản sao lưu từ VPS: `powershell -ExecutionPolicy Bypass -File deploy\pull_backup.ps1` (`-All`: cả 14 ngày) →
  `user_data\vps_backups\`.
- Dữ liệu bộ ghi cho nghiên cứu, từ Drive: `rclone copy gdrive:signal-bot/live user_data/research/live` (cần chạy
  bước 2–4 ở trên trên laptop với cùng tên remote), hoặc tải bằng Google Drive for desktop.

**Khôi phục:** dừng `signal-forward`, giải nén bản sao lưu, chép hai file sqlite vào `user_data/research/forward/`,
chạy lại.

## 6. Vận hành hằng ngày

```bash
systemctl --user status signal-forward signal-recorder      # đang chạy?
journalctl --user -u signal-forward -n 50                    # lỗi khởi động
tail -n 20 ~/signal-bot/user_data/logs/forward.log           # chu kỳ 4h
cd ~/signal-bot/scripts/research && ../../.venv/bin/python run_forward.py --status
../../.venv/bin/python recorder_coverage.py --days 3
systemctl --user list-timers                                 # kiểm toán, sao lưu, đẩy Drive
tail -n 5 ~/signal-bot/user_data/logs/offload.log            # lần đẩy Drive gần nhất + ổ còn trống
df -h / && free -m                                           # ổ đĩa, RAM, swap
```

Cảnh báo vận hành vẫn gửi qua Telegram như trên laptop (chu kỳ lỗi, runner trễ, bộ ghi ngừng, độ phủ thấp, lệch đối
chiếu, fingerprint chưa đăng ký).
