
Freqtrade là một bot giao dịch crypto miễn phí và mã nguồn mở được viết bằng Python. Nó được thiết kế để hỗ trợ tất cả các sàn giao dịch lớn và được điều khiển qua Telegram hoặc giao diện web (webUI). Nó bao gồm các công cụ backtesting (kiểm thử lại trên dữ liệu lịch sử), vẽ biểu đồ và quản lý vốn, cũng như tối ưu hóa chiến lược bằng học máy (machine learning).


---

## 🚀 Hướng dẫn chạy app ở local (Windows)

> Phần này dành riêng cho môi trường đã cài sẵn của dự án này. Tài liệu định hướng thêm: [docs/overview.md](docs/overview.md) · [docs/structure.md](docs/structure.md) · [docs/lifecycle.md](docs/lifecycle.md).

### Môi trường đã có sẵn
- Python 3.13 trong `.venv`, freqtrade cài chế độ *editable* (trỏ vào source).
- TA-Lib + toàn bộ dependency đã cài. Database dùng **SQLite** (tự tạo file, không cần cài gì).
- FreqUI (giao diện web) đã cài, config dry-run sẵn ở `user_data/config.json`, strategy mẫu `MyStrategy`.

> ⚠️ Mọi lệnh gọi qua `.venv\Scripts\freqtrade.exe` (vì `freqtrade` chưa nằm trong PATH). Hoặc activate venv trước: `.venv\Scripts\Activate.ps1` rồi gõ `freqtrade ...`.

### 1. Kiểm tra cài đặt
```powershell
.venv\Scripts\freqtrade.exe --version
```

### 2. Tải dữ liệu lịch sử (public, KHÔNG cần API key)
```powershell
.venv\Scripts\freqtrade.exe download-data --exchange binance `
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT XRP/USDT `
  --timeframe 5m --days 30 --config user_data\config.json
```

### 3. Chạy WebUI (chỉ xem dashboard / backtest, không vào lệnh)
```powershell
.venv\Scripts\freqtrade.exe webserver --config user_data\config.json
```
→ Mở trình duyệt: **http://127.0.0.1:8080**
Đăng nhập: user `freqtrader` / mật khẩu xem trong `user_data\config.json` (mục `api_server`).

### 4. Chạy bot ở chế độ Dry-run (tiền ảo + WebUI đầy đủ)
```powershell
.venv\Scripts\freqtrade.exe trade --config user_data\config.json --strategy MyStrategy
```

### 5. Backtest chiến lược
```powershell
.venv\Scripts\freqtrade.exe backtesting --strategy MyStrategy `
  --timeframe 5m --config user_data\config.json
```

### 6. Tối ưu tham số (Hyperopt) — tùy chọn, tốn CPU
```powershell
.venv\Scripts\freqtrade.exe hyperopt --strategy MyStrategy `
  --hyperopt-loss SharpeHyperOptLoss --epochs 100 --config user_data\config.json
```

### Tạo mới (khi cần)
```powershell
.venv\Scripts\freqtrade.exe new-strategy --strategy TenChienLuoc   # tạo strategy mới
.venv\Scripts\freqtrade.exe new-config --config user_data\config2.json   # tạo config mới
.venv\Scripts\freqtrade.exe install-ui                              # cài lại/cập nhật FreqUI
```

### Kiểm tra chất lượng code (trước khi commit)
```powershell
ruff check freqtrade
ruff format freqtrade
mypy freqtrade
pytest
```

> 🔒 **An toàn**: luôn giữ `"dry_run": true` trong config khi thử nghiệm. Không commit API key/secret. File `user_data\config.json` chứa mật khẩu nên không nên đẩy lên git.

---

## Tuyên bố miễn trừ trách nhiệm

Phần mềm này chỉ dành cho mục đích giáo dục. Đừng mạo hiểm số tiền mà
bạn sợ mất. SỬ DỤNG PHẦN MỀM NÀY VỚI RỦI RO CỦA CHÍNH BẠN. CÁC TÁC GIẢ
VÀ TẤT CẢ CÁC BÊN LIÊN KẾT KHÔNG CHỊU TRÁCH NHIỆM CHO KẾT QUẢ GIAO DỊCH CỦA BẠN.

