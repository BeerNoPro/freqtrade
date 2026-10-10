# ============================================================
#  run.ps1 - Trình khởi chạy bot tín hiệu Binance USDT-M Futures (local, Windows)
#  Cách dùng:
#    .\run.ps1                       -> hiện menu chọn chức năng
#    .\run.ps1 ui                    -> chạy WebUI (FreqUI)
#    .\run.ps1 trade [Strategy]      -> chạy bot dry-run (luôn ép --dry-run)
#    .\run.ps1 backtest [Strategy]   -> backtest với giả định của dự án
#    .\run.ps1 hyperopt [Strategy]   -> tối ưu tham số
#    .\run.ps1 download              -> tải dữ liệu 1h + 4h futures
#    .\run.ps1 check                 -> kiểm tra phiên bản/cài đặt
#  Thiết kế và ngưỡng kiểm chứng: docs\signal-bot-workflow.md
# ============================================================

param(
    [string]$Action = "",
    [string]$Strategy = "FuturesSignalStrategy"
)

# Ép console hiển thị UTF-8 (cho tiếng Việt không bị vỡ)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# --- Cấu hình (sửa ở đây nếu cần) ---
$FT         = ".\.venv\Scripts\freqtrade.exe"
$CONFIG     = "user_data\config_futures.json"   # kế thừa config.json qua add_config_files
$TIMEFRAMES = @("1h", "4h")                     # 1h ra tín hiệu, 4h lọc xu hướng
$TIMERANGE  = "20221101-"                       # dư 2 tháng làm nóng chỉ báo trước 01/2023
$FEE        = "0.0006"                          # phí 0,06%/chiều (giả định khắt khe)
$HLOSS      = "SharpeHyperOptLoss"
$EPOCHS     = 100
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
    Write-Host ">> Chạy bot DRY-RUN với strategy '$Strategy' (Ctrl+C để dừng)" -ForegroundColor Cyan
    & $FT trade --dry-run --config $CONFIG --strategy $Strategy
}

function Invoke-Backtest {
    Write-Host ">> Backtest '$Strategy' (phí $FEE, có protections, chia theo tháng)" -ForegroundColor Cyan
    & $FT backtesting --config $CONFIG --strategy $Strategy --fee $FEE --enable-protections --breakdown month
}

function Invoke-Hyperopt {
    Write-Host ">> Hyperopt '$Strategy' ($EPOCHS epochs, loss=$HLOSS)" -ForegroundColor Cyan
    & $FT hyperopt --config $CONFIG --strategy $Strategy --hyperopt-loss $HLOSS --epochs $EPOCHS --fee $FEE --enable-protections
}

function Invoke-Download {
    Write-Host ">> Tải dữ liệu futures ($($TIMEFRAMES -join ', ')) từ $TIMERANGE cho các cặp trong config" -ForegroundColor Cyan
    & $FT download-data --config $CONFIG --timeframes $TIMEFRAMES --timerange $TIMERANGE
}

function Invoke-Check {
    & $FT --version
}

function Show-Menu {
    Write-Host ""
    Write-Host "======= BOT TÍN HIỆU FUTURES (Binance) =======" -ForegroundColor Green
    Write-Host " Strategy: $Strategy | Config: $CONFIG"
    Write-Host " 1) WebUI        (FreqUI: xem tín hiệu, biểu đồ)"
    Write-Host " 2) Trade        (chạy bot dry-run)"
    Write-Host " 3) Backtest     (kiểm thử chiến lược)"
    Write-Host " 4) Hyperopt     (tối ưu tham số)"
    Write-Host " 5) Download     (tải dữ liệu 1h + 4h)"
    Write-Host " 6) Check        (kiểm tra phiên bản)"
    Write-Host " 0) Thoát"
    Write-Host "==============================================" -ForegroundColor Green
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
