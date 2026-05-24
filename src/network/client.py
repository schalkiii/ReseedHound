import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("reseed_puppy")


class SiteClient:
    def __init__(
        self,
        concurrency: int = 20,
        timeout: float = 15.0,
        retry_count: int = 2,
        retry_delay: float = 2.0,
    ):
        self._concurrency = concurrency
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._retry_count = retry_count
        self._retry_delay = retry_delay
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(concurrency)

    async def start(self):
        connector = aiohttp.TCPConnector(
            limit=self._concurrency * 2,
            limit_per_host=2,
            ttl_dns_cache=300,
            force_close=False,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=self._timeout,
            headers={
                "User-Agent": "Reseed-Puppy-Standalone/2.0",
                "Accept": "application/json",
            },
        )

    async def close(self):
        if self._session:
            await self._session.close()

    async def post_json(self, url: str, data: dict, retries: int = 1) -> Optional[dict]:
        for attempt in range(retries):
            try:
                async with self._semaphore:
                    async with self._session.post(
                        url,
                        json=data,
                        timeout=self._timeout,
                    ) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        if resp.status == 429:
                            retry_after = resp.headers.get("Retry-After", "5")
                            await asyncio.sleep(float(retry_after))
                            continue
                        logger.warning(
                            "HTTP %d from %s (attempt %d/%d)",
                            resp.status, url, attempt + 1, retries,
                        )
            except asyncio.TimeoutError:
                logger.warning(
                    "请求超时 %s (attempt %d/%d)", url, attempt + 1, retries,
                )
            except aiohttp.ClientError as e:
                logger.warning(
                    "请求错误 %s: %s (attempt %d/%d)", url, e, attempt + 1, retries,
                )
            if attempt < retries - 1:
                await asyncio.sleep(self._retry_delay * (attempt + 1))
        return None

    async def query_site(self, site: dict, pieces_hashes: list[str]) -> Optional[dict]:
        data = {
            "passkey": site["passkey"],
            "pieces_hash": pieces_hashes,
        }
        return await self.post_json(
            site["api_url"],
            data,
            retries=self._retry_count,
        )

    async def query_site_batched(
        self,
        site: dict,
        pieces_batches: list[list[str]],
        max_consecutive_failures: int = 5,
    ) -> list[tuple[str, int]]:
        results = []
        sem = asyncio.Semaphore(2)
        site_dead = asyncio.Event()
        consecutive_failures = [0]

        async def query_one(batch: list[str]):
            if site_dead.is_set():
                return
            async with sem:
                if site_dead.is_set():
                    return
                response = await self.query_site(site, batch)
                if response is None:
                    consecutive_failures[0] += 1
                    if consecutive_failures[0] >= max_consecutive_failures:
                        site_dead.set()
                        logger.info(
                            "站点 %s 连续 %d 次失败，跳过剩余批次",
                            site.get("name", ""), consecutive_failures[0],
                        )
                    return
                consecutive_failures[0] = 0
                if isinstance(response.get("data"), dict):
                    for pieces_hash, torrent_id in response["data"].items():
                        if pieces_hash in ("passkey", "pieces_hash"):
                            continue
                        try:
                            results.append((pieces_hash, int(torrent_id)))
                        except (ValueError, TypeError):
                            pass

        tasks = [query_one(batch) for batch in pieces_batches]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results
