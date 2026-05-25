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

            announce = torrent.get(b"announce")
            if isinstance(announce, bytes):
                result["announce"] = announce.decode("utf-8", errors="replace")

            return result
        except Exception:
            return None

    @staticmethod
    async def scan_directory(
        torrent_dir: str,
        max_concurrent: int = 100,
    ) -> list[dict]:
        dir_path = Path(torrent_dir)
        if not dir_path.exists():
            raise FileNotFoundError(f"种子目录不存在: {torrent_dir}")

        files = list(dir_path.glob("*.torrent"))
        logger.info("发现 %d 个种子文件", len(files))

        sem = asyncio.Semaphore(max_concurrent)

        async def parse_one(path: Path) -> Optional[dict]:
            async with sem:
                return await asyncio.to_thread(TorrentParser.parse_file, path)

        tasks = [parse_one(f) for f in files]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]
