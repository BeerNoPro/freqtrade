# Đăng ký Windows Task Scheduler chạy kiểm toán kho dữ liệu nghiên cứu (scripts/research/audit_store.py) mỗi ngày
# lúc 03:00 UTC, sau đợt đồng bộ kho đầy đủ của runner (01:30 UTC). Mỗi lần: quét offline toàn kho pit và đối chiếu
# 30 mẫu mỗi loại dữ liệu với Binance (API fapi và data.binance.vision).
# Báo cáo: user_data\reports\audit\latest\pit\summary.md; log: user_data\logs\audit.log.
# Kết quả lần chạy cuối của task = 1 khi có lỗi critical mới (lỗi đã đối chiếu tay nằm trong
# user_data\research\audit_known.csv và không tính). Máy ngủ lúc 03:00 UTC thì task chạy khi máy thức.
#
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_audit.ps1          # đăng ký
#   powershell -ExecutionPolicy Bypass -File scripts\research\schedule_audit.ps1 -Remove  # gỡ

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "freqtrade-data-audit"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$WorkDir = Join-Path $Repo "scripts\research"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Đã gỡ task $TaskName"
    exit 0
}

if (-not (Test-Path $Python)) { throw "Không thấy $Python - tạo .venv trước." }

$Command = "& '$Python' audit_store.py --verify 30; exit `$LASTEXITCODE"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $WorkDir `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$Command`""
$At = [DateTime]::SpecifyKind([DateTime]::UtcNow.Date.AddHours(3), [DateTimeKind]::Utc).ToLocalTime()
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Write-Host "Đã đăng ký ${TaskName}: mỗi ngày lúc $($At.ToString('HH:mm')) giờ máy (03:00 UTC)."
