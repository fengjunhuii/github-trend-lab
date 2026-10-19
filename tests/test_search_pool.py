"""search_pool 分桶/细分/去重逻辑单测（FakeClient 对齐真实调用面签名）。"""

from datetime import date

from collector.search_pool import (
    bucket_query,
    fetch_new_stars,
    months_between,
    sliced_query,
)


class FakeClient:
    """对齐 GitHubClient 的 search_total_count / search_repositories 签名。"""

    def __init__(self, totals: dict[str, int], items_by_query: dict[str, list[dict]]):
        self._totals = totals
        self._items = items_by_query

    def search_total_count(self, query: str) -> int:
        return self._totals.get(query, 0)

    def search_repositories(self, query, *, per_page=100, max_pages=10, sort="stars"):
        return self._items.get(query, []), self._totals.get(query, 0)


class SearchCfg:
    per_page = 100
    max_pages = 10


def test_months_between_spans_year():
    ms = months_between(date(2026, 1, 1), date(2026, 3, 15))
    assert len(ms) == 3
    assert ms[0] == (date(2026, 1, 1), date(2026, 1, 31))
    assert ms[1] == (date(2026, 2, 1), date(2026, 2, 28))
    assert ms[-1] == (date(2026, 3, 1), date(2026, 3, 15))


def test_query_builders():
    assert bucket_query("2026-01-01", "stars:>=10000") == "created:>=2026-01-01 stars:>=10000"
    assert (
        sliced_query(date(2026, 2, 1), date(2026, 2, 28), "stars:5000..9999")
        == "created:2026-02-01..2026-02-28 stars:5000..9999"
    )


def test_over_limit_bucket_subdivides_by_month():
    """超限桶（1200 > 990）触发按月细分，全部数据可取回。"""
    months = months_between(date(2026, 1, 1), date(2026, 10, 19))
    base = "created:>=2026-01-01 stars:>=10000"
    totals = {base: 1200}
    items: dict[str, list[dict]] = {}
    for (s, e), repo_id_base in zip(months, range(1, 11)):
        q = sliced_query(s, e, "stars:>=10000")
        totals[q] = 120
        items[q] = [
            {"id": repo_id_base * 1000 + i, "full_name": f"o/r{repo_id_base}-{i}"}
            for i in range(120)
        ]
    client = FakeClient(totals, items)

    got = fetch_new_stars(
        client,
        since="2026-01-01",
        star_buckets=["stars:>=10000"],
        search_cfg=SearchCfg(),
        today=date(2026, 10, 19),
    )
    assert len(got) == 1200


def test_multi_bucket_dedupes_by_repo_id():
    """同一仓库出现在两个桶中时按 repo_id 去重。"""
    base1 = "created:>=2026-01-01 stars:>=10000"
    base2 = "created:>=2026-01-01 stars:5000..9999"
    shared = {"id": 42, "full_name": "o/shared"}
    client = FakeClient(
        {base1: 2, base2: 2},
        {
            base1: [shared, {"id": 1, "full_name": "o/a"}],
            base2: [dict(shared), {"id": 2, "full_name": "o/b"}],
        },
    )
    got = fetch_new_stars(
        client,
        since="2026-01-01",
        star_buckets=["stars:>=10000", "stars:5000..9999"],
        search_cfg=SearchCfg(),
        today=date(2026, 10, 19),
    )
    assert len(got) == 3
    assert {r["full_name"] for r in got} == {"o/shared", "o/a", "o/b"}
