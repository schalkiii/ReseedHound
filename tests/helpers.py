"""测试用固定数据与辅助函数。

这里的 golden 字节串/哈希是手工写出的外部值，不经过项目的 bencode / hash
实现推导；所有配置都现场生成，绝不读取工作区含真实凭据的 config.yaml / sites.yaml。
"""
import hashlib
from pathlib import Path

import yaml

from src.utils._bencode import bencode

INFO_GOLDEN_BYTES = (
    b"d6:lengthi1234e4:name8:test.dat12:piece lengthi65536e6:pieces20:"
    + b"\x00" * 20
    + b"e"
)
INFO_HASH_GOLDEN = "35471bc58ffcae4564c40faf40ff75dbbf97c36a"
PIECES_HASH_GOLDEN = "6768033e216468247bd031a0a2d9876d79818f8f"

SAMPLE_INFO = {
    b"announce": b"http://example.com/announce",
    b"info": {
        b"length": 1234,
        b"name": b"test.dat",
        b"piece length": 65536,
        b"pieces": b"\x00" * 20,
    },
}


def make_torrent_bytes(name: str = "test.dat", pieces: bytes = None) -> bytes:
    info = {
        b"length": 1234,
        b"name": name.encode("utf-8"),
        b"piece length": 65536,
        b"pieces": b"\x00" * 20 if pieces is None else pieces,
    }
    return bencode({b"announce": b"http://example.com/announce", b"info": info})


def make_varied_torrent_bytes(seed: int) -> bytes:
    pieces = hashlib.sha1(str(seed).encode()).digest()
    return make_torrent_bytes(f"test_{seed}.mkv", pieces)


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def minimal_config_dicts(tmp_path: Path) -> tuple[dict, dict]:
    """返回 (config.yaml 内容, sites.yaml 内容)，数值刻意与 DEFAULTS 不同。"""
    cfg = {
        "global": {"concurrency": 7, "batch_size": 13, "api_timeout": 21},
        "db": {"path": str(tmp_path / "cache.db")},
        "log": {"level": "DEBUG", "file": str(tmp_path / "reseed.log")},
        "scheduler": {
            "enabled": False,
            "interval_minutes": 1,
            "startup_delay": 0,
            "run_on_start": True,
        },
        "downloader": {
            "type": "qbittorrent",
            "host": "127.0.0.1",
            "port": 9091,
            "username": "u",
            "password": "p",
            "torrent_dir": str(tmp_path / "torrents"),
            "tag": "UnitTest",
        },
        "notify": {"feishu": {"webhook_url": "", "secret": ""}},
        "jackett": {"enabled": False, "url": "http://localhost:9117", "api_key": ""},
        "cookiecloud": {"enabled": False},
    }
    sites = {"sites": [
        {
            "name": "siteA",
            "url": "https://a.example.com",
            "api_url": "https://a.example.com/api",
            "passkey": "pa",
            "enabled": True,
        },
        {
            "name": "siteB",
            "url": "https://b.example.com",
            "api_url": "https://b.example.com/api",
            "passkey": "pb",
            "enabled": False,
        },
    ]}
    return cfg, sites
