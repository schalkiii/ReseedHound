import asyncio
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import aiohttp

from ..utils._bencode import bdecode
from ..utils.hash import compute_pieces_hash

logger = logging.getLogger("seedhound")


_TECH_TAGS = re.compile(
    r"\b(1080[pi]|720p|2160p|4[Kk]|480p|576p)\b|"
    r"\b(BluRay|Blu-ray|WEB-DL|WEBRip|HDTV|DVDRip|BDRip|Remux|AMZN|NF)\b|"
    r"\b(H\.?264|H\.?265|HEVC|AVC|XviD|DivX|x264|x265)\b|"
    r"\b(DDP?[\.\d]*|DTS[\-\.\w]*|Atmos|TrueHD|FLAC|AAC|AC3|DD[\.\d]*\+?)\b|"
    r"\b(10bit|8bit|HDR\d*|DV|DoVi|SDR|HLG)\b|"
    r"\b(MNHD|FRDS|HHWEB|DJWEB|NoGroup|No1|TRiNiTY|FLUX)\b",
    re.IGNORECASE,
)


def _extract_search_terms(torrent_name: str) -> str:
    cleaned = torrent_name.replace(".", " ").replace("_", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    year_match = re.search(r"\b((?:19|20)\d{2})\b", cleaned)
    year = year_match.group(1) if year_match else ""

    before_year = cleaned[: year_match.start()] if year_match else cleaned

    words = before_year.strip().split()
    tech_start = None
    for i, w in enumerate(words):
        if _TECH_TAGS.match(w):
            tech_start = i
            break

    if tech_start is not None:
        title_words = words[:tech_start]
    else:
        title_words = words

    if not title_words:
        title_words = [before_year.strip()]

    parts = [" ".join(title_words)]
    if year:
        parts.append(year)

    search_term = " ".join(parts).strip()
    if len(search_term) > 120:
        search_term = search_term[:120].rsplit(" ", 1)[0]

    return search_term


@dataclass
class JackettResult:
    title: str
    size: int
    seeders: int
    peers: int
    download_url: str
    info_hash: str
    indexer_id: str
    indexer_name: str
    publish_date: str = ""


@dataclass
class JackettStats:
    searched_count: int = 0
    matched_count: int = 0
    downloaded_count: int = 0
    failed_count: int = 0
    indexer_hits: dict = field(default_factory=dict)


class JackettClient:
    _CACHE_PATH = "data/jackett_dl_cache.json"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        concurrency: int = 5,
        retry_count: int = 2,
        retry_delay: float = 2.0,
        search_interval: float = 1.0,
    ):
        base = base_url.rstrip("/")
        if not base.endswith("/api/v2.0"):
            if "/api" not in base:
                base = f"{base}/api/v2.0"
        self._base_url = base
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._semaphore = asyncio.Semaphore(concurrency)
        self._retry_count = retry_count
        self._retry_delay = retry_delay
        self._search_interval = search_interval
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_search_time: float = 0.0
        self._search_lock = asyncio.Lock()
        self.stats = JackettStats()
        self._dl_cache: dict[str, str] = {}
        self._cache_dirty = False

    async def start(self):
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=10, force_close=True)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=self._timeout,
            headers={"User-Agent": "SeedHound/2.0"},
        )
        self._load_cache()

    async def close(self):
        if self._cache_dirty:
            self._save_cache()
        if self._session:
            await self._session.close()

    def _load_cache(self):
        try:
            path = Path(self._CACHE_PATH)
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._dl_cache = data.get("pieces_hash", {})
                logger.debug("Jackett 下载缓存已加载: %d 条", len(self._dl_cache))
        except Exception:
            self._dl_cache = {}

    def _save_cache(self):
        try:
            path = Path(self._CACHE_PATH)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {"pieces_hash": self._dl_cache}, f, ensure_ascii=False, indent=2
                )
            self._cache_dirty = False
            logger.debug("Jackett 下载缓存已保存: %d 条", len(self._dl_cache))
        except Exception:
            pass

    async def test_connection(self) -> bool:
        try:
            url = f"{self._base_url}/indexers/all/results/torznab/api"
            params = {
                "apikey": self._api_key,
                "t": "caps",
            }
            async with self._session.get(url, params=params) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def search(self, query: str) -> list[JackettResult]:
        async with self._search_lock:
            now = time.monotonic()
            wait_time = self._search_interval - (now - self._last_search_time)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_search_time = time.monotonic()

        for attempt in range(self._retry_count + 1):
            try:
                async with self._semaphore:
                    url = f"{self._base_url}/indexers/all/results/torznab/api"
                    params = {
                        "apikey": self._api_key,
                        "t": "search",
                        "q": query,
                    }
                    async with self._session.get(url, params=params) as resp:
                        if resp.status != 200:
                            logger.debug(
                                "Jackett 搜索 HTTP %d: %s", resp.status, query[:50]
                            )
                            return []
                        text = await resp.text()
                        parsed = self._parse_torznab(text)
                        logger.debug(
                            "  [Jackett Search] %s -> %d 条",
                            query[:40],
                            len(parsed),
                        )
                        return parsed
            except asyncio.TimeoutError:
                logger.debug(
                    "Jackett 搜索超时: %s (attempt %d)", query[:50], attempt + 1
                )
            except Exception:
                logger.debug(
                    "Jackett 搜索错误: %s (attempt %d)", query[:50], attempt + 1
                )

            if attempt < self._retry_count:
                await asyncio.sleep(self._retry_delay * (attempt + 1))

        return []

    def _parse_torznab(self, xml_text: str) -> list[JackettResult]:
        results = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.debug("Jackett 响应 XML 解析失败")
            return results

        ns = {"torznab": "http://torznab.hatim.com/schemas/2015/feed"}
        for item in root.iter("item"):
            try:
                title_el = item.find("title")
                title = (
                    title_el.text.strip()
                    if title_el is not None and title_el.text
                    else ""
                )
                if not title:
                    continue

                size_el = item.find("size")
                size = int(size_el.text) if size_el is not None and size_el.text else 0

                link_el = item.find("link")
                download_url = (
                    link_el.text.strip() if link_el is not None and link_el.text else ""
                )

                guid_el = item.find("guid")
                info_hash = (
                    guid_el.text.strip() if guid_el is not None and guid_el.text else ""
                )

                indexer_el = item.find("jackettindexer")
                indexer_id = indexer_el.get("id", "") if indexer_el is not None else ""
                indexer_name = (
                    indexer_el.text.strip()
                    if indexer_el is not None and indexer_el.text
                    else ""
                )

                pubdate_el = item.find("pubDate")
                pubdate = (
                    pubdate_el.text.strip()
                    if pubdate_el is not None and pubdate_el.text
                    else ""
                )

                seeders = 0
                peers = 0
                tn_infohash = ""
                attrs = item.find("torznab:attrs", ns)
                if attrs is not None:
                    for attr in attrs.findall("torznab:attr", ns):
                        name = attr.get("name", "")
                        value = attr.get("value", "0")
                        if name == "seeders":
                            seeders = int(value)
                        elif name == "peers":
                            peers = int(value)
                        elif name == "infohash":
                            tn_infohash = value

                if tn_infohash:
                    info_hash = tn_infohash

                results.append(
                    JackettResult(
                        title=title,
                        size=size,
                        seeders=seeders,
                        peers=peers,
                        download_url=download_url,
                        info_hash=info_hash,
                        indexer_id=indexer_id,
                        indexer_name=indexer_name,
                        publish_date=pubdate,
                    )
                )
            except Exception:
                continue

        return results

    def filter_by_size(
        self,
        results: list[JackettResult],
        target_size: int,
    ) -> list[JackettResult]:
        if not target_size or target_size <= 0:
            return []
        if target_size >= 1_000_000_000:
            tolerance = 10_000_000
        elif target_size >= 1_000_000:
            tolerance = 10_000
        else:
            tolerance = 1
        lower = target_size - tolerance
        upper = target_size + tolerance
        return [r for r in results if lower <= r.size <= upper]

    async def search_and_download(
        self,
        torrent_name: str,
        torrent_size: int,
        target_pieces_hash: str,
        target_info_hash: str = "",
        http_client=None,
    ) -> list[tuple[bytes, JackettResult]]:
        if not torrent_name or not target_pieces_hash:
            return []

        query = _extract_search_terms(torrent_name)
        if not query:
            return []
        results = await self.search(query)
        self.stats.searched_count += 1

        if not results:
            logger.info(
                "  [Jackett] 搜索=0 条: %s",
                torrent_name[:40],
            )
            return []

        matched = self.filter_by_size(results, torrent_size)
        if not matched:
            logger.info(
                "  [Jackett] 搜索=%d 尺寸匹配=0 条: %s",
                len(results),
                torrent_name[:40],
            )
            return []

        self.stats.matched_count += len(matched)
        logger.info(
            "  [Jackett] 搜索=%d 尺寸匹配=%d 条: %s",
            len(results),
            len(matched),
            torrent_name[:40],
        )
        downloaded = []

        def _parse_pieces_hash(torrent_data: bytes) -> str:
            try:
                torrent = bdecode(torrent_data)
                if isinstance(torrent, dict) and b"info" in torrent:
                    info = torrent[b"info"]
                    if isinstance(info, dict) and b"pieces" in info:
                        return compute_pieces_hash(info[b"pieces"])
            except Exception:
                pass
            return ""

        async def download_one(
            result: JackettResult,
        ) -> Optional[tuple[bytes, JackettResult]]:
            if not result.download_url:
                return None

            url = result.download_url

            if url in self._dl_cache:
                cached_hash = self._dl_cache[url]
                if cached_hash == target_pieces_hash:
                    logger.info(
                        "  [Jackett] 缓存命中(hash匹配)! %s -> %s",
                        torrent_name[:40],
                        result.indexer_name,
                    )
                else:
                    logger.debug(
                        "  [Jackett] 缓存命中(跳过): %s -> %s 已记录不匹配",
                        torrent_name[:40],
                        result.indexer_name,
                    )
                    return None

            if result.indexer_name:
                self.stats.indexer_hits[result.indexer_name] = (
                    self.stats.indexer_hits.get(result.indexer_name, 0) + 1
                )

            torrent_data, error_reason = await http_client.try_download(
                url,
                site_name=result.indexer_name,
            )
            if not torrent_data:
                self.stats.failed_count += 1
                return None

            downloaded_hash = _parse_pieces_hash(torrent_data)
            if not downloaded_hash:
                self._dl_cache[url] = ""
                self._cache_dirty = True
                self.stats.failed_count += 1
                return None

            if downloaded_hash != target_pieces_hash:
                self._dl_cache[url] = downloaded_hash
                self._cache_dirty = True
                self.stats.failed_count += 1
                return None

            self._dl_cache[url] = downloaded_hash
            self._cache_dirty = True
            self.stats.downloaded_count += 1
            logger.info(
                "  [Jackett] *** pieces_hash 匹配! *** %s -> %s",
                torrent_name[:40],
                result.indexer_name,
            )
            return (torrent_data, result)

        for result in matched:
            item = await download_one(result)
            if item:
                downloaded.append(item)
                break

        return downloaded
