# GitHub Trend Lab

每日自动采集 GitHub 高星项目数据：**2026 新星榜 / 官方 Trending / 自算 star 增速榜**。
完整设计见 [docs/development-plan.md](docs/development-plan.md)。

## 功能

- **新星池**：Search API 星标分桶采集 `created:>=2026-01-01` 的高星仓库（分桶 + 月份二维细分，绕开单查询 1000 条上限）
- **Trending**：抓取官方 trending 页面（总榜 + 7 种语言 × 日/周/月），解析容错、单板块失败不阻塞
- **每日快照**：活跃池（≤5000 仓）全量 star/fork 快照，JSONL 按天落盘进 git，重跑幂等
- **自算增速**：DuckDB 直接查 JSONL 文件算 d1/d7/d30，无需维护数据库
- **自动化**：GitHub Actions 每日 UTC 02:17 采集并自动提交数据，失败自动开 issue
- **日报**：Markdown 日报（新星榜 / Trending / 自算日增）

## 快速开始

### 1. 配置 token

```bash
cp .env.example .env   # 然后编辑 .env 填入 GH_TOKEN
```

PAT 申请：GitHub -> Settings -> Developer settings -> **Fine-grained personal access tokens**，
仅授权本仓库，权限只需 `Contents: Read and write`（供 Actions 提交数据）。
没有 token 时 core API 限额仅 60 次/小时，无法完成全量快照。

### 2. 安装

```bash
python -m venv .venv
# Windows
.venv\Scripts\pip install -e ".[dev]"
# macOS/Linux
.venv/bin/pip install -e ".[dev]"
```

### 3. 运行

```bash
python -m collector run --limit 30   # 冒烟：池 + trending + 前 30 仓快照 + 日报
python -m collector run              # 全量（首次约 10-20 分钟，受 API 限额约束）

# 也可分步执行
python -m collector pool             # 只更新活跃池
python -m collector trending         # 只抓 trending
python -m collector snapshot --limit 50
python -m collector report

# 补采/重跑（幂等，按日期覆盖写）
python -m collector snapshot --date 2026-10-18
```

## 数据布局

```
data/
├── pool.json                      # 活跃池（含入库原因，供审计）
├── snapshots/YYYY-MM-DD.jsonl     # 每日快照，每仓库一行（事实源）
└── trending/YYYY-MM-DD.json       # trending 各板块榜单
reports/YYYY-MM-DD.md               # 每日日报
```

分析时用 DuckDB 直接查 JSONL（无需导入）：

```python
import duckdb
duckdb.sql("""
    SELECT full_name, date, stars,
           stars - lag(stars) OVER (PARTITION BY repo_id ORDER BY date) AS d1
    FROM read_json_auto('data/snapshots/*.jsonl')
    WHERE full_name = 'morluto/rea' ORDER BY date
""").show()
```

## GitHub Actions 自动化

1. 仓库推到 GitHub 后：Settings -> Secrets and variables -> Actions -> 新增 secret `GH_PAT`
2. `.github/workflows/daily.yml` 每日 UTC 02:17 自动运行并提交数据
3. 手动补采：Actions 页面 -> daily-collect -> Run workflow -> 填日期

## 开发

```bash
.venv/Scripts/python -m pytest -q    # 运行测试
```
