import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils._bencode import bencode  # noqa: E402


def _make_torrent_bytes(name: str, pieces_count: int = 20) -> bytes:
    import hashlib
    return bencode({
        b"announce": b"http://example.com/announce",
        b"info": {
            b"length": 1234,
            b"name": name.encode("utf-8"),
            b"piece length": 65536,
            b"pieces": hashlib.sha1(str(pieces_count).encode()).digest(),
        },
    })


@pytest.mark.asyncio
async def test_cache_init():
    from src.storage.cache import TorrentCache

    db_path = "data/test_cache.db"
    cache = TorrentCache(db_path)
    await cache.init()
    stats = await cache.get_stats()
    assert stats["cached_torrents"] >= 0
    print(f"PASS: cache init, stats={stats}")

    await cache.clear_cache()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.mark.asyncio
async def test_cache_filter():
    from src.storage.cache import TorrentCache

    db_path = "data/test_filter.db"
    cache = TorrentCache(db_path)
    await cache.init()

    items = [
        {"pieces_hash": "a" * 40, "info_hash": "b" * 40, "file_name": "test1.torrent"},
        {"pieces_hash": "c" * 40, "info_hash": "d" * 40, "file_name": "test2.torrent"},
    ]

    # first pass: all should be uncached
    uncached = await cache.filter_uncached(items)
    assert len(uncached) == 2
    print("PASS: cache filter (first pass): 2 uncached")

    # add to cache
    await cache.add_batch(items)

    # second pass: all should be cached
    uncached = await cache.filter_uncached(items)
    assert len(uncached) == 0
    print("PASS: cache filter (second pass): 0 uncached")

    await cache.clear_cache()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.mark.asyncio
async def test_parser_scan():
    from src.engine.parser import TorrentParser

    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(5):
            torrent_data = _make_torrent_bytes(f"test_{i}.mkv", i)
            file_path = Path(tmpdir) / f"test_{i}.torrent"
            file_path.write_bytes(torrent_data)

        results = await TorrentParser.scan_directory(tmpdir, max_concurrent=10)
        assert len(results) == 5
        for r in results:
            assert len(r["info_hash"]) == 40
            assert len(r["pieces_hash"]) == 40
        print(f"PASS: parser scan: {len(results)} torrents parsed")


@pytest.mark.asyncio
async def test_config():
    from src.storage.config import Config

    config = Config("config.yaml", "sites.yaml")
    assert config.get("global.concurrency") == 20
    assert config.get("global.batch_size") == 100
    assert config.get("db.path") == "data/cache.db"
    assert config.get("log.level") == "INFO"
    print("PASS: config dot-notation access")


async def main():
    await test_cache_init()
    await test_cache_filter()
    await test_parser_scan()
    await test_config()
    print()
    print("=" * 50)
    print("  所有异步集成测试通过!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
