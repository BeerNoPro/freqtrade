# ============================================================
#  run.ps1 - Trình khởi chạy Freqtrade (local, Windows)
#  Cách dùng:
#    .\run.ps1            -> hiện menu chọn chức năng
#    .\run.ps1 ui         -> chạy WebUI
#    .\run.ps1 trade      -> chạy bot dry-run
#    .\run.ps1 backtest   -> backtest
#    .\run.ps1 hyperopt   -> tối ưu tham số
#    .\run.ps1 download   -> tải dữ liệu lịch sử
#    .\run.ps1 check      -> kiểm tra phiên bản/cài đặt
# ============================================================

param(
    [string]$Action = ""
)

# Ép console hiển thị UTF-8 (cho tiếng Việt không bị vỡ)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# --- Cấu hình (sửa ở đây nếu cần) ---
$FT       = ".\.venv\Scripts\freqtrade.exe"
$CONFIG   = "user_data\config.json"
$STRATEGY = "MyStrategy"
$EXCHANGE = "binance"
$PAIRS    = "BTC/USDT ETH/USDT BNB/USDT SOL/USDT XRP/USDT"
$TIMEFRAME = "5m"
$DAYS     = 30
$HLOSS    = "SharpeHyperOptLoss"
$EPOCHS   = 100
# ------------------------------------

# Kiểm tra freqtrade tồn tại
if (-not (Test-Path $FT)) {
    Write-Host "[LỖI] Không tìm thấy $FT" -ForegroundColor Red
    Write-Host "       Hãy chắc chắn .venv đã được tạo và freqtrade đã cài." -ForegroundColor Yellow
    exit 1
}

function Invoke-UI {
    Write-Host ">> Chạy WebUI tại http://127.0.0.1:8080 (Ctrl+C để dừng)" -ForegroundColor Cyan
    & $FT webserver --config $CONFIG
}

function Invoke-Trade {
    Write-Host ">> Chạy bot DRY-RUN với strategy '$STRATEGY' (Ctrl+C để dừng)" -ForegroundColor Cyan
    & $FT trade --config $CONFIG --strategy $STRATEGY
}

function Invoke-Backtest {
    Write-Host ">> Backtest strategy '$STRATEGY' (timeframe $TIMEFRAME)" -ForegroundColor Cyan
    & $FT backtesting --strategy $STRATEGY --timeframe $TIMEFRAME --config $CONFIG
}

function Invoke-Hyperopt {
    Write-Host ">> Hyperopt '$STRATEGY' ($EPOCHS epochs, loss=$HLOSS)" -ForegroundColor Cyan
    & $FT hyperopt --strategy $STRATEGY --hyperopt-loss $HLOSS --epochs $EPOCHS --config $CONFIG
}

function Invoke-Download {
    Write-Host ">> Tải $DAYS ngày data ($TIMEFRAME) cho: $PAIRS" -ForegroundColor Cyan
    $pairArgs = $PAIRS -split ' '
    & $FT download-data --exchange $EXCHANGE --pairs $pairArgs --timeframe $TIMEFRAME --days $DAYS --config $CONFIG
}

function Invoke-Check {
    & $FT --version
}

function Show-Menu {
    Write-Host ""
    Write-Host "=========== FREQTRADE LAUNCHER ===========" -ForegroundColor Green
    Write-Host " 1) WebUI        (xem dashboard / backtest qua web)"
    Write-Host " 2) Trade        (chạy bot dry-run)"
    Write-Host " 3) Backtest     (kiểm thử chiến lược)"
    Write-Host " 4) Hyperopt     (tối ưu tham số)"
    Write-Host " 5) Download     (tải dữ liệu lịch sử)"
    Write-Host " 6) Check        (kiểm tra phiên bản)"
    Write-Host " 0) Thoát"
    Write-Host "==========================================" -ForegroundColor Green
    $choice = Read-Host "Chọn"
    switch ($choice) {
        "1" { Invoke-UI }
        "2" { Invoke-Trade }
        "3" { Invoke-Backtest }
        "4" { Invoke-Hyperopt }
        "5" { Invoke-Download }
        "6" { Invoke-Check }
        "0" { return }
        default { Write-Host "Lựa chọn không hợp lệ." -ForegroundColor Yellow }
    }
}

switch ($Action.ToLower()) {
    "ui"        { Invoke-UI }
    "web"       { Invoke-UI }
    "trade"     { Invoke-Trade }
    "backtest"  { Invoke-Backtest }
    "hyperopt"  { Invoke-Hyperopt }
    "download"  { Invoke-Download }
    "check"     { Invoke-Check }
    ""          { Show-Menu }
    default     { Write-Host "Action không hợp lệ: '$Action'. Dùng: ui|trade|backtest|hyperopt|download|check" -ForegroundColor Yellow }
}
