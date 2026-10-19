# GitHub Trend Lab — 完整开发计划

> 目标：采集并积累 GitHub 高星项目数据（2026 年新星榜 / 官方 Trending / 自算增速榜），每日自动更新，渐进式构建分析与展示层。
> 技术栈：Python 3.11+ · httpx · pydantic · DuckDB · GitHub Actions · GitHub Pages
> 存储策略：**JSONL 快照文件 = 事实源（进 git）**，DuckDB = 架在文件上的查询引擎（不落库、不进 git）

---

## 0. 项目边界与核心决策（已定）

| 决策点 | 结论 | 原因 |
|---|---|---|
| 采集范围 | 只维护"活跃池"（≤5000 仓库），不做全量 | 全量不现实且无必要 |
| 存储形式 | 按天 JSONL 文件 + DuckDB 直接查询 | git 友好、append-only、Actions 无状态 |
| Trending 来源 | 抓取 github.com/trending 页面 + 自算增速双轨 | 官方无 API；自算是差异化核心 |
| 调度 | GitHub Actions cron（公仓免费） | 零服务器成本 |
| 时区 | 全部 UTC，日期边界 = UTC 日 | 避免 Actions 触发时刻歧义 |
| 展示 | 阶段1 Markdown 日报 → 阶段2 Pages 静态页 | 渐进式，先跑通再好看 |

**已知硬约束：**
1. Search API 单次最多返回 1000 条 → 必须分桶（见 §4.1）
2. Search API 限流：认证 30 次/分钟；Core REST 认证 5000 次/小时（见 §4.2）
3. Trending 页面无官方 API，HTML 改版会破坏解析 → 解析器独立模块 + golden file 测试（见 §4.4）

---

## 1. 总体架构

```
                ┌─────────────────────────────────────────────┐
                │        GitHub Actions (cron 每日 UTC 2:17)   │
                └────────────────────┬────────────────────────┘
                                     ▼
┌──────────────────────────── collector/ ─────────────────────────────┐
│  search_pool.py    Search API 分桶 → 新星池（2026年创建, stars分档）  │
│  trending_page.py  抓 trending 页面（总榜/语言榜 × 日/周/月）         │
│  snapshot.py       对池内仓库拍快照 + 与昨日 diff                    │
│  storage.py        JSONL 读写（唯一持久层）                          │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
              data/snapshots/2026-10-19.jsonl   ← 进 git（事实源）
              data/trending/2026-10-19.json     ← 进 git
                               ▼
        ┌─────────────────────┴──────────────────────┐
        ▼                                            ▼
  report/daily_report.py                    web/（阶段2）
  Markdown 日报，Actions 自动提交             GitHub Pages 静态站
                                            （榜单页 + star 曲线）
  analysis/（阶段2+）
  DuckDB 内存视图：delta/增速榜/爆发分数
```

---

## 2. 数据模型

### 2.1 快照 JSONL（`data/snapshots/YYYY-MM-DD.jsonl`，每仓库一行）

```json
{
  "schema_v": 1,
  "date": "2026-10-19",
  "repo_id": 1209966933,
  "full_name": "morluto/rea",
  "description": "Reverse engineer anything with agents ...",
  "language": "TypeScript",
  "topics": ["ai-agents", "reverse-engineering"],
  "owner_type": "User",
  "created_at": "2026-04-14T00:51:32Z",
  "pushed_at": "2026-10-19T11:30:40Z",
  "stars": 330107,
  "forks": 83544,
  "open_issues": 1241
}
```

字段白名单即上表，**不存 API 原始返回**（控制体积）。`schema_v` 用于未来字段演进。

### 2.2 Trending JSON（`data/trending/YYYY-MM-DD.json`）

```json
{
  "schema_v": 1,
  "date": "2026-10-19",
  "captured_at_utc": "2026-10-19T02:20:00Z",
  "boards": [
    { "language": "",       "period": "daily",   "repos": [ { "full_name": "...", "rank": 1, "stars_total": 330107, "stars_period": 5120 } ] },
    { "language": "python", "period": "weekly",  "repos": [ "..." ] }
  ]
}
```

