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

Cỡ máy: 2 GB RAM + swap 2 GB (đo trên laptop: runner tối đa ~350 MB; bộ ghi lúc gộp file cuối ngày ~300 MB sau bản
sửa `742b204`, trước đó 1,6 GB; đồng bộ kho hằng ngày thêm ~250 MB). Ổ: bộ ghi ~30 MB/ngày (~11 GB/năm), kho ~160 MB,
venv ~1 GB.

## 2. Cài lần đầu (VPS Ubuntu 24.04 trống)

```bash
ssh ubuntu@159.65.140.79 'bash -s' < deploy/setup_vps.sh
```

Script cài git, sqlite3, uv 0.10.9, Python 3.13; tạo swap 2 GB; giới hạn journald 200 MB; bật tường lửa chỉ mở SSH;
clone `main` vào `~/signal-bot`; tạo venv; cài unit systemd; bật timer kiểm toán và sao lưu. Runner và bộ ghi được
bật nhưng **chưa chạy**, chờ chép dữ liệu (mục 3).

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

## 5. Sao lưu

`signal-backup.timer` (03:30 UTC): bản sao an toàn của `journal.sqlite` và `product.sqlite` (lệnh `.backup` của
sqlite, kiểm tra `integrity_check`), `messages.log`, luật và file riêng → `user_data/backups/<ngày>.tar.gz`, giữ 14
ngày. Kéo về laptop: `powershell -ExecutionPolicy Bypass -File deploy\pull_backup.ps1` (thêm `-All` để kéo cả 14
ngày) → `user_data\vps_backups\`. Khôi phục: dừng `signal-forward`, giải nén, chép hai file sqlite vào
`user_data/research/forward/`, chạy lại.

## 6. Vận hành hằng ngày

```bash
systemctl --user status signal-forward signal-recorder      # đang chạy?
journalctl --user -u signal-forward -n 50                    # lỗi khởi động
tail -n 20 ~/signal-bot/user_data/logs/forward.log           # chu kỳ 4h
cd ~/signal-bot/scripts/research && ../../.venv/bin/python run_forward.py --status
../../.venv/bin/python recorder_coverage.py --days 3
systemctl --user list-timers                                 # kiểm toán, sao lưu
df -h / && free -m                                           # ổ đĩa, RAM
```

Cảnh báo vận hành vẫn gửi qua Telegram như trên laptop (chu kỳ lỗi, runner trễ, bộ ghi ngừng, độ phủ thấp, lệch đối
chiếu, fingerprint chưa đăng ký).
