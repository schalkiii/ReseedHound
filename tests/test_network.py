"""网络层：限流识别、HTML 死种原因、批次查询异常、Jackett 解析与尺寸过滤。"""
import asyncio

import pytest

import src.network.client as client_module
from src.engine.jackett import JackettClient, JackettResult, _extract_search_terms
from src.network.client import (
    RATE_LIMIT_HINT_PATTERNS,
    RATE_LIMIT_KEYWORDS,
    DeadTorrentCache,
    SiteClient,
    _detect_html_reason,
    _is_likely_text_error,
    _parse_rate_limit_response,
)
from tests.helpers import PIECES_HASH_GOLDEN, make_torrent_bytes


def test_rate_limit_rules_have_no_duplicates():
    assert len(RATE_LIMIT_KEYWORDS) == len(set(RATE_LIMIT_KEYWORDS))
    patterns = [p.pattern for p, _ in RATE_LIMIT_HINT_PATTERNS]
    assert len(patterns) == len(set(patterns))


def test_rate_limit_minutes_hint_overrides_longer_phrases():
    body = "请求过于频繁，请 5 分钟后重试".encode()
    assert _parse_rate_limit_response(body, "text/plain") == 300.0
    assert _is_likely_text_error(body, "text/plain") == 300.0


def test_rate_limit_seconds_hint_has_lower_bound():
    body = b"too many, try again in 3 second"
    assert _parse_rate_limit_response(body, "text/plain") == 10.0


@pytest.mark.parametrize("body", [
    b"everything is fine",
    "频繁".encode() + b"x" * 10001,  # 超大响应体不参与解析
])
def test_rate_limit_returns_none_for_irrelevant_body(body):
    assert _parse_rate_limit_response(body, "text/plain") is None


@pytest.mark.parametrize("text,expected", [
    ("<html>没有权限</html>", "permission_denied"),
    ("种子已删除", "torrent_deleted"),
    ("404 not found", "not_found"),
    ("请登录", "auth_required"),
    ("403 forbidden", "forbidden"),
    ("一切正常", None),
])
def test_detect_html_reason(text, expected):
    assert _detect_html_reason(text.encode()) == expected


@pytest.mark.asyncio
async def test_dead_torrent_cache_and_persistence():
    class FakeStore:
        def __init__(self):
            self.recorded = []

        async def record_dead(self, site, torrent_id):
            self.recorded.append((site, torrent_id))

    store = FakeStore()
    cache = DeadTorrentCache(torrent_cache=store)
    assert cache.is_dead("s", 1) is False
    await cache.mark_dead("s", 1)
    assert cache.is_dead("s", 1) is True
    assert store.recorded == [("s", 1)]
    await cache.seed({("s", 2), ("s", 3)})
    assert cache.size() == 3


def _make_client_with_responses(responses):
    client = SiteClient.__new__(SiteClient)
    queue = list(responses)

    async def fake_query_site(site, batch, label=""):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client.query_site = fake_query_site
    return client


@pytest.mark.asyncio
async def test_query_site_batched_parses_data_and_skips_meta_keys():
    client = _make_client_with_responses([
        {"data": {"ph1": 11, "passkey": "ignored", "ph2": "22"}},
        {"data": {"ph3": 33}},
    ])
    results = await client.query_site_batched(
        {"name": "siteA"}, [["ph1", "ph2"], ["ph3"]]
    )
    assert sorted(results) == [("ph1", 11), ("ph2", 22), ("ph3", 33)]


@pytest.mark.asyncio
async def test_query_site_batched_non_dict_response_is_a_failure(caplog):
    client = _make_client_with_responses([["unexpected list"]])
    with caplog.at_level("WARNING", logger="seedhound"):
        results = await client.query_site_batched({"name": "siteA"}, [["ph1"]])
    assert results == []
    assert "非预期的响应类型" in caplog.text


