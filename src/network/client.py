import asyncio
import logging
import re
import time
from typing import Optional

import aiohttp
from yarl import URL

from ..storage.config import DEFAULTS

logger = logging.getLogger("seedhound")

DEAD_TORRENT_PATTERNS = [
    (re.compile(r"没有权限|无权访问|无权限"), "permission_denied"),
    (
        re.compile(
            r"种子已删除|已被删除|已被移除|torrent.*deleted|torrent.*removed",
            re.IGNORECASE,
        ),
        "torrent_deleted",
    ),
    (re.compile(r"不存在|not found|no such|404", re.IGNORECASE), "not_found"),
    (re.compile(r"未登录|请登录|login|sign.?in", re.IGNORECASE), "auth_required"),
    (re.compile(r"禁止访问|access denied|forbidden|403", re.IGNORECASE), "forbidden"),
]


class DeadTorrentCache:
    def __init__(self, torrent_cache=None):
        self._dead: set[tuple[str, int]] = set()
        self._lock = asyncio.Lock()
        self._torrent_cache = torrent_cache

    def is_dead(self, site_name: str, torrent_id: int) -> bool:
        return (site_name, torrent_id) in self._dead

    async def mark_dead(self, site_name: str, torrent_id: int):
        async with self._lock:
            self._dead.add((site_name, torrent_id))
        if self._torrent_cache is not None:
            await self._torrent_cache.record_dead(site_name, torrent_id)

    async def seed(self, entries: set[tuple[str, int]]):
        """启动时把持久化且达阈值的死种灌入内存集合，避免重复下载。"""
        async with self._lock:
            self._dead.update(entries)

    def size(self) -> int:
        return len(self._dead)


def _detect_html_reason(data: bytes) -> Optional[str]:
    text = data.decode("utf-8", errors="replace")
    for pattern, reason in DEAD_TORRENT_PATTERNS:
        if pattern.search(text):
            return reason
    return None


# 「(\d+)\s*分钟后」已覆盖「分钟后重试 / 分钟后可下载」等更长文案，
# 保留更短的通配式即可，避免子集规则永远排不上用场。
RATE_LIMIT_HINT_PATTERNS = [
    (re.compile(r"(\d+)\s*分钟后"), "minutes"),
    (re.compile(r"(\d+)\s*秒后"), "seconds"),
    (re.compile(r"wait\s+(\d+)\s+minute", re.IGNORECASE), "minutes"),
    (re.compile(r"try\s+again\s+in\s+(\d+)\s+second", re.IGNORECASE), "seconds"),
    (re.compile(r"retry\s+after\s+(\d+)\s+second", re.IGNORECASE), "seconds"),
]

# 关键词同样按「最短可匹配」收敛：含「频繁」的文案已覆盖「过于频繁」「请求过于频繁」，
# 含「rate limit」的文案已覆盖「rate limit reached」。
RATE_LIMIT_KEYWORDS = [
    "频繁",
    "rate limit",
    "too many",
    "请稍后",
    "请稍候",
]

# 这些 HTTP 状态码是「结果不可改变」的硬失败（凭据失效 / 接口不存在 / 资源永久移除），
# 重试与退避只会白等，直接短路返回。
NON_RETRYABLE_HTTP_STATUS = frozenset({401, 403, 404, 405, 410})

# 这些异常消息同样代表硬失败：域名已死（DNS 解析失败）、证书已废（自签名/过期）。
# 匹配用小写子串，避免 aiohttp 各版本错误文案差异。
NON_RETRYABLE_ERROR_HINTS = (
    "getaddrinfo failed",
    "name or service not known",
    "certificate verify failed",
    "certificateverifyfailed",
    "clientconnectorcertificateerror",
    "ssl: certificate_verify_failed",
)


def _is_non_retryable_error(message: str) -> bool:
    """判断异常消息是否为「重试也不会成功」的硬失败。"""
    low = str(message).lower()
    return any(hint in low for hint in NON_RETRYABLE_ERROR_HINTS)


def _parse_rate_limit_response(data: bytes, content_type: str) -> Optional[float]:
    if len(data) > 10000:
        return None
    try:
        text = data.decode("utf-8", errors="replace")
        text_first = text[:500]
        for kw in RATE_LIMIT_KEYWORDS:
            if kw in text_first:
                for pattern, unit in RATE_LIMIT_HINT_PATTERNS:
                    m = pattern.search(text_first)
                    if m:
                        val = int(m.group(1))
                        delay = val * 60 if unit == "minutes" else val
                        logger.info(
                            "检测到限流提示，建议等待 %d 秒 (%d %s)", delay, val, unit
                        )
                        return max(delay, 10.0)
                return 60.0
    except Exception as exc:
        logger.debug("解析限流响应失败: %s", exc)
    return None


