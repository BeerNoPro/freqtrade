# Đăng ký Windows Task Scheduler chạy liên tục bộ ghi dữ liệu thị trường (scripts/research/record_market.py).
# Bộ ghi lưu những gì Binance không giữ lịch sử: thanh lý, giá mua/bán tốt nhất, funding dự kiến, sổ lệnh,
# quyền chọn (IV, greeks, OI), thông tin sàn hằng ngày -> user_data/research/live.
# Máy tắt hoặc ngủ thì khoảng đó mất dữ liệu (ghi lại trong user_data/research/live/_gaps.jsonl).
# Trên VPS không dùng task này mà dùng systemd `signal-recorder.service` (docs/deploy-vps.md).
#
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_recorder.ps1          # đăng ký và chạy ngay
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_recorder.ps1 -Remove  # dừng và gỡ

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "freqtrade-market-recorder"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$WorkDir = Join-Path $Repo "scripts\research"

if ($Remove) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Đã dừng và gỡ task $TaskName"
    exit 0
}

if (-not (Test-Path $Python)) { throw "Không thấy $Python - tạo .venv trước." }

# Chạy trong cửa sổ PowerShell ẩn (pythonw của venv uv vẫn gọi python.exe có console), log ở
# user_data\logs\recorder.log. Khởi động khi đăng nhập; kích hoạt lặp mỗi 5 phút làm watchdog (đang chạy thì bỏ qua,
# đã dừng thì bật lại); không giới hạn thời gian chạy.
$Command = "& '$Python' record_market.py; exit `$LASTEXITCODE"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $WorkDir `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$Command`""
$Triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Đã đăng ký và chạy $TaskName (log: user_data\logs\recorder.log, dữ liệu: user_data\research\live)."
