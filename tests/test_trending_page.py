"""trending 页面解析 golden file 回归测试。

fixtures/trending_sample.html 为页面结构标本：页面改版导致解析挂掉时，
先更新 fixture 与 SELECTORS，再让测试通过。
"""

from pathlib import Path

import pytest

from collector.trending_page import ParseError, parse_trending_html, board_url

FIXTURE = Path(__file__).parent / "fixtures" / "trending_sample.html"


def test_board_url():
    assert board_url("", "daily") == "https://github.com/trending?since=daily"
    assert board_url("python", "weekly") == "https://github.com/trending/python?since=weekly"


def test_parse_extracts_rows_in_order():
    repos = parse_trending_html(FIXTURE.read_text(encoding="utf-8"))
    assert len(repos) == 3

    first = repos[0]
    assert first["full_name"] == "morluto/rea"
    assert first["stars_total"] == 330107  # 逗号千分位已剥
    assert first["forks"] == 83544
    assert first["stars_period"] == 5120
    assert first["language"] == "TypeScript"
    assert "Reverse engineer" in (first["description"] or "")

    second = repos[1]
    assert second["full_name"] == "deepseek-ai/deepseek-harness"
    assert second["stars_period"] == 3456  # weekly 文案

    third = repos[2]
    assert third["full_name"] == "Panniantong/Agent-Reach"
    assert third["language"] is None  # 无语言标签
    assert third["description"] is None  # 无描述
    assert third["stars_period"] == 12345  # monthly 文案


def test_parse_empty_page_raises():
    with pytest.raises(ParseError):
        parse_trending_html("<html><body><main></main></body></html>")
