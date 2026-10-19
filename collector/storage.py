"""JSONL/JSON 数据文件读写（唯一持久层）。

目录约定（docs/development-plan.md §7）：
  data/pool.json                     活跃池
  data/snapshots/YYYY-MM-DD.jsonl    每日快照（每仓库一行，覆盖写 = 幂等）
  data/trending/YYYY-MM-DD.json      每日 trending 各板块

纪律：所有写操作先 mkdir(parents=True)；所有 read/write 显式 encoding="utf-8"
（错题本 windows-env #6 / toolchain #20）。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
SNAP_DIR = DATA_DIR / "snapshots"
TREND_DIR = DATA_DIR / "trending"
POOL_FILE = DATA_DIR / "pool.json"

SCHEMA_V = 1


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def parse_date(s: str) -> date:
    return date.fromisoformat(s)


# ---------- 活跃池 ----------


def load_pool() -> dict:
    if POOL_FILE.exists():
        return json.loads(POOL_FILE.read_text(encoding="utf-8"))
    return {"schema_v": SCHEMA_V, "updated_at": None, "stats": {}, "repos": []}


def save_pool(pool: dict) -> None:
    POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    pool["schema_v"] = SCHEMA_V
    pool["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    POOL_FILE.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 每日快照 ----------


def snapshot_path(day: date) -> Path:
    return SNAP_DIR / f"{day.isoformat()}.jsonl"


def write_snapshots(day: date, rows: list[dict]) -> Path:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(day)
    lines = [json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows]
    # newline="\n": 统一 LF；每行由 json.dumps 保证单行（控制字符均已转义）
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    return path


def read_snapshots(day: date) -> list[dict]:
    path = snapshot_path(day)
    if not path.exists():
        return []
    # 严格按物理 \n 切分，禁止 splitlines()：description 可能含 U+2028/U+2029/U+0085
    # （JSON 合法、json.dumps 不转义），splitlines() 会把它们当换行把一行 JSON 切成
    # 两半导致 "Unterminated string"（2026-10-19 实踩，见 docs/dev-notes.md）。
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]


def latest_snapshot_before(day: date) -> tuple[date, list[dict]] | None:
    """严格早于 day 的最近一次快照；重跑当天时会自动忽略当天文件（幂等关键）。"""
    if not SNAP_DIR.exists():
        return None
    dates: list[date] = []
    for p in SNAP_DIR.glob("*.jsonl"):
        try:
            d = parse_date(p.stem)
        except ValueError:
            continue
        if d < day:
            dates.append(d)
    if not dates:
        return None
    prev = max(dates)
    return prev, read_snapshots(prev)


# ---------- trending ----------


def trending_path(day: date) -> Path:
    return TREND_DIR / f"{day.isoformat()}.json"


def write_trending(day: date, data: dict) -> Path:
    TREND_DIR.mkdir(parents=True, exist_ok=True)
    path = trending_path(day)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_trending(day: date) -> dict | None:
    path = trending_path(day)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
