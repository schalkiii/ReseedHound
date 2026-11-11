"""HDArea info_hash 辅种通道：配置、分批、响应解析、流水线分流与凭据脱敏。"""
import pytest

from src.engine.seeder import ReseedEngine, ReseedStats
from src.network.hdarea import (
    HDAREA_HASHES_PATH,
    MAX_HASHES_PER_REQUEST,
    HdareaMatch,
    HdareaMatcher,
    hashes_url,
    is_hdarea_site,
    parse_hashes_response,
    split_batches,
)
from src.storage.config import Config
from src.utils.logger import redact_secrets
from tests.helpers import write_yaml

H1 = "a" * 40
H2 = "b" * 40
H3 = "c" * 40
SHORT_LINK = "https://hdarea.club/download.php?id=111&dl_token=secret-token"


# ---------- 站点识别与 URL ----------

def test_is_hdarea_site():
    assert is_hdarea_site({"api_type": "hdarea"}) is True
    assert is_hdarea_site({"api_type": "HDArea"}) is True
    assert is_hdarea_site({"api_type": "pieces_hash"}) is False
    assert is_hdarea_site({}) is False


def test_hashes_url_explicit_and_derived():
    assert hashes_url({
        "url": "https://hdarea.club",
        "api_url": "https://hdarea.club/api/v1/torrents/hashes",
    }) == "https://hdarea.club/api/v1/torrents/hashes"
    assert hashes_url({"url": "https://hdarea.club/"}) == (
        "https://hdarea.club" + HDAREA_HASHES_PATH
    )


# ---------- 分批 ----------

def test_split_batches_filters_and_caps():
    mixed = [H1.upper(), "not-a-hash", H2, "", H3]
    assert split_batches(mixed) == [[H1, H2, H3]]

    many = [f"{i:040x}" for i in range(MAX_HASHES_PER_REQUEST + 5)]
    batches = split_batches(many)
    assert len(batches) == 2
    assert len(batches[0]) == MAX_HASHES_PER_REQUEST
    assert len(batches[1]) == 5
    # 调用方要求更大尺寸也不能越过接口硬上限
    assert len(split_batches(many, size=1000)[0]) == MAX_HASHES_PER_REQUEST
    assert split_batches([]) == []


# ---------- 响应解析 ----------

def _payload(*items):
    return {
        "success": True, "code": 0, "http_code": 200,
        "message": "OK", "timestamp": 1,
        "data": {"count": len(items), "items": list(items)},
    }


def test_parse_hashes_response_valid():
    parsed = parse_hashes_response(_payload(
        {"id": 111, "info_hash": H1.upper(), "name": "Movie", "download_url": SHORT_LINK},
        {"id": 222, "info_hash": H2, "name": "No link"},
    ))
    assert [m.torrent_id for m in parsed] == [111, 222]
    assert parsed[0].info_hash == H1
    assert parsed[0].download_url == SHORT_LINK
    assert parsed[1].download_url == ""


@pytest.mark.parametrize("bad", [
    None, [], "text",
    {"success": False, "code": 401, "data": {"items": []}},
    {"success": True, "code": 5, "data": {"items": []}},
    {"success": True, "code": 0, "data": []},
    {"success": True, "code": 0},
])
def test_parse_hashes_response_rejects_bad_envelope(bad):
    assert parse_hashes_response(bad) == []


def test_parse_hashes_response_skips_malformed_items():
    parsed = parse_hashes_response(_payload(
        {"id": "nan", "info_hash": H1},
        {"id": 333, "info_hash": "short"},
        {"id": 444, "info_hash": H3, "download_url": SHORT_LINK},
        "not-a-dict",
    ))
    assert len(parsed) == 1
    assert parsed[0].torrent_id == 444


# ---------- 配置层 ----------

def test_config_accepts_api_key_site_without_passkey(config_files):
    cfg_path, sites_path, cfg, sites = config_files
    sites["sites"] = [
        {"name": "HDArea", "url": "https://hdarea.club", "api_type": "hdarea",
         "api_key": "x" * 32, "api_url": "https://hdarea.club/api/v1/torrents/hashes"},
        {"name": "legacy", "url": "https://legacy.example", "passkey": "p",
         "api_url": "https://legacy.example/api"},
        {"name": "no-credential", "url": "https://none.example"},
    ]
    write_yaml(sites_path, sites)
    config = Config(str(cfg_path), str(sites_path))
    assert [s["name"] for s in config.sites] == ["HDArea", "legacy"]