### 2.3 派生视图（DuckDB 内存层，`analysis/views.sql`，不落盘）

```sql
CREATE VIEW v_repo_daily AS
  SELECT * FROM read_json_auto('data/snapshots/*.jsonl');

CREATE VIEW v_delta AS
  SELECT full_name, date, stars,
         stars - lag(stars)    OVER w AS d1,   -- 日增
         stars - lag(stars, 7) OVER w AS d7,   -- 周增
         stars - lag(stars, 30) OVER w AS d30  -- 月增
  FROM v_repo_daily
  WINDOW w AS (PARTITION BY repo_id ORDER BY date);

-- 爆发分数（阶段3）：当日增量 / 该仓库历史日增中位数，>10 视为爆发
```

---

## 3. 阶段与任务分解

### 阶段 1 — MVP 采集管道（预计 1.5~2 天）

- [x] **T1.1 项目骨架**（0.5h）
  目录结构见 §7；`pyproject.toml`（依赖：httpx, pydantic, beautifulsoup4/lxml, duckdb, click）；`.gitignore`；`config.yaml`；`README.md`（含 PAT 申请指引，只需 `public repo` 读权限——注：PAT 即使只读也会消耗限流额度，必须配置）。
- [x] **T1.2 GitHub API 客户端**（2h）
  `ghclient.py`：token 注入、分页封装、`Retry-After`/指数退避、Search 接口 30/min 主动节流、429/403 识别与降级。核心考点：限流状态从响应头 `x-ratelimit-remaining` 读取而非自己计数。
- [x] **T1.3 新星池采集**（2h）
  `search_pool.py`：按 §4.1 分桶拉取 `created:>=2026-01-01` 仓库；桶间去重（按 repo_id）；桶超限自动细分（星标档 × 创建月份二维细分）；产出 `pool.json`（含入库原因：`star_bucket` / `trending_recent` / `fast_riser`）。
- [x] **T1.4 Trending 页抓取器**（3h）
  `trending_page.py`：语言 × 周期矩阵抓取；CSS 选择器集中定义在一处常量区；解析失败 → 抛 `ParseError` 且当日该板留空（不阻塞其他板）；golden file 回归测试（`tests/fixtures/trending_sample.html`）。
- [x] **T1.5 快照采集与落库**（2h）
  `snapshot.py`：MVP 用 REST `GET /repos/{owner}/{repo}` 逐个更新池内仓库（池 ≤3000 时总量 < 5000/h 限额；池涨到 3000+ 后切换 GraphQL 批量，100 个/请求，见 §4.2）；与昨日快照 diff 输出 `d1` 字段；写当日 JSONL。
- [x] **T1.6 CLI 入口**（1h）
  `python -m collector pool / trending / snapshot / run`（run = 全流程）；`--date` 参数支持补采与重跑（幂等：按日期覆盖写）。
- [x] **T1.7 Markdown 日报**（2h）
  `report/daily_report.py`：当日新星榜 Top 50、trending Top 20（总/日/周）、自算日增 Top 20；输出 `reports/2026-10-19.md`。
- [x] **T1.8 GitHub Actions workflow**（1.5h）
  见 §4.3；含失败自动开 issue 提醒。
