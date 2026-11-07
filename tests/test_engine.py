"""引擎：运行模式切换、双下载器启动、三阶段主路径与演练模式。"""
from types import SimpleNamespace

import pytest

import src.engine.seeder as seeder_module
from src.engine.seeder import ReseedEngine, ReseedStats
from src.storage.config import Config
from tests.helpers import make_varied_torrent_bytes, write_yaml


class FakeCache:
    async def init(self):
        return None

    async def load_persistent_dead(self, min_days=15):
        return set()

    async def filter_uncached(self, items):
        return list(items)

    async def add_batch(self, entries):
        return None

    async def get_reseeded_hashes_by_site(self, hashes):
        return {}

    async def clear_dead_if_alive(self, site, torrent_id):
        return None

    async def add_reseed_record(self, pieces_hash, site, torrent_id):
        return None


class FakeHttpClient:
    async def start(self):
        return None

    async def close(self):
        return None


class FakeDeadCache:
    async def seed(self, entries):
        return None

    def size(self):
        return 0


def _engine(config):
    engine = ReseedEngine.__new__(ReseedEngine)
    engine._config = config
    engine._stats = ReseedStats()
    engine._batch_size = config.value("global", "batch_size")
    engine._cache = FakeCache()
    engine._dead_cache = FakeDeadCache()
    engine._client = FakeHttpClient()
    engine._jackett = None
    engine._mode = config.operation_mode
    engine._downloader = None
    engine._src_downloader = None
    engine._torrents = []
    engine._qb_announces = {}
    return engine


def _dual_config(config_files):
    cfg_path, sites_path, cfg, _ = config_files
    cfg["downloader"] = {
        "source": {
            "type": "qbittorrent", "host": "src", "port": 8080,
            "torrent_dir": "/src",
        },
        "destination": {
            "type": "transmission", "host": "dst", "port": 9091,
        },
    }
    write_yaml(cfg_path, cfg)
    return Config(str(cfg_path), str(sites_path))


def test_mode_falls_back_when_jackett_disabled(config_files, caplog):
    cfg_path, sites_path, cfg, _ = config_files
    cfg["global"]["mode"] = "jackett"
    cfg["jackett"]["enabled"] = False
    write_yaml(cfg_path, cfg)
    with caplog.at_level("WARNING", logger="seedhound"):
        engine = ReseedEngine(Config(str(cfg_path), str(sites_path)))
    assert engine._mode == "pieces_hash"
    assert "回退为 pieces_hash" in caplog.text


def test_mode_both_creates_jackett_client(config_files):
    cfg_path, sites_path, cfg, _ = config_files
    cfg["global"]["mode"] = "both"
    cfg["jackett"] = {
        "enabled": True, "url": "http://jackett:9117", "api_key": "key",
    }
    write_yaml(cfg_path, cfg)
    engine = ReseedEngine(Config(str(cfg_path), str(sites_path)))
    assert engine._mode == "both"
    assert engine._jackett is not None
    assert engine._jackett._base_url == "http://jackett:9117/api/v2.0"


@pytest.mark.asyncio
async def test_start_uses_destination_as_primary_downloader(config_files, monkeypatch):
    config = _dual_config(config_files)
    engine = _engine(config)
    created = []

    class FakeDownloader:
        def __init__(self, cfg):
            self.cfg = cfg

        async def connect(self):
            return True

        async def close(self):
            return None

    def fake_factory(cfg):
        created.append(cfg)
        return FakeDownloader(cfg)

    monkeypatch.setattr(seeder_module, "create_downloader", fake_factory)
    await engine.start()
    assert [c["host"] for c in created] == ["dst", "src"]
    assert engine._downloader.cfg["host"] == "dst"
    assert engine._src_downloader.cfg["host"] == "src"
    await engine.close()


@pytest.mark.asyncio
async def test_phase_scan_requires_torrent_dir(config_files):
    cfg_path, sites_path, cfg, _ = config_files
    cfg["downloader"]["torrent_dir"] = ""
    write_yaml(cfg_path, cfg)
    engine = _engine(Config(str(cfg_path), str(sites_path)))
    with pytest.raises(ValueError, match="torrent_dir 未配置"):
        await engine._phase_scan()


@pytest.mark.asyncio
async def test_phase_scan_deduplicates_by_pieces_hash(fresh_config, tmp_path):
    torrent_dir = tmp_path / "torrents"
    torrent_dir.mkdir()
    # 两个不同 info_hash、相同 pieces_hash，应折叠成一个待查询种子
    (torrent_dir / "a.torrent").write_bytes(make_varied_torrent_bytes(1))
    (torrent_dir / "b.torrent").write_bytes(make_varied_torrent_bytes(1))
    engine = _engine(fresh_config)
    await engine._phase_scan()
    assert engine._stats.total_torrents == 2
    assert engine._stats.duplicate_count == 1
    assert len(engine._torrents) == 1
    assert len(engine._qb_pieces_hashes) == 1


@pytest.mark.asyncio
async def test_run_dry_run_skips_add_phase(fresh_config):
    engine = _engine(fresh_config)
    calls = []

    async def scan():
        calls.append("scan")

    async def query(sites):
        calls.append("query")

    async def add(sites):
        calls.append("add")

    engine._phase_scan = scan
    engine._phase_query_sites = query
    engine._phase_add_torrents = add
    stats = await engine.run(dry_run=True)
    assert calls == ["scan", "query"]  # 演练模式不得添加种子
    assert stats.end_time >= stats.start_time
    assert stats.dead_count == 0


@pytest.mark.asyncio
async def test_phase_jackett_search_handles_empty_torrent_list(fresh_config):
    engine = _engine(fresh_config)
    engine._torrents = []

    class FakeDownloader:
        async def get_all_info_hashes(self):
            return set()

    engine._downloader = FakeDownloader()
    engine._jackett = SimpleNamespace(stats=SimpleNamespace(
        searched_count=0, matched_count=0, downloaded_count=0, failed_count=0,
    ))
    await engine._phase_jackett_search()
