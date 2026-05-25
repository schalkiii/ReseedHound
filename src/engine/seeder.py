import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from ..network.client import SiteClient
from ..storage.cache import TorrentCache
from ..storage.config import Config
from .downloader import DownloaderBase, create_downloader
from .parser import TorrentParser

logger = logging.getLogger("seedhound")


@dataclass
class ReseedStats:
    total_torrents: int = 0
    new_torrents: int = 0
    duplicate_count: int = 0
    tracker_skip_count: int = 0
    matched_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    site_details: dict = field(default_factory=dict)
    site_match_counts: dict = field(default_factory=dict)
    site_succeeded: dict = field(default_factory=dict)
    site_failed: dict = field(default_factory=dict)
    failed_sites: list = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time

    @property
    def duration_str(self) -> str:
        secs = int(self.duration_seconds)
        if secs < 60:
            return f"{secs}秒"
        mins, secs = divmod(secs, 60)
        return f"{mins}分{secs}秒"


class ReseedEngine:
    def __init__(self, config: Config):
        self._config = config
        self._cache = TorrentCache(config.db_path)
        self._client = SiteClient(
            concurrency=config.global_config.get("concurrency", 20),
            timeout=config.global_config.get("request_timeout", 15),
            retry_count=config.global_config.get("retry_count", 2),
            retry_delay=config.global_config.get("retry_delay", 2.0),
        )
        self._downloader: Optional[DownloaderBase] = None
        self._batch_size = config.global_config.get("batch_size", 100)
        self._stats = ReseedStats()

    async def start(self):
        await self._cache.init()
        await self._client.start()
        dl_config = self._config.downloader_config
        self._downloader = create_downloader(dl_config)
        if not await self._downloader.connect():
            raise RuntimeError("下载器连接失败")

    async def close(self):
        if self._downloader:
            await self._downloader.close()
        await self._client.close()

    async def run(self, dry_run: bool = False, site_name: str = None) -> ReseedStats:
        self._stats.start_time = time.time()
        logger.info("=" * 60)
        log_parts = ["SeedHound 辅种引擎启动"]
        if dry_run:
            log_parts.append("[演练模式] 只查询不添加")
        if site_name:
            log_parts.append(f"[站点指定] 仅处理: {site_name}")
        logger.info("  ".join(log_parts))
        logger.info("=" * 60)

        await self._phase_scan()

        sites = self._config.sites
        if site_name:
            site_names = [s.strip().lower() for s in site_name.split(",")]
            sites = [s for s in sites if s.get("name", "").lower() in site_names]
            if not sites:
                available = ", ".join(s.get("name", "?") for s in self._config.sites)
                logger.error("未找到指定站点: %s (可用: %s)", site_name, available)
                return self._stats
            found = {s.get("name", "").lower() for s in sites}
            missing = [n for n in site_names if n not in found]
            if missing:
                logger.warning("以下站点未在配置中找到: %s", ", ".join(missing))
        total_for_query = len(self._torrents)
        logger.info(
            "启用站点: %d 个, 待查询种子: %d 个 (其中新增 %d 个)",
            len(sites), total_for_query, self._stats.new_torrents,
        )

        await self._phase_query_sites(sites)

        if not dry_run:
            await self._phase_add_torrents(sites)
        else:
            logger.info("[演练模式] 跳过阶段3（添加种子到下载器）")

        self._stats.end_time = time.time()
        logger.info("=" * 60)
        logger.info(
            "辅种完成: 扫描=%d, 去重=%d, 新=%d, 匹配=%d, 追踪器跳过=%d, 成功=%d, 失败=%d, 耗时=%s",
            self._stats.total_torrents,
            self._stats.duplicate_count,
            self._stats.new_torrents,
            self._stats.matched_count,
            self._stats.tracker_skip_count,
            self._stats.succeeded_count,
            self._stats.failed_count,
            self._stats.duration_str,
        )
        logger.info("=" * 60)

        return self._stats

    async def _phase_scan(self):
        logger.info("[阶段1] 扫描本地种子文件...")
        torrent_dir = self._config.downloader_config.get("torrent_dir", "")
        torrents = await TorrentParser.scan_directory(torrent_dir)
        self._stats.total_torrents = len(torrents)

        uncached = await self._cache.filter_uncached(torrents)
        self._stats.new_torrents = len(uncached)
        if uncached:
            await self._cache.add_batch(uncached)

        seen = {}
        unique_torrents = []
        for t in torrents:
            ph = t["pieces_hash"]
            if ph not in seen:
                seen[ph] = t
                unique_torrents.append(t)
        duplicate_count = len(torrents) - len(unique_torrents)
        self._stats.duplicate_count = duplicate_count

        self._qb_pieces_hashes = {t["pieces_hash"] for t in unique_torrents}
        self._qb_announces = {
            t["pieces_hash"]: t.get("announce", "")
            for t in unique_torrents
        }

        logger.info(
            "扫描完成: 总计=%d, 新增=%d, 已缓存=%d, 去重=%d, 待查询=%d",
            len(torrents), len(uncached), len(torrents) - len(uncached),
            duplicate_count, len(unique_torrents),
        )
        self._torrents = unique_torrents

    async def _phase_query_sites(self, sites: list[dict]):
        logger.info("[阶段2] 并发查询各站点...")
        pieces_hashes = [t["pieces_hash"] for t in self._torrents]

        site_sem = asyncio.Semaphore(
            self._config.global_config.get("concurrency", 20)
        )

        async def query_one_site(site: dict):
            async with site_sem:
                site_name = site["name"]
                logger.info("查询站点: %s", site_name)

                batches = [
                    pieces_hashes[i:i + self._batch_size]
                    for i in range(0, len(pieces_hashes), self._batch_size)
                ]

                results = await self._client.query_site_batched(site, batches)
                self._stats.site_details[site_name] = len(results)
                logger.info("站点 %s 匹配到 %d 个种子", site_name, len(results))
                if not results:
                    self._stats.failed_sites.append(site_name)
                return (site, results)

        tasks = [query_one_site(site) for site in sites]
        site_results = await asyncio.gather(*tasks, return_exceptions=True)

        self._pieces_hash_index = {t["pieces_hash"]: t for t in self._torrents}
        self._match_map: dict[str, list[tuple[str, int, str]]] = {}

        for result in site_results:
            if isinstance(result, Exception):
                logger.error("站点查询异常: %s", result)
                continue
            site, matches = result
            if not matches:
                continue
            for pieces_hash, torrent_id in matches:
                if pieces_hash in self._pieces_hash_index:
                    if pieces_hash not in self._match_map:
                        self._match_map[pieces_hash] = []
                    self._match_map[pieces_hash].append(
                        (site["name"], torrent_id, site["url"])
                    )

        self._stats.matched_count = len(self._match_map)
        logger.info("全局匹配: %d 个种子可跨站辅种", self._stats.matched_count)

        site_match_counts = {}
        for _ph, matches in self._match_map.items():
            for site_name, _tid, _surl in matches:
                site_match_counts[site_name] = site_match_counts.get(site_name, 0) + 1
        self._stats.site_match_counts = site_match_counts

    async def _phase_add_torrents(self, sites: list[dict]):
        logger.info("[阶段3] 添加种子到下载器...")
        if not self._match_map:
            logger.info("没有可辅种的种子")
            return

        add_sem = asyncio.Semaphore(10)
        site_configs = {s["name"]: s for s in sites}

        domain_to_site = {}
        for s in sites:
            domain = urlparse(s["url"]).netloc
            if domain:
                domain_to_site[domain] = s["name"]

        qb_site_seeding = {}
        for ph, announce_url in self._qb_announces.items():
            if announce_url:
                domain = urlparse(announce_url).netloc
                site_name = domain_to_site.get(domain)
                if site_name:
                    qb_site_seeding.setdefault(ph, set()).add(site_name)

        qb_seeding_count = sum(len(v) for v in qb_site_seeding.values())
        if qb_seeding_count:
            logger.info(
                "qB 中已有辅种记录（基于 tracker 解析）: %d 条 (涉及 %d 个唯一种子)",
                qb_seeding_count, len(qb_site_seeding),
            )

        combos_to_check = []
        for pieces_hash, matches in self._match_map.items():
            for site_name, torrent_id, _site_url in matches:
                combos_to_check.append((pieces_hash, site_name, torrent_id))
        reseeded_set = await self._cache.get_reseeded_combos(combos_to_check)

        valid_reseeded = {
            (ph, sn, tid) for (ph, sn, tid) in reseeded_set
            if ph in self._qb_pieces_hashes
        }
        stale_count = len(reseeded_set) - len(valid_reseeded)
        if valid_reseeded:
            logger.info(
                "已有辅种记录: %d 条有效 (qB中仍存在), %d 条已过期 (qB中已删除, 将重新添加)",
                len(valid_reseeded), stale_count,
            )
        if stale_count:
            logger.info("%d 条记录对应的种子已不在qB中（将重新尝试添加）", stale_count)

        async def add_one(pieces_hash: str, matches: list):
            async with add_sem:
                entry = self._pieces_hash_index.get(pieces_hash)
                if not entry:
                    return

                dl_info = await self._downloader.get_torrent_info(entry["info_hash"])
                if not dl_info:
                    return

                if dl_info["state"] == "downloading":
                    return

                skip_hash = self._config.downloader_config.get("skip_hash_check", True)
                auto_start = self._config.downloader_config.get("auto_start", False)
                torrent_tag = self._config.downloader_config.get("tag", "SeedHound")

                for site_name, torrent_id, site_url in matches:
                    if (pieces_hash, site_name, torrent_id) in valid_reseeded:
                        continue
                    if site_name in qb_site_seeding.get(pieces_hash, set()):
                        self._stats.tracker_skip_count += 1
                        continue

                    site_cfg = site_configs.get(site_name)
                    if not site_cfg:
                        continue
                    dl_url = (
                        f"{site_url}/download.php?id={torrent_id}"
                        f"&passkey={site_cfg['passkey']}"
                    )

                    torrent_data = await self._client.get_bytes(dl_url)
                    if not torrent_data:
                        self._stats.failed_count += 1
                        continue

                    parsed = TorrentParser.parse_bytes(torrent_data)
                    if not parsed:
                        self._stats.failed_count += 1
                        logger.warning(
                            "下载的不是有效torrent文件: %s (站点: %s)",
                            entry["file_name"], site_name,
                        )
                        continue
                    new_info_hash = parsed["info_hash"]

                    success = await self._downloader.add_torrent(
                        torrent_files=torrent_data,
                        save_path=dl_info["save_path"],
                        skip_hash_check=skip_hash,
                        paused=not auto_start,
                        tag=torrent_tag,
                    )
                    if success:
                        self._stats.succeeded_count += 1
                        self._stats.site_succeeded[site_name] = (
                            self._stats.site_succeeded.get(site_name, 0) + 1
                        )
                        await self._cache.add_reseed_record(
                            pieces_hash, site_name, torrent_id,
                        )
                        logger.info(
                            "辅种成功: %s -> %s (id=%d)",
                            entry["file_name"], site_name, torrent_id,
                        )

                        if new_info_hash:
                            await asyncio.sleep(0.3)
                            ti = await self._downloader.get_torrent_info(
                                new_info_hash
                            )
                            if ti and ti.get("state") in (
                                "pausedUP", "completed",
                            ):
                                await self._downloader.resume_torrent(
                                    new_info_hash
                                )
                                logger.info(
                                    "自动开始: %s (%s, 已完成)",
                                    entry["file_name"], site_name,
                                )
                    else:
                        self._stats.failed_count += 1
                        self._stats.site_failed[site_name] = (
                            self._stats.site_failed.get(site_name, 0) + 1
                        )
                    await asyncio.sleep(0.3)

        tasks = [
            add_one(ph, matches)
            for ph, matches in self._match_map.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        if self._stats.tracker_skip_count:
            logger.info(
                "基于 tracker 跳过 (qB中已在同站辅种): %d 条",
                self._stats.tracker_skip_count,
            )
