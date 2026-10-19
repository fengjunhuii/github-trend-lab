"""GitHub API 客户端：限流感知 + 重试 + Search 节流。

设计要点（docs/development-plan.md §4.2）：
- 限流状态从响应头 x-ratelimit-* 读取，不自行计数
- core 限额接近耗尽 -> 睡到 reset；超过可等待上限抛 RateLimitError
- search 请求固定节流（认证 30/min -> 2.5s/次；匿名 10/min -> 7s/次）
- 5xx / 网络错误指数退避重试；403 带 Retry-After 时等待后重试
- 非限流类 403（如权限问题）不重试直接暴露，避免"越重试越挂"（错题本 data-semantics #3）
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
MAX_RETRIES = 3
BACKOFF_BASE = 2.0
CORE_RESERVE = 50             # core 剩余额度低于该值时进入等待
CORE_MAX_SLEEP_SEC = 15 * 60  # 愿意等待的上限，超过直接抛错（Actions 总超时内）


class RateLimitError(RuntimeError):
    """限流无法在可等待窗口内恢复。"""


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        search_interval: float = 2.5,
        timeout: float = 30.0,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-trend-lab/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            log.warning("未配置 GH_TOKEN，使用匿名限额（core 60 次/小时，search 10 次/分钟）")
        # follow_redirects: /repos/{owner}/{repo} 在仓库改名后返回 301，跟随即拿到现名
        self._client = httpx.Client(
            base_url=API_BASE, headers=headers, timeout=timeout, follow_redirects=True
        )
        self._search_interval = max(search_interval, 7.0) if not token else search_interval
        self._last_search_at = 0.0
        self.core_remaining: int | None = None
        self.core_reset: int = 0
        self.core_used = 0    # 本次进程成功请求数（用量统计，非限流依据）
        self.search_used = 0

    # ---------- 限流 ----------

    def _read_rate_headers(self, resp: httpx.Response) -> None:
        try:
            self.core_remaining = int(resp.headers["x-ratelimit-remaining"])
            self.core_reset = int(resp.headers["x-ratelimit-reset"])
        except (KeyError, ValueError):
            pass

    def _respect_core_limit(self) -> None:
        if self.core_remaining is None or self.core_remaining > CORE_RESERVE:
            return
        wait = max(0, self.core_reset - time.time())
        if wait > CORE_MAX_SLEEP_SEC:
            raise RateLimitError(
                f"core 限额耗尽，{wait / 60:.0f} 分钟后重置，超出可等待上限"
            )
        log.warning("core 限额接近耗尽（剩余 %d），等待 %.0f 秒至重置", self.core_remaining, wait + 1)
        time.sleep(wait + 1)
        self.core_remaining = None  # 重置后状态未知，交由下个响应头刷新

    def _throttle_search(self) -> None:
        wait = self._search_interval - (time.monotonic() - self._last_search_at)
        if wait > 0:
            time.sleep(wait)
        self._last_search_at = time.monotonic()

    # ---------- 请求 ----------

    def get_json(self, path: str, *, params: dict | None = None, search: bool = False) -> Any:
        if search:
            self._throttle_search()
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            if not search:
                self._respect_core_limit()
            try:
                resp = self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_err = exc
                if attempt == MAX_RETRIES:
                    break
                log.warning("网络错误(第 %d 次): %s，%.1fs 后重试", attempt, exc, delay)
                time.sleep(delay)
                delay *= BACKOFF_BASE
                continue

            if not search:
                self._read_rate_headers(resp)

            if resp.status_code < 400:
                if search:
                    self.search_used += 1
                else:
                    self.core_used += 1
                return None if resp.status_code == 204 else resp.json()

            if resp.status_code in (403, 429):
                retry_after = resp.headers.get("retry-after")
                if retry_after is not None and attempt < MAX_RETRIES:
                    wait = int(retry_after) + 1
                    log.warning("命中二级限流，Retry-After %ds", wait)
                    time.sleep(wait)
                    continue
                if not search and self.core_remaining == 0:
                    self._respect_core_limit()  # 内部会 sleep 或抛 RateLimitError
                    continue
                resp.raise_for_status()  # 非限流 403（权限等），不重试

            if resp.status_code >= 500 and attempt < MAX_RETRIES:
                log.warning("服务端 %d (第 %d 次)，%.1fs 后重试", resp.status_code, attempt, delay)
                time.sleep(delay)
                delay *= BACKOFF_BASE
                continue

            resp.raise_for_status()

        raise RateLimitError(f"GET {path} 重试耗尽: {last_err}")

    # ---------- 业务封装 ----------

    def search_repositories(
        self, query: str, *, per_page: int = 100, max_pages: int = 10, sort: str = "stars"
    ) -> tuple[list[dict], int]:
        """返回 (items, total_count)。total > max_pages*per_page 时由调用方负责分桶。"""
        items: list[dict] = []
        total = 0
        for page in range(1, max_pages + 1):
            data = self.get_json(
                "/search/repositories",
                params={
                    "q": query,
                    "per_page": per_page,
                    "page": page,
                    "sort": sort,
                    "order": "desc",
                },
                search=True,
            )
            total = int(data.get("total_count", 0))
            page_items = data.get("items", [])
            items.extend(page_items)
            if len(page_items) < per_page or len(items) >= total:
                break
        return items, total

    def search_total_count(self, query: str) -> int:
        data = self.get_json(
            "/search/repositories", params={"q": query, "per_page": 1}, search=True
        )
        return int(data.get("total_count", 0))

    def get_repo(self, full_name: str) -> dict:
        return self.get_json(f"/repos/{full_name}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
