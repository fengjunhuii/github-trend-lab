"""新星池采集：Search API 分桶查询，绕开单查询 1000 条上限。

策略（docs/development-plan.md §4.1）：
- 星标分桶查询 created:>=since
- 任一桶 total_count > 990 -> 该桶 x 创建月份 二维细分
- 跨桶/跨月按 repo_id 去重；记录入库原因（new_star / trending_recent / fast_riser）
- 每桶做 expected vs fetched 数量一致性校验（错题本 data-semantics #5：截断不报错）
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import date, timedelta
from collections import Counter

from . import storage
from .ghclient import GitHubClient

log = logging.getLogger(__name__)

BUCKET_SAFE_MAX = 990  # 桶安全上限（API 硬上限 1000，留余量）


class SearchConfigLike:
    """仅为类型提示；实际传入 cfg.search（pydantic）或测试桩。"""

    per_page: int
    max_pages: int


def months_between(start: date, end: date) -> list[tuple[date, date]]:
    """[start, end] 覆盖的逐月区间列表，返回每月 (seg_start, seg_end)。"""
    months: list[tuple[date, date]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last_day = calendar.monthrange(y, m)[1]
        seg_start = max(date(y, m, 1), start)
        seg_end = min(date(y, m, last_day), end)
        months.append((seg_start, seg_end))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def bucket_query(since: str, star_qualifier: str) -> str:
    return f"created:>={since} {star_qualifier}"


def sliced_query(start: date, end: date, star_qualifier: str) -> str:
    return f"created:{start.isoformat()}..{end.isoformat()} {star_qualifier}"


def fetch_new_stars(
    client: GitHubClient,
    *,
    since: str,
    star_buckets: list[str],
    search_cfg: SearchConfigLike,
    today: date | None = None,
) -> list[dict]:
    """返回去重后的新星仓库（API 原始 dict），任一桶超限自动按月细分。"""
    today = today or storage.utc_today()
    months = months_between(date.fromisoformat(since), today)
    seen: dict[int, dict] = {}

    for bucket in star_buckets:
        base_query = bucket_query(since, bucket)
        total = client.search_total_count(base_query)
        plan: list[tuple[str, int]] = [(base_query, total)]
        if total > BUCKET_SAFE_MAX:
            log.warning(
                "桶 %s 超限(total=%d > %d)，启用创建月份二维细分",
                bucket, total, BUCKET_SAFE_MAX,
            )
            plan = []
            for m_start, m_end in months:
                q = sliced_query(m_start, m_end, bucket)
                t = client.search_total_count(q)
                if t > BUCKET_SAFE_MAX:
                    log.error("月度子桶仍超限(%d)，将截断至 API 上限: %s", t, q)
                plan.append((q, t))

        expected = sum(t for _, t in plan)
        fetched = 0
        for q, _ in plan:
            items, _t = client.search_repositories(
                q, per_page=search_cfg.per_page, max_pages=search_cfg.max_pages
            )
            fetched += len(items)
            for repo in items:
                seen.setdefault(repo["id"], repo)
        if fetched != expected:
            log.warning("桶 %s 数量校验不一致: expected=%d fetched=%d", bucket, expected, fetched)
        log.info("桶 %s: total=%d, 去重后累计 %d 仓", bucket, expected, len(seen))

    return list(seen.values())


# ---------- 池维护 ----------


def _score(entry: dict) -> int:
    """保留分：trending > fast_riser > 新星星标。淘汰尾部时用。"""
    reasons = set(entry.get("reasons") or [])
    s = 0
    if "trending_recent" in reasons:
        s += 10**9
    if "fast_riser" in reasons:
        s += 10**8
    meta = entry.get("meta") or {}
    s += int(meta.get("stars") or 0)
    return s


def trim_pool(entries: list[dict], pool_max: int) -> tuple[list[dict], int]:
    if len(entries) <= pool_max:
        return entries, 0
    ranked = sorted(entries, key=_score, reverse=True)
    keep_ids = {id(e) for e in ranked[:pool_max]}
    return [e for e in entries if id(e) in keep_ids], len(entries) - pool_max


def build_pool(client: GitHubClient, cfg, *, day: date | None = None) -> dict:
    """pool = 新星池 ∪ trending_recent(近N天) ∪ fast_riser(上一快照 d1 TopN)。"""
    day = day or storage.utc_today()
    pool = storage.load_pool()
    by_name: dict[str, dict] = {e["full_name"]: e for e in pool["repos"]}

    # 1) 新星池
    raw = fetch_new_stars(
        client,
        since=cfg.since,
        star_buckets=cfg.pool.star_buckets,
        search_cfg=cfg.search,
        today=day,
    )
    new_count = 0
    for repo in raw:
        name = repo["full_name"]
        entry = by_name.get(name)
        if entry is None:
            by_name[name] = {
                "full_name": name,
                "repo_id": repo["id"],
                "reasons": ["new_star"],
                "first_seen": day.isoformat(),
                "meta": {
                    "language": repo.get("language"),
                    "description": repo.get("description"),
                    "created_at": repo.get("created_at"),
                    "stars": repo.get("stargazers_count"),
                },
            }
            new_count += 1
        else:
            reasons = entry.setdefault("reasons", [])
            if "new_star" not in reasons:
                reasons.append("new_star")
            if entry.get("repo_id") is None:
                entry["repo_id"] = repo["id"]

    # 2) trending_recent：近 N 天 trending 出现过的仓库
    keep_from = day - timedelta(days=cfg.pool.trending_keep_days)
    trend_names: set[str] = set()
    if storage.TREND_DIR.exists():
        for path in sorted(storage.TREND_DIR.glob("*.json")):
            try:
                file_day = storage.parse_date(path.stem)
            except ValueError:
                continue
            if not (keep_from <= file_day <= day):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("trending 文件损坏，跳过: %s", path)
                continue
            for board in data.get("boards", []):
                for r in board.get("repos", []):
                    trend_names.add(r["full_name"])
    trend_added = 0
    for name in sorted(trend_names):
        if name not in by_name:
            by_name[name] = {
                "full_name": name,
                "repo_id": None,
                "reasons": ["trending_recent"],
                "first_seen": day.isoformat(),
                "meta": {},
            }
            trend_added += 1

    # 3) fast_riser：上一快照日增 Top N（捕捉低星快升项目）
    riser_added = 0
    prev = storage.latest_snapshot_before(day)
    if prev:
        _, rows = prev
        top = sorted(rows, key=lambda r: r.get("d1") or 0, reverse=True)
        top = top[: cfg.pool.fast_riser_top]
        for r in top:
            if (r.get("d1") or 0) <= 0:
                continue
            entry = by_name.get(r["full_name"])
            if entry is None:
                by_name[r["full_name"]] = {
                    "full_name": r["full_name"],
                    "repo_id": r["repo_id"],
                    "reasons": ["fast_riser"],
                    "first_seen": day.isoformat(),
                    "meta": {"language": r.get("language"), "stars": r.get("stars")},
                }
                riser_added += 1
            else:
                reasons = entry.setdefault("reasons", [])
                if "fast_riser" not in reasons:
                    reasons.append("fast_riser")

    entries, dropped = trim_pool(list(by_name.values()), cfg.pool.pool_max)
    if dropped:
        log.warning("池超上限(%d)，按保留分淘汰 %d 个低分条目", cfg.pool.pool_max, dropped)

    pool["repos"] = sorted(entries, key=lambda e: e["full_name"])
    pool["stats"] = {
        "total": len(pool["repos"]),
        "by_first_reason": dict(Counter((e.get("reasons") or ["?"])[0] for e in pool["repos"])),
        "dropped": dropped,
    }
    storage.save_pool(pool)
    log.info(
        "池构建完成: 共 %d 仓 (新增 new_star=%d trending=%d fast_riser=%d)",
        len(pool["repos"]), new_count, trend_added, riser_added,
    )
    return pool