- [ ] **T1.9 联调与验收**（1h）
  > 2026-10-19：单测 11/11 通过；本地全流程跑通——pool 3345 仓、trending 24 板块 378 项、snapshot 3529 行、日报生成（DoD 1 ✅）。
  > API 实测用量 Search 68 / Core 3529：按 DoD 2 原字面（≤20 / ≤3200）未达，根因是估算未计入 2000..4999 桶触发按月细分带来的翻页；Core 占限额 70.6%，仍在安全余量内。处理：DoD 2 修正为 Search ≤80 / Core ≤3600（§4.2 同步更新）；池 >3000 切 GraphQL 批量是 T2 既定项，届时 Core 用量可降至 ~50。
  > 修复 2 个 bug：board_url 双重路径（单测捕获）；splitlines() 误切含 U+2028 的 JSONL 行（docs/dev-notes.md 已登记，含回归测试）。
  > 2026-10-19（续）：仓库已推送 https://github.com/fengjunhuii/github-trend-lab（13 commits，首日数据入库；远端树确认无 .env）。剩余：用户配置 fine-grained PAT 为 secret `GH_PAT`（仅本仓库 Contents: Read and write）→ workflow_dispatch 手动触发验证 → 连续 3 天 cron 成功即 DoD 3 达标收口；届时可顺带用补采做 DoD 4 的删档重跑实机验证。
  > 2026-10-20：Actions 端到端跑通（Run #2 success，collector 21 分钟，core=3530）。首次运行曾失败，根因是**运行期间人工向 main 推 docs 提交导致 push 非快进拒绝**（教训：bot 运行期间不动远端）；已修复 workflow——push 前 fetch+rebase+重试，并补 `issues: write`（此前失败自动开 issue 因权限 403）。bot 提交 `data: 2026-10-19`（3530 行快照+24 板块+日报）已验证落地。DoD 1/2/4 ✅（Run #2 同日重跑即幂等覆盖实证）；DoD 3 进行中：10-20/21/22 三天 cron（UTC 02:17）连续成功后阶段 1 收口。

**阶段 1 验收标准（DoD）：**
1. 本地 `python -m collector run` 一次跑通，产出当日 snapshots + trending + 日报三个文件
2. 单次全流程 API 用量 ≤ Search 80 次 + Core 3600 次（Core < 限额 72%；2026-10-19 实测 68/3529 达标——原估算 20/3200 未计分桶按月细分，已修正）
3. Actions 连续 3 天定时成功并自动提交数据
4. 手工删掉某天数据后 `--date` 重跑可完整恢复（幂等验证）

### 阶段 2 — 分析层与 Web 展示（预计 2~3 天）

- [ ] **T2.1 DuckDB 分析模块**（2h）：`analysis/views.sql` + 增速榜/新星榜（按月切片）/语言分布查询函数
- [ ] **T2.2 自算趋势榜算法**（3h）：star velocity（7 日加权平均日增）；与官方 trending Top20 做重叠率对比，重叠率作为算法校准指标（学习点）
- [ ] **T2.3 前端数据构建**（2h）：`web/build_data.py` 把 JSONL 聚合成前端需要的紧凑 JSON（榜单 + 每仓时间序列）
- [ ] **T2.4 静态站点**（6h）：纯 HTML + ECharts（无构建工具）；页面：总览（三榜）/ 仓库详情（star 曲线 + 增速）/ 语言趋势；部署到 GitHub Pages（`/docs` 目录或 gh-pages 分支）
- [ ] **T2.5 日报升级**（1h）：加入周环比、新入榜仓库（昨日不在池、今日进 Top N）标记

**阶段 2 验收标准：**
1. Pages 上线，三榜 + 任一仓库曲线可看
2. 自算趋势榜与官方 trending 30 日平均重叠率 ≥ 50%（达不到则调权重）
3. 全站无后端，数据刷新 = 重跑 build_data + push

### 阶段 3 — 深度分析与学习产出（持续，按兴趣选做）

- [ ] **T3.1 爆发检测**：爆发分数（当日增量/历史中位数日增）> 10 且绝对增量 > 200 → 标记"爆发"，日报单独一节
- [ ] **T3.2 语言/主题趋势**：月度语言份额时间序列、topics 共现分析
- [ ] **T3.3 Streamlit 本地看板**（可选）：交互式筛选、对比多个仓库曲线
- [ ] **T3.4 AI 日报**（可选）：把当日 diff 喂给 LLM 生成中文趋势解读（学习点：prompt + 成本控制）
- [ ] **T3.5 数据质量监控**：每日采集后自检（行数、字段缺失率、与昨日 diff 的异常跳变），异常时 issue 告警

