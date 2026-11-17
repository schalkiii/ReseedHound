import asyncio
import logging
from pathlib import Path
from typing import Optional

from ..utils._bencode import bdecode
from ..utils.hash import compute_info_hash, compute_pieces_hash

logger = logging.getLogger("seedhound")


class TorrentParser:
    @staticmethod
    def parse_file(file_path: Path) -> Optional[dict]:
        try:
            data = file_path.read_bytes()
            return TorrentParser.parse_bytes(
                data, file_name=file_path.name, file_path=str(file_path)
            )
        except Exception:
            logger.debug("解析种子失败: %s", file_path.name)
            return None

    @staticmethod
    def parse_bytes(data: bytes, file_name: str = "", file_path: str = "") -> Optional[dict]:
        try:
            torrent = bdecode(data)
            if not isinstance(torrent, dict) or b"info" not in torrent:
                return None
            info = torrent[b"info"]
            if not isinstance(info, dict) or b"pieces" not in info:
                return None

            result = {
                "info_hash": compute_info_hash(info),
                "pieces_hash": compute_pieces_hash(info[b"pieces"]),
                "file_name": file_name,
                "file_path": file_path,
            }

            torrent_name = info.get(b"name")
            if isinstance(torrent_name, bytes):
                result["torrent_name"] = torrent_name.decode("utf-8", errors="replace")
            elif isinstance(torrent_name, str):
                result["torrent_name"] = torrent_name

            if b"length" in info:
                result["total_size"] = info[b"length"]
            elif b"files" in info:
                total = 0
                for f in info[b"files"]:
                    if isinstance(f, dict) and b"length" in f:
                        total += f[b"length"]
                result["total_size"] = total

            announce = torrent.get(b"announce")
            if isinstance(announce, bytes):
                result["announce"] = announce.decode("utf-8", errors="replace")
            elif isinstance(announce, str):
                result["announce"] = announce

            announce_list = torrent.get(b"announce-list")
            if announce_list is not None:
                urls = []
                for tier in announce_list:
                    if isinstance(tier, (list, tuple)):
                        for url in tier:
                            if isinstance(url, bytes):
                                urls.append(url.decode("utf-8", errors="replace"))
                            elif isinstance(url, str):
                                urls.append(url)
                if urls:
                    result["announce_list"] = urls

            return result
        except Exception as exc:
            logger.debug("解析种子数据失败 %s: %s", file_name or "<bytes>", exc)
            return None

    @staticmethod
    async def scan_directory(
        torrent_dir: str,
        max_concurrent: int = 100,
        cache=None,
    ) -> list[dict]:
        """扫描目录下的 .torrent。

        `cache`（TorrentCache）非空时启用文件级解析缓存：(path, mtime, size) 命中
        即重建解析结果，只对新增/变更文件做读盘解析（与 Rust 版同口径）。
        """
        dir_path = Path(torrent_dir)
        if not dir_path.exists():
            raise FileNotFoundError(f"种子目录不存在: {torrent_dir}")

        files = [p for p in dir_path.glob("*.torrent") if p.is_file()]
        logger.info("发现 %d 个种子文件", len(files))

        identities: list[tuple[str, int, int]] = []
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            identities.append((str(p), int(st.st_mtime), st.st_size))

        cached: dict = {}
        if cache is not None:
            cached = await cache.lookup_file_cache(identities)
        cache_hits = len(cached)
        misses = [(p, m, s) for (p, m, s) in identities if p not in cached]
        miss_total = len(misses)
        if cache is not None:
            logger.info("[阶段1] 文件级解析缓存: 命中 %d（跳过解析）, 解析 %d", cache_hits, miss_total)

        sem = asyncio.Semaphore(max_concurrent)
        done = 0

        async def parse_one(path_str: str) -> Optional[dict]:
            nonlocal done
            async with sem:
                result = await asyncio.to_thread(TorrentParser.parse_file, Path(path_str))
                done += 1
                if done % 5000 == 0 or done == miss_total:
                    logger.info(
                        "[阶段1] 解析进度 %d/%d（缓存命中 %d）", done, miss_total, cache_hits
                    )
                return result

        tasks = [parse_one(p) for p, _, _ in misses]
        results = await asyncio.gather(*tasks)

        torrents: list[dict] = []
        store_entries: list[tuple[str, int, int, dict]] = []
        for (path_str, mtime, size), result in zip(misses, results):
            if result is not None:
                store_entries.append((path_str, mtime, size, result))
                torrents.append(result)
        if cache is not None:
            if store_entries:
                await cache.store_file_cache(store_entries)
            pruned = await cache.prune_file_cache({p for p, _, _ in identities})
            if pruned:
                logger.info("文件解析缓存裁剪 %d 行（对应文件已删除）", pruned)
        torrents.extend(cached.values())
        return torrents
