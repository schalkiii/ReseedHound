"""下载器：工厂、双下载器主目标、qB/Transmission 接口契约与添加流程。"""
import inspect

import pytest

from src.engine.downloader import (
    DownloaderBase,
    QbittorrentDownloader,
    TransmissionDownloader,
    create_downloader,
)
from src.storage.config import Config
from tests.helpers import write_yaml


def test_create_downloader_types():
    assert isinstance(
        create_downloader({"type": "qbittorrent", "host": "h"}), QbittorrentDownloader
    )
    assert isinstance(
        create_downloader({"type": "transmission", "host": "h"}),
        TransmissionDownloader,
    )


def test_create_downloader_missing_host_is_readable():
    with pytest.raises(ValueError, match="缺少 host"):
        create_downloader({"type": "qbittorrent"})


def test_create_downloader_unknown_type():
    with pytest.raises(ValueError, match="不支持的下载器类型"):
        create_downloader({"type": "foo", "host": "h"})


def test_create_downloader_has_no_hardcoded_password():
    assert create_downloader({"host": "h"})._password == ""


def test_dual_downloader_destination_is_primary(config_files):
    cfg_path, sites_path, cfg, _ = config_files
    cfg["downloader"] = {
        "source": {
            "type": "qbittorrent", "host": "src", "port": 8080,
            "torrent_dir": "/src",
        },
        "destination": {"type": "transmission", "host": "dst", "port": 9091},
    }
    write_yaml(cfg_path, cfg)
    c = Config(str(cfg_path), str(sites_path))
    assert isinstance(
        create_downloader(c.destination_downloader_config), TransmissionDownloader
    )
    assert create_downloader(c.destination_downloader_config)._host == "dst"
    # Q-27 回归：把双下载器整块当成单下载器配置时必须给出可读报错而不是 KeyError
    with pytest.raises(ValueError, match="缺少 host"):
        create_downloader(c.downloader_config)


def test_add_torrent_signature_contract():
    base = list(inspect.signature(DownloaderBase.add_torrent).parameters)
    for cls in (QbittorrentDownloader, TransmissionDownloader):
        assert list(inspect.signature(cls.add_torrent).parameters) == base, cls.__name__


class FakeTransmissionClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.started = None

    def add_torrent(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("rpc down")
        return object()

    def start_torrent(self, info_hash):
        self.started = info_hash

    def get_torrents(self):
        return [type("T", (), {"hashString": h})() for h in ("aaa", "bbb")]

    def get_torrent(self, torrent_id):
        return type("T", (), {
            "download_dir": "/data", "status": "completed", "name": "n",
        })()


@pytest.mark.asyncio
async def test_transmission_add_torrent_with_bytes():
    dl = TransmissionDownloader("h", 9091, "u", "p")
    dl._client = FakeTransmissionClient()
    data = b"d8:announce3:foo4:infod4:name3:baree"
    assert await dl.add_torrent(
        torrent_files=data, download_url="https://ignored",
        save_path="/data", skip_hash_check=False, paused=True, tag="SeedHound",
    ) is True
    call = dl._client.calls[0]
    assert call["torrent"] == data  # bytes 优先于 URL
    assert call["download_dir"] == "/data"
    assert call["paused"] is True
    # Transmission 不支持这两个参数，必须显式忽略而不是误传
    assert "tags" not in call and "is_skip_checking" not in call


@pytest.mark.asyncio
async def test_transmission_add_torrent_with_url():
    dl = TransmissionDownloader("h", 9091, "u", "p")
    dl._client = FakeTransmissionClient()
    assert await dl.add_torrent(download_url="https://x/y.torrent") is True
    assert dl._client.calls[0]["torrent"] == "https://x/y.torrent"


@pytest.mark.asyncio
async def test_transmission_add_torrent_requires_payload():
    dl = TransmissionDownloader("h", 9091, "u", "p")
    dl._client = FakeTransmissionClient()
    assert await dl.add_torrent() is False
    assert dl._client.calls == []


@pytest.mark.asyncio
async def test_transmission_add_torrent_handles_rpc_error():
    dl = TransmissionDownloader("h", 9091, "u", "p")
    dl._client = FakeTransmissionClient(fail=True)
    assert await dl.add_torrent(torrent_files=b"x") is False


@pytest.mark.asyncio
async def test_transmission_other_methods():
    dl = TransmissionDownloader("h", 9091, "u", "p")
    dl._client = FakeTransmissionClient()
    assert await dl.get_torrent_info("h1") == {
        "save_path": "/data", "state": "completed", "name": "n",
    }
    assert await dl.get_all_info_hashes() == {"aaa", "bbb"}
    assert await dl.resume_torrent("h1") is True
    assert dl._client.started == "h1"
    await dl.close()
    assert dl._client is None


class FakeQbittorrentClient:
    def __init__(self, result="Ok."):
        self.result = result
        self.kwargs = None

    def torrents_add(self, **kwargs):
        self.kwargs = kwargs
        return self.result


@pytest.mark.asyncio
async def test_qb_add_torrent_sends_both_pause_flags():
    dl = QbittorrentDownloader("h", 8080, "u", "p")
    dl._client = FakeQbittorrentClient()
    assert await dl.add_torrent(
        torrent_files=b"x", save_path="/s", skip_hash_check=True,
        paused=False, tag="T",
    ) is True
    kw = dl._client.kwargs
    # qbittorrent-api 会折叠 is_paused=False，必须同时传 is_stopped
    assert kw["is_paused"] is False and kw["is_stopped"] is False
    assert kw["is_skip_checking"] is True
    assert kw["tags"] == "T" and kw["torrent_files"] == b"x"


@pytest.mark.asyncio
async def test_qb_add_torrent_failure_is_visible():
    dl = QbittorrentDownloader("h", 8080, "u", "p")
    dl._client = FakeQbittorrentClient({"success_count": 0, "failure_count": 1})
    assert await dl.add_torrent(torrent_files=b"x") is False