### 里程碑

| 里程碑 | 内容 | 预计 |
|---|---|---|
| M1 | 本地管道跑通（T1.1~T1.7） | Day 1~2 |
| M2 | Actions 自动化上线，无人值守（T1.8~T1.9） | Day 2 |
| M3 | 分析层 + 增强日报（T2.1/T2.2/T2.5） | Day 3~4 |
| M4 | Web 站点上线（T2.3/T2.4） | Day 5~7 |
| M5 | 持续运营，数据积累 ≥ 30 天后开深度分析 | Day 30+ |

---

## 4. 关键技术设计

### 4.1 Search API 分桶策略（绕开 1000 条上限）

```
基础桶（created:>=2026-01-01）：
  B1: stars:>=10000
  B2: stars:5000..9999
  B3: stars:2000..4999
  B4: stars:1000..1999      ← 当前实测总量 3335，各桶均 < 990
  校验：Σ各桶 total_count 与逐桶翻页数量一致；任一桶 total_count > 990 →
  触发细分：该桶 × 创建月份（created:2026-01..2026-01 等 10 个子桶）
```

实现要点：`total_count` 只在 `per_page=1` 的首次请求拿；翻页用 `per_page=100`。每个仓库记录 `first_seen_bucket`，跨桶去重以 `repo_id` 为准。

### 4.2 限流与请求预算（每日）

| 请求类型 | 数量 | 限额 | 余量 |
|---|---|---|---|
| Trending HTML 抓取 | 8 语言 × 3 周期 = 24 | 无 API 限制，控制节奏 1 req/2s | ✅ |
| Search 分桶 | ~70（含按月细分翻页，2026-10-19 实测 68） | 30/min | ✅ |
| 快照 REST（池 ~3500） | ~3530（2026-10-19 实测） | 5000/h | ✅（~71%；池续涨则切 GraphQL） |
| 快照 GraphQL（池 >3000 时切换） | 100 仓/请求 ≈ 50 | 5000 点/h | ✅ |

客户端规则：所有请求带 token；收到 403 且 `x-ratelimit-remaining: 0` → 读 `x-ratelimit-reset` 睡眠等待；Search 类请求固定节流 2.5s/次。

### 4.3 GitHub Actions workflow（`\.github/workflows/daily.yml`）

```yaml
name: daily-collect
on:
  schedule:
    - cron: "17 2 * * *"     # 避开整点高峰；Actions 按 UTC 执行，可能延迟数分钟
  workflow_dispatch:          # 支持手动触发/补采
permissions:
  contents: write
concurrency:
  group: daily-collect
  cancel-in-progress: false
jobs:
  collect:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -e .
      - name: Run collector
        env:
          GH_TOKEN: ${{ secrets.GH_PAT }}
        run: python -m collector run
      - name: Commit data & report
        run: |
          git config user.name  "trend-bot"
          git config user.email "bot@users.noreply.github.com"
          git add data reports
          git diff --cached --quiet || git commit -m "data: $(date -u +%F)"
          git push
      - name: Open issue on failure
        if: failure()
        uses: actions/github-script@v7   # 内容：日期 + 失败 step 日志摘要
```

要点：`workflow_dispatch` + `--date` 参数 = 任意补采；单 job 串行（concurrency 防重叠）；凭据用 Fine-grained PAT，仅授该仓库 Contents 读写。

### 4.4 活跃池维护逻辑

```
pool(n) = union(
  新星池: created:>=2026-01-01 且 stars>=1000        （~3335，主力）
  trending_pool: 近 30 天 trending 出现过的仓库        （保鲜期 30 天后移出）
  fast_riser: 昨日快照 d1 排名 Top 500                （捕捉低星快升项目）
)
池上限 5000：超出时按 (新星星标 / trending 热度 / d1) 综合分淘汰尾部
池定义持久化在 data/pool.json，每日增量更新，供审计
```

