"""CLI 入口：python -m collector <pool|trending|snapshot|report|run>

所有命令支持 --date 补采/重跑（幂等，按日期覆盖写）。
"""

from __future__ import annotations

import logging
from datetime import date

import click

from . import storage
from .config import get_token, load_config


@click.group()
@click.option("--config", "config_path", type=click.Path(), default=None)
@click.option("-v", "--verbose", is_flag=True, help="DEBUG 日志")
def cli(config_path: str | None, verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _client(cfg):
    from .ghclient import GitHubClient

    return GitHubClient(
        get_token(),
        search_interval=cfg.search.request_interval_sec,
    )


def _day(value: str | None) -> date:
    return date.fromisoformat(value) if value else storage.utc_today()


@cli.command()
def pool() -> None:
    """采集新星池（Search API 分桶 + trending/fast_riser 并集）。"""
    from .search_pool import build_pool

    cfg = load_config()
    with _client(cfg) as client:
        p = build_pool(client, cfg)
    click.echo(f"pool: {p['stats']['total']} repos -> data/pool.json  {p['stats']['by_first_reason']}")


@cli.command()
@click.option("--date", "date_str", default=None, help="UTC 日期 YYYY-MM-DD")
def trending(date_str: str | None) -> None:
    """抓取 trending 页面（官方口径）。"""
    from .trending_page import collect_trending

    cfg = load_config()
    day = _day(date_str)
    data = collect_trending(cfg, day=day)
    path = storage.write_trending(day, data)
    n = sum(len(b["repos"]) for b in data["boards"])
    click.echo(f"trending: {n} items -> {path}")


@cli.command()
@click.option("--date", "date_str", default=None, help="UTC 日期 YYYY-MM-DD（补采/重跑）")
@click.option("--limit", type=int, default=None, help="只快照池前 N 个仓库（调试用）")
def snapshot(date_str: str | None, limit: int | None) -> None:
    """对活跃池拍快照并写当日 JSONL。"""
    from .snapshot import collect_snapshots

    cfg = load_config()
    day = _day(date_str)
    with _client(cfg) as client:
        rows = collect_snapshots(client, cfg, day=day, limit=limit)
    click.echo(f"snapshot: {len(rows)} repos -> data/snapshots/{day.isoformat()}.jsonl")


@cli.command()
@click.option("--date", "date_str", default=None, help="UTC 日期 YYYY-MM-DD")
def report(date_str: str | None) -> None:
    """生成 Markdown 日报。"""
    from report.daily_report import generate_report

    cfg = load_config()
    path = generate_report(cfg, day=_day(date_str))
    click.echo(f"report: {path}")


@cli.command(name="run")
@click.option("--date", "date_str", default=None, help="UTC 日期 YYYY-MM-DD")
@click.option("--limit", type=int, default=None, help="快照只取池前 N 个（冒烟用）")
@click.option("--skip-trending", is_flag=True, help="跳过 trending 抓取")
def run(date_str: str | None, limit: int | None, skip_trending: bool) -> None:
    """全流程: pool -> trending -> snapshot -> report。"""
    from .search_pool import build_pool
    from .snapshot import collect_snapshots
    from .trending_page import collect_trending
    from report.daily_report import generate_report

    cfg = load_config()
    day = _day(date_str)
    with _client(cfg) as client:
        p = build_pool(client, cfg, day=day)
        click.echo(f"[1/4] pool: {p['stats']['total']} repos")
        if not skip_trending:
            t = collect_trending(cfg, day=day)
            storage.write_trending(day, t)
            click.echo(f"[2/4] trending: {sum(len(b['repos']) for b in t['boards'])} items")
        else:
            click.echo("[2/4] trending: skipped")
        rows = collect_snapshots(client, cfg, day=day, limit=limit)
        click.echo(f"[3/4] snapshot: {len(rows)} repos")
        path = generate_report(cfg, day=day)
    click.echo(f"[4/4] report: {path}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
