# Đăng ký Windows Task Scheduler chạy collector dữ liệu phái sinh (OI, long/short, taker) mỗi ngày.
# Binance chỉ giữ ~30 ngày cho các dữ liệu này: bỏ lỡ quá 29 ngày là mất lịch sử vĩnh viễn.
#
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_collector.ps1            # đăng ký 07:00 hằng ngày
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_collector.ps1 -At 21:30
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_collector.ps1 -Remove
# Trên VPS Linux dùng cron, ví dụ:
#   0 7 * * * cd /path/repo/scripts/research && ../../.venv/bin/python collect_derivs.py --outdir ../../user_data/research/derivs

param(
    [string]$At = "07:00",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "freqtrade-derivs-collector"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$WorkDir = Join-Path $Repo "scripts\research"
$LogDir = Join-Path $Repo "user_data\logs"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Đã xóa task $TaskName"
    exit 0
}

if (-not (Test-Path $Python)) { throw "Không thấy $Python - tạo .venv trước." }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Command = "& '$Python' collect_derivs.py --outdir ..\..\user_data\research\derivs *>> '$LogDir\collector.log'"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$Command`"" -WorkingDirectory $WorkDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Write-Host "Đã đăng ký $TaskName chạy hằng ngày lúc $At (log: $LogDir\collector.log)."
Write-Host "Chạy thử ngay: Start-ScheduledTask -TaskName $TaskName"
