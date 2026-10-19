"""抓取 github.com/trending 页面（官方口径榜单）。

URL 形态:
  https://github.com/trending?since=daily
  https://github.com/trending/python?since=weekly

容错设计（docs/development-plan.md T1.4）：
- CSS 选择器集中在 SELECTORS 常量区（页面改版只改一处）
- 每行独立 try/except，单行失败不阻塞整板
- 整板 0 行 -> ParseError，该板块当日留空，不阻塞其他板块
- golden file 回归测试见 tests/test_trending_page.py
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timezone

import httpx
from bs4 import BeautifulSoup
from bs4 import Tag

from . import storage

log = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"

SELECTORS = {
    "row": "article.Box-row",
    "repo_link": "h2 a",
    "description": "p",
    "language": '[itemprop="programmingLanguage"]',
    "stars_total": 'a[href$="/stargazers"]',
    "forks": 'a[href$="/forks"]',
}

PERIOD_STARS_RE = re.compile(r"([\d,]+)\s+stars\s+(today|this week|this month)", re.IGNORECASE)


class ParseError(RuntimeError):
    """页面结构不识别（可能改版）。"""


def board_url(language: str, period: str) -> str:
    base = f"{TRENDING_URL}/{language}" if language else TRENDING_URL
    return f"{base}?since={period}"


def fetch_html(url: str, *, timeout: float = 30.0) -> str:
    resp = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "github-trend-lab/0.1"},
    )
    resp.raise_for_status()
    return resp.text


def _int_text(node: Tag | None) -> int | None:
    if node is None:
        return None
    text = node.get_text(strip=True).replace(",", "")
    return int(text) if text.isdigit() else None


def parse_row(row: Tag) -> dict:
    a = row.select_one(SELECTORS["repo_link"])
    if a is None or not a.get("href"):
        raise ParseError("缺少仓库链接")
    full_name = str(a["href"]).strip("/ ")
    if full_name.count("/") != 1:
        raise ParseError(f"异常仓库路径: {full_name}")

    m = PERIOD_STARS_RE.search(row.get_text(" ", strip=True))
    lang = row.select_one(SELECTORS["language"])
    desc = row.select_one(SELECTORS["description"])
    return {
        "full_name": full_name,
        "description": desc.get_text(strip=True) if desc else None,
        "language": lang.get_text(strip=True) if lang else None,
        "stars_total": _int_text(row.select_one(SELECTORS["stars_total"])),
        "forks": _int_text(row.select_one(SELECTORS["forks"])),
        "stars_period": int(m.group(1).replace(",", "")) if m else None,
    }


def parse_trending_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(SELECTORS["row"])
    if not rows:
        raise ParseError("未找到任何 trending 行（页面结构可能已改版）")
    out: list[dict] = []
    for row in rows:
        try:
            out.append(parse_row(row))
        except Exception as exc:  # noqa: BLE001 单行失败不阻塞整板
            log.warning("trending 行解析失败: %s", exc)
    return out


def collect_trending(cfg, *, day: date | None = None) -> dict:
    """抓取 语言 x 周期 矩阵板块。"""
    day = day or storage.utc_today()
    boards: list[dict] = []
    for lang in cfg.trending.languages:
        for period in cfg.trending.periods:
            url = board_url(lang, period)
            try:
                repos = parse_trending_html(fetch_html(url))
            except Exception as exc:  # noqa: BLE001 板块失败留空，不阻塞
                log.error("板块抓取失败 %s/%s: %s", lang or "all", period, exc)
                repos = []
            boards.append(
                {
                    "language": lang,
                    "period": period,
                    "repos": [{**r, "rank": i} for i, r in enumerate(repos, 1)],
                }
            )
            log.info("trending %s/%s: %d 项", lang or "all", period, len(repos))
            time.sleep(cfg.trending.request_interval_sec)

    return {
        "schema_v": storage.SCHEMA_V,
        "date": day.isoformat(),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "boards": boards,
    }