def _is_likely_text_error(data: bytes, content_type: str) -> Optional[float]:
    if len(data) > 500:
        return None
    if b"<html" in data[:200].lower() or b"<!doctype" in data[:200].lower():
        return None
    try:
        text = data.decode("utf-8", errors="replace")
        text_first = text[:200]
        for kw in RATE_LIMIT_KEYWORDS:
            if kw in text_first:
                for pattern, unit in RATE_LIMIT_HINT_PATTERNS:
                    m = pattern.search(text_first)
                    if m:
                        val = int(m.group(1))
                        delay = val * 60 if unit == "minutes" else val
                        logger.info(
                            "检测到限流提示，建议等待 %d 秒 (%d %s)", delay, val, unit
                        )
                        return max(delay, 10.0)
                return 60.0
        return None
    except Exception as exc:
        logger.debug("解析文本错误响应失败: %s", exc)
        return None


class SiteClient:
    def __init__(
        self,
        concurrency: int = DEFAULTS["global"]["concurrency"],
        timeout: float = DEFAULTS["global"]["api_timeout"],
        download_timeout: float = DEFAULTS["global"]["download_timeout"],
        retry_count: int = DEFAULTS["global"]["retry_count"],
        retry_delay: float = DEFAULTS["global"]["retry_delay"],
        dead_cache: Optional[DeadTorrentCache] = None,
        site_cookies: Optional[dict[str, str]] = None,
        download_interval: float = DEFAULTS["global"]["download_interval"],
        host_intervals: Optional[dict[str, float]] = None,
    ):
        self._concurrency = concurrency
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._download_timeout_value = download_timeout
        self._retry_count = retry_count
        self._retry_delay = retry_delay
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(concurrency)
        self._dead_cache = dead_cache or DeadTorrentCache()
        self._site_cookies = site_cookies or {}
        self._download_interval = download_interval
        self._host_intervals = host_intervals or {}
        self._host_last_request: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_backoff: dict[str, float] = {}

    async def start(self):
        connector = aiohttp.TCPConnector(
            limit=self._concurrency * 2,
            limit_per_host=6,
            ttl_dns_cache=300,
            force_close=True,
        )
        cookie_jar = aiohttp.CookieJar()
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=self._timeout,
            cookie_jar=cookie_jar,
            headers={
                "User-Agent": "SeedHound/2.0",
                "Accept": "application/json",
            },
        )
        for domain, cookie_str in self._site_cookies.items():
            if cookie_str:
                parsed_url = URL(domain)
                for item in cookie_str.split(";"):
                    item = item.strip()
                    if "=" in item:
                        key, _, value = item.partition("=")
                        self._session.cookie_jar.update_cookies(
                            {key.strip(): value.strip()}, parsed_url
                        )

    async def close(self):
        if self._session:
            await self._session.close()

    async def get_bytes(
        self,
        url: str,
        site_name: str = "",
        torrent_id: int = 0,
    ) -> tuple[Optional[bytes], str]:
        try:
            parsed = URL(url)
            host = parsed.host or ""
            if host:
                host_interval = self._host_intervals.get(host, self._download_interval)
            else:
                host_interval = self._download_interval
            if host_interval > 0 and host:
                if host not in self._host_locks:
                    self._host_locks[host] = asyncio.Lock()
                async with self._host_locks[host]:
                    now = time.monotonic()
                    last = self._host_last_request.get(host, 0)
                    backoff = self._host_backoff.get(host, 1.0)
                    wait_time = (host_interval * backoff) - (now - last)
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                    self._host_last_request[host] = time.monotonic()

            async with self._semaphore:
                headers = {"Accept": "*/*"}
                download_timeout = aiohttp.ClientTimeout(
                    total=self._download_timeout_value
                )
                async with self._session.get(
                    url,
                    timeout=download_timeout,
                    headers=headers,
                ) as resp:
                    content_type = resp.headers.get("Content-Type", "")
                    data = await resp.read()

                    if resp.status != 200:
                        logger.warning(
                            "下载失败 %s: HTTP %d (Content-Type: %s)",
                            url,
                            resp.status,
                            content_type,
                        )
                        if resp.status in (301, 302, 303, 307, 308):
                            location = resp.headers.get("Location", "")
                            logger.warning("  重定向目标: %s", location)
                            if "login" in location.lower():
                                logger.warning(
                                    "  站点 %s 需要登录 Cookie，已标记为 auth_required",
                                    site_name or "未知",
                                )
                                if site_name and torrent_id:
                                    await self._dead_cache.mark_dead(
                                        site_name, torrent_id
                                    )
                                return None, "auth_redirect"
                        limit_delay = _parse_rate_limit_response(data, content_type)
                        if limit_delay is not None:
                            logger.warning(
                                "  服务器返回限流状态码 %d (Content-Type: %s, 大小: %d)",
                                resp.status,
                                content_type,
                                len(data),
                            )
                            if host:
                                self._host_backoff[host] = max(
                                    self._host_backoff.get(host, 1.0),
                                    limit_delay / max(host_interval, 1.0),
                                )
                            return None, "rate_limit"
                        return None, f"http_{resp.status}"

                    if (
                        b"<html" in data[:200].lower()
                        or b"<!doctype" in data[:200].lower()
                    ):
                        reason = _detect_html_reason(data)
                        logger.warning(
                            "下载返回HTML而非torrent: %s (Content-Type: %s, 大小: %d, 原因: %s)",
                            url,
                            content_type,
                            len(data),
                            reason or "未知",
                        )
                        if reason and reason != "unknown" and site_name and torrent_id:
                            await self._dead_cache.mark_dead(site_name, torrent_id)
                        return None, f"html_{reason}" if reason else "html_unknown"

                    limit_delay = _is_likely_text_error(data, content_type)
                    if limit_delay is not None:
                        logger.warning(
                            "下载返回文本错误而非torrent: %s (Content-Type: %s, 大小: %d)",
                            url,
                            content_type,
                            len(data),
                        )
                        if host:
                            self._host_backoff[host] = max(
                                self._host_backoff.get(host, 1.0),
                                limit_delay / max(host_interval, 1.0),
                            )
                        return None, "rate_limit"

                    if not data or len(data) < 20:
                        logger.warning(
                            "下载数据过短 (%d 字节): %s (Content-Type: %s)",
                            len(data),
                            url,
                            content_type,
                        )
                        return None, "too_short"

                    return data, ""
        except asyncio.TimeoutError:
            logger.warning("下载超时: %s", url)
            return None, "timeout"
        except aiohttp.ClientError as e:
            err_msg = str(e).strip() if str(e).strip() else type(e).__name__
            logger.warning("下载错误 %s: %s", url, err_msg)
            return None, "connection_error"
        except Exception as e:
            err_msg = str(e).strip() if str(e).strip() else type(e).__name__
            logger.warning("下载错误 %s: %s", url, err_msg)
            return None, "connection_error"

    async def try_download(
        self,
        url: str,
        site_name: str = "",
        torrent_id: int = 0,
    ) -> tuple[Optional[bytes], str]:
        if site_name and torrent_id and self._dead_cache.is_dead(site_name, torrent_id):
            return None, "dead_cached"

        for attempt in range(self._retry_count + 1):
            data, reason = await self.get_bytes(
                url, site_name=site_name, torrent_id=torrent_id
            )
            if data:
                if attempt > 0:
                    logger.info(
                        "下载成功 %s (第 %d 次重试后恢复)", url, attempt
                    )
                return data, ""

            if reason == "timeout" and attempt < self._retry_count:
                delay = self._retry_delay * (attempt + 1)
                logger.debug(
                    "下载超时 %s，等待 %.1f 秒后重试 (%d/%d)",
                    url, delay, attempt + 1, self._retry_count
                )
                await asyncio.sleep(delay)
            else:
                return None, reason

        return None, "timeout"

    async def post_json(
        self,
        url: str,
        data: dict,
        retries: int = 1,
        headers: Optional[dict[str, str]] = None,
        label: str = "",
    ) -> Optional[dict]:
        """POST JSON；headers 用于 X-API-Key 这类按站点区分的鉴权头。

        label 是批次上下文（如「第 3/43 批」），只影响日志，用来把「批次」和「单次重试」分开看。
        硬失败（404/405/410/401/403、DNS 失败、证书错误、200 但返回 HTML）不会重试。
        """
        suffix = f" [{label}]" if label else ""
        last_error = ""
        attempts = 0
        for attempt in range(retries):
            attempts = attempt + 1
            try:
                async with self._semaphore:
                    async with self._session.post(
                        url,
                        json=data,
                        timeout=self._timeout,
                        headers=headers,
                    ) as resp:
                        if resp.status == 200:
                            if attempt > 0:
                                logger.info(
                                    "请求成功 %s%s (第 %d 次重试后恢复)",
                                    url,
                                    suffix,
                                    attempt,
                                )
                            try:
                                return await resp.json()
                            except aiohttp.ContentTypeError:
                                # HTTP 200 但不是 JSON：常见于表单错误页 / 登录跳转页，
                                # 重试永远不会得到 JSON，直接判定为独立错误类型。
                                logger.warning(
                                    "请求失败 %s%s (html_invalid_response: "
                                    "HTTP 200 但返回 HTML/非 JSON，可能是表单错误页或需登录，不重试)",
                                    url,
                                    suffix,
                                )
                                return None
                        if resp.status == 429:
                            retry_after = resp.headers.get("Retry-After", "5")
                            # Retry-After 也可能是 HTTP-date，无法直接转 float
                            try:
                                delay = float(retry_after)
                            except (TypeError, ValueError):
                                delay = 5.0
                            await asyncio.sleep(delay)
                            continue
                        last_error = f"HTTP {resp.status}"
                        if resp.status in NON_RETRYABLE_HTTP_STATUS:
                            logger.warning(
                                "请求失败 %s%s (%s，硬失败不重试)",
                                url,
                                suffix,
                                last_error,
                            )
                            return None
                        logger.debug(
                            "%s from %s%s (attempt %d/%d)",
                            last_error,
                            url,
                            suffix,
                            attempt + 1,
                            retries,
                        )
            except asyncio.TimeoutError:
                last_error = "timeout"
                logger.debug(
                    "请求超时 %s%s (attempt %d/%d)",
                    url,
                    suffix,
                    attempt + 1,
                    retries,
                )
            except aiohttp.ClientError as e:
                last_error = str(e).strip() or type(e).__name__
                if _is_non_retryable_error(last_error):
                    logger.warning(
                        "请求失败 %s%s (%s，硬失败不重试)",
                        url,
                        suffix,
                        last_error,
                    )
                    return None
                logger.debug(
                    "请求错误 %s: %s%s (attempt %d/%d)",
                    url,
                    last_error,
                    suffix,
                    attempt + 1,
                    retries,
                )
            if attempt < retries - 1:
                delay = self._retry_delay * (attempt + 1)
                logger.debug("等待 %.1f 秒后重试 %s", delay, url)
                await asyncio.sleep(delay)

        logger.warning(
            "请求失败 %s%s (%s, 共尝试 %d 次)", url, suffix, last_error, attempts
        )
        return None

    async def query_site(
        self,
        site: dict,
        pieces_hashes: list[str],
        label: str = "",
    ) -> Optional[dict]:
        data = {
            "passkey": site["passkey"],
            "pieces_hash": pieces_hashes,
        }
        return await self.post_json(
            site["api_url"],
            data,
            retries=self._retry_count,
            label=label,
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
        batch_succeeded = [0]
        batch_failed = [0]
        total_batches = len(pieces_batches)
        site_name = site.get("name", "")

        async def query_one(batch_index: int, batch: list[str]):
            if site_dead.is_set():
                return
            # label 只用于日志，把「第几批」和「单次请求的重试次数」分开看
            label = f"第 {batch_index + 1}/{total_batches} 批"
            async with sem:
                if site_dead.is_set():
                    return
                response = await self.query_site(site, batch, label=label)
                if response is None:
                    batch_failed[0] += 1
                    consecutive_failures[0] += 1
                    if consecutive_failures[0] >= max_consecutive_failures:
                        site_dead.set()
                        logger.info(
                            "站点 %s 连续 %d 次失败 (共 %d/%d 批次失败)，跳过剩余批次",
                            site_name,
                            consecutive_failures[0],
                            batch_failed[0],
                            total_batches,
                        )
                    return
                if not isinstance(response, dict):
                    logger.warning(
                        "站点 %s 返回非预期的响应类型 %s，本批次记为失败",
                        site_name,
                        type(response).__name__,
                    )
                    batch_failed[0] += 1
                    consecutive_failures[0] += 1
                    return
                batch_succeeded[0] += 1
                consecutive_failures[0] = 0
                if isinstance(response.get("data"), dict):
                    for pieces_hash, torrent_id in response["data"].items():
                        if pieces_hash in ("passkey", "pieces_hash"):
                            continue
                        try:
                            results.append((pieces_hash, int(torrent_id)))
                        except (ValueError, TypeError):
                            pass

        tasks = [query_one(i, batch) for i, batch in enumerate(pieces_batches)]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for failure in batch_results:
            if isinstance(failure, Exception):
                logger.warning("站点 %s 批次查询异常: %s", site_name, failure)
                batch_failed[0] += 1
                consecutive_failures[0] += 1
                if consecutive_failures[0] >= max_consecutive_failures:
                    site_dead.set()

        if batch_failed[0] > 0:
            logger.warning(
                "站点 %s 批次查询: 成功 %d/%d, 失败 %d/%d",
                site_name,
                batch_succeeded[0],
                total_batches,
                batch_failed[0],
                total_batches,
            )

        return results
