"""生成 Markdown 日报：年度新星榜 / Trending / 自算日增。"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from collector import storage

log = logging.getLogger(__name__)

REPORT_DIR = Path("reports")


def _md_link(name: str) -> str:
    return f"[{name}](https://github.com/{name})"


def _table(headers: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "_（无数据）_"
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
    return "\n".join([head, sep, body])


def _fmt_d1(d1) -> str:
    return "-" if d1 is None else f"+{d1}" if d1 >= 0 else str(d1)


def generate_report(cfg, *, day: date | None = None) -> Path:
    day = day or storage.utc_today()
    rows = storage.read_snapshots(day)
    trend = storage.read_trending(day)
    since = date.fromisoformat(cfg.since)

    lines: list[str] = [f"# GitHub Trend Lab 日报 - {day.isoformat()}", ""]

    # ---- 年度新星榜 ----
    new_stars = [
        r for r in rows
        if r.get("created_at") and _safe_date(r["created_at"][:10]) and _safe_date(r["created_at"][:10]) >= since
    ]
    new_stars.sort(key=lambda r: r["stars"], reverse=True)
    top_new = new_stars[: cfg.report.top_new]
    lines += [
        f"## 年度新星榜 Top {len(top_new)}（created >= {cfg.since}）",
        "",
        _table(
            ["#", "仓库", "Stars", "日增", "语言", "创建日期"],
            [
                (i, _md_link(r["full_name"]), r["stars"], _fmt_d1(r.get("d1")),
                 r.get("language") or "-", r["created_at"][:10])
                for i, r in enumerate(top_new, 1)
            ],
        ),
        "",
    ]

    # ---- Trending（总榜 daily）----
    board = None
    if trend:
        board = next(
            (b for b in trend.get("boards", []) if b["language"] == "" and b["period"] == "daily"),
            None,
        )
    if board:
        top_t = board["repos"][: cfg.report.top_trending]
        lines += [
            f"## Trending（总榜 / daily）Top {len(top_t)}",
            "",
            _table(
                ["#", "仓库", "总 Stars", "今日 Star"],
                [
                    (r["rank"], _md_link(r["full_name"]), r.get("stars_total") or "-",
                     r.get("stars_period") or "-")
                    for r in top_t
                ],
            ),
            "",
        ]

    # ---- 自算日增 ----
    risers = sorted((r for r in rows if r.get("d1") is not None), key=lambda r: r["d1"], reverse=True)
    top_d = risers[: cfg.report.top_delta]
    lines += [
        f"## 自算日增 Top {len(top_d)}",
        "",
        _table(
            ["#", "仓库", "Stars", "日增", "语言"],
            [
                (i, _md_link(r["full_name"]), r["stars"], _fmt_d1(r["d1"]),
                 r.get("language") or "-")
                for i, r in enumerate(top_d, 1)
            ],
        ),
        "",
    ]

    # ---- 汇总 ----
    pool = storage.load_pool()
    stats = pool.get("stats", {})
    lines += [
        "## 汇总",
        "",
        f"- 快照仓库数: {len(rows)}",
        f"- 活跃池规模: {stats.get('total', len(pool.get('repos', [])))}"
        f"（{stats.get('by_first_reason', {})}）",
        f"- 池内新星（created >= {cfg.since}）: {len(new_stars)}",
        f"- 生成时间(UTC): {storage.utc_today().isoformat()}",
        "",
    ]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{day.isoformat()}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    log.info("日报已生成: %s", out)
    return out


def _safe_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None
