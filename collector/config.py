"""配置加载：config.yaml + .env（GH_TOKEN），pydantic 校验。

.env 解析注意（错题本 data-semantics #7）：剥掉行内 "# 注释"，避免注释整体变成值。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path("config.yaml")
ENV_FILE = Path(".env")


class SearchConfig(BaseModel):
    request_interval_sec: float = 2.5
    per_page: int = 100
    max_pages: int = 10


class PoolConfig(BaseModel):
    star_buckets: list[str] = Field(
        default_factory=lambda: [
            "stars:>=10000",
            "stars:5000..9999",
            "stars:2000..4999",
            "stars:1000..1999",
        ]
    )
    pool_max: int = 5000
    trending_keep_days: int = 30
    fast_riser_top: int = 500


class TrendingConfig(BaseModel):
    languages: list[str] = Field(
        default_factory=lambda: [
            "", "python", "typescript", "javascript", "go", "rust", "java", "c++",
        ]
    )
    periods: list[str] = Field(default_factory=lambda: ["daily", "weekly", "monthly"])
    request_interval_sec: float = 2.0


class SnapshotConfig(BaseModel):
    engine: str = "rest"
    graphql_switch_threshold: int = 3000


class ReportConfig(BaseModel):
    top_new: int = 50
    top_trending: int = 20
    top_delta: int = 20


class AppConfig(BaseModel):
    since: str = "2026-01-01"
    search: SearchConfig = Field(default_factory=SearchConfig)
    pool: PoolConfig = Field(default_factory=PoolConfig)
    trending: TrendingConfig = Field(default_factory=TrendingConfig)
    snapshot: SnapshotConfig = Field(default_factory=SnapshotConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)


def load_dotenv(path: Path = ENV_FILE) -> None:
    """极简 .env 加载：KEY=VALUE，支持行内注释与成对引号；不覆盖已有环境变量。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip()  # 剥行内注释
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def load_config(path: Path | None = None) -> AppConfig:
    load_dotenv()
    cfg_path = path or DEFAULT_CONFIG_PATH
    data: dict = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def get_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None
