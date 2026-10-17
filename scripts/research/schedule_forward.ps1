# Đăng ký Windows Task Scheduler chạy liên tục runner kiểm định tiến (scripts/research/run_forward.py).
# Mỗi 4 giờ (đóng nến + 90 giây) runner cập nhật kho dữ liệu, ghi setup mới vào nhật ký TRƯỚC khi biết kết quả,
# chấm setup đã đóng và đối chiếu với bản chạy lại. Máy ngủ thì setup bị lỡ được ghi bù (đánh dấu live_late).
# Trên VPS không dùng task này mà dùng systemd `signal-forward.service` (docs/deploy-vps.md).
#
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_forward.ps1          # đăng ký và chạy ngay
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_forward.ps1 -Remove  # dừng và gỡ

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "freqtrade-forward-runner"
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

# Cửa sổ PowerShell ẩn, log ở user_data\logs\forward.log. Khởi động khi đăng nhập; kích hoạt lặp mỗi 5 phút làm
# watchdog (đang chạy thì bỏ qua, đã dừng thì bật lại); không giới hạn thời gian chạy.
$Command = "& '$Python' run_forward.py; exit `$LASTEXITCODE"
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
Write-Host "Đã đăng ký và chạy $TaskName (log: user_data\logs\forward.log, nhật ký: user_data\research\forward)."
