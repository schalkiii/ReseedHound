import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("reseed_puppy")


class DownloaderBase(ABC):
    @abstractmethod
    async def connect(self) -> bool:
        ...

    @abstractmethod
    async def get_torrent_info(self, info_hash: str) -> Optional[dict]:
        ...

    @abstractmethod
    async def add_torrent(
        self,
        download_url: str,
        save_path: str,
        skip_hash_check: bool = True,
        paused: bool = True,
        tag: str = "",
    ) -> bool:
        ...

    @abstractmethod
    async def close(self):
        ...


class QbittorrentDownloader(DownloaderBase):
    def __init__(self, host: str, port: int, username: str, password: str):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client = None

    async def connect(self) -> bool:
        import qbittorrentapi

        try:
            self._client = qbittorrentapi.Client(
                host=f"{self._host}:{self._port}",
                username=self._username,
                password=self._password,
            )
            await asyncio.get_event_loop().run_in_executor(
                None, self._client.auth_log_in,
            )
            logger.info("qBittorrent 连接成功")
            return True
        except Exception as e:
            logger.error("qBittorrent 连接失败: %s", e)
            return False

    async def get_torrent_info(self, info_hash: str) -> Optional[dict]:
        try:
            torrents = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.torrents_info(torrent_hashes=info_hash),
            )
            if torrents:
                t = torrents[0]
                return {
                    "save_path": t.save_path,
                    "state": t.state,
                    "name": t.name,
                }
        except Exception as e:
            logger.debug("查询种子信息失败 %s: %s", info_hash, e)
        return None

    async def add_torrent(
        self,
        download_url: str,
        save_path: str,
        skip_hash_check: bool = True,
        paused: bool = True,
        tag: str = "",
    ) -> bool:
        try:
            kwargs = dict(
                urls=download_url,
                save_path=save_path,
                is_skip_checking=skip_hash_check,
                is_paused=paused,
            )
            if tag:
                kwargs["tags"] = tag
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.torrents_add(**kwargs),
            )
            return result == "Ok."
        except Exception as e:
            logger.error("添加种子失败: %s", e)
            return False

    async def close(self):
        if self._client:
            self._client = None


class TransmissionDownloader(DownloaderBase):
    def __init__(self, host: str, port: int, username: str, password: str):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client = None

    async def connect(self) -> bool:
        try:
            from transmission_rpc import Client
            self._client = Client(
                host=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
            )
            logger.info("Transmission 连接成功")
            return True
        except Exception as e:
            logger.error("Transmission 连接失败: %s", e)
            return False

    async def get_torrent_info(self, info_hash: str) -> Optional[dict]:
        try:
            torrent = self._client.get_torrent(torrent_id=info_hash)
            return {
                "save_path": torrent.download_dir,
                "state": torrent.status,
                "name": torrent.name,
            }
        except Exception:
            return None

    async def add_torrent(
        self,
        download_url: str,
        save_path: str,
        skip_hash_check: bool = True,
        paused: bool = True,
        tag: str = "",
    ) -> bool:
        try:
            self._client.add_torrent(
                torrent=download_url,
                download_dir=save_path,
                paused=paused,
            )
            return True
        except Exception as e:
            logger.error("Transmission 添加种子失败: %s", e)
            return False

    async def close(self):
        self._client = None


def create_downloader(config: dict) -> DownloaderBase:
    dl_type = config.get("type", "qbittorrent")
    host = config["host"]
    port = int(config.get("port", 8080))
    username = config.get("username", "admin")
    password = config.get("password", "adminadmin")

    if dl_type == "qbittorrent":
        return QbittorrentDownloader(host, port, username, password)
    if dl_type == "transmission":
        return TransmissionDownloader(host, port, username, password)
    raise ValueError(f"不支持的下载器类型: {dl_type}")
