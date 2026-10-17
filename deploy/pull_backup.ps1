# Kéo bản sao lưu mới nhất của VPS về laptop (deploy/backup.sh tạo mỗi ngày lúc 03:30 UTC).
#   powershell -ExecutionPolicy Bypass -File deploy\pull_backup.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\pull_backup.ps1 -All   # cả 14 ngày
# Lưu vào user_data\vps_backups\ (gitignore). Dùng khóa SSH thường của bạn, không phải khóa deploy.

param(
    [string]$Server = "ubuntu@159.65.140.79",
    [switch]$All
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Dest = Join-Path $Repo "user_data\vps_backups"
New-Item -ItemType Directory -Force $Dest | Out-Null

$list = ssh $Server "ls -1t signal-bot/user_data/backups/*.tar.gz 2>/dev/null"
if (-not $list) { throw "Không thấy bản sao lưu nào trên $Server" }
$files = @($list)
if (-not $All) { $files = @($files[0]) }
foreach ($f in $files) {
    scp "${Server}:$f" $Dest
    if ($LASTEXITCODE -ne 0) { throw "scp $f thất bại" }
}
Write-Host "Đã kéo $($files.Count) bản sao lưu về $Dest"
