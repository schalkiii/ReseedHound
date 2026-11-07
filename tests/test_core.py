"""核心模块：bencode / 哈希 / 解析 / 日志脱敏 / 下载器返回体 / 自动开始。"""
import asyncio
import logging

import pytest

from src.engine.downloader import QbittorrentDownloader
from src.engine.parser import TorrentParser
from src.engine.seeder import STOPPED_COMPLETE_STATES, ReseedEngine
from src.utils._bencode import bdecode, bencode
from src.utils.hash import compute_info_hash, compute_pieces_hash
from src.utils.logger import _RedactSecretsFilter, redact_secrets
from tests.helpers import (
    INFO_GOLDEN_BYTES,
    INFO_HASH_GOLDEN,
    PIECES_HASH_GOLDEN,
    SAMPLE_INFO,
    make_torrent_bytes,
    make_varied_torrent_bytes,
)


@pytest.mark.parametrize("raw,expected", [
    (b"i42e", 42),
    (b"i-7e", -7),
    (b"li1ei2ei3ee", [1, 2, 3]),
    (b"le", []),
    (b"d3:foo3:bare", {b"foo": b"bar"}),
    (b"4:spam", b"spam"),
])
def test_bdecode_types(raw, expected):
    assert bdecode(raw) == expected


def test_bencode_matches_external_golden_bytes():
    # golden 字节串手写给出，不依赖项目 bencode 推导；键必须按字典序编码
    assert bencode(SAMPLE_INFO[b"info"]) == INFO_GOLDEN_BYTES
    assert bdecode(make_torrent_bytes()) == SAMPLE_INFO


def test_info_hash_external_golden():
    assert compute_info_hash(SAMPLE_INFO[b"info"]) == INFO_HASH_GOLDEN
    assert len(INFO_HASH_GOLDEN) == 40


def test_pieces_hash_external_golden():
    assert compute_pieces_hash(b"\x00" * 20) == PIECES_HASH_GOLDEN
    assert len(PIECES_HASH_GOLDEN) == 40


def test_parser_returns_golden_and_metadata():
    parsed = TorrentParser.parse_bytes(make_torrent_bytes(), file_name="t.torrent")
    assert parsed is not None
    assert parsed["info_hash"] == INFO_HASH_GOLDEN
    assert parsed["pieces_hash"] == PIECES_HASH_GOLDEN
    assert parsed["torrent_name"] == "test.dat"
    assert parsed["total_size"] == 1234
    assert parsed["announce"] == "http://example.com/announce"
    assert parsed["file_name"] == "t.torrent"


@pytest.mark.parametrize("bad", [
    b"", b"not a torrent", b"d4:infoi1ee", b"d3:foo3:bare",
])
def test_parser_rejects_corrupted(bad):
    assert TorrentParser.parse_bytes(bad, file_name="broken.torrent") is None


def test_parser_multi_file_total_size():
    data = bencode({
        b"info": {
            b"name": b"bundle",
            b"piece length": 65536,
            b"pieces": b"\x00" * 20,
            b"files": [{b"length": 100}, {b"length": 250}],
        },
    })
    assert TorrentParser.parse_bytes(data)["total_size"] == 350


@pytest.mark.asyncio
async def test_scan_directory_skips_corrupted_and_non_torrent(tmp_path):
    (tmp_path / "ok.torrent").write_bytes(make_varied_torrent_bytes(1))
    (tmp_path / "bad.torrent").write_bytes(b"garbage")
    (tmp_path / "ignore.txt").write_text("x")
    results = await TorrentParser.scan_directory(str(tmp_path), max_concurrent=2)
    assert [r["file_name"] for r in results] == ["ok.torrent"]
    assert len(results[0]["info_hash"]) == 40


@pytest.mark.asyncio
async def test_scan_directory_empty_dir(tmp_path):
    assert await TorrentParser.scan_directory(str(tmp_path)) == []


@pytest.mark.asyncio
async def test_scan_directory_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="种子目录不存在"):
        await TorrentParser.scan_directory(str(tmp_path / "nope"))


@pytest.mark.parametrize("result,expected", [
    ("Ok.", True),
    ("", True),
    (None, True),
    ({"success_count": 1, "failure_count": 0, "pending_count": 0}, True),
    ({"success_count": 0, "failure_count": 0, "pending_count": 2}, True),
    ({"success_count": 0, "failure_count": 1, "pending_count": 0}, False),
    ("Err.: file exists", False),
    (12345, False),
    ([], False),
])
def test_add_result_compat(result, expected):
    assert QbittorrentDownloader._is_add_success(result) is expected


def test_stopped_complete_states():
    assert set(STOPPED_COMPLETE_STATES) == {"pausedUP", "stoppedUP", "completed"}


def test_log_redaction():
    url = "https://pt.example.com/download.php?id=1&passkey=deadbeef00112233"
    masked = redact_secrets(url)
    assert "deadbeef00112233" not in masked
    assert masked == "https://pt.example.com/download.php?id=1&passkey=***"

    record = logging.LogRecord(
        "seedhound", logging.WARNING, __file__, 1, "下载失败 %s", (url,), None
    )
    assert _RedactSecretsFilter().filter(record) is True
    assert "deadbeef00112233" not in record.getMessage()
    assert "passkey=***" in record.getMessage()


def test_auto_start_resumes_stopped_torrent():
    class FakeDownloader:
        def __init__(self, states):
            self.states = list(states)
            self.resumed = []

        async def get_torrent_info(self, info_hash):
            state = self.states.pop(0) if self.states else "uploading"
            return None if state is None else {"state": state}

        async def resume_torrent(self, info_hash):
            self.resumed.append(info_hash)
            return True

    engine = ReseedEngine.__new__(ReseedEngine)
    engine._downloader = FakeDownloader(["stoppedUP"])
    asyncio.run(engine._ensure_started("h1", "f.torrent", "siteA"))
    assert engine._downloader.resumed == ["h1"]

    engine._downloader = FakeDownloader(["uploading"])
    asyncio.run(engine._ensure_started("h2", "f.torrent", "siteA"))
    assert engine._downloader.resumed == []