def test_hdarea_defaults_come_from_config(fresh_config):
    assert fresh_config.value("hdarea", "request_interval") > 0
    assert fresh_config.value("hdarea", "concurrency") >= 1


# ---------- Matcher ----------

class FakePostClient:
    def __init__(self, responses=None, fail_on=None):
        self.calls = []
        self.responses = responses or []
        self.fail_on = fail_on

    async def post_json(self, url, data, retries=1, headers=None):
        self.calls.append((url, data, headers))
        index = len(self.calls) - 1
        if self.fail_on == index:
            raise RuntimeError("network down")
        if self.responses:
            return self.responses[min(index, len(self.responses) - 1)]
        return _payload()


class FakeHdareaMatcher:
    def __init__(self, matches):
        self.matches = matches
        self.calls = []

    async def query(self, site, info_hashes):
        self.calls.append((site["name"], list(info_hashes)))
        return self.matches


class FakeCache:
    async def get_reseeded_hashes_by_site(self, hashes):
        return {}

    async def clear_dead_if_alive(self, site, torrent_id):
        return None

    async def add_reseed_record(self, pieces_hash, site, torrent_id):
        return None


class FakeDeadCache:
    def is_dead(self, site, torrent_id):
        return False

    def size(self):
        return 0


class FakeDownloadClient:
    def __init__(self, results):
        self.results = list(results)
        self.urls = []

    async def try_download(self, url, site_name="", torrent_id=0):
        self.urls.append(url)
        return self.results.pop(0) if self.results else (None, "http_404")


class FakeDownloader:
    async def get_all_info_hashes(self):
        return set()

    async def get_torrent_info(self, info_hash):
        return {"state": "stoppedUP", "save_path": "/downloads"}


@pytest.mark.asyncio
async def test_matcher_sends_api_key_header_and_batches():
    client = FakePostClient(responses=[_payload(
        {"id": 111, "info_hash": H1, "download_url": SHORT_LINK}
    )])
    matcher = HdareaMatcher(client, request_interval=0, concurrency=2)
    hashes = [f"{i:040x}" for i in range(MAX_HASHES_PER_REQUEST + 1)]

    matches = await matcher.query(
        {"name": "HDArea", "url": "https://hdarea.club", "api_key": "k" * 32},
        hashes,
    )

    assert len(client.calls) == 2
    assert len(client.calls[0][1]["hashes"]) == MAX_HASHES_PER_REQUEST
    assert len(client.calls[1][1]["hashes"]) == 1
    assert all(call[2] == {"X-API-Key": "k" * 32} for call in client.calls)
    assert client.calls[0][0].endswith(HDAREA_HASHES_PATH)
    # 两个批次返回同一份 payload，因此命中项各出现一次
    assert len(matches) == 2


@pytest.mark.asyncio
async def test_matcher_missing_api_key_returns_empty():
    client = FakePostClient()
    matcher = HdareaMatcher(client, request_interval=0)
    assert await matcher.query({"name": "HDArea", "url": "https://hdarea.club"}, [H1]) == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_matcher_survives_batch_exception():
    client = FakePostClient(
        responses=[_payload({"id": 111, "info_hash": H1, "download_url": SHORT_LINK})],
        fail_on=0,
    )
    matcher = HdareaMatcher(client, request_interval=0)
    hashes = [f"{i:040x}" for i in range(MAX_HASHES_PER_REQUEST + 1)]

    matches = await matcher.query(
        {"name": "HDArea", "url": "https://hdarea.club", "api_key": "k"}, hashes
    )
    assert len(matches) == 1
    assert matches[0].torrent_id == 111


# ---------- 流水线分流 ----------

def _engine(config, torrents, client, hdarea_matcher):
    engine = ReseedEngine.__new__(ReseedEngine)
    engine._config = config
    engine._stats = ReseedStats()
    engine._batch_size = config.value("global", "batch_size")
    engine._cache = FakeCache()
    engine._dead_cache = FakeDeadCache()
    engine._client = client
    engine._hdarea = hdarea_matcher
    engine._jackett = None
    engine._mode = "pieces_hash"
    engine._downloader = FakeDownloader()
    engine._src_downloader = None
    engine._torrents = torrents
    engine._qb_pieces_hashes = {t["pieces_hash"] for t in torrents}
    engine._qb_announces = {t["pieces_hash"]: t.get("announce", "") for t in torrents}
    engine._pieces_hash_index = {t["pieces_hash"]: t for t in torrents}
    engine._info_hash_index = {}
    engine._download_url_map = {}
    engine._match_map = {}
    return engine


