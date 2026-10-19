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
