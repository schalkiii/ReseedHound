import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from ..network.client import DeadTorrentCache, SiteClient
from ..storage.cache import TorrentCache
from ..storage.config import Config
from .downloader import DownloaderBase, create_downloader
from .jackett import JackettClient
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
    dead_count: int = 0
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
        self._dead_cache = DeadTorrentCache()

        site_cookies = {}
        host_intervals = {}
        for s in config.sites:
            cookie = s.get("cookie") or s.get("cookies")
            if cookie and s.get("url"):
                site_cookies[s["url"]] = cookie
            site_interval = s.get("download_interval")
            if site_interval and s.get("url"):
                from yarl import URL

                host = URL(s["url"]).host
                if host:
                    host_intervals[host] = float(site_interval)

        self._client = SiteClient(
            concurrency=config.global_config.get("concurrency", 20),
            timeout=config.global_config.get("api_timeout", 10),
            download_timeout=config.global_config.get("download_timeout", 30),
            retry_count=config.global_config.get("retry_count", 2),
            retry_delay=config.global_config.get("retry_delay", 2.0),
            download_interval=config.global_config.get("download_interval", 5.0),
            host_intervals=host_intervals,
            dead_cache=self._dead_cache,
            site_cookies=site_cookies,
        )
        self._downloader: Optional[DownloaderBase] = None
        self._src_downloader: Optional[DownloaderBase] = None
        self._batch_size = config.global_config.get("batch_size", 100)
        self._stats = ReseedStats()

        self._jackett: Optional[JackettClient] = None
        self._use_jackett = config.jackett_enabled
        mode = config.operation_mode
        if self._use_jackett:
            jc = config.jackett_config
            self._jackett = JackettClient(
                base_url=config.jackett_url,
                api_key=config.jackett_api_key,
                timeout=jc.get("timeout", 30),
                concurrency=jc.get("concurrency", 5),
                retry_count=jc.get(
                    "retry_count", config.global_config.get("retry_count", 2)
                ),
                retry_delay=jc.get(
                    "retry_delay", config.global_config.get("retry_delay", 2.0)
                ),
                search_interval=jc.get("search_interval", 1.0),
            )
        self._mode = mode if self._use_jackett else "pieces_hash"

    async def start(self):
        await self._cache.init()
        await self._client.start()
        if self._jackett:
            await self._jackett.start()
            if not await self._jackett.test_connection():
                logger.warning("Jackett 连接失败，将仅使用 pieces_hash 模式")
                self._mode = "pieces_hash"
        dl_config = self._config.downloader_config
        self._downloader = create_downloader(dl_config)
        if not await self._downloader.connect():
            raise RuntimeError("下载器连接失败")

        if self._config.is_dual_downloader:
            src_cfg = self._config.source_downloader_config
            self._src_downloader = create_downloader(src_cfg)
            if not await self._src_downloader.connect():
                logger.warning("源下载器连接失败，将使用本地文件路径扫描")
            else:
                logger.info(
                    "双下载器模式: 源=%s:%s → 目标=%s:%s",
                    src_cfg.get("host", "?"),
                    src_cfg.get("port", "?"),
                    dl_config.get("host", "?"),
                    dl_config.get("port", "?"),
                )

    async def close(self):
        if self._downloader:
            await self._downloader.close()
        if self._src_downloader:
            await self._src_downloader.close()
        if self._jackett:
            await self._jackett.close()
        await self._client.close()

    async def run(self, dry_run: bool = False, site_name: str = None) -> ReseedStats:
        self._stats.start_time = time.time()
        logger.info("=" * 60)
        log_parts = ["SeedHound 辅种引擎启动"]
        log_parts.append(f"[模式: {self._mode}]")
        if self._config.is_dual_downloader:
            log_parts.append("[双下载器]")
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

        use_pieces_hash = self._mode in ("pieces_hash", "both")
        use_jackett = self._mode in ("jackett", "both")

        if use_jackett and not dry_run:
            await self._phase_jackett_search()

        if use_pieces_hash:
            total_for_query = len(self._torrents)
            logger.info(
                "启用站点: %d 个, 待查询种子: %d 个 (其中新增 %d 个)",
                len(sites),
                total_for_query,
                self._stats.new_torrents,
            )

            await self._phase_query_sites(sites)

            if not dry_run:
                await self._phase_add_torrents(sites)
            else:
                logger.info("[演练模式] 跳过阶段3（添加种子到下载器）")
        elif not dry_run:
            logger.info("[Jackett模式] 跳过 pieces_hash 查询")

        self._stats.end_time = time.time()
        self._stats.dead_count = self._dead_cache.size()
        logger.info("=" * 60)
        logger.info(
            "辅种完成: 扫描=%d, 去重=%d, 新=%d, 匹配=%d, 追踪器跳过=%d, 成功=%d, 失败=%d, 死种=%d, 耗时=%s",
            self._stats.total_torrents,
            self._stats.duplicate_count,
            self._stats.new_torrents,
            self._stats.matched_count,
            self._stats.tracker_skip_count,
            self._stats.succeeded_count,
            self._stats.failed_count,
            self._stats.dead_count,
            self._stats.duration_str,
        )
        logger.info("=" * 60)

        return self._stats

    async def _phase_scan(self):
        logger.info("[阶段1] 扫描本地种子文件...")
        torrent_dir = self._config.source_downloader_config.get("torrent_dir", "")
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
            t["pieces_hash"]: t.get("announce", "") for t in unique_torrents
        }

        logger.info(
            "扫描完成: 总计=%d, 新增=%d, 已缓存=%d, 去重=%d, 待查询=%d",
            len(torrents),
            len(uncached),
            len(torrents) - len(uncached),
            duplicate_count,
            len(unique_torrents),
        )
        self._torrents = unique_torrents

    async def _phase_jackett_search(self):
        logger.info("[Jackett] 通过 Jackett 搜索辅种...")
        qb_info_hashes = await self._downloader.get_all_info_hashes()
        logger.info("qB 中现有种子: %d 个", len(qb_info_hashes))

        torrent_tag = self._config.destination_downloader_config.get("tag", "SeedHound")

        total = len(self._torrents)
        searched = 0
        matched_count = 0

        with_name_and_size = sum(
            1
            for t in self._torrents
            if t.get("file_name") and t.get("total_size", 0) > 0
        )
        without_size = sum(
            1
            for t in self._torrents
            if t.get("file_name") and t.get("total_size", 0) == 0
        )
        logger.info(
            "Jackett 待搜索: 有size=%d, 缺size=%d, 缺名称=%d, 总计=%d",
            with_name_and_size,
            without_size,
            total - with_name_and_size - without_size,
            total,
        )

        for idx, t in enumerate(self._torrents):
            search_name = t.get("torrent_name") or t.get("file_name", "")
            if search_name.endswith(".torrent"):
                search_name = search_name[:-8]
            torrent_size = t.get("total_size", 0)
            if not search_name or not torrent_size:
                continue

            if (idx + 1) % 10 == 0 or (idx + 1) == 1:
                jstats = self._jackett.stats
                logger.info(
                    "Jackett 进度 [%d/%d], 搜索=%d, 尺寸命中=%d, 下载=%d, 成功=%d",
                    idx + 1,
                    total,
                    searched,
                    matched_count,
                    jstats.downloaded_count,
                    self._stats.succeeded_count,
                )
            downloaded = []
            try:
                downloaded = await self._jackett.search_and_download(
                    torrent_name=search_name,
                    torrent_size=torrent_size,
                    target_pieces_hash=t.get("pieces_hash", ""),
                    target_info_hash=t.get("info_hash", ""),
                    http_client=self._client,
                )
            except Exception as exc:
                logger.debug("Jackett 搜索异常 [%s]: %s", search_name[:50], exc)
            searched += 1

            if len(downloaded) > 0:
                matched_count += 1

            for torrent_data, result in downloaded:
                parsed = TorrentParser.parse_bytes(torrent_data, file_name=search_name)
                if not parsed:
                    self._stats.failed_count += 1
                    continue

                new_info_hash = parsed["info_hash"]
                if new_info_hash in qb_info_hashes:
                    continue

                dl_info = await self._downloader.get_torrent_info(t["info_hash"])
                if not dl_info:
                    continue
                if dl_info["state"] == "downloading":
                    continue

                success = await self._downloader.add_torrent(
                    torrent_files=torrent_data,
                    save_path=dl_info["save_path"],
                    skip_hash_check=False,
                    paused=True,
                    tag=torrent_tag,
                )
                if success:
                    self._stats.succeeded_count += 1
                    indexer = result.indexer_name or "jackett"
                    self._stats.site_succeeded[indexer] = (
                        self._stats.site_succeeded.get(indexer, 0) + 1
                    )
                    self._stats.site_details[indexer] = (
                        self._stats.site_details.get(indexer, 0) + 1
                    )
                    self._stats.matched_count += 1
                    if new_info_hash:
                        qb_info_hashes.add(new_info_hash)
                    logger.info(
                        "Jackett 辅种成功: %s -> %s",
                        search_name[:60],
                        indexer,
                    )
                else:
                    self._stats.failed_count += 1

            if (idx + 1) % 50 == 0 or (idx + 1) == total:
                logger.info(
                    "Jackett 进度: %d/%d, 搜索=%d, 命中=%d, 辅种成功=%d",
                    idx + 1,
                    total,
                    searched,
                    matched_count,
                    self._stats.succeeded_count,
                )

        jstats = self._jackett.stats
        logger.info(
            "Jackett 搜索完成: 查询=%d, 匹配=%d, 下载=%d, 失败=%d",
            jstats.searched_count or searched,
            jstats.matched_count,
            jstats.downloaded_count,
            jstats.failed_count,
        )

    async def _phase_query_sites(self, sites: list[dict]):
        logger.info("[阶段2] 并发查询各站点...")
        pieces_hashes = [t["pieces_hash"] for t in self._torrents]

        domain_to_site = {}
        for s in sites:
            domain = urlparse(s["url"]).netloc
            if domain:
                domain_to_site[domain] = s["name"]

        site_exclude: dict[str, set[str]] = defaultdict(set)
        tracker_total = 0

        for ph, announce_url in self._qb_announces.items():
            if announce_url:
                domain = urlparse(announce_url).netloc
                site_name = domain_to_site.get(domain)
                if site_name:
                    site_exclude[site_name].add(ph)
                    tracker_total += 1

        self._stats.tracker_skip_count = tracker_total

        reseed_by_site = await self._cache.get_reseeded_hashes_by_site(pieces_hashes)
        reseed_total = 0
        for site_name, hashes in reseed_by_site.items():
            valid = {ph for ph in hashes if ph in self._qb_pieces_hashes}
            if valid:
                site_exclude[site_name] |= valid
                reseed_total += len(valid)

        total_skipped = tracker_total + reseed_total
        for site_name, exclude_set in site_exclude.items():
            skipped = len(exclude_set)
            if skipped > 0:
                logger.info(
                    "站点 %s 跳过 %d 个已知种子（已辅种/同站）",
                    site_name,
                    skipped,
                )
        if total_skipped:
            logger.info(
                "总计跳过 %d 个已知种子: %d tracker + %d 历史缓存 (%d 个站点)，减少 %d 次 API 请求",
                total_skipped,
                tracker_total,
                reseed_total,
                len(site_exclude),
                total_skipped,
            )

        site_sem = asyncio.Semaphore(self._config.global_config.get("concurrency", 20))

        async def query_one_site(site: dict):
            async with site_sem:
                site_name = site["name"]
                exclude = site_exclude.get(site_name, set())
                site_hashes = [ph for ph in pieces_hashes if ph not in exclude]

                logger.info("查询站点: %s (%d 个种子)", site_name, len(site_hashes))

                batches = [
                    site_hashes[i : i + self._batch_size]
                    for i in range(0, len(site_hashes), self._batch_size)
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

        add_sem = asyncio.Semaphore(20)
        no_access_sites: set[str] = set()
        site_serious_errors: dict[str, int] = {}
        MAX_SERIOUS_ERRORS = 3

        SERIOUS_ERRORS = frozenset(
            {
                "http_401",
                "http_403",
                "http_404",
                "http_410",
                "html_permission_denied",
                "html_torrent_deleted",
                "html_not_found",
                "html_forbidden",
                "html_auth_required",
                "auth_redirect",
            }
        )

        site_configs = {s["name"]: s for s in sites}

        qb_info_hashes = await self._downloader.get_all_info_hashes()
        logger.info("qB 中现有种子: %d 个", len(qb_info_hashes))

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

                skip_hash = self._config.destination_downloader_config.get(
                    "skip_hash_check", True
                )
                auto_start = self._config.destination_downloader_config.get(
                    "auto_start", True
                )
                torrent_tag = self._config.destination_downloader_config.get(
                    "tag", "SeedHound"
                )

                for site_name, torrent_id, site_url in matches:
                    if site_name in no_access_sites:
                        continue

                    site_cfg = site_configs.get(site_name)
                    if not site_cfg:
                        continue
                    dl_url = (
                        f"{site_url}/download.php?id={torrent_id}"
                        f"&passkey={site_cfg['passkey']}"
                    )

                    torrent_data, error_reason = await self._client.try_download(
                        dl_url,
                        site_name=site_name,
                        torrent_id=torrent_id,
                    )
                    if not torrent_data:
                        self._stats.failed_count += 1
                        if error_reason in SERIOUS_ERRORS:
                            site_serious_errors[site_name] = (
                                site_serious_errors.get(site_name, 0) + 1
                            )
                            if site_serious_errors[site_name] >= MAX_SERIOUS_ERRORS:
                                if site_name not in no_access_sites:
                                    no_access_sites.add(site_name)
                                    logger.warning(
                                        "站点 %s (%d次严重错误: %s)，跳过后续下载",
                                        site_name,
                                        site_serious_errors[site_name],
                                        error_reason,
                                    )
                        continue

                    parsed = TorrentParser.parse_bytes(
                        torrent_data,
                        file_name=entry.get("file_name", ""),
                    )
                    if not parsed:
                        self._stats.failed_count += 1
                        head_hex = (
                            torrent_data[:20].hex()
                            if len(torrent_data) >= 20
                            else torrent_data.hex()
                        )
                        logger.warning(
                            "下载的不是有效torrent文件: %s (站点: %s, 大小: %d, 前20字节: %s)",
                            entry.get("file_name", "?"),
                            site_name,
                            len(torrent_data),
                            head_hex,
                        )
                        continue
                    new_info_hash = parsed["info_hash"]

                    if new_info_hash in qb_info_hashes:
                        await self._cache.add_reseed_record(
                            pieces_hash,
                            site_name,
                            torrent_id,
                        )
                        continue
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
                            pieces_hash,
                            site_name,
                            torrent_id,
                        )
                        logger.info(
                            "辅种成功: %s -> %s (id=%d)",
                            entry["file_name"],
                            site_name,
                            torrent_id,
                        )

                        if new_info_hash:
                            qb_info_hashes.add(new_info_hash)
                            await asyncio.sleep(0.3)
                            ti = await self._downloader.get_torrent_info(new_info_hash)
                            if ti and ti.get("state") in (
                                "pausedUP",
                                "completed",
                            ):
                                await self._downloader.resume_torrent(new_info_hash)
                                logger.info(
                                    "自动开始: %s (%s, 已完成)",
                                    entry["file_name"],
                                    site_name,
                                )
                    else:
                        self._stats.failed_count += 1
                        self._stats.site_failed[site_name] = (
                            self._stats.site_failed.get(site_name, 0) + 1
                        )
                    await asyncio.sleep(0.3)

        tasks = [add_one(ph, matches) for ph, matches in self._match_map.items()]
        await asyncio.gather(*tasks, return_exceptions=True)

        if self._stats.tracker_skip_count:
            logger.info(
                "基于 tracker 在查询阶段跳过 (同站已知种子): %d 条",
                self._stats.tracker_skip_count,
            )