def _hdarea_fixture(config_files):
    cfg_path, sites_path, cfg, sites = config_files
    sites["sites"] = [
        {"name": "HDArea", "url": "https://hdarea.club", "api_type": "hdarea",
         "api_key": "k" * 32, "api_url": "https://hdarea.club/api/v1/torrents/hashes"},
        {"name": "legacy", "url": "https://legacy.example", "passkey": "p",
         "api_url": "https://legacy.example/api"},
    ]
    write_yaml(sites_path, sites)
    config = Config(str(cfg_path), str(sites_path))
    torrents = [
        {"pieces_hash": "p" * 40, "info_hash": H1, "file_name": "a.torrent", "announce": ""},
        {"pieces_hash": "q" * 40, "info_hash": H2, "file_name": "b.torrent", "announce": ""},
    ]
    return config, torrents


@pytest.mark.asyncio
async def test_query_phase_routes_hdarea_and_keeps_legacy_channel(config_files):
    config, torrents = _hdarea_fixture(config_files)
    sites = config.sites
    hdarea_matcher = FakeHdareaMatcher([
        HdareaMatch(H1, 111, SHORT_LINK),
        HdareaMatch(H2, 222, "https://hdarea.club/download.php?id=222&dl_token=t2"),
    ])

    class Combined:
        def __init__(self):
            self.legacy_calls = []

        async def query_site_batched(self, site, batches):
            self.legacy_calls.append((site["name"], [len(b) for b in batches]))
            return [("p" * 40, 999)]

    combined = Combined()
    engine = _engine(config, torrents, combined, hdarea_matcher)

    await engine._phase_query_sites(sites)

    assert len(combined.legacy_calls) == 1
    assert combined.legacy_calls[0][0] == "legacy"
    assert hdarea_matcher.calls[0][0] == "HDArea"
    assert hdarea_matcher.calls[0][1] == [H1, H2]
    assert engine._download_url_map[("HDArea", 111)] == SHORT_LINK
    assert engine._stats.site_details["HDArea"] == 2
    assert ("HDArea", 111, "https://hdarea.club") in engine._match_map["p" * 40]
    assert ("legacy", 999, "https://legacy.example") in engine._match_map["p" * 40]


@pytest.mark.asyncio
async def test_add_phase_prefers_short_link_and_falls_back_on_403(config_files):
    config, torrents = _hdarea_fixture(config_files)
    sites = config.sites
    client = FakeDownloadClient(results=[(None, "http_403"), (None, "http_404")])
    engine = _engine(config, torrents, client, FakeHdareaMatcher([]))
    engine._match_map = {"p" * 40: [("HDArea", 111, "https://hdarea.club")]}
    engine._download_url_map = {("HDArea", 111): SHORT_LINK}

    await engine._phase_add_torrents(sites)

    assert client.urls[0] == SHORT_LINK
    assert len(client.urls) == 2
    assert "apikey=" in client.urls[1]
    assert "dl_token=" not in client.urls[1]


@pytest.mark.asyncio
async def test_add_phase_keeps_passkey_for_legacy_site(config_files):
    config, torrents = _hdarea_fixture(config_files)
    sites = config.sites
    client = FakeDownloadClient(results=[(None, "http_404")])
    engine = _engine(config, torrents, client, FakeHdareaMatcher([]))
    engine._match_map = {"p" * 40: [("legacy", 999, "https://legacy.example")]}

    await engine._phase_add_torrents(sites)

    assert len(client.urls) == 1
    assert "passkey=p" in client.urls[0]


# ---------- 脱敏 ----------

def test_redact_secrets_covers_hdarea_credentials():
    url = (
        "https://hdarea.club/download.php?id=1"
        "&apikey=abcdef0123456789&dl_token=very-secret-token"
    )
    masked = redact_secrets(url)
    assert "abcdef0123456789" not in masked
    assert "very-secret-token" not in masked
    assert "apikey=***" in masked and "dl_token=***" in masked
    assert redact_secrets("X-API-Key: abcdef0123456789") == "X-API-Key: ***"
    assert redact_secrets("id=1&passkey=deadbeef") == "id=1&passkey=***"