Luôn bắt đầu bằng cách chạy bot giao dịch ở chế độ Dry-Run (chạy thử) và không
dùng tiền thật trước khi bạn hiểu cách nó hoạt động và mức lãi/lỗ mà
bạn nên kỳ vọng.

Chúng tôi đặc biệt khuyến nghị bạn nên có kiến thức về lập trình và Python. Đừng
ngần ngại đọc mã nguồn và hiểu cơ chế hoạt động của bot này.

## Các sàn giao dịch được hỗ trợ

Vui lòng đọc [ghi chú riêng cho từng sàn](https://www.freqtrade.io/en/stable/exchanges/) để tìm hiểu về các cấu hình đặc biệt có thể cần thiết cho mỗi sàn.

### Các sàn Spot được hỗ trợ

- [X] [Binance](https://www.binance.com/)
- [X] [BingX](https://bingx.com/invite/0EM9RX)
- [X] [Bitget](https://www.bitget.com/)
- [X] [Bitmart](https://bitmart.com/)
- [X] [Bybit](https://bybit.com/)
- [X] [Gate.io](https://www.gate.io/ref/6266643)
- [X] [HTX](https://www.htx.com/)
- [X] [Hyperliquid](https://hyperliquid.xyz/) (Một sàn giao dịch phi tập trung, hay DEX)
- [X] [Kraken](https://kraken.com/)
- [X] [OKX](https://okx.com/)
- [X] [MyOKX](https://okx.com/) (OKX EEA)
- [ ] [có thể còn nhiều sàn khác](https://github.com/ccxt/ccxt/). _(Chúng tôi không thể đảm bảo chúng sẽ hoạt động)_

### Các sàn Futures được hỗ trợ

- [X] [Binance](https://www.binance.com/)
- [X] [Bitget](https://www.bitget.com/)
- [X] [Gate.io](https://www.gate.io/ref/6266643)
- [X] [Hyperliquid](https://hyperliquid.xyz/) (Một sàn giao dịch phi tập trung, hay DEX)
- [X] [OKX](https://okx.com/)
- [X] [Bybit](https://bybit.com/)
- [X] [Kraken](https://www.kraken.com/features/futures)

Vui lòng đảm bảo đọc [ghi chú riêng cho từng sàn](https://www.freqtrade.io/en/stable/exchanges/), cũng như tài liệu [giao dịch với đòn bẩy](https://www.freqtrade.io/en/stable/leverage/) trước khi bắt đầu.

### Được cộng đồng kiểm thử

Các sàn được cộng đồng xác nhận là hoạt động:

- [X] [Bitvavo](https://bitvavo.com/)
- [X] [Kucoin](https://www.kucoin.com/)

## Tài liệu

Chúng tôi mời bạn đọc tài liệu của bot để đảm bảo bạn hiểu cách bot hoạt động.

Vui lòng xem tài liệu đầy đủ trên [website freqtrade](https://www.freqtrade.io).

## Tính năng

- [x] **Dựa trên Python 3.11+**: Để chạy bot trên mọi hệ điều hành - Windows, macOS và Linux.
- [x] **Lưu trữ bền vững (Persistence)**: Việc lưu trữ được thực hiện thông qua sqlite.
- [x] **Dry-run (Chạy thử)**: Chạy bot mà không cần dùng tiền thật.
- [x] **Backtesting (Kiểm thử lại)**: Chạy mô phỏng chiến lược mua/bán của bạn.
- [x] **Tối ưu hóa chiến lược bằng học máy**: Dùng học máy để tối ưu các tham số chiến lược mua/bán với dữ liệu thực từ sàn.
- [X] **Mô hình dự đoán thích ứng**: Xây dựng chiến lược thông minh với FreqAI, tự huấn luyện theo thị trường thông qua các phương pháp học máy thích ứng. [Tìm hiểu thêm](https://www.freqtrade.io/en/stable/freqai/)
- [x] **Danh sách trắng (Whitelist) tiền điện tử**: Chọn loại tiền điện tử bạn muốn giao dịch hoặc dùng danh sách trắng động.
- [x] **Danh sách đen (Blacklist) tiền điện tử**: Chọn loại tiền điện tử bạn muốn tránh.
- [x] **WebUI tích hợp sẵn**: Giao diện web tích hợp sẵn để quản lý bot của bạn.
- [x] **Quản lý qua Telegram**: Quản lý bot bằng Telegram.
- [x] **Hiển thị lãi/lỗ bằng tiền pháp định (fiat)**: Hiển thị lãi/lỗ của bạn theo đơn vị tiền pháp định.
- [x] **Báo cáo trạng thái hiệu suất**: Cung cấp báo cáo trạng thái hiệu suất các giao dịch hiện tại của bạn.

## Bắt đầu nhanh

Vui lòng tham khảo [tài liệu Bắt đầu nhanh với Docker](https://www.freqtrade.io/en/stable/docker_quickstart/) để biết cách bắt đầu nhanh chóng.

Đối với các phương pháp cài đặt khác (cài đặt trực tiếp - native), vui lòng tham khảo [trang tài liệu Cài đặt](https://www.freqtrade.io/en/stable/installation/).

## Cách sử dụng cơ bản

### Các lệnh của Bot

```
usage: freqtrade [-h] [-V]
                 {trade,create-userdir,new-config,show-config,new-strategy,download-data,convert-data,convert-trade-data,trades-to-ohlcv,list-data,backtesting,backtesting-show,backtesting-analysis,edge,hyperopt,hyperopt-list,hyperopt-show,list-exchanges,list-markets,list-pairs,list-strategies,list-hyperoptloss,list-freqaimodels,list-timeframes,show-trades,test-pairlist,convert-db,install-ui,plot-dataframe,plot-profit,webserver,strategy-updater,lookahead-analysis,recursive-analysis}
                 ...

Free, open source crypto trading bot

positional arguments:
  {trade,create-userdir,new-config,show-config,new-strategy,download-data,convert-data,convert-trade-data,trades-to-ohlcv,list-data,backtesting,backtesting-show,backtesting-analysis,edge,hyperopt,hyperopt-list,hyperopt-show,list-exchanges,list-markets,list-pairs,list-strategies,list-hyperoptloss,list-freqaimodels,list-timeframes,show-trades,test-pairlist,convert-db,install-ui,plot-dataframe,plot-profit,webserver,strategy-updater,lookahead-analysis,recursive-analysis}
    trade               Mô-đun giao dịch (Trade module).
    create-userdir      Tạo thư mục user-data.
    new-config          Tạo config mới.
    show-config         Hiển thị config đã được phân giải (resolved).
    new-strategy        Tạo chiến lược mới.
    download-data       Tải dữ liệu để backtesting.
    convert-data        Chuyển đổi dữ liệu nến (OHLCV) từ định dạng này
                        sang định dạng khác.
    convert-trade-data  Chuyển đổi dữ liệu giao dịch từ định dạng này sang định dạng khác.
    trades-to-ohlcv     Chuyển dữ liệu giao dịch sang dữ liệu OHLCV.
    list-data           Liệt kê dữ liệu đã tải về.
    backtesting         Mô-đun backtesting.
    backtesting-show    Hiển thị các kết quả Backtest trước đó.
    backtesting-analysis
                        Mô-đun phân tích Backtest.
    hyperopt            Mô-đun Hyperopt.
    hyperopt-list       Liệt kê các kết quả Hyperopt.
    hyperopt-show       Hiển thị chi tiết các kết quả Hyperopt.
    list-exchanges      In ra các sàn giao dịch có sẵn.
    list-markets        In ra các thị trường (markets) trên sàn.
    list-pairs          In ra các cặp giao dịch (pairs) trên sàn.
    list-strategies     In ra các chiến lược có sẵn.
    list-hyperoptloss   In ra các hàm hyperopt loss có sẵn.
    list-freqaimodels   In ra các mô hình freqAI có sẵn.
    list-timeframes     In ra các khung thời gian (timeframes) có sẵn của sàn.
    show-trades         Hiển thị các giao dịch.
    test-pairlist       Kiểm thử cấu hình danh sách cặp (pairlist) của bạn.
    convert-db          Di chuyển cơ sở dữ liệu sang hệ thống khác.
    install-ui          Cài đặt FreqUI.
    plot-dataframe      Vẽ biểu đồ nến với các chỉ báo.
    plot-profit         Tạo biểu đồ hiển thị lợi nhuận.
    webserver           Mô-đun máy chủ web (Webserver).
    strategy-updater    Cập nhật các file chiến lược lỗi thời lên phiên bản hiện tại.
    lookahead-analysis  Kiểm tra thiên lệch nhìn trước (look ahead bias) tiềm ẩn.
    recursive-analysis  Kiểm tra vấn đề công thức đệ quy (recursive formula) tiềm ẩn.

options:
  -h, --help            hiển thị thông báo trợ giúp này và thoát.
  -V, --version         hiển thị số phiên bản của chương trình và thoát.
```

### Các lệnh RPC qua Telegram

Telegram không bắt buộc. Tuy nhiên, đây là một cách tuyệt vời để điều khiển bot của bạn. Xem thêm chi tiết và danh sách lệnh đầy đủ trong [tài liệu](https://www.freqtrade.io/en/stable/telegram-usage/).

- `/start`: Khởi động trình giao dịch.
- `/stop`: Dừng trình giao dịch.
- `/stopentry`: Dừng vào các lệnh mới.
- `/status <trade_id>|[table]`: Liệt kê tất cả hoặc các giao dịch đang mở cụ thể.
- `/profit [<n>]`: Liệt kê lợi nhuận tích lũy từ tất cả giao dịch đã hoàn thành, trong n ngày gần nhất.
- `/profit_long [<n>]`: Liệt kê lợi nhuận tích lũy từ tất cả giao dịch long đã hoàn thành, trong n ngày gần nhất.
- `/profit_short [<n>]`: Liệt kê lợi nhuận tích lũy từ tất cả giao dịch short đã hoàn thành, trong n ngày gần nhất.
- `/forceexit <trade_id>|all`: Thoát ngay lập tức giao dịch chỉ định (Bỏ qua `minimum_roi`).
- `/fx <trade_id>|all`: Bí danh của `/forceexit`.
- `/performance`: Hiển thị hiệu suất của từng giao dịch đã hoàn thành, nhóm theo cặp.
- `/balance`: Hiển thị số dư tài khoản theo từng loại tiền.
- `/daily <n>`: Hiển thị lãi hoặc lỗ mỗi ngày, trong n ngày gần nhất.
- `/help`: Hiển thị thông báo trợ giúp.
- `/version`: Hiển thị phiên bản.


## Các nhánh phát triển

Dự án hiện được thiết lập với hai nhánh chính:

- `develop` - Nhánh này thường có các tính năng mới, nhưng cũng có thể chứa các thay đổi gây phá vỡ tương thích (breaking changes). Chúng tôi cố gắng hết sức giữ cho nhánh này ổn định nhất có thể.
- `stable` - Nhánh này chứa bản phát hành ổn định mới nhất. Nhánh này nhìn chung được kiểm thử kỹ lưỡng.
- `feat/*` - Đây là các nhánh tính năng, đang được phát triển mạnh. Vui lòng không sử dụng các nhánh này trừ khi bạn muốn kiểm thử một tính năng cụ thể.

## Hỗ trợ

### Trợ giúp / Discord

Đối với bất kỳ câu hỏi nào không được đề cập trong tài liệu, hoặc để biết thêm thông tin về bot, hoặc đơn giản là giao lưu với những người cùng chí hướng, chúng tôi khuyến khích bạn tham gia [máy chủ discord](https://discord.gg/p7nuUNVfP7) của Freqtrade.

### [Lỗi / Vấn đề (Bugs / Issues)](https://github.com/freqtrade/freqtrade/issues?q=is%3Aissue)

Nếu bạn phát hiện một lỗi trong bot, vui lòng
[tìm trong trình theo dõi vấn đề (issue tracker)](https://github.com/freqtrade/freqtrade/issues?q=is%3Aissue)
trước. Nếu nó chưa được báo cáo, vui lòng
[tạo một vấn đề mới](https://github.com/freqtrade/freqtrade/issues/new/choose) và
đảm bảo bạn tuân theo hướng dẫn mẫu (template guide) để đội ngũ có thể hỗ trợ bạn
nhanh nhất có thể.

Đối với mỗi [vấn đề](https://github.com/freqtrade/freqtrade/issues/new/choose) được tạo, vui lòng theo dõi và đánh dấu mức độ hài lòng hoặc nhắc nhở đóng vấn đề khi đã đạt được sự đồng thuận.

--Tuân thủ [chính sách cộng đồng](https://docs.github.com/en/site-policy/github-terms/github-community-code-of-conduct) của github--

### [Yêu cầu tính năng (Feature Requests)](https://github.com/freqtrade/freqtrade/labels/enhancement)

Bạn có một ý tưởng hay để cải thiện bot và muốn chia sẻ? Vui lòng
tìm kiếm trước xem tính năng này đã [được thảo luận](https://github.com/freqtrade/freqtrade/labels/enhancement) chưa.
Nếu nó chưa được yêu cầu, vui lòng
[tạo một yêu cầu mới](https://github.com/freqtrade/freqtrade/issues/new/choose)
và đảm bảo bạn tuân theo hướng dẫn mẫu để nó không bị lạc
trong các báo cáo lỗi.

### [Pull Requests](https://github.com/freqtrade/freqtrade/pulls)

Bạn cảm thấy bot còn thiếu một tính năng? Chúng tôi hoan nghênh các pull request của bạn!

Vui lòng đọc
[tài liệu Đóng góp (Contributing)](https://github.com/freqtrade/freqtrade/blob/develop/CONTRIBUTING.md)
để hiểu các yêu cầu trước khi gửi pull request.

Lập trình không phải là điều bắt buộc để đóng góp - có lẽ hãy bắt đầu bằng việc cải thiện tài liệu?
Các vấn đề được gắn nhãn [good first issue](https://github.com/freqtrade/freqtrade/labels/good%20first%20issue) có thể là những đóng góp đầu tiên tốt, và sẽ giúp bạn làm quen với mã nguồn (codebase).

**Lưu ý** trước khi bắt đầu bất kỳ công việc phát triển tính năng lớn mới nào, *vui lòng mở một vấn đề (issue) mô tả những gì bạn dự định làm* hoặc trao đổi với chúng tôi trên [discord](https://discord.gg/p7nuUNVfP7) (vui lòng dùng kênh #dev cho việc này). Điều này sẽ đảm bảo các bên quan tâm có thể đưa ra phản hồi giá trị về tính năng, và cho người khác biết rằng bạn đang làm việc đó.

**Quan trọng:** Luôn tạo PR của bạn nhắm vào nhánh `develop`, không phải `stable`.

## Yêu cầu

### Đồng hồ chính xác

Đồng hồ phải chính xác, được đồng bộ với máy chủ NTP rất thường xuyên để tránh các vấn đề trong giao tiếp với các sàn giao dịch.

### Yêu cầu phần cứng tối thiểu

Để chạy bot này, chúng tôi khuyến nghị bạn dùng một máy chủ đám mây (cloud instance) tối thiểu:

- Yêu cầu hệ thống tối thiểu (khuyến nghị): 2GB RAM, 1GB dung lượng đĩa, 2vCPU

### Yêu cầu phần mềm

- [Python >= 3.11](http://docs.python-guide.org/en/latest/starting/installation/)
- [pip](https://pip.pypa.io/en/stable/installing/)
- [git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
- [TA-Lib](https://ta-lib.github.io/ta-lib-python/)
- [virtualenv](https://virtualenv.pypa.io/en/stable/installation.html) (Khuyến nghị)
- [Docker](https://www.docker.com/products/docker) (Khuyến nghị)
