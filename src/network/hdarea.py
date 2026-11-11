"""HDArea REST API v1 的 info_hash 批量匹配通道。

实测接口语义（api_docs.php §2.3 / §4，2026-11-11 在已登录浏览器内核验）：

    POST /api/v1/torrents/hashes
    X-API-Key: <32 位子令牌>
    {"hashes": ["<40 位 hex>", ...]}                 # 一次最多 200 个

    -> {"success": true, "code": 0, "data": {"count": N, "items": [
         {"id": 460345, "info_hash": "...", "name": "...", "size": ...,
          "download_url": ".../download.php?id=460345&dl_token=..."}]}}

``download_url`` 带 ``dl_token`` 短效凭证（站点声明 1800 秒有效）；实测以
``credentials:'omit'``（不带任何 Cookie）请求可直接得到 ``application/x-bittorrent``，
因此本通道既不需要 passkey，也不需要站点 Cookie。

注意 info_hash 是 pieces_hash 匹配的子集：各站会改写 info 字典（source / private /
passkey），只有 info 完全一致时才会命中，召回率天然低于通用 pieces_hash 通道。
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from ..storage.config import DEFAULTS
from .client import SiteClient

logger = logging.getLogger("seedhound")

HDAREA_API_TYPE = "hdarea"
HDAREA_HASHES_PATH = "/api/v1/torrents/hashes"

# 接口硬约束，不暴露为可配置项；split_batches 会把任何更大的请求尺寸收敛到该值。
MAX_HASHES_PER_REQUEST = 200
INFO_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class HdareaMatch:
    info_hash: str
    torrent_id: int
    download_url: str


def is_hdarea_site(site: dict) -> bool:
    """站点是否走 HDArea info_hash 通道。"""
    return str(site.get("api_type", "")).lower() == HDAREA_API_TYPE


def hashes_url(site: dict) -> str:
    """优先用显式 api_url，否则由站点主页推导官方 REST 路径。"""
    configured = str(site.get("api_url") or "").rstrip("/")
    if configured:
        return configured
    return str(site.get("url") or "").rstrip("/") + HDAREA_HASHES_PATH


def split_batches(
    info_hashes: list[str], size: int = MAX_HASHES_PER_REQUEST
) -> list[list[str]]:
    """过滤非法 info_hash 并按接口上限分批。"""
    valid = [h.lower() for h in info_hashes if INFO_HASH_PATTERN.match(h)]
    limit = max(1, min(size, MAX_HASHES_PER_REQUEST))
    return [valid[i : i + limit] for i in range(0, len(valid), limit)]


def parse_hashes_response(response: Optional[dict]) -> list[HdareaMatch]:
    """解析统一包裹结构，只保留字段完整的命中项。"""
    if not isinstance(response, dict):
        return []
    if response.get("success") is not True or response.get("code", 0) != 0:
        return []
    data = response.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return []

    matches = []
    for item in data["items"]:
        if not isinstance(item, dict):
            continue
        info_hash = str(item.get("info_hash") or "").lower()
        if not INFO_HASH_PATTERN.match(info_hash):
            continue
        try:
            torrent_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        matches.append(
            HdareaMatch(
                info_hash=info_hash,
                torrent_id=torrent_id,
                download_url=str(item.get("download_url") or ""),
            )
        )
    return matches


class HdareaMatcher:
    """把本地做种 info_hash 批量送到 HDArea，返回站内命中项。

    站点声明读取限流 60 次/分，因此用全局节流锁保证相邻请求间隔，
    并发数只限制「在途响应」数量，不再放大发送速率。
    """

    def __init__(
        self,
        site_client: SiteClient,
        request_interval: float = DEFAULTS["hdarea"]["request_interval"],
        concurrency: int = DEFAULTS["hdarea"]["concurrency"],
        retry_count: int = DEFAULTS["global"]["retry_count"],
    ):
        self._client = site_client
        self._request_interval = max(0.0, float(request_interval))
        self._retry_count = max(1, int(retry_count))
        self._semaphore = asyncio.Semaphore(max(1, int(concurrency)))
        self._request_lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        if self._request_interval <= 0:
            return
        async with self._request_lock:
            now = time.monotonic()
            wait = self._request_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def query(self, site: dict, info_hashes: list[str]) -> list[HdareaMatch]:
        site_name = site.get("name", "?")
        api_key = site.get("api_key")
        if not api_key:
            logger.warning("站点 %s 缺少 api_key，跳过 HDArea 通道", site_name)
            return []

        url = hashes_url(site)
        headers = {"X-API-Key": str(api_key)}
        batches = split_batches(info_hashes)
        if not batches:
            return []

        async def query_one(batch_index: int, batch: list[str]) -> list[HdareaMatch]:
            label = f"第 {batch_index + 1}/{len(batches)} 批"
            async with self._semaphore:
                await self._throttle()
                response = await self._client.post_json(
                    url,
                    {"hashes": batch},
                    retries=self._retry_count,
                    headers=headers,
                    label=label,
                )
                return parse_hashes_response(response)

        batch_results = await asyncio.gather(
            *(query_one(i, batch) for i, batch in enumerate(batches)),
            return_exceptions=True,
        )

        matches: list[HdareaMatch] = []
        failed_batches = 0
        for result in batch_results:
            if isinstance(result, Exception):
                failed_batches += 1
                logger.warning("站点 %s HDArea 批次查询异常: %s", site_name, result)
                continue
            matches.extend(result)

        logger.info(
            "站点 %s 查询完成: %d 批 / 失败 %d 批 / 命中 %d 个种子",
            site_name,
            len(batches),
            failed_batches,
            len(matches),
        )
        return matches