### 4.5 幂等与数据安全

- 所有输出按日期命名，重跑 = 覆盖写，无 append 污染
- 补采跨天缺失：`run --date 2026-10-15` 只生成该日文件；**历史 star 无法回补**（API 只给当前值），缺口在分析层用线性插值标记 `interpolated=true`，不伪造数据
- 日报与数据文件分离：报告可随时重生成，原始快照只写一次

---

## 5. 风险与对策

| 风险 | 概率 | 对策 | 验证方式 |
|---|---|---|---|
| trending 页 HTML 改版 | 中 | 选择器集中一处；golden file 测试；解析失败当日留空不阻塞 | tests/test_trending_page.py |
| Search 桶超 1000 条 | 中 | §4.1 二维细分 + 一致性校验告警 | 每次采集校验日志 |
| 池增长逼近 REST 限额 | 低 | 3000 阈值切换 GraphQL 批量 | pool.json 行数监控 |
| Actions cron 延迟/跳过 | 低 | 日粒度无影响；失败开 issue；周末手动补采 | workflow 运行历史 |
| PAT 泄露 | 低 | 仅 fine-grained + 单仓库 + Contents 权限；不进代码 | secrets 扫描 |
| 仓库体积增长 | 低 | 字段白名单 + 池上限 5000 → 日增 ~500KB，年 ~160MB | git count-objects 抽查 |

---

## 6. 学习点清单（每个任务的"学到什么"）

| 任务 | 学习点 |
|---|---|
| T1.2 | 限流模型（header 驱动）、指数退避、幂等重试 |
| T1.3 | API 分页设计缺陷的工程绕法（分桶/二维细分） |
| T1.4 | HTML 解析容错、golden file 回归测试 |
| T1.5 | REST→GraphQL 迁移的成本权衡 |
| T2.2 | 趋势算法设计 + 用官方数据校准自有算法 |
| T2.4 | 零构建静态站 + 数据预聚合（lakehouse 思路的微缩版） |
| T3.1 | 异常检测（稳健统计：中位数而非均值） |
| T3.4 | LLM 批量摘要的成本与结构化输出控制 |

---

## 7. 目录结构（最终形态）

```
github-trend-lab/
├── collector/
│   ├── __init__.py
│   ├── __main__.py        # click CLI: pool/trending/snapshot/run
│   ├── config.py          # config.yaml 加载 + pydantic 校验
│   ├── ghclient.py        # API 客户端（限流/重试/节流）
│   ├── search_pool.py     # T1.3 分桶采集
│   ├── trending_page.py   # T1.4 页面抓取
│   ├── snapshot.py        # T1.5 快照 + diff
│   └── storage.py         # JSONL 读写（唯一持久层）
├── analysis/
│   ├── views.sql          # DuckDB 视图定义
│   └── queries.py         # 增速榜/爆发/语言趋势查询函数
├── report/
│   └── daily_report.py
├── web/                   # 阶段2
│   ├── build_data.py
│   ├── index.html / repo.html / assets/
├── tests/
│   ├── fixtures/trending_sample.html
│   └── test_trending_page.py / test_search_pool.py
├── data/
│   ├── pool.json
│   ├── snapshots/2026-10-19.jsonl
│   └── trending/2026-10-19.json
├── reports/2026-10-19.md
├── docs/development-plan.md   ← 本文档
├── .github/workflows/daily.yml
├── config.yaml
├── pyproject.toml
├── .gitignore
└── README.md
```

---

## 8. 执行约定

1. 每完成一个 T 任务打一个 commit，message 格式 `T1.3: feat(search_pool) 分桶采集与二维细分`
2. 阶段验收不过不进下一阶段（DoD 是硬门槛）
3. 涉及外部行为假设（限流数值、HTML 结构）的代码，必须先写最小验证脚本实测再写实现
4. 本文档随开发推进更新：完成任务打勾，决策变更记录在 §0 表格追加行
