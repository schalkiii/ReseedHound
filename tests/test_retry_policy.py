"""重试策略优化：硬失败不重试、200 非 JSON 归类、日志区分批次与重试。"""
import asyncio

import aiohttp
import pytest
from aiohttp.client_reqrep import RequestInfo
from yarl import URL

from src.network.client import (
    NON_RETRYABLE_ERROR_HINTS,
    NON_RETRYABLE_HTTP_STATUS,
    SiteClient,
    _is_non_retryable_error,
)


def _ctype_error():
    url = URL("http://example.com/api/pieces-hash")
    info = RequestInfo(url=url, method="POST", headers={}, real_url=url)
    return aiohttp.ContentTypeError(info, ())


class _Resp:
    def __init__(self, status=200, json_data=None, ctype_error=False, headers=None):
        self.status = status
        self.headers = headers or {}
        self._json = json_data
        self._ctype_error = ctype_error

    async def json(self):
        if self._ctype_error:
            raise _ctype_error()
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Session:
    def __init__(self, specs):
        self._specs = list(specs)
        self.calls = 0

    def post(self, url, **kwargs):
        self.calls += 1
        spec = self._specs.pop(0) if self._specs else {"status": 200, "json_data": {}}
        return _Resp(**spec)


def _client(session, retries=2):
    c = SiteClient.__new__(SiteClient)
    c._session = session
    c._semaphore = asyncio.Semaphore(2)
    c._timeout = 5
    c._retry_delay = 0.0
    c._retry_count = retries
    return c


def test_non_retryable_status_covers_auth_and_gone():
    assert NON_RETRYABLE_HTTP_STATUS == {401, 403, 404, 405, 410}


@pytest.mark.parametrize("msg", [
    "Cannot connect to host raingfh.top:443 ssl:default [getaddrinfo failed]",
    "certificate verify failed: self-signed certificate",
    "SSL: CERTIFICATE_VERIFY_FAILED",
    "Name or service not known",
])
def test_is_non_retryable_error_detects_hard_failures(msg):
    assert _is_non_retryable_error(msg) is True


@pytest.mark.parametrize("msg", [
    "timeout",
    "Connection reset by peer",
    "Server disconnected",
    "",
])
def test_is_non_retryable_error_allows_transient(msg):
    assert _is_non_retryable_error(msg) is False


def test_non_retryable_hints_are_lowercase_substrings():
    for hint in NON_RETRYABLE_ERROR_HINTS:
        assert hint == hint.lower()


@pytest.mark.asyncio
async def test_hard_http_status_is_not_retried():
    session = _Session([{"status": 404, "json_data": None}] * 5)
    client = _client(session, retries=3)
    assert await client.post_json("http://x/api", {}, retries=3) is None
    assert session.calls == 1, "404 是硬失败，不应重试"


@pytest.mark.asyncio
async def test_transient_5xx_is_retried_up_to_limit():
    session = _Session([{"status": 502, "json_data": None}] * 5)
    client = _client(session, retries=3)
    assert await client.post_json("http://x/api", {}, retries=3) is None
    assert session.calls == 3, "5xx 属临时故障，应重试满次数"


@pytest.mark.asyncio
async def test_html_instead_of_json_is_not_retried():
    session = _Session([{"status": 200, "ctype_error": True}] * 5)
    client = _client(session, retries=3)
    assert await client.post_json("http://x/api", {}, retries=3) is None
    assert session.calls == 1, "200 但非 JSON 是硬失败，不应重试"


@pytest.mark.asyncio
async def test_successful_json_returns_payload():
    session = _Session([{"status": 200, "json_data": {"ok": 1}}])
    client = _client(session, retries=3)
    assert await client.post_json("http://x/api", {}, retries=3) == {"ok": 1}
    assert session.calls == 1


@pytest.mark.asyncio
async def test_label_appears_in_failure_log(caplog):
    session = _Session([{"status": 404, "json_data": None}])
    client = _client(session, retries=2)
    with caplog.at_level("WARNING", logger="seedhound"):
        await client.post_json("http://x/api", {}, label="第 3/43 批")
    assert any(
        "第 3/43 批" in rec.getMessage() and "硬失败不重试" in rec.getMessage()
        for rec in caplog.records
    ), caplog.text
