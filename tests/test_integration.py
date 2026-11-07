"""集成：SQLite 缓存迁移/死种、辅种阶段异常统计、周报解析与清理。"""
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.weekly_report import (
    cleanup_log,
    generate_report,
    load_site_host_map,
    parse_log,
)
from src.engine.seeder import ReseedEngine, ReseedStats
from src.storage.cache import TorrentCache
from src.utils.logger import RUN_MARKER


@pytest.mark.asyncio
async def test_cache_init_filter_and_clear(tmp_path):
    cache = TorrentCache(str(tmp_path / "sub" / "cache.db"))
    await cache.init()
    items = [
        {"pieces_hash": "a" * 40, "info_hash": "b" * 40, "file_name": "a.torrent"},
        {"pieces_hash": "c" * 40, "info_hash": "d" * 40, "file_name": "c.torrent"},
    ]
    assert await cache.filter_uncached(items) == items
    await cache.add_batch(items)
    assert await cache.filter_uncached(items) == []
    assert await cache.get_stats() == {"cached_torrents": 2, "reseed_count": 0}
    await cache.clear_cache()
    assert (await cache.get_stats())["cached_torrents"] == 0


@pytest.mark.asyncio
async def test_reseed_history_migration_dedupes_and_ignores_inserts(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE reseed_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pieces_hash TEXT NOT NULL,
            site_name TEXT NOT NULL,
            torrent_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for _ in range(3):
        con.execute(
            "INSERT INTO reseed_history (pieces_hash, site_name, torrent_id)"
            " VALUES ('ph', 'siteA', 1)"
        )
    con.execute(
        "INSERT INTO reseed_history (pieces_hash, site_name, torrent_id)"
        " VALUES ('ph', 'siteB', 2)"
    )
    con.commit()
    con.close()

    cache = TorrentCache(str(db))
    await cache.init()
    assert (await cache.get_stats())["reseed_count"] == 2
    await cache.add_reseed_record("ph", "siteA", 1)
    assert (await cache.get_stats())["reseed_count"] == 2
    assert await cache.get_reseeded_hashes_by_site(["ph"]) == {
        "siteA": {"ph"}, "siteB": {"ph"},
    }
    con = sqlite3.connect(str(db))
    names = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    con.close()
    assert "uq_reseed_history" in names


@pytest.mark.asyncio
async def test_dead_torrent_persistence_threshold(tmp_path):
    db = tmp_path / "dead.db"
    cache = TorrentCache(str(db))
    await cache.init()
    await cache.record_dead("siteA", 9)
    assert await cache.load_persistent_dead(min_days=15) == set()

    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(db))
    con.execute("UPDATE dead_torrents SET first_dead_at = ?", (old,))
    con.commit()
    con.close()
    assert await cache.load_persistent_dead(min_days=15) == {("siteA", 9)}

    await cache.clear_dead_if_alive("siteA", 9)
    assert await cache.load_persistent_dead(min_days=15) == set()


class FakeCache:
    async def get_reseeded_hashes_by_site(self, hashes):
        return {}

    async def clear_dead_if_alive(self, site, torrent_id):
        return None

    async def add_reseed_record(self, pieces_hash, site, torrent_id):
        return None


def _bare_engine(config):
    engine = ReseedEngine.__new__(ReseedEngine)
    engine._config = config
    engine._stats = ReseedStats()
    engine._batch_size = config.value("global", "batch_size")
    engine._cache = FakeCache()
    engine._torrents = [{"pieces_hash": "ph1"}]
    engine._qb_announces = {}
    engine._pieces_hash_index = {
        "ph1": {"info_hash": "ih1", "file_name": "f.torrent"},
    }
    return engine


@pytest.mark.asyncio
async def test_phase_query_sites_exception_enters_failed_sites(fresh_config):
    engine = _bare_engine(fresh_config)

    async def boom(site, batches):
        raise RuntimeError("site api down")

    engine._client = SimpleNamespace(query_site_batched=boom)
    sites = [{"name": "siteA", "url": "https://a.example.com", "passkey": "pa"}]
    await engine._phase_query_sites(sites)
    assert engine._stats.failed_sites == ["siteA"]
    assert engine._stats.site_details["siteA"] == 0
    assert engine._stats.matched_count == 0


@pytest.mark.asyncio
async def test_phase_add_torrents_task_exception_is_counted(fresh_config, caplog):
    engine = _bare_engine(fresh_config)
    engine._match_map = {"ph1": [("siteA", 1, "https://a.example.com")]}

    class BoomDownloader:
        async def get_all_info_hashes(self):
            return set()

        async def get_torrent_info(self, info_hash):
            raise RuntimeError("downloader down")

    engine._downloader = BoomDownloader()
    with caplog.at_level("ERROR", logger="seedhound"):
        await engine._phase_add_torrents([{"name": "siteA", "passkey": "pa"}])
    assert engine._stats.failed_count == 1
    assert "添加种子任务异常" in caplog.text


def _sample_log_lines():
    return [
        f"2026-11-01 00:00:00 [INFO] seedhound: {RUN_MARKER} [模式: pieces_hash]\n",
        "2026-11-01 00:00:01 [INFO] seedhound: 站点 siteA 匹配到 12 个种子\n",
        "2026-11-01 00:00:02 [INFO] seedhound: 辅种成功: f.torrent -> siteA (id=1)\n",
        "2026-11-01 00:00:03 [INFO] seedhound: 下载超时: https://a.example.com/dl\n",
        "2026-11-01 00:00:04 [INFO] seedhound: 辅种完成: 扫描=10, 去重=1, 新=9, "
        "匹配=12, 追踪器跳过=2, 成功=1, 失败=1, 死种=0\n",
    ]


def test_weekly_report_parse_and_generate(tmp_path):
    log = tmp_path / "reseed.log"
    log.write_text("".join(_sample_log_lines()), encoding="utf-8")
    since = datetime(2026, 10, 1)
    per_site, runs = parse_log(str(log), since, {"a.example.com": "siteA"})
    assert len(runs) == 1
    assert runs[0]["matched"] == 12 and runs[0]["success"] == 1
    assert per_site["siteA"]["matched"] == 12
    assert per_site["siteA"]["success"] == 1
    assert per_site["siteA"]["failed"] == 1  # 下载超时按 URL 域名归因
    report = generate_report(per_site, runs, since)
    assert "运行轮次: **1**" in report
    assert "| siteA | 12 | 1 | 1 | 0 |" in report


def test_weekly_report_cleanup_preview_and_apply(tmp_path):
    log = tmp_path / "reseed.log"
    original = "".join(_sample_log_lines())
    log.write_text(original, encoding="utf-8")
    assert "清理预览" in cleanup_log(str(log), keep_days=1, keep_runs=3, apply=False)
    assert log.read_text(encoding="utf-8") == original  # 预览不改动文件
    assert "已清理" in cleanup_log(str(log), keep_days=0, keep_runs=1, apply=True)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_load_site_host_map(config_files):
    _, sites_path, _, _ = config_files
    assert load_site_host_map(str(sites_path)) == {
        "a.example.com": "siteA",
        "b.example.com": "siteB",
    }
