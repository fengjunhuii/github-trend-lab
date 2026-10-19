"""快照采集：对活跃池逐仓更新，计算与上一快照的 d1（日增）。

幂等（docs/development-plan.md §4.5）：
- 输出按日期覆盖写，重跑不产生增量污染
- d1 基准 = latest_snapshot_before(day)，重跑当天自动忽略当天旧文件
- 历史 star 无法回补（API 只给当前值）；补采缺口的插值标记在分析层处理

引擎：
- rest：逐仓 GET /repos/{owner}/{repo}，池 <=3000 时用量安全（<5000/h 的 60%）
- graphql：阶段2实现（100 仓/请求），池超过阈值时此处给出明确告警
- 404（改名/删除）：跳过并记录，条目暂留池中（阶段2加 N 次连续 404 清理）
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from . import storage
from .ghclient import GitHubClient

log = logging.getLogger(__name__)


def to_row(day: date, repo: dict, d1: int | None) -> dict:
    """字段白名单（docs/development-plan.md §2.1），不存 API 原始返回以控制体积。"""
    return {
        "schema_v": storage.SCHEMA_V,
        "date": day.isoformat(),
        "repo_id": repo["id"],
        "full_name": repo["full_name"],
        "description": repo.get("description"),
        "language": repo.get("language"),
        "topics": repo.get("topics") or [],
        "owner_type": (repo.get("owner") or {}).get("type"),
        "created_at": repo.get("created_at"),
        "pushed_at": repo.get("pushed_at"),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "open_issues": repo.get("open_issues_count", 0),
        "d1": d1,
    }


def collect_snapshots(
    client: GitHubClient,
    cfg,
    *,
    day: date | None = None,
    limit: int | None = None,
    pool: dict | None = None,
) -> list[dict]:
    day = day or storage.utc_today()
    pool = pool or storage.load_pool()
    all_entries = pool["repos"]
    entries = all_entries[:limit] if limit else all_entries

    if (
        len(all_entries) > cfg.snapshot.graphql_switch_threshold
        and cfg.snapshot.engine == "rest"
    ):
        log.warning(
            "池规模 %d 超过阈值 %d，REST 单仓请求将逼近限额；建议阶段2切换 graphql 引擎",
            len(all_entries), cfg.snapshot.graphql_switch_threshold,
        )

    prev = storage.latest_snapshot_before(day)
    prev_by_id: dict[int, int] = {}
    prev_by_name: dict[str, dict] = {}
    if prev:
        log.info("d1 基准快照: %s (%d 仓)", prev[0].isoformat(), len(prev[1]))
        for r in prev[1]:
            prev_by_id[r["repo_id"]] = r["stars"]
            prev_by_name[r["full_name"]] = r

    rows: list[dict] = []
    failed: list[tuple[str, int]] = []
    total = len(entries)
    for i, entry in enumerate(entries, 1):
        name = entry["full_name"]
        try:
            repo = client.get_repo(name)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                log.warning("[%d/%d] 仓库不存在(改名/删除?)，跳过: %s", i, total, name)
                failed.append((name, 404))
                continue
            raise
        # repo_id 回填（trending_recent 入池时未知）
        if entry.get("repo_id") is None:
            entry["repo_id"] = repo["id"]
        prev_stars = prev_by_id.get(repo["id"])
        if prev_stars is None:
            alt = prev_by_name.get(name)
            prev_stars = alt["stars"] if alt else None
        d1 = None if prev_stars is None else repo["stargazers_count"] - prev_stars
        rows.append(to_row(day, repo, d1))
        if i % 200 == 0:
            log.info("快照进度 %d/%d (core 已用 %d)", i, total, client.core_used)

    if failed:
        log.warning("失败 %d 个，前 10: %s", len(failed), [n for n, _ in failed[:10]])

    storage.write_snapshots(day, rows)
    storage.save_pool(pool)  # repo_id 回填落盘
    log.info(
        "快照完成: %s %d 仓 -> %s (core=%d search=%d)",
        day.isoformat(), len(rows), storage.snapshot_path(day),
        client.core_used, client.search_used,
    )
    return rows