@pytest.mark.asyncio
async def test_query_site_batched_exception_is_not_swallowed(caplog):
    client = _make_client_with_responses([RuntimeError("api down")])
    with caplog.at_level("WARNING", logger="seedhound"):
        results = await client.query_site_batched({"name": "siteA"}, [["ph1"]])
    assert results == []
    assert "批次查询异常" in caplog.text


@pytest.mark.asyncio
async def test_post_json_http_date_retry_after_does_not_crash(monkeypatch):
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)

    class Resp:
        status = 429
        headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def json(self):
            return {}

    class Session:
        def post(self, url, **kwargs):
            return Resp()

    client = SiteClient.__new__(SiteClient)
    client._session = Session()
    client._semaphore = asyncio.Semaphore(1)
    client._timeout = None
    client._retry_delay = 2.0
    assert await client.post_json("http://x", {}, retries=1) is None
    assert delays == [5.0]  # HTTP-date 无法转 float 时回退 5 秒


def test_extract_search_terms():
    assert _extract_search_terms("Some.Movie.2020.1080p.BluRay.x264-GRP") == "Some Movie 2020"
    assert _extract_search_terms("Another Film 1080p WEB-DL 2019") == "Another Film 2019"


def _result(size, url="https://x/dl", indexer="IndexerA"):
    return JackettResult(
        title="Movie", size=size, seeders=1, peers=0,
        download_url=url, info_hash="abc", indexer_id="idx",
        indexer_name=indexer,
    )


def test_jackett_filter_by_size():
    client = JackettClient("http://jackett", "key")
    assert client.filter_by_size([_result(999), _result(1000), _result(1001), _result(1002)], 0) == []
    assert [r.size for r in client.filter_by_size(
        [_result(999), _result(1000), _result(1001), _result(1002)], 1001
    )] == [1000, 1001, 1002]  # 小于 1MB 时容差 1 字节
    assert [r.size for r in client.filter_by_size(
        [_result(2_000_000), _result(2_000_005), _result(2_020_001)], 2_000_000
    )] == [2_000_000, 2_000_005]  # 大于 1MB 时容差 10000 字节


def test_jackett_parse_torznab():
    xml = """<rss><channel><item>
      <title>Movie 2020 1080p</title><size>123456</size>
      <link>https://x/dl</link><guid>abc</guid>
      <jackettindexer id="idx">IndexerA</jackettindexer>
      <torznab:attrs xmlns:torznab="http://torznab.hatim.com/schemas/2015/feed">
        <torznab:attr name="seeders" value="12"/>
        <torznab:attr name="peers" value="3"/>
        <torznab:attr name="infohash" value="deadbeef"/>
      </torznab:attrs>
    </item></channel></rss>"""
    parsed = JackettClient("http://jackett", "key")._parse_torznab(xml)
    assert len(parsed) == 1
    assert parsed[0].size == 123456
    assert parsed[0].seeders == 12
    assert parsed[0].info_hash == "deadbeef"
    assert parsed[0].indexer_name == "IndexerA"


def test_jackett_parse_torznab_invalid_xml():
    assert JackettClient("http://jackett", "key")._parse_torznab("<broken") == []


@pytest.mark.asyncio
async def test_jackett_download_cache_skips_known_mismatch():
    client = JackettClient("http://jackett", "key")
    result = _result(100, url="https://x/dl")

    async def fake_search(query):
        return [result]

    class FakeHttp:
        def __init__(self):
            self.calls = 0

        async def try_download(self, url, site_name=""):
            self.calls += 1
            return make_torrent_bytes(), ""

    http = FakeHttp()
    client.search = fake_search
    # 目标 hash 与实际下载内容不符：第一次记录缓存，第二次直接跳过
    first = await client.search_and_download(
        "Movie", 100, target_pieces_hash="mismatch", http_client=http
    )
    second = await client.search_and_download(
        "Movie", 100, target_pieces_hash="mismatch", http_client=http
    )
    assert first == [] and second == []
    assert client._dl_cache["https://x/dl"] == PIECES_HASH_GOLDEN
    assert http.calls == 1
