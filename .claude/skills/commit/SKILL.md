---
name: commit
description: Commit changes in this repo following the project git rules — commit on develop, ruff, related tests, secret scan, English Conventional Commit message without any Claude/AI attribution. Never pushes. Use after finishing a verified change or whenever the user asks to commit.
argument-hint: "[gợi ý nội dung commit]"
---

# /commit — commit đúng luật dự án

Luật đầy đủ: `.claude/rules/git-workflow.md`. Làm lần lượt, dừng lại báo người dùng nếu một bước thất bại.

## 1. Kiểm tra branch
- `git branch --show-current` phải là `develop`. Nếu đang ở `main`: dừng, báo người dùng. Không tạo branch mới.

## 2. Xem thay đổi
- `git status --short` và `git diff` (cả staged lẫn unstaged).
- Nếu có nhiều thay đổi không liên quan nhau → đề xuất tách thành nhiều commit.
- Không bao giờ thêm: `user_data/config*.json`, `*.sqlite*`, dữ liệu trong `user_data/data/`, `user_data/backtest_results/`, `user_data/reports/`, `.venv/`.

## 3. Kiểm tra chất lượng
- File `.py` đã đổi: `.venv/Scripts/ruff.exe check <files>` và `.venv/Scripts/ruff.exe format --check <files>`.
- Có đổi trong `freqtrade/`: chạy `.venv/Scripts/python.exe -m pytest tests/<thư mục tương ứng> -q -x`.
- Có đổi strategy trong `user_data/strategies/`: nhắc người dùng chạy `/validate-strategy` nếu chưa chạy cho thay đổi này.

## 4. Quét secret trên phần sắp commit
```bash
git diff --cached | grep -inE "api[_-]?key|secret|token|password|jwt_secret|ws_token|[0-9]{8,10}:[A-Za-z0-9_-]{30,}"
```
Có kết quả đáng ngờ → dừng, chỉ cho người dùng dòng đó.

## 5. Viết message (tiếng Anh, Conventional Commits)
```
<type>(<scope>): <summary in present tense, ≤ 72 chars>

<body: what the code does and why, wrapped at 72 chars>
```
- `type`: feat | fix | refactor | perf | test | docs | chore | ci | trim
- `scope`: strategy | scanner | signal | telegram | config | core | deploy | docs | tools
- **Tuyệt đối không** thêm `Co-Authored-By: Claude...` hay bất kỳ dòng ghi công AI/công cụ nào.
- Dùng gợi ý trong `$ARGUMENTS` (nếu có) làm định hướng nội dung.

## 6. Commit
- `git add <các file cụ thể>` (không dùng `git add -A` khi còn file lạ), rồi `git commit -F -` với message ở bước 5.
- In ra `git log -1 --stat`.

## 7. Không push
- Agent **không** push và không tạo PR. Người dùng tự push `develop` và tạo PR `develop` → `main`.
- Cuối cùng báo: số commit `develop` đang đi trước `origin/develop` (`git rev-list --count origin/develop..develop`).
- Repo là fork công khai: nhắc người dùng nếu commit chứa strategy/config riêng.
