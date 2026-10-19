"""storage 读写往返 + 幂等语义（重跑当天时 d1 基准仍是前一天）。"""

from datetime import date

from collector import storage


def test_snapshot_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "SNAP_DIR", tmp_path / "snapshots")
    day = date(2026, 10, 19)
    rows = [
        {
            "schema_v": 1, "date": "2026-10-19", "repo_id": 1, "full_name": "a/b",
            "description": None, "language": "Go", "topics": ["x"], "owner_type": "User",
            "created_at": "2026-01-01T00:00:00Z", "pushed_at": None,
            "stars": 100, "forks": 10, "open_issues": 1, "d1": 5,
        }
    ]
    storage.write_snapshots(day, rows)
    assert storage.read_snapshots(day) == rows
    # 覆盖写幂等
    storage.write_snapshots(day, rows)
    assert storage.read_snapshots(day) == rows


def test_snapshot_roundtrip_with_unicode_line_separator(tmp_path, monkeypatch):
    """description 含 U+2028（JSON 合法、dumps 不转义、splitlines 会误切）时仍按物理行往返。"""
    monkeypatch.setattr(storage, "SNAP_DIR", tmp_path / "snapshots")
    day = date(2026, 10, 19)
    rows = [
        {
            "schema_v": 1, "date": "2026-10-19", "repo_id": 1, "full_name": "a/b",
            # 运行时构造 U+2028，源码保持纯 ASCII（不可见字面量无法可靠穿透工具链）
            "description": "Turn Videos into" + chr(0x2028) + "ASCII Frames",
            "language": None,
            "topics": [], "owner_type": "User", "created_at": "2026-01-01T00:00:00Z",
            "pushed_at": None, "stars": 1, "forks": 0, "open_issues": 0, "d1": None,
        }
    ]
    assert chr(0x2028) in rows[0]["description"]
    storage.write_snapshots(day, rows)
    assert storage.read_snapshots(day) == rows


def test_latest_snapshot_before(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "SNAP_DIR", tmp_path / "snapshots")
    d1, d2, d3 = date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19)
    for d in (d1, d2, d3):
        storage.write_snapshots(d, [{"repo_id": d.day}])

    prev = storage.latest_snapshot_before(d3)
    assert prev is not None
    assert prev[0] == d2  # 重跑当天(d3)时基准仍是前一天 -> 幂等
    assert storage.latest_snapshot_before(d1) is None


def test_pool_and_trending_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "POOL_FILE", tmp_path / "pool.json")
    monkeypatch.setattr(storage, "TREND_DIR", tmp_path / "trending")
    pool = {
        "schema_v": 1, "updated_at": None,
        "repos": [{"full_name": "a/b", "repo_id": 1, "reasons": ["new_star"],
                   "first_seen": "2026-10-19", "meta": {}}],
    }
    storage.save_pool(pool)
    assert storage.load_pool()["repos"][0]["full_name"] == "a/b"

    day = date(2026, 10, 19)
    storage.write_trending(day, {"boards": []})
    assert storage.read_trending(day) == {"boards": []}
    assert storage.read_trending(date(2026, 10, 18)) is None
