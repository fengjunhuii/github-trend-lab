# 开发笔记 / 阶段踩坑登记

> 新坑按 known-pitfalls 模板登记于此，项目收口时由 dev-retro 判定是否回写错题本。

### 坑：splitlines() 切开含 U+2028 的 JSONL 行，读回报 "Unterminated string"
- 现象：快照写盘成功，report 读回时 `json.loads` 报 `Unterminated string starting at char 108`，进程退出码 1
- 环境：Windows 10 / Python 3.12.10 / github-trend-lab 首次全量采集（3529 仓中 1 行命中）
- 根因：仓库 description 含 U+2028（LINE SEPARATOR）。JSON 规范只要求转义 U+0000-U+001F，
  `json.dumps` 对 U+2028/U+2029/U+0085 按原样输出；而 Python `str.splitlines()` 把这三个
  （以及 \x0b\x0c\x1c-\x1e）都当换行，一行合法 JSON 被切成两半。注意：`open()` 文本模式的
  universal newlines 只翻译 \r\n 与 \r，**不**翻译 U+2028，所以坑在 splitlines() 不在读取模式。
- 解法：读侧严格 `text.split("\n")` 替代 `splitlines()`；写侧 `write_text(..., newline="\n")`
  统一 LF。回归测试 `test_snapshot_roundtrip_with_unicode_line_separator`。
- 损失：约 20 分钟（快照 30 分钟跑完后挂在 report，需人工甄别数据完整性）
- 同类次数：第 1 次
- 关联：known-pitfalls windows-env #6（中文乱码）同属"文本 IO 隐式转换"家族，但本条是
  切分语义坑，非编码坑。

### 坑：git credential fill 无 username 提示时挂起 30s+（GCM 多账号弹选择窗）
- 现象：`git credential fill`（stdin 只有 protocol/host）不返回、不报错，挂到超时；
  前一天同样命令秒回
- 环境：Windows 10 / Git for Windows + Git Credential Manager / 本机存了两个
  github.com 账号（`fengjunhuii` 与 `x-access-token`，`git credential-manager github list` 可见）
- 根因：同 host 多账号且查询未带 username 时，GCM 无法消歧，弹出账号选择 GUI
  等待用户点击；无人值守的脚本会一直等。前一天成功是因为当时视为单账号场景
- 解法：credential 查询补 `username=<账号>` 行；remote URL 内嵌用户名
  `https://fengjunhuii@github.com/...` 让后续 push 走同一账号；脚本再加
  `GIT_TERMINAL_PROMPT=0` 快速失败
- 损失：约 10 分钟（建仓 push 流程中断排查）
- 同类次数：第 1 次（属 windows-env「子进程/凭据交互」家族）
- 追加（同日）：带 username 提示连续多次调用后 GCM 仍会偶发挂起（30s timeout 都杀不干净）；
  **无人值守脚本不要反复走 GCM**——API 场景直接读 .env 里的 GH_TOKEN（本会话初始就来自 GCM）。
  注意 .env 是 CRLF：`sed` 提取的值带尾部 \r，必须 `tr -d '\r'` 再拼 Authorization 头，
  否则 401（同 toolchain #30「scp 传 .env 被 \r 污染」家族）。GCM 只保留给 git push 用。
